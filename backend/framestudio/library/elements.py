"""Elements (plan step M4.1): characters, locations and props that prompts
reference by ``@handle``.

An element belongs to a project or is global. Its handle is unique in its
scope (a project element may share a handle with a global one; the project's
wins inside that project, M4.4). Handles are made from the name and can be
edited or regenerated. Media are library assets in order; the first is the
primary. Editing the description or the media bumps the version.

Export is a CSV (the metadata, with media named by content hash) or a zip
(the CSV, an ``elements.json`` and the media files); importing either into
another library recreates the elements without loss.
"""

from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
import zipfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from framestudio.db.base import utcnow
from framestudio.db.models import Asset, Element, ElementFolder, ElementMedia, ElementUse, Project

if TYPE_CHECKING:
    from framestudio.services import Services

Category = Literal["character", "location", "prop", "auto"]
Status = Literal["draft", "ready"]
HANDLE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
#: Uncompressed size an element zip may unpack to.
MAX_ZIP_BYTES = 8 * 1024**3
CSV_FIELDS = [
    "handle",
    "name",
    "category",
    "status",
    "version",
    "description",
    "scope",
    "folder",
    "properties",
    "media",
]


class ElementConflictError(ValueError):
    pass


def slugify(name: str, fallback: str = "element") -> str:
    """A handle from a name: ``Maya Ortiz`` → ``maya-ortiz``."""
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")[:40].strip("-")
    return slug or fallback


def scope_key(project_id: str | None) -> str:
    return project_id or "global"


def unique_handle(session: Session, scope: str, base: str, exclude_id: str | None = None) -> str:
    """``base``, or ``base-2``, ``base-3``… whichever is free in ``scope``."""
    taken = set(
        session.scalars(
            select(Element.handle).where(
                Element.scope_key == scope,
                Element.handle.like(f"{base}%"),
                Element.id != (exclude_id or ""),
            )
        )
    )
    if base not in taken:
        return base
    number = 2
    while f"{base}-{number}" in taken:
        number += 1
    return f"{base}-{number}"


# ---------------------------------------------------------------- shapes


class ElementMediaOut(BaseModel):
    asset_id: str
    role: str
    position: int
    kind: str
    url: str
    thumb_url: str | None


class ElementOut(BaseModel):
    id: str
    handle: str
    name: str
    category: Category
    status: Status
    version: int
    description: str
    properties: dict[str, str]
    project_id: str | None
    folder_id: str | None
    media: list[ElementMediaOut]
    cover_url: str | None
    #: Generations that referenced it.
    used_in: int
    created_at: datetime
    updated_at: datetime


def _check_handle(value: str | None) -> str | None:
    if value is not None and not HANDLE.match(value):
        raise ValueError("use lowercase letters, digits, - and _ (up to 48)")
    return value


class ElementCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    handle: str | None = None
    category: Category = "auto"
    status: Status = "draft"
    description: str = Field(default="", max_length=10_000)
    properties: dict[str, str] = Field(default_factory=dict)
    project_id: str | None = None
    folder_id: str | None = None
    #: Library assets to attach, in order; the first becomes the primary.
    asset_ids: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("handle")
    @classmethod
    def _valid_handle(cls, value: str | None) -> str | None:
        return _check_handle(value)


class ElementPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    handle: str | None = None
    #: Make a new handle from the (new) name.
    regenerate_handle: bool = False
    category: Category | None = None
    status: Status | None = None
    description: str | None = Field(default=None, max_length=10_000)
    properties: dict[str, str] | None = None
    folder_id: str | None = None

    @field_validator("handle")
    @classmethod
    def _valid_handle(cls, value: str | None) -> str | None:
        return _check_handle(value)


class ElementMediaChange(BaseModel):
    #: Assets to add at the end, in order.
    asset_ids: list[str] = Field(min_length=1, max_length=50)
    role: str = Field(default="angle", max_length=16)


class ElementFolderOut(BaseModel):
    id: str
    project_id: str | None
    name: str
    color: str
    element_count: int


class ElementFolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    color: str = Field(default="tungsten", max_length=16)
    project_id: str | None = None


class ElementFolderPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    color: str | None = Field(default=None, max_length=16)


class ImportResult(BaseModel):
    created: list[str]
    #: Handles that were taken in the target scope and got a suffix.
    renamed: dict[str, str]
    problems: list[str]


# ---------------------------------------------------------------- reading


