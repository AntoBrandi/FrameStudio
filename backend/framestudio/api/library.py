"""Library API: assets, uploads, projects, folders, trash and storage (M1)."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel

from framestudio.api.deps import ServicesDep
from framestudio.api.llm_errors import http_error
from framestudio.db.models import Asset
from framestudio.jobs.base import JobError
from framestudio.library import assets as lib
from framestudio.library import projects as proj
from framestudio.library.describe import (
    DescribeRequest,
    NameSuggestions,
    describe_if_wanted,
    queue_descriptions,
    suggest_names,
)
from framestudio.library.media import MediaError
from framestudio.providers.base import ProviderError

router = APIRouter(prefix="/api")

NOT_FOUND = {404: {"description": "Not found"}}


def _404(what: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"{what} not found")


def _query(
    project_id: str | None,
    folder_id: str | None,
    kind: list[lib.Kind],
    model: list[str],
    created_after: datetime | None,
    created_before: datetime | None,
    liked: bool | None,
    downloaded: bool | None,
    provenance: Literal["uploaded", "generated"] | None,
    trashed: bool,
    q: str | None,
    sort: Literal["newest", "oldest"],
) -> lib.AssetQuery:
    return lib.AssetQuery(
        project_id=project_id,
        folder_id=folder_id,
        kinds=list(kind),
        models=list(model),
        created_after=created_after,
        created_before=created_before,
        liked=liked,
        downloaded=downloaded,
        provenance=provenance,
        trashed=trashed,
        q=q,
        sort=sort,
    )


# Shared query parameters for listing and facets.
ProjectParam = Annotated[
    str | None, Query(description="A project id, or 'none' for the global library only")
]
FolderParam = Annotated[str | None, Query(description="A folder id, or 'none' for unfiled assets")]
KindParam = Annotated[list[lib.Kind], Query()]
ModelParam = Annotated[list[str], Query(description="Model ids; 'upload' for uploaded files")]


@router.get("/assets", tags=["assets"])
def list_assets(
    services: ServicesDep,
    project_id: ProjectParam = None,
    folder_id: FolderParam = None,
    kind: KindParam = [],  # noqa: B006 (FastAPI copies defaults)
    model: ModelParam = [],  # noqa: B006
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    liked: bool | None = None,
    downloaded: bool | None = None,
    provenance: Literal["uploaded", "generated"] | None = None,
    trashed: bool = False,
    q: Annotated[str | None, Query(max_length=500)] = None,
    sort: Literal["newest", "oldest"] = "newest",
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> lib.AssetPage:
    """Assets, newest first (or by relevance when ``q`` has words). Search
    tokens: type:, model:, after:, before:, is:liked, is:downloaded."""
    query = _query(
        project_id, folder_id, kind, model, created_after, created_before,
        liked, downloaded, provenance, trashed, q, sort,
    )  # fmt: skip
    try:
        return lib.query_assets(services, query, cursor=cursor, limit=limit)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid cursor") from None


@router.get("/assets/facets", tags=["assets"])
def asset_facets(
    services: ServicesDep,
    project_id: ProjectParam = None,
    folder_id: FolderParam = None,
    kind: KindParam = [],  # noqa: B006
    model: ModelParam = [],  # noqa: B006
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    liked: bool | None = None,
    downloaded: bool | None = None,
    provenance: Literal["uploaded", "generated"] | None = None,
    trashed: bool = False,
    q: Annotated[str | None, Query(max_length=500)] = None,
) -> lib.AssetFacets:
    """Counts by kind, model and month for the same filters (the date facet)."""
    query = _query(
        project_id, folder_id, kind, model, created_after, created_before,
        liked, downloaded, provenance, trashed, q, "newest",
    )  # fmt: skip
    return lib.asset_facets(services, query)


@router.get("/assets/summary", tags=["assets"])
def library_summary(services: ServicesDep, project_id: str | None = None) -> lib.LibrarySummary:
    return lib.summary(services, project_id)


@router.post("/assets/upload", tags=["assets"], responses=NOT_FOUND)
def upload_assets(
    services: ServicesDep,
    background: BackgroundTasks,
    files: Annotated[list[UploadFile], File(description="Images, video or audio")],
    project_id: Annotated[str | None, Form()] = None,
    folder_id: Annotated[str | None, Form()] = None,
) -> list[lib.IngestItem]:
    """Import files. Each one is checked by content; duplicates of an asset
    already in the same place are not added again; damaged or unsupported
    files come back with an error instead of failing the whole request."""
    with services.db.session() as session:
        try:
            placement = lib.resolve_placement(session, project_id, folder_id)
        except LookupError as exc:
            raise _404(str(exc).capitalize()) from None
    results: list[lib.IngestItem] = []
    for upload in files:
        name = Path(upload.filename or "upload").name
        tmp = services.media.temp_path(Path(name).suffix[:16].lower() or ".bin")
        lib.copy_upload(upload.file, tmp)
        try:
            asset, duplicate = lib.ingest_file(
                services, tmp, name=name, provenance="uploaded", placement=placement
            )
            results.append(lib.IngestItem(name=name, asset=asset, duplicate=duplicate))
            if not duplicate:
                background.add_task(describe_if_wanted, services, asset.id)
        except MediaError as exc:
            results.append(lib.IngestItem(name=name, error_code=exc.code, error=exc.message))
    return results


class DescribeQueued(BaseModel):
    job_ids: list[str]


@router.post(
    "/assets/describe", tags=["assets"], status_code=status.HTTP_202_ACCEPTED, responses=NOT_FOUND
)
async def describe_assets(body: DescribeRequest, services: ServicesDep) -> DescribeQueued:
    """Caption and tag these assets with the model chosen for captions (sends
    a downscaled copy of each picture to it). Runs as background jobs."""
    if not services.llms:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Connect a language model in Settings › Connections first."
        )

    def missing() -> list[str]:
        with services.db.session() as session:
            return [i for i in body.asset_ids if session.get(Asset, i) is None]

    if await asyncio.to_thread(missing):
        raise _404("Asset")
    return DescribeQueued(
        job_ids=await queue_descriptions(services, list(dict.fromkeys(body.asset_ids)))
    )


@router.post("/assets/{asset_id}/name-suggestions", tags=["assets"], responses=NOT_FOUND)
async def name_suggestions(asset_id: str, services: ServicesDep) -> NameSuggestions:
    """Three short names for an asset, from its caption and prompt."""
    try:
        return await suggest_names(services, asset_id)
    except LookupError:
        raise _404("Asset") from None
    except ProviderError as exc:
        raise http_error(exc) from None
    except JobError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.message) from None


@router.get("/assets/{asset_id}", tags=["assets"], responses=NOT_FOUND)
def get_asset(asset_id: str, services: ServicesDep) -> lib.AssetOut:
    with services.db.session() as session:
        out = lib.load_out(session, asset_id)
    if out is None:
        raise _404("Asset")
    return out


@router.patch("/assets/{asset_id}", tags=["assets"], responses=NOT_FOUND)
def patch_asset(asset_id: str, body: lib.AssetPatch, services: ServicesDep) -> lib.AssetOut:
    out = lib.patch_asset(services, asset_id, body)
    if out is None:
        raise _404("Asset")
    return out


@router.post("/assets/{asset_id}/viewed", tags=["assets"], status_code=status.HTTP_204_NO_CONTENT)
def asset_viewed(asset_id: str, services: ServicesDep) -> Response:
    lib.mark(services, asset_id, "viewed")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class AssetLocation(BaseModel):
    path: str


@router.get("/assets/{asset_id}/location", tags=["assets"], responses=NOT_FOUND)
def asset_location(asset_id: str, services: ServicesDep) -> AssetLocation:
    """Absolute path on the backend machine (for "Reveal in folder")."""
    with services.db.session() as session:
        asset = session.get(Asset, asset_id)
        rel = asset.path if asset else None
    if rel is None:
        raise _404("Asset")
    return AssetLocation(path=str(services.media.resolve(rel)))


def _send(
    services: ServicesDep, rel: str | None, media_type: str, **kwargs: object
) -> FileResponse:
    if not rel:
        raise _404("File")
    try:
        path = services.media.resolve(rel)
    except ValueError:
        raise _404("File") from None
    if not path.is_file():
        raise _404("File")
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
        **kwargs,  # type: ignore[arg-type]
    )


def _download_name(asset: Asset) -> str:
    ext = Path(asset.path).suffix
    if asset.original_name:
        stem = Path(asset.original_name).stem
        return f"{stem}{ext}"
    return f"framestudio-{asset.id[:8]}{ext}"


@router.get(
    "/assets/{asset_id}/file",
    tags=["assets"],
    response_class=FileResponse,
    responses={200: {"content": {"application/octet-stream": {}}}, **NOT_FOUND},
)
def asset_file(asset_id: str, services: ServicesDep, download: bool = False) -> FileResponse:
    """The media file, with range requests for scrubbing; ``download=true``
    sends it as an attachment and records the download."""
    with services.db.session() as session:
        asset = session.get(Asset, asset_id)
        if asset is None:
            raise _404("Asset")
        rel, mime, name = asset.path, asset.mime, _download_name(asset)
    if download:
        response = _send(services, rel, mime, filename=name)  # 404s before anything is recorded
        lib.mark(services, asset_id, "downloaded")
        response.headers["Cache-Control"] = "no-store"
        return response
    return _send(services, rel, mime)


def _derived(asset_id: str, services: ServicesDep, which: str, media_type: str) -> FileResponse:
    with services.db.session() as session:
        asset = session.get(Asset, asset_id)
        if asset is None:
            raise _404("Asset")
        rel = getattr(asset, f"{which}_path")
    return _send(services, rel, media_type)


@router.get("/assets/{asset_id}/thumb", tags=["assets"], response_class=FileResponse,
            responses={200: {"content": {"image/webp": {}}}, **NOT_FOUND})  # fmt: skip
def asset_thumb(asset_id: str, services: ServicesDep) -> FileResponse:
    return _derived(asset_id, services, "thumb", "image/webp")


@router.get("/assets/{asset_id}/poster", tags=["assets"], response_class=FileResponse,
            responses={200: {"content": {"image/webp": {}}}, **NOT_FOUND})  # fmt: skip
def asset_poster(asset_id: str, services: ServicesDep) -> FileResponse:
    return _derived(asset_id, services, "poster", "image/webp")


@router.get("/assets/{asset_id}/preview", tags=["assets"], response_class=FileResponse,
            responses={200: {"content": {"video/mp4": {}}}, **NOT_FOUND})  # fmt: skip
def asset_preview(asset_id: str, services: ServicesDep) -> FileResponse:
    return _derived(asset_id, services, "preview", "video/mp4")


@router.post("/assets/move", tags=["assets"], responses=NOT_FOUND)
def move_assets(body: lib.MoveAssets, services: ServicesDep) -> lib.BulkResult:
    try:
        return lib.BulkResult(updated=lib.move_assets(services, body))
    except LookupError as exc:
        raise _404(str(exc).capitalize()) from None


@router.post("/assets/favorite", tags=["assets"])
def favorite_assets(body: lib.FavoriteAssets, services: ServicesDep) -> lib.BulkResult:
    return lib.BulkResult(updated=lib.favorite_assets(services, body))


@router.post("/assets/trash", tags=["trash"])
def trash_assets(body: lib.AssetIds, services: ServicesDep) -> lib.BulkResult:
    return lib.BulkResult(updated=lib.trash_assets(services, body.asset_ids))


@router.post("/assets/restore", tags=["trash"])
def restore_assets(body: lib.AssetIds, services: ServicesDep) -> lib.BulkResult:
    return lib.BulkResult(updated=lib.restore_assets(services, body.asset_ids))


@router.post("/trash/empty", tags=["trash"])
def empty_trash(services: ServicesDep) -> lib.EmptyTrashResult:
    """Permanently delete everything in the trash."""
    return lib.empty_trash(services)


@router.get("/storage", tags=["library"])
def storage(services: ServicesDep) -> lib.StorageReport:
    return lib.storage_report(services)


# ---------------------------------------------------------------- projects


@router.get("/projects", tags=["projects"])
def list_projects(
    services: ServicesDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
    sort: Literal["updated", "created", "name"] = "updated",
    trashed: bool = False,
) -> list[proj.ProjectOut]:
    return proj.list_projects(services, q, sort, trashed)


@router.post("/projects", tags=["projects"], status_code=status.HTTP_201_CREATED)
def create_project(body: proj.ProjectCreate, services: ServicesDep) -> proj.ProjectDetail:
    return proj.create_project(services, body)


@router.get("/projects/{project_id}", tags=["projects"], responses=NOT_FOUND)
def get_project(project_id: str, services: ServicesDep) -> proj.ProjectDetail:
    out = proj.get_project(services, project_id)
    if out is None:
        raise _404("Project")
    return out


@router.patch("/projects/{project_id}", tags=["projects"], responses=NOT_FOUND)
def patch_project(
    project_id: str, body: proj.ProjectPatch, services: ServicesDep
) -> proj.ProjectDetail:
    try:
        out = proj.patch_project(services, project_id, body)
    except proj.ConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    except LookupError:
        raise HTTPException(422, "The cover must be an asset that is not in the trash.") from None
    if out is None:
        raise _404("Project")
    return out


@router.post("/projects/{project_id}/trash", tags=["projects"], responses=NOT_FOUND)
def trash_project(project_id: str, services: ServicesDep) -> proj.ProjectDetail:
    out = proj.set_project_trashed(services, project_id, True)
    if out is None:
        raise _404("Project")
    return out


@router.post("/projects/{project_id}/restore", tags=["projects"], responses=NOT_FOUND)
def restore_project(project_id: str, services: ServicesDep) -> proj.ProjectDetail:
    out = proj.set_project_trashed(services, project_id, False)
    if out is None:
        raise _404("Project")
    return out


# ----------------------------------------------------------------- folders


@router.get("/folders", tags=["folders"])
def list_folders(
    services: ServicesDep,
    project_id: Annotated[str | None, Query(description="Omit for global folders")] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    sort: Literal["manual", "name", "count", "created"] = "manual",
) -> list[proj.FolderOut]:
    return proj.list_folders(services, project_id, q, sort)


@router.post("/folders", tags=["folders"], status_code=status.HTTP_201_CREATED, responses=NOT_FOUND)
def create_folder(body: proj.FolderCreate, services: ServicesDep) -> proj.FolderOut:
    try:
        return proj.create_folder(services, body)
    except proj.ConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    except LookupError:
        raise _404("Project") from None


@router.patch("/folders/{folder_id}", tags=["folders"], responses=NOT_FOUND)
def patch_folder(folder_id: str, body: proj.FolderPatch, services: ServicesDep) -> proj.FolderOut:
    try:
        out = proj.patch_folder(services, folder_id, body)
    except proj.ConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    if out is None:
        raise _404("Folder")
    return out


@router.post("/folders/reorder", tags=["folders"], status_code=status.HTTP_204_NO_CONTENT)
def reorder_folders(body: proj.FolderOrder, services: ServicesDep) -> Response:
    proj.reorder_folders(services, body)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/folders/{folder_id}",
    tags=["folders"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=NOT_FOUND,
)
def delete_folder(folder_id: str, services: ServicesDep) -> Response:
    if not proj.delete_folder(services, folder_id):
        raise _404("Folder")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
