"""M5.7: keyframe then shot. Keyframes come from the shot's prompt and
controls at the clip's aspect ratio; the approved one animates with the same
prompt and controls, its composition intact (the clip opens on it, and closes
on the end frame when given); a finished clip extends from its last frame."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from framestudio.catalog import load_catalog
from framestudio.cinema.continuity import SAME, extract_frame, similarity
from framestudio.cinema.keyframes import keyframe_params_for
from framestudio.media.tools import ffmpeg_path, ffprobe_path
from framestudio.providers.base import GenerationRequest
from framestudio.providers.mock import render_mp4

from .conftest import wait_until
from .test_library import services_of

needs_ffmpeg = pytest.mark.skipif(
    not (ffmpeg_path() and ffprobe_path()), reason="ffmpeg and ffprobe are required"
)
FAST = {"latency_seconds": 0}
CLIP = {"aspect": "16:9", "duration": 2, "audio": False, "latency_seconds": 0}


def card(path: Path, background: tuple[int, int, int], dark_left: bool) -> Path:
    image = Image.new("RGB", (1024, 576), background)
    draw = ImageDraw.Draw(image)
    draw.rectangle((80, 90, 420, 470), fill=(25, 25, 30) if dark_left else (240, 240, 235))
    draw.ellipse((600, 140, 920, 460), fill=(240, 220, 40) if dark_left else (20, 40, 140))
    image.save(path)
    return path


def test_similarity_tells_the_same_picture_from_another(tmp_path: Path) -> None:
    a = card(tmp_path / "a.png", (170, 40, 40), dark_left=True)
    b = card(tmp_path / "b.png", (40, 70, 160), dark_left=False)
    assert similarity(a, a) == 1.0
    # The same picture, smaller and re-encoded, still matches; another does not.
    with Image.open(a) as image:
        image.resize((512, 288)).save(tmp_path / "small.jpg", quality=80)
    assert similarity(a, tmp_path / "small.jpg") > 0.9
    assert similarity(a, b) < 0.5


@needs_ffmpeg
def test_mock_clips_start_on_the_keyframe_and_end_on_the_end_frame(tmp_path: Path) -> None:
    start = card(tmp_path / "start.png", (170, 40, 40), dark_left=True)
    end = card(tmp_path / "end.png", (40, 70, 160), dark_left=False)
    for end_frame in (None, end):
        clip = tmp_path / f"clip-{'end' if end_frame else 'open'}.mp4"
        request = GenerationRequest(
            mode="video", prompt="x", seed=1, start_frame=start, end_frame=end_frame
        )
        render_mp4(request, str(clip), (640, 360), 2.0, tone=True)
        first = extract_frame(clip, "first", tmp_path / f"{clip.stem}-first.png")
        last = extract_frame(clip, "last", tmp_path / f"{clip.stem}-last.png")
        opening = similarity(first, start)
        assert opening > 0.95
        assert similarity(first, end) < SAME
        if end_frame is None:
            # Pushed in: the same scene, closer, so less alike than the opening.
            assert opening - 0.05 > similarity(last, start) > 0.5
        else:
            assert similarity(last, end) > 0.95


def test_keyframes_take_the_clips_aspect_ratio() -> None:
    catalog = load_catalog()
    mock, cloud = catalog.get("mock"), catalog.get("mock-cloud")
    assert mock and cloud
    assert keyframe_params_for(mock, mock, {"aspect": "21:9"}, {})["aspect"] == "21:9"
    # Mock cloud has no 21:9: the nearest it offers.
    assert keyframe_params_for(cloud, mock, {"aspect": "21:9"}, {})["aspect"] == "16:9"
    assert keyframe_params_for(mock, mock, {}, {})["aspect"] == "16:9"  # the clip's default
    assert keyframe_params_for(mock, mock, {"aspect": "9:16"}, {"aspect": "1:1"})["aspect"] == "1:1"


def settled(client: TestClient, generation_id: str) -> dict[str, Any]:
    def check() -> dict[str, Any] | None:
        body = client.get(f"/api/generations/{generation_id}").json()
        return body if body["status"] in {"succeeded", "failed", "cancelled"} else None

    return wait_until(check, timeout=60)  # type: ignore[no-any-return]


def make_shot(client: TestClient, **extra: Any) -> dict[str, Any]:
    body = {
        "prompt": "A waitress wipes the counter of a neon diner #dolly-in",
        "spec": {"lighting": {"preset": "practicals"}},
        "video_model": "mock",
        "video_params": CLIP,
        "keyframe_model": "mock",
        "keyframe_params": FAST,
        "count": 2,
        **extra,
    }
    response = client.post("/api/shots", json=body)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


@needs_ffmpeg
def test_an_approved_keyframe_animates_with_its_composition_intact(authed: TestClient) -> None:
    """M5.7 Done when: an approved keyframe animates with its composition and
    identity intact."""
    project = authed.post("/api/projects", json={"name": "Diner"}).json()
    shot = make_shot(authed, project_id=project["id"])
    assert shot["status"] in {"keyframes", "choose"}
    assert shot["title"].startswith("A waitress wipes the counter")
    assert shot["video_model"] == "mock" and shot["keyframe_model"] == "mock"
    keyframes = [settled(authed, g["id"]) for g in shot["keyframes"]]
    assert len(keyframes) == 2 and all(k["status"] == "succeeded" for k in keyframes)
    for keyframe in keyframes:
        # The shot's prompt and controls, as a still at the clip's aspect ratio.
        assert keyframe["mode"] == "image" and keyframe["shot_role"] == "keyframe"
        assert keyframe["params"]["aspect"] == "16:9"
        assert keyframe["prompt_tokens"] == shot["prompt_tokens"]
        assert keyframe["shot"] == shot["spec"]
        assert keyframe["shot"]["lighting"]["preset"] == "practicals"
        assert keyframe["project_id"] == project["id"]
        assert "moves" not in [
            s["family"] for s in keyframe["resolved"]["sections"] if s["included"]
        ]
    assert authed.get(f"/api/shots/{shot['id']}").json()["status"] == "choose"

    # Approve the first, end on the second: the clip has the same prompt and controls.
    chosen, end = keyframes[0]["asset"]["id"], keyframes[1]["asset"]["id"]
    response = authed.post(
        f"/api/shots/{shot['id']}/animate", json={"keyframe_id": chosen, "end_frame_id": end}
    )
    assert response.status_code == 200, response.text
    animating = response.json()
    assert animating["status"] == "animating"
    assert (animating["keyframe_id"], animating["end_frame_id"]) == (chosen, end)
    [clip] = animating["clips"]
    assert (clip["start_frame"], clip["end_frame"]) == (chosen, end)
    assert clip["prompt_tokens"] == shot["prompt_tokens"]
    assert clip["shot"] == keyframes[0]["shot"]
    assert "moves" in [s["family"] for s in clip["resolved"]["sections"] if s["included"]]
    assert settled(authed, clip["id"])["status"] == "succeeded"

    done = authed.get(f"/api/shots/{shot['id']}").json()
    assert done["status"] == "done"
    check = done["checks"][clip["id"]]
    assert check["start"] > 0.95 and check["end"] > 0.95 and check["matches"] is True

    # The other keyframe would not have passed for the start: the check tells them apart.
    clip_asset = settled(authed, clip["id"])["asset"]
    location = authed.get(f"/api/assets/{clip_asset['id']}/location").json()["path"]
    first = extract_frame(Path(location), "first", services_of(authed).media.temp_path(".png"))
    other = authed.get(f"/api/assets/{end}/location").json()["path"]
    assert similarity(first, Path(other)) < SAME
    first.unlink()


@needs_ffmpeg
def test_a_finished_clip_extends_from_its_last_frame(authed: TestClient) -> None:
    shot = make_shot(authed, count=1)
    [keyframe] = [settled(authed, g["id"]) for g in shot["keyframes"]]
    animated = authed.post(
        f"/api/shots/{shot['id']}/animate", json={"keyframe_id": keyframe["asset"]["id"]}
    ).json()
    [clip] = animated["clips"]
    assert settled(authed, clip["id"])["status"] == "succeeded"

    response = authed.post(
        f"/api/shots/{shot['id']}/extend",
        json={
            "generation_id": clip["id"],
            "prompt_tokens": [{"type": "text", "text": "She looks up at the door"}],
        },
    )
    assert response.status_code == 201, response.text
    following = response.json()
    assert following["id"] != shot["id"] and following["continues_from"] == clip["id"]
    assert following["prompt"] == "She looks up at the door"
    assert following["spec"] == shot["spec"] and following["video_model"] == "mock"
    frame = authed.get(f"/api/assets/{following['keyframe_id']}").json()
    assert frame["kind"] == "image" and frame["provenance"] == "generated"
    [next_clip] = following["clips"]
    assert next_clip["start_frame"] == following["keyframe_id"]
    assert settled(authed, next_clip["id"])["status"] == "succeeded"
    checked = authed.get(f"/api/shots/{following['id']}").json()["checks"][next_clip["id"]]
    assert checked["start"] > 0.95 and checked["end"] is None and checked["matches"]

    # The same frame on its own, from the viewer.
    made = authed.post(
        f"/api/assets/{settled(authed, clip['id'])['asset']['id']}/frame", json={"which": "first"}
    )
    assert made.status_code == 200, made.text
    assert made.json()["kind"] == "image"
    picture = authed.post(f"/api/assets/{keyframe['asset']['id']}/frame", json={"which": "last"})
    assert picture.status_code == 422


def test_shots_refuse_what_cannot_work(authed: TestClient) -> None:
    # Mock cloud does not start clips from pictures.
    bad = authed.post(
        "/api/shots", json={"prompt": "x", "video_model": "mock-cloud", "keyframe_model": "mock"}
    )
    assert bad.status_code == 422 and "does not start a clip from an image" in bad.text
    assert authed.get("/api/shots/nope").status_code == 404
    assert authed.post("/api/shots/nope/animate", json={"keyframe_id": "x"}).status_code == 404
    gone = authed.post("/api/shots", json={"prompt": "x", "project_id": "nope"})
    assert gone.status_code == 404
    assert authed.get("/api/shots").json() == []  # nothing half-made was kept

    shot = make_shot(authed, count=1)
    [keyframe] = [settled(authed, g["id"]) for g in shot["keyframes"]]
    # A keyframe is not a clip to extend; an unknown frame is refused.
    wrong = authed.post(f"/api/shots/{shot['id']}/extend", json={"generation_id": keyframe["id"]})
    assert wrong.status_code == 422
    missing = authed.post(f"/api/shots/{shot['id']}/animate", json={"keyframe_id": "nope"})
    assert missing.status_code == 422

    # Auto picks a model that starts from pictures (offline: the mock engine).
    auto = authed.post(
        "/api/shots/estimate",
        json={"prompt": "x", "video_model": "auto", "keyframe_model": "auto", "count": 3},
    ).json()
    assert auto["ok"] and auto["video_model"] == "mock" and auto["keyframes"]["seconds"] is not None

    # Archiving hides a shot from the list.
    assert authed.patch(f"/api/shots/{shot['id']}", json={"archived": True}).status_code == 200
    assert [s["id"] for s in authed.get("/api/shots").json()] == []
    assert [s["id"] for s in authed.get("/api/shots", params={"archived": True}).json()] == [
        shot["id"]
    ]


@pytest.mark.skipif(
    not (os.environ.get("LIVE_API") and os.environ.get("FRAMESTUDIO_LIVE_BYTEPLUS_KEY")),
    reason="paid: LIVE_API=1 and FRAMESTUDIO_LIVE_BYTEPLUS_KEY",
)
def test_live_seedance_clip_opens_on_its_keyframe(authed: TestClient, tmp_path: Path) -> None:
    """Live (paid, about $0.24: one 4-second 480p clip on Seedance 2.0 Fast):
    Seedance animates a keyframe and its first frame matches it. The keyframe
    is a local picture, so only Seedance is paid for."""
    key = os.environ["FRAMESTUDIO_LIVE_BYTEPLUS_KEY"]
    assert authed.put("/api/connections/byteplus", json={"api_key": key}).status_code == 200
    picture = card(tmp_path / "keyframe.png", (150, 60, 40), dark_left=True)
    [uploaded] = authed.post(
        "/api/assets/upload", files=[("files", ("keyframe.png", picture.read_bytes()))]
    ).json()
    clip_params = {"aspect": "16:9", "duration": 4, "resolution": "480p"}
    shot_id = make_shot(authed, count=1, video_model="seedance-2-0-fast", video_params=clip_params)[
        "id"
    ]
    response = authed.post(
        f"/api/shots/{shot_id}/animate", json={"keyframe_id": uploaded["asset"]["id"]}
    )
    assert response.status_code == 200, response.text
    [clip] = response.json()["clips"]
    done = wait_until(
        lambda: (
            (g := authed.get(f"/api/generations/{clip['id']}").json())["status"]
            in {"succeeded", "failed"}
            and g
        ),
        timeout=600,
        interval=5,
    )
    assert done["status"] == "succeeded", done
    check = authed.get(f"/api/shots/{shot_id}").json()["checks"][clip["id"]]
    assert check["start"] >= SAME, check
