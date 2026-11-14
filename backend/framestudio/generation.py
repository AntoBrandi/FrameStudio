"""Generations: request → job → asset, with lineage (plan step M0.5)."""

from __future__ import annotations

import asyncio
import secrets
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from sqlalchemy import and_, exists, func, not_, select, update
from sqlalchemy.orm import Session

from framestudio import prompts
from framestudio.catalog import ParamError, estimate_cost, validate_params
from framestudio.cinema import ShotSpec
from framestudio.db.base import utcnow
from framestudio.db.models import (
    Asset,
    Element,
    ElementMedia,
    ElementUse,
    Generation,
    Job,
    Shot,
)
from framestudio.jobs.base import JobContext, JobError, JobKind, JobSnapshot, PollResult
from framestudio.library.assets import (
    AssetOut,
    Placement,
    asset_out,
    ingest_file,
    resolve_placement,
)
from framestudio.library.describe import describe_if_wanted
from framestudio.library.media import MediaError
from framestudio.prompts import Token
from framestudio.providers.base import (
    GenerationOutput,
    GenerationProvider,
    GenerationRequest,
    HostedProvider,
    LocalProvider,
    ProviderError,
    RemoteProvider,
    with_retries,
)
from framestudio.references import Resolved, resolve
from framestudio.router import AUTO, RouteRequest, route
from framestudio.services import Services

MAX_SEED = 2**31 - 1
GenerationStatus = Literal["queued", "running", "succeeded", "failed", "cancelled", "interrupted"]
_STATUS_FROM_JOB: dict[str, GenerationStatus] = {
    "queued": "queued",
    "submitting": "running",
    "running": "running",
    "succeeded": "succeeded",
    "failed": "failed",
    "cancelled": "cancelled",
    "interrupted": "interrupted",
}


TOKENS: TypeAdapter[list[Token]] = TypeAdapter(list[Token])


class GenerationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["image", "video"] = "image"
    model: str = "mock"
    prompt: str = Field(default="", max_length=4000)
    #: The prompt as tokens (M4.3). When given, ``prompt`` is ignored and made
    #: from them; when not, ``prompt`` is parsed into tokens.
    prompt_tokens: list[Token] | None = Field(default=None, max_length=2000)
    seed: int | None = Field(default=None, ge=0, le=MAX_SEED)
    batch: int = Field(default=1, ge=1, le=4)
    params: dict[str, Any] = Field(default_factory=dict)
    #: Library assets given to the model as references, in order (M2.5).
    references: list[str] = Field(default_factory=list, max_length=16)
    #: Image to video: the first frame, and optionally the last (asset ids).
    start_frame: str | None = None
    end_frame: str | None = None
    parent_asset_id: str | None = None
    #: Where results land: a project, a folder (which implies its project), or
    #: the global library when both are omitted.
    project_id: str | None = None
    folder_id: str | None = None
    #: Cinema controls (M5.1): film, camera, moves, palette and lighting.
    shot: ShotSpec | None = None
    #: Send the pictures of the elements named in the prompt (M4.4). Element
    #: generators turn it off and attach the pictures they start from.
    element_pictures: bool = True
    #: Add the result to this element's pictures, with this role (M4.6).
    attach_element_id: str | None = None
    attach_role: Literal["sheet", "angle", "turntable"] = "sheet"
    #: The planned shot this belongs to (M5.7) and its part in it; set by
    #: ``cinema.keyframes``.
    shot_id: str | None = None
    shot_role: Literal["keyframe", "clip"] | None = None


class GenerationOut(BaseModel):
    id: str
    batch_id: str
    batch_index: int
    mode: Literal["image", "video"]
    model_id: str
    provider: str
    prompt: str
    prompt_tokens: list[Token]
    shot: ShotSpec | None = None
    #: What the model was given (M4.4); None for older generations.
    resolved: Resolved | None
    params: dict[str, Any]
    seed: int | None
    status: GenerationStatus
    error_code: str | None
    error_message: str | None
    job_id: str | None
    references: list[str]
    start_frame: str | None
    end_frame: str | None
    parent_asset_id: str | None
    project_id: str | None
    folder_id: str | None
    #: The element the result is added to (M4.6), if any.
    attach_element_id: str | None = None
    #: The planned shot (M5.7) and whether this is one of its keyframes or a clip.
    shot_id: str | None = None
    shot_role: Literal["keyframe", "clip"] | None = None
    asset: AssetOut | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


