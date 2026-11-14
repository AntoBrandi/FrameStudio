"""Contract suite for generation providers (plan step M2.2).

Every provider runs the same checks: what it estimates, what files it returns
for each of its catalog fixtures, how it fails, and (hosted providers) how its
tasks report progress, cancel and survive a restart. M2.7+ adds real
providers here with recorded responses; ``LIVE_API=1`` runs them for real.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx2
import pytest
from PIL import Image

from framestudio.app import create_app
from framestudio.catalog import ModelSpec, estimate_cost, load_catalog, validate_params
from framestudio.catalog.fal_import import spec_from_fal
from framestudio.config import Config
from framestudio.connections.fake import FAL_DEMO, fal_model
from framestudio.jobs.base import JobContext
from framestudio.media.tools import ffmpeg_path, ffprobe_path
from framestudio.providers.base import (
    Estimate,
    GenerationOutput,
    GenerationProvider,
    GenerationRequest,
    HostedProvider,
    LocalProvider,
    ProviderError,
    RemoteProvider,
    TaskStatus,
)
from framestudio.providers.fal import FalProvider
from framestudio.providers.gemini_image import GeminiImageProvider
from framestudio.providers.mock import MockCloudProvider, MockProvider
from framestudio.providers.seedance import SeedanceProvider
from framestudio.services import Services

from ..conftest import TOKEN
from .byteplus_wire import FakeBytePlus
from .fal_wire import FakeFal
from .gemini_image_wire import FakeGemini

pytestmark = pytest.mark.anyio

HAS_FFMPEG = bool(ffmpeg_path() and ffprobe_path())


@dataclass
class Harness:
    """A provider under test, the catalog models it serves, and params that
    make it fail on purpose."""

    name: str
    make: Callable[[], GenerationProvider]
    models: tuple[str, ...]
    failing: dict[str, Any]
    fast: dict[str, Any]
    #: A prompt the provider refuses (for providers without a failure switch).
    failing_prompt: str = "a lighthouse at dusk"
    #: Hosted tasks: settings (and a prompt) for a task that finishes within
    #: a second, and for one that stays queued.
    quick: dict[str, Any] = field(default_factory=dict)
    slow: tuple[dict[str, Any], str] = ({}, "a lighthouse at dusk")
    #: Entries that are not in the built-in catalog (imported, as from fal).
    specs: tuple[ModelSpec, ...] = ()


LIVE = os.environ.get("LIVE_API") == "1"


def gemini_image() -> GeminiImageProvider:
    """Nano Banana against the stand-in Gemini API, or the real one with
    LIVE_API=1 and FRAMESTUDIO_LIVE_GOOGLE_KEY (paid)."""
    key = os.environ.get("FRAMESTUDIO_LIVE_GOOGLE_KEY")
    if LIVE and key:
        return GeminiImageProvider(key, httpx2.AsyncHTTPTransport())
    fake = FakeGemini()
    return GeminiImageProvider(fake.key, httpx2.MockTransport(fake))


#: One stand-in BytePlus for the session: a task outlives a provider
#: instance (as after a restart) only if the server remembers it.
BYTEPLUS = FakeBytePlus()


def seedance() -> SeedanceProvider:
    """Seedance against the stand-in ModelArk API, or the real one with
    LIVE_API=1 and FRAMESTUDIO_LIVE_BYTEPLUS_KEY (paid)."""
    key = os.environ.get("FRAMESTUDIO_LIVE_BYTEPLUS_KEY")
    if LIVE and key:
        return SeedanceProvider(key, httpx2.AsyncHTTPTransport())
    return SeedanceProvider(BYTEPLUS.key, httpx2.MockTransport(BYTEPLUS))


FAL = FakeFal()
FAL_SPECS = tuple(
    spec_from_fal(
        fal_model(demo, openapi=True),
        {"unit_price": demo["price"]["unit_price"], "unit": demo["price"]["unit"]},
    )
    for demo in FAL_DEMO
)


def fal() -> FalProvider:
    """fal.ai against the stand-in queue, or the real one with LIVE_API=1 and
    FRAMESTUDIO_LIVE_FAL_KEY (paid; the demo endpoints exist only offline)."""
    key = os.environ.get("FRAMESTUDIO_LIVE_FAL_KEY")
    if LIVE and key:
        return FalProvider(key, httpx2.AsyncHTTPTransport())
    return FalProvider(FAL.key, httpx2.MockTransport(FAL))


HARNESSES = (
    Harness(
        "mock",
        MockProvider,
        ("mock",),
        failing={"force_failure": True},
        fast={"latency_seconds": 0.1},
    ),
    Harness(
        "mock-cloud",
        MockCloudProvider,
        ("mock-cloud",),
        failing={"force_failure": True},
        fast={"latency_seconds": 0.1},
        quick={"latency_seconds": 0.4},
        slow=({"latency_seconds": 30}, "a lighthouse at dusk"),
    ),
    Harness(
        "byteplus",
        lambda: seedance(),
        ("seedance-2-0", "seedance-2-0-fast", "seedance-2-5"),
        failing={},
        fast={},
        failing_prompt="something blocked",
        slow=({}, "a slow lighthouse"),
    ),
    Harness(
        "fal",
        lambda: fal(),
        tuple(spec.id for spec in FAL_SPECS),
        failing={},
        fast={},
        failing_prompt="a flaky lighthouse",
        slow=({}, "a slow lighthouse"),
        specs=FAL_SPECS,
    ),
    Harness(
        "google",
        lambda: gemini_image(),
        ("nano-banana-pro", "nano-banana"),
        failing={},
        fast={},
        failing_prompt="something blocked",
    ),
)
CATALOG = load_catalog()
for _harness in HARNESSES:
    for _spec in _harness.specs:
        CATALOG.add(_spec)
FIXTURE_CASES = [
    pytest.param(h, model, index, id=f"{model}-{fixture.mode}-{index}")
    for h in HARNESSES
    for model in h.models
    for index, fixture in enumerate(CATALOG.get(model).fixtures)  # type: ignore[union-attr]
]


@pytest.fixture
def services(tmp_path: Path) -> Iterator[Services]:
    config = Config(data_dir=tmp_path / "data", token=TOKEN, ui_dist=tmp_path / "ui")
    app = create_app(config)
    for harness_ in HARNESSES:
        for spec in harness_.specs:
            app.state.services.catalog.add(spec)
    yield app.state.services
    app.state.services.db.dispose()


@pytest.fixture(params=HARNESSES, ids=[h.name for h in HARNESSES])
def harness(request: pytest.FixtureRequest) -> Harness:
    return request.param  # type: ignore[no-any-return]


def make_ctx(
    services: Services, reports: list[tuple[float, str | None]] | None = None
) -> JobContext:
    def report(_job: str, fraction: float, message: str | None) -> None:
        if reports is not None:
            reports.append((fraction, message))

    return JobContext("contract", {}, 1, services, report)


def build_request(
    spec: ModelSpec, mode: str, params: dict[str, Any], prompt: str = "a lighthouse at dusk"
) -> GenerationRequest:
    return GenerationRequest(
        mode=mode,  # type: ignore[arg-type]
        prompt=prompt,
        seed=1234,
        params=validate_params(spec, mode, params),
        model=spec.id,
        provider_model=spec.provider_model,
    )


async def drive(
    provider: GenerationProvider, request: GenerationRequest, ctx: JobContext, limit: float = 30
) -> list[GenerationOutput]:
    """Run a request to completion the way the job queue would."""
    if not isinstance(provider, RemoteProvider):  # local, or hosted in one call
        assert isinstance(provider, LocalProvider | HostedProvider)
        return await provider.generate(request, ctx)
    task_id = await provider.submit(request, ctx)
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        status = await provider.status(task_id, request, ctx)
        if status.state == "succeeded":
            return await provider.collect(task_id, request, ctx)
        if status.state == "failed":
            assert status.error is not None
            raise status.error
        assert status.state in {"queued", "running"}, status
        await asyncio.sleep(0.05)
    raise AssertionError("remote task did not finish")


def probe(path: Path) -> dict[str, Any]:
    out = subprocess.run(
        [
            str(ffprobe_path()),
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        check=True,
        text=True,
    )
    return json.loads(out.stdout)  # type: ignore[no-any-return]


# ---------------------------------------------------------------- every provider


def test_every_catalog_model_is_under_contract() -> None:
    covered = {model for h in HARNESSES for model in h.models}
    assert {spec.id for spec in load_catalog().all()} <= covered


async def test_identity_and_shape(harness: Harness, services: Services) -> None:
    provider = harness.make()
    assert isinstance(provider, GenerationProvider)
    if provider.remote:
        assert isinstance(provider, RemoteProvider | HostedProvider)
    else:
        assert isinstance(provider, LocalProvider)
    for model in harness.models:
        spec = services.catalog.get(model)
        assert spec is not None
        assert spec.provider == provider.id
        assert (spec.runtime == "api") == provider.remote


async def test_estimate(harness: Harness, services: Services) -> None:
    provider = harness.make()
    for model in harness.models:
        spec = services.catalog.get(model)
        assert spec is not None
        for mode in spec.modes:
            request = build_request(spec, mode, dict(harness.fast))
            estimate = await provider.estimate(spec, request, batch=2)
            assert isinstance(estimate, Estimate)
            assert estimate.seconds is None or estimate.seconds >= 0
            if spec.runtime == "api":
                expected = estimate_cost(spec, mode, request.params, 2)
                assert estimate.cost_usd is not None and estimate.cost_usd > 0
                assert estimate.cost_usd == pytest.approx(expected)
            else:
                assert estimate.cost_usd in (None, 0)


@pytest.mark.parametrize(("harness_", "model", "index"), FIXTURE_CASES)
async def test_catalog_fixtures_produce_valid_media(
    harness_: Harness, model: str, index: int, services: Services
) -> None:
    spec = services.catalog.get(model)
    assert spec is not None
    fixture = spec.fixtures[index]
    if fixture.mode == "video" and not HAS_FFMPEG:
        pytest.skip("ffmpeg is required")
    request = build_request(spec, fixture.mode, {**fixture.params, **harness_.fast})
    outputs = await drive(harness_.make(), request, make_ctx(services))
    try:
        assert outputs, "a finished request returns at least one file"
        for output in outputs:
            assert output.path.is_file() and output.path.stat().st_size > 0
            assert output.kind == fixture.mode
            assert output.path.suffix == f".{output.ext}"
            if output.kind == "image":
                with Image.open(output.path) as image:
                    image.verify()
                with Image.open(output.path) as image:
                    width, height = image.size
                    assert Image.MIME[image.format or ""] == output.mime
                aspect = str(request.params.get("aspect", "1:1"))
                w, h = (float(x) for x in aspect.split(":"))
                assert width / height == pytest.approx(w / h, rel=0.03)
            else:
                info = probe(output.path)
                duration = float(info["format"]["duration"])
                assert duration == pytest.approx(float(request.params["duration"]), abs=0.3)
                kinds = {s["codec_type"] for s in info["streams"]}
                assert "video" in kinds
                assert ("audio" in kinds) == bool(request.params.get("audio"))
    finally:
        for output in outputs:
            output.path.unlink(missing_ok=True)


async def test_failure_is_a_classified_provider_error(harness: Harness, services: Services) -> None:
    provider = harness.make()
    spec = services.catalog.get(harness.models[0])
    assert spec is not None
    request = build_request(
        spec, spec.modes[0], {**harness.fast, **harness.failing}, prompt=harness.failing_prompt
    )
    with pytest.raises(ProviderError) as caught:
        await drive(provider, request, make_ctx(services))
    error = caught.value
    assert error.kind in {"unknown", "transient", "content_policy", "invalid_params"}
    assert error.code and error.message
    job_error = error.to_job_error()
    assert (job_error.code, job_error.message, job_error.transient) == (
        error.code,
        error.message,
        error.transient,
    )


async def test_progress_stays_within_bounds(harness: Harness, services: Services) -> None:
    spec = services.catalog.get(harness.models[0])
    assert spec is not None
    reports: list[tuple[float, str | None]] = []
    provider = harness.make()
    request = build_request(spec, spec.modes[0], {**harness.fast})
    outputs = await drive(provider, request, make_ctx(services, reports))
    for output in outputs:
        output.path.unlink(missing_ok=True)
    fractions = [fraction for fraction, _ in reports]
    assert all(0.0 <= f <= 1.0 for f in fractions)
    assert fractions == sorted(fractions)


# ---------------------------------------------------------------- hosted providers

REMOTE = [h for h in HARNESSES if isinstance(h.make(), RemoteProvider)]


@pytest.fixture(params=REMOTE, ids=[h.name for h in REMOTE])
def remote(request: pytest.FixtureRequest) -> Harness:
    return request.param  # type: ignore[no-any-return]


def _remote_setup(
    harness: Harness,
    services: Services,
    params: dict[str, Any],
    prompt: str = "a lighthouse at dusk",
) -> tuple[RemoteProvider, GenerationRequest, JobContext]:
    provider = harness.make()
    assert isinstance(provider, RemoteProvider)
    spec = services.catalog.get(harness.models[0])
    assert spec is not None
    return provider, build_request(spec, spec.modes[0], params, prompt), make_ctx(services)


async def _settle(
    provider: RemoteProvider, task_id: str, request: GenerationRequest, ctx: JobContext
) -> list[TaskStatus]:
    seen: list[TaskStatus] = []
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        status = await provider.status(task_id, request, ctx)
        seen.append(status)
        if status.state not in {"queued", "running"}:
            return seen
        await asyncio.sleep(0.05)
    raise AssertionError("the task did not settle")


async def test_status_reports_progress_then_success(remote: Harness, services: Services) -> None:
    provider, request, ctx = _remote_setup(remote, services, remote.quick)
    task_id = await provider.submit(request, ctx)
    assert task_id
    seen = await _settle(provider, task_id, request, ctx)
    assert seen[-1].state == "succeeded"
    running = [s.progress for s in seen if s.state == "running" and s.progress is not None]
    assert running, "a running task reports progress"
    assert running == sorted(running) and all(0 <= p < 1 for p in running)
    outputs = await provider.collect(task_id, request, ctx)
    for output in outputs:
        output.path.unlink(missing_ok=True)


async def test_cancel_stops_a_queued_task(remote: Harness, services: Services) -> None:
    params, prompt = remote.slow
    provider, request, ctx = _remote_setup(remote, services, params, prompt)
    task_id = await provider.submit(request, ctx)
    await provider.cancel(task_id, request, ctx)
    assert (await provider.status(task_id, request, ctx)).state == "cancelled"


async def test_unknown_task_is_not_found(remote: Harness, services: Services) -> None:
    provider, request, ctx = _remote_setup(remote, services, {})
    for call in (provider.status, provider.collect, provider.cancel):
        with pytest.raises(ProviderError) as caught:
            await call("no-such-task", request, ctx)
        assert caught.value.kind == "not_found"


async def test_a_task_id_outlives_the_process(remote: Harness, services: Services) -> None:
    """Another provider instance (as after a restart) picks the task up."""
    first, request, ctx = _remote_setup(remote, services, remote.quick)
    task_id = await first.submit(request, ctx)
    second = remote.make()
    assert isinstance(second, RemoteProvider)
    assert (await _settle(second, task_id, request, ctx))[-1].state == "succeeded"
    outputs = await second.collect(task_id, request, ctx)
    assert outputs and all(o.path.is_file() for o in outputs)
    for output in outputs:
        output.path.unlink(missing_ok=True)


async def test_failed_task_carries_its_error(remote: Harness, services: Services) -> None:
    provider, request, ctx = _remote_setup(
        remote, services, {**remote.quick, **remote.failing}, remote.failing_prompt
    )
    task_id = await provider.submit(request, ctx)
    status = (await _settle(provider, task_id, request, ctx))[-1]
    assert status.state == "failed"
    assert isinstance(status.error, ProviderError)