def _media(session: Session, element_id: str) -> list[tuple[ElementMedia, Asset]]:
    rows = session.execute(
        select(ElementMedia, Asset)
        .join(Asset, Asset.id == ElementMedia.asset_id)
        .where(ElementMedia.element_id == element_id, Asset.deleted_at.is_(None))
        .order_by(ElementMedia.position, ElementMedia.id)
    ).all()
    return [(link, asset) for link, asset in rows]


def element_out(session: Session, element: Element) -> ElementOut:
    media = [
        ElementMediaOut(
            asset_id=asset.id,
            role=link.role,
            position=index,
            kind=asset.kind,
            url=f"/api/assets/{asset.id}/file",
            thumb_url=f"/api/assets/{asset.id}/thumb" if asset.thumb_path else None,
        )
        for index, (link, asset) in enumerate(_media(session, element.id))
    ]
    used = (
        session.scalar(
            select(func.count()).select_from(ElementUse).where(ElementUse.element_id == element.id)
        )
        or 0
    )
    return ElementOut(
        id=element.id,
        handle=element.handle,
        name=element.name,
        category=element.category,  # type: ignore[arg-type]
        status=element.status,  # type: ignore[arg-type]
        version=element.version,
        description=element.description,
        properties={str(k): str(v) for k, v in (element.properties or {}).items()},
        project_id=element.project_id,
        folder_id=element.folder_id,
        media=media,
        cover_url=next((m.thumb_url for m in media if m.thumb_url), None),
        used_in=used,
        created_at=element.created_at,
        updated_at=element.updated_at,
    )


def list_elements(
    services: Services,
    *,
    project_id: str | None = None,
    include_global: bool = True,
    category: str | None = None,
    status: str | None = None,
    folder_id: str | None = None,
    q: str | None = None,
) -> list[ElementOut]:
    """Elements of a project (and the global ones), or the global ones only
    when ``project_id`` is None."""
    query = select(Element)
    if project_id:
        scopes = [project_id, "global"] if include_global else [project_id]
        query = query.where(Element.scope_key.in_(scopes))
    else:
        query = query.where(Element.scope_key == "global")
    if category:
        query = query.where(Element.category == category)
    if status:
        query = query.where(Element.status == status)
    if folder_id:
        query = query.where(Element.folder_id == folder_id)
    if q:
        like = f"%{q.strip().lstrip('@').lower()}%"
        query = query.where(
            or_(
                func.lower(Element.name).like(like),
                Element.handle.like(like),
                func.lower(Element.description).like(like),
            )
        )
    with services.db.session() as session:
        rows = session.scalars(query.order_by(Element.name)).all()
        return [element_out(session, e) for e in rows]


def get_element(services: Services, element_id: str) -> ElementOut | None:
    with services.db.session() as session:
        element = session.get(Element, element_id)
        return element_out(session, element) if element else None


# ---------------------------------------------------------------- writing


def _changed(services: Services, element_id: str | None = None) -> None:
    services.events.publish("library.changed", {"scope": "elements", "id": element_id})


def _check_place(session: Session, project_id: str | None, folder_id: str | None) -> None:
    if project_id is not None:
        project = session.get(Project, project_id)
        if project is None or project.deleted_at is not None:
            raise LookupError("project")
    if folder_id is not None:
        folder = session.get(ElementFolder, folder_id)
        if folder is None or folder.project_id != project_id:
            raise LookupError("element folder")


def _attach(session: Session, element: Element, asset_ids: list[str], role: str) -> None:
    existing = _media(session, element.id)
    have = {asset.id for _, asset in existing}
    position = len(existing)
    for asset_id in asset_ids:
        asset = session.get(Asset, asset_id)
        if asset is None or asset.deleted_at is not None:
            raise LookupError("asset")
        if asset_id in have:
            continue
        link_role = "primary" if position == 0 else role
        session.add(
            ElementMedia(
                element_id=element.id, asset_id=asset_id, role=link_role, position=position
            )
        )
        have.add(asset_id)
        position += 1


def create_element(services: Services, body: ElementCreate) -> ElementOut:
    with services.db.session() as session:
        _check_place(session, body.project_id, body.folder_id)
        scope = scope_key(body.project_id)
        if body.handle:
            if session.scalar(
                select(Element.id).where(Element.scope_key == scope, Element.handle == body.handle)
            ):
                raise ElementConflictError(f"@{body.handle} is already taken here.")
            handle = body.handle
        else:
            handle = unique_handle(session, scope, slugify(body.name, body.category))
        element = Element(
            project_id=body.project_id,
            scope_key=scope,
            folder_id=body.folder_id,
            handle=handle,
            category=body.category,
            name=body.name.strip(),
            description=body.description,
            status=body.status,
            properties=dict(body.properties),
        )
        session.add(element)
        session.flush()
        _attach(session, element, body.asset_ids, "angle")
        session.flush()
        out = element_out(session, element)
    _changed(services, out.id)
    return out


