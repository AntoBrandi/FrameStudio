"""Element generators (plan step M4.6): from an element's picture, make the
set of views that keeps it consistent in later shots, and add them to it.

Recipes:

* **character_sheet**: full-body front and back, and a portrait;
* **location_set**: establishing, wide and detail shots;
* **prop_turnaround**: front, three-quarter, side and back;
* **turntable**: a short clip orbiting the element (video).

Each view is one generation whose prompt names the element as a token, so
the reference resolver (M4.4) sends its pictures and description the way the
chosen model takes them. Results are added to the element as they finish
(role "sheet", or "turntable" for the clip). Nano Banana (images) and
Seedance (the clip) are tried first; otherwise the router's pick for a
prompt with one reference picture, then any model that fits.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from framestudio.catalog import ModelSpec
from framestudio.db.models import Element
from framestudio.prompts import element, text
from framestudio.providers.base import ProviderError
from framestudio.references import PER_ELEMENT, _pictures
from framestudio.router import RouteRequest, route

if TYPE_CHECKING:
    from framestudio.generation import GenerationCreate, GenerationEstimate, GenerationOut
    from framestudio.services import Services

RecipeId = Literal["character_sheet", "location_set", "prop_turnaround", "turntable"]


class View(BaseModel):
    id: str
    label: str
    #: The prompt around the element: ``before @element after``.
    before: str
    after: str


class Recipe(BaseModel):
    id: RecipeId
    label: str
    mode: Literal["image", "video"]
    #: Element categories it suits (the UI offers these first).
    categories: list[str]
    role: Literal["sheet", "turntable"]
    #: Models tried first, in order, when they can run.
    preferred: list[str]
    views: list[View]
    params: dict[str, str] = {}


_SAME_PERSON = " Keep the same face, hair, clothing and colours as the reference."
_SAME_PLACE = " Keep the same architecture, materials, colours and light as the reference."
_SAME_THING = " Keep the same shape, materials and colours as the reference."
_STUDIO = "plain light-grey studio background, soft even lighting"

RECIPES: dict[str, Recipe] = {
    r.id: r
    for r in [
        Recipe(
            id="character_sheet",
            label="Character sheet",
            mode="image",
            categories=["character", "auto"],
            role="sheet",
            preferred=["nano-banana-pro", "nano-banana"],
            views=[
                View(
                    id="front",
                    label="Front",
                    before="Full-body front view of ",
                    after=f", standing straight, arms relaxed, facing the camera, {_STUDIO}, "
                    "sharp focus. Character reference sheet." + _SAME_PERSON,
                ),
                View(
                    id="back",
                    label="Back",
                    before="Full-body back view of ",
                    after=f", standing straight, seen from behind, {_STUDIO}. "
                    "Character reference sheet." + _SAME_PERSON,
                ),
                View(
                    id="portrait",
                    label="Portrait",
                    before="Head-and-shoulders portrait of ",
                    after=", facing the camera, neutral expression, plain light-grey background, "
                    "soft even lighting, sharp detail on the face." + _SAME_PERSON,
                ),
            ],
        ),
        Recipe(
            id="location_set",
            label="Location set",
            mode="image",
            categories=["location", "auto"],
            role="sheet",
            preferred=["nano-banana-pro", "nano-banana"],
            views=[
                View(
                    id="establishing",
                    label="Establishing",
                    before="Establishing shot of ",
                    after=": a high, wide view that shows the whole place and its surroundings, "
                    "natural light, no people." + _SAME_PLACE,
                ),
                View(
                    id="wide",
                    label="Wide",
                    before="Wide shot of ",
                    after=" at eye level, showing the main space, natural light, no people."
                    + _SAME_PLACE,
                ),
                View(
                    id="detail",
                    label="Detail",
                    before="Close detail shot of ",
                    after=": the textures and materials that define the place, shallow depth "
                    "of field, no people." + _SAME_PLACE,
                ),
            ],
        ),
        Recipe(
            id="prop_turnaround",
            label="Prop turnaround",
            mode="image",
            categories=["prop", "auto"],
            role="sheet",
            preferred=["nano-banana-pro", "nano-banana"],
            views=[
                View(
                    id=side.lower().replace(" ", "_").replace("-", "_"),
                    label=side,
                    before=f"{side} view of ",
                    after=f", centred, {_STUDIO}, product photograph." + _SAME_THING,
                )
                for side in ("Front", "Three-quarter", "Side", "Back")
            ],
        ),
        Recipe(
            id="turntable",
            label="Turntable clip",
            mode="video",
            categories=["character", "prop", "auto"],
            role="turntable",
            preferred=["seedance-2-0", "seedance-2-0-fast"],
            views=[
                View(
                    id="turntable",
                    label="Turntable",
                    before="A steady 360-degree turntable shot of ",
                    after=": the camera orbits once around it at eye level, "
                    f"{_STUDIO}, nothing else moves.",
                )
            ],
        ),
    ]
}


class ElementGenerate(BaseModel):
    recipe: RecipeId
    #: A catalog model, or None for the recipe's choice.
    model: str | None = None
    #: Only these views of the recipe (default: all).
    views: list[str] | None = Field(default=None, max_length=8)
    seed: int | None = Field(default=None, ge=0)


class RecipeViewOut(BaseModel):
    id: str
    label: str


class RecipeOut(BaseModel):
    id: RecipeId
    label: str
    mode: Literal["image", "video"]
    categories: list[str]
    views: list[RecipeViewOut]
    #: The model it would use now, or None when nothing can run it.
    model: str | None
    model_name: str | None


class ElementGenerateEstimate(BaseModel):
    ok: bool
    problems: list[str]
    model: str | None
    model_name: str | None
    views: int
    cost_usd: float | None
    seconds: float | None


class GeneratorError(ValueError):
    pass


def pick_model(services: Services, recipe: Recipe, wanted: str | None = None) -> ModelSpec | None:
    """The model a recipe uses: the one asked for, a preferred one that can
    run, the router's pick for one reference picture, or any that fits."""
    if wanted:
        spec = services.catalog.get(wanted)
        if spec is None or recipe.mode not in spec.modes:
            raise GeneratorError(f"'{wanted}' cannot make {recipe.label.lower()}s.")
        return spec
    for model_id in recipe.preferred:
        spec = services.catalog.get(model_id)
        if spec is not None and spec.provider in services.providers:
            return spec
    for references in (1, 0):
        picked = route(services, RouteRequest(mode=recipe.mode, references=references)).model
        if picked:
            return services.catalog.get(picked)
    return None


