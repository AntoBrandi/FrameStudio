"""Assets: ingest, queries, bulk actions, trash and storage (plan steps M1.1–M1.7)."""

from __future__ import annotations

import base64
import contextlib
import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import Select, and_, column, func, or_, select, text, update
from sqlalchemy.orm import Session

from framestudio.db.base import utcnow
from framestudio.db.models import Asset, Folder, Generation, Project
from framestudio.library import search
from framestudio.library.media import (
    Derived,
    MediaError,
    inspect_media,
    make_derived,
    remove_derived,
)
from framestudio.media.store import sha256_file
from framestudio.services import Services

Kind = Literal["image", "video", "audio"]
MAX_BULK = 5000


class GenerationSummary(BaseModel):
    id: str
    mode: str
    model_id: str
    prompt: str
    seed: int | None
    params: dict[str, Any]
    #: Lineage: the assets given as references, and the one it was made from.
    references: list[str] = []
    start_frame: str | None = None
    end_frame: str | None = None
    parent_asset_id: str | None = None


class AssetOut(BaseModel):
    id: str
    kind: Kind
    mime: str
    size_bytes: int
    width: int | None
    height: int | None
    duration: float | None
    fps: float | None
    has_audio: bool | None
    video_codec: str | None
    audio_codec: str | None
    sha256: str
    url: str
    thumb_url: str | None
    poster_url: str | None
    preview_url: str | None
    provenance: Literal["uploaded", "generated"]
    original_name: str | None
    source_generation_id: str | None
    generation: GenerationSummary | None
    project_id: str | None
    folder_id: str | None
    favorite: bool
    notes: str
    caption: str | None
    #: Search tags from Describe image (M2.6).
    tags: list[str]
    last_viewed_at: datetime | None
    last_downloaded_at: datetime | None
    created_at: datetime
    deleted_at: datetime | None


class AssetPage(BaseModel):
    items: list[AssetOut]
    next_cursor: str | None
    total: int


class MonthCount(BaseModel):
    month: str
    count: int


class AssetFacets(BaseModel):
    kinds: dict[str, int]
    models: dict[str, int]
    months: list[MonthCount]


class AssetPatch(BaseModel):
    favorite: bool | None = None
    notes: str | None = Field(default=None, max_length=20_000)
    caption: str | None = Field(default=None, max_length=2_000)
    original_name: str | None = Field(default=None, min_length=1, max_length=255)


class AssetIds(BaseModel):
    asset_ids: list[str] = Field(min_length=1, max_length=MAX_BULK)


class MoveAssets(AssetIds):
    """Folder wins; without one, assets go to the project's root (or the
    global library when ``project_id`` is null)."""

    folder_id: str | None = None
    project_id: str | None = None


class FavoriteAssets(AssetIds):
    favorite: bool


class BulkResult(BaseModel):
    updated: int


class IngestItem(BaseModel):
    name: str
    asset: AssetOut | None = None
    duplicate: bool = False
    error_code: str | None = None
    error: str | None = None


class LibrarySummary(BaseModel):
    total: int
    favorites: int
    trashed: int
    kinds: dict[str, int]
    models: list[str]
    last_viewed_id: str | None
    last_downloaded_id: str | None


class EmptyTrashResult(BaseModel):
    assets_deleted: int
    projects_deleted: int
    bytes_freed: int


class StorageRow(BaseModel):
    key: str
    label: str
    count: int
    bytes: int


class StorageReport(BaseModel):
    disk_bytes: int
    media_bytes: int
    derived_bytes: int
    database_bytes: int
    by_kind: list[StorageRow]
    by_project: list[StorageRow]
    trash: StorageRow


# ------------------------------------------------------------------ output


def _url(asset_id: str, what: str, present: str | None) -> str | None:
    return f"/api/assets/{asset_id}/{what}" if present else None