def patch_element(services: Services, element_id: str, patch: ElementPatch) -> ElementOut | None:
    changes = patch.model_fields_set
    with services.db.session() as session:
        element = session.get(Element, element_id)
        if element is None:
            return None
        if patch.name is not None:
            element.name = patch.name.strip()
        if patch.handle is not None and patch.handle != element.handle:
            taken = session.scalar(
                select(Element.id).where(
                    Element.scope_key == element.scope_key,
                    Element.handle == patch.handle,
                    Element.id != element.id,
                )
            )
            if taken:
                raise ElementConflictError(f"@{patch.handle} is already taken here.")
            element.handle = patch.handle
        elif patch.regenerate_handle:
            element.handle = unique_handle(
                session, element.scope_key, slugify(element.name, element.category), element.id
            )
        if patch.category is not None:
            element.category = patch.category
        if patch.status is not None:
            element.status = patch.status
        if patch.description is not None and patch.description != element.description:
            element.description = patch.description
            element.version += 1
        if patch.properties is not None:
            element.properties = dict(patch.properties)
        if "folder_id" in changes:
            _check_place(session, element.project_id, patch.folder_id)
            element.folder_id = patch.folder_id
        element.updated_at = utcnow()
        session.flush()
        out = element_out(session, element)
    _changed(services, element_id)
    return out


def add_media(services: Services, element_id: str, change: ElementMediaChange) -> ElementOut | None:
    with services.db.session() as session:
        element = session.get(Element, element_id)
        if element is None:
            return None
        _attach(session, element, change.asset_ids, change.role)
        element.version += 1
        element.updated_at = utcnow()
        session.flush()
        out = element_out(session, element)
    _changed(services, element_id)
    return out


def order_media(services: Services, element_id: str, asset_ids: list[str]) -> ElementOut | None:
    """Set the element's media: this order (the first becomes primary), new
    assets attached, assets left out detached (they stay in the library)."""
    with services.db.session() as session:
        element = session.get(Element, element_id)
        if element is None:
            return None
        asset_ids = list(dict.fromkeys(asset_ids))
        links = {link.asset_id: link for link, _ in _media(session, element_id)}
        for asset_id in asset_ids:
            if asset_id in links:
                continue
            asset = session.get(Asset, asset_id)
            if asset is None or asset.deleted_at is not None:
                raise LookupError("asset")
            links[asset_id] = ElementMedia(element_id=element.id, asset_id=asset_id, role="angle")
            session.add(links[asset_id])
        for link in links.values():
            if link.asset_id not in asset_ids:
                session.delete(link)
        for position, asset_id in enumerate(asset_ids):
            link = links[asset_id]
            link.position = position
            if position == 0:
                link.role = "primary"
            elif link.role == "primary":
                link.role = "angle"
        element.version += 1
        element.updated_at = utcnow()
        session.flush()
        out = element_out(session, element)
    _changed(services, element_id)
    return out


def delete_element(services: Services, element_id: str) -> bool:
    """Delete the element; its media stay in the library."""
    with services.db.session() as session:
        element = session.get(Element, element_id)
        if element is None:
            return False
        session.delete(element)
    _changed(services, element_id)
    return True


# ---------------------------------------------------------------- folders


def list_folders(services: Services, project_id: str | None) -> list[ElementFolderOut]:
    with services.db.session() as session:
        folders = session.scalars(
            select(ElementFolder)
            .where(
                ElementFolder.project_id.is_(None)
                if project_id is None
                else ElementFolder.project_id == project_id
            )
            .order_by(ElementFolder.sort_order, ElementFolder.name)
        ).all()
        return [
            ElementFolderOut(
                id=f.id,
                project_id=f.project_id,
                name=f.name,
                color=f.color,
                element_count=session.scalar(select(func.count()).where(Element.folder_id == f.id))
                or 0,
            )
            for f in folders
        ]


def create_folder(services: Services, body: ElementFolderCreate) -> ElementFolderOut:
    with services.db.session() as session:
        _check_place(session, body.project_id, None)
        folder = ElementFolder(project_id=body.project_id, name=body.name.strip(), color=body.color)
        session.add(folder)
        session.flush()
        out = ElementFolderOut(
            id=folder.id,
            project_id=folder.project_id,
            name=folder.name,
            color=folder.color,
            element_count=0,
        )
    _changed(services)
    return out


