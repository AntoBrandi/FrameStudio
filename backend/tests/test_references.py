"""M4.4: the reference resolver. The same prompt naming the same elements
becomes the right request for each kind of model: several reference images
(Nano Banana, Seedance), one input image (a fal image-to-image endpoint), or
words only (the mock engine; a clip given a start frame).

Golden files in ``tests/golden/references/`` hold, per case, what the resolver
decided and the request body the provider stand-in received, with pictures
named instead of inlined. Rewrite them after a deliberate change with
``FRAMESTUDIO_UPDATE_GOLDEN=1 uv run pytest tests/test_references.py`` and
read the diff.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
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
from framestudio.catalog.fal_import import model_id as fal_model_id
from framestudio.config import Config
from framestudio.generation import GenerateHostedJob, GenerateRemoteJob
from framestudio.media.tools import ffmpeg_path, ffprobe_path

from .conftest import AUTH, BASE_URL, TOKEN, wait_until
from .contracts.byteplus_wire import FakeBytePlus
from .contracts.fal_wire import FakeFal
from .contracts.gemini_image_wire import FakeGemini

pytestmark = pytest.mark.skipif(not (ffmpeg_path() and ffprobe_path()), reason="ffmpeg is required")

GOLDEN = Path(__file__).parent / "golden" / "references"
UPDATE = os.environ.get("FRAMESTUDIO_UPDATE_GOLDEN") == "1"
PROMPT = "@maya walks along @pier holding @lantern at dusk, @ghost watching from the dunes"
EDIT = "fal-ai/demo-edit"


class Network:
    """Google, BytePlus and fal stand-ins behind one transport, by host."""

    def __init__(self, gemini: FakeGemini, byteplus: FakeBytePlus, fal: FakeFal) -> None:
        self.gemini, self.byteplus, self.fal = gemini, byteplus, fal

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        host = request.url.host
        if host.endswith("googleapis.com"):
            return self.gemini(request)
        if host.endswith("bytepluses.com"):
            return self.byteplus(request)
        return self.fal(request)


class Library:
    """A client with elements, and the names of the pictures by content."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.names: dict[str, str] = {}
        self.ids: dict[str, str] = {}

    def picture(self, name: str, colour: tuple[int, int, int]) -> str:
        buffer = io.BytesIO()
        Image.new("RGB", (320, 240), colour).save(buffer, format="PNG")
        data = buffer.getvalue()
        response = self.client.post("/api/assets/upload", files=[("files", (f"{name}.png", data))])
        asset_id: str = response.json()[0]["asset"]["id"]
        self.names[hashlib.sha256(data).hexdigest()] = name
        self.ids[asset_id] = name
        return asset_id

    def name_of(self, value: str) -> str:
        """``<picture>`` for a data URI or base64 payload of a known picture."""
        payload = value.split(",", 1)[1] if value.startswith("data:") else value
        # Google's SDK sends URL-safe base64; the others the standard kind.
        alphabet = b"-_" if ("-" in payload or "_" in payload) else None
        try:
            data = base64.b64decode(payload, altchars=alphabet, validate=True)
        except ValueError:
            return value
        return f"<{self.names.get(hashlib.sha256(data).hexdigest(), 'unknown picture')}>"


def scrub(value: Any, library: Library) -> Any:
    """The request with pictures named and nothing that changes between runs."""
    if isinstance(value, dict):
        return {k: scrub(v, library) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v, library) for v in value]
    if isinstance(value, str) and (value.startswith("data:") or len(value) > 200):
        return library.name_of(value)
    return value


@pytest.fixture
def stand_ins() -> tuple[FakeGemini, FakeBytePlus, FakeFal]:
    return FakeGemini(), FakeBytePlus(), FakeFal()


