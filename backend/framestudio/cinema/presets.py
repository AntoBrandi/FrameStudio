"""Saved palettes and their use (plan step M5.4): extract a palette from a
library picture, keep it as a preset, and grade a picture or clip toward a
palette as a job that adds the result to the library."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from framestudio.cinema import PaletteSpec, vocab
from framestudio.cinema import palette as pal
from framestudio.db.models import Asset, Preset
from framestudio.jobs.base import JobContext, JobError, JobKind

if TYPE_CHECKING:
    from framestudio.services import Services


class PaletteExtract(BaseModel):
    asset_id: str
    count: int = Field(default=6, ge=2, le=8)


class ExtractedPalette(BaseModel):
    swatches: list[str]
    #: Share of the picture each swatch covers.
    shares: list[float]
    words: str


class PresetCreate(BaseModel):
    kind: Literal["palette", "lighting"]
    name: str = Field(min_length=1, max_length=80)
    data: dict[str, Any]
    preview_asset_id: str | None = None


class PresetOut(BaseModel):
    id: str
    kind: str
    name: str
    data: dict[str, Any]
    preview_url: str | None


class GradeRequest(BaseModel):
    palette: PaletteSpec
    #: How far colours move toward the palette: 0 (none) to 1 (all the way).
    strength: float = Field(default=0.6, ge=0, le=1)


def _source_file(services: Services, asset: Asset) -> Path:
    path = services.media.resolve(asset.path)
    if not path.is_file():
        raise LookupError("asset file")
    return path


def extract_from_asset(services: Services, body: PaletteExtract) -> ExtractedPalette:
    """A palette from a library still (or a clip's poster frame)."""
    with services.db.session() as session:
        asset = session.get(Asset, body.asset_id)
        if asset is None or asset.deleted_at is not None:
            raise LookupError("asset")
        if asset.kind == "image":
            source = _source_file(services, asset)
        elif asset.kind == "video" and asset.poster_path:
            source = services.media.resolve(asset.poster_path)
        else:
            raise ValueError("A palette comes from a picture or a clip with a poster frame.")
    swatches, shares = pal.extract_palette(source, body.count)
    return ExtractedPalette(
        swatches=swatches,
        shares=[round(s, 3) for s in shares],
        words=pal.describe(swatches, shares),
    )


def swatches_of(palette: PaletteSpec) -> list[str]:
    if palette.swatches:
        return list(palette.swatches)
    preset = vocab().palette(palette.id) if palette.id else None
    if preset is None:
        raise ValueError("The palette has no colours.")
    return list(preset.swatches)


def preset_out(preset: Preset) -> PresetOut:
    return PresetOut(
        id=preset.id,
        kind=preset.kind,
        name=preset.name,
        data=preset.data,
        preview_url=f"/api/assets/{preset.preview_asset_id}/thumb"
        if preset.preview_asset_id
        else None,
    )


def list_presets(services: Services, kind: str | None) -> list[PresetOut]:
    with services.db.session() as session:
        query = select(Preset).order_by(Preset.created_at.desc())
        if kind:
            query = query.where(Preset.kind == kind)
        return [preset_out(p) for p in session.scalars(query)]


def create_preset(services: Services, body: PresetCreate) -> PresetOut:
    if body.kind == "palette":
        PaletteSpec.model_validate(body.data)  # a palette must be a valid one
    with services.db.session() as session:
        if body.preview_asset_id and session.get(Asset, body.preview_asset_id) is None:
            raise LookupError("asset")
        preset = Preset(
            kind=body.kind,
            name=body.name.strip(),
            data=body.data,
            preview_asset_id=body.preview_asset_id,
        )
        session.add(preset)
        session.flush()
        out = preset_out(preset)
    services.events.publish("cinema.presets", {"id": out.id})
    return out


def delete_preset(services: Services, preset_id: str) -> bool:
    with services.db.session() as session:
        preset = session.get(Preset, preset_id)
        if preset is None:
            return False
        session.delete(preset)
    services.events.publish("cinema.presets", {"id": preset_id})
    return True


async def queue_grade(services: Services, asset_id: str, body: GradeRequest) -> str:
    """Start grading; returns the job id."""
    swatches = swatches_of(body.palette)
    with services.db.session() as session:
        asset = session.get(Asset, asset_id)
        if asset is None or asset.deleted_at is not None:
            raise LookupError("asset")
        if asset.kind not in {"image", "video"}:
            raise ValueError("Only pictures and clips can be graded.")
        name = asset.original_name or asset.id
    label = body.palette.label or (
        vocab().palette(body.palette.id).label if body.palette.id else None
    )  # type: ignore[union-attr]
    job = await services.jobs.submit(
        GradeJob.kind,
        {
            "asset_id": asset_id,
            "swatches": swatches,
            "strength": body.strength,
            "palette": label or "palette",
            "title": f"Grade {name}",
        },
        title=f"Grade with {label or 'palette'}",
    )
    return job.id


class GradeJob(JobKind):
    """Grade a picture or clip toward a palette (ffmpeg lut3d) into a new asset."""

    kind: ClassVar[str] = "grade"
    lane = "cpu"

    def title(self, payload: dict[str, Any]) -> str:
        return str(payload.get("title") or "Colour grade")

    async def run(self, ctx: JobContext) -> dict[str, Any]:
        from framestudio.library.assets import Placement, ingest_file
        from framestudio.library.media import MediaError

        services = ctx.services
        payload = ctx.payload

        def load() -> tuple[Path, str, Placement, str]:
            with services.db.session() as session:
                asset = session.get(Asset, payload["asset_id"])
                if asset is None or asset.deleted_at is not None:
                    raise JobError("not_found", "The picture or clip is gone.")
                stem = Path(asset.original_name or asset.id).stem
                return (
                    _source_file(services, asset),
                    asset.kind,
                    Placement(project_id=asset.project_id, folder_id=asset.folder_id),
                    stem,
                )

        source, kind, placement, stem = await asyncio.to_thread(load)
        ctx.progress(0.1, "Grading")
        ext = "mp4" if kind == "video" else "png"
        out = services.media.temp_path(f".{ext}")
        try:
            await asyncio.to_thread(
                pal.grade,
                source,
                out,
                payload["swatches"],
                float(payload["strength"]),
                video=kind == "video",
            )
        except Exception as exc:
            out.unlink(missing_ok=True)
            raise JobError("grade_failed", f"ffmpeg could not grade it: {exc}") from exc
        ctx.progress(0.9, "Saving")
        name = f"{stem} · graded {payload.get('palette', 'palette')}.{ext}"
        try:
            asset, _ = await asyncio.to_thread(
                lambda: ingest_file(
                    services, out, name=name, provenance="generated", placement=placement
                )
            )
        except MediaError as exc:
            raise JobError("bad_output", exc.message) from exc
        return {"asset_id": asset.id}
