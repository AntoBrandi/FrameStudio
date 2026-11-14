"""Write N distinct small images (mixed formats) for import tests.

uv run python tests/fixtures/make_import_set.py OUT_DIR COUNT
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

FORMATS = [("png", "PNG"), ("jpg", "JPEG"), ("webp", "WEBP"), ("gif", "GIF")]
SIZES = [(160, 90), (90, 160), (120, 120), (200, 80)]


def main(out: Path, count: int) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        ext, fmt = FORMATS[index % len(FORMATS)]
        size = SIZES[index % len(SIZES)]
        color = ((index * 53) % 256, (index * 97) % 256, (index * 151) % 256)
        image = Image.new("RGB", size, color)
        ImageDraw.Draw(image).text((6, 6), f"import {index}", fill=(255, 255, 255))
        image.save(out / f"import-{index:03d}.{ext}", format=fmt)


if __name__ == "__main__":
    main(Path(sys.argv[1]), int(sys.argv[2]))
