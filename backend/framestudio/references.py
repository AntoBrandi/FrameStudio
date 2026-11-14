"""Reference resolver (plan step M4.4): what a model is given for a prompt
that names elements.

Each model gets elements in the form it can use:

* **multi_reference** (Nano Banana, Seedance, fal models taking a list of
  images): the elements' pictures go as reference images after the ones
  attached by hand, primary first, then more angles while there is room (up
  to three per element). The prompt names each element with its pictures:
  "Maya Ortiz (image 2)".
* **single_image** (image-to-image models with one input image): the first
  element with a picture gives it, unless a picture was attached by hand.
* **description** (models that take no pictures, or a clip given start and
  end frames): elements are named in words.

In every case the descriptions of the elements follow the prompt, one line
each, so the look carries over even when no picture can. Pictures that do not
fit the model's limit are left out with a warning, and those elements are
described in words instead; descriptions that would pass the prompt limit are
dropped last, also with a warning.

The result is stored with each generation (the prompt the model saw, the
pictures and the element versions) and shown in the composer before Generate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from framestudio.catalog import ModelSpec
from framestudio.db.models import Asset, Element, ElementMedia
from framestudio.prompts import ElementToken, MoveToken, TextToken, Token, element_ids, warnings

Strategy = Literal["multi_reference", "single_image", "description"]
#: Pictures one element may give in multi-reference mode.
PER_ELEMENT = 3


class ResolvedReference(BaseModel):
    asset_id: str
    #: How the prompt calls it ("image 2").
    label: str
    #: "attached" (by hand) or "element".
    source: Literal["attached", "element"]
    handle: str | None = None
    thumb_url: str | None = None


class ResolvedElement(BaseModel):
    id: str
    handle: str
    name: str
    version: int
    #: Pictures sent for it (0: described in words).
    images: int
    described: bool


class Resolved(BaseModel):
    model: str
    strategy: Strategy
    #: The prompt the model is given.
    prompt: str
    references: list[ResolvedReference]
    elements: list[ResolvedElement]
    warnings: list[str]
    #: What stops the generation (an edit model with no picture).
    problems: list[str] = []


@dataclass
class _Element:
    row: Element
    pictures: list[str]
    given: list[str]


def strategy_for(spec: ModelSpec, frames: bool) -> Strategy:
    limits = spec.limits
    if frames or limits.max_references == 0 or "image" not in limits.reference_kinds:
        return "description"
    return "single_image" if limits.max_references == 1 else "multi_reference"


def _pictures(session: Session, element_id: str, kinds: list[str]) -> list[str]:
    rows = session.execute(
        select(Asset.id, Asset.kind)
        .join(ElementMedia, ElementMedia.asset_id == Asset.id)
        .where(ElementMedia.element_id == element_id, Asset.deleted_at.is_(None))
        .order_by(ElementMedia.position, ElementMedia.id)
    ).all()
    return [asset_id for asset_id, kind in rows if kind in kinds]


def _labels(numbers: list[int]) -> str:
    if len(numbers) == 1:
        return f"image {numbers[0]}"
    listed = ", ".join(str(n) for n in numbers[:-1])
    return f"images {listed} and {numbers[-1]}"


def _thumb(session: Session, asset_id: str) -> str | None:
    asset = session.get(Asset, asset_id)
    return f"/api/assets/{asset_id}/thumb" if asset and asset.thumb_path else None


def resolve(
    session: Session,
    spec: ModelSpec,
    tokens: list[Token],
    attached: list[str],
    *,
    frames: bool = False,
    element_pictures: bool = True,
) -> Resolved:
    strategy = strategy_for(spec, frames)
    kinds = list(spec.limits.reference_kinds)
    notes: list[str] = list(warnings(tokens))

    elements: list[_Element] = []
    for element_id in element_ids(tokens):
        row = session.get(Element, element_id)
        if row is not None:
            elements.append(_Element(row, _pictures(session, row.id, kinds or ["image"]), []))

    # Pictures: those attached by hand first, then the elements'.
    chosen: list[str] = [] if strategy == "description" else list(attached)
    capacity = {"description": 0, "single_image": 1}.get(strategy, spec.limits.max_references)

    def give(item: _Element, asset_id: str) -> None:
        if asset_id not in chosen:
            chosen.append(asset_id)
        item.given.append(asset_id)

    if not element_pictures:  # named and described only (element generators)
        for item in elements:
            item.pictures = []
    for item in elements:  # the primary picture of each, in order of use
        if item.pictures and len(chosen) < capacity:
            give(item, item.pictures[0])
    if strategy == "multi_reference":  # then more angles, in turns
        for extra in range(1, PER_ELEMENT):
            for item in elements:
                if len(item.pictures) > extra and len(chosen) < capacity:
                    give(item, item.pictures[extra])

    numbers = {asset_id: index + 1 for index, asset_id in enumerate(chosen)}
    for item in elements:
        if item.pictures and not item.given and strategy != "description":
            limit = "one reference image" if capacity == 1 else f"{capacity} reference images"
            notes.append(
                f"{spec.name} takes {limit}; @{item.row.handle} is described in words instead."
            )
    if frames and any(item.pictures for item in elements):
        notes.append(
            "Start and end frames take the place of reference images; "
            "elements are described in words."
        )

    # The prompt: elements by name (with their pictures), then their descriptions.
    by_id = {item.row.id: item for item in elements}
    parts: list[str] = []
    for token in tokens:
        if isinstance(token, TextToken):
            parts.append(token.text)
        elif isinstance(token, ElementToken):
            item = by_id.get(token.element_id or "")
            if item is None:
                parts.append(f"@{token.handle}")
            elif not item.given:
                parts.append(item.row.name)
            elif strategy == "single_image":
                parts.append(f"{item.row.name} (the reference image)")
            else:
                parts.append(f"{item.row.name} ({_labels([numbers[a] for a in item.given])})")
        elif isinstance(token, MoveToken):
            parts.append(f"#{token.name}")
        else:
            parts.append(f"/{token.name}")
    prompt = "".join(parts).strip()
    described = [item for item in elements if item.row.description.strip()]
    lines = [f"{item.row.name}: {' '.join(item.row.description.split())}" for item in described]
    limit = spec.limits.max_prompt_chars
    while lines and limit and len(prompt) + 2 + len("\n".join(lines)) > limit:
        dropped = described.pop()
        lines.pop()
        notes.append(
            f"The description of @{dropped.row.handle} was left out to fit "
            f"{spec.name}'s {limit}-character prompt."
        )
    if lines:
        prompt = f"{prompt}\n\n" + "\n".join(lines) if prompt else "\n".join(lines)
    kept = {item.row.id for item in described}

    references = [
        ResolvedReference(
            asset_id=asset_id,
            label="the reference image"
            if strategy == "single_image"
            else f"image {numbers[asset_id]}",
            source="attached" if asset_id in attached else "element",
            handle=next((i.row.handle for i in elements if asset_id in i.given), None),
            thumb_url=_thumb(session, asset_id),
        )
        for asset_id in chosen
    ]
    return Resolved(
        model=spec.id,
        strategy=strategy,
        prompt=prompt,
        references=references,
        elements=[
            ResolvedElement(
                id=item.row.id,
                handle=item.row.handle,
                name=item.row.name,
                version=item.row.version,
                images=len(item.given),
                described=item.row.id in kept,
            )
            for item in elements
        ],
        warnings=list(dict.fromkeys(notes)),
        problems=(
            [f"{spec.name} changes a picture: attach one or name an element that has pictures."]
            if spec_needs_picture(spec) and not chosen
            else []
        ),
    )


def spec_needs_picture(spec: ModelSpec) -> bool:
    """Image models that only edit (no text-to-image) need an input picture."""
    return (
        "image" in spec.modes
        and "text_to_image" not in spec.capabilities
        and "image_edit" in spec.capabilities
    )
