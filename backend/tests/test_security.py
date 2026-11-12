"""M0.2: requests with no token, a wrong token or a foreign Origin are refused;
plus the Host (DNS rebinding) check, cookie sign-in and token storage."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from framestudio.app import create_app
from framestudio.config import Config
from framestudio.security import (
    COOKIE_NAME,
    AccessPolicy,
    load_or_create_token,
    normalize_origin,
    split_host_header,
)

from .conftest import AUTH, TOKEN


def test_no_token_is_refused(client: TestClient) -> None:
    response = client.get("/api/system/info")
    assert response.status_code == 401
    assert response.json()["code"] == "auth_required"
    assert response.headers["www-authenticate"].startswith("Bearer")


def test_wrong_token_is_refused(client: TestClient) -> None:
    response = client.get("/api/system/info", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401


def test_bearer_token_is_accepted(client: TestClient) -> None:
    assert client.get("/api/system/info", headers=AUTH).status_code == 200


def test_cookie_token_is_accepted(client: TestClient) -> None:
    client.cookies.set(COOKIE_NAME, TOKEN)
    assert client.get("/api/system/info").status_code == 200


def test_openapi_needs_token(client: TestClient) -> None:
    assert client.get("/api/openapi.json").status_code == 401
    assert client.get("/api/openapi.json", headers=AUTH).status_code == 200


def test_unknown_api_path_needs_token_then_404s(client: TestClient) -> None:
    assert client.get("/api/nope").status_code == 401
    assert client.get("/api/nope", headers=AUTH).status_code == 404


@pytest.mark.parametrize(
    "origin", ["http://evil.example", "https://127.0.0.1:8790", "http://127.0.0.1:9999", "null"]
)
def test_foreign_origin_is_refused_even_with_token(client: TestClient, origin: str) -> None:
    response = client.post(
        "/api/jobs/fake", json={"seconds": 1}, headers={**AUTH, "Origin": origin}
    )
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden_origin"


def test_same_origin_is_accepted(client: TestClient) -> None:
    response = client.get("/api/system/info", headers={**AUTH, "Origin": "http://127.0.0.1:8790"})
    assert response.status_code == 200


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_cross_site_fetch_without_origin_is_refused(client: TestClient, site: str) -> None:
    response = client.get("/api/system/info", headers={**AUTH, "Sec-Fetch-Site": site})
    assert response.status_code == 403


def test_configured_dev_origin_is_accepted(data_dir: Path, tmp_path: Path) -> None:
    config = Config(
        data_dir=data_dir,
        token=TOKEN,
        ui_dist=tmp_path / "ui",
        allowed_origins=["http://localhost:5173"],
    )
    with TestClient(create_app(config), base_url="http://127.0.0.1:8790") as test_client:
        response = test_client.get(
            "/api/system/info", headers={**AUTH, "Origin": "http://localhost:5173"}
        )
        assert response.status_code == 200


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8790", "192.168.1.20:8790"])
def test_unknown_host_is_refused_everywhere(client: TestClient, host: str) -> None:
    for path in ("/api/health", "/", "/api/system/info"):
        response = client.get(path, headers={**AUTH, "Host": host})
        assert response.status_code == 403, path
        assert response.json()["code"] == "forbidden_host"


@pytest.mark.parametrize("host", ["localhost:8790", "127.0.0.1", "[::1]:8790", "app.localhost"])
def test_loopback_hosts_are_accepted(client: TestClient, host: str) -> None:
    assert client.get("/api/health", headers={"Host": host}).status_code == 200


def test_remote_access_allows_ip_literals_but_not_names(data_dir: Path, tmp_path: Path) -> None:
    config = Config(data_dir=data_dir, token=TOKEN, ui_dist=tmp_path / "ui", host="0.0.0.0")
    with TestClient(create_app(config), base_url="http://127.0.0.1:8790") as test_client:
        lan = test_client.get("/api/system/info", headers={**AUTH, "Host": "192.168.1.20:8790"})
        assert lan.status_code == 200
        rebinding = test_client.get("/api/health", headers={"Host": "evil.example"})
        assert rebinding.status_code == 403
        # Remote access is still behind the token.
        anonymous = test_client.get("/api/system/info", headers={"Host": "192.168.1.20:8790"})
        assert anonymous.status_code == 401


def test_login_link_sets_strict_httponly_cookie(client: TestClient) -> None:
    response = client.get(
        "/auth/login", params={"token": TOKEN, "next": "/image"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/image"
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert client.get("/api/system/info").status_code == 200


@pytest.mark.parametrize("target", ["//evil.example", "https://evil.example", "/\\evil"])
def test_login_never_redirects_off_site(client: TestClient, target: str) -> None:
    response = client.get(
        "/auth/login", params={"token": TOKEN, "next": target}, follow_redirects=False
    )
    assert response.headers["location"] == "/"


def test_login_link_with_wrong_token(client: TestClient) -> None:
    response = client.get("/auth/login", params={"token": "bad"}, follow_redirects=False)
    assert response.status_code == 401
    assert "set-cookie" not in response.headers


def test_session_endpoint(client: TestClient) -> None:
    assert client.post("/api/auth/session", json={"token": "bad"}).status_code == 401
    assert client.get("/api/system/info").status_code == 401
    assert client.post("/api/auth/session", json={"token": TOKEN}).status_code == 204
    assert client.get("/api/system/info").status_code == 200
    assert client.delete("/api/auth/session").status_code == 204
    client.cookies.clear()
    assert client.get("/api/system/info").status_code == 401


def test_session_endpoint_refuses_foreign_origin(client: TestClient) -> None:
    response = client.post(
        "/api/auth/session", json={"token": TOKEN}, headers={"Origin": "http://evil.example"}
    )
    assert response.status_code == 403


def test_token_file_is_created_private_and_reused(tmp_path: Path) -> None:
    path = tmp_path / "auth_token"
    first = load_or_create_token(path)
    assert len(first) >= 32
    assert load_or_create_token(path) == first
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_install_token_is_used_when_not_overridden(data_dir: Path, tmp_path: Path) -> None:
    config = Config(data_dir=data_dir, ui_dist=tmp_path / "ui")
    with TestClient(create_app(config), base_url="http://127.0.0.1:8790") as test_client:
        token = (data_dir / "auth_token").read_text().strip()
        ok = test_client.get("/api/system/info", headers={"Authorization": f"Bearer {token}"})
        assert ok.status_code == 200


def test_security_headers(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cross-origin-resource-policy"] == "same-origin"


def test_origin_and_host_parsing() -> None:
    assert normalize_origin("HTTP://LocalHost:80") == "http://localhost"
    assert normalize_origin("https://[::1]:8790") == "https://[::1]:8790"
    assert normalize_origin("file://x") is None
    assert split_host_header("[::1]:8790") == "::1"
    assert split_host_header("Example.com:80") == "example.com"
    policy = AccessPolicy.build("x" * 43, allowed_hosts=["studio.lan"])
    assert policy.host_allowed("studio.lan:8790")
    assert not policy.host_allowed(None)
