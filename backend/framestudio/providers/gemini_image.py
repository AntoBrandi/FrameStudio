"""Nano Banana: images from Google's Gemini image models (plan step M2.7).

Text to image and instruction edits with reference images, through the
``google-genai`` SDK and the Google connection's key. One paid call returns
the image, so this is a *hosted* provider (``generate`` on the network lane)
rather than a task to poll.

Model IDs are read from the API: a catalog entry's ``provider_model`` names a
model family (``gemini-3-pro-image``) and the newest model the account can use
in that family is picked at run time.
"""

from __future__ import annotations

import asyncio
import mimetypes
import time
from pathlib import Path
from typing import Any

import httpx2
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from framestudio.catalog import ModelSpec, estimate_cost
from framestudio.jobs.base import JobContext
from framestudio.llm.adapt import connection_error, from_status
from framestudio.providers.base import Estimate, GenerationOutput, GenerationRequest, ProviderError

PROVIDER = "google"
MODEL_LIST_TTL = 3600.0
_REFUSED = {
    "SAFETY",
    "PROHIBITED_CONTENT",
    "BLOCKLIST",
    "SPII",
    "IMAGE_SAFETY",
    "IMAGE_PROHIBITED_CONTENT",
}
_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


def pick_model(family: str, available: list[str]) -> str | None:
    """The best model id for ``family`` among ``available``: the family name
    itself, else the newest stable variant, else the newest preview."""
    if family in available:
        return family
    matches = sorted((m for m in available if m.startswith(family)), reverse=True)
    stable = [m for m in matches if "preview" not in m and "exp" not in m]
    return (stable or matches or [None])[0]


class GeminiImageProvider:
    id = PROVIDER
    remote = True

    def __init__(self, api_key: str, transport: httpx2.AsyncBaseTransport) -> None:
        # The transport comes from the outbound policy (framestudio.net).
        options = types.HttpOptions(
            api_version="v1beta",
            httpx_async_client=httpx2.AsyncClient(transport=transport, timeout=300),
        )
        self._client = genai.Client(api_key=api_key, http_options=options)
        self._models: tuple[float, list[str]] | None = None

    async def available_models(self, refresh: bool = False) -> list[str]:
        if not refresh and self._models and time.monotonic() - self._models[0] < MODEL_LIST_TTL:
            return self._models[1]
        names: list[str] = []
        try:
            pager = await self._client.aio.models.list(config={"page_size": 200})
            async for model in pager:
                name = (model.name or "").removeprefix("models/")
                if "image" in name and "generateContent" in (model.supported_actions or []):
                    names.append(name)
        except genai_errors.APIError as exc:
            raise from_status(PROVIDER, int(exc.code or 500), exc.message or "") from exc
        except httpx2.RequestError as exc:
            raise connection_error(PROVIDER, exc) from exc
        self._models = (time.monotonic(), names)
        return names

    async def resolve(self, family: str) -> str:
        model = pick_model(family, await self.available_models())
        if model is None:
            raise ProviderError(
                "unavailable",
                f"Your Google account offers no {family} model. Check that the Gemini API is "
                "enabled for this key and the model is available in your region.",
                provider=PROVIDER,
            )
        return model

    async def estimate(
        self, spec: ModelSpec, request: GenerationRequest, batch: int = 1
    ) -> Estimate:
        cost = estimate_cost(spec, request.mode, request.params, batch)
        return Estimate(
            cost_usd=cost, seconds=20.0, notes="Hosted by Google; requests run side by side."
        )

    def _contents(self, request: GenerationRequest) -> list[Any]:
        parts: list[Any] = []
        for path in request.references:
            mime = mimetypes.guess_type(path.name)[0] or "image/png"
            parts.append(types.Part.from_bytes(data=Path(path).read_bytes(), mime_type=mime))
        parts.append(types.Part(text=request.prompt or "An image."))
        return [types.Content(role="user", parts=parts)]

    def _config(self, request: GenerationRequest) -> types.GenerateContentConfig:
        image = types.ImageConfig()
        if request.params.get("aspect"):
            image.aspect_ratio = str(request.params["aspect"])
        if request.params.get("resolution"):
            image.image_size = str(request.params["resolution"])
        return types.GenerateContentConfig(response_modalities=["IMAGE"], image_config=image)

    async def generate(self, request: GenerationRequest, ctx: JobContext) -> list[GenerationOutput]:
        model = await self.resolve(request.provider_model or "gemini-3-pro-image")
        ctx.progress(0.1, "Rendering at Google")
        try:
            contents = await asyncio.to_thread(self._contents, request)
            response = await self._client.aio.models.generate_content(
                model=model, contents=contents, config=self._config(request)
            )
        except genai_errors.APIError as exc:
            raise from_status(PROVIDER, int(exc.code or 500), exc.message or "") from exc
        except httpx2.TimeoutException as exc:
            # The image may have been made and billed: say so rather than retry.
            raise ProviderError(
                "unknown",
                "Google did not answer in time; the request may still have been charged.",
                provider=PROVIDER,
                code="hosted_timeout",
            ) from exc
        except httpx2.RequestError as exc:
            raise connection_error(PROVIDER, exc) from exc
        outputs: list[GenerationOutput] = []
        for candidate in response.candidates or []:
            for part in (candidate.content.parts if candidate.content else None) or []:
                blob = part.inline_data
                if blob and blob.data and (blob.mime_type or "").startswith("image/"):
                    ext = _EXT.get(blob.mime_type or "", "png")
                    path = ctx.services.media.temp_path(f".{ext}")
                    await asyncio.to_thread(path.write_bytes, blob.data)
                    outputs.append(
                        GenerationOutput(
                            path=path, ext=ext, mime=blob.mime_type or "image/png", kind="image"
                        )
                    )
        if outputs:
            return outputs
        finish = None
        if response.candidates and response.candidates[0].finish_reason:
            finish = str(getattr(response.candidates[0].finish_reason, "value", ""))
        blocked = bool(response.prompt_feedback and response.prompt_feedback.block_reason)
        if blocked or finish in _REFUSED:
            raise ProviderError(
                "content_policy",
                "Google declined to make this image. Try rewording the prompt or other references.",
                provider=PROVIDER,
            )
        raise ProviderError(
            "unknown", "Google returned no image.", provider=PROVIDER, code="no_image"
        )
