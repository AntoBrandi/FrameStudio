"""M2.2: the LLM interface helpers and the scripted fake model."""

from __future__ import annotations

import jsonschema
import pytest

from framestudio.llm.base import (
    ChatRequest,
    ChatResponse,
    ImagePart,
    LLMProvider,
    Message,
    ToolCall,
    ToolResult,
    Usage,
    validate_request,
)
from framestudio.llm.fake import FakeLLM, Turn, sample_for_schema
from framestudio.providers.base import ProviderError

pytestmark = pytest.mark.anyio


def request(*messages: Message, **options: object) -> ChatRequest:
    return ChatRequest(model="fake-large", messages=messages, **options)  # type: ignore[arg-type]


def test_fake_is_an_llm_provider() -> None:
    assert isinstance(FakeLLM(), LLMProvider)


async def test_records_requests_and_follows_the_script() -> None:
    llm = FakeLLM(script=[Turn(text="one")])
    llm.queue(lambda req: Turn(text=f"two: {len(req.messages)} messages"))
    first = await llm.complete(request(Message.user("a")))
    second = await llm.complete(
        request(Message.user("a"), Message.assistant("one"), Message.user("b"))
    )
    third = await llm.complete(request(Message.user("echo me")))
    assert [first.text, second.text] == ["one", "two: 3 messages"]
    assert third.text == "Fake reply to: echo me"
    assert len(llm.requests) == 3
    assert llm.requests[1].messages[-1].text == "b"


async def test_default_echo_sees_images() -> None:
    llm = FakeLLM()
    image = ImagePart(b"\x89PNG", "image/png")
    response = await llm.complete(request(Message.user("what is it?", image)))
    assert response.text.endswith("(with 1 image)")
    assert llm.requests[0].messages[0].parts[0] == image


async def test_scripted_errors_are_raised() -> None:
    llm = FakeLLM(script=[Turn(error=ProviderError("transient"))])
    with pytest.raises(ProviderError) as caught:
        await llm.complete(request(Message.user("hi")))
    assert caught.value.kind == "transient"


async def test_unknown_model_is_not_found() -> None:
    with pytest.raises(ProviderError) as caught:
        await FakeLLM().complete(ChatRequest(model="gpt-zero", messages=(Message.user("hi"),)))
    assert caught.value.kind == "not_found"


async def test_tool_calls_get_ids() -> None:
    llm = FakeLLM(script=[Turn(tool_calls=(ToolCall("", "a", {}), ToolCall("", "b", {})))])
    response = await llm.complete(request(Message.user("go")))
    assert response.stop_reason == "tool_use"
    ids = [c.id for c in response.tool_calls]
    assert all(ids) and len(set(ids)) == 2


async def test_without_a_default_an_empty_script_is_a_test_bug() -> None:
    with pytest.raises(AssertionError):
        await FakeLLM(default=None).complete(request(Message.user("hi")))


def test_sample_for_schema() -> None:
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "minLength": 2},
            "kind": {"enum": ["a", "b"]},
            "count": {"type": "integer", "minimum": 3},
            "ratio": {"type": ["number", "null"]},
            "flags": {"type": "array", "items": {"type": "boolean"}, "minItems": 2},
            "nested": {"type": "object", "properties": {"x": {"const": 1}}, "required": ["x"]},
            "either": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
            "optional": {"type": "string"},
        },
        "required": ["name", "kind", "count", "ratio", "flags", "nested", "either"],
    }
    value = sample_for_schema(schema)
    jsonschema.validate(value, schema)
    assert "optional" not in value


def test_parsed_tolerates_a_code_fence_but_not_prose() -> None:
    def response(text: str) -> ChatResponse:
        return ChatResponse(Message.assistant(text), "end", Usage(), model="m")

    assert response('```json\n{"a": 1}\n```').parsed() == {"a": 1}
    with pytest.raises(ProviderError) as caught:
        response("Sure! Here it is: {a: 1}").parsed()
    assert caught.value.code == "llm_bad_json"


def test_usage_adds_up() -> None:
    assert Usage(1, 2, 3, 4) + Usage(10, 20, 30, 40) == Usage(11, 22, 33, 44)


def test_validate_request_accepts_a_tool_round_trip() -> None:
    call = ToolCall("c1", "get", {})
    validate_request(
        request(
            Message.user("q"),
            Message.assistant("", call),
            Message.tool_results(ToolResult("c1", "r")),
        )
    )


def test_validate_request_lists_every_problem() -> None:
    bad = ChatRequest(model="", messages=(), max_tokens=0, response_schema={"type": "array"})
    with pytest.raises(ProviderError) as caught:
        validate_request(bad)
    message = caught.value.message.lower()
    for fragment in ("no model", "no messages", "max_tokens", "response_schema"):
        assert fragment in message
