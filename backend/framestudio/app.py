"""Application factory."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager

import httpx2
from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from framestudio import __version__
from framestudio.api import (
    auth,
    catalog_api,
    cinema_api,
    connections_api,
    elements_api,
    jobs,
    library,
    llm_api,
    media,
    prompts_api,
    system,
)
from framestudio.catalog import Catalog, load_catalog
from framestudio.cinema.presets import GradeJob
from framestudio.config import LOOPBACK_HOST, Config
from framestudio.connections.fake import fake_provider_transport
from framestudio.connections.registry import host_allowlist
from framestudio.connections.service import Connections
from framestudio.db import migrate
from framestudio.db.session import Database
from framestudio.events import EventBus
from framestudio.generation import GenerateHostedJob, GenerateJob, GenerateRemoteJob
from framestudio.jobs.base import JobKind
from framestudio.jobs.fake import FakeRemoteJob, FakeSleepJob
from framestudio.jobs.queue import JobQueue
from framestudio.library.assets import backfill_derived
from framestudio.library.describe import DescribeJob
from framestudio.llm.base import LLMProvider
from framestudio.llm.registry import LLMRegistry
from framestudio.media.store import MediaStore
from framestudio.net import OutboundPolicy
from framestudio.paths import DataPaths
from framestudio.providers.base import GenerationProvider
from framestudio.providers.mock import MockCloudProvider, MockProvider
from framestudio.security import AccessGuard, AccessPolicy, load_or_create_token
from framestudio.services import Services
from framestudio.settings import SettingsStore
from framestudio.ui import mount_ui
from framestudio.usage import UsageLedger
from framestudio.vault import open_vault

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
    llms: dict[str, LLMProvider] | None = None,
    catalog: Catalog | None = None,
    extra_kinds: Iterable[JobKind] = (),
    lane_limits: dict[str, int] | None = None,
    http_transport: httpx2.AsyncBaseTransport | None = None,
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
    services.providers = (
        providers
        if providers is not None
        else {
            "mock": MockProvider(services.settings),
            "mock-cloud": MockCloudProvider(services.settings),
        }
    )
    services.catalog = catalog if catalog is not None else load_catalog(user_dir=paths.user_catalog)
    services.vault = open_vault(paths.key_file, config.secrets)
    services.usage = UsageLedger(services)
    services.connections = Connections(services)
    if http_transport is None and config.fake_providers:
        http_transport = fake_provider_transport()
    services.outbound = OutboundPolicy(
        host_allowlist(), services.connections.is_enabled, http_transport
    )
    # Language models of the connections that are on; rebuilt when they change.
    services.llm_registry = LLMRegistry(services, fixed=llms)
    kinds: list[JobKind] = [
        FakeSleepJob(),
        FakeRemoteJob(),
        GenerateJob(services.providers),
        GenerateRemoteJob(services.providers),
        GenerateHostedJob(services.providers),
        DescribeJob(),
        GradeJob(),
        *extra_kinds,
    ]
    services.jobs = JobQueue(services, kinds, lane_limits)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        services.events.bind(asyncio.get_running_loop())
        await services.jobs.start()
        # Thumbnails for assets made before the library existed (M0 data).
        backfill = asyncio.create_task(asyncio.to_thread(backfill_derived, services))
        log.info("FrameStudio %s ready; data in %s", __version__, paths.root)
        try:
            yield
        finally:
            await services.jobs.stop()
            await services.outbound.aclose()
            await asyncio.wait({backfill}, timeout=5)
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
    for router in (
        system.router,
        auth.router,
        jobs.router,
        media.router,
        library.router,
        catalog_api.router,
        connections_api.router,
        llm_api.router,
        elements_api.router,
        prompts_api.router,
        cinema_api.router,
    ):
        app.include_router(router)
    mount_ui(app, config.resolved_ui_dist())
    app.add_middleware(AccessGuard, policy=policy)
    app.add_middleware(SecurityHeaders)
    return app
