"""M5.1: the adherence harness. For each cinema control an A/B pair is made
with the same subject and seed, scored by a picture metric or a vision-model
judge, and the result (followed, weak, ignored, untested) is saved per model.
Controls a model ignores are flagged in the compiled preview.

Offline, the mock engine (which paints a seed-coloured background whatever
the prompt says) measures as ignoring every metric control. With
``LIVE_API=1`` and ``FRAMESTUDIO_LIVE_GOOGLE_KEY``, palette and lighting are
measured on Nano Banana for real (four images, within a $0.40 budget)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from framestudio.cinema import ShotSpec, vocab
from framestudio.cinema.adherence import (
    CASES,
    BudgetError,
    Case,
    RunRequest,
    load_table,
    run_adherence,
    score_metric,
    status_for,
)
from framestudio.media.tools import ffmpeg_path

from .conftest import wait_until

LIVE_KEY = (
    os.environ.get("FRAMESTUDIO_LIVE_GOOGLE_KEY") if os.environ.get("LIVE_API") == "1" else None
)


def solid(path: Path, colour: str | tuple[int, int, int]) -> Path:
    if isinstance(colour, str):
        colour = tuple(int(colour.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[assignment]
    Image.new("RGB", (64, 64), colour).save(path)  # type: ignore[arg-type]
    return path


def clip(path: Path, moving: bool) -> Path:
    """A one-second clip: a test pattern that scrolls, or one that stands still."""
    ffmpeg = ffmpeg_path()
    assert ffmpeg
    source = "testsrc2=size=160x120:rate=10" if moving else "color=c=0x406080:size=160x120:rate=10"
    subprocess.run(
        [ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", source, "-t", "1", str(path)], check=True
    )
    return path


# ---------------------------------------------------------------- metrics


def test_palette_metric(tmp_path: Path) -> None:
    case = CASES["palette"]
    teal = vocab().palette("teal-night").swatches  # type: ignore[union-attr]
    gold = vocab().palette("golden-hour").swatches  # type: ignore[union-attr]
    following = score_metric(
        case, solid(tmp_path / "a.png", teal[2]), solid(tmp_path / "b.png", gold[3])
    )
    assert status_for(case, following) == "followed"
    grey = solid(tmp_path / "g.png", (120, 120, 120))
    assert status_for(case, score_metric(case, grey, grey)) == "ignored"


def test_brightness_and_colourfulness(tmp_path: Path) -> None:
    dark, light = (
        solid(tmp_path / "d.png", (20, 20, 25)),
        solid(tmp_path / "l.png", (200, 190, 180)),
    )
    assert status_for(CASES["lighting"], score_metric(CASES["lighting"], dark, light)) == "followed"
    assert status_for(CASES["lighting"], score_metric(CASES["lighting"], light, dark)) == "ignored"
    grey, red = solid(tmp_path / "g.png", (128, 128, 128)), solid(tmp_path / "r.png", (210, 40, 40))
    assert status_for(CASES["era"], score_metric(CASES["era"], grey, red)) == "followed"


@pytest.mark.skipif(not ffmpeg_path(), reason="ffmpeg is required")
def test_motion_metric(tmp_path: Path) -> None:
    moving, still = clip(tmp_path / "m.mp4", True), clip(tmp_path / "s.mp4", False)
    assert status_for(CASES["moves"], score_metric(CASES["moves"], moving, still)) == "followed"
    assert status_for(CASES["moves"], score_metric(CASES["moves"], still, still)) == "ignored"


# ---------------------------------------------------------------- runs with stand-ins


def maker_for(tmp_path: Path, follows: set[str]) -> Any:
    """Makes pictures that follow the controls in ``follows`` and ignore the rest."""

    async def make(case: Case, shot: ShotSpec) -> Path:
        side = "a" if shot == case.a else "b"
        path = tmp_path / f"{case.control}-{side}.png"
        if case.control not in follows:
            return solid(path, (120, 120, 120))
        if case.control == "palette":
            return solid(path, "#1F6F78" if side == "a" else "#F5C46B")
        if case.control == "lighting":
            return solid(path, (15, 15, 20) if side == "a" else (210, 205, 200))
        if case.control == "era":
            return solid(path, (128, 128, 128) if side == "a" else (220, 50, 40))
        return solid(path, (200, 0, 0) if side == "a" else (0, 0, 200))

    return make


async def judge_by_colour(question: str, first: Path, second: Path) -> str:
    """A judge that recognises the A pictures (red in the stand-in maker)."""
    with Image.open(first) as image:
        return "1" if image.getpixel((0, 0))[0] > 150 else "2"  # type: ignore[index]


@pytest.mark.anyio
async def test_a_run_classifies_and_saves(authed: TestClient, tmp_path: Path) -> None:
    services = authed.app.state.services  # type: ignore[attr-defined]
    body = RunRequest(model="mock", controls=["palette", "lighting", "era", "lens", "genre"])
    found = await run_adherence(
        services,
        body,
        make=maker_for(tmp_path, {"palette", "lighting", "lens"}),
        judge=judge_by_colour,
    )
    assert {c: m.status for c, m in found.items()} == {
        "palette": "followed",
        "lighting": "followed",
        "era": "ignored",
        "lens": "followed",
        "genre": "ignored",
    }
    assert found["lens"].method == "judge" and found["palette"].method == "metric"
    # Saved: the table and the compiler both see it.
    assert load_table(services)["mock"]["era"].status == "ignored"
    preview = authed.post(
        "/api/generations/preview",
        json={"model": "mock", "prompt": "a street", "shot": {"film": {"era": "1970s"}}},
    ).json()
    [film] = [s for s in preview["sections"] if s["family"] == "film"]
    assert film["note"] == "Mock engine tends to ignore this (era)."
    rows = {r["model"]: r for r in authed.get("/api/cinema/adherence").json()}
    assert rows["mock"]["controls"]["palette"]["status"] == "followed"
    assert rows["nano-banana-pro"]["controls"]["palette"]["status"] == "untested"
    assert "moves" not in rows["nano-banana-pro"]["controls"]  # video only


@pytest.mark.anyio
async def test_without_a_vision_model_judged_controls_stay_untested(
    authed: TestClient, tmp_path: Path
) -> None:
    services = authed.app.state.services  # type: ignore[attr-defined]
    found = await run_adherence(
        services, RunRequest(model="mock", controls=["aperture"]), make=maker_for(tmp_path, set())
    )
    assert found["aperture"].status == "untested"
    assert "vision language model" in (found["aperture"].note or "")


@pytest.mark.anyio
async def test_the_budget_is_checked_first(authed: TestClient) -> None:
    services = authed.app.state.services  # type: ignore[attr-defined]
    with pytest.raises(BudgetError, match=r"over the \$0\.00 budget"):
        await run_adherence(services, RunRequest(model="mock-cloud", budget_usd=0))
    estimate = authed.post(
        "/api/cinema/adherence/estimate", json={"model": "mock-cloud", "controls": ["palette"]}
    ).json()
    assert estimate["generations"] == 2 and estimate["within_budget"] is True
    refused = authed.post(
        "/api/cinema/adherence/runs", json={"model": "mock-cloud", "budget_usd": 0}
    )
    assert refused.status_code == 402
    unknown = authed.post(
        "/api/cinema/adherence/estimate", json={"model": "mock", "controls": ["x"]}
    )
    assert unknown.status_code == 422


@pytest.mark.skipif(not ffmpeg_path(), reason="ffmpeg is required")
def test_the_mock_engine_measured_offline(authed: TestClient) -> None:
    """A real run through the generation pipeline: the mock engine paints the
    same seed-coloured background whatever the shot says, so it ignores every
    control a metric measures; judged controls need a vision model."""
    authed.patch("/api/settings", json={"mock_latency_seconds": 0})
    started = authed.post(
        "/api/cinema/adherence/runs",
        json={"model": "mock", "controls": ["palette", "lighting", "era", "lens"]},
    )
    assert started.status_code == 202, started.text
    run_id = started.json()["id"]

    def done() -> dict[str, Any] | None:
        run = authed.get(f"/api/cinema/adherence/runs/{run_id}").json()
        return run if run["state"] != "running" else None

    run = wait_until(done, timeout=60)
    assert run["state"] == "done", run
    statuses = {c: m["status"] for c, m in run["results"].items()}
    assert statuses == {
        "palette": "ignored",
        "lighting": "ignored",
        "era": "ignored",
        "lens": "untested",
    }
    assert authed.get("/api/cinema/adherence/runs/nope").status_code == 404


@pytest.mark.skipif(not LIVE_KEY, reason="LIVE_API=1 and FRAMESTUDIO_LIVE_GOOGLE_KEY (paid)")
def test_live_palette_and_lighting_on_nano_banana(authed: TestClient) -> None:
    assert (
        authed.put("/api/connections/google", json={"api_key": LIVE_KEY}).json()["status"] == "ok"
    )
    started = authed.post(
        "/api/cinema/adherence/runs",
        json={"model": "nano-banana", "controls": ["palette", "lighting"], "budget_usd": 0.4},
    )
    assert started.status_code == 202, started.text
    run_id = started.json()["id"]
    run = wait_until(
        lambda: (
            (r := authed.get(f"/api/cinema/adherence/runs/{run_id}").json())["state"] != "running"
            and r
        ),
        timeout=300,
        interval=2,
    )
    assert run["state"] == "done", run
    assert set(run["results"]) == {"palette", "lighting"}
