"""Regenerate the media fixture corpus (committed; run only to change it).

    cd backend && uv run python tests/fixtures/make_corpus.py

Every file is tiny. The JPEG carries an EXIF orientation so ingest must
report its displayed size; the MP3 has cover art so detection must not
mistake it for a video.
"""

from __future__ import annotations

import io
import subprocess
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent / "media"


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def main() -> None:
    HERE.mkdir(exist_ok=True)
    Image.new("RGB", (96, 64), (242, 166, 90)).save(HERE / "still.png")
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90° on display: 80x40 stored, 40x80 shown
    Image.new("RGB", (80, 40), (127, 168, 245)).save(HERE / "rotated.jpg", exif=exif, quality=80)
    Image.new("RGBA", (64, 64), (115, 201, 155, 200)).save(HERE / "alpha.webp", quality=80)
    frames = [Image.new("P", (48, 32), i * 40) for i in range(3)]
    frames[0].save(HERE / "anim.gif", save_all=True, append_images=frames[1:], duration=100, loop=0)

    src_v = "testsrc2=size=160x90:rate=24:duration=1"
    src_a = "sine=frequency=440:sample_rate=48000:duration=1"
    ffmpeg("-f", "lavfi", "-i", src_v, "-f", "lavfi", "-i", src_a, "-c:v", "libx264",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(HERE / "clip.mp4"))  # fmt: skip
    ffmpeg("-f", "lavfi", "-i", src_v, "-c:v", "libx264", "-pix_fmt", "yuv420p",
           str(HERE / "silent.mov"))  # fmt: skip
    ffmpeg("-f", "lavfi", "-i", src_v, "-f", "lavfi", "-i", src_a, "-c:v", "libvpx-vp9",
           "-b:v", "100k", "-c:a", "libopus", "-shortest", str(HERE / "clip.webm"))  # fmt: skip
    ffmpeg("-f", "lavfi", "-i", src_a, str(HERE / "tone.wav"))
    cover = io.BytesIO()
    Image.new("RGB", (32, 32), (200, 80, 80)).save(cover, format="PNG")
    (HERE / "_cover.png").write_bytes(cover.getvalue())
    ffmpeg("-f", "lavfi", "-i", src_a, "-i", str(HERE / "_cover.png"), "-map", "0:a", "-map", "1:v",
           "-c:a", "libmp3lame", "-b:a", "64k", "-c:v", "png", "-disposition:v", "attached_pic",
           str(HERE / "song.mp3"))  # fmt: skip
    (HERE / "_cover.png").unlink()

    png = (HERE / "still.png").read_bytes()
    (HERE / "truncated.png").write_bytes(png[: len(png) // 2])
    (HERE / "garbage.mp4").write_bytes(b"\x00\x01not really a video" * 64)
    (HERE / "notes.txt").write_text("just some text, not media\n")
    (HERE / "empty.jpg").write_bytes(b"")


if __name__ == "__main__":
    main()
