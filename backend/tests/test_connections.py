"""M2.3: add, check, turn off and remove keys for each provider; keys never
leak; spending caps stop requests once reached."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from framestudio.app import create_app
from framestudio.config import Config
from framestudio.connections.service import ConnectionUpdate
from framestudio.db.models import Connection, Generation
from framestudio.generation import GenerateRemoteJob, GenerationCreate, create_generations
from framestudio.jobs.base import JobContext
from framestudio.llm.base import ChatRequest, Message
from framestudio.llm.fake import FakeLLM
from framestudio.llm.metered import MeteredLLM
from framestudio.providers.base import GenerationRequest, ProviderError
from framestudio.providers.mock import MockCloudProvider, MockProvider
from framestudio.services import Services

from .conftest import AUTH, BASE_URL, TOKEN, async_wait_until, wait_until

PROVIDERS = ("openai", "anthropic", "google", "byteplus")
HOST = {
    "openai": "api.openai.com",
    "anthropic": "api.anthropic.com",
    "google": "generativelanguage.googleapis.com",
    "byteplus": "ark.ap-southeast.bytepluses.com",
}


def secret(provider: str, kind: str = "good") -> str:
    return f"{kind}-{provider}-KEY-0123456789abcdefXYZ"


class FakeProviders:
    """Each provider's key-check endpoint, answering by the key's prefix."""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.revoked: set[str] = set()

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        headers = request.headers
        key = (
            headers.get("authorization", "").removeprefix("Bearer ")
            or headers.get("x-api-key")
            or headers.get("x-goog-api-key")
            or ""
        )
        if key in self.revoked:
            return httpx2.Response(401, json={"error": {"message": "revoked"}})
        if key.startswith("down"):
            return httpx2.Response(503, text="overloaded")
        if key.startswith("good"):
            return httpx2.Response(200, json={"data": []})
        if request.url.host == HOST["google"]:  # Google answers a bad key with 400
            return httpx2.Response(
                400, json={"error": {"message": "API key not valid. Please pass a valid API key."}}
            )
        return httpx2.Response(401, json={"error": {"message": "invalid x-api-key"}})


@pytest.fixture
def fake() -> FakeProviders:
    return FakeProviders()


@pytest.fixture
def app(config: Config, fake: FakeProviders) -> FastAPI:  # overrides conftest's app
    return create_app(config, http_transport=httpx2.MockTransport(fake))


class Recording:
    """Every response body the API returned, for the leak scan."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.bodies: list[str] = []

    def __getattr__(self, method: str) -> Any:
        call = getattr(self.client, method)

        def wrapped(*args: Any, **kwargs: Any) -> Any:
            response = call(*args, **kwargs)
            self.bodies.append(response.text)
            return response

        return wrapped


@pytest.fixture
def api(app: FastAPI) -> Iterator[Recording]:
    with TestClient(app, base_url=BASE_URL, headers=AUTH) as client:
        yield Recording(client)


def by_provider(api: Recording) -> dict[str, dict[str, Any]]:
    return {c["provider"]: c for c in api.get("/api/connections").json()}


# ---------------------------------------------------------------- add, check, remove


def test_lists_every_provider_with_its_hosts(api: Recording) -> None:
    listed = by_provider(api)
    assert set(listed) == {*PROVIDERS, "fal", "mock-cloud"}  # fal: tests/test_fal.py
    assert listed["fal"]["hosts"] == ["*.fal.media", "api.fal.ai", "queue.fal.run"]
    for provider in PROVIDERS:
        entry = listed[provider]
        assert HOST[provider] in entry["hosts"]
        assert all(h == HOST[provider] or h.startswith("*.") for h in entry["hosts"])
        assert entry["requires_key"] and entry["key_url"].startswith("https://")
        assert not entry["connected"] and not entry["enabled"]
    assert listed["mock-cloud"]["test"] and listed["mock-cloud"]["hosts"] == []


@pytest.mark.parametrize("provider", PROVIDERS)
def test_add_check_and_remove_a_key(api: Recording, fake: FakeProviders, provider: str) -> None:
    key = secret(provider)
    response = api.put(f"/api/connections/{provider}", json={"api_key": key})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["connected"] and body["enabled"] and body["status"] == "ok"
    assert body["key_hint"] == f"…{key[-4:]}"
    assert body["key_store"] == "file"
    assert body["validated_at"] is not None
    request = fake.requests[-1]
    assert request.url.host == HOST[provider] and request.url.scheme == "https"
    assert key not in str(request.url)  # in a header, never the URL
    services: Services = api.client.app.state.services  # type: ignore[attr-defined]
    assert services.connections.api_key(provider) == key
    assert services.connections.is_enabled(provider)

    checked = api.post(f"/api/connections/{provider}/check").json()
    assert checked["status"] == "ok"

    assert api.delete(f"/api/connections/{provider}").status_code == 204
    after = by_provider(api)[provider]
    assert not after["connected"] and after["key_hint"] is None
    assert services.connections.api_key(provider) is None
    assert not services.connections.is_enabled(provider)
    assert key not in services.paths.key_file.read_text()


