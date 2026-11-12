"""ffmpeg and ffprobe, found on PATH or through FRAMESTUDIO_FFMPEG/FFPROBE."""

from __future__ import annotations

import functools
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class MediaToolError(RuntimeError):
    pass


def ffmpeg_path() -> str | None:
    return os.environ.get("FRAMESTUDIO_FFMPEG") or shutil.which("ffmpeg")


def ffprobe_path() -> str | None:
    return os.environ.get("FRAMESTUDIO_FFPROBE") or shutil.which("ffprobe")


@functools.cache
def has_encoder(name: str) -> bool:
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        return False
    try:
        listing = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return any(line.split()[1:2] == [name] for line in listing.splitlines() if line.strip())


@dataclass(frozen=True)
class ProbeResult:
    width: int | None
    height: int | None
    duration: float | None
    fps: float | None
    has_audio: bool
    video_codec: str | None


def _rate(value: str | None) -> float | None:
    if not value or value in {"0/0", "0"}:
        return None
    num, _, den = value.partition("/")
    try:
        return float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return None


def probe(path: Path) -> ProbeResult:
    ffprobe = ffprobe_path()
    if not ffprobe:
        raise MediaToolError("ffprobe was not found. Install ffmpeg and make sure it is on PATH.")
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise MediaToolError(completed.stderr.strip() or "ffprobe could not read the file")
    data = json.loads(completed.stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = any(s.get("codec_type") == "audio" for s in streams)
    duration = data.get("format", {}).get("duration")
    return ProbeResult(
        width=int(video["width"]) if video and video.get("width") else None,
        height=int(video["height"]) if video and video.get("height") else None,
        duration=float(duration) if duration else None,
        fps=_rate(video.get("avg_frame_rate") or video.get("r_frame_rate")) if video else None,
        has_audio=audio,
        video_codec=video.get("codec_name") if video else None,
    )


def run_ffmpeg(args: list[str], timeout: float = 300) -> None:
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        raise MediaToolError("ffmpeg was not found. Install ffmpeg and make sure it is on PATH.")
    completed = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise MediaToolError(completed.stderr.strip()[-2000:] or "ffmpeg failed")