@pytest.fixture
def library(
    tmp_path: Path,
    stand_ins: tuple[FakeGemini, FakeBytePlus, FakeFal],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Library]:
    gemini, byteplus, fal = stand_ins
    monkeypatch.setattr(GenerateRemoteJob, "poll_interval", 0.05)
    monkeypatch.setattr(GenerateHostedJob, "retry_delay", 0.0)
    config = Config(data_dir=tmp_path / "data", token=TOKEN, ui_dist=tmp_path / "ui")
    app = create_app(config, http_transport=httpx2.MockTransport(Network(gemini, byteplus, fal)))
    with TestClient(app, base_url=BASE_URL, headers=AUTH) as client:
        for provider, key in (("google", gemini.key), ("byteplus", byteplus.key), ("fal", fal.key)):
            response = client.put(f"/api/connections/{provider}", json={"api_key": key})
            assert response.json()["status"] == "ok", response.text
        imported = client.post("/api/catalog/fal/import", json={"endpoint_ids": [EDIT]})
        assert imported.status_code == 200, imported.text
        lib = Library(client)
        sides = {"front": (200, 60, 40), "side": (190, 70, 50), "back": (180, 80, 60)}
        sides["hands"] = (170, 90, 70)
        maya = [lib.picture(f"maya-{side}", colour) for side, colour in sides.items()]
        pier = lib.picture("pier", (40, 60, 200))
        lantern = lib.picture("lantern", (200, 180, 40))
        for body in (
            {"name": "Maya Ortiz", "handle": "maya", "category": "character", "asset_ids": maya,
             "description": "Woman in her thirties, short dark hair, yellow raincoat."},
            {"name": "Old Pier", "handle": "pier", "category": "location", "asset_ids": [pier],
             "description": "Weathered wooden pier, fog, sodium lamps."},
            {"name": "Brass Lantern", "handle": "lantern", "category": "prop",
             "asset_ids": [lantern]},
        ):  # fmt: skip
            assert client.post("/api/elements", json=body).status_code == 201
        yield lib


def settled(client: TestClient, generation_id: str) -> dict[str, Any]:
    def check() -> dict[str, Any] | None:
        body = client.get(f"/api/generations/{generation_id}").json()
        return body if body["status"] in {"succeeded", "failed", "cancelled"} else None

    return wait_until(check, timeout=30)  # type: ignore[no-any-return]


CASES: dict[str, dict[str, Any]] = {
    # Several reference images, after one attached by hand.
    "nano-banana-pro": {"model": "nano-banana-pro", "attach": ["sketch"]},
    # Three at most: the sketch and two primaries fit; the lantern goes in words.
    "nano-banana-limit": {"model": "nano-banana", "attach": ["sketch"]},
    "seedance-references": {"model": "seedance-2-0", "mode": "video", "params": {"aspect": "16:9"}},
    # One input image: the first element's primary.
    "fal-single-image": {"model": EDIT},
    # No pictures at all: names and descriptions.
    "mock-words": {"model": "mock"},
    # A start frame takes the place of references.
    "seedance-start-frame": {"model": "seedance-2-0", "mode": "video", "start": "sketch"},
}


def sent(
    stand_ins: tuple[FakeGemini, FakeBytePlus, FakeFal], provider: str
) -> dict[str, Any] | None:
    gemini, byteplus, fal = stand_ins
    if provider == "google":
        return gemini.requests[-1]["body"]
    if provider == "byteplus":
        return byteplus.created[-1]
    if provider == "fal":
        return fal.submitted[-1]["body"]
    return None


