"""M4.6: element generators. From one picture of an element, a recipe makes a
set of views (character sheet, location set, prop turnaround, turntable clip)
and adds them to the element as they finish.

Nano Banana and Seedance run against their stand-ins; with nothing connected
the recipes fall back to the mock engine. ``LIVE_API=1`` with
``FRAMESTUDIO_LIVE_GOOGLE_KEY`` makes a real character sheet (paid, about
three images)."""

from __future__ import annotations

import base64
import hashlib
import io
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from framestudio.app import create_app
from framestudio.config import Config
from framestudio.generation import GenerateHostedJob, GenerateRemoteJob
from framestudio.library.element_generators import RECIPES
from framestudio.media.tools import ffmpeg_path, ffprobe_path

from .conftest import AUTH, BASE_URL, TOKEN, wait_until
from .contracts.byteplus_wire import FakeBytePlus
from .contracts.gemini_image_wire import FakeGemini

pytestmark = pytest.mark.skipif(not (ffmpeg_path() and ffprobe_path()), reason="ffmpeg is required")

LIVE_KEY = (
    os.environ.get("FRAMESTUDIO_LIVE_GOOGLE_KEY") if os.environ.get("LIVE_API") == "1" else None
)


class Network:
    def __init__(self, gemini: FakeGemini, byteplus: FakeBytePlus) -> None:
        self.gemini, self.byteplus = gemini, byteplus

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.host.endswith("bytepluses.com"):
            return self.byteplus(request)
        return self.gemini(request)


@pytest.fixture
def stand_ins() -> tuple[FakeGemini, FakeBytePlus]:
    return FakeGemini(), FakeBytePlus()


@pytest.fixture
def client(
    tmp_path: Path, stand_ins: tuple[FakeGemini, FakeBytePlus], monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setattr(GenerateRemoteJob, "poll_interval", 0.05)
    monkeypatch.setattr(GenerateHostedJob, "retry_delay", 0.0)
    config = Config(data_dir=tmp_path / "data", token=TOKEN, ui_dist=tmp_path / "ui")
    app = create_app(config, http_transport=httpx2.MockTransport(Network(*stand_ins)))
    with TestClient(app, base_url=BASE_URL, headers=AUTH) as test_client:
        test_client.patch("/api/settings", json={"mock_latency_seconds": 0})
        yield test_client


def connect(client: TestClient, stand_ins: tuple[FakeGemini, FakeBytePlus]) -> None:
    gemini, byteplus = stand_ins
    for provider, key in (("google", gemini.key), ("byteplus", byteplus.key)):
        assert client.put(f"/api/connections/{provider}", json={"api_key": key}).status_code == 200


def photo() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (480, 640), (180, 90, 60)).save(buffer, format="PNG")
    return buffer.getvalue()


def make_element(client: TestClient, category: str = "character") -> tuple[dict[str, Any], str]:
    upload = client.post("/api/assets/upload", files=[("files", ("maya.png", photo()))])
    picture: str = upload.json()[0]["asset"]["id"]
    body = {
        "name": "Maya Ortiz",
        "handle": "maya",
        "category": category,
        "description": "Woman in her thirties, short dark hair, yellow raincoat.",
        "asset_ids": [picture],
    }
    response = client.post("/api/elements", json=body)
    assert response.status_code == 201, response.text
    return response.json(), picture


