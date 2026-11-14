"""OpenAI through the official ``openai`` SDK and its Responses API (plan step
M2.4).

* Nothing is stored on OpenAI's side (``store: false``). For reasoning
  models the encrypted reasoning items come back with each reply; they are
  kept in ``Message.native`` with the rest of the output and sent back
  unchanged, so tool-calling turns keep their reasoning.
* Tools are strict functions; structured output is a strict JSON schema
  (OpenAI's strict mode lists every property as required, and optional ones
  become nullable).
* ``effort`` maps to ``reasoning.effort`` on reasoning models.
* A refusal (a ``refusal`` content part, or a content filter stop) is a
  reply with ``stop_reason: "refusal"``.
"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx2
import openai

from framestudio.llm.adapt import connection_error, from_status, strict_schema
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

PROVIDER = "openai"
#: Model families that reason (and take ``reasoning.effort``).
REASONING_PREFIXES = ("gpt-5", "gpt-6", "o1", "o3", "o4")
_CHAT_PREFIXES = ("gpt-", "o1", "o3", "o4", "chatgpt-")
_NOT_CHAT = (
    "audio", "realtime", "tts", "transcribe", "image", "search", "embedding",
    "moderation", "instruct", "dall-e", "whisper", "sora", "computer-use",
)  # fmt: skip


def is_reasoning(model: str) -> bool:
    return model.startswith(REASONING_PREFIXES)


class OpenAILLM:
    id = PROVIDER

    def __init__(
        self,
        api_key: str,
        transport: httpx2.AsyncBaseTransport,
        *,
        max_retries: int = 2,
    ) -> None:
        # The transport comes from the outbound policy (framestudio.net).
        self._client = openai.AsyncOpenAI(
            api_key=api_key,
            http_client=openai.DefaultAsyncHttpx2Client(transport=transport),
            max_retries=max_retries,
        )

    async def list_models(self) -> list[ModelInfo]:
        try:
            models = [model async for model in self._client.models.list()]
        except openai.APIStatusError as exc:
            raise _status_error(exc) from exc
        except openai.APIConnectionError as exc:
            raise connection_error(
                PROVIDER, exc, timeout=isinstance(exc, openai.APITimeoutError)
            ) from exc
        out = []
        for model in sorted(models, key=lambda m: m.id):
            mid = model.id
            if not mid.startswith(_CHAT_PREFIXES) or any(word in mid for word in _NOT_CHAT):
                continue
            out.append(
                ModelInfo(
                    id=mid,
                    name=mid,
                    provider=PROVIDER,
                    vision=not mid.startswith(("gpt-3.5", "o1-mini", "o3-mini")),
                    effort=is_reasoning(mid),
                )
            )
        return out

    def params(self, request: ChatRequest) -> dict[str, Any]:
        """The Responses API request body for ``request``."""
        names = {}
        items: list[dict[str, Any]] = []
        for message in request.messages:
            items.extend(_items(message, names))
        params: dict[str, Any] = {
            "model": request.model,
            "input": items,
            "max_output_tokens": request.max_tokens,
            "store": False,
        }
        if request.system:
            params["instructions"] = request.system
        if request.tools:
            params["tools"] = [
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": strict_schema(tool.input_schema, require_all=True),
                    "strict": True,
                }
                for tool in request.tools
            ]
        if request.response_schema is not None:
            params["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "reply",
                    "schema": strict_schema(request.response_schema, require_all=True),
                    "strict": True,
                }
            }
        if is_reasoning(request.model):
            params["include"] = ["reasoning.encrypted_content"]
            if request.effort:
                params["reasoning"] = {"effort": request.effort}
        return params

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        validate_request(request, PROVIDER)
        params = self.params(request)
        final: Any = None
        try:
            async with self._client.responses.stream(**params) as stream:
                async for event in stream:
                    if event.type == "response.output_text.delta" and event.delta:
                        yield TextDelta(event.delta)
                    elif event.type in {"response.completed", "response.incomplete"}:
                        # A reply cut short ends with response.incomplete, which
                        # the SDK's get_final_response() does not accept.
                        final = event.response
                    elif event.type == "response.failed":
                        error = event.response.error
                        message = (
                            getattr(error, "message", None) or "OpenAI could not finish the reply."
                        )
                        raise ProviderError(
                            "unknown", message, provider=PROVIDER, code="llm_failed"
                        )
        except openai.APIStatusError as exc:
            raise _status_error(exc) from exc
        except openai.APIConnectionError as exc:
            raise connection_error(
                PROVIDER, exc, timeout=isinstance(exc, openai.APITimeoutError)
            ) from exc
        if final is None:
            raise ProviderError(
                "unknown",
                "The OpenAI stream ended without a reply.",
                provider=PROVIDER,
                code="llm_no_reply",
            )
        response = _response(final)
        for call in response.tool_calls:
            yield ToolCallEvent(call)
        yield Done(response)

    async def complete(self, request: ChatRequest) -> ChatResponse:
        return await collect(self.stream(request))


def _status_error(exc: openai.APIStatusError) -> ProviderError:
    retry_after = exc.response.headers.get("retry-after") if exc.response is not None else None
    return from_status(PROVIDER, exc.status_code, exc.body or exc.message, retry_after)


def _items(message: Message, names: dict[str, str]) -> list[dict[str, Any]]:
    native = message.native.get(PROVIDER)
    if message.role == "assistant" and native:
        return list(native["output"])
    items: list[dict[str, Any]] = []
    content: list[dict[str, Any]] = []
    for part in message.parts:
        if isinstance(part, TextPart) and part.text:
            kind = "output_text" if message.role == "assistant" else "input_text"
            content.append({"type": kind, "text": part.text})
        elif isinstance(part, ImagePart):
            data = base64.standard_b64encode(part.data).decode()
            content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:{part.mime};base64,{data}",
                    "detail": "auto",
                }
            )
        elif isinstance(part, ToolCall):
            names[part.id] = part.name
            items.append(
                {
                    "type": "function_call",
                    "call_id": part.id,
                    "name": part.name,
                    "arguments": json.dumps(part.arguments),
                }
            )
        elif isinstance(part, ToolResult):
            output = f"Error: {part.content}" if part.is_error else part.content
            items.append(
                {"type": "function_call_output", "call_id": part.call_id, "output": output}
            )
    if content:
        items.insert(0, {"role": message.role, "content": content})
    return items


def _response(final: Any) -> ChatResponse:
    text_parts: list[str] = []
    calls: list[ToolCall] = []
    refused = False
    for item in final.output:
        if item.type == "message":
            for part in item.content:
                if part.type == "output_text":
                    text_parts.append(part.text)
                elif part.type == "refusal":
                    refused = True
        elif item.type == "function_call":
            try:
                arguments = json.loads(item.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {"_raw": item.arguments}
            calls.append(ToolCall(item.call_id, item.name, arguments))
    reason = getattr(final.incomplete_details, "reason", None) if final.incomplete_details else None
    stop: StopReason
    if refused or reason == "content_filter":
        stop = "refusal"
    elif reason == "max_output_tokens":
        stop = "max_tokens"
    elif calls:
        stop = "tool_use"
    else:
        stop = "end"
    text = "".join(text_parts)
    native = {"output": [item.to_dict(mode="json") for item in final.output]}
    usage = final.usage
    cached = (
        (usage.input_tokens_details.cached_tokens or 0)
        if usage and usage.input_tokens_details
        else 0
    )
    return ChatResponse(
        message=Message(
            "assistant", tuple([TextPart(text)] if text else []) + tuple(calls), {PROVIDER: native}
        ),
        stop_reason=stop,
        usage=Usage(
            input_tokens=max(0, (usage.input_tokens if usage else 0) - cached),
            output_tokens=usage.output_tokens if usage else 0,
            cache_read_tokens=cached,
        ),
        model=final.model,
        provider=PROVIDER,
    )
