"""A scripted language model that ships with the product (decision D8): tests,
end-to-end runs and the offline demo use it instead of a paid API.

Each call takes the next scripted :class:`Turn` (or calls a function of the
request to make one). With the script empty it falls back to ``default``,
which echoes the prompt or, for structured output, returns the smallest JSON
document matching the schema. Every request is recorded in ``requests``.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections import deque
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

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
    ToolCall,
    ToolCallEvent,
    Usage,
    collect,
    last_user_text,
    validate_request,
)
from framestudio.providers.base import ProviderError


@dataclass(frozen=True)
class Turn:
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    #: Inferred when omitted: ``tool_use`` with tool calls, else ``end``.
    stop_reason: StopReason | None = None
    #: Raised instead of answering.
    error: ProviderError | None = None
    usage: Usage | None = None


Script = Turn | Callable[[ChatRequest], Turn]

FAKE_MODELS = (
    ModelInfo(
        id="fake-large",
        name="Fake large",
        provider="fake",
        context_tokens=200_000,
        max_output_tokens=32_000,
    ),
    ModelInfo(
        id="fake-small",
        name="Fake small",
        provider="fake",
        vision=False,
        context_tokens=32_000,
        max_output_tokens=4_096,
    ),
)


def sample_for_schema(schema: dict[str, Any]) -> Any:
    """The smallest value matching a (simple) JSON schema."""
    if "const" in schema:
        return schema["const"]
    if schema.get("enum"):
        return schema["enum"][0]
    for key in ("anyOf", "oneOf"):
        if schema.get(key):
            return sample_for_schema(schema[key][0])
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "null")
    if kind == "object":
        props: dict[str, Any] = schema.get("properties", {})
        return {name: sample_for_schema(props.get(name, {})) for name in schema.get("required", [])}
    if kind == "array":
        count = int(schema.get("minItems", 0))
        return [sample_for_schema(schema.get("items", {})) for _ in range(count)]
    if kind == "string":
        return "x" * int(schema.get("minLength", 4) or 4)
    if kind in {"integer", "number"}:
        value = schema.get("minimum", 0)
        return int(value) if kind == "integer" else float(value)
    if kind == "boolean":
        return False
    return None


def echo(request: ChatRequest) -> Turn:
    if request.response_schema is not None:
        return Turn(text=json.dumps(sample_for_schema(request.response_schema)))
    images = sum(
        isinstance(p, ImagePart) for m in request.messages for p in m.parts if m.role == "user"
    )
    seen = f" (with {images} image{'s' if images != 1 else ''})" if images else ""
    return Turn(text=f"Fake reply to: {last_user_text(request.messages)}{seen}")


def demo(request: ChatRequest) -> Turn:
    """Plausible answers for the app's own features, so the offline demo
    (FRAMESTUDIO_FAKE_PROVIDERS) can be tried end to end without a key."""
    schema = request.response_schema or {}
    props = schema.get("properties", {})
    text = last_user_text(request.messages)
    if "prompt" in props and "changes" in props:
        original = text.rsplit("Prompt to improve:", 1)[-1].strip() or text
        improved = (
            f"{original}, framed as a medium-wide shot, soft directional light from the left, "
            "muted natural colours, fine detail"
        )
        changes = ["Added framing", "Described the light", "Set the colour palette"]
        return Turn(text=json.dumps({"prompt": improved, "changes": changes}))
    if "caption" in props:
        images = sum(isinstance(p, ImagePart) for m in request.messages for p in m.parts)
        caption = (
            "A picture from the library, described offline." if images else "A described item."
        )
        return Turn(text=json.dumps({"caption": caption, "tags": ["offline", "demo"]}))
    if "names" in props:
        return Turn(text=json.dumps({"names": ["Untitled study", "Quiet frame", "Library still"]}))
    return echo(request)


@dataclass
class FakeLLM:
    script: Iterable[Script] = ()
    default: Callable[[ChatRequest], Turn] | None = echo
    id: str = "fake"
    models: tuple[ModelInfo, ...] = FAKE_MODELS
    #: Characters per streamed text delta.
    chunk: int = 8
    #: Seconds between deltas (0 in tests; small in the demo so streaming shows).
    delay: float = 0.0
    #: Raised by every call, e.g. to stand in for a revoked key.
    fail_with: ProviderError | None = None
    requests: list[ChatRequest] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._pending: deque[Script] = deque(self.script)

    def queue(self, *turns: Script) -> None:
        self._pending.extend(turns)

    async def list_models(self) -> list[ModelInfo]:
        if self.fail_with:
            raise self.fail_with
        return list(self.models)

    def _next(self, request: ChatRequest) -> Turn:
        if self._pending:
            item = self._pending.popleft()
            return item(request) if callable(item) else item
        if self.default is None:
            raise AssertionError("FakeLLM script ran out and has no default")
        return self.default(request)

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        if self.fail_with:
            raise self.fail_with
        validate_request(request, self.id)
        if request.model not in {m.id for m in self.models}:
            raise ProviderError("not_found", f"Unknown model '{request.model}'.", provider=self.id)
        self.requests.append(request)
        turn = self._next(request)
        if turn.error:
            raise turn.error
        text = turn.text
        stop = turn.stop_reason or ("tool_use" if turn.tool_calls else "end")
        if stop != "max_tokens" and len(text) > request.max_tokens * 4:
            text, stop = text[: request.max_tokens * 4], "max_tokens"
        for start in range(0, len(text), max(1, self.chunk)):
            if self.delay:
                await asyncio.sleep(self.delay)
            yield TextDelta(text[start : start + self.chunk])
        calls = tuple(
            call
            if call.id
            else ToolCall(f"call_{uuid.uuid4().hex[:12]}", call.name, call.arguments)
            for call in turn.tool_calls
        )
        for call in calls:
            yield ToolCallEvent(call)
        prompt_chars = sum(len(m.text) for m in request.messages) + len(request.system or "")
        usage = turn.usage or Usage(
            input_tokens=max(1, prompt_chars // 4), output_tokens=max(1, len(text) // 4)
        )
        message = Message.assistant(text, *calls)
        yield Done(ChatResponse(message, stop, usage, model=request.model, provider=self.id))

    async def complete(self, request: ChatRequest) -> ChatResponse:
        return await collect(self.stream(request))
