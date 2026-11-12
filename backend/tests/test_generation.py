"""M0.5 pipeline: POST /generations → job → asset with metadata and lineage."""

from __future__ import annotations

import io
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from framestudio.media.tools import ffmpeg_path, ffprobe_path

from .conftest import wait_until

needs_ffmpeg = pytest.mark.skipif(
    not (ffmpeg_path() and ffprobe_path()), reason="ffmpeg and ffprobe are required"
)


def _generate(client: TestClient, **body: Any) -> list[dict[str, Any]]:
    response = client.post("/api/generations", json=body)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def _settled(client: TestClient, generation_id: str) -> dict[str, Any]:
    def check() -> dict[str, Any] | None:
        body = client.get(f"/api/generations/{generation_id}").json()
        return body if body["status"] in {"succeeded", "failed", "cancelled"} else None

    return wait_until(check, timeout=30)  # type: ignore[no-any-return]


def test_image_generation_produces_an_asset_with_lineage(authed: TestClient) -> None:
    [queued] = _generate(
        authed,
        mode="image",
        prompt="a tuna sandwich in a neon kitchen",
        seed=42,
        params={"latency_seconds": 0.05, "aspect": "16:9"},
    )
    assert queued["status"] == "queued"
    assert queued["job_id"]
    done = _settled(authed, queued["id"])
    assert done["status"] == "succeeded", done
    asset = done["asset"]
    assert asset["kind"] == "image"
    assert (asset["width"], asset["height"]) == (1024, 576)
    assert asset["source_generation_id"] == done["id"]
    assert done["seed"] == 42

    file = authed.get(asset["url"])
    assert file.status_code == 200
    assert file.headers["content-type"] == "image/png"
    with Image.open(io.BytesIO(file.content)) as image:
        assert image.size == (1024, 576)

    listed = authed.get("/api/assets").json()
    assert [a["id"] for a in listed["items"]] == [asset["id"]]
    assert asset["provenance"] == "generated"
    assert asset["generation"]["prompt"] == "a tuna sandwich in a neon kitchen"
    thumb = authed.get(asset["thumb_url"])
    assert thumb.headers["content-type"] == "image/webp"
    job = authed.get(f"/api/jobs/{done['job_id']}").json()
    assert job["state"] == "succeeded"
    assert job["result"] == {"asset_id": asset["id"]}


def test_same_seed_and_prompt_dedupes_the_file(authed: TestClient) -> None:
    body = {"prompt": "same", "seed": 7, "params": {"latency_seconds": 0}}
    first = _settled(authed, _generate(authed, **body)[0]["id"])
    second = _settled(authed, _generate(authed, **body)[0]["id"])
    assert first["asset"]["sha256"] == second["asset"]["sha256"]
    assert first["asset"]["id"] != second["asset"]["id"]  # two rows, one file


def test_batch_gets_consecutive_seeds(authed: TestClient) -> None:
    items = _generate(authed, prompt="batch", seed=100, batch=3, params={"latency_seconds": 0})
    assert [g["seed"] for g in items] == [100, 101, 102]
    assert len({g["batch_id"] for g in items}) == 1
    assert [g["batch_index"] for g in items] == [0, 1, 2]
    for item in items:
        assert _settled(authed, item["id"])["status"] == "succeeded"


def test_failure_shows_an_error_and_retry_recovers(authed: TestClient) -> None:
    authed.patch("/api/settings", json={"mock_failure_rate": 1.0, "mock_latency_seconds": 0})
    [queued] = _generate(authed, prompt="doomed")
    failed = _settled(authed, queued["id"])
    assert failed["status"] == "failed"
    assert failed["error_code"] == "mock_failure"
    assert failed["asset"] is None

    authed.patch("/api/settings", json={"mock_failure_rate": 0.0})
    retried = authed.post(f"/api/generations/{queued['id']}/retry")
    assert retried.status_code == 200
    assert retried.json()["status"] in {"queued", "running", "succeeded"}
    recovered = _settled(authed, queued["id"])
    assert recovered["status"] == "succeeded"
    assert recovered["error_code"] is None


def test_cancel_a_generation(authed: TestClient) -> None:
    [queued] = _generate(authed, prompt="slow", params={"latency_seconds": 30})
    wait_until(lambda: authed.get(f"/api/generations/{queued['id']}").json()["status"] == "running")
    cancelled = authed.post(f"/api/generations/{queued['id']}/cancel").json()
    assert cancelled["status"] == "cancelled"


def test_unknown_model_is_rejected(authed: TestClient) -> None:
    response = authed.post("/api/generations", json={"model": "nope", "prompt": "x"})
    assert response.status_code == 422


def test_feed_is_newest_first_and_filters_by_mode(authed: TestClient) -> None:
    first = _generate(authed, prompt="one", params={"latency_seconds": 0})[0]
    second = _generate(authed, prompt="two", params={"latency_seconds": 0})[0]
    feed = authed.get("/api/generations", params={"mode": "image"}).json()
    assert [g["id"] for g in feed][:2] == [second["id"], first["id"]]
    assert authed.get("/api/generations", params={"mode": "video"}).json() == []


@needs_ffmpeg
def test_video_generation_with_tone(authed: TestClient) -> None:
    [queued] = _generate(
        authed,
        mode="video",
        prompt="test pattern",
        seed=3,
        params={"latency_seconds": 0, "duration": 1, "audio": True},
    )
    done = _settled(authed, queued["id"])
    assert done["status"] == "succeeded", done
    asset = done["asset"]
    assert asset["kind"] == "video"
    assert asset["mime"] == "video/mp4"
    assert (asset["width"], asset["height"]) == (640, 360)
    assert asset["has_audio"] is True
    assert asset["duration"] == pytest.approx(1.0, abs=0.15)
    assert asset["fps"] == pytest.approx(24.0)

    partial = authed.get(asset["url"], headers={"Range": "bytes=0-99"})
    assert partial.status_code == 206
    assert len(partial.content) == 100
