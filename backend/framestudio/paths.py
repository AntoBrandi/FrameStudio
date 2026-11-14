"""Layout of the data directory."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DataPaths:
    root: Path

    @property
    def database(self) -> Path:
        return self.root / "framestudio.db"

    @property
    def token(self) -> Path:
        return self.root / "auth_token"

    @property
    def key_file(self) -> Path:
        """Encrypted API keys when the OS keyring is not used (framestudio.vault)."""
        return self.root / "secrets.enc"

    @property
    def user_catalog(self) -> Path:
        """Catalog entries the user added, e.g. imported from fal.ai (M2.10)."""
        return self.root / "catalog"

    @property
    def media(self) -> Path:
        return self.root / "media"

    @property
    def tmp(self) -> Path:
        return self.root / "tmp"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    def ensure(self) -> DataPaths:
        for directory in (self.root, self.media, self.tmp, self.logs):
            directory.mkdir(parents=True, exist_ok=True)
        return self
