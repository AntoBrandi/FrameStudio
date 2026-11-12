"""Process-level configuration: where data lives and how the server listens.

Everything here comes from ``FRAMESTUDIO_*`` environment variables or CLI flags
and is fixed for the life of the process. User-facing preferences that can
change at runtime live in the settings store (``framestudio.settings``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import platformdirs
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from framestudio import APP_NAME

DEFAULT_PORT = 8790
LOOPBACK_HOST = "127.0.0.1"


def default_data_dir() -> Path:
    """Per-user data directory.

    Linux: ``$XDG_DATA_HOME/FrameStudio`` (``~/.local/share/FrameStudio``);
    macOS: ``~/Library/Application Support/FrameStudio``;
    Windows: ``%LOCALAPPDATA%\\FrameStudio``. The desktop shell mirrors this in
    ``desktop/src/paths.ts``; keep the two in step.
    """
    return Path(platformdirs.user_data_dir(APP_NAME, appauthor=False, roaming=False))


def _split_csv(value: object) -> object:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return value


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FRAMESTUDIO_", extra="ignore")

    data_dir: Path = Field(default_factory=default_data_dir)
    #: Bind address. ``None`` means 127.0.0.1, or all interfaces when remote
    #: access is turned on in settings.
    host: str | None = None
    port: int = DEFAULT_PORT
    #: Overrides the per-install token (CI, tests, a shell that manages its own).
    token: str | None = None
    #: Extra browser origins allowed to call the API, e.g. the Vite dev server.
    allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    #: Extra host names the server may be addressed by (DNS-rebinding guard).
    allowed_hosts: Annotated[list[str], NoDecode] = Field(default_factory=list)
    #: Built UI to serve at "/". Defaults to ``ui/dist`` in a source checkout.
    ui_dist: Path | None = None
    dev: bool = False

    @field_validator("allowed_origins", "allowed_hosts", mode="before")
    @classmethod
    def _csv(cls, value: object) -> object:
        return _split_csv(value)

    @field_validator("data_dir", mode="after")
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    def resolved_ui_dist(self) -> Path:
        if self.ui_dist is not None:
            return self.ui_dist
        return Path(__file__).resolve().parents[2] / "ui" / "dist"
