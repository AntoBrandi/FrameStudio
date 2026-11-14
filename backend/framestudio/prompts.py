"""Prompt tokens (plan step M4.3).

A prompt is a list of tokens: plain text, ``@handle`` (an element),
``#move`` (a camera move, M5) and ``/skill`` (a Director skill, M6). The token
list is what is stored; the plain text is a rendering of it. This module holds
the one parser that turns text into tokens, used for pasted text, prompts sent
as text through the API, and prompts written by the Director.

Rules:

* ``@`` followed by a handle starts an element token when it is not preceded by
  a letter, digit or ``_`` (so ``ana@example.com`` stays text). Handles are
  matched without regard to case and stored in lowercase. A handle that names
  no element in scope stays an element token with no ``element_id``, so the
  composer can warn about it.
* ``#name`` and ``/name`` become tokens only for known moves and skills;
  ``#ff0000`` or ``black/white`` stay text. A skill must start the prompt or
  follow whitespace.
* Adjacent text tokens are merged and empty ones dropped.

``render(parse(text))`` gives back the text (handles in lowercase), and
``parse(render(tokens))`` gives back any token list the parser produced.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Annotated, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from framestudio.db.models import Element

NAME = r"[A-Za-z0-9][A-Za-z0-9_-]{0,47}"
_TOKEN = re.compile(rf"(?<![A-Za-z0-9_])(?P<sigil>[@#/])(?P<name>{NAME})(?![A-Za-z0-9_-])")


class TextToken(BaseModel):
    type: Literal["text"]
    text: str


class ElementToken(BaseModel):
    type: Literal["element"]
    handle: str
    #: The element it names, or None when no element in scope has the handle.
    element_id: str | None = None


class MoveToken(BaseModel):
    type: Literal["move"]
    name: str


class SkillToken(BaseModel):
    type: Literal["skill"]
    name: str


Token = Annotated[TextToken | ElementToken | MoveToken | SkillToken, Field(discriminator="type")]


def text(value: str) -> TextToken:
    return TextToken(type="text", text=value)


def element(handle: str, element_id: str | None = None) -> ElementToken:
    return ElementToken(type="element", handle=handle, element_id=element_id)


@dataclass
class Vocabulary:
    """What tokens can name in one place: a project's elements and the global
    ones (the project's win on a shared handle), moves and skills."""

    elements: dict[str, str] = field(default_factory=dict)
    #: Element id → its current handle, for every element in scope.
    handles: dict[str, str] = field(default_factory=dict)
    moves: set[str] = field(default_factory=set)
    skills: set[str] = field(default_factory=set)


def load_vocabulary(session: Session, project_id: str | None) -> Vocabulary:
    scopes = [project_id, "global"] if project_id else ["global"]
    rows = session.execute(
        select(Element.id, Element.handle, Element.scope_key).where(Element.scope_key.in_(scopes))
    ).all()
    vocabulary = Vocabulary()
    # Global first, so the project's elements overwrite a shared handle.
    for element_id, handle, _scope in sorted(rows, key=lambda r: r[2] != "global"):
        vocabulary.elements[handle] = element_id
        vocabulary.handles[element_id] = handle
    return vocabulary


def merge(tokens: list[Token]) -> list[Token]:
    """Join adjacent text tokens and drop empty ones."""
    out: list[Token] = []
    for token in tokens:
        if isinstance(token, TextToken):
            if not token.text:
                continue
            if out and isinstance(out[-1], TextToken):
                out[-1] = text(out[-1].text + token.text)
                continue
        out.append(token)
    return out


def parse(value: str, vocabulary: Vocabulary | None = None) -> list[Token]:
    vocabulary = vocabulary or Vocabulary()
    tokens: list[Token] = []
    cursor = 0
    for match in _TOKEN.finditer(value):
        sigil, name = match["sigil"], match["name"].lower()
        token: Token | None = None
        if sigil == "@":
            token = element(name, vocabulary.elements.get(name))
        elif sigil == "#" and name in vocabulary.moves:
            token = MoveToken(type="move", name=name)
        elif sigil == "/" and name in vocabulary.skills:
            start = match.start()
            if start == 0 or value[start - 1].isspace():
                token = SkillToken(type="skill", name=name)
        if token is None:
            continue
        tokens.append(text(value[cursor : match.start()]))
        tokens.append(token)
        cursor = match.end()
    tokens.append(text(value[cursor:]))
    return merge(tokens)


def render(tokens: list[Token], vocabulary: Vocabulary | None = None) -> str:
    """The plain text of ``tokens``; elements under their current handle when
    ``vocabulary`` knows them."""
    handles = vocabulary.handles if vocabulary else {}
    parts = []
    for token in tokens:
        if isinstance(token, TextToken):
            parts.append(token.text)
        elif isinstance(token, ElementToken):
            current = handles.get(token.element_id or "")
            parts.append(f"@{current or token.handle}")
        elif isinstance(token, MoveToken):
            parts.append(f"#{token.name}")
        else:
            parts.append(f"/{token.name}")
    return "".join(parts)


def normalize(tokens: list[Token], vocabulary: Vocabulary) -> list[Token]:
    """The canonical form of a token list from the editor or the API: text
    parsed for tokens typed by hand, elements matched to this scope (by id,
    else by handle) and shown under their current handle."""
    out: list[Token] = []
    for token in tokens:
        if isinstance(token, TextToken):
            out.extend(parse(token.text, vocabulary))
        elif isinstance(token, ElementToken):
            current = vocabulary.handles.get(token.element_id or "")
            if current:
                out.append(element(current, token.element_id))
            else:
                handle = token.handle.lower()
                out.append(element(handle, vocabulary.elements.get(handle)))
        else:
            out.append(token)
    return merge(out)


def warnings(tokens: list[Token]) -> list[str]:
    unknown = dict.fromkeys(
        t.handle for t in tokens if isinstance(t, ElementToken) and t.element_id is None
    )
    return [f"@{handle} is not an element here; it is sent as plain text." for handle in unknown]


def element_ids(tokens: list[Token]) -> list[str]:
    """The elements a prompt uses, in order of first use."""
    return list(
        dict.fromkeys(t.element_id for t in tokens if isinstance(t, ElementToken) and t.element_id)
    )


class PromptParse(BaseModel):
    """Text to parse, or a token list to put in canonical form."""

    text: str | None = Field(default=None, max_length=20_000)
    tokens: list[Token] | None = Field(default=None, max_length=4_000)
    #: Whose elements the handles name (with the global ones).
    project_id: str | None = None


class PromptParsed(BaseModel):
    tokens: list[Token]
    text: str
    #: Unknown handles, in words.
    warnings: list[str]


def parse_request(session: Session, body: PromptParse) -> PromptParsed:
    vocabulary = load_vocabulary(session, body.project_id)
    if body.tokens is not None:
        tokens = normalize(body.tokens, vocabulary)
    else:
        tokens = parse(body.text or "", vocabulary)
    return PromptParsed(tokens=tokens, text=render(tokens), warnings=warnings(tokens))
