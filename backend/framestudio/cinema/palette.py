"""Colour palettes (plan step M5.4): a palette from any picture, its words
for a prompt, and a colour grade that pulls a picture or clip toward it.

* **Extraction**: seeded k-means in CIELAB over a downscaled copy (k-means++
  start, fixed seed, so the same picture always gives the same swatches),
  ordered from dark to light.
* **Words**: each swatch named by its nearest named colour, with the overall
  temperature (warm, cool, neutral) and saturation (muted, vivid).
* **Grade**: a 3D LUT (``.cube``) that moves every colour's hue and chroma
  toward the swatches near it in lightness and colour, keeping most of its
  lightness, mixed in by a strength from 0 to 1; ffmpeg applies it with
  ``lut3d`` to stills and clips alike.
"""

from __future__ import annotations

import math
import random
import subprocess
from pathlib import Path

from PIL import Image

from framestudio.cinema.colour import (
    Lab,
    chroma,
    distance,
    hex_to_lab,
    hue_degrees,
    rgb_to_hex,
    to_lab,
    to_rgb,
)

SAMPLE = 64
LUT_SIZE = 17

#: Names for the words: (name, sRGB hex).
NAMES: list[tuple[str, str]] = [
    ("black", "#0A0A0A"), ("charcoal", "#36454F"), ("slate grey", "#708090"), ("grey", "#8C8C8C"),
    ("silver", "#C0C0C0"), ("off-white", "#F2F0EA"), ("white", "#FFFFFF"), ("cream", "#F3E5C0"),
    ("beige", "#D9C7A1"), ("sand", "#C2A878"), ("tan", "#A88B62"), ("brown", "#6B4423"),
    ("dark brown", "#3B2516"), ("rust", "#A3471F"), ("burnt orange", "#C8611E"),
    ("orange", "#F08A24"), ("amber", "#F2A65A"), ("gold", "#D9A520"), ("mustard", "#C9A227"),
    ("yellow", "#F2D43D"), ("pale yellow", "#F6EFA8"), ("olive", "#6B6B2A"),
    ("moss green", "#5B7A3A"),
    ("green", "#3E9A4A"), ("mint", "#A1EEBD"), ("sage", "#9CAF88"), ("forest green", "#1F4D2B"),
    ("teal", "#1F6F78"), ("deep teal", "#12404D"), ("turquoise", "#3CC4C4"), ("cyan", "#19C3E6"),
    ("sky blue", "#7BC3EA"), ("steel blue", "#4A6F8F"), ("blue", "#2E5BD9"), ("navy", "#16244D"),
    ("midnight blue", "#0B1D26"), ("indigo", "#3B2A85"), ("violet", "#7B5BC4"),
    ("lavender", "#C9B6E4"), ("plum", "#5E2750"), ("purple", "#6A2C8C"), ("magenta", "#C21E8C"),
    ("pink", "#F09AC0"), ("blush", "#F6D6D6"), ("rose", "#D4607A"), ("red", "#C8262E"),
    ("crimson", "#8E1B2C"), ("maroon", "#4D1419"),
]  # fmt: skip
_NAMED = [(name, hex_to_lab(value)) for name, value in NAMES]


# ---------------------------------------------------------------- extraction


def _pixels(path: Path) -> list[Lab]:
    with Image.open(path) as image:
        small = image.convert("RGB").resize((SAMPLE, SAMPLE), Image.Resampling.BILINEAR)
        return [to_lab(p) for p in small.get_flattened_data()]  # type: ignore[arg-type]


def _kmeans(points: list[Lab], k: int, seed: int, rounds: int = 20) -> list[Lab]:
    rng = random.Random(seed)  # noqa: S311 (a reproducible start, not a secret)
    centers = [points[rng.randrange(len(points))]]
    while len(centers) < k:  # k-means++: far points are likelier next centres
        weights = [min(distance(p, c) for c in centers) ** 2 for p in points]
        total = sum(weights)
        if total == 0:
            break
        pick, acc = rng.random() * total, 0.0
        for point, weight in zip(points, weights, strict=True):
            acc += weight
            if acc >= pick:
                centers.append(point)
                break
    for _ in range(rounds):
        groups: list[list[Lab]] = [[] for _ in centers]
        for point in points:
            nearest = min(range(len(centers)), key=lambda i: distance(point, centers[i]))
            groups[nearest].append(point)
        moved = [
            (
                sum(p[0] for p in group) / len(group),
                sum(p[1] for p in group) / len(group),
                sum(p[2] for p in group) / len(group),
            )
            if group
            else center
            for group, center in zip(groups, centers, strict=True)
        ]
        if all(distance(a, b) < 0.05 for a, b in zip(moved, centers, strict=True)):
            centers = moved
            break
        centers = moved
    return centers