def patch_folder(services: Services, folder_id: str, body: ElementFolderPatch) -> bool:
    with services.db.session() as session:
        folder = session.get(ElementFolder, folder_id)
        if folder is None:
            return False
        if body.name is not None:
            folder.name = body.name.strip()
        if body.color is not None:
            folder.color = body.color
    _changed(services)
    return True


def delete_folder(services: Services, folder_id: str) -> bool:
    """Delete the folder; its elements stay, outside any folder."""
    with services.db.session() as session:
        folder = session.get(ElementFolder, folder_id)
        if folder is None:
            return False
        for element in session.scalars(select(Element).where(Element.folder_id == folder_id)):
            element.folder_id = None
        session.delete(folder)
    _changed(services)
    return True


# ---------------------------------------------------------------- export and import


def _export_rows(services: Services, project_id: str | None) -> list[dict[str, Any]]:
    with services.db.session() as session:
        scope = scope_key(project_id)
        project = session.get(Project, project_id) if project_id else None
        folders = {f.id: f.name for f in session.scalars(select(ElementFolder))}
        rows = []
        for element in session.scalars(
            select(Element).where(Element.scope_key == scope).order_by(Element.handle)
        ):
            media = []
            for link, asset in _media(session, element.id):
                ext = Path(asset.path).suffix.lstrip(".")
                media.append(
                    {
                        "role": link.role,
                        "sha256": asset.sha256,
                        "ext": ext,
                        "name": asset.original_name or f"{asset.sha256[:12]}.{ext}",
                        "path": asset.path,
                    }
                )
            rows.append(
                {
                    "handle": element.handle,
                    "name": element.name,
                    "category": element.category,
                    "status": element.status,
                    "version": element.version,
                    "description": element.description,
                    "scope": project.slug if project else "global",
                    "folder": folders.get(element.folder_id or "", ""),
                    "properties": dict(element.properties or {}),
                    "media": media,
                }
            )
        return rows


def _csv(rows: list[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS)
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                **{k: row[k] for k in CSV_FIELDS if k not in {"properties", "media"}},
                "properties": json.dumps(row["properties"], ensure_ascii=False, sort_keys=True),
                "media": " ".join(f"{m['role']}:{m['sha256']}.{m['ext']}" for m in row["media"]),
            }
        )
    return buffer.getvalue()


def export_csv(services: Services, project_id: str | None) -> str:
    return _csv(_export_rows(services, project_id))


def export_zip(services: Services, project_id: str | None) -> bytes:
    rows = _export_rows(services, project_id)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("elements.csv", _csv(rows))
        written: set[str] = set()
        manifest = []
        for row in rows:
            media = []
            for item in row["media"]:
                file = f"media/{item['sha256']}.{item['ext']}"
                if file not in written:
                    archive.write(services.media.resolve(item["path"]), file)
                    written.add(file)
                media.append({"role": item["role"], "file": file, "name": item["name"]})
            manifest.append({**{k: v for k, v in row.items() if k != "media"}, "media": media})
        archive.writestr(
            "elements.json",
            json.dumps({"version": 1, "elements": manifest}, indent=1, ensure_ascii=False),
        )
    return buffer.getvalue()


def _import_rows(
    services: Services,
    rows: list[dict[str, Any]],
    project_id: str | None,
    asset_for: Any,
) -> ImportResult:
    created: list[str] = []
    renamed: dict[str, str] = {}
    problems: list[str] = []
    with services.db.session() as session:
        _check_place(session, project_id, None)
        scope = scope_key(project_id)
        folders = {
            f.name: f.id
            for f in session.scalars(
                select(ElementFolder).where(
                    ElementFolder.project_id.is_(None)
                    if project_id is None
                    else ElementFolder.project_id == project_id
                )
            )
        }
        for row in rows:
            handle = str(row.get("handle") or "") or slugify(str(row.get("name", "")))
            if not HANDLE.match(handle):
                handle = slugify(handle)
            free = unique_handle(session, scope, handle)
            if free != handle:
                renamed[handle] = free
            folder_name = str(row.get("folder") or "")
            if folder_name and folder_name not in folders:
                folder = ElementFolder(project_id=project_id, name=folder_name[:80])
                session.add(folder)
                session.flush()
                folders[folder_name] = folder.id
            category = (
                row.get("category")
                if row.get("category") in {"character", "location", "prop", "auto"}
                else "auto"
            )
            status = row.get("status") if row.get("status") in {"draft", "ready"} else "draft"
            element = Element(
                project_id=project_id,
                scope_key=scope,
                folder_id=folders.get(folder_name) if folder_name else None,
                handle=free,
                category=category,
                name=str(row.get("name") or free)[:120],
                version=_int(row.get("version")),
                description=str(row.get("description") or ""),
                status=status,
                properties=_properties(row.get("properties")),
            )
            session.add(element)
            session.flush()
            for position, item in enumerate(row.get("media") or []):
                asset_id = asset_for(session, item)
                if asset_id is None:
                    name = item.get("sha256") or item.get("file")
                    problems.append(f"@{free}: media {name} is not in this library")
                    continue
                session.add(
                    ElementMedia(
                        element_id=element.id,
                        asset_id=asset_id,
                        role=str(item.get("role") or "angle")[:16],
                        position=position,
                    )
                )
            created.append(free)
    _changed(services)
    return ImportResult(created=created, renamed=renamed, problems=problems)


