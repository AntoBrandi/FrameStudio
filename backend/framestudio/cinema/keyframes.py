"""Keyframe then shot (plan step M5.7).

A planned shot keeps a prompt and its cinema controls (a ShotSpec) together
with the video model that will animate it. It starts with keyframe stills from
an image model, made from the same prompt and controls at the clip's aspect
ratio (the compiler leaves camera moves and pace out of stills, with a note).
You approve one; the clip is then animated from it as its first frame, with
the same prompt and controls, and optionally towards an end frame where the
model takes one. A finished clip can be extended: its last frame becomes the
approved keyframe of a new shot that continues it.

Continuity is checked, not assumed: each finished clip's first frame is
compared with its keyframe (and its last with the end frame) by
``cinema.continuity.similarity``; scores are kept with the shot.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from framestudio.catalog import ModelSpec, ParamError, validate_params
from framestudio.cinema import ShotSpec
from framestudio.cinema.continuity import SAME, check_clip, extract_frame
from framestudio.db.models import Asset, Generation, Shot
from framestudio.generation import (
    AUTO,
    TOKENS,
    GenerationCreate,
    GenerationEstimate,
    GenerationOut,
    UnknownModelError,
    create_generations,
    estimate_generations,
    generation_out,
)
from framestudio.library.assets import AssetOut, Placement, ingest_file, load_out, resolve_placement
from framestudio.prompts import Token, render
from framestudio.providers.base import GenerationProvider
from framestudio.router import RouteRequest, route
from framestudio.services import Services

log = logging.getLogger("framestudio")

ShotStatus = Literal["keyframes", "choose", "animating", "done", "failed"]
_ACTIVE = {"queued", "running"}
_ENDED = {"failed", "cancelled", "interrupted"}


class ShotCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(default="", max_length=4000)
    prompt_tokens: list[Token] | None = Field(default=None, max_length=2000)
    spec: ShotSpec | None = None
    project_id: str | None = None
    folder_id: str | None = None
    #: The model that animates the approved keyframe ("auto": the router's
    #: pick among models that start a clip from an image).
    video_model: str = AUTO
    video_params: dict[str, Any] = Field(default_factory=dict)
    #: The image model for the keyframes ("auto": the router's pick).
    keyframe_model: str = AUTO
    keyframe_params: dict[str, Any] = Field(default_factory=dict)
    #: How many keyframes to make to choose from.
    count: int = Field(default=4, ge=1, le=4)
    seed: int | None = None


class MoreKeyframes(BaseModel):
    model_config = ConfigDict(extra="forbid")
    count: int = Field(default=4, ge=1, le=4)
    seed: int | None = None


class Animate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The approved keyframe (usually one of the shot's): the clip's first frame.
    keyframe_id: str
    #: Where the clip should end, for models that take an end frame.
    end_frame_id: str | None = None
    #: Override the shot's video model or settings for this take.
    video_model: str | None = None
    video_params: dict[str, Any] | None = None
    seed: int | None = None


class Extend(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The finished clip to continue (one of the shot's clips).
    generation_id: str
    #: What happens next; the shot's prompt when left out.
    prompt_tokens: list[Token] | None = Field(default=None, max_length=2000)


class ShotPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=120)
    archived: bool | None = None


class Continuity(BaseModel):
    #: Similarity of the clip's first frame to the keyframe, and of its last
    #: frame to the end frame (None without one), 0..1.
    start: float | None
    end: float | None
    #: Whether every frame that was given is matched (score >= ``SAME``).
    matches: bool


class ShotOut(BaseModel):
    id: str
    project_id: str | None
    folder_id: str | None
    title: str
    prompt: str
    prompt_tokens: list[Token]
    spec: ShotSpec | None
    video_model: str
    video_params: dict[str, Any]
    keyframe_model: str
    keyframe_params: dict[str, Any]
    keyframe_id: str | None
    end_frame_id: str | None
    continues_from: str | None
    status: ShotStatus
    keyframes: list[GenerationOut]
    clips: list[GenerationOut]
    #: Continuity per finished clip (generation id → scores).
    checks: dict[str, Continuity]
    archived: bool
    created_at: datetime
    updated_at: datetime


class ShotEstimate(BaseModel):
    ok: bool
    problems: list[str]
    keyframes: GenerationEstimate | None
    clip: GenerationEstimate | None
    #: Keyframes and one clip together (None when either has no price).
    cost_usd: float | None
    seconds: float | None
    #: The models "auto" resolved to.
    keyframe_model: str | None
    video_model: str | None


class ShotError(ValueError):
    """A request that cannot apply to this shot (422)."""


# ---------------------------------------------------------------- models


def _pick(services: Services, model: str, req: RouteRequest) -> str:
    if model != AUTO:
        return model
    result = route(services, req)
    if result.model is None:
        raise UnknownModelError(result.reason)
    return result.model


def _spec(services: Services, model_id: str) -> ModelSpec:
    spec = services.catalog.get(model_id)
    if spec is None:
        raise UnknownModelError(f"Unknown model '{model_id}'.")
    return spec


def _ratio(value: str) -> float:
    width, _, height = value.partition(":")
    try:
        return float(width) / float(height)
    except (ValueError, ZeroDivisionError):
        return 1.0


def keyframe_params_for(
    image: ModelSpec, video: ModelSpec, video_params: dict[str, Any], given: dict[str, Any]
) -> dict[str, Any]:
    """The keyframe model's settings: the clip's aspect ratio (or the nearest
    the image model offers), then whatever was given explicitly."""
    params: dict[str, Any] = {}
    aspect = validate_params(video, "video", video_params).get("aspect")
    choice = image.params.get("aspect")
    if aspect and choice is not None and choice.values:
        values = [str(v) for v in choice.values]
        params["aspect"] = (
            aspect
            if aspect in values
            else min(values, key=lambda v: abs(_ratio(v) - _ratio(str(aspect))))
        )
    return {**params, **given}


def _models(services: Services, body: ShotCreate) -> tuple[ModelSpec, ModelSpec]:
    video = _spec(
        services, _pick(services, body.video_model, RouteRequest(mode="video", start_frame=True))
    )
    if "video" not in video.modes or "image_to_video" not in video.capabilities:
        raise ParamError([f"{video.name} does not start a clip from an image"])
    image = _spec(services, _pick(services, body.keyframe_model, RouteRequest(mode="image")))
    if "image" not in image.modes:
        raise ParamError([f"{image.name} does not make images"])
    return image, video


# ---------------------------------------------------------------- reading


def _status(keyframes: list[Generation], clips: list[Generation]) -> ShotStatus:
    if clips:
        latest = clips[-1]
        if latest.status in _ACTIVE:
            return "animating"
        return "done" if latest.status == "succeeded" else "failed"
    if any(g.status in _ACTIVE for g in keyframes):
        return "keyframes"
    if keyframes and all(g.status in _ENDED for g in keyframes):
        return "failed"
    return "choose"


def _continuity(scores: dict[str, Any]) -> Continuity:
    given = [v for v in (scores.get("start"), scores.get("end")) if v is not None]
    return Continuity(
        start=scores.get("start"),
        end=scores.get("end"),
        matches=bool(given) and all(v >= SAME for v in given),
    )


def shot_out(session: Any, shot: Shot) -> ShotOut:
    generations = session.scalars(
        select(Generation)
        .where(Generation.shot_id == shot.id)
        .order_by(Generation.created_at, Generation.batch_index)
    ).all()
    keyframes = [g for g in generations if g.shot_role == "keyframe"]
    clips = [g for g in generations if g.shot_role == "clip"]
    return ShotOut(
        id=shot.id,
        project_id=shot.project_id,
        folder_id=shot.folder_id,
        title=shot.title,
        prompt=shot.prompt,
        prompt_tokens=shot.prompt_tokens or [],  # type: ignore[arg-type]
        spec=ShotSpec.model_validate(shot.spec) if shot.spec else None,
        video_model=shot.video_model,
        video_params=shot.video_params or {},
        keyframe_model=shot.keyframe_model,
        keyframe_params=shot.keyframe_params or {},
        keyframe_id=shot.keyframe_id,
        end_frame_id=shot.end_frame_id,
        continues_from=shot.continues_from,
        status=_status(keyframes, clips),
        keyframes=[generation_out(session, g) for g in keyframes],
        clips=[generation_out(session, g) for g in clips],
        checks={k: _continuity(v) for k, v in (shot.checks or {}).items()},
        archived=shot.archived,
        created_at=shot.created_at,
        updated_at=shot.updated_at,
    )


def _pending_checks(
    services: Services, shot_id: str
) -> list[tuple[str, Path, Path | None, Path | None]]:
    """Finished clips of the shot without continuity scores yet: (generation
    id, clip file, start picture, end picture)."""
    out = []
    with services.db.session() as session:
        shot = session.get(Shot, shot_id)
        if shot is None:
            return []
        done = set((shot.checks or {}).keys())
        clips = session.scalars(
            select(Generation).where(
                Generation.shot_id == shot_id,
                Generation.shot_role == "clip",
                Generation.status == "succeeded",
            )
        ).all()
        for clip in clips:
            if clip.id in done:
                continue
            asset = session.scalars(
                select(Asset).where(Asset.source_generation_id == clip.id, Asset.kind == "video")
            ).first()
            if asset is None:
                continue

            def path(asset_id: str | None) -> Path | None:
                frame = session.get(Asset, asset_id) if asset_id else None
                return services.media.resolve(frame.path) if frame else None

            out.append(
                (
                    clip.id,
                    services.media.resolve(asset.path),
                    path(clip.start_frame_id),
                    path(clip.end_frame_id),
                )
            )
    return out


def run_checks(services: Services, shot_id: str) -> None:
    """Score the continuity of finished clips that have not been scored yet."""
    pending = _pending_checks(services, shot_id)
    if not pending:
        return
    results: dict[str, Any] = {}
    for generation_id, clip, start, end in pending:
        scratch = services.media.temp_path(".continuity")
        scratch.mkdir(parents=True, exist_ok=True)
        try:
            results[generation_id] = check_clip(clip, start, end, scratch)
        except (ValueError, OSError) as exc:  # an unreadable clip: record nothing, try later
            log.warning("Continuity check failed for %s: %s", generation_id, exc)
        finally:
            for file in scratch.glob("*"):
                file.unlink(missing_ok=True)
            scratch.rmdir()
    if not results:
        return
    with services.db.session() as session:
        shot = session.get(Shot, shot_id)
        if shot is not None:
            shot.checks = {**(shot.checks or {}), **results}


def get_shot(services: Services, shot_id: str) -> ShotOut | None:
    run_checks(services, shot_id)
    with services.db.session() as session:
        shot = session.get(Shot, shot_id)
        return shot_out(session, shot) if shot else None


def list_shots(
    services: Services, project_id: str | None, archived: bool = False, limit: int = 50
) -> list[ShotOut]:
    with services.db.session() as session:
        query = select(Shot.id).where(Shot.archived.is_(archived))
        if project_id == "none":
            query = query.where(Shot.project_id.is_(None))
        elif project_id is not None:
            query = query.where(Shot.project_id == project_id)
        ids = session.scalars(query.order_by(Shot.created_at.desc()).limit(limit)).all()
    for shot_id in ids:
        run_checks(services, shot_id)
    with services.db.session() as session:
        shots = [session.get(Shot, i) for i in ids]
        return [shot_out(session, s) for s in shots if s is not None]


def patch_shot(services: Services, shot_id: str, body: ShotPatch) -> ShotOut | None:
    with services.db.session() as session:
        shot = session.get(Shot, shot_id)
        if shot is None:
            return None
        if body.title is not None:
            shot.title = body.title.strip()
        if body.archived is not None:
            shot.archived = body.archived
        session.flush()
        out = shot_out(session, shot)
    services.events.publish("shot.updated", {"id": shot_id})
    return out


# ---------------------------------------------------------------- making


def _title(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= 80 else text[:79].rsplit(" ", 1)[0] + "…"


async def _keyframes(
    services: Services,
    providers: dict[str, GenerationProvider],
    shot: Shot,
    count: int,
    seed: int | None,
    tokens: list[Token] | None = None,
) -> list[GenerationOut]:
    return await create_generations(
        services,
        GenerationCreate(
            mode="image",
            model=shot.keyframe_model,
            prompt=shot.prompt,
            prompt_tokens=tokens if tokens is not None else (shot.prompt_tokens or None),  # type: ignore[arg-type]
            shot=ShotSpec.model_validate(shot.spec) if shot.spec else None,
            batch=count,
            seed=seed,
            params=shot.keyframe_params or {},
            project_id=shot.project_id,
            folder_id=shot.folder_id,
            shot_id=shot.id,
            shot_role="keyframe",
        ),
        providers,
    )


def _insert(services: Services, **values: Any) -> str:
    with services.db.session() as session:
        shot = Shot(**values)
        session.add(shot)
        session.flush()
        return shot.id


def _delete(services: Services, shot_id: str) -> None:
    with services.db.session() as session:
        shot = session.get(Shot, shot_id)
        if shot is not None:
            session.delete(shot)


def _place(services: Services, project_id: str | None, folder_id: str | None) -> Placement:
    with services.db.session() as session:
        return resolve_placement(session, project_id, folder_id)  # LookupError when gone


async def create_shot(
    services: Services, body: ShotCreate, providers: dict[str, GenerationProvider]
) -> ShotOut:
    """Plan a shot and start its keyframes."""
    image, video = await asyncio.to_thread(_models, services, body)
    video_params = validate_params(video, "video", body.video_params)  # ParamError: refused
    keyframe_params = keyframe_params_for(image, video, body.video_params, body.keyframe_params)
    placement = await asyncio.to_thread(_place, services, body.project_id, body.folder_id)
    shot_id = await asyncio.to_thread(
        _insert,
        services,
        project_id=placement.project_id,
        folder_id=placement.folder_id,
        title="",
        prompt=body.prompt,
        prompt_tokens=[t.model_dump(mode="json") for t in body.prompt_tokens or []],
        spec=body.spec.model_dump(mode="json", exclude_defaults=True)
        if body.spec and not body.spec.is_empty()
        else None,
        video_model=video.id,
        video_params=video_params,
        keyframe_model=image.id,
        keyframe_params=keyframe_params,
        checks={},
    )

    def load() -> Shot:
        with services.db.session() as session:
            shot = session.get(Shot, shot_id)
            assert shot is not None
            session.expunge(shot)
            return shot

    shot = await asyncio.to_thread(load)
    try:
        made = await _keyframes(
            services, providers, shot, body.count, body.seed, body.prompt_tokens
        )
    except BaseException:
        await asyncio.to_thread(_delete, services, shot_id)
        raise

    def settle() -> ShotOut:
        # The prompt as the generations normalized it (chips resolved for the project).
        first = made[0]
        with services.db.session() as session:
            row = session.get(Shot, shot_id)
            assert row is not None
            row.prompt = first.prompt
            row.prompt_tokens = [t.model_dump(mode="json") for t in first.prompt_tokens]
            row.title = _title(first.prompt) or "Untitled shot"
            session.flush()
            return shot_out(session, row)

    out = await asyncio.to_thread(settle)
    services.events.publish("shot.updated", {"id": shot_id})
    return out


def _load(services: Services, shot_id: str) -> Shot:
    with services.db.session() as session:
        shot = session.get(Shot, shot_id)
        if shot is None:
            raise LookupError("shot")
        session.expunge(shot)
        return shot


async def more_keyframes(
    services: Services, shot_id: str, body: MoreKeyframes, providers: dict[str, GenerationProvider]
) -> ShotOut:
    shot = await asyncio.to_thread(_load, services, shot_id)
    await _keyframes(services, providers, shot, body.count, body.seed)
    services.events.publish("shot.updated", {"id": shot_id})
    out = await asyncio.to_thread(get_shot, services, shot_id)
    assert out is not None
    return out


async def animate(
    services: Services, shot_id: str, body: Animate, providers: dict[str, GenerationProvider]
) -> ShotOut:
    """Approve a keyframe (and an end frame) and animate the clip from it with
    the shot's prompt and controls."""
    shot = await asyncio.to_thread(_load, services, shot_id)
    video_model = body.video_model or shot.video_model
    video_params = body.video_params if body.video_params is not None else (shot.video_params or {})
    spec = await asyncio.to_thread(_spec, services, video_model)
    if "image_to_video" not in spec.capabilities:
        raise ParamError([f"{spec.name} does not start a clip from an image"])
    await create_generations(
        services,
        GenerationCreate(
            mode="video",
            model=video_model,
            prompt=shot.prompt,
            prompt_tokens=shot.prompt_tokens or None,  # type: ignore[arg-type]
            shot=ShotSpec.model_validate(shot.spec) if shot.spec else None,
            params=video_params,
            seed=body.seed,
            start_frame=body.keyframe_id,
            end_frame=body.end_frame_id,
            project_id=shot.project_id,
            folder_id=shot.folder_id,
            shot_id=shot.id,
            shot_role="clip",
        ),
        providers,
    )  # ParamError when the frames do not suit the model

    def approve() -> None:
        with services.db.session() as session:
            row = session.get(Shot, shot_id)
            assert row is not None
            row.keyframe_id = body.keyframe_id
            row.end_frame_id = body.end_frame_id
            row.video_model = video_model
            row.video_params = video_params
            row.archived = False

    await asyncio.to_thread(approve)
    services.events.publish("shot.updated", {"id": shot_id})
    out = await asyncio.to_thread(get_shot, services, shot_id)
    assert out is not None
    return out


