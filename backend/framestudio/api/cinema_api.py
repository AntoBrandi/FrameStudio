"""Cinema controls API (plan milestone M5): the vocabulary the pickers offer,
and the adherence table with its measuring runs (M5.1)."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from framestudio.api.deps import ServicesDep
from framestudio.cinema import Rig, Vocab, vocab
from framestudio.cinema import adherence as adh
from framestudio.cinema import presets as ps
from framestudio.cinema import previews as pv
from framestudio.cinema.compiler import PROFILES, Profile
from framestudio.cinema.lighting import describe as describe_rig

router = APIRouter(prefix="/api/cinema", tags=["cinema"])
log = logging.getLogger("framestudio.cinema")


class RunState(BaseModel):
    id: str
    model: str
    state: Literal["running", "done", "failed"]
    results: dict[str, adh.Measurement] = {}
    error: str | None = None


class Preview(BaseModel):
    image: str | None = None
    video: str | None = None


class VocabOut(Vocab):
    #: Preview artwork per family and choice, where it has been made (M5.2).
    previews: dict[str, dict[str, Preview]] = Field(default_factory=dict)


@router.get("/vocab")
def get_vocab() -> VocabOut:
    """Every choice the film, camera, move, palette and lighting pickers offer,
    with the preview artwork that exists for them."""
    previews = {
        family: {
            term: Preview(
                **{kind: f"/api/cinema/previews/{family}/{name}" for kind, name in files.items()}
            )
            for term, files in terms.items()
        }
        for family, terms in pv.installed().items()
    }
    return VocabOut(**vocab().model_dump(), previews=previews)


_PREVIEW_FILE = re.compile(r"^[a-z0-9][a-z0-9-]*\.(webp|mp4)$")


@router.get("/previews/{family}/{name}", responses={404: {"description": "Not found"}})
def get_preview(family: str, name: str) -> FileResponse:
    """A picker preview (local file shipped with the app)."""
    if family not in pv.FAMILIES or not _PREVIEW_FILE.match(name):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Preview not found")
    path = pv.PREVIEW_DIR / family / name
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Preview not found")
    media = "video/mp4" if name.endswith(".mp4") else "image/webp"
    return FileResponse(path, media_type=media, headers={"Cache-Control": "max-age=86400"})


@router.get("/profiles")
def get_profiles() -> list[Profile]:
    """How the compiler writes for each model family."""
    return list(PROFILES.values())


@router.get("/adherence")
def get_adherence(services: ServicesDep) -> list[adh.AdherenceRow]:
    """Which controls each model follows: measured, or untested."""
    return adh.table_out(services)


@router.post("/adherence/estimate")
async def estimate_adherence(body: adh.RunRequest, services: ServicesDep) -> adh.RunEstimate:
    """What measuring would cost: two generations per control."""
    try:
        return await adh.estimate_run(services, body)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None


def _runs(request: Request) -> dict[str, RunState]:
    runs: dict[str, RunState] | None = getattr(request.app.state, "adherence_runs", None)
    if runs is None:
        runs = request.app.state.adherence_runs = {}
    return runs


@router.post(
    "/adherence/runs",
    status_code=status.HTTP_202_ACCEPTED,
    responses={402: {"description": "Over the budget"}},
)
async def start_adherence_run(
    body: adh.RunRequest, services: ServicesDep, request: Request
) -> RunState:
    """Measure controls on a model with real generations (paid on hosted
    models; refused when the estimate is over ``budget_usd``)."""
    try:
        estimate = await adh.estimate_run(services, body)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    if not estimate.within_budget:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"About ${estimate.cost_usd:.2f}, over the ${body.budget_usd:.2f} budget.",
        )
    run = RunState(id=uuid.uuid4().hex, model=body.model, state="running")
    runs = _runs(request)
    runs[run.id] = run

    async def work() -> None:
        try:
            run.results = await adh.run_adherence(services, body)
            run.state = "done"
        except Exception as exc:  # reported on the run, not raised into the loop
            log.warning("adherence run on %s failed: %s", body.model, exc)
            run.state, run.error = "failed", str(exc)
        services.events.publish("cinema.adherence", {"run": run.id, "state": run.state})

    task = asyncio.get_running_loop().create_task(work())
    request.app.state.__dict__.setdefault("adherence_tasks", set()).add(task)
    return run


@router.get("/adherence/runs/{run_id}", responses={404: {"description": "Not found"}})
def get_adherence_run(run_id: str, request: Request) -> RunState:
    run = _runs(request).get(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    return run


# ---------------------------------------------------------------- palettes (M5.4)


@router.post("/palettes/extract", responses={404: {"description": "Not found"}})
async def extract_palette(body: ps.PaletteExtract, services: ServicesDep) -> ps.ExtractedPalette:
    """A palette from a library picture (or a clip's poster): swatches dark to
    light, the share of the picture each covers, and words for a prompt."""
    try:
        return await asyncio.to_thread(ps.extract_from_asset, services, body)
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Asset not found") from None
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None


@router.get("/presets")
def list_presets(
    services: ServicesDep, kind: Literal["palette", "lighting"] | None = None
) -> list[ps.PresetOut]:
    """Saved palettes and lighting rigs."""
    return ps.list_presets(services, kind)


@router.post(
    "/presets", status_code=status.HTTP_201_CREATED, responses={404: {"description": "Not found"}}
)
def create_preset(body: ps.PresetCreate, services: ServicesDep) -> ps.PresetOut:
    try:
        return ps.create_preset(services, body)
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Asset not found") from None
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None


@router.delete(
    "/presets/{preset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"description": "Not found"}},
)
def delete_preset(preset_id: str, services: ServicesDep) -> Response:
    if not ps.delete_preset(services, preset_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Preset not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class GradeQueued(BaseModel):
    job_id: str


@router.post(
    "/grade/{asset_id}",
    status_code=status.HTTP_202_ACCEPTED,
    responses={404: {"description": "Not found"}},
)
async def grade_asset(asset_id: str, body: ps.GradeRequest, services: ServicesDep) -> GradeQueued:
    """Grade a picture or clip toward a palette (a 3D LUT at ``strength``); the
    result is a new asset beside it."""
    try:
        return GradeQueued(job_id=await ps.queue_grade(services, asset_id, body))
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Asset not found") from None
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None


# ---------------------------------------------------------------- lighting (M5.5)


class RigWords(BaseModel):
    words: str


@router.post("/lighting/describe")
def describe_lighting(rig: Rig) -> RigWords:
    """The words a lighting rig becomes in a prompt."""
    return RigWords(words=describe_rig(rig))
