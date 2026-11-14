"""M5.4: colour palettes. A palette is extracted from a picture (seeded
k-means in CIELAB, dark to light), worded for prompts, saved as a preset, and
applied to a picture or clip as a 3D LUT grade with a strength."""

from __future__ import annotations

import io
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from framestudio.cinema import vocab
from framestudio.cinema.colour import chroma, distance, hex_to_lab, hue_degrees, to_lab
from framestudio.cinema.palette import describe, extract, extract_palette, grade, lut_cube
from framestudio.media.tools import ffmpeg_path, ffprobe_path

from .conftest import wait_until

pytestmark = pytest.mark.skipif(not (ffmpeg_path() and ffprobe_path()), reason="ffmpeg is required")

KNOWN = ["#0B1D26", "#1F6F78", "#F2A65A", "#E8E6DC"]
GOLDEN = vocab().palette("golden-hour").swatches  # type: ignore[union-attr]


def blocks(path: Path, colours: list[str]) -> Path:
    """A picture of large flat blocks, one per colour."""
    image = Image.new("RGB", (400, 400))
    draw = ImageDraw.Draw(image)
    for index, colour in enumerate(colours):
        x, y = index % 2, index // 2
        draw.rectangle([x * 200, y * 200, x * 200 + 199, y * 200 + 199], fill=colour)
    image.save(path)
    return path


def mean_lab(path: Path) -> tuple[float, float, float]:
    with Image.open(path) as image:
        pixels = [to_lab(p) for p in image.convert("RGB").resize((32, 32)).get_flattened_data()]  # type: ignore[arg-type]
    n = len(pixels)
    return (
        sum(p[0] for p in pixels) / n,
        sum(p[1] for p in pixels) / n,
        sum(p[2] for p in pixels) / n,
    )


def first_frame(clip: Path, out: Path) -> Path:
    subprocess.run(
        [ffmpeg_path(), "-v", "error", "-y", "-i", str(clip), "-frames:v", "1", str(out)],
        check=True,
    )  # type: ignore[list-item]
    return out


def test_extraction_is_deterministic_and_finds_the_colours(tmp_path: Path) -> None:
    picture = blocks(tmp_path / "blocks.png", KNOWN)
    swatches = extract(picture, count=4)
    assert extract(picture, count=4) == swatches
    for known in KNOWN:
        assert min(distance(hex_to_lab(known), hex_to_lab(s)) for s in swatches) < 4, known
    lightness = [hex_to_lab(s)[0] for s in swatches]
    assert lightness == sorted(lightness)
    # Asked for more colours than there are, it gives about as many as there are.
    assert len(extract(picture, count=8)) <= 8
    _, shares = extract_palette(picture, count=4)
    assert sum(shares) == pytest.approx(1.0) and all(
        s == pytest.approx(0.25, abs=0.05) for s in shares
    )


def test_words_follow_the_picture(tmp_path: Path) -> None:
    assert describe(GOLDEN).startswith("rich, warm tones")
    teal = vocab().palette("teal-night").swatches  # type: ignore[union-attr]
    words = describe(teal, [0.3, 0.3, 0.2, 0.1, 0.05, 0.05])
    assert "cool" in words and "deep teal" in words
    assert describe(["#111111", "#777777", "#EEEEEE"]).startswith("neutral tones: black, grey")


def test_the_grade_shifts_hue_toward_the_palette(tmp_path: Path) -> None:
    """LUT test: the mean hue moves toward the palette's, more with strength."""
    source = tmp_path / "steel.png"
    Image.new("RGB", (64, 64), (106, 122, 138)).save(source)  # a cool steel grey
    palette_hue = hue_degrees(
        (
            0,
            sum(hex_to_lab(s)[1] for s in GOLDEN) / len(GOLDEN),
            sum(hex_to_lab(s)[2] for s in GOLDEN) / len(GOLDEN),
        )
    )

    def hue_gap(strength: float) -> float:
        out = tmp_path / f"graded-{strength}.png"
        grade(source, out, GOLDEN, strength, video=False)
        hue = hue_degrees(mean_lab(out))
        return abs((hue - palette_hue + 180) % 360 - 180)

    before = hue_gap(0.0)
    halfway, full = hue_gap(0.5), hue_gap(1.0)
    assert before > 90  # a cool picture against a warm palette
    assert full < halfway < before and full < 30
    # Strength 0 changes nothing.
    assert distance(mean_lab(tmp_path / "graded-0.0.png"), mean_lab(source)) < 1.5
    # The LUT is a valid .cube of the right size.
    cube = lut_cube(GOLDEN, 0.5, size=5)
    assert cube.splitlines()[1] == "LUT_3D_SIZE 5" and len(cube.splitlines()) == 2 + 125