@pytest.mark.parametrize("provider", PROVIDERS)
def test_a_refused_key_is_not_stored(api: Recording, provider: str) -> None:
    response = api.put(f"/api/connections/{provider}", json={"api_key": secret(provider, "bad")})
    assert response.status_code == 422
    assert "refused this key" in response.json()["detail"]
    assert not by_provider(api)[provider]["connected"]


def test_an_unreachable_provider_keeps_the_key_unchecked(api: Recording) -> None:
    body = api.put("/api/connections/openai", json={"api_key": secret("openai", "down")}).json()
    assert body["connected"] and body["status"] == "unreachable"
    assert "busy" in body["status_message"]
    assert body["validated_at"] is None


def test_check_notices_a_revoked_key(api: Recording, fake: FakeProviders) -> None:
    api.put("/api/connections/anthropic", json={"api_key": secret("anthropic")})
    fake.revoked.add(secret("anthropic"))
    body = api.post("/api/connections/anthropic/check").json()
    assert body["status"] == "refused"
    assert "refused this key" in body["status_message"]


def test_replacing_a_key_forgets_the_old_one(api: Recording) -> None:
    first, second = secret("openai"), "good-openai-SECOND-key-9876543210"
    api.put("/api/connections/openai", json={"api_key": first})
    api.put("/api/connections/openai", json={"api_key": second})
    services: Services = api.client.app.state.services  # type: ignore[attr-defined]
    assert services.connections.api_key("openai") == second
    stored = json.loads(services.paths.key_file.read_text())["items"]
    assert len(stored) == 1


def test_turning_a_connection_off_blocks_its_calls(api: Recording) -> None:
    api.put("/api/connections/google", json={"api_key": secret("google")})
    body = api.put("/api/connections/google", json={"enabled": False}).json()
    assert body["connected"] and not body["enabled"]
    services: Services = api.client.app.state.services  # type: ignore[attr-defined]
    assert not services.connections.is_enabled("google")

    async def call() -> None:
        async with services.outbound.client("google") as client:
            await client.get("https://generativelanguage.googleapis.com/v1beta/models")

    with pytest.raises(httpx2.RequestError, match="turned off"):
        asyncio.run(call())
    assert api.put("/api/connections/google", json={"enabled": True}).json()["enabled"]


@pytest.mark.parametrize(
    "body",
    [
        {"api_key": "has spaces in it 1234567890"},
        {"api_key": ""},
        {"api_key": "x" * 600},
    ],
)
def test_malformed_keys_are_refused_without_echo(api: Recording, body: dict[str, str]) -> None:
    response = api.put("/api/connections/openai", json=body)
    assert response.status_code == 422
    if body["api_key"]:
        assert body["api_key"] not in response.text


def test_settings_without_a_key_need_one_first(api: Recording) -> None:
    response = api.put("/api/connections/openai", json={"monthly_cap_usd": 5})
    assert response.status_code == 422
    assert "Add a key" in response.json()["detail"]


def test_unknown_provider(api: Recording) -> None:
    assert api.get("/api/connections/nope").status_code == 404
    assert api.put("/api/connections/nope", json={"enabled": True}).status_code == 404
    assert api.post("/api/connections/nope/check").status_code == 404
    assert api.delete("/api/connections/nope").status_code == 404


def test_the_test_provider_takes_a_cap_but_no_key(api: Recording) -> None:
    body = api.put("/api/connections/mock-cloud", json={"monthly_cap_usd": 0.05}).json()
    assert body["connected"] and body["monthly_cap_usd"] == 0.05 and body["status"] == "ok"
    refused = api.put("/api/connections/mock-cloud", json={"api_key": secret("mock")})
    assert refused.status_code == 422
    cleared = api.put("/api/connections/mock-cloud", json={"monthly_cap_usd": None}).json()
    assert cleared["monthly_cap_usd"] is None


def test_routes_need_the_token(client: TestClient) -> None:
    assert client.get("/api/connections").status_code == 401
    assert client.put("/api/connections/openai", json={"api_key": "good"}).status_code == 401
    assert client.get("/api/usage").status_code == 401


