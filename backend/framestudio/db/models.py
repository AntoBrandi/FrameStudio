"""ORM models. Every schema change ships with an Alembic migration and an
upgrade test (definition of done, every step)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from framestudio.db.base import Base, UTCDateTime, utcnow


def new_id() -> str:
    return uuid.uuid4().hex


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(64))
    lane: Mapped[str] = mapped_column(String(16))
    #: queued | submitting | running | succeeded | failed | cancelled | interrupted
    state: Mapped[str] = mapped_column(String(16), default="queued")
    title: Mapped[str] = mapped_column(String(200), default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    #: Remote task handle, stored before the first poll so a restart resumes
    #: polling instead of submitting (and paying for) the task again.
    provider_task_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (Index("ix_jobs_state_lane_created", "state", "lane", "created_at"),)


class Generation(Base):
    __tablename__ = "generations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    batch_id: Mapped[str] = mapped_column(String(32), index=True)
    batch_index: Mapped[int] = mapped_column(Integer, default=0)
    mode: Mapped[str] = mapped_column(String(16))  # image | video
    model_id: Mapped[str] = mapped_column(String(128))
    variant: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider: Mapped[str] = mapped_column(String(64))
    prompt: Mapped[str] = mapped_column(Text, default="")
    #: The prompt as tokens (M4.3); ``prompt`` is its plain-text rendering.
    prompt_tokens: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    #: What the model was given (M4.4): the prompt it saw, the pictures with
    #: their labels and the element versions (``references.Resolved``).
    resolved: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    #: Library assets given to the model as references (M2.5), in order.
    reference_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    #: Image to video (M2.8): the first frame, and optionally the last.
    start_frame_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    end_frame_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: queued | running | succeeded | failed | cancelled | interrupted
    status: Mapped[str] = mapped_column(String(16), default="queued")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    parent_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("assets.id", ondelete="SET NULL", use_alter=True), nullable=True
    )
    #: Where the result lands (M1): a project and/or a folder, or the global library.
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    folder_id: Mapped[str | None] = mapped_column(
        ForeignKey("folders.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(16))  # image | video | audio
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    #: Path relative to the media directory, always with forward slashes.
    path: Mapped[str] = mapped_column(String(300))
    mime: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    fps: Mapped[float | None] = mapped_column(Float, nullable=True)
    has_audio: Mapped[bool | None] = mapped_column(nullable=True)
    video_codec: Mapped[str | None] = mapped_column(String(32), nullable=True)
    audio_codec: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: Derived files (relative to the media directory): WebP thumbnail, video
    #: poster, short muted hover preview.
    thumb_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    poster_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    preview_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    #: uploaded | generated
    provenance: Mapped[str] = mapped_column(
        String(16), default="generated", server_default="generated"
    )
    original_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_generation_id: Mapped[str | None] = mapped_column(
        ForeignKey("generations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    folder_id: Mapped[str | None] = mapped_column(
        ForeignKey("folders.id", ondelete="SET NULL"), nullable=True, index=True
    )
    favorite: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    notes: Mapped[str] = mapped_column(Text, default="", server_default="")
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    last_viewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_downloaded_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    __table_args__ = (Index("ix_assets_deleted_created", "deleted_at", "created_at"),)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))
    #: Names exported folders; unique, stable across renames unless changed.
    slug: Mapped[str] = mapped_column(String(140), unique=True)
    description: Mapped[str] = mapped_column(String(140), default="", server_default="")
    cover_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("assets.id", ondelete="SET NULL", use_alter=True), nullable=True
    )
    brief: Mapped[str] = mapped_column(Text, default="", server_default="")
    #: Describe new images with the captions model (M2.6); sends them to it.
    describe_images: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class Folder(Base):
    __tablename__ = "folders"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    #: ``None`` for folders in the global library.
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    #: Reserved for nesting later; unused in M1.
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("folders.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(80))
    color: Mapped[str] = mapped_column(String(16), default="tungsten")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Connection(Base):
    """A hosted provider you connected. Holds where its key is stored, never
    the key itself (framestudio.vault)."""

    __tablename__ = "connections"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    #: Name of the key in the store, and which store (keyring | file | memory).
    secret_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    secret_backend: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: The key's last four characters, to tell keys apart.
    key_hint: Mapped[str | None] = mapped_column(String(8), nullable=True)
    #: ok | refused | unreachable | unchecked | missing
    status: Mapped[str] = mapped_column(String(16), default="unchecked")
    status_message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    validated_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    monthly_cap_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class UsageRecord(Base):
    """One billable request to a hosted provider: what it used and what it cost
    (estimated). Kept when the generation or connection is deleted."""

    __tablename__ = "usage"
    __table_args__ = (Index("ix_usage_provider_month", "provider", "month"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(120), default="")
    #: composer | director | enhance | describe | ...
    source: Mapped[str] = mapped_column(String(32), default="composer")
    #: Calendar month in UTC, YYYY-MM; caps are monthly.
    month: Mapped[str] = mapped_column(String(7))
    requests: Mapped[int] = mapped_column(Integer, default=1)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    images: Mapped[int] = mapped_column(Integer, default=0)
    video_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    generation_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ElementFolder(Base):
    """A folder of elements (M4.1), separate from asset folders."""

    __tablename__ = "element_folders"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    #: ``None`` for global element folders.
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(80))
    color: Mapped[str] = mapped_column(String(16), default="tungsten")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Element(Base):
    """A character, location or prop referenced by @handle (M4)."""

    __tablename__ = "elements"
    __table_args__ = (Index("ux_elements_scope_handle", "scope_key", "handle", unique=True),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    #: ``None`` for global elements; project elements go with their project.
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    #: The project id, or "global": handles are unique per scope.
    scope_key: Mapped[str] = mapped_column(String(32))
    folder_id: Mapped[str | None] = mapped_column(
        ForeignKey("element_folders.id", ondelete="SET NULL"), nullable=True
    )
    handle: Mapped[str] = mapped_column(String(64))
    #: character | location | prop | auto
    category: Mapped[str] = mapped_column(String(16), default="auto")
    name: Mapped[str] = mapped_column(String(120))
    version: Mapped[int] = mapped_column(Integer, default=1)
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    #: draft | ready
    status: Mapped[str] = mapped_column(String(16), default="draft")
    properties: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class ElementMedia(Base):
    """A picture or clip of an element; the first by position is primary."""

    __tablename__ = "element_media"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    element_id: Mapped[str] = mapped_column(
        ForeignKey("elements.id", ondelete="CASCADE"), index=True
    )
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    #: primary | angle | sheet | turntable (M4.6 adds generated roles)
    role: Mapped[str] = mapped_column(String(16), default="angle")
    position: Mapped[int] = mapped_column(Integer, default=0)


class ElementUse(Base):
    """A generation that referenced an element ("Used in")."""

    __tablename__ = "element_uses"

    element_id: Mapped[str] = mapped_column(
        ForeignKey("elements.id", ondelete="CASCADE"), primary_key=True
    )
    generation_id: Mapped[str] = mapped_column(
        ForeignKey("generations.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
