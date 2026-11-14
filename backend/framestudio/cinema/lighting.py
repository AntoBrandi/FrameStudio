"""Lighting rigs in words (plan step M5.5).

A rig's lights become one phrase each, brightest first: its role (key light,
fill, rim or backlight), quality (hard or soft), colour (named, warm or cool),
strength relative to the key, and direction as seen from the camera. The
balance between the key and the rest adds the contrast; a key from behind with
nothing in front is a silhouette.

    "a hard warm amber key light from the front left, a faint soft white fill
    light from the right side; high contrast with deep shadows"
"""

from __future__ import annotations

from framestudio.cinema import Light, Rig
from framestudio.cinema.colour import chroma, hex_to_lab, hue_degrees
from framestudio.cinema.palette import colour_name

#: Horizontal directions by azimuth sector (seen from the camera).
_SECTORS = [
    (22.5, "the front"),
    (67.5, "the front right"),
    (112.5, "the right side"),
    (157.5, "behind on the right"),
    (202.5, "directly behind"),
    (247.5, "behind on the left"),
    (292.5, "the left side"),
    (337.5, "the front left"),
    (360.0, "the front"),
]


def behind(light: Light) -> bool:
    return 112.5 <= light.azimuth < 247.5 and light.elevation < 60


def direction(light: Light) -> str:
    if light.elevation >= 60:
        return "from directly above"
    if light.elevation <= -45:
        return "from below"
    side = next(name for limit, name in _SECTORS if light.azimuth < limit)
    if light.elevation >= 25:
        return (
            f"from high on {side}"
            if not side.startswith(("behind", "directly"))
            else f"from high {side}"
        )
    if light.elevation <= -15:
        return (
            f"from low on {side}"
            if not side.startswith(("behind", "directly"))
            else f"from low {side}"
        )
    return f"from {side}"


def colour_words(hex_colour: str) -> str:
    lab = hex_to_lab(hex_colour)
    if chroma(lab) < 8:
        return "white" if lab[0] > 60 else "grey"
    hue = hue_degrees(lab)
    warmth = "warm" if hue < 100 or hue > 330 else "cool"
    return f"{warmth} {colour_name(lab)}"


def _phrase(light: Light, role: str, key_intensity: float) -> str:
    words = []
    relative = light.intensity / key_intensity if key_intensity else 0
    if role != "key light" and role != "backlight":
        if relative < 0.35:
            words.append("faint")
        elif relative < 0.7:
            words.append("gentle")
    if light.softness < 0.33:
        words.append("hard")
    elif light.softness > 0.66:
        words.append("soft")
    words.append(colour_words(light.colour))
    words.append(role)
    return f"a {' '.join(words)} {direction(light)}"


def describe(rig: Rig) -> str:
    lights = sorted((x for x in rig.lights if x.intensity > 0), key=lambda x: -x.intensity)
    if not lights:
        return "darkness, lit only by ambient light" if rig.ambient > 0.2 else "near darkness"
    key, rest = lights[0], lights[1:]
    phrases = [_phrase(key, "backlight" if behind(key) else "key light", key.intensity)]
    for light in rest:
        phrases.append(
            _phrase(light, "rim light" if behind(light) else "fill light", key.intensity)
        )
    front = [x for x in lights if not behind(x)]
    if behind(key) and all(x.intensity < 0.3 * key.intensity for x in front):
        balance = "the subject in silhouette against the light"
    else:
        fill = sum(x.intensity for x in front if x is not key) + rig.ambient
        ratio = key.intensity / max(fill, 0.05)
        if ratio > 4:
            balance = "high contrast with deep shadows"
        elif ratio < 1.6:
            balance = "low contrast, even and open"
        else:
            balance = "moderate contrast"
    return f"{', '.join(phrases)}; {balance}"