# ---------------------------------------------------------------- the secret scan


def test_keys_never_reach_logs_database_files_or_responses(
    api: Recording, caplog: pytest.LogCaptureFixture
) -> None:
    keys = [secret(p) for p in PROVIDERS]
    with caplog.at_level(logging.DEBUG):
        for provider, key in zip(PROVIDERS, keys, strict=True):
            api.put(f"/api/connections/{provider}", json={"api_key": key})
            api.post(f"/api/connections/{provider}/check")
            api.put(f"/api/connections/{provider}", json={"enabled": False, "monthly_cap_usd": 3})
        api.put("/api/connections/openai", json={"api_key": secret("openai", "bad")})  # refused
        api.get("/api/connections")
        api.get("/api/usage")
        api.get("/api/openapi.json")
    services: Services = api.client.app.state.services  # type: ignore[attr-defined]
    services.db.dispose()  # flush the WAL into the database file
    leaked: list[str] = []
    files = [p for p in services.paths.root.rglob("*") if p.is_file()]
    assert services.paths.key_file in files and services.paths.database in files
    for key in [*keys, secret("openai", "bad")]:
        needle = key.encode()
        leaked += [f"file {p.name}" for p in files if needle in p.read_bytes()]
        leaked += ["log" for r in caplog.records if key in r.getMessage()]
        leaked += ["response" for body in api.bodies if key in body]
    assert leaked == []
    assert all("api_key" not in body for body in api.bodies[:-1])  # the schema names the field
    for key, provider in zip(keys, PROVIDERS, strict=True):
        assert services.connections.api_key(provider) == key  # still usable inside


# ---------------------------------------------------------------- usage and caps


def test_usage_summary(api: Recording) -> None:
    services: Services = api.client.app.state.services  # type: ignore[attr-defined]
    services.usage.record(
        "openai", model="gpt", source="enhance", cost_usd=0.002, input_tokens=100, output_tokens=40
    )
    services.usage.record(
        "openai", model="gpt", source="director", cost_usd=0.003, input_tokens=50, output_tokens=10
    )
    services.usage.record("byteplus", model="seedance", cost_usd=0.5, video_seconds=5)
    summary = api.get("/api/usage").json()
    rows = {p["provider"]: p for p in summary["providers"]}
    assert rows["openai"]["requests"] == 2 and rows["openai"]["input_tokens"] == 150
    assert rows["openai"]["cost_usd"] == pytest.approx(0.005)
    assert rows["byteplus"]["video_seconds"] == 5
    assert summary["total_cost_usd"] == pytest.approx(0.505)
    assert api.get("/api/usage", params={"month": "2020-01"}).json()["providers"] == []
    assert api.get("/api/usage", params={"month": "January"}).status_code == 422
    listed = by_provider(api)
    assert listed["openai"]["spent_this_month_usd"] == pytest.approx(0.005)


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(GenerateRemoteJob, "poll_interval", 0.05)
    monkeypatch.setattr(GenerateRemoteJob, "retry_delay", 0.0)


def _generate(api: Recording, **body: Any) -> Any:
    return api.post(
        "/api/generations", json={"model": "mock-cloud", "params": {"latency_seconds": 0}, **body}
    )


def test_a_cap_stops_hosted_generations_once_reached(api: Recording) -> None:
    api.put("/api/connections/mock-cloud", json={"monthly_cap_usd": 0.02})
    services: Services = api.client.app.state.services  # type: ignore[attr-defined]
    for _ in range(2):
        response = _generate(api)
        assert response.status_code == 201, response.text
        generation_id = response.json()[0]["id"]
        wait_until(
            lambda gid=generation_id: (
                api.client.get(f"/api/generations/{gid}").json()["status"] == "succeeded"
            )
        )
    assert services.usage.month_spend("mock-cloud") == pytest.approx(0.02)
    refused = _generate(api)
    assert refused.status_code == 402
    assert "monthly cap" in refused.json()["detail"]
    summary = {p["provider"]: p for p in api.get("/api/usage").json()["providers"]}
    assert summary["mock-cloud"]["images"] == 2 and summary["mock-cloud"]["requests"] == 2
    # Raising the cap lets work through again; local models never count.
    api.put("/api/connections/mock-cloud", json={"monthly_cap_usd": 1})
    assert _generate(api).status_code == 201
    assert api.post("/api/generations", json={"model": "mock"}).status_code == 201