def stored_tokens(generation: Generation) -> list[Token]:
    """The generation's prompt tokens (older generations: one text token)."""
    if generation.prompt_tokens is None:
        return [prompts.text(generation.prompt)] if generation.prompt else []
    return TOKENS.validate_python(generation.prompt_tokens)


def generation_out(session: Session, generation: Generation) -> GenerationOut:
    asset = session.scalars(
        select(Asset)
        .where(Asset.source_generation_id == generation.id, Asset.deleted_at.is_(None))
        .order_by(Asset.created_at.desc())
        .limit(1)
    ).first()
    return GenerationOut(
        id=generation.id,
        batch_id=generation.batch_id,
        batch_index=generation.batch_index,
        mode=generation.mode,  # type: ignore[arg-type]
        model_id=generation.model_id,
        provider=generation.provider,
        prompt=generation.prompt,
        prompt_tokens=stored_tokens(generation),
        shot=ShotSpec.model_validate(generation.shot) if generation.shot else None,
        resolved=Resolved.model_validate(generation.resolved) if generation.resolved else None,
        params=generation.params or {},
        references=list(generation.reference_ids or []),
        start_frame=generation.start_frame_id,
        end_frame=generation.end_frame_id,
        seed=generation.seed,
        status=generation.status,  # type: ignore[arg-type]
        error_code=generation.error_code,
        error_message=generation.error_message,
        job_id=generation.job_id,
        parent_asset_id=generation.parent_asset_id,
        project_id=generation.project_id,
        folder_id=generation.folder_id,
        attach_element_id=generation.attach_element_id,
        shot_id=generation.shot_id,
        shot_role=generation.shot_role,  # type: ignore[arg-type]
        asset=asset_out(asset, generation) if asset else None,
        created_at=generation.created_at,
        started_at=generation.started_at,
        finished_at=generation.finished_at,
    )


def feed_query(
    mode: str | None = None,
    project_id: str | None = None,
    hide_failed: bool = False,
    models: list[str] | None = None,
) -> Any:
    """Generations for a feed. Finished ones whose results are all in the
    trash are left out, as the results are."""
    has_live_asset = exists().where(
        Asset.source_generation_id == Generation.id, Asset.deleted_at.is_(None)
    )
    query = select(Generation).where(
        not_(and_(Generation.status == "succeeded", not_(has_live_asset)))
    )
    if mode is not None:
        query = query.where(Generation.mode == mode)
    if project_id == "none":
        query = query.where(Generation.project_id.is_(None))
    elif project_id:
        query = query.where(Generation.project_id == project_id)
    if hide_failed:
        query = query.where(Generation.status.not_in(("failed", "cancelled", "interrupted")))
    if models:
        query = query.where(Generation.model_id.in_(models))
    return query.order_by(Generation.created_at.desc(), Generation.batch_index.asc())


class UnknownModelError(ValueError):
    pass


def availability(services: Services, spec: Any) -> tuple[bool, str | None]:
    """Whether a catalog model can run right now, and why not."""
    if spec.provider in services.providers:
        return True, None
    if spec.runtime == "api":
        return False, f"Connect {spec.provider} in Settings › Connections."
    return False, "Install it from Settings › Models."


def _sync_generation(services: Services, job: JobSnapshot) -> None:
    """Mirror a generation job's state onto its generation rows."""
    status = _STATUS_FROM_JOB.get(job.state)
    if status is None:
        return
    with services.db.session() as session:
        generations = session.scalars(select(Generation).where(Generation.job_id == job.id)).all()
        outs = []
        for generation in generations:
            generation.status = status
            if status == "queued":
                generation.error_code = None
                generation.error_message = None
                generation.started_at = None
                generation.finished_at = None
            elif status == "running" and generation.started_at is None:
                generation.started_at = utcnow()
            elif status in {"succeeded", "failed", "cancelled", "interrupted"}:
                generation.finished_at = utcnow()
                generation.error_code = job.error_code if status != "succeeded" else None
                generation.error_message = job.error_message if status != "succeeded" else None
            session.flush()
            outs.append(generation_out(session, generation))
    for out in outs:
        services.events.publish("generation.updated", out.model_dump(mode="json"))