def frame_asset(
    services: Services,
    asset_id: str,
    which: Literal["first", "last"],
    placement: Placement | None = None,
) -> AssetOut:
    """A clip's first or last frame as a new picture in the library, next to
    the clip (or in ``placement``). Used to extend a clip."""
    with services.db.session() as session:
        asset = session.get(Asset, asset_id)
        if asset is None or asset.deleted_at is not None:
            raise LookupError("asset")
        if asset.kind != "video":
            raise ShotError("Only a clip has frames to take")
        clip = services.media.resolve(asset.path)
        where = placement or Placement(project_id=asset.project_id, folder_id=asset.folder_id)
        stem = Path(asset.original_name).stem if asset.original_name else f"clip-{asset.id[:8]}"
    out = services.media.temp_path(".png")
    extract_frame(clip, which, out)
    made, _ = ingest_file(
        services,
        out,
        name=f"{stem}-{which}-frame.png",
        provenance="generated",
        placement=where,
        # Not the clip's generation: a generation's result is its newest asset,
        # and a frame must not take the clip's place (graded copies do the same).
        dedupe=True,
    )
    return made


async def extend(
    services: Services, shot_id: str, body: Extend, providers: dict[str, GenerationProvider]
) -> ShotOut:
    """Continue a finished clip: its last frame becomes the approved keyframe
    of a new shot with the same controls, animated at once."""
    shot = await asyncio.to_thread(_load, services, shot_id)

    def clip_asset() -> str:
        with services.db.session() as session:
            generation = session.get(Generation, body.generation_id)
            if (
                generation is None
                or generation.shot_id != shot_id
                or generation.shot_role != "clip"
            ):
                raise ShotError("That clip is not part of this shot")
            asset = session.scalars(
                select(Asset).where(
                    Asset.source_generation_id == generation.id,
                    Asset.kind == "video",
                    Asset.deleted_at.is_(None),
                )
            ).first()
            if generation.status != "succeeded" or asset is None:
                raise ShotError("Only a finished clip can be extended")
            return asset.id

    asset_id = await asyncio.to_thread(clip_asset)
    placement = Placement(project_id=shot.project_id, folder_id=shot.folder_id)
    frame = await asyncio.to_thread(frame_asset, services, asset_id, "last", placement)
    tokens = (
        [t.model_dump(mode="json") for t in body.prompt_tokens]
        if body.prompt_tokens is not None
        else shot.prompt_tokens
    )
    text = render(TOKENS.validate_python(tokens or []))
    new_id = await asyncio.to_thread(
        _insert,
        services,
        project_id=shot.project_id,
        folder_id=shot.folder_id,
        title=_title(f"{shot.title} (continued)"),
        prompt=text,
        prompt_tokens=tokens or [],
        spec=shot.spec,
        video_model=shot.video_model,
        video_params=shot.video_params or {},
        keyframe_model=shot.keyframe_model,
        keyframe_params=shot.keyframe_params or {},
        continues_from=body.generation_id,
        checks={},
    )
    try:
        return await animate(services, new_id, Animate(keyframe_id=frame.id), providers)
    except BaseException:
        await asyncio.to_thread(_delete, services, new_id)
        raise


