"""The backend serves the built UI (D9), and the committed OpenAPI snapshot
matches the code (M0.2: the snapshot test fails on unreviewed drift)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from framestudio.app import create_app
from framestudio.cli import build_openapi
from framestudio.config import Config

from .conftest import TOKEN

REPO = Path(__file__).resolve().parents[2]
SNAPSHOT = REPO / "ui" / "src" / "api" / "openapi.json"


@pytest.fixture
def ui_client(tmp_path: Path) -> TestClient:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>FrameStudio</title><div id=root>")
    (dist / "assets" / "index-abc123.js").write_text("console.log('ui')")
    (dist / "favicon.svg").write_text("<svg/>")
    config = Config(data_dir=tmp_path / "data", token=TOKEN, ui_dist=dist)
    with TestClient(create_app(config), base_url="http://127.0.0.1:8790") as client:
        yield client


def test_index_is_public_and_not_cached(ui_client: TestClient) -> None:
    response = ui_client.get("/")
    assert response.status_code == 200
    assert "FrameStudio" in response.text
    assert response.headers["cache-control"] == "no-cache"


def test_client_routes_fall_back_to_index(ui_client: TestClient) -> None:
    for path in ("/image", "/studio/project/abc", "/settings"):
        response = ui_client.get(path)
        assert response.status_code == 200
        assert "<div id=root>" in response.text


def test_hashed_assets_are_immutable(ui_client: TestClient) -> None:
    response = ui_client.get("/assets/index-abc123.js")
    assert response.status_code == 200
    assert "immutable" in response.headers["cache-control"]
    assert ui_client.get("/favicon.svg").status_code == 200


def test_missing_files_404_instead_of_index(ui_client: TestClient) -> None:
    assert ui_client.get("/assets/index-old999.js").status_code == 404


def test_path_traversal_is_contained(ui_client: TestClient) -> None:
    for path in ("/..%2f..%2fdata%2fauth_token", "/../data/auth_token", "/%2e%2e/data/auth_token"):
        response = ui_client.get(path)
        assert TOKEN not in response.text


def test_unbuilt_ui_explains_itself(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "bun run build" in response.text


def test_openapi_snapshot_matches() -> None:
    assert SNAPSHOT.is_file(), "Missing OpenAPI snapshot: run `bun run api:gen`."
    committed = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert committed == build_openapi(), (
        "The API changed but ui/src/api/openapi.json was not regenerated. "
        "Run `bun run api:gen`, review the diff, and commit it."
    )
