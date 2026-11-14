"""Jobs, the event stream, and fake jobs for demos and tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from framestudio.api.deps import ServicesDep
from framestudio.db.models import Job
from framestudio.jobs.base import ACTIVE_STATES
from framestudio.jobs.queue import JobNotFoundError, JobStateError
from framestudio.jobs.schemas import JobOut, job_out

router = APIRouter(prefix="/api", tags=["jobs"])


class FakeJobCreate(BaseModel):
    kind: Literal["local", "remote"] = "local"
    seconds: float = Field(default=10, gt=0, le=3600)
    fail: bool = False


@router.get("/jobs")
def list_jobs(
    services: ServicesDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[JobOut]:
    """Active jobs first, then the most recent finished ones."""
    with services.db.session() as session:
        jobs = session.scalars(
            select(Job)
            .where(or_(Job.state.in_(ACTIVE_STATES), Job.finished_at.is_not(None)))
            .order_by(Job.created_at.desc())
            .limit(limit)
        ).all()
        return [job_out(job, services.jobs.live_progress(job.id)) for job in jobs]


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, services: ServicesDep) -> JobOut:
    try:
        return await services.jobs.get(job_id)
    except JobNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found") from None


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, services: ServicesDep) -> JobOut:
    try:
        return await services.jobs.cancel(job_id)
    except JobNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found") from None


@router.post("/jobs/{job_id}/retry")
async def retry_job(job_id: str, services: ServicesDep) -> JobOut:
    try:
        return await services.jobs.retry(job_id)
    except JobNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found") from None
    except JobStateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None


@router.post("/jobs/fake", status_code=status.HTTP_201_CREATED)
async def create_fake_job(body: FakeJobCreate, services: ServicesDep) -> JobOut:
    """Queue a fake job: ``local`` sleeps on the GPU lane, ``remote`` behaves
    like a paid API task on the network lane (and survives restarts)."""
    kind = "fake.sleep" if body.kind == "local" else "fake.remote"
    return await services.jobs.submit(kind, {"seconds": body.seconds, "fail": body.fail})


@router.get(
    "/events",
    tags=["events"],
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "Event stream"}},
)
async def events(
    request: Request,
    services: ServicesDep,
    last_event_id: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    """Server-Sent Events: ``job.updated``, ``job.progress``,
    ``generation.updated``, ``asset.created``, ``settings.updated``, ``resync``."""

    # Subscribe before the response starts, so nothing published after the
    # client sees the stream open can be missed.
    subscription = services.events.open(last_event_id)

    async def stream() -> AsyncIterator[str]:
        try:
            yield "retry: 2000\n\n"
            async for event in subscription.events():
                if await request.is_disconnected():
                    return
                yield ": keep-alive\n\n" if event is None else event.to_sse()
        finally:
            subscription.close()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