def recipes_for(services: Services) -> list[RecipeOut]:
    out = []
    for recipe in RECIPES.values():
        spec = pick_model(services, recipe)
        out.append(
            RecipeOut(
                id=recipe.id,
                label=recipe.label,
                mode=recipe.mode,
                categories=recipe.categories,
                views=[RecipeViewOut(id=v.id, label=v.label) for v in recipe.views],
                model=spec.id if spec else None,
                model_name=spec.name if spec else None,
            )
        )
    return out


def _requests(
    services: Services, element_id: str, body: ElementGenerate
) -> tuple[Recipe, ModelSpec | None, list[GenerationCreate]]:
    from framestudio.generation import GenerationCreate

    recipe = RECIPES[body.recipe]
    with services.db.session() as session:
        row = session.get(Element, element_id)
        if row is None:
            raise LookupError("element")
        handle, project_id = row.handle, row.project_id
        pictures = _pictures(session, element_id, ["image"])
    spec = pick_model(services, recipe, body.model)
    # Every view starts from the same pictures: those the element has now
    # (results of the first views must not feed the later ones).
    room = 0
    if spec is not None and "image" in spec.limits.reference_kinds:
        room = min(spec.limits.max_references, PER_ELEMENT)
    sources = pictures[:room]
    views = [v for v in recipe.views if body.views is None or v.id in body.views]
    if not views:
        raise GeneratorError("Pick at least one view.")
    requests = [
        GenerationCreate(
            mode=recipe.mode,
            model=spec.id if spec else "auto",
            prompt_tokens=[text(view.before), element(handle, element_id), text(view.after)],
            params=dict(recipe.params),
            seed=None if body.seed is None else body.seed + index,
            project_id=project_id,
            references=sources,
            element_pictures=False,
            attach_element_id=element_id,
            attach_role=recipe.role,
        )
        for index, view in enumerate(views)
    ]
    return recipe, spec, requests


async def estimate_element_generation(
    services: Services, element_id: str, body: ElementGenerate
) -> ElementGenerateEstimate:
    from framestudio.generation import estimate_generations

    recipe, spec, requests = await asyncio.to_thread(_requests, services, element_id, body)
    if spec is None:
        return ElementGenerateEstimate(
            ok=False,
            problems=[f"No connected model can make a {recipe.label.lower()}."],
            model=None,
            model_name=None,
            views=len(requests),
            cost_usd=None,
            seconds=None,
        )
    estimates: list[GenerationEstimate] = [
        await estimate_generations(services, request, services.providers) for request in requests
    ]
    problems = list(dict.fromkeys(p for e in estimates for p in e.problems))
    costs = [e.cost_usd for e in estimates if e.cost_usd is not None]
    cost = round(sum(costs), 4) if costs else None
    if cost and spec.runtime == "api" and not problems:
        try:
            await asyncio.to_thread(services.usage.check_cap, spec.provider, cost)
        except ProviderError as exc:
            problems.append(exc.message)
    seconds = [e.seconds for e in estimates if e.seconds is not None]
    return ElementGenerateEstimate(
        ok=not problems,
        problems=problems,
        model=spec.id,
        model_name=spec.name,
        views=len(requests),
        cost_usd=cost,
        seconds=max(seconds) if seconds else None,
    )


async def generate_for_element(
    services: Services, element_id: str, body: ElementGenerate
) -> list[GenerationOut]:
    """Queue one generation per view; results are added to the element."""
    from framestudio.generation import create_generations

    estimate = await estimate_element_generation(services, element_id, body)
    if not estimate.ok:
        raise GeneratorError(" ".join(estimate.problems))
    _, _, requests = await asyncio.to_thread(_requests, services, element_id, body)
    out: list[GenerationOut] = []
    for request in requests:
        out.extend(await create_generations(services, request, services.providers))
    return out
