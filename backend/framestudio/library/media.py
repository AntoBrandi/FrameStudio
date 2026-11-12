"""Recognise media files and make their derived files (plan step M1.1).

Formats are identified by content, never by extension: Pillow for images,
ffprobe for video and audio. The extension only decides how a failure is
worded, "damaged" for a file that claims to be media, "not supported" for
anything else.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PIL import Image, ImageOps, UnidentifiedImageError

from framestudio.media.tools import (
    MediaToolError,
    ffmpeg_path,
    ffprobe_path,
    has_encoder,
    run_ffmpeg,
)

Kind = Literal["image", "video", "audio"]

THUMB_SIZE = 768
PREVIEW_SECONDS = 3
PREVIEW_WIDTH = 320

IMAGE_FORMATS: dict[str, tuple[str, str]] = {
    "PNG": ("image/png", "png"),
    "JPEG": ("image/jpeg", "jpg"),
    "WEBP": ("image/webp", "webp"),
    "GIF": ("image/gif", "gif"),
    "BMP": ("image/bmp", "bmp"),
    "TIFF": ("image/tiff", "tiff"),
}
MEDIA_EXTENSIONS = frozenset(
    {
        "png", "jpg", "jpeg", "webp", "gif", "bmp", "tif", "tiff",
        "mp4", "m4v", "mov", "webm", "mkv", "avi",
        "mp3", "wav", "flac", "ogg", "oga", "opus", "m4a", "aac",
    }
)  # fmt: skip


class MediaError(Exception):
    """A file that cannot become an asset, with a code the UI can explain."""

    def __init__(self, code: Literal["unsupported_type", "corrupt_file"], message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class MediaInfo:
    kind: Kind
    mime: str
    ext: str
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    fps: float | None = None
    has_audio: bool | None = None
    video_codec: str | None = None
    audio_codec: str | None = None


@dataclass(frozen=True)
class Derived:
    thumb: str | None = None
    poster: str | None = None
    preview: str | None = None


def _suffix(name: str | None) -> str:
    return Path(name or "").suffix.lower().lstrip(".")


def _rate(value: str | None) -> float | None:
    if not value or value in {"0/0", "0"}:
        return None
    num, _, den = value.partition("/")
    try:
        rate = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return None
    return round(rate, 3) if rate > 0 else None


def _damaged(name: str | None, detail: str) -> MediaError:
    label = name or "The file"
    return MediaError("corrupt_file", f"{label} could not be read; it may be damaged ({detail}).")


def _unsupported(name: str | None) -> MediaError:
    label = name or "This file"
    return MediaError(
        "unsupported_type",
        f"{label} is not a supported image, video or audio file. "
        "Supported: PNG, JPEG, WebP, GIF, MP4, MOV, WebM, MKV, MP3, WAV, FLAC, OGG, M4A.",
    )


def _inspect_image(path: Path, name: str | None) -> MediaInfo | None:
    try:
        with Image.open(path) as image:
            fmt = image.format or ""
            if fmt not in IMAGE_FORMATS:
                raise _unsupported(name)
            try:
                image.load()  # decodes the first frame: catches truncated files
            except (OSError, SyntaxError, ValueError) as exc:
                raise _damaged(name, str(exc)) from exc
            oriented = ImageOps.exif_transpose(image) or image
            width, height = oriented.size
    except UnidentifiedImageError:
        return None
    except MediaError:
        raise
    except (OSError, SyntaxError, ValueError) as exc:
        raise _damaged(name, str(exc)) from exc
    mime, ext = IMAGE_FORMATS[fmt]
    return MediaInfo(kind="image", mime=mime, ext=ext, width=width, height=height)


def _ffprobe(path: Path) -> dict[str, object] | None:
    ffprobe = ffprobe_path()
    if not ffprobe:
        raise MediaError(
            "unsupported_type",
            "Video and audio need ffmpeg, which was not found. Install ffmpeg and make sure "
            "it is on PATH.",
        )
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
        return None
    return json.loads(completed.stdout or "{}")  # type: ignore[no-any-return]


def _av_mime(kind: Kind, format_name: str, ext: str, codec: str | None) -> tuple[str, str]:
    names = set(format_name.split(","))
    if "mov" in names or "mp4" in names:
        if kind == "audio":
            return "audio/mp4", "m4a"
        return ("video/quicktime", "mov") if ext == "mov" else ("video/mp4", "mp4")
    if "matroska" in names or "webm" in names:
        webm_codecs = {"vp8", "vp9", "av1", "opus", "vorbis"}
        if ext == "webm" or (codec in webm_codecs and ext != "mkv"):
            return ("audio/webm", "webm") if kind == "audio" else ("video/webm", "webm")
        return "video/x-matroska", "mkv"
    if "avi" in names:
        return "video/x-msvideo", "avi"
    if "mp3" in names:
        return "audio/mpeg", "mp3"
    if "wav" in names:
        return "audio/wav", "wav"
    if "flac" in names:
        return "audio/flac", "flac"
    if "ogg" in names:
        return ("video/ogg", "ogv") if kind == "video" else ("audio/ogg", "ogg")
    if "aac" in names:
        return "audio/aac", "aac"
    raise MediaError("unsupported_type", f"The container format '{format_name}' is not supported.")


def inspect_media(path: Path, name: str | None = None) -> MediaInfo:
    """Identify a file by content; raise :class:`MediaError` when it cannot be used."""
    if path.stat().st_size == 0:
        raise _damaged(name, "the file is empty")
    image = _inspect_image(path, name)
    if image is not None:
        return image
    ext = _suffix(name)
    data = _ffprobe(path)
    if data is None:
        raise (
            _damaged(name, "ffprobe could not read it")
            if ext in MEDIA_EXTENSIONS
            else _unsupported(name)
        )
    fmt = data.get("format", {}) or {}
    format_name = str(fmt.get("format_name", ""))  # type: ignore[union-attr]
    if "image2" in format_name or format_name.endswith("_pipe"):
        raise _unsupported(name)
    streams = [s for s in data.get("streams", []) or [] if isinstance(s, dict)]  # type: ignore[union-attr]
    video = next(
        (
            s
            for s in streams
            if s.get("codec_type") == "video"
            and not (s.get("disposition") or {}).get("attached_pic")
        ),
        None,
    )
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None and audio is None:
        raise (
            _damaged(name, "no audio or video streams")
            if ext in MEDIA_EXTENSIONS
            else _unsupported(name)
        )
    duration_text = fmt.get("duration") or (video or audio or {}).get("duration")  # type: ignore[union-attr]
    try:
        duration = round(float(duration_text), 3) if duration_text else None
    except ValueError:
        duration = None
    kind: Kind = "video" if video is not None else "audio"
    mime, out_ext = _av_mime(kind, format_name, ext, (video or audio or {}).get("codec_name"))
    return MediaInfo(
        kind=kind,
        mime=mime,
        ext=out_ext,
        width=int(video["width"]) if video and video.get("width") else None,
        height=int(video["height"]) if video and video.get("height") else None,
        duration=duration,
        fps=_rate(video.get("avg_frame_rate") or video.get("r_frame_rate")) if video else None,
        has_audio=audio is not None,
        video_codec=str(video.get("codec_name")) if video else None,
        audio_codec=str(audio.get("codec_name")) if audio else None,
    )


# ----------------------------------------------------------------- derived


def derived_dir(sha256: str) -> str:
    return f"derived/{sha256[:2]}/{sha256}"


def _save_webp(image: Image.Image, dest: Path) -> None:
    image = ImageOps.exif_transpose(image) or image
    if image.mode not in {"RGB", "RGBA"}:
        image = image.convert("RGBA" if "A" in image.getbands() or image.mode == "P" else "RGB")
    image.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.Resampling.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    image.save(tmp, format="WEBP", quality=80, method=4)
    tmp.replace(dest)


def _video_poster(source: Path, info: MediaInfo, dest: Path, scratch: Path) -> None:
    at = min(1.0, (info.duration or 0) * 0.1)
    frame = scratch / "poster.png"
    run_ffmpeg(["-ss", f"{at:.3f}", "-i", str(source), "-frames:v", "1", str(frame)], timeout=60)
    if not frame.exists():  # seeking past the only frame of a very short clip
        run_ffmpeg(["-i", str(source), "-frames:v", "1", str(frame)], timeout=60)
    with Image.open(frame) as image:
        _save_webp(image, dest)


def _video_preview(source: Path, dest: Path) -> None:
    codec = (
        ["-c:v", "libx264", "-preset", "veryfast", "-crf", "30"]
        if has_encoder("libx264")
        else ["-c:v", "mpeg4", "-q:v", "8"]
    )
    tmp = dest.with_name("preview.tmp.mp4")
    run_ffmpeg(
        [
            "-i", str(source), "-t", str(PREVIEW_SECONDS),
            "-vf", f"scale={PREVIEW_WIDTH}:-2,fps=15", "-an",
            *codec, "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(tmp),
        ],
        timeout=120,
    )  # fmt: skip
    tmp.replace(dest)


def _waveform(source: Path, dest: Path, scratch: Path) -> None:
    picture = scratch / "wave.png"
    run_ffmpeg(
        [
            "-i", str(source),
            "-filter_complex", "aformat=channel_layouts=mono,showwavespic=s=768x192:colors=#F2A65A",
            "-frames:v", "1", str(picture),
        ],
        timeout=120,
    )  # fmt: skip
    with Image.open(picture) as image:
        _save_webp(image, dest)


def make_derived(
    source: Path, info: MediaInfo, sha256: str, media_root: Path, scratch: Path
) -> Derived:
    """Thumbnail (all kinds), poster and hover preview (video). Files are keyed by
    content hash, so identical files share them; existing ones are reused."""
    base = derived_dir(sha256)
    folder = media_root / base
    folder.mkdir(parents=True, exist_ok=True)
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        if info.kind == "image":
            thumb = folder / "thumb.webp"
            if not thumb.exists():
                with Image.open(source) as image:
                    image.seek(0)
                    _save_webp(image, thumb)
            return Derived(thumb=f"{base}/thumb.webp")
        if not ffmpeg_path():
            return Derived()
        if info.kind == "video":
            poster = folder / "poster.webp"
            if not poster.exists():
                _video_poster(source, info, poster, scratch)
            preview = folder / "preview.mp4"
            if not preview.exists() and (info.duration or 0) > 0:
                _video_preview(source, preview)
            return Derived(
                thumb=f"{base}/poster.webp",
                poster=f"{base}/poster.webp",
                preview=f"{base}/preview.mp4" if preview.exists() else None,
            )
        wave = folder / "waveform.webp"
        if not wave.exists():
            _waveform(source, wave, scratch)
        return Derived(thumb=f"{base}/waveform.webp")
    except (MediaToolError, OSError) as exc:
        raise MediaError(
            "corrupt_file", f"The file could not be decoded; it may be damaged ({exc})."
        ) from exc
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def remove_derived(sha256: str, media_root: Path) -> None:
    shutil.rmtree(media_root / derived_dir(sha256), ignore_errors=True)
