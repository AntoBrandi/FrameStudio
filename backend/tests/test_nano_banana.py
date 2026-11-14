"""M2.7: Nano Banana stills and edits with references through the Google
connection, on the stand-in Gemini API (tests/contracts/gemini_image_wire.py)."""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from framestudio.app import create_app
from framestudio.catalog import estimate_cost, load_catalog
from framestudio.config import Config
from framestudio.generation import GenerateHostedJob
from framestudio.providers.gemini_image import pick_model

from .conftest import AUTH, BASE_URL, TOKEN, wait_until
from .contracts.gemini_image_wire import FakeGemini


class Network:
    """Key checks and the Gemini API, both answered by the stand-in."""

    def __init__(self, gemini: FakeGemini) -> None:
        self.gemini = gemini

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        return self.gemini(request)


@pytest.fixture
def gemini() -> FakeGemini:
    return FakeGemini()


@pytest.fixture
def client(
    tmp_path: Path, gemini: FakeGemini, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setattr(GenerateHostedJob, "retry_delay", 0.0)
    config = Config(data_dir=tmp_path / "data", token=TOKEN, ui_dist=tmp_path / "ui")
    app = create_app(config, http_transport=httpx2.MockTransport(Network(gemini)))
    with TestClient(app, base_url=BASE_URL, headers=AUTH) as test_client:
        response = test_client.put("/api/connections/google", json={"api_key": gemini.key})
        assert response.status_code == 200, response.text
        yield test_client


def png(colour: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (48, 48), colour).save(buffer, format="PNG")
    return buffer.getvalue()


def upload(client: TestClient, name: str, colour: tuple[int, int, int]) -> str:
    response = client.post("/api/assets/upload", files=[("files", (name, png(colour)))])
    return response.json()[0]["asset"]["id"]  # type: ignore[no-any-return]


def settled(client: TestClient, generation_id: str) -> dict[str, Any]:
    def check() -> dict[str, Any] | None:
        body = client.get(f"/api/generations/{generation_id}").json()
        return body if body["status"] in {"succeeded", "failed", "cancelled"} else None

    return wait_until(check, timeout=20)  # type: ignore[no-any-return]


def generate(client: TestClient, **body: Any) -> dict[str, Any]:
    response = client.post("/api/generations", json={"model": "nano-banana-pro", **body})
    assert response.status_code == 201, response.text
    return settled(client, response.json()[0]["id"])


def test_the_models_are_available_once_google_is_connected(client: TestClient) -> None:
    models = {m["id"]: m for m in client.get("/api/models").json()}
    assert models["nano-banana-pro"]["available"] and models["nano-banana"]["available"]
    client.put("/api/connections/google", json={"enabled": False})
    models = {m["id"]: m for m in client.get("/api/models").json()}
    assert not models["nano-banana-pro"]["available"]
    assert "Connect google" in models["nano-banana-pro"]["unavailable_reason"]


def test_a_still_lands_in_the_library(client: TestClient, gemini: FakeGemini) -> None:
    done = generate(
        client, prompt="a lighthouse at dusk", params={"aspect": "16:9", "resolution": "2K"}
    )
    assert done["status"] == "succeeded", done
    asset = done["asset"]
    assert asset["width"] / asset["height"] == pytest.approx(16 / 9, rel=0.02)
    [sent] = gemini.requests
    assert sent["model"] == "gemini-3-pro-image-preview"  # the family, resolved from the API
    config = sent["body"]["generationConfig"]
    assert config["responseModalities"] == ["IMAGE"]
    image = config["imageConfig"]
    assert (image.get("aspectRatio") or image.get("aspect_ratio")) == "16:9"
    assert (image.get("imageSize") or image.get("image_size")) == "2K"
    job = client.get(f"/api/jobs/{done['job_id']}").json()
    assert (job["kind"], job["lane"]) == ("generate.hosted", "network")
    usage = {p["provider"]: p for p in client.get("/api/usage").json()["providers"]}
    assert usage["google"]["images"] == 1 and usage["google"]["cost_usd"] == pytest.approx(0.134)


def test_an_edit_with_two_references_keeps_its_lineage(
    client: TestClient, gemini: FakeGemini
) -> None:
    still = generate(client, prompt="a lighthouse at dusk")
    texture = upload(client, "texture.png", (200, 40, 40))
    edit = generate(
        client,
        prompt="make it stormy, keep the lighthouse, use the texture for the sky",
        references=[still["asset"]["id"], texture],
        parent_asset_id=still["asset"]["id"],
    )
    assert edit["status"] == "succeeded", edit
    assert edit["references"] == [still["asset"]["id"], texture]
    assert edit["parent_asset_id"] == still["asset"]["id"]
    parts = gemini.requests[-1]["body"]["contents"][0]["parts"]
    assert sum(1 for p in parts if "inlineData" in p or "inline_data" in p) == 2
    assert parts[-1]["text"].startswith("make it stormy")


def test_a_refusal_is_reported_and_costs_nothing(client: TestClient) -> None:
    done = generate(client, prompt="something blocked")
    assert done["status"] == "failed"
    assert done["error_code"] == "provider_content_policy"
    assert "declined" in done["error_message"]
    assert client.get("/api/usage").json()["providers"] == []


def test_busy_answers_are_retried_without_paying_twice(
    client: TestClient, gemini: FakeGemini
) -> None:
    gemini.fail_next = [503, 429]
    done = generate(client, prompt="a red door")
    assert done["status"] == "succeeded"
    assert len(gemini.requests) == 3
    usage = client.get("/api/usage").json()["providers"]
    assert usage[0]["requests"] == 1


def test_a_missing_model_family_is_explained(client: TestClient, gemini: FakeGemini) -> None:
    gemini.models = ["gemini-3-pro"]
    done = generate(client, prompt="x")
    assert done["status"] == "failed"
    assert "offers no gemini-3-pro-image model" in done["error_message"]


def test_model_ids_are_picked_from_the_api() -> None:
    available = ["gemini-3-pro-image-preview", "gemini-3-pro-image-0925", "gemini-2.5-flash-image"]
    assert pick_model("gemini-3-pro-image", available) == "gemini-3-pro-image-0925"
    assert pick_model("gemini-2.5-flash-image", available) == "gemini-2.5-flash-image"
    assert (
        pick_model("gemini-3-pro-image", ["gemini-3-pro-image-preview"])
        == "gemini-3-pro-image-preview"
    )
    assert pick_model("imagen-9", available) is None


@pytest.mark.parametrize(
    ("model", "params", "batch", "cost"),
    [
        ("nano-banana-pro", {"resolution": "1K"}, 1, 0.134),
        ("nano-banana-pro", {"resolution": "4K"}, 2, 0.48),
        ("nano-banana", {}, 3, 0.117),
    ],
)
def test_cost_estimates(model: str, params: dict[str, Any], batch: int, cost: float) -> None:
    spec = load_catalog().get(model)
    assert spec is not None
    assert estimate_cost(spec, "image", params, batch) == pytest.approx(cost)
