"""Adherence (plan step M5.1): which cinema controls each model follows.

For each control there is an A/B pair: the same subject and seed, with the
control set two clearly different ways (a teal night palette against a golden
hour one). The two results are scored by a picture metric where one fits:

* palette: distance of the pixels to each palette's swatches, in CIELAB;
* lighting (silhouette against soft cross light): brightness;
* era (1920s black and white against 1950s Technicolor): colourfulness;
* moves and tempo (video): motion between the first and last frames;

or, for the rest (genre, camera body, lens, focal length, aperture), by a
vision model asked which picture shows the A choice, asked twice with the
pictures swapped (both answers must be right). A control is **followed**
when the results differ the expected way, **weak** when a metric moves only
part of the way, **ignored** when not at all, and **untested** until measured.

Built-in results ship in ``adherence.yaml``; runs made here are saved in the
data folder and take precedence. A run makes real generations: it is priced
first and refused above its budget.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import uuid
from collections.abc import Awaitable, Callable
from datetime import date
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import yaml
from PIL import Image
from pydantic import BaseModel, Field

from framestudio.cinema import (
    CONTROL_FAMILIES,
    CameraSpec,
    FilmSpec,
    LightingSpec,
    PaletteSpec,
    ShotSpec,
    vocab,
)
from framestudio.cinema.colour import hex_to_lab, to_lab

if TYPE_CHECKING:
    from framestudio.services import Services

BUILTIN = Path(__file__).resolve().parent / "adherence.yaml"
Status = Literal["followed", "weak", "ignored", "untested"]
Metric = Literal["palette", "brightness", "colourfulness", "motion", "judge"]


class Measurement(BaseModel):
    status: Status
    #: How far apart A and B came out (metric units), or the judge's share right.
    score: float | None = None
    method: Literal["metric", "judge"] | None = None
    checked_at: str | None = None
    note: str | None = None


class Case(BaseModel):
    control: str
    mode: Literal["image", "video"]
    subject: str
    a: ShotSpec
    b: ShotSpec
    metric: Metric
    #: For the judge: a question whose right answer is the A picture.
    question: str | None = None
    #: The score above which the control counts as followed (metrics).
    threshold: float = 6.0


_STREET = "A quiet street corner with a café table and a bicycle, at dusk."
_FACE = "A woman reading a letter at a table by a window."
CASES: dict[str, Case] = {
    c.control: c
    for c in [
        Case(
            control="palette", mode="image", subject=_STREET, metric="palette",
            a=ShotSpec(palette=PaletteSpec(id="teal-night")),
            b=ShotSpec(palette=PaletteSpec(id="golden-hour")),
        ),
        Case(
            control="lighting", mode="image", subject=_FACE, metric="brightness",
            a=ShotSpec(lighting=LightingSpec(preset="silhouette")),
            b=ShotSpec(lighting=LightingSpec(preset="soft-cross")),
        ),
        Case(
            control="era", mode="image", subject=_STREET, metric="colourfulness",
            a=ShotSpec(film=FilmSpec(era="1920s")),
            b=ShotSpec(film=FilmSpec(era="1950s")),
        ),
        Case(
            control="genre", mode="image", subject=_FACE, metric="judge",
            a=ShotSpec(film=FilmSpec(genre="noir")),
            b=ShotSpec(film=FilmSpec(genre="fantasy")),
            question="Which picture looks like film noir, with hard low-key light and deep "
            "shadows?",
        ),
        Case(
            control="body", mode="image", subject=_STREET, metric="judge",
            a=ShotSpec(camera=CameraSpec(body="vhs")),
            b=ShotSpec(camera=CameraSpec(body="imax")),
            question="Which picture looks like footage from a VHS camcorder?",
        ),
        Case(
            control="lens", mode="image", subject=_STREET, metric="judge",
            a=ShotSpec(camera=CameraSpec(lens="fisheye")),
            b=ShotSpec(camera=CameraSpec(lens="spherical")),
            question="Which picture shows strong fisheye barrel distortion?",
        ),
        Case(
            control="focal_length", mode="image", subject=_STREET, metric="judge",
            a=ShotSpec(camera=CameraSpec(focal_length=14)),
            b=ShotSpec(camera=CameraSpec(focal_length=200)),
            question="Which picture is the wider view, taken with the wider lens?",
        ),
        Case(
            control="aperture", mode="image", subject=_FACE, metric="judge",
            a=ShotSpec(camera=CameraSpec(aperture="f/1.4")),
            b=ShotSpec(camera=CameraSpec(aperture="f/16")),
            question="Which picture has the more blurred background (shallower depth of field)?",
        ),
        Case(
            control="moves", mode="video", subject=_STREET, metric="motion",
            a=ShotSpec(moves=["orbit"]), b=ShotSpec(moves=["static"]), threshold=4.0,
        ),
        Case(
            control="tempo", mode="video", subject=_STREET, metric="motion",
            a=ShotSpec(film=FilmSpec(tempo="frantic")), b=ShotSpec(film=FilmSpec(tempo="calm")),
            threshold=4.0,
        ),
    ]
}  # fmt: skip
assert set(CASES) == set(CONTROL_FAMILIES)


# ---------------------------------------------------------------- the table


@cache
def _builtin() -> dict[str, dict[str, Measurement]]:
    raw = yaml.safe_load(BUILTIN.read_text(encoding="utf-8")) or {}
    return {
        model: {control: Measurement.model_validate(m) for control, m in controls.items()}
        for model, controls in raw.items()
    }


def _user_file(services: Services) -> Path:
    return services.paths.root / "adherence.json"


def load_table(services: Services) -> dict[str, dict[str, Measurement]]:
    """Built-in results, overridden by runs made with this install."""
    table = {model: dict(controls) for model, controls in _builtin().items()}
    path = _user_file(services)
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        for model, controls in raw.items():
            for control, m in controls.items():
                table.setdefault(model, {})[control] = Measurement.model_validate(m)
    return table


def save_measurements(services: Services, model_id: str, found: dict[str, Measurement]) -> None:
    path = _user_file(services)
    raw = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    raw.setdefault(model_id, {}).update({c: m.model_dump(mode="json") for c, m in found.items()})
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(raw, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def ignored_controls(services: Services, model_id: str) -> set[str]:
    return {c for c, m in load_table(services).get(model_id, {}).items() if m.status == "ignored"}


class AdherenceRow(BaseModel):
    model: str
    name: str
    controls: dict[str, Measurement]


def table_out(services: Services) -> list[AdherenceRow]:
    """Every catalog model with every control (untested where unknown)."""
    table = load_table(services)
    rows = []
    for spec in services.catalog.all():
        known = table.get(spec.id, {})
        controls = {}
        for control, case in CASES.items():
            if case.mode not in spec.modes:
                continue
            controls[control] = known.get(control, Measurement(status="untested"))
        rows.append(AdherenceRow(model=spec.id, name=spec.name, controls=controls))
    return rows


# ---------------------------------------------------------------- metrics


def _pixels(path: Path) -> list[tuple[float, float, float]]:
    with Image.open(path) as image:
        small = image.convert("RGB").resize((48, 48))
        return [to_lab(p) for p in small.get_flattened_data()]  # type: ignore[arg-type]


def palette_distance(path: Path, swatches: list[str]) -> float:
    """Mean CIELAB distance from each pixel to its nearest swatch."""
    targets = [hex_to_lab(s) for s in swatches]
    total = 0.0
    pixels = _pixels(path)
    for lab in pixels:
        total += min(sum((a - b) ** 2 for a, b in zip(lab, t, strict=True)) ** 0.5 for t in targets)
    return total / len(pixels)


def brightness(path: Path) -> float:
    pixels = _pixels(path)
    return sum(p[0] for p in pixels) / len(pixels)


def colourfulness(path: Path) -> float:
    pixels = _pixels(path)
    return sum((p[1] ** 2 + p[2] ** 2) ** 0.5 for p in pixels) / len(pixels)


def _frame(clip: Path, last: bool, out: Path) -> Path:
    from framestudio.media.tools import ffmpeg_path

    ffmpeg = ffmpeg_path()
    if ffmpeg is None:
        raise RuntimeError("ffmpeg is required to measure motion")
    seek = ["-sseof", "-0.25"] if last else []
    subprocess.run(
        [ffmpeg, "-v", "error", "-y", *seek, "-i", str(clip), "-frames:v", "1", str(out)],
        check=True,
        timeout=60,
    )
    return out


def motion(path: Path) -> float:
    """Mean absolute difference (0–255) between a clip's first and last frames."""
    tmp = path.parent / f".motion-{uuid.uuid4().hex}"
    try:
        first = _frame(path, False, tmp.with_suffix(".a.png"))
        last = _frame(path, True, tmp.with_suffix(".b.png"))
        with Image.open(first) as a, Image.open(last) as b:
            pa = list(a.convert("L").resize((64, 64)).get_flattened_data())
            pb = list(b.convert("L").resize((64, 64)).get_flattened_data())
        return sum(abs(x - y) for x, y in zip(pa, pb, strict=True)) / len(pa)
    finally:
        for suffix in (".a.png", ".b.png"):
            tmp.with_suffix(suffix).unlink(missing_ok=True)


