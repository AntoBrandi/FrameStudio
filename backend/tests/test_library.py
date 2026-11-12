"""M1: ingest, projects, folders, filters, search, viewer actions, trash and storage."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from framestudio.config import Config
from framestudio.db.models import Asset, Generation
from framestudio.library import search
from framestudio.library.search import parse_query
from framestudio.services import Services

from .conftest import AUTH, BASE_URL

CORPUS = Path(__file__).parent / "fixtures" / "media"
SNAPSHOT = Path(__file__).parent / "snapshots" / "ingest_metadata.json"
GOOD = [
    "still.png", "rotated.jpg", "alpha.webp", "anim.gif",
    "clip.mp4", "silent.mov", "clip.webm", "tone.wav", "song.mp3",
]  # fmt: skip
BAD = {
    "truncated.png": "corrupt_file",
    "garbage.mp4": "corrupt_file",
    "empty.jpg": "corrupt_file",
    "notes.txt": "unsupported_type",
}


def upload(client: TestClient, *names: str, **form: str) -> list[dict[str, Any]]:
    files = [("files", (name, (CORPUS / name).read_bytes())) for name in names]
    response = client.post("/api/assets/upload", files=files, data=form)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def assets(client: TestClient, **params: Any) -> dict[str, Any]:
    response = client.get("/api/assets", params=params)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def services_of(client: TestClient) -> Services:
    return client.app.state.services  # type: ignore[attr-defined,no-any-return]


# ------------------------------------------------------------------ M1.1


def _summary(item: dict[str, Any]) -> dict[str, Any]:
    asset = item["asset"]
    return {
        "kind": asset["kind"],
        "mime": asset["mime"],
        "size": [asset["width"], asset["height"]],
        "duration": round(asset["duration"], 1) if asset["duration"] else None,
        "fps": asset["fps"],
        "has_audio": asset["has_audio"],
        "codecs": [asset["video_codec"], asset["audio_codec"]],
        "derived": [
            bool(asset["thumb_url"]),
            bool(asset["poster_url"]),
            bool(asset["preview_url"]),
        ],
    }


def test_corpus_metadata_matches_snapshot(authed: TestClient) -> None:
    results = upload(authed, *GOOD)
    assert [r["error"] for r in results] == [None] * len(GOOD)
    actual = {r["name"]: _summary(r) for r in results}
    if os.environ.get("FRAMESTUDIO_UPDATE_SNAPSHOTS") == "1":
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n")
    assert actual == json.loads(SNAPSHOT.read_text())


def test_derived_files_are_served(authed: TestClient) -> None:
    by_name = {r["name"]: r["asset"] for r in upload(authed, "still.png", "clip.mp4", "tone.wav")}
    for name in ("still.png", "clip.mp4", "tone.wav"):
        thumb = authed.get(by_name[name]["thumb_url"])
        assert thumb.status_code == 200
        assert thumb.headers["content-type"] == "image/webp"
    preview = authed.get(by_name["clip.mp4"]["preview_url"])
    assert preview.headers["content-type"] == "video/mp4"
    assert len(preview.content) > 0


@pytest.mark.parametrize(("name", "code"), sorted(BAD.items()))
def test_damaged_or_unsupported_files_are_rejected(
    authed: TestClient, name: str, code: str
) -> None:
    [result] = upload(authed, name)
    assert result["asset"] is None
    assert result["error_code"] == code
    assert name in result["error"]
    assert assets(authed)["total"] == 0
    tmp = services_of(authed).paths.tmp
    assert list(tmp.iterdir()) == []  # nothing left behind


def test_duplicate_adds_nothing_and_other_projects_share_the_file(authed: TestClient) -> None:
    [first] = upload(authed, "still.png")
    [again] = upload(authed, "still.png")
    assert again["duplicate"] is True
    assert again["asset"]["id"] == first["asset"]["id"]
    assert assets(authed)["total"] == 1

    project = authed.post("/api/projects", json={"name": "Other"}).json()
    [elsewhere] = upload(authed, "still.png", project_id=project["id"])
    assert elsewhere["duplicate"] is False
    assert elsewhere["asset"]["sha256"] == first["asset"]["sha256"]
    media = services_of(authed).paths.media
    stored = [p for p in media.rglob("*.png")]
    assert len(stored) == 1  # one file on disk, two assets


def test_duplicate_of_a_trashed_asset_restores_it(authed: TestClient) -> None:
    [first] = upload(authed, "still.png")
    authed.post("/api/assets/trash", json={"asset_ids": [first["asset"]["id"]]})
    [again] = upload(authed, "still.png")
    assert again["duplicate"] is True
    assert again["asset"]["deleted_at"] is None
    assert assets(authed)["total"] == 1


def test_file_ranges_and_download(authed: TestClient) -> None:
    [item] = upload(authed, "clip.mp4")
    asset = item["asset"]
    partial = authed.get(asset["url"], headers={"Range": "bytes=0-99"})
    assert partial.status_code == 206
    assert len(partial.content) == 100
    download = authed.get(asset["url"], params={"download": "true"})
    assert download.headers["content-disposition"].startswith("attachment")
    assert "clip.mp4" in download.headers["content-disposition"]
    assert authed.get(f"/api/assets/{asset['id']}").json()["last_downloaded_at"] is not None


def test_upload_into_unknown_folder_is_404(authed: TestClient) -> None:
    files = [("files", ("still.png", (CORPUS / "still.png").read_bytes()))]
    response = authed.post("/api/assets/upload", files=files, data={"folder_id": "nope"})
    assert response.status_code == 404


# ------------------------------------------------------------------ M1.2


def test_project_lifecycle(authed: TestClient) -> None:
    created = authed.post(
        "/api/projects", json={"name": "Night Diner", "description": "A short noir"}
    )
    assert created.status_code == 201
    project = created.json()
    assert project["slug"] == "night-diner"
    assert "## Context" in project["brief"]
    assert project["cover_url"] is None

    second = authed.post("/api/projects", json={"name": "Night  Diner!"}).json()
    assert second["slug"] == "night-diner-2"

    renamed = authed.patch(f"/api/projects/{project['id']}", json={"name": "Night Diner II"}).json()
    assert renamed["slug"] == "night-diner"  # slugs stay put unless changed explicitly
    clash = authed.patch(f"/api/projects/{project['id']}", json={"slug": "night-diner-2"})
    assert clash.status_code == 409
    bad = authed.patch(f"/api/projects/{project['id']}", json={"slug": "Not A Slug"})
    assert bad.status_code == 422
    too_long = authed.post("/api/projects", json={"name": "x", "description": "d" * 141})
    assert too_long.status_code == 422

    [a, b] = upload(authed, "still.png", "alpha.webp", project_id=project["id"])
    detail = authed.get(f"/api/projects/{project['id']}").json()
    assert detail["asset_count"] == 2
    assert detail["cover_url"] == f"/api/assets/{b['asset']['id']}/thumb"  # newest by default
    chosen = authed.patch(
        f"/api/projects/{project['id']}", json={"cover_asset_id": a["asset"]["id"]}
    ).json()
    assert chosen["cover_url"] == f"/api/assets/{a['asset']['id']}/thumb"

    trashed = authed.post(f"/api/projects/{project['id']}/trash").json()
    assert trashed["deleted_at"] is not None
    assert [p["id"] for p in authed.get("/api/projects").json()] == [second["id"]]
    assert [p["id"] for p in authed.get("/api/projects", params={"trashed": True}).json()] == [
        project["id"]
    ]
    authed.post(f"/api/projects/{project['id']}/restore")
    assert len(authed.get("/api/projects").json()) == 2


def test_project_search_and_sort(authed: TestClient) -> None:
    for name in ("Bravo", "alpha", "Charlie"):
        authed.post("/api/projects", json={"name": name, "description": f"about {name}"})
    by_name = [p["name"] for p in authed.get("/api/projects", params={"sort": "name"}).json()]
    assert by_name == ["alpha", "Bravo", "Charlie"]
    newest = [p["name"] for p in authed.get("/api/projects", params={"sort": "created"}).json()]
    assert newest == ["Charlie", "alpha", "Bravo"]
    found = authed.get("/api/projects", params={"q": "CHAR"}).json()
    assert [p["name"] for p in found] == ["Charlie"]


def test_project_scoped_and_global_assets(authed: TestClient) -> None:
    project = authed.post("/api/projects", json={"name": "P"}).json()
    upload(authed, "still.png", project_id=project["id"])
    upload(authed, "alpha.webp")
    assert assets(authed)["total"] == 2
    assert assets(authed, project_id=project["id"])["total"] == 1
    assert assets(authed, project_id="none")["total"] == 1


def test_generations_land_in_the_chosen_project(authed: TestClient) -> None:
    project = authed.post("/api/projects", json={"name": "P"}).json()
    [gen] = authed.post(
        "/api/generations",
        json={"prompt": "x", "project_id": project["id"], "params": {"latency_seconds": 0}},
    ).json()
    assert gen["project_id"] == project["id"]
    from .conftest import wait_until

    wait_until(lambda: assets(authed, project_id=project["id"])["total"] == 1)
    missing = authed.post("/api/generations", json={"prompt": "x", "project_id": "nope"})
    assert missing.status_code == 404


# ------------------------------------------------------------------ M1.3


def test_folders_counts_and_moves(authed: TestClient) -> None:
    characters = authed.post("/api/folders", json={"name": "Characters", "color": "rose"}).json()
    locations = authed.post("/api/folders", json={"name": "Locations"}).json()
    assert authed.post("/api/folders", json={"name": "characters"}).status_code == 409
    ids = _seed(services_of(authed), 20)
    moved = authed.post("/api/assets/move", json={"asset_ids": ids, "folder_id": characters["id"]})
    assert moved.json() == {"updated": 20}
    counts = {f["name"]: f["asset_count"] for f in authed.get("/api/folders").json()}
    assert counts == {"Characters": 20, "Locations": 0}
    assert assets(authed, folder_id=characters["id"])["total"] == 20

    authed.post("/api/assets/move", json={"asset_ids": ids[:5], "folder_id": locations["id"]})
    authed.post("/api/assets/trash", json={"asset_ids": ids[5:7]})
    counts = {f["name"]: f["asset_count"] for f in authed.get("/api/folders").json()}
    assert counts == {"Characters": 13, "Locations": 5}  # trashed assets do not count

    by_count = authed.get("/api/folders", params={"sort": "count"}).json()
    assert [f["name"] for f in by_count] == ["Characters", "Locations"]
    assert [f["name"] for f in authed.get("/api/folders", params={"q": "loc"}).json()] == [
        "Locations"
    ]

    assert authed.delete(f"/api/folders/{characters['id']}").status_code == 204
    assert assets(authed, folder_id="none")["total"] == 13  # back in All assets, not deleted
    assert assets(authed)["total"] == 18


def test_project_folders_set_the_project(authed: TestClient) -> None:
    project = authed.post("/api/projects", json={"name": "P"}).json()
    props = authed.post("/api/folders", json={"name": "Props", "project_id": project["id"]}).json()
    assert authed.get("/api/folders").json() == []  # not a global folder
    assert [
        f["id"] for f in authed.get("/api/folders", params={"project_id": project["id"]}).json()
    ] == [props["id"]]
    ids = _seed(services_of(authed), 3)
    authed.post("/api/assets/move", json={"asset_ids": ids, "folder_id": props["id"]})
    assert assets(authed, project_id=project["id"])["total"] == 3
    authed.post("/api/assets/move", json={"asset_ids": ids, "project_id": None})
    assert assets(authed, project_id="none", folder_id="none")["total"] == 3


def test_folder_rename_and_reorder(authed: TestClient) -> None:
    a = authed.post("/api/folders", json={"name": "A"}).json()
    b = authed.post("/api/folders", json={"name": "B"}).json()
    authed.post("/api/folders/reorder", json={"folder_ids": [b["id"], a["id"]]})
    assert [f["name"] for f in authed.get("/api/folders").json()] == ["B", "A"]
    renamed = authed.patch(
        f"/api/folders/{a['id']}", json={"name": "Shots", "color": "teal"}
    ).json()
    assert (renamed["name"], renamed["color"]) == ("Shots", "teal")
    assert authed.patch(f"/api/folders/{a['id']}", json={"name": "b"}).status_code == 409


# ------------------------------------------------------------------ M1.4


def _seed(
    services: Services,
    count: int,
    *,
    start: datetime | None = None,
    prompts: list[str] | None = None,
    model: str = "mock",
    kinds: tuple[str, ...] = ("image", "video", "audio"),
) -> list[str]:
    """Insert assets straight into the database (no files), newest last."""
    start = start or datetime(2026, 10, 1, tzinfo=UTC)
    ids: list[str] = []
    with services.db.session() as session:
        for index in range(count):
            created = start + timedelta(hours=index)
            generation = Generation(
                batch_id="b",
                mode="image",
                model_id=model,
                provider=model,
                prompt=(prompts or [f"seeded asset {index}"])[index % len(prompts or [1])],
                params={},
                status="succeeded",
                progress=1.0,
                created_at=created,
            )
            session.add(generation)
            session.flush()
            asset = Asset(
                kind=kinds[index % len(kinds)],
                sha256=f"{index:064x}",
                path=f"fake/{index}.png",
                mime="image/png",
                size_bytes=1000 + index,
                width=100,
                height=100,
                source_generation_id=generation.id,
                created_at=created,
            )
            session.add(asset)
            session.flush()
            ids.append(asset.id)
        search.index_assets(session, ids)
    return ids


def test_cursor_pagination_returns_every_asset_once(authed: TestClient) -> None:
    ids = _seed(services_of(authed), 450)
    seen: list[str] = []
    cursor = None
    while True:
        page = assets(authed, limit=100, **({"cursor": cursor} if cursor else {}))
        assert page["total"] == 450
        seen += [a["id"] for a in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert seen == list(reversed(ids))
    oldest = assets(authed, sort="oldest", limit=3)["items"]
    assert [a["id"] for a in oldest] == ids[:3]
    assert authed.get("/api/assets", params={"cursor": "%%%"}).status_code == 400


def test_filters_combine(authed: TestClient) -> None:
    services = services_of(authed)
    ids = _seed(services, 12, model="mock")
    other = _seed(services, 3, model="flux", start=datetime(2026, 11, 5, tzinfo=UTC))
    [uploaded] = upload(authed, "still.png")
    authed.post("/api/assets/favorite", json={"asset_ids": ids[:4], "favorite": True})
    missing = authed.get(f"/api/assets/{ids[0]}/file", params={"download": "true"})
    assert missing.status_code == 404  # seeded rows have no file, and a 404 records nothing
    with services.db.session() as session:
        session.get(Asset, ids[1]).last_downloaded_at = datetime.now(UTC)

    assert assets(authed, kind="image")["total"] == 4 + 1 + 1
    assert assets(authed, kind=["video", "audio"])["total"] == 8 + 2
    assert assets(authed, model="flux")["total"] == 3
    assert assets(authed, model="upload")["items"][0]["id"] == uploaded["asset"]["id"]
    assert assets(authed, model=["flux", "upload"])["total"] == 4
    assert assets(authed, liked=True)["total"] == 4
    assert assets(authed, liked=True, kind="image")["total"] == 2
    assert assets(authed, downloaded=True)["total"] == 1
    assert assets(authed, provenance="uploaded")["total"] == 1
    window = assets(
        authed,
        created_after="2026-10-01T03:00:00Z",
        created_before="2026-10-01T06:00:00Z",
    )
    assert window["total"] == 3
    assert {a["id"] for a in window["items"]} == set(ids[3:6])
    assert assets(authed, model="flux", liked=True)["total"] == 0
    assert other


def test_ten_thousand_assets_page_quickly(authed: TestClient) -> None:
    import time

    _seed(services_of(authed), 10_000)
    started = time.perf_counter()
    page = assets(authed, limit=200)
    filtered = assets(authed, kind="video", liked=False, limit=200)
    elapsed = time.perf_counter() - started
    assert page["total"] == 10_000
    assert filtered["total"] > 3000
    assert elapsed < 2.0, f"listing took {elapsed:.2f}s"


# ------------------------------------------------------------------ M1.5


def test_search_finds_generations_by_prompt_words(authed: TestClient) -> None:
    ids = _seed(
        services_of(authed),
        3,
        prompts=["a tuna sandwich in a neon kitchen", "a lighthouse at dusk", "kitchen sink drama"],
    )
    found = assets(authed, q="tuna kitchen")
    assert [a["id"] for a in found["items"]] == [ids[0]]
    assert {a["id"] for a in assets(authed, q="kitch")["items"]} == {ids[0], ids[2]}  # prefix
    assert assets(authed, q="submarine")["total"] == 0


def test_search_ranks_prompt_matches_above_notes(authed: TestClient) -> None:
    ids = _seed(services_of(authed), 3, prompts=["a quiet harbour", "the harbour at night", "rain"])
    authed.patch(f"/api/assets/{ids[2]}", json={"notes": "reshoot near the harbour"})
    ranked = [a["id"] for a in assets(authed, q="harbour")["items"]]
    assert set(ranked[:2]) == {ids[0], ids[1]}
    assert ranked[2] == ids[2]


def test_search_handles_accents_and_cjk(authed: TestClient) -> None:
    ids = _seed(
        services_of(authed),
        4,
        prompts=["un café à Paris", "Straße bei Nacht", "日本の料理と夜の街", "서울의 밤거리"],
    )
    assert [a["id"] for a in assets(authed, q="cafe")["items"]] == [ids[0]]
    assert [a["id"] for a in assets(authed, q="straße")["items"]] == [ids[1]]
    assert [a["id"] for a in assets(authed, q="料理")["items"]] == [ids[2]]
    assert [a["id"] for a in assets(authed, q="夜の街")["items"]] == [ids[2]]
    assert assets(authed, q="理料")["total"] == 0  # characters must be adjacent and in order
    assert [a["id"] for a in assets(authed, q="밤거리")["items"]] == [ids[3]]


def test_search_by_file_name_and_notes(authed: TestClient) -> None:
    [item] = upload(authed, "rotated.jpg")
    asset_id = item["asset"]["id"]
    assert [a["id"] for a in assets(authed, q="rotated")["items"]] == [asset_id]
    authed.patch(f"/api/assets/{asset_id}", json={"notes": "use for the opening titles"})
    assert [a["id"] for a in assets(authed, q="opening titles")["items"]] == [asset_id]


def test_search_tokens_and_date_facet(authed: TestClient) -> None:
    services = services_of(authed)
    october = _seed(services, 6, prompts=["red door"], start=datetime(2026, 10, 3, tzinfo=UTC))
    _seed(services, 4, prompts=["red door"], start=datetime(2026, 11, 2, tzinfo=UTC))
    authed.post("/api/assets/favorite", json={"asset_ids": october[:2], "favorite": True})
    assert assets(authed, q="red type:video")["total"] == 3  # kinds cycle per seeding: 2 + 1
    assert assets(authed, q="door before:2026-10-31")["total"] == 6
    assert assets(authed, q="door after:2026-11-01")["total"] == 4
    assert assets(authed, q="is:liked")["total"] == 2
    facets = authed.get("/api/assets/facets", params={"q": "door"}).json()
    assert facets["months"] == [{"month": "2026-11", "count": 4}, {"month": "2026-10", "count": 6}]
    assert sum(facets["kinds"].values()) == 10
    assert facets["models"] == {"mock": 10}


def test_parse_query() -> None:
    parsed = parse_query(
        'type:video model:mock "neon kitchen" tuna is:liked after:2026-01-02 foo:bar'
    )
    assert parsed.kinds == ["video"]
    assert parsed.models == ["mock"]
    assert parsed.liked is True
    assert parsed.after == datetime(2026, 1, 2, tzinfo=UTC)
    assert parsed.terms == ["neon kitchen", "tuna", "foo:bar"]
    assert parsed.match() == '"neon"* AND "kitchen"* AND "tuna"* AND "foo"* AND "bar"*'
    assert parse_query('say "hi').match() is not None  # unbalanced quotes do not break FTS


# ------------------------------------------------------------------ M1.6


def test_viewed_and_downloaded_summary(authed: TestClient) -> None:
    [a, b] = upload(authed, "still.png", "alpha.webp")
    a_id, b_id = a["asset"]["id"], b["asset"]["id"]
    assert authed.post(f"/api/assets/{a_id}/viewed").status_code == 204
    authed.get(f"/api/assets/{b_id}/file", params={"download": "true"})
    summary = authed.get("/api/assets/summary").json()
    assert summary["last_viewed_id"] == a_id
    assert summary["last_downloaded_id"] == b_id
    assert summary["total"] == 2
    assert summary["models"] == ["upload"]
    location = authed.get(f"/api/assets/{a_id}/location").json()["path"]
    assert Path(location).is_file()


def test_patch_asset(authed: TestClient) -> None:
    [item] = upload(authed, "still.png")
    asset_id = item["asset"]["id"]
    patched = authed.patch(
        f"/api/assets/{asset_id}",
        json={"favorite": True, "notes": "hero frame", "original_name": "hero.png"},
    ).json()
    assert (patched["favorite"], patched["notes"], patched["original_name"]) == (
        True,
        "hero frame",
        "hero.png",
    )
    assert authed.patch("/api/assets/nope", json={"favorite": True}).status_code == 404


# ------------------------------------------------------------------ M1.7


def test_trash_restore_round_trip(authed: TestClient) -> None:
    [item] = upload(authed, "still.png")
    asset_id = item["asset"]["id"]
    authed.post("/api/assets/trash", json={"asset_ids": [asset_id]})
    assert assets(authed)["total"] == 0
    trash = assets(authed, trashed=True)
    assert [a["id"] for a in trash["items"]] == [asset_id]
    assert authed.get("/api/assets/summary").json()["trashed"] == 1
    authed.post("/api/assets/restore", json={"asset_ids": [asset_id]})
    assert assets(authed)["total"] == 1
    assert assets(authed, trashed=True)["total"] == 0


def test_trashed_results_leave_the_generation_feed(authed: TestClient) -> None:
    [gen] = authed.post(
        "/api/generations", json={"prompt": "x", "params": {"latency_seconds": 0}}
    ).json()
    from .conftest import wait_until

    done = wait_until(
        lambda: (g := authed.get(f"/api/generations/{gen['id']}").json())["asset"] and g, timeout=15
    )
    assert len(authed.get("/api/generations").json()) == 1
    authed.post("/api/assets/trash", json={"asset_ids": [done["asset"]["id"]]})
    assert authed.get("/api/generations").json() == []


def test_empty_trash_deletes_files_only_when_unused(authed: TestClient) -> None:
    project = authed.post("/api/projects", json={"name": "P"}).json()
    [global_copy] = upload(authed, "still.png")
    [project_copy] = upload(authed, "still.png", project_id=project["id"])  # same bytes
    [lonely] = upload(authed, "alpha.webp")
    services = services_of(authed)
    authed.post(
        "/api/assets/trash",
        json={"asset_ids": [global_copy["asset"]["id"], lonely["asset"]["id"]]},
    )
    result = authed.post("/api/trash/empty").json()
    assert result["assets_deleted"] == 2
    assert result["bytes_freed"] == (CORPUS / "alpha.webp").stat().st_size
    assert len(list(services.paths.media.rglob("*.png"))) == 1  # still used by the project copy
    assert list(services.paths.media.rglob("*.webp")) != []  # derived thumbs of the png remain
    assert authed.get(project_copy["asset"]["thumb_url"]).status_code == 200
    assert assets(authed, trashed=True)["total"] == 0


def test_emptying_a_trashed_project_keeps_its_media(authed: TestClient) -> None:
    project = authed.post("/api/projects", json={"name": "P"}).json()
    folder = authed.post("/api/folders", json={"name": "F", "project_id": project["id"]}).json()
    [item] = upload(authed, "still.png", folder_id=folder["id"])
    authed.post(f"/api/projects/{project['id']}/trash")
    result = authed.post("/api/trash/empty").json()
    assert result["projects_deleted"] == 1
    moved = authed.get(f"/api/assets/{item['asset']['id']}").json()
    assert (moved["project_id"], moved["folder_id"], moved["deleted_at"]) == (None, None, None)
    assert authed.get(f"/api/projects/{project['id']}").status_code == 404


def test_brief_survives_a_restart(config: Config) -> None:
    from framestudio.app import create_app

    with TestClient(create_app(config), base_url=BASE_URL, headers=AUTH) as first:
        project = first.post("/api/projects", json={"name": "Brief"}).json()
        brief = project["brief"].replace("## Context\n", "## Context\nA diner at 3 a.m.\n")
        first.patch(f"/api/projects/{project['id']}", json={"brief": brief})
    with TestClient(create_app(config), base_url=BASE_URL, headers=AUTH) as second:
        assert "A diner at 3 a.m." in second.get(f"/api/projects/{project['id']}").json()["brief"]


def test_storage_report(authed: TestClient) -> None:
    project = authed.post("/api/projects", json={"name": "Big"}).json()
    upload(authed, "clip.mp4", project_id=project["id"])
    [png] = upload(authed, "still.png")
    authed.post("/api/assets/trash", json={"asset_ids": [png["asset"]["id"]]})
    report = authed.get("/api/storage").json()
    kinds = {row["key"]: row for row in report["by_kind"]}
    assert kinds["video"]["count"] == 1
    assert kinds["video"]["bytes"] == (CORPUS / "clip.mp4").stat().st_size
    assert "image" not in kinds  # the png is in the trash
    assert report["trash"]["count"] == 1
    assert report["by_project"][0]["label"] == "Big"
    assert report["media_bytes"] >= kinds["video"]["bytes"]
    assert report["derived_bytes"] > 0
    assert report["database_bytes"] > 0
