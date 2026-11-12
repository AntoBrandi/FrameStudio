"""Mock engine (decision D8): a PNG showing the prompt on a seed-coloured
background, or an ffmpeg test-pattern MP4 with an optional tone. Latency and
failure rate come from settings, or per request from ``latency_seconds`` and
``force_failure`` params."""

from __future__ import annotations

import asyncio
import colorsys
import random
import textwrap
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from framestudio.jobs.base import JobContext, JobError
from framestudio.media.tools import MediaToolError, ffmpeg_path, has_encoder, run_ffmpeg
from framestudio.providers.base import GenerationOutput, GenerationRequest

IMAGE_SIZES: dict[str, tuple[int, int]] = {
    "1:1": (768, 768),
    "16:9": (1024, 576),
    "9:16": (576, 1024),
    "4:3": (896, 672),
    "3:4": (672, 896),
    "21:9": (1176, 504),
}
VIDEO_SIZES: dict[str, tuple[int, int]] = {
    "1:1": (480, 480),
    "16:9": (640, 360),
    "9:16": (360, 640),
    "4:3": (576, 432),
    "3:4": (432, 576),
    "21:9": (840, 360),
}


def seed_color(seed: int) -> tuple[int, int, int]:
    hue = (seed * 0.618033988749895) % 1.0
    r, g, b = colorsys.hsv_to_rgb(hue, 0.55, 0.78)
    return int(r * 255), int(g * 255), int(b * 255)


def seed_hue_degrees(seed: int) -> int:
    return int(((seed * 0.618033988749895) % 1.0) * 360)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except (TypeError, OSError):  # Pillow built without FreeType
        return ImageFont.load_default()


def render_png(request: GenerationRequest, path: str, size: tuple[int, int]) -> None:
    width, height = size
    background = seed_color(request.seed)
    luminance = 0.2126 * background[0] + 0.7152 * background[1] + 0.0722 * background[2]
    ink = (20, 22, 26) if luminance > 140 else (240, 242, 245)
    image = Image.new("RGB", size, background)
    draw = ImageDraw.Draw(image)
    margin = max(24, width // 24)
    draw.rectangle(
        (margin // 2, margin // 2, width - margin // 2, height - margin // 2), outline=ink, width=2
    )
    draw.text((margin, margin), "MOCK ENGINE", fill=ink, font=_font(max(14, width // 40)))
    body_font = _font(max(18, width // 22))
    chars_per_line = max(12, int(width / (body_font.size * 0.55)))  # type: ignore[union-attr]
    lines = textwrap.wrap(request.prompt or "(empty prompt)", chars_per_line)[:8]
    line_height = int(body_font.size * 1.3)  # type: ignore[union-attr]
    top = (height - line_height * len(lines)) // 2
    for index, line in enumerate(lines):
        draw.text((margin, top + index * line_height), line, fill=ink, font=body_font)
    footer = f"seed {request.seed} · {width}x{height}"
    draw.text(
        (margin, height - margin - max(14, width // 40)),
        footer,
        fill=ink,
        font=_font(max(14, width // 40)),
    )
    image.save(path, format="PNG", optimize=False)


def render_mp4(
    request: GenerationRequest, path: str, size: tuple[int, int], duration: float, tone: bool
) -> None:
    width, height = size
    args = [
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={width}x{height}:rate=24:duration={duration:g}",
    ]
    if tone:
        frequency = 220 + (request.seed % 6) * 110
        sine = f"sine=frequency={frequency}:sample_rate=48000:duration={duration:g}"
        args += ["-f", "lavfi", "-i", sine]
    args += ["-vf", f"hue=h={seed_hue_degrees(request.seed)}"]
    if has_encoder("libx264"):
        args += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"]
    else:
        args += ["-c:v", "mpeg4", "-q:v", "5", "-pix_fmt", "yuv420p"]
    if tone:
        args += ["-c:a", "aac", "-b:a", "96k", "-shortest"]
    args += ["-movflags", "+faststart", path]
    run_ffmpeg(args, timeout=120)


class MockProvider:
    id = "mock"

    async def generate(self, request: GenerationRequest, ctx: JobContext) -> GenerationOutput:
        settings = ctx.services.settings.get()
        params: dict[str, Any] = request.params
        latency = float(params.get("latency_seconds", settings.mock_latency_seconds))
        fail = bool(params.get("force_failure")) or (
            settings.mock_failure_rate > 0 and random.random() < settings.mock_failure_rate  # noqa: S311
        )
        steps = max(1, int(latency * 10))
        for step in range(steps):
            await asyncio.sleep(latency / steps)
            ctx.progress(0.9 * (step + 1) / steps, "Rendering")
        if fail:
            raise JobError(
                "mock_failure",
                "The mock engine failed this generation on purpose (failure rate or "
                "force_failure). Retry to run it again.",
            )
        aspect = str(params.get("aspect", "16:9" if request.mode == "video" else "1:1"))
        media = ctx.services.media
        if request.mode == "image":
            size = IMAGE_SIZES.get(aspect, IMAGE_SIZES["1:1"])
            out = media.temp_path(".png")
            await asyncio.to_thread(render_png, request, str(out), size)
            return GenerationOutput(path=out, ext="png", mime="image/png", kind="image")
        if not ffmpeg_path():
            raise JobError(
                "ffmpeg_missing",
                "Mock video needs ffmpeg. Install ffmpeg and make sure it is on PATH.",
            )
        size = VIDEO_SIZES.get(aspect, VIDEO_SIZES["16:9"])
        duration = min(30.0, max(0.5, float(params.get("duration", 3))))
        tone = bool(params.get("audio", True))
        out = media.temp_path(".mp4")
        try:
            await asyncio.to_thread(render_mp4, request, str(out), size, duration, tone)
        except MediaToolError as exc:
            out.unlink(missing_ok=True)
            raise JobError("ffmpeg_failed", str(exc)) from exc
        return GenerationOutput(path=out, ext="mp4", mime="video/mp4", kind="video")