def png(colours: list[str]) -> bytes:
    buffer = io.BytesIO()
    image = Image.new("RGB", (320, 240))
    draw = ImageDraw.Draw(image)
    for index, colour in enumerate(colours):
        draw.rectangle([index * 80, 0, index * 80 + 79, 239], fill=colour)
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def upload(client: TestClient, name: str, data: bytes) -> dict[str, Any]:
    response = client.post("/api/assets/upload", files=[("files", (name, data))])
    assert response.status_code == 200, response.text
    return response.json()[0]["asset"]  # type: ignore[no-any-return]


def finished(client: TestClient, job_id: str) -> dict[str, Any]:
    def check() -> dict[str, Any] | None:
        job = next((j for j in client.get("/api/jobs").json() if j["id"] == job_id), None)
        return job if job and job["state"] in {"succeeded", "failed", "cancelled"} else None

    return wait_until(check, timeout=60)  # type: ignore[no-any-return]


def test_a_palette_from_a_still_applied_to_a_clip(authed: TestClient, tmp_path: Path) -> None:
    """M5.4 Done when: a palette extracted from a still can be applied to a clip."""
    sunset = ["#2A0F1F", "#7A2E3A", "#D9653B", "#F2B880"]
    still = upload(authed, "sunset.png", png(sunset))
    extracted = authed.post(
        "/api/cinema/palettes/extract", json={"asset_id": still["id"], "count": 4}
    ).json()
    assert len(extracted["swatches"]) == 4 and "warm" in extracted["words"]
    saved = authed.post(
        "/api/cinema/presets",
        json={
            "kind": "palette",
            "name": "Sunset",
            "data": {
                "label": "Sunset",
                "swatches": extracted["swatches"],
                "words": extracted["words"],
            },
            "preview_asset_id": still["id"],
        },
    )
    assert saved.status_code == 201, saved.text
    preset = saved.json()
    assert preset["preview_url"] == f"/api/assets/{still['id']}/thumb"
    assert [
        p["name"] for p in authed.get("/api/cinema/presets", params={"kind": "palette"}).json()
    ] == ["Sunset"]

    # A cool clip, graded toward the sunset.
    clip = tmp_path / "cool.mp4"
    subprocess.run(
        [ffmpeg_path(), "-v", "error", "-y", "-f", "lavfi", "-i",
         "color=c=0x56708A:size=320x240:rate=12", "-t", "1", "-pix_fmt", "yuv420p",
         str(clip)],  # type: ignore[list-item]
        check=True,
    )  # fmt: skip
    source = upload(authed, "cool.mp4", clip.read_bytes())
    queued = authed.post(
        f"/api/cinema/grade/{source['id']}", json={"palette": preset["data"], "strength": 0.9}
    )
    assert queued.status_code == 202, queued.text
    job = finished(authed, queued.json()["job_id"])
    assert job["state"] == "succeeded", job
    graded = authed.get(f"/api/assets/{job['result']['asset_id']}").json()
    assert graded["kind"] == "video" and graded["original_name"] == "cool · graded Sunset.mp4"
    assert graded["duration"] == pytest.approx(1.0, abs=0.2)
    before = mean_lab(first_frame(clip, tmp_path / "before.png"))
    data = authed.get(f"/api/assets/{graded['id']}/file").content
    (tmp_path / "graded.mp4").write_bytes(data)
    after = mean_lab(first_frame(tmp_path / "graded.mp4", tmp_path / "after.png"))
    warm = hue_degrees(hex_to_lab("#D9653B"))
    gap = lambda lab: abs((hue_degrees(lab) - warm + 180) % 360 - 180)  # noqa: E731
    assert gap(after) < gap(before) - 60 and chroma(after) > chroma(before)


def test_grading_a_still_with_a_preset_palette(authed: TestClient) -> None:
    still = upload(authed, "grey.png", png(["#808080", "#909090", "#A0A0A0", "#B0B0B0"]))
    queued = authed.post(
        f"/api/cinema/grade/{still['id']}", json={"palette": {"id": "teal-night"}, "strength": 0.5}
    )
    job = finished(authed, queued.json()["job_id"])
    assert job["state"] == "succeeded" and job["lane"] == "cpu"
    graded = authed.get(f"/api/assets/{job['result']['asset_id']}").json()
    assert graded["kind"] == "image" and graded["original_name"] == "grey · graded Teal night.png"


def test_refusals(authed: TestClient) -> None:
    assert authed.post("/api/cinema/palettes/extract", json={"asset_id": "nope"}).status_code == 404
    assert (
        authed.post("/api/cinema/grade/nope", json={"palette": {"id": "teal-night"}}).status_code
        == 404
    )
    bad = authed.post(
        "/api/cinema/presets", json={"kind": "palette", "name": "x", "data": {"swatches": ["red"]}}
    )
    assert bad.status_code == 422
    assert authed.delete("/api/cinema/presets/nope").status_code == 404
    unknown = authed.post("/api/cinema/grade/nope", json={"palette": {"id": "plaid"}})
    assert unknown.status_code == 422