def asset_out(asset: Asset, generation: Generation | None = None) -> AssetOut:
    summary = (
        GenerationSummary(
            id=generation.id,
            mode=generation.mode,
            model_id=generation.model_id,
            prompt=generation.prompt,
            seed=generation.seed,
            references=list(generation.reference_ids or []),
            start_frame=generation.start_frame_id,
            end_frame=generation.end_frame_id,
            parent_asset_id=generation.parent_asset_id,
            params=generation.params or {},
        )
        if generation is not None
        else None
    )
    return AssetOut(
        id=asset.id,
        kind=asset.kind,  # type: ignore[arg-type]
        mime=asset.mime,
        size_bytes=asset.size_bytes,
        width=asset.width,
        height=asset.height,
        duration=asset.duration,
        fps=asset.fps,
        has_audio=asset.has_audio,
        video_codec=asset.video_codec,
        audio_codec=asset.audio_codec,
        sha256=asset.sha256,
        url=f"/api/assets/{asset.id}/file",
        thumb_url=_url(asset.id, "thumb", asset.thumb_path),
        poster_url=_url(asset.id, "poster", asset.poster_path),
        preview_url=_url(asset.id, "preview", asset.preview_path),
        provenance=asset.provenance,  # type: ignore[arg-type]
        original_name=asset.original_name,
        source_generation_id=asset.source_generation_id,
        generation=summary,
        project_id=asset.project_id,
        folder_id=asset.folder_id,
        favorite=asset.favorite,
        notes=asset.notes or "",
        caption=asset.caption,
        tags=list(asset.tags or []),
        last_viewed_at=asset.last_viewed_at,
        last_downloaded_at=asset.last_downloaded_at,
        created_at=asset.created_at,
        deleted_at=asset.deleted_at,
    )


def load_out(session: Session, asset_id: str) -> AssetOut | None:
    row = session.execute(
        select(Asset, Generation)
        .outerjoin(Generation, Generation.id == Asset.source_generation_id)
        .where(Asset.id == asset_id)
    ).first()
    return asset_out(row[0], row[1]) if row else None


# ------------------------------------------------------------------ ingest


@dataclass
class Placement:
    project_id: str | None = None
    folder_id: str | None = None


def resolve_placement(session: Session, project_id: str | None, folder_id: str | None) -> Placement:
    """Validate a target; a folder decides the project."""
    if folder_id:
        folder = session.get(Folder, folder_id)
        if folder is None:
            raise LookupError("folder")
        return Placement(project_id=folder.project_id, folder_id=folder.id)
    if project_id:
        project = session.get(Project, project_id)
        if project is None or project.deleted_at is not None:
            raise LookupError("project")
    return Placement(project_id=project_id or None)


def ingest_file(
    services: Services,
    source: Path,
    *,
    name: str | None,
    provenance: Literal["uploaded", "generated"],
    placement: Placement | None = None,
    source_generation_id: str | None = None,
    dedupe: bool = True,
) -> tuple[AssetOut, bool]:
    """Turn a file under ``tmp/`` into an asset. Returns ``(asset, duplicate)``.

    Uploads are deduplicated per destination: the same bytes dropped into the
    same project again return the existing asset (restoring it from the trash
    if needed) and add nothing.
    """
    placement = placement or Placement()
    try:
        info = inspect_media(source, name)
        digest = sha256_file(source)
        if dedupe:
            existing = _find_duplicate(services, digest, placement.project_id)
            if existing is not None:
                return existing, True
        scratch = services.media.temp_path(".derive")
        try:
            derived = make_derived(source, info, digest, services.media.root, scratch)
        except MediaError:
            remove_derived(digest, services.media.root)
            raise
        stored = services.media.put(source, info.ext)
    finally:
        source.unlink(missing_ok=True)
    with services.db.session() as session:
        asset = Asset(
            kind=info.kind,
            sha256=stored.sha256,
            path=stored.rel_path,
            mime=info.mime,
            size_bytes=stored.size_bytes,
            width=info.width,
            height=info.height,
            duration=info.duration,
            fps=info.fps,
            has_audio=info.has_audio,
            video_codec=info.video_codec,
            audio_codec=info.audio_codec,
            thumb_path=derived.thumb,
            poster_path=derived.poster,
            preview_path=derived.preview,
            provenance=provenance,
            original_name=(name or None) and Path(name).name[:255],
            source_generation_id=source_generation_id,
            project_id=placement.project_id,
            folder_id=placement.folder_id,
        )
        session.add(asset)
        session.flush()
        search.index_assets(session, [asset.id])
        out = load_out(session, asset.id)
    assert out is not None
    services.events.publish("asset.created", out.model_dump(mode="json"))
    return out, False


