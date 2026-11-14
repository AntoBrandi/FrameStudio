"""Continuity between a keyframe and its clip (plan step M5.7).

A clip animated from a keyframe should open on that picture, and close on the
end frame when one was given: the composition and the identity in it carry
over. These checks pull the clip's first and last frames with ffmpeg and score
how closely each matches its picture, from 0 (unrelated) to 1 (the same).

The score is the structural similarity (SSIM) of small colour copies, in
8×8 blocks: it compares brightness, contrast and structure locally, so it
stays high when a model re-renders the frame faithfully (a slight zoom,
compression) and drops when the clip starts somewhere else. It is a
heuristic: good for "did the clip start from my keyframe", not for ranking
subtle differences.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from PIL import Image, ImageOps

from framestudio.media.tools import run_ffmpeg

#: Width of the copies compared (height follows the frame).
SIDE = 96
#: Scores at or above this count as the same picture. Measured on the mock
#: engine: the same picture re-encoded scores about 1.0, unrelated pictures
#: 0.2-0.4, and the last frame of a 12% push-in 0.67 against its first.
SAME = 0.75


def extract_frame(video: Path, which: Literal["first", "last"], out: Path) -> Path:
    """Write the clip's first or last frame to ``out`` (a .png)."""
    if which == "first":
        run_ffmpeg(["-i", str(video), "-frames:v", "1", str(out)], timeout=60)
    else:
        # Decode the last second and keep overwriting: the file ends as the last frame.
        run_ffmpeg(["-sseof", "-1", "-i", str(video), "-update", "1", str(out)], timeout=60)
        if not out.exists():  # clips shorter than the seek
            run_ffmpeg(["-i", str(video), "-update", "1", str(out)], timeout=60)
    if not out.exists():
        raise ValueError(f"No frame could be read from {video.name}")
    return out


def _channels(image: Image.Image, size: tuple[int, int]) -> list[list[float]]:
    fitted = ImageOps.fit(image.convert("RGB"), size, Image.Resampling.BILINEAR)
    return [[float(v) for v in band.tobytes()] for band in fitted.split()]


#: SSIM constants for 8-bit values (Wang et al. 2004).
_C1, _C2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
BLOCK = 8


def _ssim(x: list[float], y: list[float], width: int, height: int) -> float:
    """Mean SSIM over non-overlapping blocks of one channel."""
    scores = []
    for top in range(0, height - BLOCK + 1, BLOCK):
        for left in range(0, width - BLOCK + 1, BLOCK):
            idx = [(top + r) * width + left + c for r in range(BLOCK) for c in range(BLOCK)]
            a = [x[i] for i in idx]
            b = [y[i] for i in idx]
            n = len(idx)
            ma, mb = sum(a) / n, sum(b) / n
            va = sum((v - ma) ** 2 for v in a) / n
            vb = sum((v - mb) ** 2 for v in b) / n
            cov = sum((p - ma) * (q - mb) for p, q in zip(a, b, strict=True)) / n
            scores.append(
                ((2 * ma * mb + _C1) * (2 * cov + _C2)) / ((ma**2 + mb**2 + _C1) * (va + vb + _C2))
            )
    return sum(scores) / len(scores) if scores else 0.0


def similarity(a: Path | Image.Image, b: Path | Image.Image) -> float:
    """How alike two pictures are, 0..1: the mean structural similarity
    (SSIM, in 8×8 blocks) of their colour channels on small copies. ``b`` is
    fitted to ``a``'s aspect ratio (cover and centre crop), as a video model
    fits a keyframe to the clip's frame."""
    first = Image.open(a) if isinstance(a, Path) else a
    second = Image.open(b) if isinstance(b, Path) else b
    try:
        width, height = first.size
        size = (SIDE, max(BLOCK, round(SIDE * height / max(1, width))))
        x, y = _channels(first, size), _channels(second, size)
    finally:
        if isinstance(a, Path):
            first.close()
        if isinstance(b, Path):
            second.close()
    score = sum(_ssim(p, q, *size) for p, q in zip(x, y, strict=True)) / 3
    return round(max(0.0, min(1.0, score)), 3)


def check_clip(
    clip: Path, start: Path | None, end: Path | None, scratch: Path
) -> dict[str, float | None]:
    """Scores for a clip against its start and end pictures (None when the
    clip had no such picture)."""
    scores: dict[str, float | None] = {"start": None, "end": None}
    if start is not None:
        frame = extract_frame(clip, "first", scratch / "first.png")
        scores["start"] = similarity(frame, start)
    if end is not None:
        frame = extract_frame(clip, "last", scratch / "last.png")
        scores["end"] = similarity(frame, end)
    return scores
