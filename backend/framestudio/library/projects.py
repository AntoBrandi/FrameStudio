"""Projects and folders (plan steps M1.2, M1.3, M1.7)."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from framestudio.db.base import utcnow
from framestudio.db.models import Asset, Folder, Project
from framestudio.services import Services

BRIEF_SECTIONS = ("Context", "Narrative", "Visuals", "Production", "Status", "Resources", "Notes")
BRIEF_TEMPLATE = "\n\n".join(f"## {section}\n" for section in BRIEF_SECTIONS)
FOLDER_COLORS = ("tungsten", "daylight", "green", "rose", "violet", "teal", "amber", "slate")
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FolderColor = Literal["tungsten", "daylight", "green", "rose", "violet", "teal", "amber", "slate"]


class ConflictError(ValueError):
    pass


def slugify(value: str, max_length: int = 60) -> str:
    ascii_text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug[:max_length].rstrip("-") or "project"


# ---------------------------------------------------------------- projects


class ProjectOut(BaseModel):
    id: str
    name: str
    slug: str
    description: str
    cover_asset_id: str | None
    #: The chosen cover, or the newest asset's thumbnail.
    cover_url: str | None
    asset_count: int
    #: New images here are described by the captions model (M2.6).
    describe_images: bool
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class ProjectDetail(ProjectOut):
    brief: str


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=140)

    @field_validator("name")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    slug: str | None = Field(default=None, min_length=1, max_length=60)
    description: str | None = Field(default=None, max_length=140)
    cover_asset_id: str | None = None
    brief: str | None = Field(default=None, max_length=200_000)
    describe_images: bool | None = None

    @field_validator("slug")
    @classmethod
    def _slug(cls, value: str | None) -> str | None:
        if value is not None and not SLUG_PATTERN.fullmatch(value):
            raise ValueError("use lowercase letters, digits and single hyphens")
        return value


def _unique_slug(session: Session, base: str, exclude_id: str | None = None) -> str:
    candidate, n = base, 2
    while True:
        query = select(Project.id).where(Project.slug == candidate)
        if exclude_id:
            query = query.where(Project.id != exclude_id)
        if session.scalar(query) is None:
            return candidate
        candidate = f"{base[:55]}-{n}"
        n += 1


def _project_out(session: Session, project: Project, detail: bool = False) -> ProjectOut:
    live = (Asset.project_id == project.id, Asset.deleted_at.is_(None))
    count = session.scalar(select(func.count()).where(*live)) or 0
    cover_id = project.cover_asset_id
    if cover_id is None:
        cover_id = session.scalar(
            select(Asset.id)
            .where(*live, Asset.thumb_path.is_not(None))
            .order_by(Asset.created_at.desc())
        )
    data = {
        "id": project.id,
        "name": project.name,
        "slug": project.slug,
        "description": project.description,
        "cover_asset_id": project.cover_asset_id,
        "cover_url": f"/api/assets/{cover_id}/thumb" if cover_id else None,
        "asset_count": count,
        "describe_images": bool(project.describe_images),
        "created_at": project.created_at,
        "updated_at": project.updated_at,
        "deleted_at": project.deleted_at,
    }
    if detail:
        return ProjectDetail(**data, brief=project.brief)
    return ProjectOut(**data)


def list_projects(
    services: Services,
    q: str | None = None,
    sort: Literal["updated", "created", "name"] = "updated",
    trashed: bool = False,
) -> list[ProjectOut]:
    with services.db.session() as session:
        query = select(Project).where(
            Project.deleted_at.is_not(None) if trashed else Project.deleted_at.is_(None)
        )
        if q:
            like = f"%{q.strip().lower()}%"
            query = query.where(
                func.lower(Project.name).like(like) | func.lower(Project.description).like(like)
            )
        order = {
            "updated": Project.updated_at.desc(),
            "created": Project.created_at.desc(),
            "name": func.lower(Project.name).asc(),
        }[sort]
        return [_project_out(session, p) for p in session.scalars(query.order_by(order)).all()]


def get_project(services: Services, project_id: str) -> ProjectDetail | None:
    with services.db.session() as session:
        project = session.get(Project, project_id)
        return _project_out(session, project, detail=True) if project else None  # type: ignore[return-value]


def create_project(services: Services, body: ProjectCreate) -> ProjectDetail:
    with services.db.session() as session:
        project = Project(
            name=body.name,
            slug=_unique_slug(session, slugify(body.name)),
            description=body.description.strip(),
            brief=BRIEF_TEMPLATE,
        )
        session.add(project)
        session.flush()
        out = _project_out(session, project, detail=True)
    services.events.publish("library.changed", {"scope": "projects"})
    return out  # type: ignore[return-value]


def patch_project(services: Services, project_id: str, patch: ProjectPatch) -> ProjectDetail | None:
    changes = patch.model_dump(exclude_unset=True)
    with services.db.session() as session:
        project = session.get(Project, project_id)
        if project is None:
            return None
        if changes.get("slug"):
            if _unique_slug(session, changes["slug"], exclude_id=project.id) != changes["slug"]:
                raise ConflictError(f"Another project already uses the slug '{changes['slug']}'.")
            project.slug = changes["slug"]
        if changes.get("name"):
            project.name = changes["name"].strip()
        if changes.get("description") is not None:
            project.description = changes["description"].strip()
        if "cover_asset_id" in changes:
            cover = changes["cover_asset_id"]
            if cover is not None:
                asset = session.get(Asset, cover)
                if asset is None or asset.deleted_at is not None:
                    raise LookupError("cover asset")
            project.cover_asset_id = cover
        if changes.get("brief") is not None:
            project.brief = changes["brief"]
        if changes.get("describe_images") is not None:
            project.describe_images = changes["describe_images"]
        project.updated_at = utcnow()
        try:
            session.flush()
        except IntegrityError as exc:
            raise ConflictError("That slug is taken.") from exc
        out = _project_out(session, project, detail=True)
    services.events.publish("library.changed", {"scope": "projects", "id": project_id})
    return out  # type: ignore[return-value]


def set_project_trashed(services: Services, project_id: str, trashed: bool) -> ProjectDetail | None:
    with services.db.session() as session:
        project = session.get(Project, project_id)
        if project is None:
            return None
        project.deleted_at = utcnow() if trashed else None
        session.flush()
        out = _project_out(session, project, detail=True)
    services.events.publish("library.changed", {"scope": "projects", "id": project_id})
    return out  # type: ignore[return-value]


# ----------------------------------------------------------------- folders


class FolderOut(BaseModel):
    id: str
    project_id: str | None
    name: str
    color: FolderColor
    sort_order: int
    asset_count: int
    created_at: datetime


class FolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    color: FolderColor = "tungsten"
    project_id: str | None = None


class FolderPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    color: FolderColor | None = None
    sort_order: int | None = None


class FolderOrder(BaseModel):
    folder_ids: list[str] = Field(min_length=1, max_length=1000)


def _scope(project_id: str | None):  # type: ignore[no-untyped-def]
    return Folder.project_id.is_(None) if project_id is None else Folder.project_id == project_id


def _name_taken(
    session: Session, project_id: str | None, name: str, exclude: str | None = None
) -> bool:
    query = select(Folder.id).where(_scope(project_id), func.lower(Folder.name) == name.lower())
    if exclude:
        query = query.where(Folder.id != exclude)
    return session.scalar(query) is not None


def list_folders(
    services: Services,
    project_id: str | None,
    q: str | None = None,
    sort: Literal["manual", "name", "count", "created"] = "manual",
) -> list[FolderOut]:
    with services.db.session() as session:
        count = (
            select(func.count(Asset.id))
            .where(Asset.folder_id == Folder.id, Asset.deleted_at.is_(None))
            .correlate(Folder)
            .scalar_subquery()
        )
        query = select(Folder, count).where(_scope(project_id))
        if q:
            query = query.where(func.lower(Folder.name).like(f"%{q.strip().lower()}%"))
        order = {
            "manual": (Folder.sort_order.asc(), func.lower(Folder.name).asc()),
            "name": (func.lower(Folder.name).asc(),),
            "count": (count.desc(), func.lower(Folder.name).asc()),
            "created": (Folder.created_at.desc(),),
        }[sort]
        return [
            FolderOut(
                id=f.id,
                project_id=f.project_id,
                name=f.name,
                color=f.color,  # type: ignore[arg-type]
                sort_order=f.sort_order,
                asset_count=n or 0,
                created_at=f.created_at,
            )
            for f, n in session.execute(query.order_by(*order)).all()
        ]


def _folder_out(session: Session, folder: Folder) -> FolderOut:
    n = session.scalar(
        select(func.count()).where(Asset.folder_id == folder.id, Asset.deleted_at.is_(None))
    )
    return FolderOut(
        id=folder.id,
        project_id=folder.project_id,
        name=folder.name,
        color=folder.color,  # type: ignore[arg-type]
        sort_order=folder.sort_order,
        asset_count=n or 0,
        created_at=folder.created_at,
    )


def create_folder(services: Services, body: FolderCreate) -> FolderOut:
    name = body.name.strip()
    with services.db.session() as session:
        if body.project_id is not None:
            project = session.get(Project, body.project_id)
            if project is None or project.deleted_at is not None:
                raise LookupError("project")
        if _name_taken(session, body.project_id, name):
            raise ConflictError(f"A folder named '{name}' already exists here.")
        last = session.scalar(select(func.max(Folder.sort_order)).where(_scope(body.project_id)))
        folder = Folder(
            name=name,
            color=body.color,
            project_id=body.project_id,
            sort_order=(last or 0) + 1,
        )
        session.add(folder)
        session.flush()
        out = _folder_out(session, folder)
    services.events.publish("library.changed", {"scope": "folders"})
    return out


def patch_folder(services: Services, folder_id: str, patch: FolderPatch) -> FolderOut | None:
    with services.db.session() as session:
        folder = session.get(Folder, folder_id)
        if folder is None:
            return None
        if patch.name is not None:
            name = patch.name.strip()
            if _name_taken(session, folder.project_id, name, exclude=folder.id):
                raise ConflictError(f"A folder named '{name}' already exists here.")
            folder.name = name
        if patch.color is not None:
            folder.color = patch.color
        if patch.sort_order is not None:
            folder.sort_order = patch.sort_order
        session.flush()
        out = _folder_out(session, folder)
    services.events.publish("library.changed", {"scope": "folders"})
    return out


def reorder_folders(services: Services, body: FolderOrder) -> None:
    with services.db.session() as session:
        for index, folder_id in enumerate(body.folder_ids):
            session.execute(update(Folder).where(Folder.id == folder_id).values(sort_order=index))
    services.events.publish("library.changed", {"scope": "folders"})


def delete_folder(services: Services, folder_id: str) -> bool:
    """Delete a folder; its assets return to All assets (they are never deleted)."""
    with services.db.session() as session:
        folder = session.get(Folder, folder_id)
        if folder is None:
            return False
        session.execute(update(Asset).where(Asset.folder_id == folder_id).values(folder_id=None))
        session.delete(folder)
    services.events.publish("library.changed", {"scope": "folders"})
    return True
