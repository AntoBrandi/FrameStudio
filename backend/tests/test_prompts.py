"""M4.3: prompts as token lists, with one parser for pasted text, prompts sent
as text and prompts written by the Director."""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st

from framestudio.prompts import (
    MoveToken,
    SkillToken,
    Token,
    Vocabulary,
    element,
    merge,
    normalize,
    parse,
    render,
    text,
    warnings,
)

from .conftest import wait_until

FIXTURES = yaml.safe_load((Path(__file__).parent / "fixtures" / "prompts.yaml").read_text())
ELEMENTS: dict[str, str] = FIXTURES["vocabulary"]["elements"]
VOCABULARY = Vocabulary(
    elements=dict(ELEMENTS),
    handles={v: k for k, v in ELEMENTS.items()},
    moves=set(FIXTURES["vocabulary"]["moves"]),
    skills=set(FIXTURES["vocabulary"]["skills"]),
)


def expected(items: list[Any]) -> list[Token]:
    out: list[Token] = []
    for item in items:
        if isinstance(item, str):
            out.append(text(item))
        elif "@" in item:
            out.append(element(item["@"], ELEMENTS[item["@"]]))
        elif "@?" in item:
            out.append(element(item["@?"]))
        elif "#" in item:
            out.append(MoveToken(type="move", name=item["#"]))
        else:
            out.append(SkillToken(type="skill", name=item["/"]))
    return out


# ---------------------------------------------------------------- fixture prompts


@pytest.mark.parametrize(
    "case", FIXTURES["cases"], ids=lambda c: f"{c['source']}:{c.get('note') or c['text'][:30]}"
)
def test_fixture_prompts(case: dict[str, Any]) -> None:
    tokens = parse(case["text"], VOCABULARY)
    assert tokens == expected(case["tokens"])
    assert render(tokens) == case.get("rendered", case["text"])
    assert parse(render(tokens), VOCABULARY) == tokens


def test_unknown_handles_warn_once() -> None:
    tokens = parse("@ghost meets @maya and @ghost again", VOCABULARY)
    assert warnings(tokens) == ["@ghost is not an element here; it is sent as plain text."]


# ---------------------------------------------------------------- properties

# Text made mostly of the characters that matter: sigils, handle characters,
# boundaries and a few others (lowercase, so rendering gives it back exactly).
ALPHABET = "@#/-_ .,'\n" + "abmyaio09" + "é"
words = st.sampled_from([*ELEMENTS, *VOCABULARY.moves, *VOCABULARY.skills, "ghost", "x"])
prompt_text = st.lists(
    st.one_of(st.text(ALPHABET, max_size=6), words.map(lambda w: "@" + w), words.map("#".__add__)),
    max_size=12,
).map("".join)


@settings(max_examples=400)
@given(prompt_text)
def test_text_to_tokens_to_text(value: str) -> None:
    tokens = parse(value, VOCABULARY)
    assert render(tokens) == value
    assert all(t.text for t in tokens if t.type == "text")
    assert all(a.type != "text" or b.type != "text" for a, b in pairwise(tokens))


@settings(max_examples=400)
@given(prompt_text)
def test_tokens_to_text_to_tokens(value: str) -> None:
    tokens = parse(value, VOCABULARY)
    assert parse(render(tokens), VOCABULARY) == tokens


# Token lists built directly: text between tokens is kept off the token
# characters at its edges, as it is wherever a token can end or start.
# Inert text: no sigil followed by a name (lone sigils, e-mails, fractions).
inner = st.lists(
    st.one_of(st.text("abc ,.'é\n-", max_size=4), st.sampled_from(["@ ", "# ", "a@b", "1/2", "#"])),
    max_size=3,
).map("".join)
# After a token: a boundary character. Before one: whitespace (a skill needs it).
after = st.sampled_from([" ", ", ", ". ", "\n", ") ", "'s "])
before = st.sampled_from([" ", "\n", ", ", "( ", ": "])
spacer = st.builds(lambda a, b, c: a + b + c, after, inner, before)
tokens_strategy = st.lists(
    st.one_of(
        st.sampled_from(sorted(ELEMENTS)).map(lambda h: element(h, ELEMENTS[h])),
        st.sampled_from(["ghost", "nobody-here"]).map(element),
        st.sampled_from(sorted(VOCABULARY.moves)).map(lambda m: MoveToken(type="move", name=m)),
        st.sampled_from(sorted(VOCABULARY.skills)).map(lambda s: SkillToken(type="skill", name=s)),
    ),
    max_size=8,
)


@settings(max_examples=300)
@given(tokens_strategy, st.lists(spacer, min_size=9, max_size=9), st.booleans())
def test_built_token_lists_round_trip(
    specials: list[Token], spacers: list[str], lead: bool
) -> None:
    tokens: list[Token] = [text(spacers[0])] if lead else []
    for index, token in enumerate(specials):
        tokens += [token, text(spacers[index + 1])]
    tokens = merge(tokens)
    assert parse(render(tokens), VOCABULARY) == tokens


def test_normalize_follows_renames_and_scope() -> None:
    # The editor sends a chip by id, and text with a handle typed by hand.
    edited: list[Token] = [element("old-name", "e-maya"), text(" with @lighthouse")]
    tokens = normalize(edited, VOCABULARY)
    assert tokens == [
        element("maya", "e-maya"),
        text(" with "),
        element("lighthouse", "e-lighthouse"),
    ]
    # An element no longer in scope is matched again by handle, or left unknown.
    gone = normalize([element("maya", "e-deleted"), element("nobody", "e-x")], VOCABULARY)
    assert gone == [element("maya", "e-maya"), element("nobody")]
    assert render([element("old", "e-maya")], VOCABULARY) == "@maya"


# ---------------------------------------------------------------- through the API


def make(client: TestClient, **body: Any) -> dict[str, Any]:
    response = client.post("/api/elements", json=body)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def test_parse_endpoint_uses_the_project_and_global_elements(authed: TestClient) -> None:
    film = authed.post("/api/projects", json={"name": "Harbour"}).json()["id"]
    global_maya = make(authed, name="Maya")
    local_maya = make(authed, name="Maya", project_id=film)
    pier = make(authed, name="Pier", project_id=film)

    def parsed(**body: Any) -> dict[str, Any]:
        response = authed.post("/api/prompts/parse", json=body)
        assert response.status_code == 200, response.text
        return response.json()  # type: ignore[no-any-return]

    in_project = parsed(text="@maya on @pier, @ghost", project_id=film)
    assert [t.get("element_id") for t in in_project["tokens"] if t["type"] == "element"] == [
        local_maya["id"],
        pier["id"],
        None,
    ]
    assert in_project["warnings"] == ["@ghost is not an element here; it is sent as plain text."]
    in_library = parsed(text="@maya on @pier")
    ids = [t.get("element_id") for t in in_library["tokens"] if t["type"] == "element"]
    assert ids == [global_maya["id"], None]
    # A token list from the editor comes back in canonical form.
    canonical = parsed(
        tokens=[
            {"type": "element", "handle": "x", "element_id": pier["id"]},
            {"type": "text", "text": " and "},
            {"type": "text", "text": "@maya"},
        ],
        project_id=film,
    )
    assert canonical["text"] == "@pier and @maya"
    assert canonical["warnings"] == []


def settled(client: TestClient, generation_id: str) -> dict[str, Any]:
    def check() -> dict[str, Any] | None:
        body = client.get(f"/api/generations/{generation_id}").json()
        return body if body["status"] in {"succeeded", "failed"} else None

    return wait_until(check, timeout=20)  # type: ignore[no-any-return]


def test_generations_store_tokens_and_used_in(authed: TestClient) -> None:
    authed.patch("/api/settings", json={"mock_latency_seconds": 0})
    maya = make(authed, name="Maya")
    make(authed, name="Pier", category="location")
    body = {
        "model": "mock",
        "prompt": "ignored when tokens are given",
        "prompt_tokens": [
            {"type": "element", "handle": "maya", "element_id": maya["id"]},
            {"type": "text", "text": " on @pier at dusk, @ghost watching"},
        ],
    }
    response = authed.post("/api/generations", json=body)
    assert response.status_code == 201, response.text
    [queued] = response.json()
    assert queued["prompt"] == "@maya on @pier at dusk, @ghost watching"
    kinds = [(t["type"], t.get("handle")) for t in queued["prompt_tokens"]]
    assert kinds == [
        ("element", "maya"),
        ("text", None),
        ("element", "pier"),
        ("text", None),
        ("element", "ghost"),
        ("text", None),
    ]
    assert settled(authed, queued["id"])["status"] == "succeeded"
    # Sent as text, a prompt is parsed the same way.
    as_text = authed.post("/api/generations", json={"model": "mock", "prompt": "@maya alone"})
    [second] = as_text.json()
    assert second["prompt_tokens"][0] == {
        "type": "element",
        "handle": "maya",
        "element_id": maya["id"],
    }
    settled(authed, second["id"])
    used = {e["handle"]: e["used_in"] for e in authed.get("/api/elements").json()}
    assert used == {"maya": 2, "pier": 1}

    # Renaming the element: the stored token keeps pointing at it.
    authed.patch(f"/api/elements/{maya['id']}", json={"handle": "maya-o"})
    stored = authed.get(f"/api/generations/{second['id']}").json()
    assert stored["prompt_tokens"][0]["element_id"] == maya["id"]
    check = authed.post("/api/prompts/parse", json={"tokens": stored["prompt_tokens"]}).json()
    assert check["text"] == "@maya-o alone"


def test_prompt_length_counts_the_rendered_text(authed: TestClient) -> None:
    long = [{"type": "text", "text": "x" * 3000}] * 2
    response = authed.post("/api/generations", json={"model": "mock", "prompt_tokens": long})
    assert response.status_code == 422
    assert "4000" in response.text