def test_a_batch_that_would_pass_the_cap_is_refused_whole(api: Recording) -> None:
    api.put("/api/connections/mock-cloud", json={"monthly_cap_usd": 0.025})
    response = _generate(api, batch=3)
    assert response.status_code == 402
    assert "would pass" in response.json()["detail"]
    assert api.client.get("/api/generations").json() == []


def make_services(tmp_path: Path, cloud: MockCloudProvider) -> Services:
    config = Config(data_dir=tmp_path / "data", token=TOKEN, ui_dist=tmp_path / "ui")
    app = create_app(config, providers={"mock": MockProvider(), "mock-cloud": cloud})
    return app.state.services  # type: ignore[no-any-return]


@pytest.mark.anyio
async def test_queued_jobs_cannot_overspend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requests accepted while the cap still had room (here: every one) are
    held to it when they are sent: the reservation lets only what fits through."""
    cloud = MockCloudProvider()
    services = make_services(tmp_path, cloud)
    await services.connections.update("mock-cloud", ConnectionUpdate(monthly_cap_usd=0.01))
    monkeypatch.setattr(services.usage, "check_cap", lambda *_args, **_kw: None)
    services.events.bind(asyncio.get_running_loop())
    await services.jobs.start()
    try:
        created = [
            (
                await create_generations(
                    services,
                    GenerationCreate(model="mock-cloud", params={"latency_seconds": 0}),
                    services.providers,
                )
            )[0].id
            for _ in range(3)
        ]

        async def settled() -> bool:
            states = [await asyncio.to_thread(_status, services, gid) for gid in created]
            return all(state in {"succeeded", "failed"} for state in states)

        await async_wait_until(settled)
        states = sorted([await asyncio.to_thread(_status, services, gid) for gid in created])
        assert states == ["failed", "failed", "succeeded"]
        assert len(cloud.submitted) == 1
        assert services.usage.month_spend("mock-cloud") == pytest.approx(0.01)
        codes = {_error_code(services, gid) for gid in created} - {None}
        assert codes == {"spend_cap"}
    finally:
        await services.jobs.stop()


def _error_code(services: Services, generation_id: str) -> str | None:
    with services.db.session() as session:
        generation = session.get(Generation, generation_id)
        return generation.error_code if generation else None


class RefusingCloud(MockCloudProvider):
    def __init__(self, error: ProviderError) -> None:
        super().__init__()
        self.error = error

    async def submit(self, request: GenerationRequest, ctx: JobContext) -> str:
        raise self.error


@pytest.mark.anyio
@pytest.mark.parametrize(("kind", "kept"), [("auth", 0), ("transient", 1)])
async def test_a_refused_submit_costs_nothing_but_a_timeout_is_kept(
    tmp_path: Path, kind: str, kept: int
) -> None:
    services = make_services(tmp_path, RefusingCloud(ProviderError(kind)))
    services.events.bind(asyncio.get_running_loop())
    await services.jobs.start()
    try:
        out = await create_generations(
            services, GenerationCreate(model="mock-cloud"), services.providers
        )

        async def failed() -> bool:
            return await asyncio.to_thread(_status, services, out[0].id) == "failed"

        await async_wait_until(failed)
        rows = services.usage.summary().providers
        assert sum(p.requests for p in rows) == kept  # a timeout may have been accepted
    finally:
        await services.jobs.stop()


def _status(services: Services, generation_id: str) -> str:
    with services.db.session() as session:
        generation = session.get(Generation, generation_id)
        assert generation is not None
        return generation.status


@pytest.mark.anyio
async def test_llm_calls_are_metered_and_capped(tmp_path: Path) -> None:
    services = make_services(tmp_path, MockCloudProvider())
    fake_llm = FakeLLM(id="openai")
    llm = MeteredLLM(
        fake_llm,
        services.usage,
        source="enhance",
        price=lambda _m, u: u.input_tokens * 1e-6 + u.output_tokens * 4e-6,
    )
    request = ChatRequest(model="fake-large", messages=(Message.user("make this prompt better"),))
    response = await llm.complete(request)
    summary = {p.provider: p for p in services.usage.summary().providers}
    assert summary["openai"].output_tokens == response.usage.output_tokens
    assert summary["openai"].cost_usd > 0

    with services.db.session() as session:
        session.add(Connection(provider="openai", enabled=True, status="ok", monthly_cap_usd=0.0))
    with pytest.raises(ProviderError) as caught:
        await llm.for_source("director").complete(request)
    assert caught.value.code == "spend_cap"
    assert len(fake_llm.requests) == 1  # the capped call never reached the model
