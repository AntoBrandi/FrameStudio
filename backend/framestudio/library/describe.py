"""Describe image (plan step M2.6): captions and tags for imported and
generated images, so the library can be searched by what pictures show, and
name suggestions for assets.

Describing sends a downscaled copy of the image (or a video's poster frame)
to the language model chosen for captions, so it is opt-in: per project, or
for the library outside projects in Settings, or by hand from the viewer.
"""

from __future__ import annotations

import asyncio
import io
import json
from typing import TYPE_CHECKING, Any, ClassVar

from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy import select

from framestudio.db.models import Asset, Generation, Project
from framestudio.jobs.base import JobContext, JobError, JobKind
from framestudio.library.search import index_assets
from framestudio.llm.base import ChatRequest, ImagePart, Message
from framestudio.providers.base import ProviderError

if TYPE_CHECKING:
    from framestudio.services import Services

LONG_SIDE = 1024
MAX_TAGS = 12

DESCRIBE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "caption": {"type": "string", "description": "One or two plain sentences."},
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "3 to 12 short tags.",
        },
    },
    "required": ["caption", "tags"],
    "additionalProperties": False,
}
NAMES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"names": {"type": "array", "items": {"type": "string"}}},
    "required": ["names"],
    "additionalProperties": False,
}

DESCRIBE_SYSTEM = """\
You describe images for a creative studio's library so people can find them by \
searching later. Write a caption of one or two plain sentences: the subject, \
what is happening, the setting, and the look (shot size, light, colour, style). \
Then give 3 to 12 short lowercase tags people would search for. Describe only \
what is visible; do not guess names of real people."""

NAMES_SYSTEM = """\
You suggest short names for images in a creative library: 2 to 6 words, \
descriptive and specific, no file extensions, no quotes. Give three options."""


class DescribeRequest(BaseModel):
    asset_ids: list[str] = Field(min_length=1, max_length=200)


class NameSuggestions(BaseModel):
    names: list[str]


def image_for_llm(services: Services, asset: Asset) -> ImagePart:
    """A JPEG of the picture, long side at most 1024 px."""
    if asset.kind == "image":
        source = services.media.resolve(asset.path)
    elif asset.kind == "video" and asset.poster_path:
        source = services.media.resolve(asset.poster_path)
    else:
        raise JobError(
            "not_describable", "Only images and videos with a poster frame can be described."
        )
    try:
        with Image.open(source) as image:
            picture = image.convert("RGB")
            picture.thumbnail((LONG_SIDE, LONG_SIDE))
            buffer = io.BytesIO()
            picture.save(buffer, format="JPEG", quality=85)
    except OSError as exc:
        raise JobError("not_describable", f"The file could not be read: {exc}") from exc
    return ImagePart(buffer.getvalue(), "image/jpeg")


def _clean_tags(tags: Any) -> list[str]:
    seen: dict[str, None] = {}
    for tag in tags if isinstance(tags, list) else []:
        text = " ".join(str(tag).lower().strip(" #,.").split())[:40]
        if text:
            seen.setdefault(text, None)
    return list(seen)[:MAX_TAGS]


async def describe_asset(services: Services, asset_id: str) -> dict[str, Any]:
    def load() -> tuple[Asset, ImagePart]:
        with services.db.session() as session:
            asset = session.get(Asset, asset_id)
            if asset is None or asset.deleted_at is not None:
                raise JobError("not_found", "The asset is gone.")
            session.expunge(asset)
        return asset, image_for_llm(services, asset)

    _, image = await asyncio.to_thread(load)
    try:
        llm, model = services.llm_registry.for_task("captions")
        response = await llm.complete(
            ChatRequest(
                model=model,
                system=DESCRIBE_SYSTEM,
                messages=(Message.user("Describe this image.", image),),
                response_schema=DESCRIBE_SCHEMA,
                max_tokens=2000,
                effort="low",
            )
        )
        data = response.parsed()
    except ProviderError as exc:
        raise exc.to_job_error() from exc
    caption = " ".join(str(data.get("caption", "")).split())[:500]
    tags = _clean_tags(data.get("tags"))
    if not caption:
        raise JobError("llm_empty", "The model returned no caption.")

    def save() -> None:
        with services.db.session() as session:
            asset = session.get(Asset, asset_id)
            if asset is None:
                return
            asset.caption, asset.tags = caption, tags
            session.flush()
            index_assets(session, [asset_id])

    await asyncio.to_thread(save)
    services.events.publish("library.changed", {"scope": "assets", "asset_ids": [asset_id]})
    return {"asset_id": asset_id, "caption": caption, "tags": tags}


class DescribeJob(JobKind):
    kind: ClassVar[str] = "describe"
    lane = "network"

    def title(self, payload: dict[str, Any]) -> str:
        return "Describe image"

    async def run(self, ctx: JobContext) -> dict[str, Any]:
        ctx.progress(0.1, "Asking the language model")
        return await describe_asset(ctx.services, ctx.payload["asset_id"])


def wants_description(services: Services, asset_id: str) -> bool:
    """Whether a new asset should be described automatically: its project
    asks for it (or, outside projects, the library setting does), and a
    language model is connected."""
    if not services.llms:
        return False
    with services.db.session() as session:
        asset = session.get(Asset, asset_id)
        if asset is None or asset.kind not in {"image", "video"} or asset.caption:
            return False
        if asset.project_id:
            project = session.get(Project, asset.project_id)
            return bool(project and project.describe_images)
    return services.settings.get().describe_library


async def queue_descriptions(services: Services, asset_ids: list[str]) -> list[str]:
    """Queue a describe job per asset; returns the job ids."""
    jobs = []
    for asset_id in asset_ids:
        job = await services.jobs.submit("describe", {"asset_id": asset_id})
        jobs.append(job.id)
    return jobs


async def describe_if_wanted(services: Services, asset_id: str) -> None:
    if await asyncio.to_thread(wants_description, services, asset_id):
        await queue_descriptions(services, [asset_id])


async def suggest_names(services: Services, asset_id: str) -> NameSuggestions:
    """Three short names for an asset, from its caption and prompt (or the
    picture itself when it has neither)."""

    def load() -> tuple[str, ImagePart | None]:
        with services.db.session() as session:
            asset = session.get(Asset, asset_id)
            if asset is None:
                raise LookupError("asset")
            prompt = None
            if asset.source_generation_id:
                prompt = session.scalar(
                    select(Generation.prompt).where(Generation.id == asset.source_generation_id)
                )
            known = {"caption": asset.caption, "tags": asset.tags, "prompt": prompt}
            context = json.dumps({k: v for k, v in known.items() if v})
            needs_image = not asset.caption and not prompt
            session.expunge(asset)
        return context, image_for_llm(services, asset) if needs_image else None

    context, image = await asyncio.to_thread(load)
    llm, model = services.llm_registry.for_task("prompt_help")
    images = (image,) if image else ()
    text = (
        f"Suggest names for this library item. What we know: {context}"
        if context != "{}"
        else "Suggest names for this image."
    )
    response = await llm.complete(
        ChatRequest(
            model=model,
            system=NAMES_SYSTEM,
            messages=(Message.user(text, *images),),
            response_schema=NAMES_SCHEMA,
            max_tokens=1000,
            effort="low",
        )
    )
    names = [" ".join(str(n).split())[:80] for n in response.parsed().get("names", [])]
    return NameSuggestions(names=[n for n in names if n][:3])
