"""Generations: request → job → asset, with lineage (plan step M0.5)."""

from __future__ import annotations

import asyncio
import secrets
import uuid
from datetime import datetime
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, exists, not_, select
from sqlalchemy.orm import Session

from framestudio.db.base import utcnow
from framestudio.db.models import Asset, Generation, Job
from framestudio.jobs.base import JobContext, JobError, JobKind, JobSnapshot
from framestudio.library.assets import (
    AssetOut,
    Placement,
    asset_out,
    ingest_file,
    resolve_placement,
)
from framestudio.library.media import MediaError
from framestudio.providers.base import GenerationOutput, GenerationProvider, GenerationRequest
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


class GenerationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["image", "video"] = "image"
    model: str = "mock"
    prompt: str = Field(default="", max_length=4000)
    seed: int | None = Field(default=None, ge=0, le=MAX_SEED)
    batch: int = Field(default=1, ge=1, le=4)
    params: dict[str, Any] = Field(default_factory=dict)
    parent_asset_id: str | None = None
    #: Where results land: a project, a folder (which implies its project), or
    #: the global library when both are omitted.
    project_id: str | None = None
    folder_id: str | None = None


class GenerationOut(BaseModel):
    id: str
    batch_id: str
    batch_index: int
    mode: Literal["image", "video"]
    model_id: str
    provider: str
    prompt: str
    params: dict[str, Any]
    seed: int | None
    status: GenerationStatus
    error_code: str | None
    error_message: str | None
    job_id: str | None
    parent_asset_id: str | None
    project_id: str | None
    folder_id: str | None
    asset: AssetOut | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


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
        params=generation.params or {},
        seed=generation.seed,
        status=generation.status,  # type: ignore[arg-type]
        error_code=generation.error_code,
        error_message=generation.error_message,
        job_id=generation.job_id,
        parent_asset_id=generation.parent_asset_id,
        project_id=generation.project_id,
        folder_id=generation.folder_id,
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


class GenerateJob(JobKind):
    kind: ClassVar[str] = "generate"
    lane = "gpu"

    def __init__(self, providers: dict[str, GenerationProvider]) -> None:
        self.providers = providers

    def title(self, payload: dict[str, Any]) -> str:
        return str(payload.get("title") or "Generation")

    async def run(self, ctx: JobContext) -> dict[str, Any]:
        generation_id = ctx.payload["generation_id"]
        services = ctx.services
        request, provider_id = await asyncio.to_thread(_load_request, services, generation_id)
        provider = self.providers.get(provider_id)
        if provider is None:
            raise JobError("unknown_provider", f"Provider '{provider_id}' is not available.")
        output = await provider.generate(request, ctx)
        ctx.progress(0.95, "Saving")
        try:
            asset_id = await asyncio.to_thread(_store_output, services, generation_id, output)
        finally:
            output.path.unlink(missing_ok=True)
        return {"asset_id": asset_id}

    def on_change(self, services: Services, job: JobSnapshot) -> None:
        status = _STATUS_FROM_JOB.get(job.state)
        if status is None:
            return
        with services.db.session() as session:
            generations = session.scalars(
                select(Generation).where(Generation.job_id == job.id)
            ).all()
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


def _load_request(services: Services, generation_id: str) -> tuple[GenerationRequest, str]:
    with services.db.session() as session:
        generation = session.get(Generation, generation_id)
        if generation is None:
            raise JobError("not_found", "The generation was deleted.")
        request = GenerationRequest(
            mode=generation.mode,  # type: ignore[arg-type]
            prompt=generation.prompt,
            seed=generation.seed if generation.seed is not None else 0,
            params=dict(generation.params or {}),
        )
        return request, generation.provider


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
    return asset.id


async def create_generations(
    services: Services,
    request: GenerationCreate,
    providers: dict[str, GenerationProvider],
) -> list[GenerationOut]:
    if request.model not in providers:
        raise UnknownModelError(f"Unknown model '{request.model}'.")

    def place() -> Placement:
        with services.db.session() as session:
            return resolve_placement(session, request.project_id, request.folder_id)

    placement = await asyncio.to_thread(place)  # LookupError when the target is gone
    base_seed = request.seed if request.seed is not None else secrets.randbelow(MAX_SEED)
    batch_id = uuid.uuid4().hex
    created: list[str] = []
    snippet = request.prompt.strip().replace("\n", " ")
    snippet = (snippet[:48] + "…") if len(snippet) > 48 else snippet

    for index in range(request.batch):
        seed = (base_seed + index) % (MAX_SEED + 1)
        title = f"{request.mode.capitalize()} · {request.model} · {snippet or 'no prompt'}"

        def add_generation(
            session: Session, job: Job, seed: int = seed, index: int = index
        ) -> None:
            generation = Generation(
                batch_id=batch_id,
                batch_index=index,
                mode=request.mode,
                model_id=request.model,
                provider=request.model,
                prompt=request.prompt,
                params=request.params,
                seed=seed,
                status="queued",
                progress=0.0,
                job_id=job.id,
                parent_asset_id=request.parent_asset_id,
                project_id=placement.project_id,
                folder_id=placement.folder_id,
            )
            session.add(generation)
            session.flush()
            job.payload = {"generation_id": generation.id, "title": job.title}
            created.append(generation.id)

        await services.jobs.submit(
            "generate", {"title": title}, title=title, before_commit=add_generation
        )

    def load() -> list[GenerationOut]:
        with services.db.session() as session:
            return [
                generation_out(session, g)
                for g in (session.get(Generation, gid) for gid in created)
                if g is not None
            ]

    return await asyncio.to_thread(load)
