"""M5.5: lighting rigs. A rig of up to four lights (direction, colour,
brightness, softness) and ambient fill becomes lighting words; a saved rig
reloads exactly and changes the compiled prompt."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from framestudio.cinema import Light, Rig, vocab
from framestudio.cinema.lighting import describe, direction


def light(**values: Any) -> Light:
    return Light(**{"azimuth": 0, "elevation": 0, "colour": "#FFFFFF", **values})


@pytest.mark.parametrize(
    ("azimuth", "elevation", "words"),
    [
        (0, 0, "from the front"),
        (45, 0, "from the front right"),
        (90, 5, "from the right side"),
        (135, 0, "from behind on the right"),
        (180, 10, "from directly behind"),
        (225, 0, "from behind on the left"),
        (270, 0, "from the left side"),
        (315, 0, "from the front left"),
        (350, 0, "from the front"),
        (315, 35, "from high on the front left"),
        (180, 35, "from high directly behind"),
        (90, -20, "from low on the right side"),
        (10, 70, "from directly above"),
        (200, -60, "from below"),
    ],
)
def test_directions(azimuth: float, elevation: float, words: str) -> None:
    assert direction(light(azimuth=azimuth, elevation=elevation)) == words


def test_rig_to_words() -> None:
    hard_key = Rig(lights=[light(azimuth=315, colour="#F2A65A", softness=0.1)], ambient=0.05)
    assert (
        describe(hard_key)
        == "a hard warm amber key light from the front left; high contrast with deep shadows"
    )
    with_fill = Rig(
        lights=[
            light(azimuth=315, colour="#FFFFFF", intensity=0.9, softness=0.5),
            light(azimuth=90, colour="#7BC3EA", intensity=0.2, softness=0.9),
        ],
        ambient=0.1,
    )
    assert describe(with_fill) == (
        "a white key light from the front left, a faint soft cool sky blue fill light "
        "from the right side; moderate contrast"
    )
    rim = Rig(
        lights=[
            light(azimuth=10, intensity=0.8, softness=0.8),
            light(azimuth=180, elevation=30, intensity=0.6),
        ]
    )
    assert "rim light from high directly behind" in describe(rim)
    even = Rig(
        lights=[light(azimuth=330, softness=0.9), light(azimuth=30, softness=0.9, intensity=0.9)],
        ambient=0.3,
    )
    assert describe(even).endswith("low contrast, even and open")
    behind_only = Rig(lights=[light(azimuth=180, elevation=5)], ambient=0.02)
    assert describe(behind_only).endswith("the subject in silhouette against the light")
    dark = Rig(lights=[light(intensity=0)], ambient=0.0)
    assert describe(dark) == "near darkness"


def test_rigs_check_their_values() -> None:
    with pytest.raises(ValidationError):
        Rig(lights=[])
    with pytest.raises(ValidationError):
        Rig(lights=[light()] * 5)
    with pytest.raises(ValidationError, match="not a colour"):
        light(colour="orange")
    with pytest.raises(ValidationError):
        light(azimuth=360)
    assert light(colour="#ffd9a0").colour == "#FFD9A0"


def test_every_preset_has_a_rig() -> None:
    words = {t.id: describe(t.rig) for t in vocab().lighting if t.rig}
    assert set(words) == {t.id for t in vocab().lighting}
    assert words["silhouette"].endswith("in silhouette against the light")
    assert words["soft-cross"].endswith("low contrast, even and open")
    assert words["overhead-fall"].startswith("a hard warm off-white key light from directly above")


def test_a_saved_rig_reloads_exactly_and_changes_the_prompt(authed: TestClient) -> None:
    """M5.5 Done when: a saved rig reloads exactly and changes the compiled prompt."""
    rig = {
        "lights": [
            {
                "azimuth": 300.0,
                "elevation": 22.5,
                "colour": "#FFB46B",
                "intensity": 0.85,
                "softness": 0.2,
            },
            {
                "azimuth": 165.0,
                "elevation": 40.0,
                "colour": "#7BC3EA",
                "intensity": 0.5,
                "softness": 0.75,
            },
        ],
        "ambient": 0.08,
    }
    saved = authed.post(
        "/api/cinema/presets", json={"kind": "lighting", "name": "Diner neon", "data": rig}
    )
    assert saved.status_code == 201, saved.text
    [reloaded] = authed.get("/api/cinema/presets", params={"kind": "lighting"}).json()
    assert reloaded["data"] == rig and Rig.model_validate(reloaded["data"]) == Rig.model_validate(
        rig
    )

    def lighting_section(lighting: dict[str, Any]) -> str:
        body = {
            "model": "mock",
            "prompt": "A woman at a diner counter",
            "shot": {"lighting": lighting},
        }
        preview = authed.post("/api/generations/preview", json=body).json()
        return next(s["text"] for s in preview["sections"] if s["family"] == "lighting")

    with_rig = lighting_section({"preset": "practicals", "rig": reloaded["data"]})
    assert with_rig == f"Lighting: {describe(Rig.model_validate(rig))}."
    assert "cool sky blue rim light" in with_rig
    # The preset alone gives the preset's words; a changed rig gives other words.
    assert lighting_section({"preset": "practicals"}) != with_rig
    moved = {**rig, "lights": [{**rig["lights"][0], "azimuth": 60.0}, rig["lights"][1]]}
    assert "from the front right" in lighting_section({"rig": moved})

    words = authed.post("/api/cinema/lighting/describe", json=rig).json()["words"]
    assert words == describe(Rig.model_validate(rig))
    bad = authed.post(
        "/api/cinema/presets", json={"kind": "lighting", "name": "x", "data": {"lights": []}}
    )
    assert bad.status_code == 422
