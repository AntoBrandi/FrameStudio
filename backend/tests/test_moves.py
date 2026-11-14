"""M5.3: the camera move library. Moves are #move tokens, ordered, several
per shot, and compile differently by model capability: Seedance's own camera
verbs, sentences for other video models, tags for diffusion models, left out
of stills. The motion check confirms a clip moves the way its move says:
divergence clearly positive for a dolly in, near zero for a locked-off shot.

``LIVE_API=1`` with ``FRAMESTUDIO_LIVE_BYTEPLUS_KEY`` runs that check on two
Seedance 2.0 Fast clips (480p, 4 s: about $0.50)."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from framestudio.catalog import ModelSpec, load_catalog
from framestudio.cinema import ShotSpec, vocab
from framestudio.cinema.compiler import compile_prompt
from framestudio.cinema.motion import divergence
from framestudio.media.tools import ffmpeg_path

from .conftest import wait_until

CATALOG = load_catalog()
LIVE_KEY = (
    os.environ.get("FRAMESTUDIO_LIVE_BYTEPLUS_KEY") if os.environ.get("LIVE_API") == "1" else None
)
SUBJECT = "A woman walks through a market hall"
#: The camera verbs BytePlus's Seedance prompt guide lists, and what else the
#: Seedance wording may use (tilt, focus, handheld, first-person, aerial…).
SEEDANCE_WORDS = re.compile(
    r"\b(push|pull|pan|move|orbit|follow|rise|descend|zoom|tilt|still|focus|handheld|first-person|"
    r"fixed|aerial|fpv)\w*\b",
    re.IGNORECASE,
)


def diffusion_video() -> ModelSpec:
    data: dict[str, Any] = CATALOG.get("mock").model_dump(mode="json")  # type: ignore[union-attr]
    data.update(id="test-video-diffusion", name="Test video diffusion", prompt_profile="diffusion")
    return ModelSpec.model_validate(data)


def moves_section(spec: ModelSpec, mode: str, moves: list[str]) -> Any:
    result = compile_prompt(spec, mode, SUBJECT, token_moves=moves)
    return next((s for s in result.sections if s.family == "moves"), None), result


@pytest.mark.parametrize("move", sorted(vocab().move_ids))
def test_mapping_table(move: str) -> None:
    """Every move, for every kind of model."""
    term = vocab().find("moves", move)
    assert term is not None and "seedance" in term.profiles
    seedance, _ = moves_section(CATALOG.get("seedance-2-0"), "video", [move])  # type: ignore[arg-type]
    assert seedance.text == f"Camera movement: {term.profiles['seedance'].rstrip('.')}."
    assert SEEDANCE_WORDS.search(term.profiles["seedance"]), term.profiles["seedance"]
    generic, _ = moves_section(CATALOG.get("mock"), "video", [move])  # type: ignore[arg-type]
    assert generic.text == f"Camera movement: {term.phrase}."
    tags, _ = moves_section(diffusion_video(), "video", [move])
    assert tags.text == term.tags
    still, result = moves_section(CATALOG.get("nano-banana-pro"), "image", [move])  # type: ignore[arg-type]
    assert not still.included and still.note == "Applies to video only."
    assert term.phrase not in result.prompt


def test_moves_keep_their_order_and_seedance_wants_two() -> None:
    spec = CATALOG.get("seedance-2-0")
    assert spec is not None
    two, result = moves_section(spec, "video", ["dolly-in", "rack-focus"])
    assert two.text == (
        "Camera movement: the camera pushes in toward the subject, "
        "then the focus shifts from the foreground to the background."
    )
    assert two.note is None
    three, _ = moves_section(spec, "video", ["orbit", "dolly-in", "rack-focus"])
    assert three.text.startswith(
        "Camera movement: the camera orbits around the subject, then the camera pushes in"
    )
    assert three.note and "2 camera moves per clip" in three.note
    # Order comes from the prompt, then the shot's own list, each once.
    result = compile_prompt(
        spec, "video", SUBJECT, token_moves=["tilt-up"], shot=ShotSpec(moves=["orbit", "tilt-up"])
    )
    assert "the camera tilts up, then the camera orbits around the subject" in result.prompt


def test_typed_moves_reach_the_request(authed: TestClient) -> None:
    body = {"model": "mock", "mode": "video", "prompt": "rain on glass #dolly-in #rack-focus"}
    preview = authed.post("/api/generations/preview", json=body).json()
    assert preview["prompt"].startswith(
        "rain on glass. Camera movement: the camera dollies in toward the subject, "
        "then the focus racks"
    )


def clip(path: Path, zoom: str | None) -> Path:
    ffmpeg = ffmpeg_path()
    assert ffmpeg
    args = [ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x480:rate=24"]
    if zoom:
        center = "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        args += ["-vf", f"zoompan=z='{zoom}':{center}:d=1:s=320x240:fps=24"]
    subprocess.run([*args, "-t", "3", str(path)], check=True)
    return path


@pytest.mark.skipif(not ffmpeg_path(), reason="ffmpeg is required")
def test_divergence_reads_the_move(tmp_path: Path) -> None:
    push_in = divergence(clip(tmp_path / "in.mp4", "1+0.006*on"))
    pull_out = divergence(clip(tmp_path / "out.mp4", "1.45-0.006*on"))
    locked = divergence(clip(tmp_path / "still.mp4", None))
    assert push_in > 0.25 and pull_out < -0.25 and abs(locked) < 0.03


@pytest.mark.skipif(not LIVE_KEY, reason="LIVE_API=1 and FRAMESTUDIO_LIVE_BYTEPLUS_KEY (paid)")
def test_live_dolly_in_against_static_on_seedance(authed: TestClient, tmp_path: Path) -> None:
    assert (
        authed.put("/api/connections/byteplus", json={"api_key": LIVE_KEY}).json()["status"] == "ok"
    )
    results = {}
    for move in ("dolly-in", "static"):
        body = {
            "model": "seedance-2-0-fast",
            "mode": "video",
            "prompt": f"A quiet library reading room with long wooden tables. #{move}",
            "params": {"resolution": "480p", "duration": 4, "audio": False},
            "seed": 11,
        }
        [queued] = authed.post("/api/generations", json=body).json()
        done = wait_until(
            lambda q=queued: (
                (g := authed.get(f"/api/generations/{q['id']}").json())["status"]
                in {"succeeded", "failed"}
                and g
            ),
            timeout=600,
            interval=5,
        )
        assert done["status"] == "succeeded", done
        target = tmp_path / f"{move}.mp4"
        target.write_bytes(authed.get(f"/api/assets/{done['asset']['id']}/file").content)
        results[move] = divergence(target)
    assert results["dolly-in"] > 0.05, results
    assert abs(results["static"]) < 0.03, results
