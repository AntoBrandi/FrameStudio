"""The long-lived objects one backend process owns, passed to routes and jobs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from framestudio.config import Config
from framestudio.db.session import Database
from framestudio.events import EventBus
from framestudio.paths import DataPaths
from framestudio.security import AccessPolicy
from framestudio.settings import SettingsStore

if TYPE_CHECKING:
    from framestudio.jobs.queue import JobQueue
    from framestudio.media.store import MediaStore
    from framestudio.providers.base import GenerationProvider


@dataclass
class Services:
    config: Config
    paths: DataPaths
    db: Database
    settings: SettingsStore
    events: EventBus
    policy: AccessPolicy
    bind_host: str
    media: MediaStore
    jobs: JobQueue = field(init=False)
    providers: dict[str, GenerationProvider] = field(default_factory=dict)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    #: Remote access as it was when this process started (changes need a restart).
    remote_access_at_start: bool = False
