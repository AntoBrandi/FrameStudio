"""M2.10: fal.ai as a second route: models, parameters and prices read from
its platform API into catalog entries, generations through its queue, and
Auto falling back to it when BytePlus is off (stand-ins in tests/contracts)."""

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
from framestudio.catalog.fal_import import FalImportError, model_id, spec_from_fal
from framestudio.config import Config
from framestudio.connections.fake import FAL_DEMO, fal_model
from framestudio.generation import GenerateRemoteJob
from framestudio.media.tools import ffmpeg_path, ffprobe_path
from framestudio.providers.fal import find_media

from .conftest import AUTH, BASE_URL, TOKEN, wait_until
from .contracts.byteplus_wire import FakeBytePlus
from .contracts.fal_wire import FakeFal

pytestmark = pytest.mark.skipif(not (ffmpeg_path() and ffprobe_path()), reason="ffmpeg is required")

VIDEO = "fal-ai/demo-video/image-to-video"
IMAGE = "fal-ai/demo-image"
EDIT = "fal-ai/demo-edit"


class Network:
    """fal and BytePlus stand-ins behind one transport, routed by host."""

    def __init__(self, fal: FakeFal, byteplus: FakeBytePlus) -> None:
        self.fal, self.byteplus = fal, byteplus

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        host = request.url.host
        return self.byteplus(request) if host.endswith("bytepluses.com") else self.fal(request)


@pytest.fixture
def fal() -> FakeFal:
    return FakeFal()


@pytest.fixture
def byteplus() -> FakeBytePlus:
    return FakeBytePlus()


def make_client(tmp_path: Path, fal: FakeFal, byteplus: FakeBytePlus) -> TestClient:
    config = Config(data_dir=tmp_path / "data", token=TOKEN, ui_dist=tmp_path / "ui")
    app = create_app(config, http_transport=httpx2.MockTransport(Network(fal, byteplus)))
    return TestClient(app, base_url=BASE_URL, headers=AUTH)