@pytest.mark.parametrize("case", sorted(CASES))
def test_golden_requests(
    case: str, library: Library, stand_ins: tuple[FakeGemini, FakeBytePlus, FakeFal]
) -> None:
    client = library.client
    spec = CASES[case]
    model = fal_model_id(EDIT) if spec["model"] == EDIT else spec["model"]
    sketch = library.picture("sketch", (30, 170, 90))
    body: dict[str, Any] = {
        "model": model,
        "mode": spec.get("mode", "image"),
        "prompt": PROMPT,
        "seed": 7,
        "params": spec.get("params", {}),
        "references": [sketch] if "sketch" in spec.get("attach", []) else [],
    }
    if spec.get("start") == "sketch":
        body["start_frame"] = sketch

    preview = client.post("/api/generations/preview", json=body)
    assert preview.status_code == 200, preview.text
    response = client.post("/api/generations", json=body)
    assert response.status_code == 201, response.text
    generation = settled(client, response.json()[0]["id"])
    assert generation["status"] == "succeeded", generation
    resolved = generation["resolved"]
    assert resolved == preview.json()  # the preview is what was sent

    result = {
        "model": model,
        "strategy": resolved["strategy"],
        "prompt_sent": resolved["prompt"],
        "references": [
            {
                **{k: v for k, v in r.items() if k not in {"asset_id", "thumb_url"}},
                "picture": library.ids[r["asset_id"]],
            }
            for r in resolved["references"]
        ],
        "elements": [{k: v for k, v in e.items() if k != "id"} for e in resolved["elements"]],
        "warnings": resolved["warnings"],
        "request": scrub(sent(stand_ins, generation["provider"]), library),
    }
    path = GOLDEN / f"{case}.json"
    text = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if UPDATE or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8")) == result, f"{case} differs from {path}"


def test_the_same_prompt_three_ways(library: Library) -> None:
    """M4.4 Done when: the same prompt gives the right payload to three kinds of model."""
    by_case = {p.stem: json.loads(p.read_text()) for p in GOLDEN.glob("*.json")}
    many, one, words = (
        by_case["seedance-references"],
        by_case["fal-single-image"],
        by_case["mock-words"],
    )
    assert (many["strategy"], one["strategy"], words["strategy"]) == (
        "multi_reference",
        "single_image",
        "description",
    )
    # Several pictures, numbered in the prompt the way the request orders them.
    images = [c for c in many["request"]["content"] if c["type"] == "image_url"]
    assert [c["image_url"]["url"] for c in images] == [
        "<maya-front>", "<pier>", "<lantern>", "<maya-side>", "<maya-back>",
    ]  # fmt: skip
    assert many["prompt_sent"].startswith(
        "Maya Ortiz (images 1, 4 and 5) walks along Old Pier (image 2) "
        "holding Brass Lantern (image 3)"
    )
    # One picture: Maya's primary; the others in words.
    assert one["request"]["image_url"] == "<maya-front>"
    assert one["prompt_sent"].startswith(
        "Maya Ortiz (the reference image) walks along Old Pier holding"
    )
    # Words only, with the descriptions after the prompt.
    assert words["prompt_sent"] == (
        "Maya Ortiz walks along Old Pier holding Brass Lantern at dusk, "
        "@ghost watching from the dunes"
        "\n\nMaya Ortiz: Woman in her thirties, short dark hair, yellow raincoat."
        "\nOld Pier: Weathered wooden pier, fog, sodium lamps."
    )
    assert words["references"] == [] and "@ghost is not an element here" in words["warnings"][0]


def test_used_in_and_versions_are_recorded(library: Library) -> None:
    client = library.client
    response = client.post("/api/generations", json={"model": "mock", "prompt": "@maya alone"})
    generation = settled(client, response.json()[0]["id"])
    [maya] = generation["resolved"]["elements"]
    assert (maya["handle"], maya["version"], maya["images"]) == ("maya", 1, 0)
    elements = {e["handle"]: e for e in client.get("/api/elements").json()}
    assert elements["maya"]["used_in"] == 1 and elements["pier"]["used_in"] == 0


def test_a_long_description_is_dropped_to_fit(library: Library) -> None:
    client = library.client
    [pier] = [e for e in client.get("/api/elements").json() if e["handle"] == "pier"]
    client.patch(f"/api/elements/{pier['id']}", json={"description": "fog " * 2000})
    preview = client.post(
        "/api/generations/preview", json={"model": "nano-banana-pro", "prompt": "@maya on @pier"}
    ).json()
    assert len(preview["prompt"]) <= 8000
    assert "Woman in her thirties" in preview["prompt"]
    assert any(re.search(r"description of @pier was left out", w) for w in preview["warnings"])
