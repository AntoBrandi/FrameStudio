"""M2.9: Auto picks the model that fits the inputs and can run (router table)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from framestudio.app import create_app
from framestudio.config import Config
from framestudio.providers.mock import (
    MockCloudProvider,
    MockProvider,
    OfflineHostedProvider,
    OfflineTaskProvider,
)
from framestudio.router import RouteRequest, route

from .conftest import AUTH, BASE_URL, TOKEN


def services_with(tmp_path: Path, *connected: str) -> Any:
    providers: dict[str, Any] = {"mock": MockProvider(), "mock-cloud": MockCloudProvider()}
    if "google" in connected:
        providers["google"] = OfflineHostedProvider("google")
    if "byteplus" in connected:
        providers["byteplus"] = OfflineTaskProvider("byteplus")
    config = Config(data_dir=tmp_path / "data", token=TOKEN, ui_dist=tmp_path / "ui")
    return create_app(config, providers=providers).state.services


TABLE = [
    # connected, request, expected model (None: nothing fits and can run)
    ((), RouteRequest(mode="image"), "mock"),
    ((), RouteRequest(mode="image", references=2), "mock-cloud"),
    ((), RouteRequest(mode="image", references=5), None),
    ((), RouteRequest(mode="video"), "mock"),
    # The mock engine starts clips from pictures too (M5.7), so this works offline.
    ((), RouteRequest(mode="video", start_frame=True), "mock"),
    ((), RouteRequest(mode="video", start_frame=True, end_frame=True), "mock"),
    ((), RouteRequest(mode="video", references=9), None),
    (("google",), RouteRequest(mode="image"), "nano-banana-pro"),
    (("google",), RouteRequest(mode="image", references=14), "nano-banana-pro"),
    (("google",), RouteRequest(mode="image", references=15), None),
    (("google",), RouteRequest(mode="video"), "mock"),
    (("byteplus",), RouteRequest(mode="video"), "seedance-2-0"),
    (("byteplus",), RouteRequest(mode="video", start_frame=True), "seedance-2-0"),
    (("byteplus",), RouteRequest(mode="video", start_frame=True, end_frame=True), "seedance-2-0"),
    (("byteplus",), RouteRequest(mode="video", references=9), "seedance-2-0"),
    (("byteplus",), RouteRequest(mode="video", references=20), "seedance-2-5"),
    (("byteplus",), RouteRequest(mode="video", references=31), None),
    (("byteplus",), RouteRequest(mode="image"), "mock"),
    (("google", "byteplus"), RouteRequest(mode="image", references=1), "nano-banana-pro"),
    (("google", "byteplus"), RouteRequest(mode="video", start_frame=True), "seedance-2-0"),
]


@pytest.mark.parametrize(("connected", "request_", "expected"), TABLE)
def test_router_table(
    tmp_path: Path, connected: tuple[str, ...], request_: RouteRequest, expected: str | None
) -> None:
    result = route(services_with(tmp_path, *connected), request_)
    assert result.model == expected, result.reason
    if expected is None:
        assert result.reason.startswith("No ")
    else:
        assert result.reason.startswith(next(o.name for o in result.options if o.id == expected))


def test_unavailable_models_that_would_fit_are_named(tmp_path: Path) -> None:
    result = route(services_with(tmp_path), RouteRequest(mode="video", references=9))
    assert "Connect a provider for Seedance 2.0" in result.reason
    options = {o.id: o for o in result.options}
    assert options["mock"].misfit == "takes at most 0 references"
    assert options["seedance-2-0"].misfit is None and not options["seedance-2-0"].available


def test_unranked_models_are_never_picked(tmp_path: Path) -> None:
    services = services_with(tmp_path, "google")
    services.catalog.get("nano-banana-pro").auto_rank = None
    services.catalog.get("nano-banana").auto_rank = None
    assert route(services, RouteRequest(mode="image")).model == "mock"


@pytest.fixture
def client(tmp_path: Path) -> Any:
    providers = {
        "mock": MockProvider(),
        "mock-cloud": MockCloudProvider(),
        "google": OfflineHostedProvider("google"),
    }
    config = Config(data_dir=tmp_path / "data", token=TOKEN, ui_dist=tmp_path / "ui")
    with TestClient(
        create_app(config, providers=providers), base_url=BASE_URL, headers=AUTH
    ) as test_client:
        yield test_client


def test_auto_through_the_api(client: TestClient) -> None:
    routed = client.get("/api/route", params={"mode": "image", "references": 1}).json()
    assert routed["model"] == "nano-banana-pro"
    estimate = client.post(
        "/api/generations/estimate", json={"model": "auto", "mode": "image"}
    ).json()
    assert estimate["ok"] and estimate["model"] == "nano-banana-pro"
    created = client.post(
        "/api/generations", json={"model": "auto", "mode": "image", "prompt": "a door"}
    )
    assert created.status_code == 201, created.text
    assert (
        created.json()[0]["model_id"] == "nano-banana-pro"
    )  # the stored spec names the real model
    nine = [f"r{i}" for i in range(9)]  # more references than any offline model takes
    nothing = client.post(
        "/api/generations", json={"model": "auto", "mode": "video", "references": nine}
    )
    assert nothing.status_code == 422
    assert "No connected model" in nothing.json()["detail"]
    blocked = client.post(
        "/api/generations/estimate", json={"model": "auto", "mode": "video", "references": nine}
    ).json()
    assert not blocked["ok"] and blocked["model"] is None
