"""Preview artwork for the cinema pickers (plan step M5.2).

``previews.yaml`` lists, per picker family, a subject and the kind of preview
(a still, or a short clip for pace and camera moves). For every choice the
script asks a running FrameStudio for one generation per candidate: the
subject with that single control set, compiled for the preview model like any
shot. Candidates are saved as ``<family>/<id>.<n>.webp`` (and ``.mp4`` for
clips) in a staging folder; the first is installed as ``<family>/<id>.webp``,
and ``--pick`` installs another.

Everything goes through the app's own API, so connections, spending caps and
the usage ledger apply. ``--dry-run`` prices the whole set without spending;
a real run is refused when the estimate is above ``--budget``.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, Protocol

import yaml
from PIL import Image
from pydantic import BaseModel

from framestudio.cinema import CameraSpec, FilmSpec, LightingSpec, PaletteSpec, ShotSpec, vocab

MANIFEST = Path(__file__).resolve().parent / "previews.yaml"
#: Installed artwork, shipped with the app.
PREVIEW_DIR = Path(__file__).resolve().parent / "previews"
FAMILIES = ("genres", "eras", "tempos", "bodies", "lenses", "palettes", "lighting", "moves")
STILL_WIDTH = 480
CLIP_WIDTH = 320


class FamilyPlan(BaseModel):
    kind: Literal["image", "video"]
    subject: str


class Manifest(BaseModel):
    image_model: str
    video_model: str
    candidates: int
    image_params: dict[str, Any] = {}
    video_params: dict[str, Any] = {}
    families: dict[str, FamilyPlan]


class PreviewJob(BaseModel):
    family: str
    term: str
    kind: Literal["image", "video"]
    model: str
    prompt: str
    shot: ShotSpec
    params: dict[str, Any]


def load_manifest(path: Path = MANIFEST) -> Manifest:
    return Manifest.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def shot_for(family: str, term: str) -> ShotSpec:
    """The ShotSpec with only this choice set."""
    match family:
        case "genres":
            return ShotSpec(film=FilmSpec(genre=term))
        case "eras":
            return ShotSpec(film=FilmSpec(era=term))
        case "tempos":
            return ShotSpec(film=FilmSpec(tempo=term))
        case "bodies":
            return ShotSpec(camera=CameraSpec(body=term))
        case "lenses":
            return ShotSpec(camera=CameraSpec(lens=term))
        case "palettes":
            return ShotSpec(palette=PaletteSpec(id=term))
        case "lighting":
            return ShotSpec(lighting=LightingSpec(preset=term))
        case "moves":
            return ShotSpec(moves=[term])
    raise ValueError(f"unknown family '{family}'")


def terms(family: str) -> list[str]:
    words = vocab()
    items = words.palettes if family == "palettes" else getattr(words, family)
    return [t.id for t in items]


def plan(manifest: Manifest, families: list[str] | None = None) -> list[PreviewJob]:
    jobs = []
    for family in families or list(manifest.families):
        if family not in manifest.families:
            raise ValueError(f"unknown family '{family}' (have: {', '.join(manifest.families)})")
        spec = manifest.families[family]
        video = spec.kind == "video"
        for term in terms(family):
            jobs.append(
                PreviewJob(
                    family=family,
                    term=term,
                    kind=spec.kind,
                    model=manifest.video_model if video else manifest.image_model,
                    prompt=spec.subject,
                    shot=shot_for(family, term),
                    params=dict(manifest.video_params if video else manifest.image_params),
                )
            )
    return jobs


def installed(root: Path | None = None) -> dict[str, dict[str, dict[str, str]]]:
    """Installed artwork: family → choice → {"image": file, "video": file}."""
    root = root or PREVIEW_DIR
    out: dict[str, dict[str, dict[str, str]]] = {}
    for family in FAMILIES:
        folder = root / family
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            parts = path.name.split(".")
            if len(parts) != 2 or parts[1] not in {"webp", "mp4"}:
                continue  # candidates (<id>.<n>.webp) are not installed
            kind = "image" if parts[1] == "webp" else "video"
            out.setdefault(family, {}).setdefault(parts[0], {})[kind] = path.name
    return out


# ---------------------------------------------------------------- the run


class Api(Protocol):
    """What the script needs of an HTTP client signed in to FrameStudio."""

    def get(self, url: str, **kwargs: Any) -> Any: ...
    def post(self, url: str, **kwargs: Any) -> Any: ...


class PreviewRunError(RuntimeError):
    pass


def _body(job: PreviewJob, batch: int) -> dict[str, Any]:
    return {
        "mode": job.kind,
        "model": job.model,
        "prompt": job.prompt,
        "shot": job.shot.model_dump(mode="json", exclude_defaults=True),
        "params": job.params,
        "seed": 1000,
        "batch": batch,
    }


def estimate(api: Api, jobs: list[PreviewJob], candidates: int) -> tuple[float, list[str]]:
    """Total US dollars for every candidate of every job, and any problems."""
    total, problems = 0.0, []
    for job in jobs:
        response = api.post("/api/generations/estimate", json=_body(job, candidates))
        data = response.json()
        if not data.get("ok"):
            problems.append(f"{job.family}/{job.term}: {'; '.join(data.get('problems', []))}")
        total += float(data.get("cost_usd") or 0.0)
    return round(total, 4), problems


def _still(data: bytes, dest: Path) -> None:
    with Image.open(io.BytesIO(data)) as image:
        picture = image.convert("RGB")
        picture.thumbnail((STILL_WIDTH, STILL_WIDTH * 2))
        dest.parent.mkdir(parents=True, exist_ok=True)
        picture.save(dest, format="WEBP", quality=82)


def _clip(data: bytes, dest: Path) -> None:
    from framestudio.media.tools import ffmpeg_path

    ffmpeg = ffmpeg_path()
    if ffmpeg is None:
        raise PreviewRunError("ffmpeg is required to make clip previews")
    dest.parent.mkdir(parents=True, exist_ok=True)
    source = dest.with_suffix(".source.mp4")
    source.write_bytes(data)
    try:
        subprocess.run(
            [ffmpeg, "-v", "error", "-y", "-i", str(source), "-an", "-vf",
             f"scale={CLIP_WIDTH}:-2", "-c:v", "libx264", "-crf", "30", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", str(dest)],
            check=True, timeout=120,
        )  # fmt: skip
        poster = dest.with_suffix(".webp")
        subprocess.run(
            [ffmpeg, "-v", "error", "-y", "-i", str(dest), "-frames:v", "1", str(poster)],
            check=True,
            timeout=60,
        )
    finally:
        source.unlink(missing_ok=True)


def run(
    api: Api,
    jobs: list[PreviewJob],
    *,
    candidates: int,
    budget_usd: float,
    out_dir: Path = PREVIEW_DIR,
    log: Callable[[str], None] = print,
    poll_seconds: float = 2.0,
    timeout_seconds: float = 900.0,
) -> list[Path]:
    """Make every candidate, install the first of each, return what was installed."""
    total, problems = estimate(api, jobs, candidates)
    if problems:
        raise PreviewRunError("Some previews cannot be made:\n" + "\n".join(problems))
    if total > budget_usd:
        raise PreviewRunError(
            f"About ${total:.2f} for {len(jobs)} choices, over the ${budget_usd:.2f} budget."
        )
    log(f"Making {len(jobs) * candidates} previews for about ${total:.2f}.")
    made: list[Path] = []
    for job in jobs:
        response = api.post("/api/generations", json=_body(job, candidates))
        if response.status_code != 201:
            raise PreviewRunError(f"{job.family}/{job.term}: {response.text}")
        queued = response.json()
        deadline = time.monotonic() + timeout_seconds
        done: list[dict[str, Any]] = []
        while len(done) < len(queued):
            if time.monotonic() > deadline:
                raise PreviewRunError(f"{job.family}/{job.term}: timed out")
            done = [api.get(f"/api/generations/{g['id']}").json() for g in queued]
            done = [g for g in done if g["status"] in {"succeeded", "failed", "cancelled"}]
            if len(done) < len(queued):
                time.sleep(poll_seconds)
        for index, generation in enumerate(sorted(done, key=lambda g: g["batch_index"]), start=1):
            if generation["status"] != "succeeded" or not generation.get("asset"):
                log(f"  {job.family}/{job.term} #{index}: {generation['status']}")
                continue
            data = api.get(f"/api/assets/{generation['asset']['id']}/file").content
            ext = "mp4" if job.kind == "video" else "webp"
            candidate = out_dir / job.family / f"{job.term}.{index}.{ext}"
            (_clip if job.kind == "video" else _still)(data, candidate)
            if index == 1:
                made.append(pick(out_dir, job.family, job.term, 1))
        log(f"  {job.family}/{job.term}: done")
    return made


def pick(out_dir: Path, family: str, term: str, number: int) -> Path:
    """Install candidate ``number`` of a choice."""
    folder = out_dir / family
    for ext in ("webp", "mp4"):
        candidate = folder / f"{term}.{number}.{ext}"
        if candidate.exists():
            shutil.copyfile(candidate, folder / f"{term}.{ext}")
    still = folder / f"{term}.{number}.webp"
    if not still.exists():
        raise PreviewRunError(f"No candidate {number} for {family}/{term}.")
    return folder / f"{term}.webp"
