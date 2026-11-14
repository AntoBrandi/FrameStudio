"""Elements API (plan step M4.1): characters, locations and props, their
folders, and CSV/zip export and import."""

from __future__ import annotations

import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel, Field

from framestudio.api.deps import ServicesDep
from framestudio.db.models import Project
from framestudio.library import elements as el

router = APIRouter(prefix="/api", tags=["elements"])

NOT_FOUND = {404: {"description": "Not found"}}
CONFLICT = {409: {"description": "Handle already taken"}}
#: Largest element zip accepted for import.
MAX_IMPORT_BYTES = 2 * 1024**3


def _404(what: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"{what} not found")


def _lookup(exc: LookupError) -> HTTPException:
    return _404(str(exc).capitalize())


def _conflict(exc: el.ElementConflictError) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, str(exc))


@router.get("/elements")
def list_elements(
    services: ServicesDep,
    project_id: str | None = None,
    include_global: bool = True,
    category: el.Category | None = None,
    status_: Annotated[el.Status | None, Query(alias="status")] = None,
    folder_id: str | None = None,
    q: str | None = None,
) -> list[el.ElementOut]:
    """A project's elements and the global ones (or only the global ones
    without ``project_id``)."""
    return el.list_elements(
        services,
        project_id=project_id,
        include_global=include_global,
        category=category,
        status=status_,
        folder_id=folder_id,
        q=q,
    )


@router.post("/elements", status_code=status.HTTP_201_CREATED, responses={**NOT_FOUND, **CONFLICT})
def create_element(body: el.ElementCreate, services: ServicesDep) -> el.ElementOut:
    try:
        return el.create_element(services, body)
    except LookupError as exc:
        raise _lookup(exc) from None
    except el.ElementConflictError as exc:
        raise _conflict(exc) from None


@router.get("/elements/folders")
def list_element_folders(
    services: ServicesDep, project_id: str | None = None
) -> list[el.ElementFolderOut]:
    return el.list_folders(services, project_id)


@router.post("/elements/folders", status_code=status.HTTP_201_CREATED, responses=NOT_FOUND)
def create_element_folder(
    body: el.ElementFolderCreate, services: ServicesDep
) -> el.ElementFolderOut:
    try:
        return el.create_folder(services, body)
    except LookupError as exc:
        raise _lookup(exc) from None


@router.patch(
    "/elements/folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT, responses=NOT_FOUND
)
def patch_element_folder(
    folder_id: str, body: el.ElementFolderPatch, services: ServicesDep
) -> Response:
    if not el.patch_folder(services, folder_id, body):
        raise _404("Element folder")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/elements/folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT, responses=NOT_FOUND
)
def delete_element_folder(folder_id: str, services: ServicesDep) -> Response:
    if not el.delete_folder(services, folder_id):
        raise _404("Element folder")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/elements/export",
    responses={
        200: {
            "content": {"text/csv": {}, "application/zip": {}},
            "description": "The elements as CSV, or a zip with their media",
        },
        **NOT_FOUND,
    },
    response_class=Response,
)
async def export_elements(
    services: ServicesDep,
    project_id: str | None = None,
    format_: Annotated[Literal["csv", "zip"], Query(alias="format")] = "zip",
) -> Response:
    """Export a project's elements (or the global ones). The zip carries the
    media; the CSV names them by content hash."""
    with services.db.session() as session:
        project = session.get(Project, project_id) if project_id else None
        if project_id and (project is None or project.deleted_at is not None):
            raise _404("Project")
        stem = f"{project.slug}-elements" if project else "global-elements"
    if format_ == "csv":
        text = await asyncio.to_thread(el.export_csv, services, project_id)
        content, media_type = text.encode("utf-8"), "text/csv; charset=utf-8"
    else:
        content = await asyncio.to_thread(el.export_zip, services, project_id)
        media_type = "application/zip"
    return Response(
        content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{stem}.{format_}"'},
    )


@router.post("/elements/import", responses={**NOT_FOUND, 422: {"description": "Unreadable"}})
def import_elements(
    services: ServicesDep,
    file: Annotated[UploadFile, File(description="An elements CSV or export zip")],
    project_id: Annotated[str | None, Form()] = None,
) -> el.ImportResult:
    """Import elements exported from FrameStudio. Handles already taken here
    get a suffix; CSV media are matched by content to assets in this library."""
    data = file.file.read(MAX_IMPORT_BYTES + 1)
    if len(data) > MAX_IMPORT_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "The file is too large to import.")
    name = (file.filename or "").lower()
    try:
        if name.endswith(".csv") or not data.startswith(b"PK"):
            return el.import_csv(services, data.decode("utf-8-sig"), project_id)
        return el.import_zip(services, data, project_id)
    except LookupError as exc:
        raise _lookup(exc) from None
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None


@router.get("/elements/{element_id}", responses=NOT_FOUND)
def get_element(element_id: str, services: ServicesDep) -> el.ElementOut:
    element = el.get_element(services, element_id)
    if element is None:
        raise _404("Element")
    return element


@router.patch("/elements/{element_id}", responses={**NOT_FOUND, **CONFLICT})
def patch_element(element_id: str, body: el.ElementPatch, services: ServicesDep) -> el.ElementOut:
    try:
        element = el.patch_element(services, element_id, body)
    except LookupError as exc:
        raise _lookup(exc) from None
    except el.ElementConflictError as exc:
        raise _conflict(exc) from None
    if element is None:
        raise _404("Element")
    return element


@router.delete(
    "/elements/{element_id}", status_code=status.HTTP_204_NO_CONTENT, responses=NOT_FOUND
)
def delete_element(element_id: str, services: ServicesDep) -> Response:
    """Delete the element; its pictures and clips stay in the library."""
    if not el.delete_element(services, element_id):
        raise _404("Element")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/elements/{element_id}/media", responses=NOT_FOUND)
def add_element_media(
    element_id: str, body: el.ElementMediaChange, services: ServicesDep
) -> el.ElementOut:
    """Attach library assets (at the end; the first ever becomes primary)."""
    try:
        element = el.add_media(services, element_id, body)
    except LookupError as exc:
        raise _lookup(exc) from None
    if element is None:
        raise _404("Element")
    return element


class MediaOrder(BaseModel):
    #: The media in order; new assets are attached, any left out are detached.
    asset_ids: list[str] = Field(max_length=50)


@router.put("/elements/{element_id}/media", responses=NOT_FOUND)
def order_element_media(element_id: str, body: MediaOrder, services: ServicesDep) -> el.ElementOut:
    """Set the media: reorder (the first becomes primary), attach or detach."""
    try:
        element = el.order_media(services, element_id, body.asset_ids)
    except LookupError as exc:
        raise _lookup(exc) from None
    if element is None:
        raise _404("Element")
    return element
