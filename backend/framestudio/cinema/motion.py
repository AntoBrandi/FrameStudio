"""Camera-move smoke checks (plan step M5.3): does a clip move the way its
camera move says?

``divergence`` estimates how much the picture expands from the first frame to
the last, the average optical-flow divergence of a zoom: the scale ``s`` at
which the first frame's centre, enlarged, best matches the last frame (or the
last frame's centre the first), as ``log(s)``. It is clearly positive for a
dolly or zoom in, near zero for a locked-off shot, and negative for a dolly
out. Pure Python on small greyscale frames, so it runs anywhere ffmpeg does.
"""

from __future__ import annotations

import math
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

SIZE = 96
#: Scales tried, from 1 (no change) up to about 1.6 (a strong push in).
SCALES = [round(1 + 0.02 * i, 2) for i in range(31)]


def _frame(clip: Path, last: bool, out: Path) -> Image.Image:
    from framestudio.media.tools import ffmpeg_path

    ffmpeg = ffmpeg_path()
    if ffmpeg is None:
        raise RuntimeError("ffmpeg is required to measure camera moves")
    seek = ["-sseof", "-0.2"] if last else []
    subprocess.run(
        [ffmpeg, "-v", "error", "-y", *seek, "-i", str(clip), "-frames:v", "1", str(out)],
        check=True,
        timeout=60,
    )
    with Image.open(out) as image:
        return image.convert("L").resize((SIZE, SIZE), Image.Resampling.BILINEAR)


def first_and_last(clip: Path) -> tuple[Image.Image, Image.Image]:
    with tempfile.TemporaryDirectory(prefix="framestudio-motion-") as tmp:
        return _frame(clip, False, Path(tmp) / "a.png"), _frame(clip, True, Path(tmp) / "b.png")


def _enlarge(image: Image.Image, scale: float) -> Image.Image:
    """The centre of ``image`` enlarged by ``scale`` back to full size."""
    side = SIZE / scale
    box = ((SIZE - side) / 2, (SIZE - side) / 2, (SIZE + side) / 2, (SIZE + side) / 2)
    return image.resize((SIZE, SIZE), Image.Resampling.BILINEAR, box=box)


def _mismatch(a: Image.Image, b: Image.Image) -> float:
    # The border can hold content that came into view; compare the middle.
    inner = (SIZE // 8, SIZE // 8, SIZE - SIZE // 8, SIZE - SIZE // 8)
    return ImageStat.Stat(ImageChops.difference(a.crop(inner), b.crop(inner))).mean[0]


def zoom_between(first: Image.Image, last: Image.Image) -> float:
    """The scale from ``first`` to ``last`` (above 1: the picture grew)."""
    best_scale, best = 1.0, _mismatch(first, last)
    for scale in SCALES[1:]:
        grew = _mismatch(_enlarge(first, scale), last)
        if grew < best:
            best_scale, best = scale, grew
        shrank = _mismatch(first, _enlarge(last, scale))
        if shrank < best:
            best_scale, best = 1 / scale, shrank
    return best_scale


def divergence(clip: Path) -> float:
    """``log`` of the zoom between a clip's first and last frames."""
    first, last = first_and_last(clip)
    return math.log(zoom_between(first, last))
