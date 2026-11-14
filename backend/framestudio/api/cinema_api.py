"""Cinema controls API (plan milestone M5): the vocabulary the pickers offer,
and the adherence table with its measuring runs (M5.1)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from framestudio.api.deps import ServicesDep
from framestudio.cinema import Vocab, vocab
from framestudio.cinema import adherence as adh
from framestudio.cinema.compiler import PROFILES, Profile

router = APIRouter(prefix="/api/cinema", tags=["cinema"])
log = logging.getLogger("framestudio.cinema")


class RunState(BaseModel):
    id: str
    model: str
    state: Literal["running", "done", "failed"]
    results: dict[str, adh.Measurement] = {}
    error: str | None = None


@router.get("/vocab")
def get_vocab() -> Vocab:
    """Every choice the film, camera, move, palette and lighting pickers offer."""
    return vocab()


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
