"""Planned shots: keyframes first, then the clip (M5.7)."""

from __future__ import annotations

import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from framestudio.api.deps import ServicesDep
from framestudio.catalog import ParamError
from framestudio.cinema import keyframes as kf
from framestudio.generation import UnknownModelError
from framestudio.library.assets import AssetOut
from framestudio.providers.base import ProviderError

router = APIRouter(prefix="/api", tags=["shots"])
NOT_FOUND = {404: {"description": "Not found"}}


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, UnknownModelError | kf.ShotError):
        return HTTPException(422, str(exc))
    if isinstance(exc, ParamError):
        return HTTPException(422, exc.problems)
    if isinstance(exc, ProviderError):  # a spending cap
        return HTTPException(status.HTTP_402_PAYMENT_REQUIRED, exc.message)
    if isinstance(exc, LookupError):
        return HTTPException(
            status.HTTP_404_NOT_FOUND, f"{str(exc).strip(chr(39)).capitalize()} not found"
        )
    raise exc


_ERRORS = (UnknownModelError, kf.ShotError, ParamError, ProviderError, LookupError)


@router.post("/shots", status_code=status.HTTP_201_CREATED, responses=NOT_FOUND)
async def create_shot(body: kf.ShotCreate, services: ServicesDep) -> kf.ShotOut:
    """Plan a shot and start its keyframes: stills from the same prompt and
    cinema controls, at the clip's aspect ratio."""
    try:
        return await kf.create_shot(services, body, services.providers)
    except _ERRORS as exc:
        raise _http(exc) from None


@router.post("/shots/estimate")
async def estimate_shot(body: kf.ShotCreate, services: ServicesDep) -> kf.ShotEstimate:
    """Cost and time of the keyframes plus one clip, without starting anything."""
    return await kf.estimate_shot(services, body, services.providers)


@router.get("/shots")
def list_shots(
    services: ServicesDep,
    project_id: Annotated[str | None, Query(description="A project id, or 'none'")] = None,
    archived: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[kf.ShotOut]:
    return kf.list_shots(services, project_id, archived, limit)


@router.get("/shots/{shot_id}", responses=NOT_FOUND)
def get_shot(shot_id: str, services: ServicesDep) -> kf.ShotOut:
    out = kf.get_shot(services, shot_id)
    if out is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shot not found")
    return out


@router.patch("/shots/{shot_id}", responses=NOT_FOUND)
def patch_shot(shot_id: str, body: kf.ShotPatch, services: ServicesDep) -> kf.ShotOut:
    out = kf.patch_shot(services, shot_id, body)
    if out is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shot not found")
    return out


@router.post("/shots/{shot_id}/keyframes", responses=NOT_FOUND)
async def more_keyframes(shot_id: str, body: kf.MoreKeyframes, services: ServicesDep) -> kf.ShotOut:
    """More keyframes to choose from, with the same settings."""
    try:
        return await kf.more_keyframes(services, shot_id, body, services.providers)
    except _ERRORS as exc:
        raise _http(exc) from None


@router.post("/shots/{shot_id}/animate", responses=NOT_FOUND)
async def animate_shot(shot_id: str, body: kf.Animate, services: ServicesDep) -> kf.ShotOut:
    """Approve a keyframe (and an end frame, where the model takes one) and
    animate the clip from it with the shot's prompt and controls."""
    try:
        return await kf.animate(services, shot_id, body, services.providers)
    except _ERRORS as exc:
        raise _http(exc) from None


@router.post("/shots/{shot_id}/extend", status_code=status.HTTP_201_CREATED, responses=NOT_FOUND)
async def extend_shot(shot_id: str, body: kf.Extend, services: ServicesDep) -> kf.ShotOut:
    """Continue a finished clip: a new shot starts from its last frame."""
    try:
        return await kf.extend(services, shot_id, body, services.providers)
    except _ERRORS as exc:
        raise _http(exc) from None


class FrameRequest(BaseModel):
    which: Literal["first", "last"] = "last"


@router.post("/assets/{asset_id}/frame", tags=["assets"], responses=NOT_FOUND)
async def clip_frame(asset_id: str, body: FrameRequest, services: ServicesDep) -> AssetOut:
    """A clip's first or last frame as a new picture beside it (to extend a clip)."""
    try:
        return await asyncio.to_thread(kf.frame_asset, services, asset_id, body.which)
    except (kf.ShotError, LookupError) as exc:
        raise _http(exc) from None
    except ValueError as exc:  # no frame could be read
        raise HTTPException(422, str(exc)) from None
