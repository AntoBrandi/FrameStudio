"""Persistent in-process job queue with a serial GPU lane and a concurrent
network lane (plan step M0.3).

Guarantees:

* Jobs survive restarts. Queued jobs run after a restart; local jobs that were
  running become ``interrupted`` (the user can retry them).
* A remote (paid) task is never submitted twice. Its provider task ID is
  stored before the first poll, and after a restart the job resumes polling.
  A job that crashed between "submit started" and "task ID stored" cannot
  know whether the provider accepted it, so it becomes ``interrupted`` instead
  of being sent again.
* State changes are conditional updates (``WHERE state IN …``), so a cancel
  racing a worker cannot resurrect or double-finish a job.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from collections.abc import Callable, Iterable
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from framestudio.db.base import utcnow
from framestudio.db.models import Job
from framestudio.jobs.base import (
    RETRYABLE_STATES,
    JobContext,
    JobError,
    JobKind,
    JobSnapshot,
)
from framestudio.jobs.schemas import JobOut, job_out
from framestudio.services import Services

log = logging.getLogger(__name__)

DEFAULT_LANE_LIMITS: dict[str, int] = {"gpu": 1, "network": 8, "cpu": 2}
PROGRESS_EVENT_INTERVAL = 0.1


def _snapshot(job: Job) -> JobSnapshot:
    return JobSnapshot(
        id=job.id,
        kind=job.kind,
        lane=job.lane,
        state=job.state,
        payload=dict(job.payload or {}),
        attempts=job.attempts,
        provider_task_id=job.provider_task_id,
        result=job.result,
        error_code=job.error_code,
        error_message=job.error_message,
    )


class JobNotFoundError(LookupError):
    pass


class _Skipped(Exception):
    """The job ended without a result of its own (e.g. cancelled before it ran)."""


class JobStateError(ValueError):
    pass


class JobQueue:
    def __init__(
        self,
        services: Services,
        kinds: Iterable[JobKind],
        lane_limits: dict[str, int] | None = None,
    ) -> None:
        self._services = services
        self._kinds: dict[str, JobKind] = {k.kind: k for k in kinds}
        self._limits = dict(lane_limits or DEFAULT_LANE_LIMITS)
        self._queues: dict[str, asyncio.Queue[str]] = {}
        self._workers: list[asyncio.Task[None]] = []
        self._running: dict[str, asyncio.Task[None]] = {}
        self._live: dict[str, tuple[float, str | None]] = {}
        self._last_emit: dict[str, float] = {}
        self._cancel_requested: set[str] = set()
        self._stopping = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: int | None = None

    # ------------------------------------------------------------------ setup

    def register(self, kind: JobKind) -> None:
        self._kinds[kind.kind] = kind

    def kind(self, name: str) -> JobKind:
        return self._kinds[name]

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._loop_thread = threading.get_ident()
        self._stopping = False
        self._queues = {lane: asyncio.Queue() for lane in self._limits}
        pending = await asyncio.to_thread(self._recover)
        for lane, count in self._limits.items():
            for index in range(count):
                self._workers.append(
                    asyncio.create_task(self._worker(lane), name=f"jobs-{lane}-{index}")
                )
        for job_id, lane in pending:
            self._queues[lane].put_nowait(job_id)

    async def stop(self) -> None:
        """Interrupt local work; leave remote tasks to resume next start."""
        self._stopping = True
        running = list(self._running.values())
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        self._running.clear()

    def _recover(self) -> list[tuple[str, str]]:
        """Fix up jobs left behind by the previous process; return work to queue."""
        pending: list[tuple[str, str]] = []
        changed: list[JobSnapshot] = []
        with self._services.db.session() as session:
            jobs = session.scalars(
                select(Job)
                .where(Job.state.in_(("queued", "submitting", "running")))
                .order_by(Job.created_at)
            ).all()
            for job in jobs:
                kind = self._kinds.get(job.kind)
                if kind is None:
                    job.state = "failed"
                    job.error_code = "unknown_kind"
                    job.error_message = f"No handler for job kind '{job.kind}'."
                    job.finished_at = utcnow()
                    changed.append(_snapshot(job))
                elif job.state == "queued":
                    pending.append((job.id, job.lane))
                elif kind.remote and job.state == "running" and job.provider_task_id:
                    job.message = "Resuming after a restart"
                    pending.append((job.id, job.lane))
                else:
                    job.error_message = (
                        "The app stopped while the request was being sent; the provider may "
                        "or may not have accepted it. Check the provider before retrying."
                        if kind.remote
                        else "Stopped by a restart. Retry to run it again."
                    )
                    job.state = "interrupted"
                    job.error_code = "interrupted"
                    job.finished_at = utcnow()
                    changed.append(_snapshot(job))
        for snap in changed:
            self._after_change(snap)
        return pending

    # ------------------------------------------------------------- public API

    async def submit(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        title: str | None = None,
        before_commit: Callable[[Session, Job], None] | None = None,
    ) -> JobOut:
        """Persist a job (plus anything ``before_commit`` adds in the same
        transaction), then queue it."""
        handler = self._kinds.get(kind)
        if handler is None:
            raise JobStateError(f"Unknown job kind '{kind}'.")

        def create() -> tuple[str, JobOut]:
            with self._services.db.session() as session:
                job = Job(
                    kind=kind,
                    lane=handler.lane,
                    state="queued",
                    title=title or handler.title(payload),
                    payload=payload,
                    progress=0.0,
                    attempts=0,
                )
                session.add(job)
                session.flush()
                if before_commit is not None:
                    before_commit(session, job)
                session.flush()
                snap, out = _snapshot(job), job_out(job)
            self._after_change(snap, out)  # after commit, still off the event loop
            return snap.id, out

        job_id, out = await asyncio.to_thread(create)
        self._queues[handler.lane].put_nowait(job_id)
        return out

    async def cancel(self, job_id: str) -> JobOut:
        snap = await asyncio.to_thread(
            self._update, job_id, ("queued",), state="cancelled", finished_at=utcnow()
        )
        if snap is None:
            task = self._running.get(job_id)
            if task is not None and not task.done():
                self._cancel_requested.add(job_id)
                current = await self.get(job_id)
                if current.state != "submitting":
                    # Mid-submit cancels wait until the task ID is stored, so
                    # the remote task can be cancelled instead of orphaned.
                    task.cancel()
                await asyncio.wait({task}, timeout=60)
        return await self.get(job_id)

    async def retry(self, job_id: str) -> JobOut:
        current = await self.get(job_id)
        if current.state not in RETRYABLE_STATES:
            raise JobStateError(f"A {current.state} job cannot be retried.")
        snap = await asyncio.to_thread(
            self._update,
            job_id,
            tuple(RETRYABLE_STATES),
            state="queued",
            progress=0.0,
            message=None,
            result=None,
            error_code=None,
            error_message=None,
            provider_task_id=None,
            started_at=None,
            finished_at=None,
        )
        if snap is None:
            raise JobStateError("The job changed state; refresh and try again.")
        self._queues[snap.lane].put_nowait(job_id)
        return await self.get(job_id)

    async def get(self, job_id: str) -> JobOut:
        def load() -> JobOut:
            with self._services.db.session() as session:
                job = session.get(Job, job_id)
                if job is None:
                    raise JobNotFoundError(job_id)
                return job_out(job, self._live.get(job_id))

        return await asyncio.to_thread(load)

    def live_progress(self, job_id: str) -> tuple[float, str | None] | None:
        return self._live.get(job_id)

    def is_running(self, job_id: str) -> bool:
        task = self._running.get(job_id)
        return task is not None and not task.done()

    async def wait_idle(self, limit: float = 30.0) -> None:
        """Test helper: wait until every lane is empty and nothing runs."""
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            if not self._running and all(q.empty() for q in self._queues.values()):
                await asyncio.sleep(0.05)
                if not self._running and all(q.empty() for q in self._queues.values()):
                    return
            await asyncio.sleep(0.02)
        raise TimeoutError("job queue did not become idle")

    # --------------------------------------------------------------- workers

    async def _worker(self, lane: str) -> None:
        queue = self._queues[lane]
        while True:
            job_id = await queue.get()
            try:
                snap = await asyncio.to_thread(self._load_runnable, job_id)
                if snap is None or job_id in self._running:
                    continue
                task = asyncio.create_task(self._execute(snap), name=f"job-{job_id}")
                self._running[job_id] = task
                try:
                    await asyncio.wait({task})
                finally:
                    self._running.pop(job_id, None)
                    self._cancel_requested.discard(job_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("job worker error for %s", job_id)

    def _load_runnable(self, job_id: str) -> JobSnapshot | None:
        with self._services.db.session() as session:
            job = session.get(Job, job_id)
            if job is None:
                return None
            kind = self._kinds.get(job.kind)
            if kind is None:
                return None
            if job.state == "queued":
                return _snapshot(job)
            if kind.remote and job.state == "running" and job.provider_task_id:
                return _snapshot(job)
            return None

    async def _execute(self, snap: JobSnapshot) -> None:
        kind = self._kinds[snap.kind]
        ctx = JobContext(
            job_id=snap.id,
            payload=snap.payload,
            attempts=snap.attempts,
            services=self._services,
            _report=self._report_progress,
        )
        task_id = snap.provider_task_id
        try:
            if kind.remote:
                try:
                    result = await self._run_remote(kind, ctx, snap)
                except _Skipped:
                    return
            else:
                started = await asyncio.to_thread(
                    self._update,
                    snap.id,
                    ("queued",),
                    state="running",
                    started_at=utcnow(),
                    attempts=Job.attempts + 1,
                    progress=0.0,
                )
                if started is None:
                    return  # cancelled before it started
                ctx.attempts = started.attempts
                result = await kind.run(ctx)
            await asyncio.to_thread(
                self._update,
                snap.id,
                ("running",),
                state="succeeded",
                progress=1.0,
                result=result or {},
                message=None,
                finished_at=utcnow(),
            )
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if snap.id in self._cancel_requested:
                if current is not None:
                    current.uncancel()
                remote_id = task_id or await asyncio.to_thread(self._task_id, snap.id)
                if kind.remote and remote_id:
                    try:
                        await kind.cancel_remote(ctx, remote_id)
                    except Exception:
                        log.exception("remote cancel failed for job %s", snap.id)
                await asyncio.to_thread(
                    self._update,
                    snap.id,
                    ("queued", "submitting", "running"),
                    state="cancelled",
                    finished_at=utcnow(),
                    message=None,
                )
                return
            # Shutdown: remote tasks with a stored ID resume next start.
            remote_id = await asyncio.to_thread(self._task_id, snap.id)
            if not (kind.remote and remote_id):
                await asyncio.to_thread(
                    self._update,
                    snap.id,
                    ("submitting", "running"),
                    state="interrupted",
                    error_code="interrupted",
                    error_message="Stopped because the app shut down. Retry to run it again.",
                    finished_at=utcnow(),
                )
            raise
        except JobError as exc:
            await asyncio.to_thread(self._fail, snap.id, exc.code, exc.message)
        except Exception as exc:
            log.exception("job %s (%s) failed", snap.id, snap.kind)
            await asyncio.to_thread(self._fail, snap.id, "internal_error", str(exc) or repr(exc))
        finally:
            self._live.pop(snap.id, None)
            self._last_emit.pop(snap.id, None)

    async def _run_remote(
        self, kind: JobKind, ctx: JobContext, snap: JobSnapshot
    ) -> dict[str, Any] | None:
        task_id = snap.provider_task_id
        if task_id is None:
            marked = await asyncio.to_thread(
                self._update,
                snap.id,
                ("queued",),
                state="submitting",
                started_at=utcnow(),
                attempts=Job.attempts + 1,
                progress=0.0,
            )
            if marked is None:
                raise _Skipped  # cancelled while queued
            ctx.attempts = marked.attempts
            task_id = await kind.submit(ctx)
            await asyncio.to_thread(
                self._update,
                snap.id,
                ("submitting",),
                state="running",
                provider_task_id=task_id,
            )
            if snap.id in self._cancel_requested:  # cancel arrived mid-submit
                with contextlib.suppress(Exception):
                    await kind.cancel_remote(ctx, task_id)
                await asyncio.to_thread(
                    self._update,
                    snap.id,
                    ("running",),
                    state="cancelled",
                    finished_at=utcnow(),
                )
                raise _Skipped
        while True:
            outcome = await kind.poll(ctx, task_id)
            if outcome.state == "succeeded":
                return outcome.result
            if outcome.state == "failed":
                raise outcome.error or JobError("remote_failed", "The provider reported a failure.")
            if outcome.progress is not None:
                ctx.progress(outcome.progress, outcome.message)
            await asyncio.sleep(kind.poll_interval)

    # ---------------------------------------------------------------- helpers

    def _task_id(self, job_id: str) -> str | None:
        with self._services.db.session() as session:
            job = session.get(Job, job_id)
            return job.provider_task_id if job else None

    def _fail(self, job_id: str, code: str, message: str) -> None:
        self._update(
            job_id,
            ("submitting", "running"),
            state="failed",
            error_code=code,
            error_message=message,
            finished_at=utcnow(),
        )

    def _update(self, job_id: str, expect: tuple[str, ...], **values: Any) -> JobSnapshot | None:
        """Conditional state change; returns the new snapshot or ``None``."""
        values.setdefault("updated_at", utcnow())
        with self._services.db.session() as session:
            result = session.execute(
                update(Job)
                .where(Job.id == job_id, Job.state.in_(expect))
                .values(**values)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:  # type: ignore[attr-defined]
                return None
            job = session.get(Job, job_id, populate_existing=True)
            assert job is not None
            snap, out = _snapshot(job), job_out(job, self._live.get(job_id))
        self._after_change(snap, out)
        return snap

    def _after_change(self, snap: JobSnapshot, out: JobOut | None = None) -> None:
        if out is None:
            out = self._load_out(snap.id)
        if out is not None:
            self._services.events.publish("job.updated", out.model_dump(mode="json"))
        kind = self._kinds.get(snap.kind)
        if kind is not None:
            try:
                kind.on_change(self._services, snap)
            except Exception:
                log.exception("on_change hook failed for job %s", snap.id)

    def _load_out(self, job_id: str) -> JobOut | None:
        with self._services.db.session() as session:
            job = session.get(Job, job_id)
            return job_out(job) if job else None

    def _report_progress(self, job_id: str, fraction: float, message: str | None) -> None:
        if self._loop is not None and threading.get_ident() != self._loop_thread:
            self._loop.call_soon_threadsafe(self._report_progress, job_id, fraction, message)
            return
        self._live[job_id] = (fraction, message)
        now = time.monotonic()
        if now - self._last_emit.get(job_id, 0.0) < PROGRESS_EVENT_INTERVAL and fraction < 1.0:
            return
        self._last_emit[job_id] = now
        self._services.events.publish(
            "job.progress", {"id": job_id, "progress": fraction, "message": message}
        )