def score_metric(case: Case, a: Path, b: Path) -> float:
    """How far apart A and B came out, in the direction the control should
    push them (positive: followed)."""
    if case.metric == "palette":
        swatches_a = _swatches(case.a)
        swatches_b = _swatches(case.b)
        on_a = palette_distance(a, swatches_b) - palette_distance(a, swatches_a)
        on_b = palette_distance(b, swatches_a) - palette_distance(b, swatches_b)
        return (on_a + on_b) / 2
    if case.metric == "brightness":  # A (silhouette) darker than B
        return brightness(b) - brightness(a)
    if case.metric == "colourfulness":  # A (black and white) less colourful
        return colourfulness(b) - colourfulness(a)
    if case.metric == "motion":  # A moves more than B
        return motion(a) - motion(b)
    raise ValueError(f"{case.metric} is not a metric")


def _swatches(shot: ShotSpec) -> list[str]:
    assert shot.palette is not None
    preset = vocab().palette(shot.palette.id) if shot.palette.id else None
    return shot.palette.swatches or (preset.swatches if preset else [])


def status_for(case: Case, score: float) -> Status:
    if score >= case.threshold:
        return "followed"
    if score >= case.threshold / 2:
        return "weak"
    return "ignored"


# ---------------------------------------------------------------- runs

#: Answers which of two pictures shows the A choice: returns "1" or "2".
Judge = Callable[[str, Path, Path], Awaitable[str]]
#: Makes one side of a case: returns the result's file.
Maker = Callable[[Case, ShotSpec], Awaitable[Path]]


async def judge_case(case: Case, a: Path, b: Path, judge: Judge) -> float:
    """Share of answers that pick A, asking with A first and then second."""
    assert case.question
    first = await judge(case.question, a, b)
    second = await judge(case.question, b, a)
    return ((first.strip().startswith("1")) + (second.strip().startswith("2"))) / 2


class RunRequest(BaseModel):
    model: str
    #: Controls to measure (default: all that apply to the model).
    controls: list[str] | None = None
    #: The most the run may cost, in US dollars (hosted models).
    budget_usd: float = Field(default=1.0, ge=0, le=50)
    seed: int = Field(default=7, ge=0)


class RunEstimate(BaseModel):
    model: str
    controls: list[str]
    generations: int
    cost_usd: float
    within_budget: bool


class BudgetError(ValueError):
    pass


def cases_for(spec_modes: list[str], controls: list[str] | None) -> list[Case]:
    wanted = controls or list(CASES)
    unknown = [c for c in wanted if c not in CASES]
    if unknown:
        raise ValueError(f"unknown controls: {', '.join(unknown)}")
    return [CASES[c] for c in wanted if CASES[c].mode in spec_modes]


async def estimate_run(services: Services, body: RunRequest) -> RunEstimate:
    from framestudio.generation import GenerationCreate, estimate_generations

    spec = services.catalog.get(body.model)
    if spec is None:
        raise ValueError(f"unknown model '{body.model}'")
    cases = cases_for(list(spec.modes), body.controls)
    cost = 0.0
    for case in cases:
        request = GenerationCreate(
            mode=case.mode, model=spec.id, prompt=case.subject, seed=body.seed
        )
        estimate = await estimate_generations(services, request, services.providers)
        cost += 2 * (estimate.cost_usd or 0.0)
    return RunEstimate(
        model=spec.id,
        controls=[c.control for c in cases],
        generations=2 * len(cases),
        cost_usd=round(cost, 4),
        within_budget=cost <= body.budget_usd,
    )


async def run_adherence(
    services: Services,
    body: RunRequest,
    *,
    make: Maker | None = None,
    judge: Judge | None = None,
) -> dict[str, Measurement]:
    """Measure the controls for one model, save and return the results."""
    estimate = await estimate_run(services, body)
    if not estimate.within_budget:
        raise BudgetError(
            f"Measuring {len(estimate.controls)} controls on {body.model} would cost about "
            f"${estimate.cost_usd:.2f}, over the ${body.budget_usd:.2f} budget."
        )
    spec = services.catalog.get(body.model)
    assert spec is not None
    make = make or _pipeline_maker(services, spec.id, body.seed)
    judge = judge or _llm_judge(services)
    today = date.today().isoformat()
    found: dict[str, Measurement] = {}
    for case in cases_for(list(spec.modes), body.controls):
        if case.metric == "judge" and judge is None:
            found[case.control] = Measurement(
                status="untested",
                note="Needs a vision language model (Settings › Language models).",
            )
            continue
        a, b = await make(case, case.a), await make(case, case.b)
        if case.metric == "judge":
            assert judge is not None
            share = await judge_case(case, a, b, judge)
            # Asked twice with the pictures swapped: one right answer means the
            # judge picked the same position both times, so it could not tell.
            found[case.control] = Measurement(
                status="followed" if share == 1 else "ignored",
                score=share,
                method="judge",
                checked_at=today,
                note="The judge could not tell the two apart." if share == 0.5 else None,
            )
        else:
            score = await asyncio.to_thread(score_metric, case, a, b)
            found[case.control] = Measurement(
                status=status_for(case, score),
                score=round(score, 2),
                method="metric",
                checked_at=today,
            )
    save_measurements(services, spec.id, found)
    return found


def _pipeline_maker(services: Services, model_id: str, seed: int) -> Maker:
    """Each side as a real generation through the app's own pipeline."""

    async def make(case: Case, shot: ShotSpec) -> Path:
        from framestudio.db.models import Asset, Generation
        from framestudio.generation import GenerationCreate, create_generations

        request = GenerationCreate(
            mode=case.mode, model=model_id, prompt=case.subject, seed=seed, shot=shot
        )
        [queued] = await create_generations(services, request, services.providers)
        for _ in range(1200):  # up to ten minutes
            with services.db.session() as session:
                generation = session.get(Generation, queued.id)
                status = generation.status if generation else "failed"
                if status == "succeeded":
                    asset = (
                        session.query(Asset).filter(Asset.source_generation_id == queued.id).first()
                    )
                    if asset is None:
                        raise RuntimeError("the generation left no file")
                    return services.media.resolve(asset.path)
                if status in {"failed", "cancelled", "interrupted"}:
                    message = generation.error_message if generation else "gone"
                    raise RuntimeError(f"{case.control}: the generation {status}: {message}")
            await asyncio.sleep(0.5)
        raise TimeoutError(f"{case.control}: the generation took too long")

    return make


def _llm_judge(services: Services) -> Judge | None:
    """A vision language model as judge, if one is connected (it sees both
    pictures, so this sends them to the provider, as Describe does)."""
    from framestudio.providers.base import ProviderError

    try:
        llm, model = services.llm_registry.for_task("captions")
    except ProviderError:
        return None

    async def judge(question: str, first: Path, second: Path) -> str:
        from framestudio.llm.base import ChatRequest, ImagePart, Message

        def part(path: Path) -> ImagePart:
            import io

            with Image.open(path) as image:
                picture = image.convert("RGB")
                picture.thumbnail((768, 768))
                buffer = io.BytesIO()
                picture.save(buffer, format="JPEG", quality=88)
            return ImagePart(buffer.getvalue(), "image/jpeg")

        images = await asyncio.to_thread(lambda: (part(first), part(second)))
        response = await llm.complete(
            ChatRequest(
                model=model,
                messages=(
                    Message.user(
                        f"{question} Answer with 1 for the first picture or 2 for the second, "
                        "and nothing else.",
                        *images,
                    ),
                ),
                max_tokens=400,
                effort="low",
            )
        )
        text = response.text.strip()
        return next((c for c in text if c in "12"), "")

    return judge
