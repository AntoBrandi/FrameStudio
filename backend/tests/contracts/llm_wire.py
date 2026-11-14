"""Recorded HTTP exchanges for the LLM contract suite (plan step M2.4).

Each case of ``test_llm_contract.py`` has one fixture per provider in
``fixtures/llm/<provider>/<case>.json``: the requests the adapter is expected
to make and the provider's responses, replayed through the real SDK by
:class:`Replay`. Fixtures marked ``"synthetic": true`` are written by
:func:`generate` in each provider's documented wire format; running the suite
with ``LIVE_API=1 FRAMESTUDIO_RECORD=1`` and real keys replaces them with
recordings (``"synthetic": false``), which ``generate`` never overwrites.
Recordings keep request bodies and response bodies only: no headers, so no keys.

    uv run python -m tests.contracts.llm_wire     # rewrite the synthetic fixtures
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx2

FIXTURES = Path(__file__).parent / "fixtures" / "llm"
MODELS = {"anthropic": "claude-opus-5-5", "openai": "gpt-5", "google": "gemini-3-pro"}


# ---------------------------------------------------------------- replay and record


@dataclass
class Exchange:
    method: str
    path: str
    status: int
    body: Any
    content_type: str = "application/json"
    request: Any = None


def load(provider: str, case: str) -> list[Exchange]:
    data = json.loads((FIXTURES / provider / f"{case}.json").read_text(encoding="utf-8"))
    return [Exchange(**exchange) for exchange in data["exchanges"]]


class Replay(httpx2.AsyncBaseTransport):
    """Answers the adapter's requests from a fixture, in order, checking that
    each one goes where the fixture says."""

    def __init__(self, exchanges: list[Exchange]) -> None:
        self.pending = list(exchanges)
        self.requests: list[httpx2.Request] = []
        self.bodies: list[Any] = []

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        body = await request.aread()
        self.requests.append(request)
        self.bodies.append(json.loads(body) if body else None)
        if not self.pending:
            raise AssertionError(f"unexpected request {request.method} {request.url}")
        exchange = self.pending.pop(0)
        assert (request.method, request.url.path) == (exchange.method, exchange.path), (
            f"expected {exchange.method} {exchange.path}, got {request.method} {request.url.path}"
        )
        content = exchange.body if isinstance(exchange.body, str) else json.dumps(exchange.body)
        return httpx2.Response(
            exchange.status,
            headers={"content-type": exchange.content_type},
            content=content.encode(),
        )


@dataclass
class Recorder(httpx2.AsyncBaseTransport):
    """Passes requests to the network and writes what came back as a fixture."""

    provider: str
    case: str
    inner: httpx2.AsyncBaseTransport = field(default_factory=lambda: httpx2.AsyncHTTPTransport())
    exchanges: list[dict[str, Any]] = field(default_factory=list)

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        sent = await request.aread()
        response = await self.inner.handle_async_request(request)
        content = await response.aread()
        content_type = response.headers.get("content-type", "application/json").split(";")[0]
        text = content.decode("utf-8", errors="replace")
        self.exchanges.append(
            {
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "content_type": content_type,
                "body": json.loads(text) if content_type == "application/json" and text else text,
                "request": json.loads(sent) if sent else None,
            }
        )
        self.save()
        return httpx2.Response(response.status_code, headers=response.headers, content=content)

    def save(self) -> None:
        write(self.provider, self.case, self.exchanges, synthetic=False)


def write(provider: str, case: str, exchanges: list[dict[str, Any]], *, synthetic: bool) -> None:
    path = FIXTURES / provider / f"{case}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    note = (
        "Written from the provider's documented wire format; replace with a recording "
        "(LIVE_API=1 FRAMESTUDIO_RECORD=1)."
        if synthetic
        else "Recorded from the live API."
    )
    data = {"synthetic": synthetic, "note": note, "model": MODELS[provider], "exchanges": exchanges}
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- the scenarios

TEXT = "Hello! A lighthouse at dusk sounds lovely."
TOOL_ANSWER = "It is 21°C and clear in Lisbon."
SHOT = {
    "subject": "a lighthouse at dusk",
    "camera": "dolly in",
    "duration_seconds": 5,
    "tags": ["dusk", "coast"],
}
LONG = "Once upon a time, a lighthouse keeper "
SIGNATURE = "EqQBCkYIARgCIkAsynthetic-signature"


def _chunks(text: str, size: int = 9) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


def _sse(events: list[dict[str, Any]], named: bool = True) -> str:
    out = []
    for event in events:
        prefix = f"event: {event['type']}\n" if named else ""
        out.append(f"{prefix}data: {json.dumps(event)}\n\n")
    return "".join(out)


# -- Anthropic Messages API (SSE)


def _anthropic_stream(
    blocks: list[dict[str, Any]], stop: str, *, output_tokens: int = 12, stop_details: Any = None
) -> str:
    events: list[dict[str, Any]] = [
        {
            "type": "message_start",
            "message": {
                "id": "msg_01Synthetic",
                "type": "message",
                "role": "assistant",
                "model": MODELS["anthropic"],
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 42,
                    "output_tokens": 1,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                },
            },
        }
    ]
    for index, block in enumerate(blocks):
        if block["type"] == "thinking":
            events.append(
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {"type": "thinking", "thinking": "", "signature": ""},
                }
            )
            events.append(
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": "signature_delta", "signature": block["signature"]},
                }
            )
        elif block["type"] == "text":
            events.append(
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {"type": "text", "text": ""},
                }
            )
            for piece in _chunks(block["text"]):
                events.append(
                    {
                        "type": "content_block_delta",
                        "index": index,
                        "delta": {"type": "text_delta", "text": piece},
                    }
                )
        elif block["type"] == "tool_use":
            events.append(
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {
                        "type": "tool_use",
                        "id": block["id"],
                        "name": block["name"],
                        "input": {},
                    },
                }
            )
            for piece in _chunks(json.dumps(block["input"]), 7):
                events.append(
                    {
                        "type": "content_block_delta",
                        "index": index,
                        "delta": {"type": "input_json_delta", "partial_json": piece},
                    }
                )
        events.append({"type": "content_block_stop", "index": index})
    delta: dict[str, Any] = {"stop_reason": stop, "stop_sequence": None}
    if stop_details is not None:
        delta["stop_details"] = stop_details
    events.append(
        {"type": "message_delta", "delta": delta, "usage": {"output_tokens": output_tokens}}
    )
    events.append({"type": "message_stop"})
    return _sse(events)


def _anthropic() -> dict[str, list[dict[str, Any]]]:
    post = {
        "method": "POST",
        "path": "/v1/messages",
        "status": 200,
        "content_type": "text/event-stream",
    }
    thinking = {"type": "thinking", "signature": SIGNATURE}
    capabilities = {
        "batch": {"supported": True},
        "citations": {"supported": True},
        "code_execution": {"supported": True},
        "image_input": {"supported": True},
        "pdf_input": {"supported": True},
        "structured_outputs": {"supported": True},
        "effort": {
            "supported": True,
            "low": {"supported": True},
            "medium": {"supported": True},
            "high": {"supported": True},
            "xhigh": {"supported": True},
            "max": {"supported": True},
        },
    }
    return {
        "text": [
            {
                **post,
                "body": _anthropic_stream([thinking, {"type": "text", "text": TEXT}], "end_turn"),
            }
        ],
        "tool": [
            {
                **post,
                "body": _anthropic_stream(
                    [
                        thinking,
                        {
                            "type": "tool_use",
                            "id": "toolu_01Synthetic",
                            "name": "get_weather",
                            "input": {"city": "Lisbon"},
                        },
                    ],
                    "tool_use",
                ),
            },
            {
                **post,
                "body": _anthropic_stream([{"type": "text", "text": TOOL_ANSWER}], "end_turn"),
            },
        ],
        "image": [
            {**post, "body": _anthropic_stream([{"type": "text", "text": "Red."}], "end_turn")}
        ],
        "structured": [
            {
                **post,
                "body": _anthropic_stream([{"type": "text", "text": json.dumps(SHOT)}], "end_turn"),
            }
        ],
        "refusal": [
            {
                **post,
                "body": _anthropic_stream(
                    [],
                    "refusal",
                    output_tokens=0,
                    stop_details={"type": "refusal", "category": None, "explanation": "Declined."},
                ),
            }
        ],
        "max_tokens": [
            {
                **post,
                "body": _anthropic_stream(
                    [{"type": "text", "text": LONG}], "max_tokens", output_tokens=16
                ),
            }
        ],
        "auth": [
            {
                "method": "POST",
                "path": "/v1/messages",
                "status": 401,
                "body": {
                    "type": "error",
                    "error": {"type": "authentication_error", "message": "invalid x-api-key"},
                },
            }
        ],
        "models": [
            {
                "method": "GET",
                "path": "/v1/models",
                "status": 200,
                "body": {
                    "data": [
                        {
                            "type": "model",
                            "id": "claude-opus-5-5",
                            "display_name": "Claude Opus 5.5",
                            "created_at": "2026-10-01T00:00:00Z",
                            "max_input_tokens": 1000000,
                            "max_tokens": 128000,
                            "capabilities": capabilities,
                        }
                    ],
                    "has_more": False,
                    "first_id": "claude-opus-5-5",
                    "last_id": "claude-opus-5-5",
                },
            }
        ],
    }


# -- OpenAI Responses API (SSE)


def _openai_response(
    output: list[dict[str, Any]],
    status: str = "completed",
    incomplete: Any = None,
    output_tokens: int = 12,
) -> dict[str, Any]:
    return {
        "id": "resp_synthetic",
        "object": "response",
        "created_at": 1763000000,
        "status": status,
        "model": MODELS["openai"],
        "output": output,
        "incomplete_details": incomplete,
        "error": None,
        "instructions": None,
        "metadata": {},
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "store": False,
        "text": {"format": {"type": "text"}},
        "usage": {
            "input_tokens": 42,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": output_tokens,
            "output_tokens_details": {"reasoning_tokens": 4},
            "total_tokens": 42 + output_tokens,
        },
    }


def _openai_stream(
    output: list[dict[str, Any]], status: str = "completed", incomplete: Any = None
) -> str:
    events: list[dict[str, Any]] = [
        {"type": "response.created", "response": _openai_response([], "in_progress")}
    ]
    for index, item in enumerate(output):
        if item["type"] == "message":
            events.append(
                {
                    "type": "response.output_item.added",
                    "output_index": index,
                    "item": {**item, "status": "in_progress", "content": []},
                }
            )
            for c_index, part in enumerate(item["content"]):
                empty = (
                    {**part, "text": ""}
                    if part["type"] == "output_text"
                    else {**part, "refusal": ""}
                )
                events.append(
                    {
                        "type": "response.content_part.added",
                        "item_id": item["id"],
                        "output_index": index,
                        "content_index": c_index,
                        "part": empty,
                    }
                )
                if part["type"] == "output_text":
                    for piece in _chunks(part["text"]):
                        events.append(
                            {
                                "type": "response.output_text.delta",
                                "item_id": item["id"],
                                "output_index": index,
                                "content_index": c_index,
                                "delta": piece,
                                "logprobs": [],
                            }
                        )
                    events.append(
                        {
                            "type": "response.output_text.done",
                            "item_id": item["id"],
                            "output_index": index,
                            "content_index": c_index,
                            "text": part["text"],
                            "logprobs": [],
                        }
                    )
                else:
                    events.append(
                        {
                            "type": "response.refusal.delta",
                            "item_id": item["id"],
                            "output_index": index,
                            "content_index": c_index,
                            "delta": part["refusal"],
                        }
                    )
                    events.append(
                        {
                            "type": "response.refusal.done",
                            "item_id": item["id"],
                            "output_index": index,
                            "content_index": c_index,
                            "refusal": part["refusal"],
                        }
                    )
                events.append(
                    {
                        "type": "response.content_part.done",
                        "item_id": item["id"],
                        "output_index": index,
                        "content_index": c_index,
                        "part": part,
                    }
                )
        elif item["type"] == "function_call":
            events.append(
                {
                    "type": "response.output_item.added",
                    "output_index": index,
                    "item": {**item, "arguments": "", "status": "in_progress"},
                }
            )
            for piece in _chunks(item["arguments"], 7):
                events.append(
                    {
                        "type": "response.function_call_arguments.delta",
                        "item_id": item["id"],
                        "output_index": index,
                        "delta": piece,
                    }
                )
            events.append(
                {
                    "type": "response.function_call_arguments.done",
                    "item_id": item["id"],
                    "output_index": index,
                    "arguments": item["arguments"],
                    "name": item["name"],
                }
            )
        else:
            events.append(
                {"type": "response.output_item.added", "output_index": index, "item": item}
            )
        events.append({"type": "response.output_item.done", "output_index": index, "item": item})
    final = "response.completed" if status == "completed" else "response.incomplete"
    events.append({"type": final, "response": _openai_response(output, status, incomplete)})
    for number, event in enumerate(events):
        event["sequence_number"] = number
    return _sse(events)


def _openai() -> dict[str, list[dict[str, Any]]]:
    post = {
        "method": "POST",
        "path": "/v1/responses",
        "status": 200,
        "content_type": "text/event-stream",
    }
    reasoning = {
        "id": "rs_synthetic",
        "type": "reasoning",
        "summary": [],
        "encrypted_content": "gAAAAAsynthetic-reasoning",
    }

    def message(text: str) -> dict[str, Any]:
        return {
            "id": "msg_synthetic",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        }

    call = {
        "id": "fc_synthetic",
        "type": "function_call",
        "status": "completed",
        "call_id": "call_synthetic",
        "name": "get_weather",
        "arguments": json.dumps({"city": "Lisbon"}),
    }
    refusal = {
        "id": "msg_synthetic",
        "type": "message",
        "status": "completed",
        "role": "assistant",
        "content": [{"type": "refusal", "refusal": "I can't help with that."}],
    }
    return {
        "text": [{**post, "body": _openai_stream([reasoning, message(TEXT)])}],
        "tool": [
            {**post, "body": _openai_stream([reasoning, call])},
            {**post, "body": _openai_stream([message(TOOL_ANSWER)])},
        ],
        "image": [{**post, "body": _openai_stream([message("Red.")])}],
        "structured": [{**post, "body": _openai_stream([message(json.dumps(SHOT))])}],
        "refusal": [{**post, "body": _openai_stream([refusal])}],
        "max_tokens": [
            {
                **post,
                "body": _openai_stream(
                    [message(LONG)], "incomplete", {"reason": "max_output_tokens"}
                ),
            }
        ],
        "auth": [
            {
                "method": "POST",
                "path": "/v1/responses",
                "status": 401,
                "body": {
                    "error": {
                        "message": "Incorrect API key provided.",
                        "type": "invalid_request_error",
                        "param": None,
                        "code": "invalid_api_key",
                    }
                },
            }
        ],
        "models": [
            {
                "method": "GET",
                "path": "/v1/models",
                "status": 200,
                "body": {
                    "object": "list",
                    "data": [
                        {
                            "id": "gpt-5",
                            "object": "model",
                            "created": 1754000000,
                            "owned_by": "system",
                        },
                        {
                            "id": "gpt-5-mini",
                            "object": "model",
                            "created": 1754000000,
                            "owned_by": "system",
                        },
                        {
                            "id": "text-embedding-3-small",
                            "object": "model",
                            "created": 1705000000,
                            "owned_by": "system",
                        },
                        {
                            "id": "gpt-realtime",
                            "object": "model",
                            "created": 1756000000,
                            "owned_by": "system",
                        },
                    ],
                },
            }
        ],
    }


# -- Gemini API (SSE, alt=sse)


def _gemini_stream(chunks: list[dict[str, Any]]) -> str:
    return "".join(f"data: {json.dumps(chunk)}\r\n\r\n" for chunk in chunks)


def _gemini_chunk(
    parts: list[dict[str, Any]] | None, finish: str | None = None, *, usage: bool = False
) -> dict[str, Any]:
    candidate: dict[str, Any] = {"index": 0}
    if parts is not None:
        candidate["content"] = {"role": "model", "parts": parts}
    if finish:
        candidate["finishReason"] = finish
    chunk: dict[str, Any] = {
        "candidates": [candidate],
        "modelVersion": MODELS["google"],
        "responseId": "synthetic",
    }
    if usage:
        chunk["usageMetadata"] = {
            "promptTokenCount": 42,
            "candidatesTokenCount": 12,
            "thoughtsTokenCount": 4,
            "totalTokenCount": 58,
        }
    return chunk


def _gemini_text(text: str, finish: str = "STOP") -> str:
    pieces = _chunks(text)
    chunks = [_gemini_chunk([{"text": piece}]) for piece in pieces[:-1]]
    chunks.append(_gemini_chunk([{"text": pieces[-1]}], finish, usage=True))
    return _gemini_stream(chunks)


def _gemini() -> dict[str, list[dict[str, Any]]]:
    model = MODELS["google"]
    post = {
        "method": "POST",
        "path": f"/v1beta/models/{model}:streamGenerateContent",
        "status": 200,
        "content_type": "text/event-stream",
    }
    signature = base64.b64encode(b"synthetic-thought-signature").decode()
    call = {
        "functionCall": {"name": "get_weather", "args": {"city": "Lisbon"}},
        "thoughtSignature": signature,
    }
    return {
        "text": [{**post, "body": _gemini_text(TEXT)}],
        "tool": [
            {**post, "body": _gemini_stream([_gemini_chunk([call], "STOP", usage=True)])},
            {**post, "body": _gemini_text(TOOL_ANSWER)},
        ],
        "image": [{**post, "body": _gemini_text("Red.")}],
        "structured": [{**post, "body": _gemini_text(json.dumps(SHOT))}],
        "refusal": [{**post, "body": _gemini_stream([_gemini_chunk(None, "SAFETY", usage=True)])}],
        "max_tokens": [{**post, "body": _gemini_text(LONG, "MAX_TOKENS")}],
        "auth": [
            {
                **post,
                "status": 400,
                "content_type": "application/json",
                "body": {
                    "error": {
                        "code": 400,
                        "message": "API key not valid. Please pass a valid API key.",
                        "status": "INVALID_ARGUMENT",
                    }
                },
            }
        ],
        "models": [
            {
                "method": "GET",
                "path": "/v1beta/models",
                "status": 200,
                "body": {
                    "models": [
                        {
                            "name": f"models/{model}",
                            "displayName": "Gemini 3 Pro",
                            "inputTokenLimit": 1048576,
                            "outputTokenLimit": 65536,
                            "supportedGenerationMethods": ["generateContent", "countTokens"],
                            "thinking": True,
                        },
                        {
                            "name": "models/gemini-3-pro-image",
                            "displayName": "Nano Banana Pro",
                            "supportedGenerationMethods": ["generateContent"],
                        },
                        {
                            "name": "models/text-embedding-004",
                            "displayName": "Text Embedding",
                            "supportedGenerationMethods": ["embedContent"],
                        },
                    ]
                },
            }
        ],
    }


# -- The "Done when" of M2.4: one streamed, tool-calling, image-reading turn.

PALETTE_TOOL = "save_palette"
PALETTE_ARGS = {"colour": "red"}
PALETTE_ANSWER = "Saved red as the palette for this shot."


def _conversations() -> dict[str, list[dict[str, Any]]]:
    anthropic_post = {
        "method": "POST",
        "path": "/v1/messages",
        "status": 200,
        "content_type": "text/event-stream",
    }
    openai_post = {
        "method": "POST",
        "path": "/v1/responses",
        "status": 200,
        "content_type": "text/event-stream",
    }
    gemini_post = {
        "method": "POST",
        "path": f"/v1beta/models/{MODELS['google']}:streamGenerateContent",
        "status": 200,
        "content_type": "text/event-stream",
    }
    tool_use = {
        "type": "tool_use",
        "id": "toolu_02Synthetic",
        "name": PALETTE_TOOL,
        "input": PALETTE_ARGS,
    }
    openai_call = {
        "id": "fc_palette",
        "type": "function_call",
        "status": "completed",
        "call_id": "call_palette",
        "name": PALETTE_TOOL,
        "arguments": json.dumps(PALETTE_ARGS),
    }
    reasoning = {
        "id": "rs_palette",
        "type": "reasoning",
        "summary": [],
        "encrypted_content": "gAAAAApalette",
    }
    answer = {
        "id": "msg_palette",
        "type": "message",
        "status": "completed",
        "role": "assistant",
        "content": [{"type": "output_text", "text": PALETTE_ANSWER, "annotations": []}],
    }
    gemini_call = {
        "functionCall": {"name": PALETTE_TOOL, "args": PALETTE_ARGS},
        "thoughtSignature": base64.b64encode(b"palette-signature").decode(),
    }
    lead_in = {"type": "text", "text": "A red square. Saving it now."}
    return {
        "anthropic": [
            {
                **anthropic_post,
                "body": _anthropic_stream(
                    [{"type": "thinking", "signature": SIGNATURE}, lead_in, tool_use], "tool_use"
                ),
            },
            {
                **anthropic_post,
                "body": _anthropic_stream([{"type": "text", "text": PALETTE_ANSWER}], "end_turn"),
            },
        ],
        "openai": [
            {**openai_post, "body": _openai_stream([reasoning, openai_call])},
            {**openai_post, "body": _openai_stream([answer])},
        ],
        "google": [
            {
                **gemini_post,
                "body": _gemini_stream(
                    [_gemini_chunk([{"text": lead_in["text"]}, gemini_call], "STOP", usage=True)]
                ),
            },
            {**gemini_post, "body": _gemini_text(PALETTE_ANSWER)},
        ],
    }


def _auth_three_times(cases: dict[str, list[dict[str, Any]]], models_path: str) -> None:
    """The bad-key case asks three times: complete, stream, then the model list."""
    refusal = cases["auth"][0]
    cases["auth"] = [refusal, refusal, {**refusal, "method": "GET", "path": models_path}]


def generate() -> list[Path]:
    """Rewrite every synthetic fixture; recordings are left alone."""
    written = []
    everything = {"anthropic": _anthropic(), "openai": _openai(), "google": _gemini()}
    _auth_three_times(everything["anthropic"], "/v1/models")
    _auth_three_times(everything["openai"], "/v1/models")
    _auth_three_times(everything["google"], "/v1beta/models")
    for provider, exchanges in _conversations().items():
        everything[provider]["conversation"] = exchanges
    for provider, cases in everything.items():
        for case, exchanges in cases.items():
            path = FIXTURES / provider / f"{case}.json"
            if path.exists() and not json.loads(path.read_text(encoding="utf-8")).get(
                "synthetic", True
            ):
                continue
            write(provider, case, exchanges, synthetic=True)
            written.append(path)
    return written


if __name__ == "__main__":
    for path in generate():
        print(path.relative_to(FIXTURES))
