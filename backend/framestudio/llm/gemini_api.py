"""Gemini through the official ``google-genai`` SDK (plan step M2.4).

* Function calling with automatic calling turned off (FrameStudio runs the
  tools); the model's parts, thought signatures included, are kept in
  ``Message.native`` and sent back unchanged, as Gemini requires.
* Structured output: ``response_mime_type`` JSON with ``response_json_schema``.
* ``effort`` maps to the thinking level.
* Safety stops (and a blocked prompt) are replies with
  ``stop_reason: "refusal"``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx2
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from framestudio.llm.adapt import connection_error, from_status, strict_schema, tool_names
from framestudio.llm.base import (
    ChatRequest,
    ChatResponse,
    Done,
    ImagePart,
    Message,
    ModelInfo,
    StopReason,
    StreamEvent,
    TextDelta,
    TextPart,
    ToolCall,
    ToolCallEvent,
    ToolResult,
    Usage,
    collect,
    validate_request,
)
from framestudio.providers.base import ProviderError

PROVIDER = "google"
API_VERSION = "v1beta"
_LEVEL = {
    "low": types.ThinkingLevel.LOW,
    "medium": types.ThinkingLevel.MEDIUM,
    "high": types.ThinkingLevel.HIGH,
    "xhigh": types.ThinkingLevel.XHIGH,
    "max": types.ThinkingLevel.XHIGH,
}
_REFUSED = {
    "SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII",
    "IMAGE_SAFETY", "IMAGE_PROHIBITED_CONTENT", "MODEL_ARMOR",
}  # fmt: skip
#: Gemini models that are not chat models (image, speech, embeddings…).
_NOT_CHAT = ("image", "tts", "embedding", "live", "audio", "aqa", "imagen", "veo", "robotics")


class GeminiLLM:
    id = PROVIDER

    def __init__(self, api_key: str, transport: httpx2.AsyncBaseTransport) -> None:
        # The transport comes from the outbound policy (framestudio.net).
        options = types.HttpOptions(
            api_version=API_VERSION,
            httpx_async_client=httpx2.AsyncClient(transport=transport, timeout=600),
        )
        self._client = genai.Client(api_key=api_key, http_options=options)

    async def list_models(self) -> list[ModelInfo]:
        out = []
        try:
            pager = await self._client.aio.models.list(config={"page_size": 100})
            async for model in pager:
                name = (model.name or "").removeprefix("models/")
                actions = model.supported_actions or []
                if "generateContent" not in actions or not name.startswith("gemini"):
                    continue
                if any(word in name for word in _NOT_CHAT):
                    continue
                out.append(
                    ModelInfo(
                        id=name,
                        name=model.display_name or name,
                        provider=PROVIDER,
                        effort=bool(model.thinking),
                        context_tokens=model.input_token_limit,
                        max_output_tokens=model.output_token_limit,
                    )
                )
        except genai_errors.APIError as exc:
            raise _api_error(exc) from exc
        except httpx2.RequestError as exc:
            raise connection_error(
                PROVIDER, exc, timeout=isinstance(exc, httpx2.TimeoutException)
            ) from exc
        return out

    def config(self, request: ChatRequest) -> types.GenerateContentConfig:
        config = types.GenerateContentConfig(max_output_tokens=request.max_tokens)
        if request.system:
            config.system_instruction = request.system
        if request.tools:
            config.tools = [
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name=tool.name,
                            description=tool.description,
                            parameters_json_schema=strict_schema(tool.input_schema),
                        )
                        for tool in request.tools
                    ]
                )
            ]
            config.automatic_function_calling = types.AutomaticFunctionCallingConfig(disable=True)
        if request.response_schema is not None:
            config.response_mime_type = "application/json"
            config.response_json_schema = strict_schema(request.response_schema)
        if request.effort:
            config.thinking_config = types.ThinkingConfig(thinking_level=_LEVEL[request.effort])
        return config

    def contents(self, request: ChatRequest) -> list[types.Content]:
        names = tool_names(request.messages)
        return [_content(message, names) for message in request.messages]

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        validate_request(request, PROVIDER)
        parts: list[types.Part] = []
        finish: str | None = None
        blocked = False
        usage: types.GenerateContentResponseUsageMetadata | None = None
        model = request.model
        try:
            chunks = await self._client.aio.models.generate_content_stream(
                model=request.model, contents=self.contents(request), config=self.config(request)
            )
            async for chunk in chunks:
                if chunk.prompt_feedback and chunk.prompt_feedback.block_reason:
                    blocked = True
                usage = chunk.usage_metadata or usage
                model = chunk.model_version or model
                for candidate in chunk.candidates or []:
                    if candidate.finish_reason:
                        finish = str(
                            getattr(candidate.finish_reason, "value", candidate.finish_reason)
                        )
                    for part in (candidate.content.parts if candidate.content else None) or []:
                        parts.append(part)
                        if part.text and not part.thought:
                            yield TextDelta(part.text)
        except genai_errors.APIError as exc:
            raise _api_error(exc) from exc
        except httpx2.RequestError as exc:
            raise connection_error(
                PROVIDER, exc, timeout=isinstance(exc, httpx2.TimeoutException)
            ) from exc
        response = _response(parts, finish, blocked, usage, model)
        for call in response.tool_calls:
            yield ToolCallEvent(call)
        yield Done(response)

    async def complete(self, request: ChatRequest) -> ChatResponse:
        return await collect(self.stream(request))


def _api_error(exc: genai_errors.APIError) -> ProviderError:
    return from_status(PROVIDER, int(exc.code or 500), exc.message or exc.status or "")


def _content(message: Message, names: dict[str, str]) -> types.Content:
    native = message.native.get(PROVIDER)
    if message.role == "assistant" and native:
        return types.Content.model_validate(native["content"])
    parts: list[types.Part] = []
    for part in message.parts:
        if isinstance(part, TextPart) and part.text:
            parts.append(types.Part(text=part.text))
        elif isinstance(part, ImagePart):
            parts.append(types.Part.from_bytes(data=part.data, mime_type=part.mime))
        elif isinstance(part, ToolCall):
            parts.append(
                types.Part(
                    function_call=types.FunctionCall(
                        id=part.id, name=part.name, args=part.arguments
                    )
                )
            )
        elif isinstance(part, ToolResult):
            key = "error" if part.is_error else "result"
            parts.append(
                types.Part(
                    function_response=types.FunctionResponse(
                        id=part.call_id,
                        name=names.get(part.call_id, ""),
                        response={key: part.content},
                    )
                )
            )
    return types.Content(role="model" if message.role == "assistant" else "user", parts=parts)


def _response(
    parts: list[types.Part],
    finish: str | None,
    blocked: bool,
    usage: types.GenerateContentResponseUsageMetadata | None,
    model: str,
) -> ChatResponse:
    text = "".join(p.text for p in parts if p.text and not p.thought)
    calls: list[ToolCall] = []
    for part in parts:
        if part.function_call is not None:
            call = part.function_call
            if not call.id:  # the Gemini API may leave ids out; ours must be unique
                call.id = f"call_{uuid.uuid4().hex[:12]}"
            calls.append(ToolCall(call.id, call.name or "", dict(call.args or {})))
    stop: StopReason
    if blocked or (finish in _REFUSED):
        stop = "refusal"
    elif finish == "MAX_TOKENS":
        stop = "max_tokens"
    elif calls:
        stop = "tool_use"
    else:
        stop = "end"
    content = types.Content(role="model", parts=parts)
    native = {"content": content.model_dump(mode="json", exclude_none=True)}
    prompt = (usage.prompt_token_count or 0) if usage else 0
    cached = (usage.cached_content_token_count or 0) if usage else 0
    output = (
        ((usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)) if usage else 0
    )
    return ChatResponse(
        message=Message(
            "assistant", tuple([TextPart(text)] if text else []) + tuple(calls), {PROVIDER: native}
        ),
        stop_reason=stop,
        usage=Usage(
            input_tokens=max(0, prompt - cached), output_tokens=output, cache_read_tokens=cached
        ),
        model=model,
        provider=PROVIDER,
    )
