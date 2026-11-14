"""M2.4: what each adapter sends. The contract suite checks behaviour; these
check the request shapes each provider documents (strict tools, effort,
structured output, images, caching, and replaying the provider's own
reasoning data unchanged)."""

from __future__ import annotations

import base64
import io
import logging
from typing import Any

import pytest
from PIL import Image

from framestudio.llm.anthropic_api import AnthropicLLM
from framestudio.llm.base import ChatRequest, ImagePart, Message, ToolResult, ToolSpec, Usage
from framestudio.llm.gemini_api import GeminiLLM
from framestudio.llm.metered import MeteredLLM
from framestudio.llm.openai_api import OpenAILLM
from framestudio.llm.pricing import FALLBACK, rate_for

from .contracts.llm_wire import MODELS, Replay, load

pytestmark = pytest.mark.anyio

KEY = "sk-secret-0123456789abcdefKEY"
TOOL = ToolSpec(
    "get_weather",
    "Current weather for a city.",
    {
        "type": "object",
        "properties": {"city": {"type": "string"}, "units": {"type": "string", "enum": ["c", "f"]}},
        "required": ["city"],
    },
)
SCHEMA = {
    "type": "object",
    "properties": {"subject": {"type": "string"}, "note": {"type": "string"}},
    "required": ["subject"],
}


def either(node: dict[str, Any], camel: str, snake: str) -> Any:
    """Google's JSON accepts both spellings, and the SDK sends some fields in each."""
    return node[camel] if camel in node else node[snake]


def png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 0, 0)).save(buffer, format="PNG")
    return buffer.getvalue()


def adapter(provider: str, case: str) -> tuple[Any, Replay]:
    replay = Replay(load(provider, case))
    cls = {"anthropic": AnthropicLLM, "openai": OpenAILLM, "google": GeminiLLM}[provider]
    return cls(KEY, replay), replay


def ask(provider: str, *messages: Message, **options: Any) -> ChatRequest:
    return ChatRequest(model=MODELS[provider], messages=messages, **options)


async def tool_round_trip(provider: str) -> tuple[Replay, Any]:
    llm, replay = adapter(provider, "tool")
    question = Message.user("Weather in Lisbon?")
    first = await llm.complete(ask(provider, question, tools=(TOOL,), system="Be brief."))
    call = first.tool_calls[0]
    await llm.complete(
        ask(
            provider,
            question,
            first.message,
            Message.tool_results(ToolResult(call.id, '{"temp_c": 21}')),
            tools=(TOOL,),
            system="Be brief.",
        )
    )
    return replay, first


# ---------------------------------------------------------------- Claude


async def test_claude_request_shape() -> None:
    llm, replay = adapter("anthropic", "text")
    await llm.complete(
        ask("anthropic", Message.user("Hi"), system="Be brief.", effort="high", max_tokens=900)
    )
    body = replay.bodies[0]
    assert body["model"] == "claude-opus-5-5" and body["max_tokens"] == 900
    assert body["system"] == [
        {"type": "text", "text": "Be brief.", "cache_control": {"type": "ephemeral"}}
    ]
    assert body["output_config"] == {"effort": "high"}
    assert "thinking" not in body  # always on for Opus 5.5; explicit settings 400
    assert "temperature" not in body and "top_p" not in body  # removed in API 2026-10-21
    assert body["stream"] is True
    assert KEY not in str(replay.requests[0].url)


async def test_claude_leaves_effort_to_the_model_unless_asked() -> None:
    llm, replay = adapter("anthropic", "text")
    await llm.complete(ask("anthropic", Message.user("Hi")))
    assert "output_config" not in replay.bodies[0]
    assert "system" not in replay.bodies[0]


async def test_claude_tools_are_strict_and_thinking_is_replayed() -> None:
    replay, first = await tool_round_trip("anthropic")
    sent = replay.bodies[0]
    assert sent["tools"][0]["strict"] is True
    assert sent["tools"][0]["input_schema"]["additionalProperties"] is False
    assert sent["tools"][0]["input_schema"]["required"] == ["city"]  # optional stays optional
    assert "tool_choice" not in sent  # auto: forcing a tool is refused on current models
    follow_up = replay.bodies[1]["messages"]
    assistant = follow_up[1]
    assert assistant["role"] == "assistant"
    assert assistant["content"] == first.message.native["anthropic"]["content"]
    assert assistant["content"][0]["type"] == "thinking"
    assert assistant["content"][0]["signature"].startswith("EqQB")
    assert follow_up[2]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "toolu_01Synthetic",
        "content": '{"temp_c": 21}',
        "is_error": False,
    }


async def test_claude_structured_output_and_images() -> None:
    llm, replay = adapter("anthropic", "structured")
    await llm.complete(ask("anthropic", Message.user("Plan it"), response_schema=SCHEMA))
    fmt = replay.bodies[0]["output_config"]["format"]
    assert fmt["type"] == "json_schema" and fmt["schema"]["additionalProperties"] is False
    llm, replay = adapter("anthropic", "image")
    await llm.complete(ask("anthropic", Message.user("Colour?", ImagePart(png(), "image/png"))))
    image = replay.bodies[0]["messages"][0]["content"][0]
    assert image["type"] == "image" and image["source"]["media_type"] == "image/png"
    assert base64.b64decode(image["source"]["data"]) == png()


async def test_claude_reports_models_with_capabilities() -> None:
    llm, _ = adapter("anthropic", "models")
    models = await llm.list_models()
    assert models[0].id == "claude-opus-5-5" and models[0].name == "Claude Opus 5.5"
    assert models[0].context_tokens == 1_000_000 and models[0].effort and models[0].vision


# ---------------------------------------------------------------- OpenAI


async def test_openai_request_shape() -> None:
    llm, replay = adapter("openai", "structured")
    await llm.complete(
        ask(
            "openai",
            Message.user("Plan it"),
            system="Be brief.",
            response_schema=SCHEMA,
            effort="xhigh",
        )
    )
    body = replay.bodies[0]
    assert body["store"] is False  # nothing kept on OpenAI's side
    assert body["instructions"] == "Be brief."
    assert body["include"] == ["reasoning.encrypted_content"]
    assert body["reasoning"] == {"effort": "xhigh"}
    fmt = body["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True
    assert fmt["schema"]["required"] == ["subject", "note"]  # strict mode lists every property
    assert fmt["schema"]["properties"]["note"]["type"] == ["string", "null"]


async def test_openai_tools_strict_and_reasoning_replayed() -> None:
    replay, first = await tool_round_trip("openai")
    tool = replay.bodies[0]["tools"][0]
    assert tool["type"] == "function" and tool["strict"] is True
    assert tool["parameters"]["required"] == ["city", "units"]
    follow_up = replay.bodies[1]["input"]
    native = first.message.native["openai"]["output"]
    assert follow_up[1 : 1 + len(native)] == native
    assert follow_up[1]["type"] == "reasoning" and follow_up[1]["encrypted_content"]
    assert follow_up[-1] == {
        "type": "function_call_output",
        "call_id": "call_synthetic",
        "output": '{"temp_c": 21}',
    }


async def test_openai_images_go_as_data_urls() -> None:
    llm, replay = adapter("openai", "image")
    await llm.complete(ask("openai", Message.user("Colour?", ImagePart(png(), "image/png"))))
    content = replay.bodies[0]["input"][0]["content"]
    assert content[0]["type"] == "input_image"
    assert content[0]["image_url"].startswith("data:image/png;base64,")
    assert content[1] == {"type": "input_text", "text": "Colour?"}


async def test_openai_lists_only_chat_models() -> None:
    llm, _ = adapter("openai", "models")
    ids = [m.id for m in await llm.list_models()]
    assert ids == ["gpt-5", "gpt-5-mini"]


# ---------------------------------------------------------------- Gemini


async def test_gemini_request_shape() -> None:
    llm, replay = adapter("google", "structured")
    await llm.complete(
        ask(
            "google",
            Message.user("Plan it"),
            system="Be brief.",
            response_schema=SCHEMA,
            effort="low",
        )
    )
    body = replay.bodies[0]
    request = replay.requests[0]
    assert request.headers["x-goog-api-key"] == KEY
    assert KEY not in str(request.url)
    assert body["systemInstruction"]["parts"][0]["text"] == "Be brief."
    config = body["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseJsonSchema"]["additionalProperties"] is False
    assert either(config["thinkingConfig"], "thinkingLevel", "thinking_level") == "LOW"


async def test_gemini_thought_signatures_are_replayed() -> None:
    replay, first = await tool_round_trip("google")
    declaration = replay.bodies[0]["tools"][0]["functionDeclarations"][0]
    assert declaration["name"] == "get_weather"
    schema = either(declaration, "parametersJsonSchema", "parameters_json_schema")
    assert schema["additionalProperties"] is False
    contents = replay.bodies[1]["contents"]
    model_turn = contents[1]
    assert model_turn["role"] == "model"
    assert model_turn["parts"][0]["thoughtSignature"]
    assert model_turn["parts"][0]["functionCall"]["name"] == "get_weather"
    response = contents[2]["parts"][0]["functionResponse"]
    assert response["name"] == "get_weather"  # Gemini wants the tool's name back
    assert response["id"] == first.tool_calls[0].id
    assert response["response"] == {"result": '{"temp_c": 21}'}


async def test_gemini_lists_only_chat_models() -> None:
    llm, _ = adapter("google", "models")
    models = await llm.list_models()
    assert [m.id for m in models] == ["gemini-3-pro"]
    assert models[0].context_tokens == 1048576 and models[0].effort


# ---------------------------------------------------------------- all of them


@pytest.mark.parametrize("provider", ["anthropic", "openai", "google"])
async def test_keys_stay_out_of_logs(provider: str, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        await tool_round_trip(provider)
        llm, _ = adapter(provider, "auth")
        with pytest.raises(Exception):  # noqa: B017 (the auth error itself is tested elsewhere)
            await llm.complete(ask(provider, Message.user("Hi")))
    assert KEY not in caplog.text


@pytest.mark.parametrize("provider", ["anthropic", "openai", "google"])
async def test_metered_adapter_records_cost(provider: str) -> None:
    class Ledger:
        def __init__(self) -> None:
            self.rows: list[dict[str, Any]] = []

        def check_cap(self, provider: str, estimate: float | None) -> None:
            return None

        def record(self, provider: str, **row: Any) -> str:
            self.rows.append({"provider": provider, **row})
            return "u1"

    from framestudio.llm.pricing import price

    llm, _ = adapter(provider, "text")
    ledger = Ledger()
    metered = MeteredLLM(llm, ledger, source="enhance", price=price(provider))  # type: ignore[arg-type]
    await metered.complete(ask(provider, Message.user("Hi")))
    [row] = ledger.rows
    assert row["provider"] == provider and row["source"] == "enhance"
    assert row["input_tokens"] > 0 and row["output_tokens"] > 0 and row["cost_usd"] > 0


def test_prices_pick_the_longest_prefix_and_fall_back_high() -> None:
    assert rate_for("anthropic", "claude-opus-5-5") is not None
    assert rate_for("anthropic", "claude-opus-5-5").input == 4.0  # type: ignore[union-attr]
    assert rate_for("anthropic", "claude-opus-5").input == 5.0  # type: ignore[union-attr]
    assert rate_for("openai", "gpt-5-mini-2026").input == 0.25  # type: ignore[union-attr]
    assert rate_for("openai", "gpt-9-ultra") == FALLBACK["openai"]
    assert rate_for("nobody", "x") is None
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000, cache_read_tokens=1_000_000)
    assert rate_for("anthropic", "claude-opus-5-5").cost(usage) == pytest.approx(24.2)  # type: ignore[union-attr]
