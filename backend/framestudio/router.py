"""Auto (plan step M2.9): the model that fits what was given and can run now.

A model fits when it makes the mode and takes the inputs: start and end
frames, and as many references as were given. Among models that fit and are
available, the lowest ``auto_rank`` in the catalog wins (hosted models now;
local variants join in M3 with ranks of their own). Models without a rank are
never picked automatically.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from framestudio.catalog import ModelSpec
    from framestudio.services import Services

AUTO = "auto"


class RouteRequest(BaseModel):
    mode: Literal["image", "video"] = "image"
    references: int = Field(default=0, ge=0, le=64)
    start_frame: bool = False
    end_frame: bool = False


class RouteOption(BaseModel):
    id: str
    name: str
    rank: int | None
    available: bool
    #: Why the model does not fit these inputs, or None when it does.
    misfit: str | None


class RouteResult(BaseModel):
    #: The chosen model, or None when nothing fits and can run.
    model: str | None
    reason: str
    options: list[RouteOption]


def misfit(spec: ModelSpec, req: RouteRequest) -> str | None:
    if req.mode not in spec.modes:
        return f"does not make {req.mode}s"
    if req.start_frame and "image_to_video" not in spec.capabilities:
        return "does not start from an image"
    if req.end_frame and "start_end_frames" not in spec.capabilities:
        return "does not take an end frame"
    if req.references and spec.limits.max_references < req.references:
        return f"takes at most {spec.limits.max_references} references"
    return None


def route(services: Services, req: RouteRequest) -> RouteResult:
    from framestudio.generation import availability

    options = []
    for spec in services.catalog.all():
        available, _ = availability(services, spec)
        options.append(
            RouteOption(
                id=spec.id,
                name=spec.name,
                rank=spec.auto_rank,
                available=available,
                misfit=misfit(spec, req),
            )
        )
    options.sort(key=lambda o: (o.rank is None, o.rank or 0, o.id))
    fitting = [o for o in options if o.rank is not None and o.misfit is None]
    chosen = next((o for o in fitting if o.available), None)
    inputs = []
    if req.start_frame:
        inputs.append("a start and end frame" if req.end_frame else "a start frame")
    if req.references:
        inputs.append(f"{req.references} reference{'s' if req.references != 1 else ''}")
    given = f" with {' and '.join(inputs)}" if inputs else ""
    if chosen:
        return RouteResult(
            model=chosen.id,
            reason=f"{chosen.name}: the best available {req.mode} model{given}.",
            options=options,
        )
    if fitting:
        names = ", ".join(o.name for o in fitting[:3])
        reason = (
            f"No connected model makes {article(req.mode)}{given}. Connect a provider for {names}."
        )
    else:
        reason = f"No model in the catalog makes {article(req.mode)}{given}."
    return RouteResult(model=None, reason=reason, options=options)


def article(mode: str) -> str:
    return f"an {mode}" if mode[0] in "aeiou" else f"a {mode}"