@pytest.fixture
def client(
    tmp_path: Path, fal: FakeFal, byteplus: FakeBytePlus, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setattr(GenerateRemoteJob, "poll_interval", 0.05)
    with make_client(tmp_path, fal, byteplus) as test_client:
        response = test_client.put("/api/connections/fal", json={"api_key": fal.key})
        assert response.status_code == 200 and response.json()["status"] == "ok", response.text
        yield test_client


def png(colour: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (640, 360), colour).save(buffer, format="PNG")
    return buffer.getvalue()


def upload(client: TestClient, colour: tuple[int, int, int] = (200, 100, 40)) -> str:
    response = client.post("/api/assets/upload", files=[("files", ("still.png", png(colour)))])
    return response.json()[0]["asset"]["id"]  # type: ignore[no-any-return]


def settled(client: TestClient, generation_id: str) -> dict[str, Any]:
    def check() -> dict[str, Any] | None:
        body = client.get(f"/api/generations/{generation_id}").json()
        return body if body["status"] in {"succeeded", "failed", "cancelled"} else None

    return wait_until(check, timeout=30)  # type: ignore[no-any-return]


def run(client: TestClient, **body: Any) -> dict[str, Any]:
    response = client.post("/api/generations", json=body)
    assert response.status_code == 201, response.text
    return settled(client, response.json()[0]["id"])


def import_models(client: TestClient, *endpoints: str) -> list[dict[str, Any]]:
    response = client.post("/api/catalog/fal/import", json={"endpoint_ids": list(endpoints)})
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


# ---------------------------------------------------------------- the catalog from fal


def test_search_and_import(client: TestClient, tmp_path: Path) -> None:
    found = client.get("/api/catalog/fal/search", params={"q": "demo"}).json()
    assert {m["endpoint_id"] for m in found} == {VIDEO, IMAGE, EDIT}
    assert all(m["supported"] for m in found)
    [video, image] = import_models(client, VIDEO, IMAGE)
    assert video["id"] == "fal-demo-video-image-to-video" and video["provider"] == "fal"
    assert video["available"] and video["modes"] == ["video"]
    assert set(video["capabilities"]) == {"image_to_video", "start_end_frames", "native_audio"}
    assert video["params"]["duration"]["values"] == ["5", "10"]
    assert video["params"]["aspect"]["label"] == "Aspect ratio"
    assert video["pricing"] == {**video["pricing"], "unit": "video_second", "usd": 0.08}
    assert image["params"]["guidance_scale"]["advanced"] is True
    assert image["pricing"]["unit"] == "image"
    # Written to the user's catalog folder: still there after a restart.
    files = sorted(p.name for p in (tmp_path / "data" / "catalog").glob("*.yaml"))
    assert files == ["fal-demo-image.yaml", "fal-demo-video-image-to-video.yaml"]


def test_imported_models_survive_a_restart(
    tmp_path: Path, fal: FakeFal, byteplus: FakeBytePlus
) -> None:
    with make_client(tmp_path, fal, byteplus) as first:
        first.put("/api/connections/fal", json={"api_key": fal.key})
        import_models(first, VIDEO)
    with make_client(tmp_path, fal, byteplus) as second:
        ids = [m["id"] for m in second.get("/api/models").json()]
        assert "fal-demo-video-image-to-video" in ids


def test_an_edit_model_takes_one_picture(client: TestClient, fal: FakeFal) -> None:
    """M4.4: image-to-image endpoints with one ``image_url`` take one picture."""
    [edit] = import_models(client, EDIT)
    assert edit["limits"]["max_references"] == 1
    assert edit["capabilities"] == ["image_edit"]
    refused = client.post("/api/generations", json={"model": edit["id"], "prompt": "bluer"})
    assert refused.status_code == 422 and "changes a picture" in refused.text
    still = upload(client)
    done = run(client, model=edit["id"], prompt="bluer", references=[still])
    assert done["status"] == "succeeded", done
    [sent] = fal.submitted
    assert sent["body"]["image_url"].startswith("data:image/png;base64,")
    assert "start_image_url" not in sent["body"]


def test_import_refuses_what_it_cannot_use(client: TestClient) -> None:
    unknown = client.post("/api/catalog/fal/import", json={"endpoint_ids": ["fal-ai/nope"]})
    assert unknown.status_code == 422 and "no model fal-ai/nope" in unknown.json()["detail"]
    odd = fal_model({**FAL_DEMO[0], "category": "video-to-audio"}, openapi=True)
    with pytest.raises(FalImportError, match="cannot use video-to-audio"):
        spec_from_fal(odd, {"unit_price": 1, "unit": "request"})
    no_prompt = {**FAL_DEMO[1], "input": {"image_url": {"type": "string"}}}
    with pytest.raises(FalImportError, match="takes no prompt"):
        spec_from_fal(fal_model(no_prompt, openapi=True), {"unit_price": 1, "unit": "image"})
    with pytest.raises(FalImportError, match="no price"):
        spec_from_fal(fal_model(FAL_DEMO[1], openapi=True), None)


def test_search_needs_the_connection(tmp_path: Path, fal: FakeFal, byteplus: FakeBytePlus) -> None:
    with make_client(tmp_path, fal, byteplus) as bare:
        response = bare.get("/api/catalog/fal/search", params={"q": "demo"})
        assert response.status_code == 409 and "turned off" in response.json()["detail"]


def test_ids_and_media() -> None:
    assert model_id("fal-ai/kling-video/v2.1/pro") == "fal-kling-video-v2-1-pro"
    assert model_id("acme/thing") == "fal-acme-thing"
    output = {
        "video": {"url": "https://v3b.fal.media/a.mp4", "content_type": "video/mp4"},
        "seed": 4,
    }
    assert find_media(output) == [("https://v3b.fal.media/a.mp4", "video/mp4")]
    images = {
        "images": [{"url": "https://x.fal.media/1.png"}, {"url": "https://x.fal.media/2.png"}]
    }
    assert [u for u, _ in find_media(images)] == [
        "https://x.fal.media/1.png",
        "https://x.fal.media/2.png",
    ]


# ---------------------------------------------------------------- generating


def test_a_still_animates_through_fal(client: TestClient, fal: FakeFal) -> None:
    import_models(client, VIDEO)
    still = upload(client)
    done = run(
        client,
        model="fal-demo-video-image-to-video",
        mode="video",
        prompt="the tide comes in",
        start_frame=still,
        params={"duration": "5", "aspect": "9:16"},
    )
    assert done["status"] == "succeeded", done
    assert done["asset"]["kind"] == "video" and done["asset"]["has_audio"]
    assert done["asset"]["duration"] == pytest.approx(5, abs=0.3)
    [sent] = fal.submitted
    assert sent["endpoint"] == VIDEO
    body = sent["body"]
    assert body["prompt"] == "the tide comes in"
    assert body["image_url"].startswith("data:image/png;base64,")
    assert (body["duration"], body["aspect_ratio"], body["generate_audio"]) == ("5", "9:16", True)
    assert isinstance(body["seed"], int)
    usage = {p["provider"]: p for p in client.get("/api/usage").json()["providers"]}
    assert usage["fal"]["cost_usd"] == pytest.approx(0.4)  # 5 s at $0.08


def test_same_still_second_provider_only_a_model_change(
    client: TestClient, byteplus: FakeBytePlus
) -> None:
    """M2.10 Done when: the same request animates through BytePlus and fal."""
    client.put("/api/connections/byteplus", json={"api_key": byteplus.key})
    import_models(client, VIDEO)
    still = upload(client)
    request = {
        "mode": "video",
        "prompt": "waves",
        "start_frame": still,
        "params": {"aspect": "16:9"},
    }
    first = run(client, model="seedance-2-0", **request)
    second = run(client, model="fal-demo-video-image-to-video", **request)
    assert first["status"] == second["status"] == "succeeded"
    assert (first["provider"], second["provider"]) == ("byteplus", "fal")
    assert first["start_frame"] == second["start_frame"] == still


def test_auto_falls_back_to_fal_when_byteplus_is_off(
    client: TestClient, byteplus: FakeBytePlus
) -> None:
    import_models(client, VIDEO)
    client.put("/api/connections/byteplus", json={"api_key": byteplus.key})
    query = {"mode": "video", "start_frame": True}
    assert client.get("/api/route", params=query).json()["model"] == "seedance-2-0"
    client.put("/api/connections/byteplus", json={"enabled": False})
    routed = client.get("/api/route", params=query).json()
    assert routed["model"] == "fal-demo-video-image-to-video"


def test_failures_and_cancel(client: TestClient, fal: FakeFal) -> None:
    import_models(client, IMAGE)
    flaky = run(client, model="fal-demo-image", prompt="a flaky lighthouse")
    assert flaky["status"] == "failed" and flaky["error_code"] == "fal_runner_disconnected"
    expired = run(client, model="fal-demo-image", prompt="an expired harbour")
    assert expired["status"] == "failed" and "keeps results" in expired["error_message"]
    queued = client.post(
        "/api/generations", json={"model": "fal-demo-image", "prompt": "a slow dawn"}
    ).json()[0]
    wait_until(lambda: fal.submitted)
    client.post(f"/api/generations/{queued['id']}/cancel")
    assert settled(client, queued["id"])["status"] == "cancelled"
    assert len(fal.cancelled) == 1
    good = run(client, model="fal-demo-image", prompt="a clear morning", params={"aspect": "16:9"})
    assert good["status"] == "succeeded"
    assert good["asset"]["width"] / good["asset"]["height"] == pytest.approx(16 / 9, rel=0.02)
