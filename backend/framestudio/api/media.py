"""Generations."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status

from framestudio.api.deps import ServicesDep
from framestudio.catalog import ParamError
from framestudio.db.models import Generation
from framestudio.generation import (
    GenerationCreate,
    GenerationEstimate,
    GenerationOut,
    UnknownModelError,
    create_generations,
    estimate_generations,
    feed_query,
    generation_out,
)
from framestudio.jobs.queue import JobNotFoundError, JobStateError
from framestudio.providers.base import ProviderError

router = APIRouter(prefix="/api")


@router.post("/generations", tags=["generations"], status_code=status.HTTP_201_CREATED)
async def create_generation(body: GenerationCreate, services: ServicesDep) -> list[GenerationOut]:
    """Queue one generation per batch item; each lands as an asset."""
    try:
        return await create_generations(services, body, services.providers)
    except UnknownModelError as exc:
        raise HTTPException(422, str(exc)) from None
    except ParamError as exc:
        raise HTTPException(422, exc.problems) from None
    except ProviderError as exc:  # a spending cap
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, exc.message) from None
    except LookupError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"{str(exc).capitalize()} not found"
        ) from None


@router.post("/generations/estimate", tags=["generations"])
async def estimate_generation(body: GenerationCreate, services: ServicesDep) -> GenerationEstimate:
    """Cost and time for these settings, and what stands in the way, without
    starting anything. The composer calls it as settings change."""
    return await estimate_generations(services, body, services.providers)


@router.get("/generations", tags=["generations"])
def list_generations(
    services: ServicesDep,
    mode: Literal["image", "video"] | None = None,
    project_id: Annotated[str | None, Query(description="A project id, or 'none'")] = None,
    hide_failed: bool = False,
    model: Annotated[list[str], Query()] = [],  # noqa: B006
    limit: Annotated[int, Query(ge=1, le=200)] = 60,
) -> list[GenerationOut]:
    query = feed_query(mode, project_id, hide_failed, list(model)).limit(limit)
    with services.db.session() as session:
        return [generation_out(session, g) for g in session.scalars(query).all()]


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