class _ProviderJob(JobKind):
    #: Base delay before retrying a transient provider error (doubles each time).
    retry_delay: ClassVar[float] = 1.0

    def __init__(self, providers: dict[str, GenerationProvider]) -> None:
        self.providers = providers

    def title(self, payload: dict[str, Any]) -> str:
        return str(payload.get("title") or "Generation")

    def on_change(self, services: Services, job: JobSnapshot) -> None:
        _sync_generation(services, job)

    async def _prepare(self, ctx: JobContext) -> tuple[GenerationRequest, GenerationProvider]:
        request, provider_id = await asyncio.to_thread(
            _load_request, ctx.services, ctx.payload["generation_id"]
        )
        provider = self.providers.get(provider_id)
        if provider is None:
            raise JobError(
                "unknown_provider",
                f"Provider '{provider_id}' is not connected. Check Settings › Connections.",
            )
        return request, provider

    async def _store(self, ctx: JobContext, outputs: list[GenerationOutput]) -> dict[str, Any]:
        if not outputs:
            raise JobError("no_output", "The provider finished without returning a file.")
        ctx.progress(0.95, "Saving")
        asset_ids: list[str] = []
        try:
            for output in outputs:
                asset_ids.append(
                    await asyncio.to_thread(
                        _store_output, ctx.services, ctx.payload["generation_id"], output
                    )
                )
        finally:
            for output in outputs:
                output.path.unlink(missing_ok=True)
        for asset_id in asset_ids:
            await describe_if_wanted(ctx.services, asset_id)
        return {"asset_id": asset_ids[0], "asset_ids": asset_ids}


class GenerateJob(_ProviderJob):
    """A generation on this machine (GPU lane)."""

    kind: ClassVar[str] = "generate"
    lane = "gpu"

    async def run(self, ctx: JobContext) -> dict[str, Any]:
        request, provider = await self._prepare(ctx)
        if not isinstance(provider, LocalProvider):
            raise JobError("wrong_provider", f"Provider '{provider.id}' does not run locally.")
        try:
            outputs = await with_retries(
                lambda: provider.generate(request, ctx), base_delay=self.retry_delay
            )
        except ProviderError as exc:
            raise exc.to_job_error() from exc
        return await self._store(ctx, outputs)


async def _reserve(
    ctx: JobContext, provider: GenerationProvider, request: GenerationRequest
) -> str:
    """Check the cap and record the estimated spend before a paid request."""
    services = ctx.services
    spec = services.catalog.get(request.model)
    cost = estimate_cost(spec, request.mode, request.params) if spec else None
    try:
        return await asyncio.to_thread(
            services.usage.reserve,
            provider.id,
            cost,
            model=request.model,
            images=1 if request.mode == "image" else 0,
            video_seconds=float(request.params.get("duration", 0))
            if request.mode == "video"
            else 0.0,
            generation_id=ctx.payload["generation_id"],
        )
    except ProviderError as exc:
        raise exc.to_job_error() from exc


#: Refusals that happen before the provider does any work, so trying again
#: cannot pay twice.
_REFUSED_BEFORE_WORK = {429, 503}
_NOT_CHARGED = {"auth", "quota", "invalid_params", "unavailable", "not_found", "content_policy"}


class GenerateHostedJob(_ProviderJob):
    """A generation by a paid hosted model that answers in one call (network
    lane). The spend is reserved first; a request the provider refuses
    outright costs nothing and voids the reservation."""

    kind: ClassVar[str] = "generate.hosted"
    lane = "network"
    attempts: ClassVar[int] = 3

    async def run(self, ctx: JobContext) -> dict[str, Any]:
        request, provider = await self._prepare(ctx)
        if not (provider.remote and isinstance(provider, HostedProvider)):
            raise JobError("wrong_provider", f"Provider '{provider.id}' is not a hosted model.")
        usage_id = await _reserve(ctx, provider, request)
        for attempt in range(1, self.attempts + 1):
            try:
                outputs = await provider.generate(request, ctx)
                break
            except ProviderError as exc:
                retry = exc.status in _REFUSED_BEFORE_WORK and attempt < self.attempts
                if not retry:
                    # Refused outright: nothing to pay. A timeout or a dropped
                    # connection may have been charged, so its reservation stays.
                    refused = exc.status is not None or exc.kind in _NOT_CHARGED
                    if refused:
                        await asyncio.to_thread(ctx.services.usage.void, usage_id)
                    raise exc.to_job_error() from exc
                ctx.progress(0.05, "The provider is busy; trying again")
                await asyncio.sleep(exc.retry_after or self.retry_delay * 2 ** (attempt - 1))
        return await self._store(ctx, outputs)


class GenerateRemoteJob(_ProviderJob):
    """A generation run as a paid task by a hosted provider (network lane).

    ``submit`` runs once: its task ID is stored before the first poll, so a
    restart resumes polling. Status and collect retry transient errors; while
    the provider stays unreachable the job keeps waiting (the task may still be
    running, or finished and paid for) until ``max_poll_misses`` polls in a row
    fail.
    """

    kind: ClassVar[str] = "generate.remote"
    lane = "network"
    remote = True
    poll_interval: ClassVar[float] = 2.0
    max_poll_misses: ClassVar[int] = 120

    def __init__(self, providers: dict[str, GenerationProvider]) -> None:
        super().__init__(providers)
        self._misses: dict[str, int] = {}

    async def _remote(self, ctx: JobContext) -> tuple[GenerationRequest, RemoteProvider]:
        request, provider = await self._prepare(ctx)
        if not isinstance(provider, RemoteProvider):
            raise JobError("wrong_provider", f"Provider '{provider.id}' is not a hosted service.")
        return request, provider

    async def submit(self, ctx: JobContext) -> str:
        request, provider = await self._remote(ctx)
        services = ctx.services
        spec = services.catalog.get(request.model)
        cost = estimate_cost(spec, request.mode, request.params) if spec else None
        try:
            # Checked again here: the job may have waited while other spending landed.
            usage_id = await asyncio.to_thread(
                services.usage.reserve,
                provider.id,
                cost,
                model=request.model,
                images=1 if request.mode == "image" else 0,
                video_seconds=float(request.params.get("duration", 0))
                if request.mode == "video"
                else 0.0,
                generation_id=ctx.payload["generation_id"],
            )
        except ProviderError as exc:
            raise exc.to_job_error() from exc
        ctx.progress(0.0, "Sending to the provider")
        try:
            return await provider.submit(request, ctx)  # never retried: it may be paid
        except ProviderError as exc:
            if not exc.transient:  # refused outright: nothing to pay for
                await asyncio.to_thread(services.usage.void, usage_id)
            raise exc.to_job_error() from exc

    async def poll(self, ctx: JobContext, task_id: str) -> PollResult:
        generation_id = ctx.payload["generation_id"]
        stored = await asyncio.to_thread(_stored_asset_ids, ctx.services, generation_id)
        if stored:  # collected before a restart: do not download (or ingest) twice
            return PollResult("succeeded", result={"asset_id": stored[0], "asset_ids": stored})
        request, provider = await self._remote(ctx)
        try:
            status = await with_retries(
                lambda: provider.status(task_id, request, ctx), base_delay=self.retry_delay
            )
        except ProviderError as exc:
            return self._missed(ctx, exc, "Waiting for the provider to answer")
        if status.state != "succeeded":
            self._misses.pop(ctx.job_id, None)
        if status.state in {"queued", "running"}:
            label = status.message or (
                "Queued at the provider" if status.state == "queued" else "Rendering remotely"
            )
            return PollResult("running", progress=0.9 * (status.progress or 0.0), message=label)
        if status.state == "cancelled":
            return PollResult(
                "failed", error=JobError("provider_cancelled", "The provider cancelled this task.")
            )
        if status.state == "failed":
            error = status.error or ProviderError("unknown", provider=provider.id)
            return PollResult("failed", error=error.to_job_error())
        ctx.progress(0.92, "Downloading")
        try:
            outputs = await with_retries(
                lambda: provider.collect(task_id, request, ctx), base_delay=self.retry_delay
            )
        except ProviderError as exc:
            # The task is finished and paid for: keep trying to download it
            # rather than fail and invite a Retry that would pay again.
            return self._missed(ctx, exc, "Waiting to download the result")
        self._misses.pop(ctx.job_id, None)
        try:
            result = await self._store(ctx, outputs)
        except JobError as exc:
            return PollResult("failed", error=exc)
        return PollResult("succeeded", result=result)

    def _missed(self, ctx: JobContext, exc: ProviderError, waiting: str) -> PollResult:
        """A poll that got no answer: wait on transient errors, up to a limit."""
        if not exc.transient:
            self._misses.pop(ctx.job_id, None)
            return PollResult("failed", error=exc.to_job_error())
        misses = self._misses.get(ctx.job_id, 0) + 1
        self._misses[ctx.job_id] = misses
        if misses >= self.max_poll_misses:
            self._misses.pop(ctx.job_id, None)
            return PollResult("failed", error=exc.to_job_error())
        return PollResult("running", message=waiting)

    async def cancel_remote(self, ctx: JobContext, task_id: str) -> None:
        self._misses.pop(ctx.job_id, None)
        request, provider = await self._remote(ctx)
        try:
            await with_retries(
                lambda: provider.cancel(task_id, request, ctx), base_delay=self.retry_delay
            )
        except ProviderError as exc:
            if exc.kind != "not_found":
                raise


def _load_request(services: Services, generation_id: str) -> tuple[GenerationRequest, str]:
    with services.db.session() as session:
        generation = session.get(Generation, generation_id)
        if generation is None:
            raise JobError("not_found", "The generation was deleted.")
        spec = services.catalog.get(generation.model_id)
        resolved = generation.resolved or {}
        reference_ids = (
            [r["asset_id"] for r in resolved["references"]]
            if "references" in resolved
            else list(generation.reference_ids or [])
        )
        references = []
        for asset_id in reference_ids:
            asset = session.get(Asset, asset_id)
            path = services.media.resolve(asset.path) if asset else None
            if path is None or not path.is_file():
                raise JobError(
                    "reference_missing",
                    "A reference image is no longer in the library. Pick the references again.",
                )
            references.append(path)

        def frame(asset_id: str | None) -> Path | None:
            if asset_id is None:
                return None
            asset = session.get(Asset, asset_id)
            path = services.media.resolve(asset.path) if asset else None
            if path is None or not path.is_file():
                raise JobError(
                    "reference_missing",
                    "A start or end frame is no longer in the library. Pick it again.",
                )
            return path

        request = GenerationRequest(
            mode=generation.mode,  # type: ignore[arg-type]
            prompt=resolved.get("prompt", generation.prompt),
            negative_prompt=resolved.get("negative_prompt"),
            seed=generation.seed if generation.seed is not None else 0,
            params=dict(generation.params or {}),
            model=generation.model_id,
            provider_model=spec.provider_model if spec else None,
            references=tuple(references),
            start_frame=frame(generation.start_frame_id),
            end_frame=frame(generation.end_frame_id),
        )
        return request, generation.provider


def _stored_asset_ids(services: Services, generation_id: str) -> list[str]:
    with services.db.session() as session:
        return list(
            session.scalars(
                select(Asset.id)
                .where(Asset.source_generation_id == generation_id)
                .order_by(Asset.created_at.asc())
            ).all()
        )


def _store_output(services: Services, generation_id: str, output: GenerationOutput) -> str:
    with services.db.session() as session:
        generation = session.get(Generation, generation_id)
        placement = Placement(
            project_id=generation.project_id if generation else None,
            folder_id=generation.folder_id if generation else None,
        )
    try:
        asset, _ = ingest_file(
            services,
            output.path,
            name=None,
            provenance="generated",
            placement=placement,
            source_generation_id=generation_id,
            dedupe=False,
        )
    except MediaError as exc:
        raise JobError(
            "bad_output", f"The engine produced an unreadable file: {exc.message}"
        ) from exc
    _attach_to_element(services, generation_id, asset.id)
    return asset.id


def _attach_to_element(services: Services, generation_id: str, asset_id: str) -> None:
    """Add a result to the element its generation was made for (M4.6)."""
    with services.db.session() as session:
        generation = session.get(Generation, generation_id)
        if generation is None or generation.attach_element_id is None:
            return
        element_id = generation.attach_element_id
        # Several views finish at once: bump the version in one statement
        # first, which also takes SQLite's write lock, so the position below
        # is read inside it.
        bumped = session.execute(
            update(Element)
            .where(Element.id == element_id)
            .values(version=Element.version + 1, updated_at=utcnow())
        )
        if not bumped.rowcount:  # type: ignore[attr-defined]
            return
        last = session.scalar(
            select(func.max(ElementMedia.position)).where(ElementMedia.element_id == element_id)
        )
        session.add(
            ElementMedia(
                element_id=element_id,
                asset_id=asset_id,
                role=generation.attach_role or "sheet",
                position=0 if last is None else last + 1,
            )
        )
    services.events.publish("library.changed", {"scope": "elements", "id": element_id})


class GenerationEstimate(BaseModel):
    """What Generate would do with these settings, before pressing it."""

    #: Whether the request would be accepted now.
    ok: bool
    #: Why not, in words for the composer.
    problems: list[str]
    #: The model that would run (what Auto picked, when it was asked for).
    model: str | None = None
    #: Estimated US dollars for the whole batch (hosted models).
    cost_usd: float | None
    #: Estimated seconds until the results are in.
    seconds: float | None
    notes: str | None


async def estimate_generations(
    services: Services, request: GenerationCreate, providers: dict[str, GenerationProvider]
) -> GenerationEstimate:
    def refused(*problems: str) -> GenerationEstimate:
        return GenerationEstimate(
            ok=False, problems=list(problems), cost_usd=None, seconds=None, notes=None
        )

    try:
        request = resolve_auto(services, request)
    except UnknownModelError as exc:
        return refused(str(exc))
    spec = services.catalog.get(request.model)
    if spec is None:
        return refused(f"Unknown model '{request.model}'.")
    provider = providers.get(spec.provider)
    if provider is None:
        return refused(f"{spec.name} is not available: {availability(services, spec)[1]}")
    problems: list[str] = []
    if request.batch > spec.limits.max_batch:
        problems.append(f"{spec.name} makes at most {spec.limits.max_batch} at a time")
    try:
        params = validate_params(spec, request.mode, request.params)
    except ParamError as exc:
        problems.extend(exc.problems)
        params = {}
    problems.extend(await asyncio.to_thread(check_references, services, spec, request.references))
    problems.extend(await asyncio.to_thread(check_frames, services, spec, request))
    if problems:
        return refused(*problems)
    probe = GenerationRequest(
        mode=request.mode,
        prompt=request.prompt,
        seed=request.seed or 0,
        params=params,
        model=spec.id,
        provider_model=spec.provider_model,
    )
    estimate = await provider.estimate(spec, probe, request.batch)
    if spec.runtime == "api":
        try:
            await asyncio.to_thread(services.usage.check_cap, spec.provider, estimate.cost_usd)
        except ProviderError as exc:
            problems.append(exc.message)
    return GenerationEstimate(
        ok=not problems,
        problems=problems,
        model=spec.id,
        cost_usd=estimate.cost_usd,
        seconds=estimate.seconds,
        notes=estimate.notes,
    )


def check_references(services: Services, spec: Any, asset_ids: list[str]) -> list[str]:
    """Why these references cannot go to ``spec``, or an empty list."""
    limits = spec.limits
    if not asset_ids:
        return []
    if limits.max_references == 0:
        return [f"{spec.name} does not take reference images"]
    problems = []
    if len(asset_ids) > limits.max_references:
        problems.append(f"{spec.name} takes at most {limits.max_references} references")
    if len(set(asset_ids)) != len(asset_ids):
        problems.append("The same reference is in the list twice")
    with services.db.session() as session:
        for asset_id in asset_ids:
            asset = session.get(Asset, asset_id)
            if asset is None or asset.deleted_at is not None:
                problems.append("A reference is not in the library (or is in the trash)")
            elif asset.kind not in limits.reference_kinds:
                kinds = ", ".join(limits.reference_kinds) or "nothing"
                problems.append(f"{spec.name} takes {kinds} references, not {asset.kind}")
    return list(dict.fromkeys(problems))


