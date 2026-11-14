"""Claude through the official ``anthropic`` SDK (plan step M2.4).

* Default model ``claude-opus-5-5``. Its thinking is always on (adaptive);
  depth is set with ``output_config.effort`` when a request asks for one
  (otherwise the model's default, ``medium`` on Opus 5.5, applies).
* Tools are sent with ``strict: true`` and the default ``tool_choice`` (auto):
  forcing a tool is refused on current models.
* Structured output uses ``output_config.format`` with a JSON schema.
* The stable prefix (tools, system prompt) carries a cache breakpoint.
* A refusal is a reply with ``stop_reason: "refusal"``, not an exception.
* Each reply's content blocks (thinking blocks with their signatures
  included) are kept in ``Message.native`` and sent back unchanged.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from typing import Any

import anthropic
import httpx2

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

PROVIDER = "anthropic"
DEFAULT_MODEL = "claude-opus-5-5"
_STOP: dict[str, StopReason] = {
    "end_turn": "end",
    "stop_sequence": "end",
    "pause_turn": "end",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "model_context_window_exceeded": "max_tokens",
    "refusal": "refusal",
}


class AnthropicLLM:
    id = PROVIDER

    def __init__(
        self,
        api_key: str,
        transport: httpx2.AsyncBaseTransport,
        *,
        max_retries: int = 2,
    ) -> None:
        # The transport comes from the outbound policy (framestudio.net).
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key,
            http_client=anthropic.DefaultAsyncHttpxClient(transport=transport),
            max_retries=max_retries,
        )

    # ------------------------------------------------------------ models

    async def list_models(self) -> list[ModelInfo]:
        try:
            models = [model async for model in self._client.models.list(limit=100)]
        except anthropic.APIStatusError as exc:
            raise _status_error(exc) from exc
        except anthropic.APIConnectionError as exc:
            raise connection_error(
                PROVIDER, exc, timeout=isinstance(exc, anthropic.APITimeoutError)
            ) from exc
        out = []
        for model in models:
            caps = model.capabilities
            out.append(
                ModelInfo(
                    id=model.id,
                    name=model.display_name or model.id,
                    provider=PROVIDER,
                    vision=bool(caps.image_input.supported) if caps else True,
                    tools=True,
                    structured_output=bool(caps.structured_outputs.supported) if caps else True,
                    effort=bool(caps.effort.supported) if caps else True,
                    context_tokens=model.max_input_tokens,
                    max_output_tokens=model.max_tokens,
                )
            )
        return out

    # ------------------------------------------------------------ requests

    def params(self, request: ChatRequest) -> dict[str, Any]:
        """The Messages API request body for ``request``."""
        params: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "messages": [_message(m) for m in request.messages],
        }
        cache = {"type": "ephemeral"}
        if request.tools:
            tools = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": strict_schema(tool.input_schema),
                    "strict": True,
                }
                for tool in request.tools
            ]
            if request.cache and not request.system:
                tools[-1]["cache_control"] = cache
            params["tools"] = tools
        if request.system:
            block: dict[str, Any] = {"type": "text", "text": request.system}
            if request.cache:
                block["cache_control"] = cache  # caches tools + system together
            params["system"] = [block]
        output_config: dict[str, Any] = {}
        if request.effort:
            output_config["effort"] = request.effort
        if request.response_schema is not None:
            output_config["format"] = {
                "type": "json_schema",
                "schema": strict_schema(request.response_schema),
            }
        if output_config:
            params["output_config"] = output_config
        return params

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        validate_request(request, PROVIDER)
        params = self.params(request)
        try:
            async with self._client.messages.stream(**params) as stream:
                async for event in stream:
                    if (
                        event.type == "content_block_delta"
                        and event.delta.type == "text_delta"
                        and event.delta.text
                    ):
                        yield TextDelta(event.delta.text)
                final = await stream.get_final_message()
        except anthropic.APIStatusError as exc:
            raise _status_error(exc) from exc
        except anthropic.APIConnectionError as exc:
            raise connection_error(
                PROVIDER, exc, timeout=isinstance(exc, anthropic.APITimeoutError)
            ) from exc
        response = _response(final)
        for call in response.tool_calls:
            yield ToolCallEvent(call)
        yield Done(response)

    async def complete(self, request: ChatRequest) -> ChatResponse:
        return await collect(self.stream(request))


def _status_error(exc: anthropic.APIStatusError) -> ProviderError:
    retry_after = exc.response.headers.get("retry-after") if exc.response is not None else None
    return from_status(PROVIDER, exc.status_code, exc.body or exc.message, retry_after)


def _message(message: Message) -> dict[str, Any]:
    native = message.native.get(PROVIDER)
    if message.role == "assistant" and native:
        return {"role": "assistant", "content": native["content"]}
    content: list[dict[str, Any]] = []
    for part in message.parts:
        if isinstance(part, TextPart):
            if part.text:
                content.append({"type": "text", "text": part.text})
        elif isinstance(part, ImagePart):
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": part.mime,
                        "data": base64.standard_b64encode(part.data).decode(),
                    },
                }
            )
        elif isinstance(part, ToolCall):
            content.append(
                {"type": "tool_use", "id": part.id, "name": part.name, "input": part.arguments}
            )
        elif isinstance(part, ToolResult):
            content.append(
                {
                    "type": "tool_result",
                    "tool_use_id": part.call_id,
                    "content": part.content,
                    "is_error": part.is_error,
                }
            )
    # Tool results lead their user turn.
    content.sort(key=lambda block: block["type"] != "tool_result")
    return {"role": message.role, "content": content}


def _response(final: Any) -> ChatResponse:
    text = "".join(block.text for block in final.content if block.type == "text")
    calls = [
        ToolCall(block.id, block.name, dict(block.input or {}))
        for block in final.content
        if block.type == "tool_use"
    ]
    native = {"content": [block.to_dict(mode="json") for block in final.content]}
    message = Message(
        "assistant", tuple([TextPart(text)] if text else []) + tuple(calls), {PROVIDER: native}
    )
    usage = final.usage
    return ChatResponse(
        message=message,
        stop_reason=_STOP.get(final.stop_reason or "end_turn", "end"),
        usage=Usage(
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
            cache_read_tokens=usage.cache_read_input_tokens or 0,
            cache_write_tokens=usage.cache_creation_input_tokens or 0,
        ),
        model=final.model,
        provider=PROVIDER,
    )
