"""M2.2: generations on hosted providers run as paid remote tasks: network
lane, submitted once, resumed after a restart, retried only where safe."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from framestudio.app import create_app
from framestudio.config import Config
from framestudio.db.models import Asset, Generation, Job
from framestudio.generation import (
    GenerateJob,
    GenerateRemoteJob,
    GenerationCreate,
    create_generations,
)
from framestudio.jobs.base import JobContext
from framestudio.providers.base import GenerationRequest, ProviderError, TaskStatus
from framestudio.providers.mock import FAILURE_MESSAGE, MockCloudProvider, MockProvider
from framestudio.services import Services

from .conftest import TOKEN, async_wait_until, wait_until


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(GenerateRemoteJob, "poll_interval", 0.05)
    monkeypatch.setattr(GenerateRemoteJob, "retry_delay", 0.0)
    monkeypatch.setattr(GenerateJob, "retry_delay", 0.0)
    yield


# ---------------------------------------------------------------- through the API


def _settled(client: TestClient, generation_id: str) -> dict[str, Any]:
    def check() -> dict[str, Any] | None:
        body = client.get(f"/api/generations/{generation_id}").json()
        return body if body["status"] in {"succeeded", "failed", "cancelled"} else None

    return wait_until(check, timeout=30)  # type: ignore[no-any-return]


def _create(client: TestClient, **body: Any) -> dict[str, Any]:
    response = client.post("/api/generations", json={"model": "mock-cloud", **body})
    assert response.status_code == 201, response.text
    return response.json()[0]  # type: ignore[no-any-return]


def test_hosted_model_runs_as_a_remote_task(authed: TestClient) -> None:
    created = _create(authed, prompt="harbour at night", params={"latency_seconds": 0.2})
    assert created["provider"] == "mock-cloud"
    done = _settled(authed, created["id"])
    assert done["status"] == "succeeded", done
    assert done["asset"]["generation"]["model_id"] == "mock-cloud"
    job = authed.get(f"/api/jobs/{done['job_id']}").json()
    assert (job["kind"], job["lane"]) == ("generate.remote", "network")
    assert job["result"]["asset_ids"] == [done["asset"]["id"]]
    provider = authed.app.state.services.providers["mock-cloud"]  # type: ignore[attr-defined]
    assert len(provider.submitted) == 1


def test_hosted_failure_is_reported(authed: TestClient) -> None:
    created = _create(authed, params={"latency_seconds": 0, "force_failure": True})
    done = _settled(authed, created["id"])
    assert done["status"] == "failed"
    assert (done["error_code"], done["error_message"]) == ("mock_failure", FAILURE_MESSAGE)


def test_cancel_cancels_at_the_provider(authed: TestClient) -> None:
    provider = authed.app.state.services.providers["mock-cloud"]  # type: ignore[attr-defined]
    created = _create(authed, params={"latency_seconds": 60})
    wait_until(lambda: provider.submitted)
    response = authed.post(f"/api/generations/{created['id']}/cancel")
    assert response.status_code == 200, response.text
    assert _settled(authed, created["id"])["status"] == "cancelled"
    assert provider.cancelled == set(provider.submitted)


def test_batch_of_hosted_generations(authed: TestClient) -> None:
    response = authed.post(
        "/api/generations",
        json={"model": "mock-cloud", "batch": 3, "params": {"latency_seconds": 0.1}},
    )
    assert response.status_code == 201
    ids = [g["id"] for g in response.json()]
    assert all(_settled(authed, gid)["status"] == "succeeded" for gid in ids)


# ---------------------------------------------------------------- restart and retries


def make_services(tmp_path: Path, **providers: Any) -> Services:
    config = Config(data_dir=tmp_path / "data", token=TOKEN, ui_dist=tmp_path / "ui")
    defaults: dict[str, Any] = {"mock": MockProvider(), "mock-cloud": MockCloudProvider()}
    app = create_app(config, providers={**defaults, **providers})
    return app.state.services  # type: ignore[no-any-return]


async def start(services: Services) -> None:
    services.events.bind(asyncio.get_running_loop())
    await services.jobs.start()


async def generate(services: Services, model: str = "mock-cloud", **params: Any) -> str:
    out = await create_generations(
        services, GenerationCreate(model=model, params=params), services.providers
    )
    return out[0].id


def _row(services: Services, generation_id: str) -> tuple[str, str | None, str | None]:
    with services.db.session() as session:
        generation = session.get(Generation, generation_id)
        assert generation is not None
        job = session.get(Job, generation.job_id)
        assert job is not None
        return generation.status, generation.error_code, job.provider_task_id


async def status_of(services: Services, generation_id: str) -> str:
    return (await asyncio.to_thread(_row, services, generation_id))[0]


async def settled(
    services: Services, generation_id: str, limit: float = 15
) -> tuple[str, str | None]:
    async def check() -> tuple[str, str | None] | None:
        state, code, _ = await asyncio.to_thread(_row, services, generation_id)
        return (state, code) if state in {"succeeded", "failed", "cancelled"} else None

    return await async_wait_until(check, limit=limit)  # type: ignore[no-any-return]


def _asset_count(services: Services, generation_id: str) -> int:
    with services.db.session() as session:
        return (
            session.scalar(
                select(func.count())
                .select_from(Asset)
                .where(Asset.source_generation_id == generation_id)
            )
            or 0
        )


@pytest.mark.anyio
async def test_restart_resumes_the_task_without_paying_twice(tmp_path: Path) -> None:
    first_provider = MockCloudProvider()
    first = make_services(tmp_path, **{"mock-cloud": first_provider})
    await start(first)
    generation_id = await generate(first, latency_seconds=1.0)

    async def has_task() -> bool:
        return (await asyncio.to_thread(_row, first, generation_id))[2] is not None

    await async_wait_until(has_task)
    await first.jobs.stop()  # the app quits mid-task
    assert await status_of(first, generation_id) == "running"
    assert len(first_provider.submitted) == 1

    second_provider = MockCloudProvider()  # a fresh process
    second = make_services(tmp_path, **{"mock-cloud": second_provider})
    await start(second)
    try:
        assert await settled(second, generation_id) == ("succeeded", None)
        assert second_provider.submitted == []
        assert await asyncio.to_thread(_asset_count, second, generation_id) == 1
    finally:
        await second.jobs.stop()


class CountingCloud(MockCloudProvider):
    def __init__(self) -> None:
        super().__init__()
        self.collects = 0
        self.collect_failures: list[ProviderError] = []
        self.status_failures: list[ProviderError] = []
        self.submit_error: ProviderError | None = None
        #: Keep raising the first status failure instead of using it up.
        self.forever = False

    async def submit(self, request: GenerationRequest, ctx: JobContext) -> str:
        if self.submit_error:
            self.submitted.append("refused")
            raise self.submit_error
        return await super().submit(request, ctx)

    async def status(self, task_id: str, request: GenerationRequest, ctx: JobContext) -> TaskStatus:
        if self.status_failures:
            error = self.status_failures[0]
            if not self.forever:
                self.status_failures.pop(0)
            raise error
        return await super().status(task_id, request, ctx)

    async def collect(self, task_id: str, request: GenerationRequest, ctx: JobContext) -> Any:
        self.collects += 1
        if self.collect_failures:
            raise self.collect_failures.pop(0)
        return await super().collect(task_id, request, ctx)


@pytest.mark.anyio
async def test_results_are_collected_once(tmp_path: Path) -> None:
    cloud = CountingCloud()
    services = make_services(tmp_path, **{"mock-cloud": cloud})
    await start(services)
    try:
        generation_id = await generate(services, latency_seconds=0)
        assert await settled(services, generation_id) == ("succeeded", None)
        _, _, task_id = await asyncio.to_thread(_row, services, generation_id)
        assert task_id is not None
        # A poll after the results are in (as after a crash between storing
        # them and marking the job done) reuses them instead of downloading.
        kind = services.jobs.kind("generate.remote")
        ctx = JobContext("x", {"generation_id": generation_id}, 1, services, lambda *_: None)
        again = await kind.poll(ctx, task_id)
        assert again.state == "succeeded"
        assert cloud.collects == 1
        assert await asyncio.to_thread(_asset_count, services, generation_id) == 1
    finally:
        await services.jobs.stop()


@pytest.mark.anyio
async def test_transient_status_errors_are_retried(tmp_path: Path) -> None:
    cloud = CountingCloud()
    cloud.status_failures = [ProviderError("transient")] * 4
    services = make_services(tmp_path, **{"mock-cloud": cloud})
    await start(services)
    try:
        generation_id = await generate(services, latency_seconds=0)
        assert await settled(services, generation_id) == ("succeeded", None)
        assert len(cloud.submitted) == 1
    finally:
        await services.jobs.stop()


@pytest.mark.anyio
async def test_an_unreachable_provider_eventually_fails_the_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(GenerateRemoteJob, "max_poll_misses", 3)
    cloud = CountingCloud()
    cloud.forever = True
    cloud.status_failures = [ProviderError("transient")]
    services = make_services(tmp_path, **{"mock-cloud": cloud})
    await start(services)
    try:
        generation_id = await generate(services, latency_seconds=0)
        assert await settled(services, generation_id) == ("failed", "provider_transient")
        assert len(cloud.submitted) == 1
    finally:
        await services.jobs.stop()


@pytest.mark.anyio
async def test_a_failing_download_keeps_waiting_instead_of_paying_again(tmp_path: Path) -> None:
    cloud = CountingCloud()
    cloud.collect_failures = [ProviderError("transient")] * 5  # outlasts one round of retries
    services = make_services(tmp_path, **{"mock-cloud": cloud})
    await start(services)
    try:
        generation_id = await generate(services, latency_seconds=0)
        assert await settled(services, generation_id) == ("succeeded", None)
        assert len(cloud.submitted) == 1
        assert cloud.collects == 6
    finally:
        await services.jobs.stop()


@pytest.mark.anyio
async def test_a_refused_submit_is_not_retried(tmp_path: Path) -> None:
    cloud = CountingCloud()
    cloud.submit_error = ProviderError("transient", "Timed out.")  # maybe accepted: never resend
    services = make_services(tmp_path, **{"mock-cloud": cloud})
    await start(services)
    try:
        generation_id = await generate(services)
        assert await settled(services, generation_id) == ("failed", "provider_transient")
        assert cloud.submitted == ["refused"]
    finally:
        await services.jobs.stop()


@pytest.mark.anyio
async def test_a_permanent_status_error_fails_at_once(tmp_path: Path) -> None:
    cloud = CountingCloud()
    cloud.status_failures = [ProviderError("auth")]
    services = make_services(tmp_path, **{"mock-cloud": cloud})
    await start(services)
    try:
        generation_id = await generate(services, latency_seconds=0)
        assert await settled(services, generation_id) == ("failed", "provider_auth")
    finally:
        await services.jobs.stop()


class FlakyLocal(MockProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, request: GenerationRequest, ctx: JobContext) -> Any:
        self.calls += 1
        if self.calls == 1:
            raise ProviderError("transient", "GPU busy")
        return await super().generate(request, ctx)


@pytest.mark.anyio
async def test_local_transient_errors_are_retried(tmp_path: Path) -> None:
    local = FlakyLocal()
    services = make_services(tmp_path, mock=local)
    await start(services)
    try:
        generation_id = await generate(services, model="mock", latency_seconds=0)
        assert await settled(services, generation_id) == ("succeeded", None)
        assert local.calls == 2
    finally:
        await services.jobs.stop()


@pytest.mark.anyio
async def test_a_model_whose_provider_is_missing_is_refused(tmp_path: Path) -> None:
    services = make_services(tmp_path)
    services.providers.pop("mock-cloud")
    with pytest.raises(ValueError, match="Connect mock-cloud"):
        await generate(services)
