"""Typed settings stored in SQLite, one row per field."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from framestudio.db.models import Setting
from framestudio.db.session import Database


class AppSettings(BaseModel):
    """Every user-changeable setting with its default."""

    # Responses always carry every field, so the schema marks them required.
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)

    theme: Literal["dark", "light", "system"] = "dark"
    language: str = Field(default="en", min_length=2, max_length=16)
    #: Listen on all interfaces (behind the same token). Applies after a restart.
    remote_access: bool = False
    #: Notify when a job finishes while the window is in the background.
    notify_on_job_finish: bool = True
    #: Mock engine knobs (test double that ships with the product, D8).
    mock_latency_seconds: float = Field(default=2.0, ge=0, le=120)
    mock_failure_rate: float = Field(default=0.0, ge=0, le=1)


class AppSettingsPatch(BaseModel):
    """Partial update: omitted fields keep their current value."""

    model_config = ConfigDict(extra="forbid")

    theme: Literal["dark", "light", "system"] | None = None
    language: str | None = Field(default=None, min_length=2, max_length=16)
    remote_access: bool | None = None
    notify_on_job_finish: bool | None = None
    mock_latency_seconds: float | None = Field(default=None, ge=0, le=120)
    mock_failure_rate: float | None = Field(default=None, ge=0, le=1)


class SettingsStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self) -> AppSettings:
        with self._db.session() as session:
            rows = session.scalars(select(Setting)).all()
        stored: dict[str, Any] = {
            row.key: row.value for row in rows if row.key in AppSettings.model_fields
        }
        defaults = AppSettings().model_dump()
        merged = {**defaults, **stored}
        try:
            return AppSettings.model_validate(merged)
        except ValueError:
            # A stored value no longer validates (e.g. a narrowed enum): drop
            # stored values one by one rather than refusing to start.
            valid = dict(defaults)
            for key, value in stored.items():
                try:
                    AppSettings.model_validate({**valid, key: value})
                except ValueError:
                    continue
                valid[key] = value
            return AppSettings.model_validate(valid)

    def update(self, patch: AppSettingsPatch) -> AppSettings:
        changes = patch.model_dump(exclude_unset=True)
        changes = {k: v for k, v in changes.items() if v is not None}
        merged = AppSettings.model_validate({**self.get().model_dump(), **changes})
        with self._db.session() as session:
            for key in changes:
                row = session.get(Setting, key)
                value = getattr(merged, key)
                if row is None:
                    session.add(Setting(key=key, value=value))
                else:
                    row.value = value
        return merged
