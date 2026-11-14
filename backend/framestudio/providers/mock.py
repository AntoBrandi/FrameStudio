"""Mock engine (decision D8): a PNG showing the prompt on a seed-coloured
background, or an ffmpeg test-pattern MP4 with an optional tone. Latency and
failure rate come from settings, or per request from ``latency_seconds`` and
``force_failure`` params."""

from __future__ import annotations

import asyncio
import colorsys
import random
import textwrap
import time
import uuid
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from framestudio.catalog import ModelSpec, estimate_cost
from framestudio.jobs.base import JobContext
from framestudio.media.store import MediaStore
from framestudio.media.tools import MediaToolError, ffmpeg_path, has_encoder, run_ffmpeg
from framestudio.providers.base import (
    Estimate,
    GenerationOutput,
    GenerationRequest,
    ProviderError,
    TaskStatus,
)
from framestudio.settings import SettingsStore

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


def _draw_references(image: Image.Image, request: GenerationRequest, margin: int) -> None:
    """Reference images as a row of thumbnails, bottom right: proof they arrived."""
    width, height = image.size
    side = max(32, width // 8)
    x = width - margin - side
    for path in reversed(request.references[:4]):
        try:
            with Image.open(path) as ref:
                thumb = ref.convert("RGB")
                thumb.thumbnail((side, side))
        except OSError:
            continue
        image.paste(thumb, (x, height - margin - side - side // 2))
        x -= side + 6


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
    _draw_references(image, request, margin)
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


FAILURE_MESSAGE = (
    "The mock engine failed this generation on purpose (failure rate or force_failure). "
    "Retry to run it again."
)


def _should_fail(params: dict[str, Any], failure_rate: float) -> bool:
    if params.get("force_failure"):
        return True
    return failure_rate > 0 and random.random() < failure_rate  # noqa: S311


async def render_output(request: GenerationRequest, media: MediaStore) -> GenerationOutput:
    """Draw the placeholder still or test-pattern clip for ``request``."""
    params = request.params
    aspect = str(params.get("aspect", "16:9" if request.mode == "video" else "1:1"))
    if request.mode == "image":
        size = IMAGE_SIZES.get(aspect, IMAGE_SIZES["1:1"])
        out = media.temp_path(".png")
        await asyncio.to_thread(render_png, request, str(out), size)
        return GenerationOutput(path=out, ext="png", mime="image/png", kind="image")
    if not ffmpeg_path():
        raise ProviderError(
            "unavailable",
            "Mock video needs ffmpeg. Install ffmpeg and make sure it is on PATH.",
            code="ffmpeg_missing",
        )
    size = VIDEO_SIZES.get(aspect, VIDEO_SIZES["16:9"])
    duration = min(30.0, max(0.5, float(params.get("duration", 3))))
    tone = bool(params.get("audio", True))
    out = media.temp_path(".mp4")
    try:
        await asyncio.to_thread(render_mp4, request, str(out), size, duration, tone)
    except MediaToolError as exc:
        out.unlink(missing_ok=True)
        raise ProviderError("unknown", str(exc), code="ffmpeg_failed") from exc
    return GenerationOutput(path=out, ext="mp4", mime="video/mp4", kind="video")


class MockProvider:
    """Renders on this machine, like a local engine (GPU lane)."""

    id = "mock"
    remote = False

    def __init__(self, settings: SettingsStore | None = None) -> None:
        self._settings = settings

    async def estimate(
        self, spec: ModelSpec, request: GenerationRequest, batch: int = 1
    ) -> Estimate:
        default = self._settings.get().mock_latency_seconds if self._settings else 2.0
        latency = float(request.params.get("latency_seconds", default))
        # Local work runs one after another on the GPU lane.
        return Estimate(cost_usd=None, seconds=latency * batch)

    async def generate(self, request: GenerationRequest, ctx: JobContext) -> list[GenerationOutput]:
        settings = ctx.services.settings.get()
        params: dict[str, Any] = request.params
        latency = float(params.get("latency_seconds", settings.mock_latency_seconds))
        fail = _should_fail(params, settings.mock_failure_rate)
        steps = max(1, int(latency * 10))
        for step in range(steps):
            await asyncio.sleep(latency / steps)
            ctx.progress(0.9 * (step + 1) / steps, "Rendering")
        if fail:
            raise ProviderError("unknown", FAILURE_MESSAGE, provider=self.id, code="mock_failure")
        return [await render_output(request, ctx.services.media)]


class MockCloudProvider:
    """Behaves like a paid hosted API (network lane): submit returns a task ID,
    status reports progress, collect downloads the result, cancel stops it.

    The task ID encodes when the task started, how long it takes and whether it
    will fail, so a task picks up where it left off after the backend restarts,
    as a real provider's would.
    """

    id = "mock-cloud"
    remote = True

    def __init__(self, settings: SettingsStore | None = None) -> None:
        self.submitted: list[str] = []
        self.cancelled: set[str] = set()
        self._settings = settings

    async def estimate(
        self, spec: ModelSpec, request: GenerationRequest, batch: int = 1
    ) -> Estimate:
        cost = estimate_cost(spec, request.mode, request.params, batch)
        default = self._settings.get().mock_latency_seconds if self._settings else 2.0
        # Hosted tasks run side by side, so a batch takes about as long as one.
        return Estimate(
            cost_usd=cost, seconds=float(request.params.get("latency_seconds", default))
        )

    async def submit(self, request: GenerationRequest, ctx: JobContext) -> str:
        settings = ctx.services.settings.get()
        latency = float(request.params.get("latency_seconds", settings.mock_latency_seconds))
        fail = int(_should_fail(request.params, settings.mock_failure_rate))
        # Milliseconds, truncated: a start time rounded up would lie in the future.
        started_ms = int(time.time() * 1000)
        task_id = f"mc-{started_ms}-{latency:.3f}-{fail}-{uuid.uuid4().hex[:8]}"
        self.submitted.append(task_id)
        return task_id

    def _parse(self, task_id: str) -> tuple[float, float, bool]:
        parts = task_id.split("-")
        if len(parts) != 5 or parts[0] != "mc":
            raise ProviderError("not_found", f"Unknown task {task_id}.", provider=self.id)
        try:
            return int(parts[1]) / 1000, float(parts[2]), parts[3] == "1"
        except ValueError:
            raise ProviderError("not_found", f"Unknown task {task_id}.", provider=self.id) from None

    async def status(self, task_id: str, request: GenerationRequest, ctx: JobContext) -> TaskStatus:
        started, latency, fail = self._parse(task_id)
        if task_id in self.cancelled:
            return TaskStatus("cancelled")
        elapsed = time.time() - started
        if elapsed < latency:
            return TaskStatus(
                "running",
                progress=min(0.99, elapsed / latency) if latency else 0.0,
                message="Rendering remotely",
            )
        if fail:
            error = ProviderError("unknown", FAILURE_MESSAGE, provider=self.id, code="mock_failure")
            return TaskStatus("failed", error=error)
        return TaskStatus("succeeded", progress=1.0)

    async def collect(
        self, task_id: str, request: GenerationRequest, ctx: JobContext
    ) -> list[GenerationOutput]:
        self._parse(task_id)
        return [await render_output(request, ctx.services.media)]

    async def cancel(self, task_id: str, request: GenerationRequest, ctx: JobContext) -> None:
        self._parse(task_id)
        self.cancelled.add(task_id)


class OfflineHostedProvider:
    """A connected hosted provider in the offline demo (FRAMESTUDIO_FAKE_PROVIDERS):
    draws the mock image, with references, in one call."""

    remote = True

    def __init__(self, provider: str, settings: SettingsStore | None = None) -> None:
        self.id = provider
        self._settings = settings

    async def estimate(
        self, spec: ModelSpec, request: GenerationRequest, batch: int = 1
    ) -> Estimate:
        cost = estimate_cost(spec, request.mode, request.params, batch)
        return Estimate(cost_usd=cost, seconds=1.0, notes="Offline stand-in")

    async def generate(self, request: GenerationRequest, ctx: JobContext) -> list[GenerationOutput]:
        await asyncio.sleep(0.3)
        ctx.progress(0.5, "Rendering (offline stand-in)")
        return [await render_output(request, ctx.services.media)]


class OfflineTaskProvider(MockCloudProvider):
    """A connected task-based provider (Seedance) in the offline demo: the
    mock clip, run as a remote task, under the provider's own name."""

    def __init__(self, provider: str, settings: SettingsStore | None = None) -> None:
        super().__init__(settings)
        self.id = provider  # type: ignore[misc]
