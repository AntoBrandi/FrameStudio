"""M4.1: elements (characters, locations, props) with handles unique per scope,
media with a primary, folders, and CSV/zip export that imports back without loss."""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from framestudio.app import create_app
from framestudio.config import Config
from framestudio.library.elements import slugify

from .conftest import AUTH, BASE_URL, TOKEN


def png(colour: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (320, 240), colour).save(buffer, format="PNG")
    return buffer.getvalue()


def upload(client: TestClient, colour: tuple[int, int, int], project_id: str | None = None) -> str:
    data = {"project_id": project_id} if project_id else {}
    response = client.post(
        "/api/assets/upload", files=[("files", (f"{colour}.png", png(colour)))], data=data
    )
    assert response.status_code == 200, response.text
    return response.json()[0]["asset"]["id"]  # type: ignore[no-any-return]


def create(client: TestClient, **body: Any) -> dict[str, Any]:
    response = client.post("/api/elements", json=body)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def project(client: TestClient, name: str) -> str:
    response = client.post("/api/projects", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]  # type: ignore[no-any-return]


def make_client(root: Path) -> TestClient:
    config = Config(data_dir=root / "data", token=TOKEN, ui_dist=root / "ui")
    return TestClient(create_app(config), base_url=BASE_URL, headers=AUTH)


@pytest.fixture
def other(tmp_path: Path) -> Iterator[TestClient]:
    """A second, empty library."""
    with make_client(tmp_path / "other") as client:
        yield client


# ---------------------------------------------------------------- handles


def test_slugify() -> None:
    assert slugify("Maya Ortiz") == "maya-ortiz"
    assert slugify("  Café  del Mar! ") == "cafe-del-mar"
    assert slugify("東京") == "element"
    assert slugify("東京", "location") == "location"
    assert len(slugify("x" * 200)) == 40


def test_handles_are_unique_per_scope(authed: TestClient) -> None:
    first = create(authed, name="Maya Ortiz", category="character")
    second = create(authed, name="Maya  Ortiz", category="character")
    third = create(authed, name="maya ortiz")
    assert [first["handle"], second["handle"], third["handle"]] == [
        "maya-ortiz",
        "maya-ortiz-2",
        "maya-ortiz-3",
    ]
    # A project may reuse a global handle; its own copies are numbered again.
    film = project(authed, "Night Film")
    local = create(authed, name="Maya Ortiz", project_id=film)
    assert local["handle"] == "maya-ortiz" and local["project_id"] == film
    # A handle asked for explicitly must be free.
    taken = authed.post("/api/elements", json={"name": "Other", "handle": "maya-ortiz"})
    assert taken.status_code == 409 and "@maya-ortiz" in taken.json()["detail"]
    bad = authed.post("/api/elements", json={"name": "Other", "handle": "Not A Handle"})
    assert bad.status_code == 422


def test_rename_and_regenerate_the_handle(authed: TestClient) -> None:
    maya = create(authed, name="Maya")
    create(authed, name="Lighthouse", category="location")
    patched = authed.patch(f"/api/elements/{maya['id']}", json={"handle": "lighthouse"})
    assert patched.status_code == 409
    renamed = authed.patch(f"/api/elements/{maya['id']}", json={"handle": "maya_o"}).json()
    assert renamed["handle"] == "maya_o"
    regenerated = authed.patch(
        f"/api/elements/{maya['id']}", json={"name": "Lighthouse", "regenerate_handle": True}
    ).json()
    assert regenerated["handle"] == "lighthouse-2"
    # Regenerating keeps a handle that is already its own.
    again = authed.patch(f"/api/elements/{maya['id']}", json={"regenerate_handle": True}).json()
    assert again["handle"] == "lighthouse-2"


# ---------------------------------------------------------------- editing


def test_create_edit_delete(authed: TestClient) -> None:
    red, blue, green = (upload(authed, c) for c in [(200, 0, 0), (0, 0, 200), (0, 200, 0)])
    maya = create(
        authed,
        name="Maya Ortiz",
        category="character",
        description="Thirties, short dark hair, yellow raincoat.",
        properties={"age": "34", "wardrobe": "raincoat"},
        asset_ids=[red, blue],
    )
    assert maya["version"] == 1 and maya["status"] == "draft"
    assert [(m["asset_id"], m["role"]) for m in maya["media"]] == [
        (red, "primary"),
        (blue, "angle"),
    ]
    assert maya["cover_url"] == f"/api/assets/{red}/thumb"
    assert maya["used_in"] == 0

    edited = authed.patch(
        f"/api/elements/{maya['id']}",
        json={"description": "Thirties, buzz cut, yellow raincoat.", "status": "ready"},
    ).json()
    assert edited["version"] == 2 and edited["status"] == "ready"
    # Same description again: not a new version.
    same = authed.patch(
        f"/api/elements/{maya['id']}", json={"description": edited["description"]}
    ).json()
    assert same["version"] == 2

    added = authed.post(f"/api/elements/{maya['id']}/media", json={"asset_ids": [green, red]})
    assert [m["asset_id"] for m in added.json()["media"]] == [red, blue, green]
    ordered = authed.put(f"/api/elements/{maya['id']}/media", json={"asset_ids": [green, red]})
    body = ordered.json()
    assert [(m["asset_id"], m["role"]) for m in body["media"]] == [
        (green, "primary"),
        (red, "angle"),
    ]
    assert body["version"] == 4
    unknown = authed.put(f"/api/elements/{maya['id']}/media", json={"asset_ids": ["nope"]})
    assert unknown.status_code == 404
    # Setting the list also attaches new assets.
    full = authed.put(f"/api/elements/{maya['id']}/media", json={"asset_ids": [green, red, blue]})
    assert [m["asset_id"] for m in full.json()["media"]] == [green, red, blue]

    # Trashed media drop out of the element; restored, they come back.
    authed.post("/api/assets/trash", json={"asset_ids": [green]})
    media = authed.get(f"/api/elements/{maya['id']}").json()["media"]
    assert [m["asset_id"] for m in media] == [red, blue]
    authed.post("/api/assets/restore", json={"asset_ids": [green]})
    media = authed.get(f"/api/elements/{maya['id']}").json()["media"]
    assert (media[0]["asset_id"], media[0]["role"]) == (green, "primary")

    assert authed.delete(f"/api/elements/{maya['id']}").status_code == 204
    assert authed.get(f"/api/elements/{maya['id']}").status_code == 404
    # Its media stay in the library.
    assert authed.get(f"/api/assets/{red}").status_code == 200


def test_listing_filters(authed: TestClient) -> None:
    film = project(authed, "Harbour")
    create(authed, name="Maya", category="character", status="ready")
    create(authed, name="Pier", category="location", project_id=film, description="wooden, fog")
    create(authed, name="Lantern", category="prop", project_id=film)

    def names(**params: Any) -> list[str]:
        response = authed.get("/api/elements", params=params)
        assert response.status_code == 200, response.text
        return [e["name"] for e in response.json()]

    assert names() == ["Maya"]  # global only
    assert names(project_id=film) == ["Lantern", "Maya", "Pier"]
    assert names(project_id=film, include_global=False) == ["Lantern", "Pier"]
    assert names(project_id=film, category="location") == ["Pier"]
    assert names(project_id=film, status="ready") == ["Maya"]
    assert names(project_id=film, q="fog") == ["Pier"]
    assert names(project_id=film, q="@lan") == ["Lantern"]


def test_folders(authed: TestClient) -> None:
    film = project(authed, "Harbour")
    cast = authed.post("/api/elements/folders", json={"name": "Cast", "project_id": film}).json()
    maya = create(authed, name="Maya", project_id=film, folder_id=cast["id"])
    assert maya["folder_id"] == cast["id"]
    listed = authed.get("/api/elements/folders", params={"project_id": film}).json()
    assert [(f["name"], f["element_count"]) for f in listed] == [("Cast", 1)]
    assert authed.get("/api/elements/folders").json() == []  # global folders
    # A global element cannot go in a project's folder.
    stray = authed.post("/api/elements", json={"name": "Stray", "folder_id": cast["id"]})
    assert stray.status_code == 404
    in_folder = authed.get(
        "/api/elements", params={"project_id": film, "folder_id": cast["id"]}
    ).json()
    assert [e["name"] for e in in_folder] == ["Maya"]
    authed.patch(f"/api/elements/folders/{cast['id']}", json={"name": "Main cast"})
    assert authed.delete(f"/api/elements/folders/{cast['id']}").status_code == 204
    assert authed.get(f"/api/elements/{maya['id']}").json()["folder_id"] is None


def test_unknown_things(authed: TestClient) -> None:
    assert authed.get("/api/elements/nope").status_code == 404
    assert authed.patch("/api/elements/nope", json={"name": "x"}).status_code == 404
    assert authed.delete("/api/elements/nope").status_code == 404
    assert authed.post("/api/elements", json={"name": "x", "project_id": "nope"}).status_code == 404
    assert (
        authed.post("/api/elements", json={"name": "x", "asset_ids": ["nope"]}).status_code == 404
    )


def test_needs_the_access_token(client: TestClient) -> None:
    assert client.get("/api/elements").status_code == 401
    assert client.get("/api/elements/export").status_code == 401


# ---------------------------------------------------------------- export and import


def comparable(element: dict[str, Any]) -> dict[str, Any]:
    keep = ("handle", "name", "category", "status", "version", "description", "properties")
    return {k: element[k] for k in keep} | {"roles": [m["role"] for m in element["media"]]}


def seed(client: TestClient, project_id: str | None = None) -> list[dict[str, Any]]:
    red, blue = upload(client, (200, 0, 0), project_id), upload(client, (0, 0, 200), project_id)
    folder = client.post(
        "/api/elements/folders", json={"name": "Cast", "project_id": project_id}
    ).json()
    create(
        client,
        name="Maya Ortiz",
        category="character",
        status="ready",
        description='Says "hello", then, quietly:\nbye.',
        properties={"age": "34", "notes": 'comma, quote " and ünïcode'},
        asset_ids=[red, blue],
        project_id=project_id,
        folder_id=folder["id"],
    )
    pier = create(client, name="Pier", category="location", project_id=project_id)
    client.patch(f"/api/elements/{pier['id']}", json={"description": "wooden"})
    create(client, name="Lantern", category="prop", asset_ids=[blue], project_id=project_id)
    response = client.get("/api/elements", params={"project_id": project_id} if project_id else {})
    return sorted(response.json(), key=lambda e: e["handle"])  # type: ignore[no-any-return]


def test_zip_round_trip_into_another_library(authed: TestClient, other: TestClient) -> None:
    before = seed(authed)
    response = authed.get("/api/elements/export", params={"format": "zip"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert 'filename="global-elements.zip"' in response.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = sorted(archive.namelist())
    assert names[:2] == ["elements.csv", "elements.json"]
    assert len([n for n in names if n.startswith("media/")]) == 2  # blue is stored once

    imported = other.post(
        "/api/elements/import", files={"file": ("global-elements.zip", response.content)}
    )
    assert imported.status_code == 200, imported.text
    result = imported.json()
    assert sorted(result["created"]) == ["lantern", "maya-ortiz", "pier"]
    assert result["renamed"] == {} and result["problems"] == []
    after = sorted(other.get("/api/elements").json(), key=lambda e: e["handle"])
    assert [comparable(e) for e in after] == [comparable(e) for e in before]
    # The media are the same files, and the folder came along.
    sha = {a["id"]: a["sha256"] for a in authed.get("/api/assets").json()["items"]}
    other_sha = {a["id"]: a["sha256"] for a in other.get("/api/assets").json()["items"]}
    for old, new in zip(before, after, strict=True):
        assert [sha[m["asset_id"]] for m in old["media"]] == [
            other_sha[m["asset_id"]] for m in new["media"]
        ]
    [folder] = other.get("/api/elements/folders").json()
    assert folder["name"] == "Cast" and folder["element_count"] == 1


def test_zip_import_into_a_project_twice(authed: TestClient) -> None:
    seed(authed)
    exported = authed.get("/api/elements/export").content
    film = project(authed, "Harbour")
    files = {"file": ("e.zip", exported)}
    first = authed.post("/api/elements/import", files=files, data={"project_id": film}).json()
    assert first["renamed"] == {}  # a project's handles are its own
    second = authed.post("/api/elements/import", files=files, data={"project_id": film}).json()
    assert second["renamed"] == {
        "lantern": "lantern-2",
        "maya-ortiz": "maya-ortiz-2",
        "pier": "pier-2",
    }
    listed = authed.get("/api/elements", params={"project_id": film, "include_global": False})
    assert len(listed.json()) == 6
    # The second import reused the project's copies of the media.
    project_assets = authed.get("/api/assets", params={"project_id": film}).json()["items"]
    assert len(project_assets) == 2


def test_csv_round_trip(authed: TestClient, tmp_path: Path) -> None:
    film = project(authed, "Harbour")
    before = seed(authed, film)
    response = authed.get("/api/elements/export", params={"format": "csv", "project_id": film})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert 'filename="harbour-elements.csv"' in response.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert [r["handle"] for r in rows] == ["lantern", "maya-ortiz", "pier"]
    assert rows[1]["scope"] == "harbour" and rows[1]["folder"] == "Cast"
    assert rows[1]["media"].startswith("primary:")

    # Into another project of the same library: the media are found by content.
    second = project(authed, "Sequel")
    imported = authed.post(
        "/api/elements/import",
        files={"file": ("harbour-elements.csv", response.content)},
        data={"project_id": second},
    ).json()
    assert imported["problems"] == []
    after = authed.get("/api/elements", params={"project_id": second, "include_global": False})
    assert [comparable(e) for e in after.json()] == [comparable(e) for e in before]


def test_csv_without_the_media_reports_them(authed: TestClient, other: TestClient) -> None:
    seed(authed)
    exported = authed.get("/api/elements/export", params={"format": "csv"}).content
    result = other.post("/api/elements/import", files={"file": ("e.csv", exported)}).json()
    assert len(result["created"]) == 3
    assert len(result["problems"]) == 3  # red and blue for Maya, blue for the lantern
    assert all("is not in this library" in p for p in result["problems"])


def test_import_refuses_junk(authed: TestClient) -> None:
    empty_zip = io.BytesIO()
    with zipfile.ZipFile(empty_zip, "w") as archive:
        archive.writestr("readme.txt", "hi")
    response = authed.post("/api/elements/import", files={"file": ("x.zip", empty_zip.getvalue())})
    assert response.status_code == 422 and "elements.json" in response.json()["detail"]
    binary = authed.post("/api/elements/import", files={"file": ("x.csv", b"\xff\xfe\x00junk")})
    assert binary.status_code == 422
    # A bare CSV header and nothing else imports nothing.
    header = authed.post("/api/elements/import", files={"file": ("x.csv", b"handle,name\n")})
    assert header.json()["created"] == []
    loose = authed.post(
        "/api/elements/import",
        files={"file": ("x.csv", b"handle,name,version,properties\nBad Handle!,Odd,zero,[1]\n")},
    ).json()
    assert loose["created"] == ["bad-handle"]
    [odd] = authed.get("/api/elements", params={"q": "odd"}).json()
    assert (odd["version"], odd["properties"], odd["category"]) == (1, {}, "auto")