async def estimate_shot(
    services: Services, body: ShotCreate, providers: dict[str, GenerationProvider]
) -> ShotEstimate:
    """What the keyframes and one clip will cost and take, before anything starts."""
    try:
        image, video = await asyncio.to_thread(_models, services, body)
    except (UnknownModelError, ParamError) as exc:
        problems = exc.problems if isinstance(exc, ParamError) else [str(exc)]
        return ShotEstimate(
            ok=False, problems=problems, keyframes=None, clip=None, cost_usd=None,
            seconds=None, keyframe_model=None, video_model=None,
        )  # fmt: skip
    params = keyframe_params_for(image, video, body.video_params, body.keyframe_params)
    common = {"prompt": "", "shot": body.spec, "project_id": body.project_id}
    keyframes = await estimate_generations(
        services,
        GenerationCreate(mode="image", model=image.id, batch=body.count, params=params, **common),
        providers,
    )
    clip = await estimate_generations(
        services,
        GenerationCreate(mode="video", model=video.id, params=body.video_params, **common),
        providers,
    )
    problems = [*keyframes.problems, *clip.problems]

    def total(a: float | None, b: float | None) -> float | None:
        return None if a is None and b is None else round((a or 0) + (b or 0), 4)

    return ShotEstimate(
        ok=keyframes.ok and clip.ok,
        problems=list(dict.fromkeys(problems)),
        keyframes=keyframes,
        clip=clip,
        cost_usd=total(keyframes.cost_usd, clip.cost_usd),
        seconds=total(keyframes.seconds, clip.seconds),
        keyframe_model=image.id,
        video_model=video.id,
    )


def load_asset(services: Services, asset_id: str) -> AssetOut | None:
    with services.db.session() as session:
        return load_out(session, asset_id)
