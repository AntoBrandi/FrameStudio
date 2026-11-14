"""M5.1: the ShotSpec and the prompt compiler. Each cinema control changes
the compiled request for every model family, in that family's own way.

Golden files in ``tests/golden/compiler/`` hold the compiled prompt, negative
prompt, native parameters and sections of each case for each profile; rewrite
them after a deliberate change with ``FRAMESTUDIO_UPDATE_GOLDEN=1`` and read
the diff."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from framestudio.catalog import ModelSpec, load_catalog
from framestudio.cinema import (
    VOCAB_FILE,
    CameraSpec,
    FilmSpec,
    LightingSpec,
    PaletteSpec,
    ShotSpec,
    vocab,
)
from framestudio.cinema.compiler import Compiled, compile_prompt, profile_for

GOLDEN = Path(__file__).parent / "golden" / "compiler"
UPDATE = os.environ.get("FRAMESTUDIO_UPDATE_GOLDEN") == "1"
SUBJECT = "Maya Ortiz (image 1) pushes through the door of the diner, rain streaking the glass"
DESCRIPTIONS = [("maya", "Maya Ortiz: Woman in her thirties, short dark hair, yellow raincoat.")]

CATALOG = load_catalog()


def diffusion_model() -> ModelSpec:
    """A diffusion model with a negative-prompt input, as fal imports them."""
    data = CATALOG.get("mock").model_dump(mode="json")  # type: ignore[union-attr]
    data.update(
        id="test-diffusion",
        name="Test diffusion",
        provider="fal",
        runtime="api",
        prompt_profile="diffusion",
        provider_inputs={"prompt": "prompt", "negative_prompt": "negative_prompt"},
        pricing={
            "unit": "image",
            "usd": 0.01,
            "source_url": "https://example.invalid/price",
            "checked_at": "2026-11-14",
        },
        limits={**data["limits"], "max_prompt_chars": 400},
    )
    return ModelSpec.model_validate(data)


# Each profile with a model of that family and the mode it is tested in.
PROFILES: dict[str, tuple[ModelSpec, str]] = {
    "gemini": (CATALOG.get("nano-banana-pro"), "image"),  # type: ignore[dict-item]
    "seedance": (CATALOG.get("seedance-2-0"), "video"),  # type: ignore[dict-item]
    "diffusion": (diffusion_model(), "image"),
    "generic": (CATALOG.get("mock"), "video"),  # type: ignore[dict-item]
}

CASES: dict[str, ShotSpec] = {
    "none": ShotSpec(),
    "film": ShotSpec(film=FilmSpec(genre="noir", era="1970s", tempo="calm")),
    "camera": ShotSpec(
        camera=CameraSpec(body="film-35", lens="anamorphic", focal_length=50, aperture="f/1.4")
    ),
    "moves": ShotSpec(moves=["dolly-in", "rack-focus"]),
    "lighting": ShotSpec(lighting=LightingSpec(preset="contre-jour")),
    "palette": ShotSpec(palette=PaletteSpec(id="teal-night")),
    "negatives": ShotSpec(film=FilmSpec(era="1920s"), palette=PaletteSpec(id="monochrome")),
    # The plan's M5 demo shot.
    "demo": ShotSpec(
        film=FilmSpec(genre="noir", era="1970s", tempo="calm"),
        camera=CameraSpec(body="film-35", lens="anamorphic", aperture="f/1.4"),
        moves=["dolly-in", "rack-focus"],
        palette=PaletteSpec(id="teal-night"),
        lighting=LightingSpec(preset="contre-jour"),
    ),
}

# One choice per control, to show each one changes the request.
SINGLE: dict[str, ShotSpec] = {
    "genre": ShotSpec(film=FilmSpec(genre="western")),
    "era": ShotSpec(film=FilmSpec(era="1980s")),
    "tempo": ShotSpec(film=FilmSpec(tempo="frantic")),
    "body": ShotSpec(camera=CameraSpec(body="vhs")),
    "lens": ShotSpec(camera=CameraSpec(lens="fisheye")),
    "focal_length": ShotSpec(camera=CameraSpec(focal_length=14)),
    "aperture": ShotSpec(camera=CameraSpec(aperture="f/16")),
    "moves": ShotSpec(moves=["orbit"]),
    "lighting": ShotSpec(lighting=LightingSpec(preset="silhouette")),
    "palette": ShotSpec(palette=PaletteSpec(id="golden-hour")),
}


def compiled(profile: str, shot: ShotSpec, **extra: Any) -> Compiled:
    spec, mode = PROFILES[profile]
    return compile_prompt(
        spec, extra.pop("mode", mode), SUBJECT, shot=shot, descriptions=DESCRIPTIONS, **extra
    )


def summary(result: Compiled) -> dict[str, Any]:
    return {
        "prompt": result.prompt,
        "negative_prompt": result.negative_prompt,
        "native": result.native,
        "sections": [
            {"family": s.family, "included": s.included, **({"note": s.note} if s.note else {})}
            for s in result.sections
        ],
        "warnings": result.warnings,
    }


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_golden_matrix(profile: str) -> None:
    """Snapshot of every case for one model family."""
    assert profile_for(PROFILES[profile][0]).id == profile
    result = {case: summary(compiled(profile, shot)) for case, shot in CASES.items()}
    path = GOLDEN / f"{profile}.json"
    text = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if UPDATE or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8")) == result


@pytest.mark.parametrize("profile", sorted(PROFILES))
@pytest.mark.parametrize("control", sorted(SINGLE))
def test_each_control_changes_the_request(profile: str, control: str) -> None:
    """M5.1 Done when: each control visibly changes the compiled request for
    each model profile (video-only controls are shown as left out of stills)."""
    base = compiled(profile, ShotSpec())
    changed = compiled(profile, SINGLE[control])
    _, mode = PROFILES[profile]
    if control in {"moves", "tempo"} and mode == "image":
        left_out = [
            s for s in changed.sections if not s.included and s.note == "Applies to video only."
        ]
        assert left_out and changed.prompt == base.prompt
    else:
        assert changed.prompt != base.prompt


def test_the_families_write_differently() -> None:
    demo = {p: compiled(p, CASES["demo"]).prompt for p in PROFILES}
    # Seedance names the camera movement right after the subject.
    assert re.search(r"glass\. Camera movement: the camera pushes in", demo["seedance"])
    # Nano Banana spells out the palette's colours; Seedance does not.
    assert "#1F6F78" in demo["gemini"] and "#1F6F78" not in demo["seedance"]
    # Diffusion models get tags, not sentences.
    assert "35mm film, anamorphic lens, oval bokeh" in demo["diffusion"]
    assert "Camera:" not in demo["diffusion"]
    # The descriptions come last, after a blank line (the diffusion model's
    # 400-character limit leaves it out: see the next tests).
    for prompt in (demo["gemini"], demo["seedance"], demo["generic"]):
        assert prompt.endswith(
            "\n\nMaya Ortiz: Woman in her thirties, short dark hair, yellow raincoat."
        )


def test_negatives_go_where_the_model_takes_them() -> None:
    shot = CASES["negatives"]
    assert "Avoid: colour." in compiled("gemini", shot).prompt
    diffusion = compiled("diffusion", shot)
    assert diffusion.negative_prompt == "colour" and "Avoid" not in diffusion.prompt
    seedance = compiled("seedance", shot)
    assert seedance.negative_prompt is None and "Avoid" not in seedance.prompt
    [note] = [s.note for s in seedance.sections if s.family == "negative"]
    assert note == "Seedance 2.0 takes no negative prompt."


def test_moves_from_tokens_come_first_and_once() -> None:
    spec, _ = PROFILES["seedance"]
    shot = ShotSpec(moves=["rack-focus", "orbit"])
    result = compile_prompt(
        spec, "video", SUBJECT, shot=shot, token_moves=["dolly-in", "rack-focus"]
    )
    assert (
        "Camera movement: the camera pushes in toward the subject, then the focus shifts "
        "from the foreground to the background, then the camera orbits around the subject"
    ) in result.prompt


def test_the_prompt_limit_drops_least_important_first() -> None:
    result = compiled("diffusion", CASES["demo"])  # its limit is 400 characters
    assert len(result.prompt) <= 400
    dropped = [
        s.family for s in result.sections if not s.included and s.note and "Left out" in s.note
    ]
    assert dropped and dropped[0] == "elements"
    fit = [w for w in result.warnings if "to fit" in w]
    assert fit == [
        "The description of @maya was left out to fit Test diffusion's 400-character prompt."
    ]


def test_native_parameters_and_flags() -> None:
    data = CATALOG.get("seedance-2-0").model_dump(mode="json")  # type: ignore[union-attr]
    data["params"]["camera_fixed"] = {
        "type": "boolean",
        "label": "Fixed camera",
        "default": False,
        "control": "toggle",
    }
    spec = ModelSpec.model_validate(data)
    still = compile_prompt(spec, "video", SUBJECT, shot=ShotSpec(moves=["static"]))
    assert still.native == {"camera_fixed": True}
    moving = compile_prompt(spec, "video", SUBJECT, shot=ShotSpec(moves=["orbit"]))
    assert moving.native == {}
    # Controls a model is measured to ignore are flagged, not removed.
    flagged = compile_prompt(spec, "video", SUBJECT, shot=CASES["camera"], ignored={"aperture"})
    [camera] = [s for s in flagged.sections if s.family == "camera"]
    assert camera.included and camera.note == "Seedance 2.0 tends to ignore this (aperture)."


def test_shotspec_checks_its_values() -> None:
    with pytest.raises(ValidationError, match="unknown genre 'jazz'"):
        ShotSpec(film=FilmSpec(genre="jazz"))
    with pytest.raises(ValidationError, match="not an f-number"):
        CameraSpec(aperture="1.4")
    with pytest.raises(ValidationError, match="unknown move"):
        ShotSpec(moves=["moonwalk"])
    with pytest.raises(ValidationError, match="needs a preset id"):
        PaletteSpec()
    custom = PaletteSpec(words="rust and slate", swatches=["#b7410e", "#708090"])
    assert custom.swatches == ["#B7410E", "#708090"]
    assert ShotSpec().is_empty() and not CASES["film"].is_empty()


def test_vocabulary() -> None:
    words = vocab()
    assert len(words.moves) >= 29 and len(words.lighting) == 6
    for move in words.moves:
        assert re.fullmatch(r"[a-z0-9][a-z0-9-]*", move.id)
    # Natural words the offline test stand-ins react to must not appear in it.
    lines = VOCAB_FILE.read_text(encoding="utf-8").lower().splitlines()
    text = "\n".join(line for line in lines if not line.lstrip().startswith("#"))
    for word in ("slow", "blocked", "inactive", "flaky", "expired"):
        assert not re.search(rf"\b{word}\b", text), word


# ---------------------------------------------------------------- through the API


def test_moves_are_prompt_tokens_and_the_shot_is_stored(authed: TestClient) -> None:
    parsed = authed.post(
        "/api/prompts/parse", json={"text": "rain on glass #dolly-in #ff0000"}
    ).json()
    assert [t["type"] for t in parsed["tokens"]] == ["text", "move", "text"]
    shot = {"film": {"genre": "noir"}, "lighting": {"preset": "contre-jour"}}
    body = {"model": "mock", "prompt": "a diner at night #dolly-in", "shot": shot}
    preview = authed.post("/api/generations/preview", json={**body, "mode": "video"}).json()
    families = [s["family"] for s in preview["sections"] if s["included"]]
    assert families == ["subject", "moves", "film", "lighting"]
    assert preview["prompt"].startswith("a diner at night. Look: film noir")
    still = authed.post("/api/generations/preview", json=body).json()
    assert "Camera moves and pace apply to video" in still["warnings"][0]
    [made] = authed.post("/api/generations", json=body).json()
    assert ShotSpec.model_validate(made["shot"]) == ShotSpec.model_validate(shot)
    bad = authed.post("/api/generations", json={**body, "shot": {"film": {"genre": "jazz"}}})
    assert bad.status_code == 422


def test_vocab_and_profiles_endpoints(authed: TestClient) -> None:
    words = authed.get("/api/cinema/vocab").json()
    assert {"genres", "eras", "tempos", "bodies", "lenses", "moves", "lighting", "palettes"} <= set(
        words
    )
    profiles = {p["id"] for p in authed.get("/api/cinema/profiles").json()}
    assert profiles == {"gemini", "seedance", "diffusion", "generic"}
