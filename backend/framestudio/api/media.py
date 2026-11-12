"""Generations and assets."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy import select

from framestudio.api.deps import ServicesDep
from framestudio.db.models import Asset, Generation
from framestudio.generation import (
    AssetOut,
    GenerationCreate,
    GenerationOut,
    UnknownModelError,
    asset_out,
    create_generations,
    generation_out,
)
from framestudio.jobs.queue import JobNotFoundError, JobStateError

router = APIRouter(prefix="/api")


@router.post("/generations", tags=["generations"], status_code=status.HTTP_201_CREATED)
async def create_generation(body: GenerationCreate, services: ServicesDep) -> list[GenerationOut]:
    """Queue one generation per batch item; each lands as an asset."""
    try:
        return await create_generations(services, body, services.providers)
    except UnknownModelError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/generations", tags=["generations"])
def list_generations(
    services: ServicesDep,
    mode: Literal["image", "video"] | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 60,
) -> list[GenerationOut]:
    with services.db.session() as session:
        query = select(Generation).order_by(
            Generation.created_at.desc(), Generation.batch_index.asc()
        )
        if mode is not None:
            query = query.where(Generation.mode == mode)
        return [generation_out(session, g) for g in session.scalars(query.limit(limit)).all()]


@router.get("/generations/{generation_id}", tags=["generations"])
def get_generation(generation_id: str, services: ServicesDep) -> GenerationOut:
    with services.db.session() as session:
        generation = session.get(Generation, generation_id)
        if generation is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Generation not found")
        return generation_out(session, generation)


async def _job_action(generation_id: str, services: ServicesDep, action: str) -> GenerationOut:
    with services.db.session() as session:
        generation = session.get(Generation, generation_id)
        job_id = generation.job_id if generation else None
    if job_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Generation not found")
    try:
        if action == "retry":
            await services.jobs.retry(job_id)
        else:
            await services.jobs.cancel(job_id)
    except JobNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found") from None
    except JobStateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    return get_generation(generation_id, services)


@router.post("/generations/{generation_id}/retry", tags=["generations"])
async def retry_generation(generation_id: str, services: ServicesDep) -> GenerationOut:
    return await _job_action(generation_id, services, "retry")


@router.post("/generations/{generation_id}/cancel", tags=["generations"])
async def cancel_generation(generation_id: str, services: ServicesDep) -> GenerationOut:
    return await _job_action(generation_id, services, "cancel")


@router.get("/assets", tags=["assets"])
def list_assets(
    services: ServicesDep,
    kind: Literal["image", "video", "audio"] | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[AssetOut]:
    with services.db.session() as session:
        query = select(Asset).where(Asset.deleted_at.is_(None)).order_by(Asset.created_at.desc())
        if kind is not None:
            query = query.where(Asset.kind == kind)
        return [asset_out(a) for a in session.scalars(query.limit(limit)).all()]


@router.get("/assets/{asset_id}", tags=["assets"])
def get_asset(asset_id: str, services: ServicesDep) -> AssetOut:
    with services.db.session() as session:
        asset = session.get(Asset, asset_id)
        if asset is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Asset not found")
        return asset_out(asset)


@router.get(
    "/assets/{asset_id}/file",
    tags=["assets"],
    response_class=FileResponse,
    responses={200: {"content": {"application/octet-stream": {}}, "description": "Media file"}},
)
def asset_file(asset_id: str, services: ServicesDep) -> FileResponse:
    """The media file, with HTTP range support for video scrubbing."""
    with services.db.session() as session:
        asset = session.get(Asset, asset_id)
        if asset is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Asset not found")
        rel_path, mime = asset.path, asset.mime
    try:
        path = services.media.resolve(rel_path)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Asset file not found") from None
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Asset file is missing on disk")
    return FileResponse(
        path,
        media_type=mime,
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )
