from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from framestudio.db.models import Job
from framestudio.jobs.base import JobState, Lane


class JobOut(BaseModel):
    id: str
    kind: str
    lane: Lane
    state: JobState
    title: str
    progress: float
    message: str | None
    error_code: str | None
    error_message: str | None
    attempts: int
    remote: bool
    result: dict[str, Any] | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


def job_out(job: Job, live: tuple[float, str | None] | None = None) -> JobOut:
    progress, message = (job.progress, job.message)
    if live is not None and job.state == "running":
        progress, message = live
    return JobOut(
        id=job.id,
        kind=job.kind,
        lane=job.lane,  # type: ignore[arg-type]
        state=job.state,  # type: ignore[arg-type]
        title=job.title,
        progress=progress,
        message=message,
        error_code=job.error_code,
        error_message=job.error_message,
        attempts=job.attempts,
        remote=job.provider_task_id is not None or job.state == "submitting",
        result=job.result,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )
