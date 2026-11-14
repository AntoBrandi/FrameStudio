"""M5.6: the Studio workspace's project export (the slug names the exported
folders; each folder is a subdirectory; trashed assets stay out)."""

from __future__ import annotations

import io
import zipfile

from fastapi.testclient import TestClient

from .test_library import CORPUS, services_of, upload


def names(response_bytes: bytes) -> dict[str, int]:
    with zipfile.ZipFile(io.BytesIO(response_bytes)) as archive:
        assert all(info.compress_type == zipfile.ZIP_STORED for info in archive.infolist())
        return {info.filename: info.file_size for info in archive.infolist()}


def test_a_project_exports_as_a_zip_named_by_its_slug(authed: TestClient) -> None:
    project = authed.post("/api/projects", json={"name": "Night Diner"}).json()
    pid = project["id"]
    shots = authed.post("/api/folders", json={"name": "Shots & Takes", "project_id": pid}).json()
    empty = authed.post("/api/folders", json={"name": "Props", "project_id": pid}).json()
    upload(authed, "still.png", "clip.mp4", project_id=pid)
    [in_folder] = upload(authed, "rotated.jpg", project_id=pid, folder_id=shots["id"])
    [trashed] = upload(authed, "alpha.webp", project_id=pid)
    authed.post("/api/assets/trash", json={"asset_ids": [trashed["asset"]["id"]]})
    upload(authed, "anim.gif")  # in the global library, not the project
    authed.patch(f"/api/projects/{pid}", json={"brief": "## Context\nA diner at night.\n"})

    response = authed.get(f"/api/projects/{pid}/export")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert 'filename="night-diner.zip"' in response.headers["content-disposition"]
    files = names(response.content)
    assert set(files) == {
        "night-diner/brief.md",
        "night-diner/still.png",
        "night-diner/clip.mp4",
        "night-diner/shots-takes/rotated.jpg",
    }
    assert files["night-diner/clip.mp4"] == (CORPUS / "clip.mp4").stat().st_size
    assert in_folder["asset"]["folder_id"] == shots["id"]

    # One folder on its own; an empty one is an empty zip; the brief stays out.
    one = authed.get(f"/api/projects/{pid}/export", params={"folder_id": shots["id"]})
    assert 'filename="night-diner-shots-takes.zip"' in one.headers["content-disposition"]
    assert set(names(one.content)) == {"night-diner/shots-takes/rotated.jpg"}
    assert (
        names(authed.get(f"/api/projects/{pid}/export", params={"folder_id": empty["id"]}).content)
        == {}
    )

    # The temporary zip is gone once sent.
    tmp = services_of(authed).media.tmp
    assert not list(tmp.glob("*.zip"))


def png(colour: tuple[int, int, int]) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (32, 24), colour).save(buffer, "PNG")
    return buffer.getvalue()


def test_export_names_never_collide_and_unknowns_are_404(authed: TestClient) -> None:
    pid = authed.post("/api/projects", json={"name": "Twins"}).json()["id"]
    for colour in [(200, 40, 40), (40, 200, 40)]:
        response = authed.post(
            "/api/assets/upload",
            files=[("files", ("take.png", png(colour)))],
            data={"project_id": pid},
        )
        assert response.status_code == 200, response.text
    files = names(authed.get(f"/api/projects/{pid}/export").content)
    assert sorted(files) == ["twins/take-2.png", "twins/take.png"]

    assert authed.get("/api/projects/nope/export").status_code == 404
    assert (
        authed.get(f"/api/projects/{pid}/export", params={"folder_id": "nope"}).status_code == 404
    )
    other = authed.post("/api/projects", json={"name": "Other"}).json()["id"]
    folder = authed.post("/api/folders", json={"name": "F", "project_id": other}).json()
    elsewhere = authed.get(f"/api/projects/{pid}/export", params={"folder_id": folder["id"]})
    assert elsewhere.status_code == 404
    authed.post(f"/api/projects/{pid}/trash")
    assert authed.get(f"/api/projects/{pid}/export").status_code == 404
