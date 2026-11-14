"""Contract suite for language-model providers (plan step M2.2).

Each case asks a provider for one behaviour (streamed text, a tool call and
its follow-up, an image question, structured output, a refusal, a cut-off
reply, a bad key) and checks the answer the same way for every provider.
A harness hands out a provider prepared for each case: the fake LLM gets a
script; the OpenAI, Claude and Gemini adapters (M2.4) replay recordings, or
call the real API with ``LIVE_API=1``.
"""

from __future__ import annotations

import io
import os
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx2
import jsonschema
import pytest
from PIL import Image

from framestudio.llm.anthropic_api import AnthropicLLM
from framestudio.llm.base import (
    ChatRequest,
    ChatResponse,
    Done,
    ImagePart,
    LLMProvider,
    Message,
    TextDelta,
    ToolCall,
    ToolCallEvent,
    ToolResult,
    ToolSpec,
)
from framestudio.llm.fake import FakeLLM, Turn
from framestudio.llm.gemini_api import GeminiLLM
from framestudio.llm.openai_api import OpenAILLM
from framestudio.providers.base import ProviderError

from .llm_wire import MODELS, Recorder, Replay, load

pytestmark = pytest.mark.anyio

WEATHER = ToolSpec(
    name="get_weather",
    description="Current weather for a city.",
    input_schema={
        "type": "object",
        "properties": {"city": {"type": "string", "description": "City name"}},
        "required": ["city"],
        "additionalProperties": False,
    },
)
SHOT_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "camera": {"type": "string", "enum": ["static", "dolly in", "orbit", "crane up"]},
        "duration_seconds": {"type": "integer", "minimum": 2, "maximum": 12},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["subject", "camera", "duration_seconds", "tags"],
    "additionalProperties": False,
}