def _find_duplicate(services: Services, digest: str, project_id: str | None) -> AssetOut | None:
    with services.db.session() as session:
        query = select(Asset).where(
            Asset.sha256 == digest,
            Asset.project_id.is_(None) if project_id is None else Asset.project_id == project_id,
        )
        asset = session.scalars(
            query.order_by(Asset.deleted_at.is_not(None), Asset.created_at)
        ).first()
        if asset is None:
            return None
        if asset.deleted_at is not None:
            asset.deleted_at = None
        session.flush()
        return load_out(session, asset.id)


def backfill_derived(services: Services, limit: int = 500) -> int:
    """Make missing thumbnails for assets created before M1. Returns how many."""
    with services.db.session() as session:
        rows = session.execute(
            select(Asset.id, Asset.path, Asset.sha256, Asset.original_name)
            .where(Asset.thumb_path.is_(None))
            .limit(limit)
        ).all()
    done = 0
    for asset_id, rel_path, digest, name in rows:
        try:
            path = services.media.resolve(rel_path)
            info = inspect_media(path, name)
            derived: Derived = make_derived(
                path, info, digest, services.media.root, services.media.temp_path(".derive")
            )
        except (MediaError, ValueError, OSError):
            continue
        with services.db.session() as session:
            asset = session.get(Asset, asset_id)
            if asset is not None:
                asset.thumb_path = derived.thumb
                asset.poster_path = derived.poster
                asset.preview_path = derived.preview
                asset.video_codec = info.video_codec
                asset.audio_codec = info.audio_codec
                done += 1
    return done


# ------------------------------------------------------------------- query


@dataclass
class AssetQuery:
    #: None: any project; "none": only the global library; otherwise an id.
    project_id: str | None = None
    folder_id: str | None = None
    kinds: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    created_after: datetime | None = None
    created_before: datetime | None = None
    liked: bool | None = None
    downloaded: bool | None = None
    provenance: str | None = None
    trashed: bool = False
    q: str | None = None
    sort: Literal["newest", "oldest"] = "newest"


def _encode(value: list[Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")


def _decode(cursor: str) -> list[Any]:
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        value = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("bad cursor") from exc
    if not isinstance(value, list):
        raise ValueError("bad cursor")
    return value


def _filtered(query: AssetQuery) -> tuple[Select[Any], str | None]:
    """Base select over (Asset, Generation) with every filter applied, plus the
    FTS match expression (if the search has free text)."""
    parsed = search.parse_query(query.q or "")
    statement = select(Asset, Generation).outerjoin(
        Generation, Generation.id == Asset.source_generation_id
    )
    conditions = [
        Asset.deleted_at.is_not(None) if query.trashed else Asset.deleted_at.is_(None),
    ]
    if query.project_id == "none":
        conditions.append(Asset.project_id.is_(None))
    elif query.project_id:
        conditions.append(Asset.project_id == query.project_id)
    if query.folder_id == "none":
        conditions.append(Asset.folder_id.is_(None))
    elif query.folder_id:
        conditions.append(Asset.folder_id == query.folder_id)
    kinds = [*query.kinds, *parsed.kinds]
    if kinds:
        conditions.append(Asset.kind.in_(kinds))
    models = [*query.models, *parsed.models]
    if models:
        model_conditions = [Generation.model_id.in_([m for m in models if m != "upload"])]
        if "upload" in models:
            model_conditions.append(Asset.provenance == "uploaded")
        conditions.append(or_(*model_conditions))
    after = max(filter(None, [query.created_after, parsed.after]), default=None)
    before = min(filter(None, [query.created_before, parsed.before]), default=None)
    if after is not None:
        conditions.append(Asset.created_at >= after)
    if before is not None:
        conditions.append(Asset.created_at < before)
    liked = query.liked if query.liked is not None else parsed.liked
    if liked is not None:
        conditions.append(Asset.favorite.is_(liked))
    downloaded = query.downloaded if query.downloaded is not None else parsed.downloaded
    if downloaded is not None:
        conditions.append(
            Asset.last_downloaded_at.is_not(None)
            if downloaded
            else Asset.last_downloaded_at.is_(None)
        )
    provenance = query.provenance or parsed.provenance
    if provenance:
        conditions.append(Asset.provenance == provenance)
    return statement.where(and_(*conditions)), parsed.match()


def _count(session: Session, statement: Select[Any]) -> int:
    return (
        session.scalar(
            select(func.count()).select_from(statement.with_only_columns(Asset.id).subquery())
        )
        or 0
    )


def query_assets(
    services: Services, query: AssetQuery, *, cursor: str | None = None, limit: int = 200
) -> AssetPage:
    statement, match = _filtered(query)
    with services.db.session() as session:
        if match:
            ranked = (
                text(
                    f"SELECT asset_id, bm25({search.FTS_TABLE}, {search.BM25_WEIGHTS}) AS rank "  # noqa: S608 (constant table name; values are bound)
                    f"FROM {search.FTS_TABLE} WHERE {search.FTS_TABLE} MATCH :match"
                )
                .bindparams(match=match)
                .columns(column("asset_id"), column("rank"))
                .subquery("ranked")
            )
            statement = statement.join(ranked, ranked.c.asset_id == Asset.id)
            total = _count(session, statement)
            offset = int(_decode(cursor)[1]) if cursor else 0
            rows = session.execute(
                statement.order_by(ranked.c.rank, Asset.created_at.desc(), Asset.id.desc())
                .offset(offset)
                .limit(limit + 1)
            ).all()
            more = len(rows) > limit
            next_cursor = _encode(["o", offset + limit]) if more else None
        else:
            total = _count(session, statement)
            newest = query.sort == "newest"
            order = (
                (Asset.created_at.desc(), Asset.id.desc())
                if newest
                else (Asset.created_at.asc(), Asset.id.asc())
            )
            if cursor:
                created_text, last_id = _decode(cursor)
                created = datetime.fromisoformat(created_text)
                if newest:
                    statement = statement.where(
                        or_(
                            Asset.created_at < created,
                            and_(Asset.created_at == created, Asset.id < last_id),
                        )
                    )
                else:
                    statement = statement.where(
                        or_(
                            Asset.created_at > created,
                            and_(Asset.created_at == created, Asset.id > last_id),
                        )
                    )
            rows = session.execute(statement.order_by(*order).limit(limit + 1)).all()
            more = len(rows) > limit
            last = rows[limit - 1][0] if more else None
            next_cursor = _encode([last.created_at.isoformat(), last.id]) if last else None
        items = [asset_out(asset, generation) for asset, generation in rows[:limit]]
    return AssetPage(items=items, next_cursor=next_cursor, total=total)


def asset_facets(services: Services, query: AssetQuery) -> AssetFacets:
    statement, match = _filtered(query)
    if match:
        statement = statement.where(
            Asset.id.in_(
                text(
                    f"SELECT asset_id FROM {search.FTS_TABLE} WHERE {search.FTS_TABLE} MATCH :match"  # noqa: S608 (constant table name; values are bound)
                )
                .bindparams(match=match)
                .columns(column("asset_id"))
            )
        )
    base = statement.with_only_columns(
        Asset.id, Asset.kind, Asset.created_at, Generation.model_id
    ).subquery()
    with services.db.session() as session:
        kinds = dict(session.execute(select(base.c.kind, func.count()).group_by(base.c.kind)).all())
        model = func.coalesce(base.c.model_id, "upload")
        models = dict(session.execute(select(model, func.count()).group_by(model)).all())
        month = func.strftime("%Y-%m", base.c.created_at)
        months = session.execute(
            select(month, func.count()).group_by(month).order_by(month.desc())
        ).all()
    return AssetFacets(
        kinds={str(k): int(v) for k, v in kinds.items()},
        models={str(k): int(v) for k, v in models.items()},
        months=[MonthCount(month=str(m), count=int(c)) for m, c in months],
    )


def summary(services: Services, project_id: str | None = None) -> LibrarySummary:
    with services.db.session() as session:
        scope = [] if project_id is None else [Asset.project_id == project_id]
        live = and_(Asset.deleted_at.is_(None), *scope)
        total = session.scalar(select(func.count()).where(live)) or 0
        favorites = session.scalar(select(func.count()).where(live, Asset.favorite.is_(True))) or 0
        trashed = (
            session.scalar(select(func.count()).where(Asset.deleted_at.is_not(None), *scope)) or 0
        )
        kinds = dict(
            session.execute(select(Asset.kind, func.count()).where(live).group_by(Asset.kind)).all()
        )
        models = session.scalars(
            select(Generation.model_id)
            .join(Asset, Asset.source_generation_id == Generation.id)
            .where(live)
            .distinct()
        ).all()
        has_uploads = session.scalar(
            select(func.count()).where(live, Asset.provenance == "uploaded")
        )
        viewed = session.scalar(
            select(Asset.id)
            .where(live, Asset.last_viewed_at.is_not(None))
            .order_by(Asset.last_viewed_at.desc())
        )
        downloaded = session.scalar(
            select(Asset.id)
            .where(live, Asset.last_downloaded_at.is_not(None))
            .order_by(Asset.last_downloaded_at.desc())
        )
    return LibrarySummary(
        total=total,
        favorites=favorites,
        trashed=trashed,
        kinds={str(k): int(v) for k, v in kinds.items()},
        models=sorted(models) + (["upload"] if has_uploads else []),
        last_viewed_id=viewed,
        last_downloaded_id=downloaded,
    )


# ------------------------------------------------------------------ actions


def _changed(services: Services, scope: str, **data: Any) -> None:
    services.events.publish("library.changed", {"scope": scope, **data})


def patch_asset(services: Services, asset_id: str, patch: AssetPatch) -> AssetOut | None:
    changes = patch.model_dump(exclude_unset=True)
    with services.db.session() as session:
        asset = session.get(Asset, asset_id)
        if asset is None:
            return None
        for key, value in changes.items():
            if value is not None or key == "caption":
                setattr(asset, key, value)
        session.flush()
        if {"notes", "caption", "original_name"} & changes.keys():
            search.index_assets(session, [asset_id])
        out = load_out(session, asset_id)
    _changed(services, "assets", ids=[asset_id])
    return out


def _bulk(services: Services, asset_ids: list[str], scope: str, **values: Any) -> int:
    with services.db.session() as session:
        result = session.execute(
            update(Asset)
            .where(Asset.id.in_(asset_ids))
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        count = int(result.rowcount or 0)  # type: ignore[attr-defined]
    _changed(services, scope, ids=asset_ids)
    return count


def move_assets(services: Services, body: MoveAssets) -> int:
    with services.db.session() as session:
        placement = resolve_placement(session, body.project_id, body.folder_id)
    return _bulk(
        services,
        body.asset_ids,
        "assets",
        project_id=placement.project_id,
        folder_id=placement.folder_id,
    )


def favorite_assets(services: Services, body: FavoriteAssets) -> int:
    return _bulk(services, body.asset_ids, "assets", favorite=body.favorite)


def trash_assets(services: Services, asset_ids: list[str]) -> int:
    return _bulk(services, asset_ids, "assets", deleted_at=utcnow())


def restore_assets(services: Services, asset_ids: list[str]) -> int:
    return _bulk(services, asset_ids, "assets", deleted_at=None)


def mark(services: Services, asset_id: str, what: Literal["viewed", "downloaded"]) -> None:
    column = "last_viewed_at" if what == "viewed" else "last_downloaded_at"
    with services.db.session() as session:
        session.execute(update(Asset).where(Asset.id == asset_id).values({column: utcnow()}))


def empty_trash(services: Services) -> EmptyTrashResult:
    """Permanently delete trashed assets and projects. Files go only when no
    remaining asset uses them; a deleted project's media stays in the library."""
    with services.db.session() as session:
        doomed = session.execute(
            select(Asset.id, Asset.path, Asset.sha256, Asset.size_bytes).where(
                Asset.deleted_at.is_not(None)
            )
        ).all()
        ids = [row.id for row in doomed]
        search.remove_from_index(session, ids)
        if ids:
            session.execute(
                text("DELETE FROM assets WHERE id IN (SELECT value FROM json_each(:ids))"),
                {"ids": json.dumps(ids)},
            )
        projects = session.scalars(select(Project).where(Project.deleted_at.is_not(None))).all()
        for project in projects:
            session.execute(
                update(Asset)
                .where(Asset.project_id == project.id)
                .values(project_id=None, folder_id=None)
            )
            session.delete(project)
        session.flush()
        still_used = set(
            session.scalars(
                select(Asset.path).where(Asset.path.in_([row.path for row in doomed]))
            ).all()
        )
    freed = 0
    for row in doomed:
        if row.path in still_used:
            continue
        still_used.add(row.path)  # one file may back several doomed rows
        try:
            path = services.media.resolve(row.path)
            freed += path.stat().st_size
            path.unlink(missing_ok=True)
        except (ValueError, OSError):
            pass
        remove_derived(row.sha256, services.media.root)
    _changed(services, "trash")
    return EmptyTrashResult(
        assets_deleted=len(ids), projects_deleted=len(projects), bytes_freed=freed
    )


def _dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            with contextlib.suppress(OSError):
                total += (Path(root) / name).stat().st_size
    return total


def storage_report(services: Services) -> StorageReport:
    media_root = services.media.root
    derived_bytes = _dir_size(media_root / "derived")
    media_bytes = _dir_size(media_root) - derived_bytes
    database_bytes = sum(
        p.stat().st_size for p in services.paths.root.glob("framestudio.db*") if p.is_file()
    )
    with services.db.session() as session:
        live = Asset.deleted_at.is_(None)
        by_kind = session.execute(
            select(Asset.kind, func.count(), func.coalesce(func.sum(Asset.size_bytes), 0))
            .where(live)
            .group_by(Asset.kind)
        ).all()
        by_project = session.execute(
            select(
                Project.id,
                Project.name,
                func.count(Asset.id),
                func.coalesce(func.sum(Asset.size_bytes), 0),
            )
            .select_from(Asset)
            .outerjoin(Project, Project.id == Asset.project_id)
            .where(live)
            .group_by(Project.id, Project.name)
        ).all()
        trash_count, trash_bytes = session.execute(
            select(func.count(), func.coalesce(func.sum(Asset.size_bytes), 0)).where(
                Asset.deleted_at.is_not(None)
            )
        ).one()
    return StorageReport(
        disk_bytes=media_bytes + derived_bytes + database_bytes,
        media_bytes=media_bytes,
        derived_bytes=derived_bytes,
        database_bytes=database_bytes,
        by_kind=[StorageRow(key=k, label=k, count=c, bytes=b) for k, c, b in by_kind],
        by_project=sorted(
            (
                StorageRow(
                    key=pid or "none", label=name or "Library (no project)", count=c, bytes=b
                )
                for pid, name, c, b in by_project
            ),
            key=lambda row: row.bytes,
            reverse=True,
        ),
        trash=StorageRow(key="trash", label="Trash", count=trash_count, bytes=trash_bytes),
    )


def copy_upload(fileobj: Any, dest: Path) -> None:
    with dest.open("wb") as out:
        shutil.copyfileobj(fileobj, out, length=1024 * 1024)
