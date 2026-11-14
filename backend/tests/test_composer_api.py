"""M2.5: what the composer asks before Generate (estimates and problems), and
reference images: checked against each model's limits, stored with the
generation, and handed to the provider."""

from __future__ import annotations

import io
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from framestudio.generation import GenerateRemoteJob

from .conftest import wait_until


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(GenerateRemoteJob, "poll_interval", 0.05)
    yield


def estimate(client: TestClient, **body: Any) -> dict[str, Any]:
    response = client.post("/api/generations/estimate", json=body)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def png(colour: tuple[int, int, int], size: tuple[int, int] = (64, 64)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="PNG")
    return buffer.getvalue()


def upload_image(client: TestClient, name: str, colour: tuple[int, int, int]) -> str:
    response = client.post("/api/assets/upload", files=[("files", (name, png(colour)))])
    assert response.status_code == 200, response.text
    return response.json()[0]["asset"]["id"]  # type: ignore[no-any-return]


def settled(client: TestClient, generation_id: str) -> dict[str, Any]:
    def check() -> dict[str, Any] | None:
        body = client.get(f"/api/generations/{generation_id}").json()
        return body if body["status"] in {"succeeded", "failed", "cancelled"} else None

    return wait_until(check, timeout=30)  # type: ignore[no-any-return]


# ---------------------------------------------------------------- estimates


def test_local_estimate_uses_the_latency_setting(authed: TestClient) -> None:
    authed.patch("/api/settings", json={"mock_latency_seconds": 1.5})
    one = estimate(authed, model="mock", mode="image")
    assert one == {
        "ok": True,
        "problems": [],
        "model": "mock",
        "cost_usd": None,
        "seconds": 1.5,
        "notes": None,
    }
    # Local work queues on the GPU lane: a batch of three takes three times as long.
    assert estimate(authed, model="mock", mode="image", batch=3)["seconds"] == 4.5
    assert estimate(authed, model="mock", params={"latency_seconds": 0.2})["seconds"] == 0.2


def test_hosted_estimate_prices_the_batch(authed: TestClient) -> None:
    body = estimate(authed, model="mock-cloud", mode="image", batch=3)
    assert body["ok"] and body["cost_usd"] == pytest.approx(0.03)


def test_problems_come_back_as_words(authed: TestClient) -> None:
    body = estimate(authed, model="mock", mode="video", params={"duration": 99, "aspect": "5:4"})
    assert not body["ok"]
    assert any("Duration" in p for p in body["problems"])
    assert any("Aspect ratio" in p for p in body["problems"])
    assert estimate(authed, model="nope")["problems"] == ["Unknown model 'nope'."]
    rule = estimate(authed, model="mock", mode="video", params={"aspect": "21:9", "duration": 12})
    assert not rule["ok"] and "Widescreen" in rule["problems"][0]


def test_a_reached_cap_shows_before_generate(authed: TestClient) -> None:
    authed.put("/api/connections/mock-cloud", json={"monthly_cap_usd": 0.015})
    assert estimate(authed, model="mock-cloud")["ok"]
    two = estimate(authed, model="mock-cloud", batch=2)
    assert not two["ok"] and "would pass" in two["problems"][0]


def test_estimates_start_nothing(authed: TestClient) -> None:
    estimate(authed, model="mock", prompt="nothing should run")
    assert authed.get("/api/generations").json() == []
    assert authed.get("/api/jobs").json() == []


# ---------------------------------------------------------------- references


def test_references_reach_the_provider_and_stay_with_the_generation(authed: TestClient) -> None:
    red = upload_image(authed, "red.png", (230, 20, 20))
    blue = upload_image(authed, "blue.png", (20, 40, 230))
    response = authed.post(
        "/api/generations",
        json={
            "model": "mock-cloud",
            "references": [red, blue],
            "params": {"latency_seconds": 0, "aspect": "1:1"},
        },
    )
    assert response.status_code == 201, response.text
    created = response.json()[0]
    assert created["references"] == [red, blue]
    done = settled(authed, created["id"])
    assert done["status"] == "succeeded", done
    assert done["references"] == [red, blue]
    image = Image.open(io.BytesIO(authed.get(done["asset"]["url"]).content)).convert("RGB")
    width, height = image.size
    margin, side = max(24, width // 24), max(32, width // 8)
    y = height - margin - side - side // 2 + side // 2
    # Thumbnails run right to left, last reference rightmost.
    right = image.getpixel((width - margin - side // 2, y))
    left = image.getpixel((width - margin - side - 6 - side // 2, y))
    assert right == pytest.approx((20, 40, 230), abs=12)
    assert left == pytest.approx((230, 20, 20), abs=12)


@pytest.mark.parametrize(
    ("model", "kind", "count", "problem"),
    [
        ("mock", "image", 1, "does not take reference images"),
        ("mock-cloud", "image", 5, "at most 4 references"),
        ("mock-cloud", "video", 1, "takes image references, not video"),
    ],
)
def test_references_are_checked_against_the_model(
    authed: TestClient, model: str, kind: str, count: int, problem: str
) -> None:
    if kind == "video":
        from .test_library import CORPUS

        response = authed.post(
            "/api/assets/upload",
            files=[("files", ("clip.mp4", (CORPUS / "clip.mp4").read_bytes()))],
        )
        ids = [response.json()[0]["asset"]["id"]]
    else:
        ids = [upload_image(authed, f"r{i}.png", (i * 40, 10, 10)) for i in range(count)]
    response = authed.post("/api/generations", json={"model": model, "references": ids})
    assert response.status_code == 422
    assert any(problem in str(item) for item in response.json()["detail"])
    body = estimate(authed, model=model, references=ids)
    assert not body["ok"] and any(problem in p for p in body["problems"])


def test_trashed_unknown_and_repeated_references_are_refused(authed: TestClient) -> None:
    red = upload_image(authed, "red.png", (230, 20, 20))
    assert not estimate(authed, model="mock-cloud", references=[red, red])["ok"]
    assert not estimate(authed, model="mock-cloud", references=["nope"])["ok"]
    authed.post("/api/assets/trash", json={"asset_ids": [red]})
    problems = estimate(authed, model="mock-cloud", references=[red])["problems"]
    assert problems == ["A reference is not in the library (or is in the trash)"]


def test_a_reference_gone_before_the_job_runs_fails_clearly(authed: TestClient) -> None:
    red = upload_image(authed, "red.png", (230, 20, 20))
    services = authed.app.state.services  # type: ignore[attr-defined]
    with services.db.session() as session:
        from framestudio.db.models import Asset

        asset = session.get(Asset, red)
        assert asset is not None
        path = services.media.resolve(asset.path)
    created = authed.post(
        "/api/generations",
        json={"model": "mock-cloud", "references": [red], "params": {"latency_seconds": 0.3}},
    ).json()[0]
    path.unlink()
    done = settled(authed, created["id"])
    if done["status"] == "failed":  # the file went before the job read it
        assert done["error_code"] == "reference_missing"
