"""The LLM provider protocol (plan step M2.2).

A request is a list of messages made of parts (text, images, tool calls, tool
results) plus optional tools and an optional JSON schema for structured
output. Providers stream events (text deltas, finished tool calls, then one
``Done`` with the whole response) and report usage. Failures are
:class:`~framestudio.providers.base.ProviderError` with the same taxonomy as
generation providers; a refusal is a normal response with
``stop_reason == "refusal"``, not an exception.

Adapters (M2.4) map this onto each vendor's API; the contract suite in
``tests/contracts`` holds them all to the same behaviour.
"""

from __future__ import annotations

import json
import mimetypes
import re
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from framestudio.providers.base import ProviderError

Role = Literal["user", "assistant"]
StopReason = Literal["end", "tool_use", "max_tokens", "refusal"]
Effort = Literal["low", "medium", "high", "xhigh", "max"]
IMAGE_MIMES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})
TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class TextPart:
    text: str


@dataclass(frozen=True)
class ImagePart:
    data: bytes = field(repr=False)
    mime: str

    @classmethod
    def from_file(cls, path: Path, mime: str | None = None) -> ImagePart:
        guessed = mime or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return cls(data=path.read_bytes(), mime=guessed)


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    content: str
    is_error: bool = False


Part = TextPart | ImagePart | ToolCall | ToolResult


@dataclass(frozen=True)
class Message:
    role: Role
    parts: tuple[Part, ...]
    #: The reply exactly as each provider sent it (``provider id -> payload``),
    #: replayed unchanged when the conversation goes back to that provider:
    #: Claude's thinking blocks, OpenAI's reasoning items and Gemini's thought
    #: signatures must come back as they were. Other providers use ``parts``.
    native: dict[str, Any] = field(default_factory=dict, compare=False)

    @classmethod
    def user(cls, text: str = "", *images: ImagePart) -> Message:
        parts: list[Part] = list(images)
        if text:
            parts.append(TextPart(text))
        return cls("user", tuple(parts))

    @classmethod
    def assistant(cls, text: str = "", *calls: ToolCall) -> Message:
        parts: list[Part] = [TextPart(text)] if text else []
        parts.extend(calls)
        return cls("assistant", tuple(parts))

    @classmethod
    def tool_results(cls, *results: ToolResult) -> Message:
        return cls("user", tuple(results))

    @property
    def text(self) -> str:
        return "".join(p.text for p in self.parts if isinstance(p, TextPart))

    @property
    def tool_calls(self) -> list[ToolCall]:
        return [p for p in self.parts if isinstance(p, ToolCall)]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    #: JSON schema of the arguments (an object schema).
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ChatRequest:
    model: str
    messages: tuple[Message, ...]
    system: str | None = None
    tools: tuple[ToolSpec, ...] = ()
    #: A JSON schema: the reply's text is a JSON document matching it.
    response_schema: dict[str, Any] | None = None
    max_tokens: int = 4096
    #: How hard the model thinks, where the provider supports it. ``None``
    #: leaves the provider's default.
    effort: Effort | None = None
    #: Mark the stable prefix (system, tools) cacheable where supported.
    cache: bool = True


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
        )


@dataclass(frozen=True)
class ChatResponse:
    message: Message
    stop_reason: StopReason
    usage: Usage
    #: The model that answered (providers may resolve aliases).
    model: str
    provider: str = ""

    @property
    def text(self) -> str:
        return self.message.text

    @property
    def tool_calls(self) -> list[ToolCall]:
        return self.message.tool_calls

    @property
    def refused(self) -> bool:
        return self.stop_reason == "refusal"

    def parsed(self) -> Any:
        """The structured reply as JSON. Raises :class:`ProviderError` when the
        model refused, ran out of tokens or returned something else."""
        if self.refused:
            raise ProviderError(
                "content_policy", "The model declined to answer.", provider=self.provider
            )
        if self.stop_reason == "max_tokens":
            raise ProviderError(
                "invalid_params",
                "The reply was cut off before it finished. Allow more tokens.",
                provider=self.provider,
                code="llm_truncated",
            )
        text = self.text.strip()
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
        if fenced:
            text = fenced.group(1)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderError(
                "unknown",
                "The model's reply was not the JSON it was asked for.",
                provider=self.provider,
                code="llm_bad_json",
            ) from exc


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolCallEvent:
    call: ToolCall


@dataclass(frozen=True)
class Done:
    response: ChatResponse


StreamEvent = TextDelta | ToolCallEvent | Done


@dataclass(frozen=True)
class ModelInfo:
    id: str
    name: str
    provider: str
    vision: bool = True
    tools: bool = True
    structured_output: bool = True
    #: Accepts :attr:`ChatRequest.effort`.
    effort: bool = True
    context_tokens: int | None = None
    max_output_tokens: int | None = None


@runtime_checkable
class LLMProvider(Protocol):
    id: str

    async def list_models(self) -> list[ModelInfo]: ...

    def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        """Events for one turn, ending with exactly one :class:`Done`."""
        ...

    async def complete(self, request: ChatRequest) -> ChatResponse: ...


async def collect(events: AsyncIterator[StreamEvent]) -> ChatResponse:
    """Drain a stream to its final response (a ``complete`` built on ``stream``)."""
    response: ChatResponse | None = None
    async for event in events:
        if isinstance(event, Done):
            response = event.response
    if response is None:
        raise ProviderError(
            "unknown", "The model stream ended without a reply.", code="llm_no_reply"
        )
    return response


def validate_request(request: ChatRequest, provider: str = "") -> None:
    """Problems every provider would refuse, caught before anything is sent."""
    problems: list[str] = []
    if not request.model:
        problems.append("no model chosen")
    if not request.messages:
        problems.append("no messages")
    elif request.messages[0].role != "user":
        problems.append("the first message must come from the user")
    if request.max_tokens < 1:
        problems.append("max_tokens must be at least 1")
    names = [tool.name for tool in request.tools]
    if len(set(names)) != len(names):
        problems.append("tool names must be unique")
    for tool in request.tools:
        if not TOOL_NAME.match(tool.name):
            problems.append(f"tool name '{tool.name}' must be 1-64 letters, digits, _ or -")
        if tool.input_schema.get("type") != "object":
            problems.append(f"tool '{tool.name}' must take an object")
    if request.response_schema is not None and request.response_schema.get("type") != "object":
        problems.append("response_schema must describe an object")
    open_calls: set[str] = set()
    for message in request.messages:
        for part in message.parts:
            if isinstance(part, ImagePart) and part.mime not in IMAGE_MIMES:
                problems.append(f"images must be PNG, JPEG, WebP or GIF, not {part.mime}")
            if isinstance(part, ToolCall):
                if message.role != "assistant":
                    problems.append("only the assistant makes tool calls")
                open_calls.add(part.id)
            if isinstance(part, ToolResult):
                if message.role != "user":
                    problems.append("tool results go in a user message")
                if part.call_id not in open_calls:
                    problems.append(f"tool result for unknown call '{part.call_id}'")
                open_calls.discard(part.call_id)
    if problems:
        raise ProviderError(
            "invalid_params", "; ".join(problems).capitalize() + ".", provider=provider
        )


def last_user_text(messages: Sequence[Message]) -> str:
    for message in reversed(messages):
        if message.role == "user" and message.text:
            return message.text
    return ""
