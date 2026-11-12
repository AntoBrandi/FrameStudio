"""M0.3: ordering, lane limits, cancel mid-run, recovery, and restart of a
remote task without resubmitting it."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar

import pytest
from sqlalchemy import select

from framestudio.app import create_app
from framestudio.config import Config
from framestudio.db.base import utcnow
from framestudio.db.models import Job
from framestudio.jobs.base import JobContext, JobKind
from framestudio.jobs.fake import FakeRemoteJob
from framestudio.services import Services

from .conftest import TOKEN, async_wait_until

pytestmark = pytest.mark.anyio


class Recorder(JobKind):
    """Records start/finish order and how many run at once."""

    kind: ClassVar[str] = "test.record"
    lane = "gpu"

    def __init__(self) -> None:
        self.started: list[str] = []
        self.finished: list[str] = []
        self.active = 0
        self.max_active = 0

    async def run(self, ctx: JobContext) -> dict[str, Any]:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.started.append(ctx.payload["name"])
        try:
            await asyncio.sleep(float(ctx.payload.get("seconds", 0.05)))
        finally:
            self.active -= 1
        self.finished.append(ctx.payload["name"])
        return {"name": ctx.payload["name"]}


class NetRecorder(Recorder):
    kind: ClassVar[str] = "test.net"
    lane = "network"


def make_services(
    tmp_path: Path, *kinds: JobKind, limits: dict[str, int] | None = None
) -> Services:
    config = Config(data_dir=tmp_path / "data", token=TOKEN, ui_dist=tmp_path / "ui")
    app = create_app(config, extra_kinds=kinds, lane_limits=limits)
    return app.state.services  # type: ignore[no-any-return]


async def start(services: Services) -> None:
    services.events.bind(asyncio.get_running_loop())
    await services.jobs.start()


async def state_of(services: Services, job_id: str) -> str:
    return (await services.jobs.get(job_id)).state


async def test_gpu_lane_is_serial_and_fifo(tmp_path: Path) -> None:
    recorder = Recorder()
    services = make_services(tmp_path, recorder)
    await start(services)
    try:
        for name in "abcde":
            await services.jobs.submit("test.record", {"name": name})
        await services.jobs.wait_idle()
        assert recorder.started == list("abcde")
        assert recorder.max_active == 1
    finally:
        await services.jobs.stop()


async def test_network_lane_runs_concurrently_up_to_its_limit(tmp_path: Path) -> None:
    recorder = NetRecorder()
    services = make_services(tmp_path, recorder, limits={"gpu": 1, "network": 3})
    await start(services)
    try:
        for index in range(7):
            await services.jobs.submit("test.net", {"name": str(index), "seconds": 0.2})
        await services.jobs.wait_idle()
        assert recorder.max_active == 3
        assert sorted(recorder.finished) == [str(i) for i in range(7)]
    finally:
        await services.jobs.stop()


async def test_lanes_are_independent(tmp_path: Path) -> None:
    gpu, net = Recorder(), NetRecorder()
    services = make_services(tmp_path, gpu, net)
    await start(services)
    try:
        await services.jobs.submit("test.record", {"name": "slow", "seconds": 1.0})
        quick = await services.jobs.submit("test.net", {"name": "quick", "seconds": 0.01})
        await async_wait_until(
            lambda: _is(services, quick.id, "succeeded"), limit=0.8
        )  # finished while the GPU lane is still busy
        assert gpu.finished == []
    finally:
        await services.jobs.stop()


async def _is(services: Services, job_id: str, state: str) -> bool:
    return await state_of(services, job_id) == state


async def test_progress_events_and_success(tmp_path: Path) -> None:
    services = make_services(tmp_path)
    await start(services)
    seen: list[str] = []

    async def collect() -> None:
        async for event in services.events.subscribe(heartbeat=5):
            if event is None:
                continue
            seen.append(event.type)
            if event.type == "job.updated" and event.data["state"] == "succeeded":
                return

    collector = asyncio.create_task(collect())
    await asyncio.sleep(0)
    try:
        job = await services.jobs.submit("fake.sleep", {"seconds": 0.5})
        await asyncio.wait_for(collector, timeout=5)
        final = await services.jobs.get(job.id)
        assert final.state == "succeeded"
        assert final.progress == 1.0
        assert final.attempts == 1
        assert "job.progress" in seen
    finally:
        await services.jobs.stop()


async def test_cancel_queued_and_running(tmp_path: Path) -> None:
    services = make_services(tmp_path)
    await start(services)
    try:
        running = await services.jobs.submit("fake.sleep", {"seconds": 10})
        queued = await services.jobs.submit("fake.sleep", {"seconds": 10})
        await async_wait_until(lambda: _is(services, running.id, "running"))
        assert (await services.jobs.cancel(queued.id)).state == "cancelled"
        result = await services.jobs.cancel(running.id)
        assert result.state == "cancelled"
        assert not services.jobs.is_running(running.id)
        # Cancelling again is harmless.
        assert (await services.jobs.cancel(running.id)).state == "cancelled"
    finally:
        await services.jobs.stop()


async def test_failure_then_retry(tmp_path: Path) -> None:
    services = make_services(tmp_path)
    await start(services)
    try:
        job = await services.jobs.submit("fake.sleep", {"seconds": 0.05, "fail": True})
        await async_wait_until(lambda: _is(services, job.id, "failed"))
        failed = await services.jobs.get(job.id)
        assert failed.error_code == "fake_failure"
        retried = await services.jobs.retry(job.id)
        assert retried.state == "queued"
        await async_wait_until(lambda: _is(services, job.id, "failed"))
        assert (await services.jobs.get(job.id)).attempts == 2
    finally:
        await services.jobs.stop()


async def test_retry_refuses_active_jobs(tmp_path: Path) -> None:
    from framestudio.jobs.queue import JobStateError

    services = make_services(tmp_path)
    await start(services)
    try:
        job = await services.jobs.submit("fake.sleep", {"seconds": 5})
        with pytest.raises(JobStateError):
            await services.jobs.retry(job.id)
    finally:
        await services.jobs.stop()


async def test_shutdown_interrupts_local_jobs(tmp_path: Path) -> None:
    services = make_services(tmp_path)
    await start(services)
    job = await services.jobs.submit("fake.sleep", {"seconds": 10})
    await async_wait_until(lambda: _is(services, job.id, "running"))
    await services.jobs.stop()
    assert await state_of(services, job.id) == "interrupted"


def _insert(services: Services, **values: Any) -> str:
    with services.db.session() as session:
        job = Job(
            title="t",
            payload=values.pop("payload", {}),
            progress=0.0,
            attempts=1,
            started_at=utcnow(),
            **values,
        )
        session.add(job)
        session.flush()
        return job.id


async def test_recovery_after_a_crash(tmp_path: Path) -> None:
    """Rows as a crashed process leaves them: local running → interrupted;
    remote mid-submit → interrupted (never resubmitted); remote with a task
    ID → resumed; queued → runs; unknown kind → failed."""
    remote = FakeRemoteJob()
    services = make_services(tmp_path, remote)
    task_id = await remote.submit(_ctx(services, {"seconds": 0.3}))
    remote.submissions.clear()
    local = _insert(services, kind="fake.sleep", lane="gpu", state="running")
    submitting = _insert(services, kind="fake.remote", lane="network", state="submitting")
    resumable = _insert(
        services,
        kind="fake.remote",
        lane="network",
        state="running",
        provider_task_id=task_id,
        payload={"seconds": 0.3},
    )
    queued = _insert(
        services, kind="fake.sleep", lane="gpu", state="queued", payload={"seconds": 0.05}
    )
    unknown = _insert(services, kind="gone.kind", lane="gpu", state="running")

    await start(services)
    try:
        assert await state_of(services, local) == "interrupted"
        assert await state_of(services, submitting) == "interrupted"
        assert await state_of(services, unknown) == "failed"
        await async_wait_until(lambda: _is(services, resumable, "succeeded"))
        await async_wait_until(lambda: _is(services, queued, "succeeded"))
        assert remote.submissions == []  # resumed, not resubmitted
    finally:
        await services.jobs.stop()


async def test_remote_task_resumes_across_restart_without_resubmitting(tmp_path: Path) -> None:
    remote = FakeRemoteJob()
    first = make_services(tmp_path, remote)
    await start(first)
    job = await first.jobs.submit("fake.remote", {"seconds": 1.0})
    await async_wait_until(lambda: _is(first, job.id, "running"))
    await async_wait_until(lambda: _has_task_id(first, job.id))
    await first.jobs.stop()  # the process goes away mid-task
    assert await state_of(first, job.id) == "running"
    assert len(remote.submissions) == 1

    second = make_services(tmp_path, remote)  # same data dir: a restart
    await start(second)
    try:
        await async_wait_until(lambda: _is(second, job.id, "succeeded"), limit=10)
        assert len(remote.submissions) == 1
        finished = await second.jobs.get(job.id)
        assert finished.result is not None
        assert finished.result["task_id"] == remote.submissions[0]
    finally:
        await second.jobs.stop()


async def _has_task_id(services: Services, job_id: str) -> bool:
    def load() -> bool:
        with services.db.session() as session:
            return (
                session.scalars(select(Job.provider_task_id).where(Job.id == job_id)).one()
                is not None
            )

    return await asyncio.to_thread(load)


async def test_cancel_remote_task_cancels_at_provider(tmp_path: Path) -> None:
    remote = FakeRemoteJob()
    services = make_services(tmp_path, remote)
    await start(services)
    try:
        job = await services.jobs.submit("fake.remote", {"seconds": 30})
        await async_wait_until(lambda: _has_task_id(services, job.id))
        result = await services.jobs.cancel(job.id)
        assert result.state == "cancelled"
        assert remote.cancelled == remote.submissions
    finally:
        await services.jobs.stop()


async def test_cancel_during_submit_cancels_the_task_it_created(tmp_path: Path) -> None:
    remote = FakeRemoteJob()
    services = make_services(tmp_path, remote)
    await start(services)
    try:
        job = await services.jobs.submit("fake.remote", {"seconds": 30, "submit_delay": 0.5})
        await async_wait_until(lambda: _is(services, job.id, "submitting"))
        result = await services.jobs.cancel(job.id)
        assert result.state == "cancelled"
        assert len(remote.submissions) == 1
        assert remote.cancelled == remote.submissions
    finally:
        await services.jobs.stop()


def _ctx(services: Services, payload: dict[str, Any]) -> JobContext:
    return JobContext(
        job_id="x", payload=payload, attempts=1, services=services, _report=lambda *a: None
    )