def red_square_png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), (220, 30, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


@dataclass
class Harness:
    name: str
    model: str
    #: A provider prepared for one named case.
    provider: Callable[[str], LLMProvider]
    #: Cases this provider cannot do (e.g. a text-only model and "image").
    unsupported: set[str] = field(default_factory=set)


def fake_provider(case: str) -> LLMProvider:
    scripts: dict[str, list[Turn]] = {
        "text": [Turn(text="Hello! A lighthouse at dusk sounds lovely.")],
        "tool": [
            Turn(tool_calls=(ToolCall("", "get_weather", {"city": "Lisbon"}),)),
            Turn(text="It is 21°C and clear in Lisbon."),
        ],
        "image": [],  # the default echo mentions the image
        "structured": [],  # the default fills the schema
        "refusal": [Turn(stop_reason="refusal")],
        "max_tokens": [Turn(text="word " * 400)],
        "conversation": [
            Turn(
                text="A red square. Saving it now.",
                tool_calls=(ToolCall("", "save_palette", {"colour": "red"}),),
            ),
            Turn(text="Saved red as the palette for this shot."),
        ],
    }
    if case == "auth":
        return FakeLLM(fail_with=ProviderError("auth", provider="fake"))
    return FakeLLM(script=scripts.get(case, []))


ADAPTERS: dict[str, type] = {"anthropic": AnthropicLLM, "openai": OpenAILLM, "google": GeminiLLM}
#: LIVE_API=1 calls the real APIs with FRAMESTUDIO_LIVE_<PROVIDER>_KEY (paid);
#: FRAMESTUDIO_RECORD=1 also writes what they answer as the new fixtures.
LIVE = os.environ.get("LIVE_API") == "1"
RECORD = os.environ.get("FRAMESTUDIO_RECORD") == "1"


def adapter_provider(provider: str) -> Callable[[str], LLMProvider]:
    def make(case: str) -> LLMProvider:
        if not LIVE:
            replay = Replay(load(provider, case))
            adapter = ADAPTERS[provider]("test-key-0123456789", replay)
            adapter.replay = replay  # for request-shape checks
            return adapter  # type: ignore[no-any-return]
        key = os.environ.get(f"FRAMESTUDIO_LIVE_{provider.upper()}_KEY")
        if not key:
            pytest.skip(f"FRAMESTUDIO_LIVE_{provider.upper()}_KEY is not set")
        if case == "auth":
            key = "invalid-key-for-the-contract-suite"
        transport = Recorder(provider, case) if RECORD else httpx2.AsyncHTTPTransport()
        return ADAPTERS[provider](key, transport)  # type: ignore[no-any-return]

    return make


def live_model(provider: str) -> str:
    return os.environ.get(f"FRAMESTUDIO_LIVE_{provider.upper()}_MODEL", MODELS[provider])


HARNESSES = [
    Harness("fake", "fake-large", fake_provider),
    *(
        Harness(
            provider,
            live_model(provider),
            adapter_provider(provider),
            # A refusal cannot be asked for on demand from a live model.
            unsupported={"refusal"} if LIVE else set(),
        )
        for provider in ADAPTERS
    ),
]


@pytest.fixture(params=HARNESSES, ids=[h.name for h in HARNESSES])
def harness(request: pytest.FixtureRequest) -> Harness:
    return request.param  # type: ignore[no-any-return]


def ask(harness: Harness, *messages: Message, **options: object) -> ChatRequest:
    return ChatRequest(model=harness.model, messages=messages, **options)  # type: ignore[arg-type]


def needs(harness: Harness, case: str) -> None:
    if case in harness.unsupported:
        pytest.skip(f"{harness.name} does not support {case}")


async def test_streams_text_then_one_done(harness: Harness) -> None:
    provider = harness.provider("text")
    request = ask(
        harness,
        Message.user("Say hello and mention a lighthouse at dusk."),
        system="You are terse.",
        max_tokens=200,
    )
    events = [event async for event in provider.stream(request)]
    deltas = [e.text for e in events if isinstance(e, TextDelta)]
    dones = [e for e in events if isinstance(e, Done)]
    assert len(dones) == 1 and events[-1] is dones[0]
    response = dones[0].response
    assert deltas and all(deltas)
    assert response.text == "".join(deltas)
    assert response.stop_reason == "end"
    assert response.usage.input_tokens > 0 and response.usage.output_tokens > 0
    assert response.provider == provider.id
    assert response.message.role == "assistant"


async def test_complete_returns_the_same_shape(harness: Harness) -> None:
    provider = harness.provider("text")
    response = await provider.complete(ask(harness, Message.user("Say hello."), max_tokens=200))
    assert isinstance(response, ChatResponse)
    assert response.text and response.stop_reason == "end"
    assert response.tool_calls == []


async def test_tool_call_round_trip(harness: Harness) -> None:
    needs(harness, "tool")
    provider = harness.provider("tool")
    question = Message.user("What's the weather in Lisbon right now? Use the tool.")
    first = ask(harness, question, tools=(WEATHER,), max_tokens=300)
    events = [event async for event in provider.stream(first)]
    calls = [e.call for e in events if isinstance(e, ToolCallEvent)]
    response = events[-1].response  # type: ignore[union-attr]
    assert response.stop_reason == "tool_use"
    assert calls and calls == response.tool_calls
    assert len({c.id for c in calls}) == len(calls) and all(c.id for c in calls)
    call = calls[0]
    assert call.name == "get_weather"
    jsonschema.validate(call.arguments, WEATHER.input_schema)

    follow_up = ask(
        harness,
        question,
        response.message,
        Message.tool_results(ToolResult(call.id, '{"temp_c": 21, "sky": "clear"}')),
        tools=(WEATHER,),
        max_tokens=300,
    )
    answer = await provider.complete(follow_up)
    assert answer.stop_reason == "end"
    assert answer.text


async def test_image_input(harness: Harness) -> None:
    needs(harness, "image")
    provider = harness.provider("image")
    image = ImagePart(red_square_png(), "image/png")
    response = await provider.complete(
        ask(harness, Message.user("What colour fills this image? One word.", image), max_tokens=50)
    )
    assert response.stop_reason == "end"
    assert response.text


async def test_structured_output_matches_the_schema(harness: Harness) -> None:
    needs(harness, "structured")
    provider = harness.provider("structured")
    response = await provider.complete(
        ask(
            harness,
            Message.user("Plan one shot: a lighthouse at dusk, slow push in."),
            response_schema=SHOT_SCHEMA,
            max_tokens=400,
        )
    )
    assert response.stop_reason == "end"
    jsonschema.validate(response.parsed(), SHOT_SCHEMA)


async def test_refusal_is_a_response_not_an_error(harness: Harness) -> None:
    needs(harness, "refusal")
    provider = harness.provider("refusal")
    response = await provider.complete(
        ask(harness, Message.user("(a request the model declines)"), response_schema=SHOT_SCHEMA)
    )
    assert response.refused
    with pytest.raises(ProviderError) as caught:
        response.parsed()
    assert caught.value.kind == "content_policy"


async def test_max_tokens_cuts_the_reply(harness: Harness) -> None:
    provider = harness.provider("max_tokens")
    response = await provider.complete(
        ask(harness, Message.user("Write a 400-word story about a lighthouse."), max_tokens=16)
    )
    assert response.stop_reason == "max_tokens"
    with pytest.raises(ProviderError) as caught:
        response.parsed()
    assert caught.value.code == "llm_truncated"


async def test_bad_key_is_an_auth_error(harness: Harness) -> None:
    provider = harness.provider("auth")
    request = ask(harness, Message.user("Hello"))
    with pytest.raises(ProviderError) as caught:
        await provider.complete(request)
    assert caught.value.kind == "auth"
    with pytest.raises(ProviderError) as caught:
        _ = [event async for event in provider.stream(request)]
    assert caught.value.kind == "auth"
    with pytest.raises(ProviderError) as caught:
        await provider.list_models()
    assert caught.value.kind == "auth"


async def test_lists_models(harness: Harness) -> None:
    provider = harness.provider("models")
    models = await provider.list_models()
    assert models
    assert len({m.id for m in models}) == len(models)
    assert all(m.provider == provider.id and m.name for m in models)
    assert harness.model in {m.id for m in models}


async def test_malformed_requests_are_refused_before_sending(harness: Harness) -> None:
    provider = harness.provider("text")
    bad = [
        ask(harness, Message.tool_results(ToolResult("nope", "{}"))),
        ask(harness, Message.assistant("I speak first")),
        ask(harness, Message.user("hi", ImagePart(b"%PDF", "application/pdf"))),
        ask(harness, Message.user("hi"), tools=(ToolSpec("bad name!", "", {"type": "object"}),)),
    ]
    for request in bad:
        with pytest.raises(ProviderError) as caught:
            await provider.complete(request)
        assert caught.value.kind == "invalid_params"


PALETTE = ToolSpec(
    name="save_palette",
    description="Save the dominant colour of the shot as its palette.",
    input_schema={
        "type": "object",
        "properties": {"colour": {"type": "string", "description": "A plain colour name"}},
        "required": ["colour"],
        "additionalProperties": False,
    },
)


async def test_streamed_tool_calling_image_conversation(harness: Harness) -> None:
    """M2.4 "Done when": one streamed, tool-calling, image-reading conversation."""
    needs(harness, "image")
    needs(harness, "tool")
    provider = harness.provider("conversation")
    question = Message.user(
        "Here is a still from my shoot. Save its dominant colour with the save_palette tool, "
        "then tell me in one sentence what you saved.",
        ImagePart(red_square_png(), "image/png"),
    )
    system = "You help plan shots. Use tools when asked to save something."
    first = ask(harness, question, system=system, tools=(PALETTE,), max_tokens=2000)
    events = [event async for event in provider.stream(first)]
    reply = events[-1].response  # type: ignore[union-attr]
    assert reply.stop_reason == "tool_use"
    [call] = [e.call for e in events if isinstance(e, ToolCallEvent)]
    assert call.name == "save_palette"
    jsonschema.validate(call.arguments, PALETTE.input_schema)

    follow_up = ask(
        harness,
        question,
        reply.message,
        Message.tool_results(ToolResult(call.id, f"Saved palette: {call.arguments['colour']}")),
        system=system,
        tools=(PALETTE,),
        max_tokens=2000,
    )
    deltas: list[str] = []
    final = None
    async for event in provider.stream(follow_up):
        if isinstance(event, TextDelta):
            deltas.append(event.text)
        elif isinstance(event, Done):
            final = event.response
    assert final is not None and final.stop_reason == "end"
    assert deltas and "".join(deltas) == final.text
    assert call.arguments["colour"].lower() in final.text.lower()