def _properties(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k)[:80]: str(v)[:2000] for k, v in value.items()}


def _int(value: Any) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 1


def _parse_csv(text: str) -> list[dict[str, Any]]:
    rows = []
    for raw in csv.DictReader(io.StringIO(text)):
        media = []
        for token in (raw.get("media") or "").split():
            role, _, file = token.partition(":")
            sha, _, ext = file.partition(".")
            media.append({"role": role, "sha256": sha, "ext": ext})
        try:
            properties = json.loads(raw.get("properties") or "{}")
        except json.JSONDecodeError:
            properties = {}
        rows.append(
            {
                **raw,
                "properties": properties if isinstance(properties, dict) else {},
                "media": media,
            }
        )
    return rows


def import_csv(services: Services, text: str, project_id: str | None) -> ImportResult:
    """Elements from a CSV; media are found in this library by content hash."""

    def asset_for(session: Session, item: dict[str, Any]) -> str | None:
        return session.scalar(
            select(Asset.id)
            .where(Asset.sha256 == item.get("sha256"), Asset.deleted_at.is_(None))
            .limit(1)
        )

    return _import_rows(services, _parse_csv(text), project_id, asset_for)


def import_zip(services: Services, data: bytes, project_id: str | None) -> ImportResult:
    """Elements and their media from an export zip; media already in the
    project are reused (same content), others are imported."""
    from framestudio.library.assets import Placement, ingest_file
    from framestudio.library.media import MediaError

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError("This is not a zip file.") from exc
    with archive:
        members = {info.filename: info for info in archive.infolist()}
        if sum(info.file_size for info in members.values()) > MAX_ZIP_BYTES:
            raise ValueError("The zip unpacks to more than FrameStudio imports at once.")
        try:
            if "elements.json" in members:
                rows = json.loads(archive.read("elements.json")).get("elements", [])
            elif "elements.csv" in members:
                rows = _parse_csv(archive.read("elements.csv").decode("utf-8-sig"))
            else:
                raise ValueError("The zip has no elements.json or elements.csv.")
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError) as exc:
            raise ValueError("The zip's element list cannot be read.") from exc
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise ValueError("The zip's element list cannot be read.")
        ingested: dict[str, str] = {}
        problems: list[str] = []
        for row in rows:
            media = row.get("media")
            row["media"] = (
                [m for m in media if isinstance(m, dict)] if isinstance(media, list) else []
            )
            for item in row["media"]:
                file = str(item.get("file") or f"media/{item.get('sha256')}.{item.get('ext')}")
                item["file"] = file
                if file in ingested or file not in members:
                    continue
                suffix = Path(file).suffix.lower()
                tmp = services.media.temp_path(
                    suffix if re.fullmatch(r"\.[a-z0-9]{1,8}", suffix) else ".bin"
                )
                tmp.write_bytes(archive.read(file))
                try:
                    asset, _ = ingest_file(
                        services,
                        tmp,
                        name=str(item.get("name") or Path(file).name),
                        provenance="uploaded",
                        placement=Placement(project_id=project_id, folder_id=None),
                    )
                    ingested[file] = asset.id
                except MediaError as exc:
                    problems.append(f"{Path(file).name}: {exc.message}")
                finally:
                    tmp.unlink(missing_ok=True)

    def asset_for(session: Session, item: dict[str, Any]) -> str | None:
        return ingested.get(item.get("file", ""))

    result = _import_rows(services, rows, project_id, asset_for)
    result.problems = problems + result.problems
    return result