def finished(client: TestClient, generations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def check() -> list[dict[str, Any]] | None:
        out = [client.get(f"/api/generations/{g['id']}").json() for g in generations]
        return out if all(g["status"] in {"succeeded", "failed"} for g in out) else None

    try:
        return wait_until(check, timeout=40)  # type: ignore[no-any-return]
    except AssertionError:
        states = [client.get(f"/api/generations/{g['id']}").json() for g in generations]
        raise AssertionError([(g["status"], g["error_message"]) for g in states]) from None


def test_recipes_and_their_models(
    client: TestClient, stand_ins: tuple[FakeGemini, FakeBytePlus]
) -> None:
    offline = {r["id"]: r for r in client.get("/api/elements/recipes").json()}
    assert set(offline) == {"character_sheet", "location_set", "prop_turnaround", "turntable"}
    assert [v["id"] for v in offline["character_sheet"]["views"]] == ["front", "back", "portrait"]
    assert [v["id"] for v in offline["prop_turnaround"]["views"]] == [
        "front", "three_quarter", "side", "back",
    ]  # fmt: skip
    # Nothing connected: the router's pick for one reference picture (the
    # hosted test double, which takes references) stands in.
    assert offline["character_sheet"]["model"] == "mock-cloud"
    connect(client, stand_ins)
    online = {r["id"]: r for r in client.get("/api/elements/recipes").json()}
    assert online["character_sheet"]["model"] == "nano-banana-pro"
    assert online["turntable"]["model"] == "seedance-2-0"


def test_a_character_sheet_from_one_photo(
    client: TestClient, stand_ins: tuple[FakeGemini, FakeBytePlus]
) -> None:
    """M4.6 Done when: from one photo, a character sheet is stored on the element."""
    connect(client, stand_ins)
    gemini = stand_ins[0]
    maya, picture = make_element(client)
    estimate = client.post(
        f"/api/elements/{maya['id']}/generate/estimate", json={"recipe": "character_sheet"}
    ).json()
    assert estimate["ok"] and estimate["views"] == 3 and estimate["model"] == "nano-banana-pro"
    nano = next(m for m in client.get("/api/models").json() if m["id"] == "nano-banana-pro")
    assert estimate["cost_usd"] == pytest.approx(3 * nano["pricing"]["usd"], rel=0.01)

    response = client.post(
        f"/api/elements/{maya['id']}/generate", json={"recipe": "character_sheet"}
    )
    assert response.status_code == 201, response.text
    queued = response.json()
    assert len(queued) == 3 and all(g["attach_element_id"] == maya["id"] for g in queued)
    done = finished(client, queued)
    assert [g["status"] for g in done] == ["succeeded"] * 3

    element = client.get(f"/api/elements/{maya['id']}").json()
    assert [(m["role"], m["asset_id"] == picture) for m in element["media"]] == [
        ("primary", True), ("sheet", False), ("sheet", False), ("sheet", False),
    ]  # fmt: skip
    assert element["version"] == 4  # one per picture added
    assert element["used_in"] == 3
    # Each view sent the photo and named it in the prompt.
    digest = hashlib.sha256(photo()).hexdigest()
    prompts = []
    for request in gemini.requests[-3:]:
        parts = request["body"]["contents"][0]["parts"]
        images = [p["inlineData"]["data"] for p in parts if "inlineData" in p]
        sent = [hashlib.sha256(base64.urlsafe_b64decode(i)).hexdigest() for i in images]
        assert sent == [digest]
        prompts.append(next(p["text"] for p in parts if "text" in p))
    assert sorted(p.split(" of ")[0] for p in prompts) == [
        "Full-body back view", "Full-body front view", "Head-and-shoulders portrait",
    ]  # fmt: skip
    assert all(" of Maya Ortiz" in p and "as the reference." in p for p in prompts)
    # Every view starts from the photo alone, even once the first ones are in.
    assert all([r["asset_id"] for r in g["resolved"]["references"]] == [picture] for g in done)
    assert all(
        p.endswith("Maya Ortiz: Woman in her thirties, short dark hair, yellow raincoat.")
        for p in prompts
    )


def test_some_views_and_a_turntable_clip(
    client: TestClient, stand_ins: tuple[FakeGemini, FakeBytePlus]
) -> None:
    connect(client, stand_ins)
    lamp, _ = make_element(client, "prop")
    some = client.post(
        f"/api/elements/{lamp['id']}/generate",
        json={"recipe": "prop_turnaround", "views": ["side", "back"], "seed": 11},
    ).json()
    assert [g["seed"] for g in some] == [11, 12]
    clip = client.post(f"/api/elements/{lamp['id']}/generate", json={"recipe": "turntable"}).json()
    assert clip[0]["model_id"] == "seedance-2-0" and clip[0]["mode"] == "video"
    finished(client, some + clip)
    roles = [m["role"] for m in client.get(f"/api/elements/{lamp['id']}").json()["media"]]
    assert roles.count("sheet") == 2 and roles.count("turntable") == 1
    created = stand_ins[1].created[-1]
    assert any(c.get("role") == "reference_image" for c in created["content"])


def test_offline_a_test_double_makes_the_views(client: TestClient) -> None:
    """With nothing connected the recipe still runs, on the router's pick."""
    maya, _ = make_element(client)
    queued = client.post(
        f"/api/elements/{maya['id']}/generate", json={"recipe": "character_sheet"}
    ).json()
    done = finished(client, queued)
    assert {g["model_id"] for g in done} == {"mock-cloud"}
    assert all(g["resolved"]["strategy"] == "multi_reference" for g in done)
    # A model that takes no pictures can be asked for: the element goes in words.
    words = client.post(
        f"/api/elements/{maya['id']}/generate",
        json={"recipe": "character_sheet", "views": ["front"], "model": "mock"},
    ).json()
    [word] = finished(client, words)
    assert word["resolved"]["strategy"] == "description"
    media = client.get(f"/api/elements/{maya['id']}").json()["media"]
    assert len(media) == 5


def test_refusals(client: TestClient) -> None:
    maya, _ = make_element(client)
    assert (
        client.post("/api/elements/nope/generate", json={"recipe": "turntable"}).status_code == 404
    )
    wrong = client.post(
        f"/api/elements/{maya['id']}/generate",
        json={"recipe": "turntable", "model": "nano-banana-pro"},
    )
    assert wrong.status_code == 422 and "cannot make" in wrong.text
    none = client.post(
        f"/api/elements/{maya['id']}/generate",
        json={"recipe": "character_sheet", "views": ["nope"]},
    )
    assert none.status_code == 422
    # A generation for an element that is gone is refused.
    response = client.post(
        "/api/generations", json={"model": "mock", "prompt": "x", "attach_element_id": "nope"}
    )
    assert response.status_code == 404


def test_deleting_the_element_keeps_the_results(client: TestClient) -> None:
    client.patch("/api/settings", json={"mock_latency_seconds": 0.3})
    maya, _ = make_element(client)
    queued = client.post(
        f"/api/elements/{maya['id']}/generate",
        json={"recipe": "character_sheet", "views": ["front"]},
    ).json()
    client.delete(f"/api/elements/{maya['id']}")
    [done] = finished(client, queued)
    assert done["status"] == "succeeded" and done["asset"] is not None
    assert done["attach_element_id"] is None


@pytest.mark.skipif(not LIVE_KEY, reason="LIVE_API=1 and FRAMESTUDIO_LIVE_GOOGLE_KEY (paid)")
def test_live_character_sheet(tmp_path: Path) -> None:
    config = Config(data_dir=tmp_path / "data", token=TOKEN, ui_dist=tmp_path / "ui")
    with TestClient(create_app(config), base_url=BASE_URL, headers=AUTH) as live:
        assert (
            live.put("/api/connections/google", json={"api_key": LIVE_KEY}).json()["status"] == "ok"
        )
        maya, _ = make_element(live)
        queued = live.post(
            f"/api/elements/{maya['id']}/generate",
            json={"recipe": "character_sheet", "model": "nano-banana"},
        ).json()
        done = wait_until(
            lambda: (
                [
                    g
                    for g in (live.get(f"/api/generations/{q['id']}").json() for q in queued)
                    if g["status"] in {"succeeded", "failed"}
                ]
                if all(
                    live.get(f"/api/generations/{q['id']}").json()["status"]
                    in {"succeeded", "failed"}
                    for q in queued
                )
                else None
            ),
            timeout=300,
            interval=2,
        )
        assert [g["status"] for g in done] == ["succeeded"] * 3, done
        roles = [m["role"] for m in live.get(f"/api/elements/{maya['id']}").json()["media"]]
        assert roles == ["primary", "sheet", "sheet", "sheet"]


def test_recipes_are_well_formed() -> None:
    for recipe in RECIPES.values():
        assert recipe.views and len({v.id for v in recipe.views}) == len(recipe.views)
        for view in recipe.views:
            # Nothing glued to the handle on either side.
            assert view.before.endswith(" ")
            assert not re.match(r"[A-Za-z0-9_-]", view.after)
