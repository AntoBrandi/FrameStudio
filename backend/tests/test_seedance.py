"""M2.8: Seedance clips through BytePlus ModelArk on the stand-in task API
(tests/contracts/byteplus_wire.py): text to video, image to video from start
(and end) frames, references, failures with clear messages, cancel."""

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
from framestudio.config import Config
from framestudio.generation import GenerateRemoteJob
from framestudio.media.tools import ffmpeg_path, ffprobe_path
from framestudio.net import host_allowed

from .conftest import AUTH, BASE_URL, TOKEN, wait_until
from .contracts.byteplus_wire import FakeBytePlus

pytestmark = pytest.mark.skipif(not (ffmpeg_path() and ffprobe_path()), reason="ffmpeg is required")


@pytest.fixture
def byteplus() -> FakeBytePlus:
    return FakeBytePlus()


@pytest.fixture
def client(
    tmp_path: Path, byteplus: FakeBytePlus, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setattr(GenerateRemoteJob, "poll_interval", 0.05)
    config = Config(data_dir=tmp_path / "data", token=TOKEN, ui_dist=tmp_path / "ui")
    app = create_app(config, http_transport=httpx2.MockTransport(byteplus))
    with TestClient(app, base_url=BASE_URL, headers=AUTH) as test_client:
        response = test_client.put("/api/connections/byteplus", json={"api_key": byteplus.key})
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "ok"
        yield test_client


def png(colour: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (640, 360), colour).save(buffer, format="PNG")
    return buffer.getvalue()


def upload(client: TestClient, name: str, colour: tuple[int, int, int]) -> str:
    response = client.post("/api/assets/upload", files=[("files", (name, png(colour)))])
    return response.json()[0]["asset"]["id"]  # type: ignore[no-any-return]


def start(client: TestClient, **body: Any) -> Any:
    return client.post("/api/generations", json={"model": "seedance-2-0", "mode": "video", **body})


def settled(client: TestClient, generation_id: str) -> dict[str, Any]:
    def check() -> dict[str, Any] | None:
        body = client.get(f"/api/generations/{generation_id}").json()
        return body if body["status"] in {"succeeded", "failed", "cancelled"} else None

    return wait_until(check, timeout=30)  # type: ignore[no-any-return]


def run(client: TestClient, **body: Any) -> dict[str, Any]:
    response = start(client, **body)
    assert response.status_code == 201, response.text
    return settled(client, response.json()[0]["id"])


def test_a_clip_with_sound_from_text(client: TestClient, byteplus: FakeBytePlus) -> None:
    done = run(
        client, prompt="waves on a lighthouse rock", params={"duration": 5, "resolution": "720p"}
    )
    assert done["status"] == "succeeded", done
    asset = done["asset"]
    assert asset["kind"] == "video" and asset["has_audio"]
    assert asset["duration"] == pytest.approx(5, abs=0.3)
    [sent] = byteplus.created
    assert sent["model"] == "dreamina-seedance-2-0-260128"
    assert (sent["ratio"], sent["resolution"], sent["duration"], sent["generate_audio"]) == (
        "16:9",
        "720p",
        5,
        True,
    )
    assert sent["watermark"] is False and "seed" not in sent  # 2.x takes no seed
    assert sent["content"] == [{"type": "text", "text": "waves on a lighthouse rock"}]
    job = client.get(f"/api/jobs/{done['job_id']}").json()
    assert (job["kind"], job["lane"]) == ("generate.remote", "network")
    usage = {p["provider"]: p for p in client.get("/api/usage").json()["providers"]}
    assert usage["byteplus"]["video_seconds"] == 5
    assert usage["byteplus"]["cost_usd"] == pytest.approx(0.75)


def test_a_still_animates_from_its_first_frame(client: TestClient, byteplus: FakeBytePlus) -> None:
    still = upload(client, "still.png", (200, 120, 40))
    done = run(client, prompt="gentle push in", start_frame=still, params={"audio": False})
    assert done["status"] == "succeeded", done
    assert done["start_frame"] == still and done["end_frame"] is None
    assert done["asset"]["generation"]["start_frame"] == still
    assert not done["asset"]["has_audio"]
    image = byteplus.created[-1]["content"][1]
    assert image["role"] == "first_frame"
    assert image["image_url"]["url"].startswith("data:image/png;base64,")


def test_start_and_end_frames(client: TestClient, byteplus: FakeBytePlus) -> None:
    first = upload(client, "a.png", (200, 0, 0))
    last = upload(client, "b.png", (0, 0, 200))
    done = run(client, prompt="morph", start_frame=first, end_frame=last)
    assert done["status"] == "succeeded", done
    roles = [c.get("role") for c in byteplus.created[-1]["content"]]
    assert roles == [None, "first_frame", "last_frame"]


def test_reference_images(client: TestClient, byteplus: FakeBytePlus) -> None:
    refs = [upload(client, f"r{i}.png", (i * 60, 90, 90)) for i in range(3)]
    done = run(client, prompt="the character walks", references=refs)
    assert done["status"] == "succeeded", done
    assert [c.get("role") for c in byteplus.created[-1]["content"][1:]] == ["reference_image"] * 3


@pytest.mark.parametrize(
    ("body", "problem"),
    [
        ({"end_frame": "IMG"}, "An end frame needs a start frame"),
        (
            {"start_frame": "IMG", "references": ["IMG2"]},
            "either start and end frames or reference images",
        ),
        (
            {"model": "mock-cloud", "start_frame": "IMG"},
            "does not start a clip from an image",
        ),
        ({"start_frame": "CLIP"}, "must be images"),
        ({"params": {"duration": 20}}, "Duration must be at most 15"),
        ({"params": {"resolution": "4k"}, "model": "seedance-2-0-fast"}, "Resolution"),
    ],
)
def test_frames_and_settings_are_checked(
    client: TestClient, body: dict[str, Any], problem: str
) -> None:
    ids = {"IMG": upload(client, "i.png", (1, 2, 3)), "IMG2": upload(client, "j.png", (3, 2, 1))}
    from .test_library import CORPUS

    clip = client.post(
        "/api/assets/upload", files=[("files", ("clip.mp4", (CORPUS / "clip.mp4").read_bytes()))]
    )
    ids["CLIP"] = clip.json()[0]["asset"]["id"]

    def fill(value: Any) -> Any:
        if isinstance(value, list):
            return [ids.get(v, v) for v in value]
        return ids.get(value, value) if isinstance(value, str) else value

    request = {key: fill(value) for key, value in body.items()}
    response = start(client, **request)
    assert response.status_code == 422
    assert any(problem in str(item) for item in response.json()["detail"]), response.text


def test_refusals_and_inactive_models_say_what_to_do(client: TestClient) -> None:
    blocked = run(client, prompt="something blocked")
    assert blocked["status"] == "failed"
    assert blocked["error_code"] == "provider_content_policy"
    assert "BytePlus declined" in blocked["error_message"]
    inactive = run(client, prompt="inactive account")
    assert inactive["error_code"] == "model_not_activated"
    assert "Activate it in the ModelArk console" in inactive["error_message"]


def test_cancel_a_queued_task(client: TestClient, byteplus: FakeBytePlus) -> None:
    response = start(client, prompt="a slow sunrise")
    generation = response.json()[0]
    wait_until(lambda: byteplus.created)
    task_id = next(iter(byteplus.tasks))
    client.post(f"/api/generations/{generation['id']}/cancel")
    assert settled(client, generation["id"])["status"] == "cancelled"
    assert byteplus.deleted == [task_id]


def test_estimates_follow_resolution_and_length(client: TestClient) -> None:
    def cost(model: str, **params: Any) -> float:
        response = client.post(
            "/api/generations/estimate", json={"model": model, "mode": "video", "params": params}
        )
        return response.json()["cost_usd"]  # type: ignore[no-any-return]

    assert cost("seedance-2-0", duration=5, resolution="720p") == pytest.approx(0.75)
    assert cost("seedance-2-0", duration=10, resolution="1080p") == pytest.approx(3.7)
    assert cost("seedance-2-0-fast", duration=5, resolution="480p") == pytest.approx(0.3)
    assert cost("seedance-2-5", duration=30, resolution="720p") == pytest.approx(6.93)


def test_wildcard_hosts() -> None:
    hosts = frozenset({"ark.ap-southeast.bytepluses.com", "*.bytepluses.com"})
    assert host_allowed("ark.ap-southeast.bytepluses.com", hosts)
    assert host_allowed("bucket.tos-ap-southeast-1.bytepluses.com", hosts)
    assert not host_allowed("bytepluses.com", hosts)
    assert not host_allowed("evilbytepluses.com", hosts)
    assert not host_allowed("bytepluses.com.evil.example", hosts)