def extract_palette(path: Path, count: int = 6, seed: int = 7) -> tuple[list[str], list[float]]:
    """``count`` swatches (hex) of a picture, dark to light, and the share of
    the picture each covers. Colours closer than a just-noticeable difference
    are merged, so a plain picture gives fewer."""
    points = _pixels(path)
    centers = sorted(_kmeans(points, count, seed), key=lambda c: c[0])
    kept: list[Lab] = []
    for center in centers:
        if all(distance(center, k) > 3.0 for k in kept):
            kept.append(center)
    counts = [0] * len(kept)
    for point in points:
        counts[min(range(len(kept)), key=lambda i: distance(point, kept[i]))] += 1
    return [rgb_to_hex(to_rgb(c)) for c in kept], [n / len(points) for n in counts]


def extract(path: Path, count: int = 6, seed: int = 7) -> list[str]:
    return extract_palette(path, count, seed)[0]


# ---------------------------------------------------------------- words


def colour_name(lab: Lab) -> str:
    return min(_NAMED, key=lambda named: distance(lab, named[1]))[0]


def describe(swatches: list[str], shares: list[float] | None = None) -> str:
    """Prompt words for a palette: 'muted, cool tones: charcoal, steel blue,
    teal and cream'. Temperature and saturation weigh each swatch by how much
    of the picture it covers (``shares``), or equally."""
    labs = [hex_to_lab(s) for s in swatches]
    weights = shares or [1.0] * len(labs)
    names = list(dict.fromkeys(colour_name(lab) for lab in labs))
    coloured = [(lab, w) for lab, w in zip(labs, weights, strict=True) if chroma(lab) > 8]
    mood = []
    total = sum(w for _, w in coloured)
    if coloured and total > 0:
        mean_chroma = sum(chroma(lab) * w for lab, w in coloured) / total
        mood.append("vivid" if mean_chroma > 45 else "muted" if mean_chroma < 22 else "rich")
        # Warm hues run from red to yellow: Lab hue below about 100° or above 330°.
        warm = sum(w for lab, w in coloured if hue_degrees(lab) < 100 or hue_degrees(lab) > 330)
        cool = total - warm
        mood.append("warm" if warm > 1.5 * cool else "cool" if cool > 1.5 * warm else "mixed")
    else:
        mood.append("neutral")
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return f"{', '.join(mood)} tones: {listed}"


# ---------------------------------------------------------------- grade


def lut_cube(swatches: list[str], strength: float, size: int = LUT_SIZE) -> str:
    """A 3D LUT pulling colours toward the palette by ``strength`` (0–1)."""
    targets = [hex_to_lab(s) for s in swatches]
    sigma = 28.0
    lines = ['TITLE "FrameStudio palette"', f"LUT_3D_SIZE {size}"]
    step = 255 / (size - 1)
    for bi in range(size):  # the .cube order: red changes fastest
        for gi in range(size):
            for ri in range(size):
                lab = to_lab((ri * step, gi * step, bi * step))
                # Soft assignment: swatches near in lightness and colour count most.
                weights = [math.exp(-((distance(lab, t) / sigma) ** 2)) + 1e-9 for t in targets]
                total = sum(weights)
                ta = sum(w * t[1] for w, t in zip(weights, targets, strict=True)) / total
                tb = sum(w * t[2] for w, t in zip(weights, targets, strict=True)) / total
                tl = sum(w * t[0] for w, t in zip(weights, targets, strict=True)) / total
                graded = (
                    lab[0] + strength * 0.25 * (tl - lab[0]),
                    lab[1] + strength * (ta - lab[1]),
                    lab[2] + strength * (tb - lab[2]),
                )
                r, g, b = to_rgb(graded)
                lines.append(f"{r / 255:.6f} {g / 255:.6f} {b / 255:.6f}")
    return "\n".join(lines) + "\n"


def grade(source: Path, dest: Path, swatches: list[str], strength: float, *, video: bool) -> None:
    """Write ``source`` graded toward the palette to ``dest`` (ffmpeg lut3d)."""
    from framestudio.media.tools import ffmpeg_path

    ffmpeg = ffmpeg_path()
    if ffmpeg is None:
        raise RuntimeError("ffmpeg is required to grade")
    cube = dest.with_suffix(".cube")
    cube.write_text(lut_cube(swatches, strength), encoding="utf-8")
    try:
        args = [
            ffmpeg,
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-vf",
            f"lut3d=file={cube}:interp=tetrahedral",
        ]
        if video:
            args += ["-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "copy",
                     "-movflags", "+faststart"]  # fmt: skip
        else:
            args += ["-frames:v", "1"]
        subprocess.run([*args, str(dest)], check=True, timeout=600)
    finally:
        cube.unlink(missing_ok=True)