def resolve_auto(services: Services, request: GenerationCreate) -> GenerationCreate:
    """Replace the model "auto" with the model the router picks for these inputs."""
    if request.model != AUTO:
        return request
    result = route(
        services,
        RouteRequest(
            mode=request.mode,
            references=len(request.references),
            start_frame=request.start_frame is not None,
            end_frame=request.end_frame is not None,
        ),
    )
    if result.model is None:
        raise UnknownModelError(result.reason)
    return request.model_copy(update={"model": result.model})


def check_frames(services: Services, spec: Any, request: GenerationCreate) -> list[str]:
    """Why the start and end frames cannot go to ``spec``, or an empty list."""
    if request.start_frame is None and request.end_frame is None:
        return []
    problems = []
    if request.mode != "video" or "image_to_video" not in spec.capabilities:
        problems.append(f"{spec.name} does not start a clip from an image")
    if request.end_frame is not None:
        if request.start_frame is None:
            problems.append("An end frame needs a start frame")
        elif "start_end_frames" not in spec.capabilities:
            problems.append(f"{spec.name} does not take an end frame")
    if request.references:
        problems.append("Use either start and end frames or reference images, not both")
    with services.db.session() as session:
        for asset_id in (request.start_frame, request.end_frame):
            if asset_id is None:
                continue
            asset = session.get(Asset, asset_id)
            if asset is None or asset.deleted_at is not None:
                problems.append("A frame is not in the library (or is in the trash)")
            elif asset.kind != "image":
                problems.append("Start and end frames must be images")
    return list(dict.fromkeys(problems))


def tokens_for(
    services: Services, request: GenerationCreate, project_id: str | None
) -> tuple[list[Token], str]:
    """The request's prompt as canonical tokens for ``project_id``, and its text."""
    with services.db.session() as session:
        vocabulary = prompts.load_vocabulary(session, project_id)
    if request.prompt_tokens is not None:
        tokens = prompts.normalize(request.prompt_tokens, vocabulary)
    else:
        tokens = prompts.parse(request.prompt, vocabulary)
    return tokens, prompts.render(tokens)


def resolve_for(
    services: Services, spec: Any, request: GenerationCreate, tokens: list[Token]
) -> Resolved:
    """What ``spec`` is given for this request (M4.4, M5.1)."""
    from framestudio.cinema.adherence import ignored_controls

    frames = request.start_frame is not None or request.end_frame is not None
    ignored = ignored_controls(services, spec.id)
    with services.db.session() as session:
        return resolve(
            session,
            spec,
            tokens,
            list(request.references),
            frames=frames,
            element_pictures=request.element_pictures,
            mode=request.mode,
            shot=request.shot,
            ignored=ignored,
        )


async def preview_generation(services: Services, request: GenerationCreate) -> Resolved:
    """What the model would be given, without starting anything."""
    request = resolve_auto(services, request)  # UnknownModelError when nothing fits
    spec = services.catalog.get(request.model)
    if spec is None:
        raise UnknownModelError(f"Unknown model '{request.model}'.")

    def place() -> Placement:
        with services.db.session() as session:
            return resolve_placement(session, request.project_id, request.folder_id)

    placement = await asyncio.to_thread(place)
    tokens, _ = await asyncio.to_thread(tokens_for, services, request, placement.project_id)
    return await asyncio.to_thread(resolve_for, services, spec, request, tokens)


def job_kind_for(provider: GenerationProvider) -> str:
    if not provider.remote:
        return GenerateJob.kind
    return (
        GenerateRemoteJob.kind if isinstance(provider, RemoteProvider) else GenerateHostedJob.kind
    )


