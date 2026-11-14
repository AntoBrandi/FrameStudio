"""Pieces every LLM adapter shares: strict JSON schemas, mapping SDK errors
onto the provider error taxonomy, and finding a tool call's name."""

from __future__ import annotations

import copy
from collections.abc import Iterable
from typing import Any

from framestudio.llm.base import Message, ToolCall
from framestudio.net import OutboundBlocked
from framestudio.providers.base import ProviderError, classify_http


def strict_schema(schema: dict[str, Any], *, require_all: bool = False) -> dict[str, Any]:
    """A copy of ``schema`` that strict tool use and structured outputs
    accept: every object closes with ``additionalProperties: false``.

    OpenAI's strict mode also needs every property listed in ``required``
    (``require_all``); properties that were optional become nullable.
    """
    out = copy.deepcopy(schema)

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" or "properties" in node:
                props: dict[str, Any] = node.setdefault("properties", {})
                node["additionalProperties"] = False
                if require_all:
                    required = set(node.get("required", []))
                    for name, prop in props.items():
                        if name not in required and isinstance(prop, dict):
                            kind = prop.get("type")
                            if isinstance(kind, str) and kind != "null":
                                prop["type"] = [kind, "null"]
                            elif isinstance(kind, list) and "null" not in kind:
                                prop["type"] = [*kind, "null"]
                    node["required"] = list(props)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(out)
    return out


def blocked_by_policy(exc: BaseException) -> OutboundBlocked | None:
    """The outbound-policy refusal behind an SDK connection error, if any."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, OutboundBlocked):
            return current
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return None


def from_status(
    provider: str, status: int, body: object, retry_after: str | None = None
) -> ProviderError:
    error = classify_http(status, str(body or ""), retry_after)
    error.provider = provider
    return error


def connection_error(provider: str, exc: BaseException, *, timeout: bool = False) -> ProviderError:
    blocked = blocked_by_policy(exc)
    if blocked is not None:
        return ProviderError(
            "unavailable", str(blocked), provider=provider, code="outbound_blocked"
        )
    message = "did not answer in time" if timeout else "could not be reached"
    return ProviderError(
        "transient", f"{provider} {message}. Check the network.", provider=provider
    )


def tool_names(messages: Iterable[Message]) -> dict[str, str]:
    """Tool call id -> tool name, for APIs whose results name the tool."""
    names: dict[str, str] = {}
    for message in messages:
        for part in message.parts:
            if isinstance(part, ToolCall):
                names[part.id] = part.name
    return names
