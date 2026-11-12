"""M0.1 health test, M0.2 settings store, /system/info and data directory."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from framestudio import __version__
from framestudio.config import Config, default_data_dir

REPO = Path(__file__).resolve().parents[2]


def test_health_is_public(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app"] == "framestudio"
    assert body["version"] == __version__


def test_system_info(authed: TestClient, data_dir: Path) -> None:
    info = authed.get("/api/system/info").json()
    assert info["data_dir"] == str(data_dir)
    assert info["schema_revision"] == info["schema_head"]
    assert info["bind_host"] == "127.0.0.1"
    assert info["remote_access"] is False
    assert info["restart_required"] is False
    assert set(info["tools"]) == {"ffmpeg", "ffprobe"}


def test_data_dir_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FRAMESTUDIO_DATA_DIR", str(tmp_path / "elsewhere"))
    assert Config().data_dir == (tmp_path / "elsewhere").resolve()


def test_default_data_dir_is_per_user() -> None:
    path = default_data_dir()
    assert path.name == "FrameStudio"
    assert path.is_absolute()


def test_csv_env_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRAMESTUDIO_ALLOWED_ORIGINS", "http://localhost:5173, http://a.test")
    assert Config().allowed_origins == ["http://localhost:5173", "http://a.test"]


def test_fresh_install_creates_database(authed: TestClient, data_dir: Path) -> None:
    assert (data_dir / "framestudio.db").is_file()
    assert (data_dir / "media").is_dir()


def test_settings_defaults_and_patch(authed: TestClient) -> None:
    settings = authed.get("/api/settings").json()
    assert settings["theme"] == "dark"
    updated = authed.patch("/api/settings", json={"theme": "light"}).json()
    assert updated["theme"] == "light"
    assert authed.get("/api/settings").json()["theme"] == "light"


def test_settings_survive_restart(config: Config) -> None:
    from framestudio.app import create_app

    with TestClient(create_app(config), base_url="http://127.0.0.1:8790") as first:
        first.patch(
            "/api/settings",
            json={"language": "de"},
            headers={"Authorization": f"Bearer {config.token}"},
        )
    with TestClient(create_app(config), base_url="http://127.0.0.1:8790") as second:
        body = second.get(
            "/api/settings", headers={"Authorization": f"Bearer {config.token}"}
        ).json()
        assert body["language"] == "de"


@pytest.mark.parametrize(
    "patch",
    [{"theme": "neon"}, {"mock_failure_rate": 2}, {"unknown_key": 1}, {"language": "x"}],
)
def test_settings_reject_invalid(authed: TestClient, patch: dict[str, object]) -> None:
    assert authed.patch("/api/settings", json=patch).status_code == 422


def test_remote_access_needs_restart(authed: TestClient) -> None:
    authed.patch("/api/settings", json={"remote_access": True})
    info = authed.get("/api/system/info").json()
    assert info["remote_access"] is True
    assert info["restart_required"] is True


def test_versions_agree() -> None:
    """Root package.json is the single source; the backend mirrors it."""
    root = json.loads((REPO / "package.json").read_text())["version"]
    backend = tomllib.loads((REPO / "backend" / "pyproject.toml").read_text())["project"]["version"]
    assert root == backend
    assert re.fullmatch(r"\d+\.\d+\.\d+(-[\w.]+)?", root)