async def create_generations(
    services: Services,
    request: GenerationCreate,
    providers: dict[str, GenerationProvider],
) -> list[GenerationOut]:
    request = resolve_auto(services, request)  # UnknownModelError when nothing fits
    spec = services.catalog.get(request.model)
    if spec is None:
        raise UnknownModelError(f"Unknown model '{request.model}'.")
    if spec.provider not in providers:
        raise UnknownModelError(f"{spec.name} is not available: {availability(services, spec)[1]}")
    if request.batch > spec.limits.max_batch:
        raise ParamError([f"{spec.name} makes at most {spec.limits.max_batch} at a time"])
    params = validate_params(spec, request.mode, request.params)  # ParamError: refused
    problems = await asyncio.to_thread(check_references, services, spec, request.references)
    problems += await asyncio.to_thread(check_frames, services, spec, request)
    if problems:
        raise ParamError(problems)
    kind = job_kind_for(providers[spec.provider])
    if spec.runtime == "api":
        # ProviderError (code spend_cap) when the batch would pass the monthly cap.
        estimate = estimate_cost(spec, request.mode, params, request.batch)
        await asyncio.to_thread(services.usage.check_cap, spec.provider, estimate)

    def place() -> Placement:
        with services.db.session() as session:
            return resolve_placement(session, request.project_id, request.folder_id)

    placement = await asyncio.to_thread(place)  # LookupError when the target is gone
    if request.attach_element_id is not None:

        def element_exists() -> bool:
            with services.db.session() as session:
                return session.get(Element, request.attach_element_id) is not None

        if not await asyncio.to_thread(element_exists):
            raise LookupError("element")
    if request.shot_id is not None:

        def shot_exists() -> bool:
            with services.db.session() as session:
                return session.get(Shot, request.shot_id) is not None

        if not await asyncio.to_thread(shot_exists):
            raise LookupError("shot")
    tokens, prompt_text = await asyncio.to_thread(
        tokens_for, services, request, placement.project_id
    )
    if len(prompt_text) > 4000:
        raise ParamError(["The prompt is longer than 4000 characters"])
    request = request.model_copy(update={"prompt": prompt_text})
    resolved = await asyncio.to_thread(resolve_for, services, spec, request, tokens)
    if resolved.problems:
        raise ParamError(resolved.problems)
    if resolved.native:  # parameters the shot sets (the request's own win)
        params = validate_params(spec, request.mode, {**resolved.native, **request.params})
    resolved_json = resolved.model_dump(mode="json")
    shot_json = (
        request.shot.model_dump(mode="json", exclude_defaults=True)
        if request.shot and not request.shot.is_empty()
        else None
    )
    stored = [t.model_dump(mode="json") for t in tokens]
    used = prompts.element_ids(tokens)
    base_seed = request.seed if request.seed is not None else secrets.randbelow(MAX_SEED)
    batch_id = uuid.uuid4().hex
    created: list[str] = []
    snippet = request.prompt.strip().replace("\n", " ")
    snippet = (snippet[:48] + "…") if len(snippet) > 48 else snippet

    for index in range(request.batch):
        seed = (base_seed + index) % (MAX_SEED + 1)
        title = f"{request.mode.capitalize()} · {spec.name} · {snippet or 'no prompt'}"

        def add_generation(
            session: Session, job: Job, seed: int = seed, index: int = index
        ) -> None:
            generation = Generation(
                batch_id=batch_id,
                batch_index=index,
                mode=request.mode,
                model_id=spec.id,
                provider=spec.provider,
                prompt=request.prompt,
                prompt_tokens=stored,
                resolved=resolved_json,
                shot=shot_json,
                params=params,
                reference_ids=list(request.references),
                start_frame_id=request.start_frame,
                end_frame_id=request.end_frame,
                seed=seed,
                status="queued",
                progress=0.0,
                job_id=job.id,
                parent_asset_id=request.parent_asset_id,
                project_id=placement.project_id,
                folder_id=placement.folder_id,
                attach_element_id=request.attach_element_id,
                attach_role=request.attach_role if request.attach_element_id else None,
                shot_id=request.shot_id,
                shot_role=(request.shot_role or "clip") if request.shot_id else None,
            )
            session.add(generation)
            session.flush()
            for element_id in used:
                session.add(ElementUse(element_id=element_id, generation_id=generation.id))
            job.payload = {"generation_id": generation.id, "title": job.title}
            created.append(generation.id)

        await services.jobs.submit(
            kind, {"title": title}, title=title, before_commit=add_generation
        )

    def load() -> list[GenerationOut]:
        with services.db.session() as session:
            return [
                generation_out(session, g)
                for g in (session.get(Generation, gid) for gid in created)
                if g is not None
            ]

    return await asyncio.to_thread(load)
