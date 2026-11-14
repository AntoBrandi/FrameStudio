"""M2.4: connected providers become language models; a default model and
per-task choices decide which one answers."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from framestudio.app import create_app
from framestudio.config import Config
from framestudio.llm.anthropic_api import AnthropicLLM
from framestudio.llm.base import ChatRequest, Message
from framestudio.llm.metered import MeteredLLM
from framestudio.providers.base import ProviderError

from .conftest import AUTH, BASE_URL, TOKEN
from .contracts.llm_wire import load


def overview(client: TestClient, **params: Any) -> dict[str, Any]:
    response = client.get("/api/llm", params=params)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def effective(client: TestClient) -> dict[str, str | None]:
    return {t["task"]: t["effective"] for t in overview(client)["tasks"]}


@pytest.fixture
def offline(tmp_path: Path) -> Iterator[TestClient]:
    """Fake providers: connected providers answer with the scripted model."""
    config = Config(
        data_dir=tmp_path / "data", token=TOKEN, ui_dist=tmp_path / "ui", fake_providers=True
    )
    with TestClient(create_app(config), base_url=BASE_URL, headers=AUTH) as client:
        yield client


def connect(client: TestClient, provider: str) -> None:
    response = client.put(
        f"/api/connections/{provider}", json={"api_key": f"fs-test-ok-{provider}-123456"}
    )
    assert response.status_code == 200, response.text


def test_nothing_connected_means_no_model(offline: TestClient) -> None:
    data = overview(offline)
    assert data["providers"] == []
    assert all(task["effective"] is None for task in data["tasks"])
    registry = offline.app.state.services.llm_registry  # type: ignore[attr-defined]
    with pytest.raises(ProviderError) as caught:
        registry.for_task("prompt_help")
    assert caught.value.code == "no_llm"


def test_connected_providers_list_their_models(offline: TestClient) -> None:
    connect(offline, "openai")
    connect(offline, "anthropic")
    data = overview(offline)
    providers = {p["provider"]: p for p in data["providers"]}
    assert set(providers) == {"anthropic", "openai"}
    claude = providers["anthropic"]["models"][0]
    assert claude["ref"] == "anthropic/claude-opus-5-5"
    assert claude["usd_per_mtok_in"] == 4.0 and claude["usd_per_mtok_out"] == 20.0
    # Automatic prefers Claude Opus 5.5 when Anthropic is connected.
    assert effective(offline) == {
        "default": "anthropic/claude-opus-5-5",
        "prompt_help": "anthropic/claude-opus-5-5",
        "captions": "anthropic/claude-opus-5-5",
        "director": "anthropic/claude-opus-5-5",
    }


def test_task_choices_and_fallbacks(offline: TestClient) -> None:
    connect(offline, "openai")
    connect(offline, "anthropic")
    offline.patch(
        "/api/settings",
        json={"llm_default": "openai/gpt-5", "llm_director": "anthropic/claude-opus-5-5"},
    )
    assert effective(offline) == {
        "default": "openai/gpt-5",
        "prompt_help": "openai/gpt-5",
        "captions": "openai/gpt-5",
        "director": "anthropic/claude-opus-5-5",
    }
    # Turning a provider off sends its tasks to the default.
    offline.put("/api/connections/anthropic", json={"enabled": False})
    assert effective(offline)["director"] == "openai/gpt-5"
    offline.put("/api/connections/anthropic", json={"enabled": True})
    offline.delete("/api/connections/openai")
    assert effective(offline)["default"] == "anthropic/claude-opus-5-5"  # automatic again
    # Clearing a choice goes back to the default.
    offline.patch("/api/settings", json={"llm_director": ""})
    tasks = {t["task"]: t for t in overview(offline)["tasks"]}
    assert tasks["director"]["choice"] == ""


@pytest.mark.parametrize("choice", ["gpt-5", "OpenAI/gpt-5", "openai/", "openai/gpt 5", "x" * 200])
def test_malformed_choices_are_refused(offline: TestClient, choice: str) -> None:
    assert offline.patch("/api/settings", json={"llm_default": choice}).status_code == 422


def test_a_task_answers_metered_as_that_task(offline: TestClient) -> None:
    connect(offline, "anthropic")
    services = offline.app.state.services  # type: ignore[attr-defined]
    llm, model = services.llm_registry.for_task("director")
    assert isinstance(llm, MeteredLLM) and llm.source == "director"
    response = asyncio.run(
        llm.complete(ChatRequest(model=model, messages=(Message.user("Plan scene 3"),)))
    )
    assert "Plan scene 3" in response.text
    usage = offline.get("/api/usage").json()
    [row] = usage["providers"]
    assert row["provider"] == "anthropic" and row["requests"] == 1 and row["cost_usd"] > 0


# ---------------------------------------------------------------- real adapters


class Provider:
    """Key checks answered, then the Anthropic model list from the fixture."""

    def __init__(self, list_status: int = 200) -> None:
        self.list_status = list_status
        self.paths: list[str] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.paths.append(f"{request.method} {request.url.host}{request.url.path}")
        if request.url.path == "/v1/models" and request.url.params.get("limit") == "1":  # key check
            return httpx2.Response(200, json={"data": []})
        if request.url.path == "/v1/models":
            if self.list_status != 200:
                return httpx2.Response(
                    self.list_status,
                    json={"error": {"type": "overloaded_error", "message": "busy"}},
                )
            return httpx2.Response(200, json=load("anthropic", "models")[0].body)
        return httpx2.Response(404, json={})


def real_app(tmp_path: Path, provider: Provider) -> FastAPI:
    config = Config(data_dir=tmp_path / "data", token=TOKEN, ui_dist=tmp_path / "ui")
    return create_app(config, http_transport=httpx2.MockTransport(provider))


def test_real_adapters_use_the_guarded_transport(tmp_path: Path) -> None:
    provider = Provider()
    with TestClient(real_app(tmp_path, provider), base_url=BASE_URL, headers=AUTH) as client:
        assert (
            client.put(
                "/api/connections/anthropic", json={"api_key": "sk-ant-real-key-123456"}
            ).status_code
            == 200
        )
        services = client.app.state.services  # type: ignore[attr-defined]
        assert isinstance(services.llms["anthropic"].inner, AnthropicLLM)
        data = overview(client)
        assert [m["ref"] for m in data["providers"][0]["models"]] == ["anthropic/claude-opus-5-5"]
        assert provider.paths[-1] == "GET api.anthropic.com/v1/models"
        # Turned off: the adapter is dropped, and its transport would refuse anyway.
        client.put("/api/connections/anthropic", json={"enabled": False})
        assert "anthropic" not in services.llms
        transport = services.outbound.transport("anthropic")

        async def call() -> None:
            async with httpx2.AsyncClient(transport=transport) as http:
                await http.get("https://api.anthropic.com/v1/models")

        with pytest.raises(httpx2.RequestError, match="turned off"):
            asyncio.run(call())


def test_a_model_list_failure_is_reported_not_fatal(tmp_path: Path) -> None:
    provider = Provider(list_status=529)
    with TestClient(real_app(tmp_path, provider), base_url=BASE_URL, headers=AUTH) as client:
        client.put("/api/connections/anthropic", json={"api_key": "sk-ant-real-key-123456"})
        [entry] = overview(client)["providers"]
        assert entry["models"] == [] and "busy" in entry["error"].lower()
        # Even without a list, the preferred model is the automatic choice.
        assert effective(client)["default"] == "anthropic/claude-opus-5-5"
        provider.list_status = 200
        assert overview(client, refresh=True)["providers"][0]["error"] is None


def test_overview_never_carries_keys(offline: TestClient) -> None:
    connect(offline, "google")
    text = json.dumps(overview(offline))
    assert "fs-test-ok" not in text
