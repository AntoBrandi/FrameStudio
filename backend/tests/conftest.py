from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from framestudio.app import create_app
from framestudio.config import Config

TOKEN = "t" * 43
BASE_URL = "http://127.0.0.1:8790"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def config(data_dir: Path, tmp_path: Path) -> Config:
    return Config(data_dir=data_dir, token=TOKEN, ui_dist=tmp_path / "ui-dist")


@pytest.fixture
def app(config: Config) -> FastAPI:
    return create_app(config)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url=BASE_URL) as test_client:
        yield test_client


@pytest.fixture
def authed(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url=BASE_URL, headers=AUTH) as test_client:
        yield test_client


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class LiveServer:
    def __init__(self, app: FastAPI) -> None:
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning")
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> LiveServer:
        self.thread.start()
        deadline = time.monotonic() + 15
        while not self.server.started:
            if time.monotonic() > deadline:
                raise TimeoutError("server did not start")
            time.sleep(0.02)
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=15)


@pytest.fixture
def live_server(app: FastAPI) -> Iterator[LiveServer]:
    with LiveServer(app) as server:
        yield server


def wait_until(predicate, timeout: float = 10.0, interval: float = 0.02):  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("condition not met in time")


async def async_wait_until(predicate, limit: float = 10.0, interval: float = 0.02):  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        value = await predicate()
        if value:
            return value
        await asyncio.sleep(interval)
    raise AssertionError("condition not met in time")
