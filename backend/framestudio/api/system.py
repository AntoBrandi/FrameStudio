"""Health, system information, settings."""

from __future__ import annotations

import platform
import sys
from datetime import datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from framestudio import API_VERSION, APP_SLUG, __version__
from framestudio.api.deps import ServicesDep
from framestudio.db import migrate
from framestudio.media.tools import ffmpeg_path, ffprobe_path
from framestudio.settings import AppSettings, AppSettingsPatch

router = APIRouter(prefix="/api")


class Health(BaseModel):
    status: Literal["ok"]
    app: Literal["framestudio"]
    version: str
    api_version: int


class ToolStatus(BaseModel):
    ffmpeg: bool
    ffprobe: bool


class SystemInfo(BaseModel):
    app: Literal["framestudio"]
    version: str
    api_version: int
    python: str
    os: Literal["linux", "windows", "macos", "other"]
    platform: str
    data_dir: str
    database: str
    schema_revision: str | None
    schema_head: str | None
    bind_host: str
    port: int
    remote_access: bool
    restart_required: bool
    dev_mode: bool
    tools: ToolStatus
    started_at: datetime


def _os_name() -> Literal["linux", "windows", "macos", "other"]:
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "other"


@router.get("/health", tags=["system"])
def health() -> Health:
    """Liveness probe. Public: the shell polls it before it has a token."""
    return Health(status="ok", app=APP_SLUG, version=__version__, api_version=API_VERSION)


@router.get("/system/info", tags=["system"])
def system_info(services: ServicesDep) -> SystemInfo:
    settings = services.settings.get()
    return SystemInfo(
        app=APP_SLUG,
        version=__version__,
        api_version=API_VERSION,
        python=platform.python_version(),
        os=_os_name(),
        platform=platform.platform(terse=True),
        data_dir=str(services.paths.root),
        database=str(services.paths.database),
        schema_revision=migrate.current_revision(services.db),
        schema_head=migrate.head_revision(),
        bind_host=services.bind_host,
        port=services.config.port,
        remote_access=settings.remote_access,
        restart_required=settings.remote_access != services.remote_access_at_start,
        dev_mode=services.config.dev,
        tools=ToolStatus(ffmpeg=ffmpeg_path() is not None, ffprobe=ffprobe_path() is not None),
        started_at=services.started_at,
    )


@router.get("/settings", tags=["settings"])
def get_settings(services: ServicesDep) -> AppSettings:
    return services.settings.get()


@router.patch("/settings", tags=["settings"])
def patch_settings(patch: AppSettingsPatch, services: ServicesDep) -> AppSettings:
    updated = services.settings.update(patch)
    services.events.publish("settings.updated", updated.model_dump(mode="json"))
    return updated
