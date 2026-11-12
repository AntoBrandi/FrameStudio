"""Application factory."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from framestudio import __version__
from framestudio.api import auth, jobs, media, system
from framestudio.config import LOOPBACK_HOST, Config
from framestudio.db import migrate
from framestudio.db.session import Database
from framestudio.events import EventBus
from framestudio.generation import GenerateJob
from framestudio.jobs.base import JobKind
from framestudio.jobs.fake import FakeRemoteJob, FakeSleepJob
from framestudio.jobs.queue import JobQueue
from framestudio.media.store import MediaStore
from framestudio.paths import DataPaths
from framestudio.providers.base import GenerationProvider
from framestudio.providers.mock import MockProvider
from framestudio.security import AccessGuard, AccessPolicy, load_or_create_token
from framestudio.services import Services
from framestudio.settings import SettingsStore
from framestudio.ui import mount_ui

log = logging.getLogger("framestudio")

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Resource-Policy": "same-origin",
}


class SecurityHeaders:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in _SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def default_bind_host(remote_access: bool) -> str:
    return "0.0.0.0" if remote_access else LOOPBACK_HOST  # noqa: S104 (explicit opt-in)


def resolve_bind_host(config: Config) -> str:
    """The address to listen on: the explicit host, or what settings ask for."""
    if config.host:
        return config.host
    paths = DataPaths(config.data_dir).ensure()
    db = Database(paths.database)
    try:
        migrate.upgrade(db)
        return default_bind_host(SettingsStore(db).get().remote_access)
    finally:
        db.dispose()


def _operation_id(route: APIRoute) -> str:
    return route.name


def create_app(
    config: Config | None = None,
    *,
    providers: dict[str, GenerationProvider] | None = None,
    extra_kinds: Iterable[JobKind] = (),
    lane_limits: dict[str, int] | None = None,
) -> FastAPI:
    config = config or Config()
    paths = DataPaths(config.data_dir).ensure()
    db = Database(paths.database)
    migrate.upgrade(db)
    settings = SettingsStore(db)
    current = settings.get()
    token = config.token or load_or_create_token(paths.token)
    bind_host = config.host or default_bind_host(current.remote_access)
    # Binding beyond loopback is itself an explicit opt-in to remote access.
    remote = current.remote_access or not is_loopback(bind_host)
    policy = AccessPolicy.build(
        token,
        remote_access=remote,
        allowed_origins=config.allowed_origins,
        allowed_hosts=config.allowed_hosts,
    )
    services = Services(
        config=config,
        paths=paths,
        db=db,
        settings=settings,
        events=EventBus(),
        policy=policy,
        bind_host=bind_host,
        media=MediaStore(paths.media, paths.tmp),
        remote_access_at_start=current.remote_access,
    )
    services.providers = providers if providers is not None else {"mock": MockProvider()}
    kinds: list[JobKind] = [
        FakeSleepJob(),
        FakeRemoteJob(),
        GenerateJob(services.providers),
        *extra_kinds,
    ]
    services.jobs = JobQueue(services, kinds, lane_limits)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        services.events.bind(asyncio.get_running_loop())
        await services.jobs.start()
        log.info("FrameStudio %s ready; data in %s", __version__, paths.root)
        try:
            yield
        finally:
            await services.jobs.stop()
            db.dispose()

    app = FastAPI(
        title="FrameStudio API",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,  # Swagger UI loads assets from a CDN; local-first policy (D7)
        redoc_url=None,
        openapi_url="/api/openapi.json",
        generate_unique_id_function=_operation_id,
    )
    app.state.services = services
    for router in (system.router, auth.router, jobs.router, media.router):
        app.include_router(router)
    mount_ui(app, config.resolved_ui_dist())
    app.add_middleware(AccessGuard, policy=policy)
    app.add_middleware(SecurityHeaders)
    return app
