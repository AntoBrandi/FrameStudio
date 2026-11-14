"""Seedance video through BytePlus ModelArk (plan step M2.8).

An asynchronous paid task: ``POST /contents/generations/tasks`` returns a task
ID (stored before the first poll, so a restart resumes instead of paying
twice), ``GET …/tasks/{id}`` reports ``queued``/``running``/``succeeded``/
``failed``/``cancelled``/``expired``, and the finished clip is downloaded from
``content.video_url`` (valid for 24 hours). Only a queued task can be
cancelled; BytePlus finishes a running one, and it is charged.

Images go inline as data URIs: a start frame, start and end frames, or up to
the model's limit of reference images (the three cannot be mixed).
"""

from __future__ import annotations

import asyncio
import base64
import mimetypes
import time
from pathlib import Path
from typing import Any

import httpx2

from framestudio.catalog import ModelSpec, estimate_cost
from framestudio.jobs.base import JobContext
from framestudio.llm.adapt import connection_error
from framestudio.providers.base import (
    Estimate,
    GenerationOutput,
    GenerationRequest,
    ProviderError,
    TaskStatus,
    classify_http,
)

PROVIDER = "byteplus"
BASE_URL = "https://ark.ap-southeast.bytepluses.com/api/v3"
TASKS = f"{BASE_URL}/contents/generations/tasks"
#: Seconds of rendering per second of video, for a rough progress bar.
RENDER_FACTOR = 12.0


def _error(code: str, message: str, status: int | None = None) -> ProviderError:
    """Map a ModelArk error code onto the taxonomy, with a useful message."""
    text = f"{code} {message}".lower()
    if any(
        w in text for w in ("notopen", "not open", "notactivated", "not activated", "activation")
    ):
        return ProviderError(
            "unavailable",
            "This Seedance model is not activated on your BytePlus account. Activate it in the "
            "ModelArk console (Model activation); Seedance 2.x needs a balance of at least $30.",
            provider=PROVIDER,
            code="model_not_activated",
            status=status,
        )
    if any(w in text for w in ("sensitive", "risk", "safety", "policy", "face")):
        return ProviderError(
            "content_policy",
            f"BytePlus declined this request: {message or code}",
            provider=PROVIDER,
            status=status,
        )
    if any(w in text for w in ("overdue", "balance", "quota", "insufficient")):
        return ProviderError("quota", provider=PROVIDER, status=status)
    if any(w in text for w in ("ratelimit", "rate limit", "throttl", "serveroverloaded")):
        return ProviderError("transient", provider=PROVIDER, status=status)
    if any(w in text for w in ("authentication", "unauthorized", "invalidapikey", "api key")):
        return ProviderError("auth", provider=PROVIDER, status=status)
    if "notfound" in text or "not found" in text:
        return ProviderError("not_found", message or None, provider=PROVIDER, status=status)
    if "region" in text:
        return ProviderError(
            "unavailable",
            "Seedance is not available for this BytePlus account's region.",
            provider=PROVIDER,
            status=status,
        )
    if "invalidparameter" in text or "invalid" in text:
        return ProviderError(
            "invalid_params",
            message or None,
            provider=PROVIDER,
            code="provider_invalid_params",
            status=status,
        )
    return ProviderError("unknown", message or code or None, provider=PROVIDER, status=status)


def _http_error(response: httpx2.Response) -> ProviderError:
    try:
        error = response.json().get("error") or {}
    except ValueError:
        error = {}
    if error.get("code") or error.get("message"):
        mapped = _error(
            str(error.get("code", "")), str(error.get("message", "")), response.status_code
        )
        if mapped.kind != "unknown":
            return mapped
    found = classify_http(response.status_code, response.text, response.headers.get("retry-after"))
    found.provider = PROVIDER
    return found


def _data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime.lower()};base64,{base64.b64encode(path.read_bytes()).decode()}"


class SeedanceProvider:
    id = PROVIDER
    remote = True

    def __init__(self, api_key: str, transport: httpx2.AsyncBaseTransport) -> None:
        # The transport comes from the outbound policy (framestudio.net).
        self._key = api_key
        self._transport = transport
        self._started: dict[str, float] = {}

    def _client(self, timeout: float = 60.0) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=self._transport,
            timeout=timeout,
            headers={"Authorization": f"Bearer {self._key}"},
        )

    async def estimate(
        self, spec: ModelSpec, request: GenerationRequest, batch: int = 1
    ) -> Estimate:
        cost = estimate_cost(spec, request.mode, request.params, batch)
        seconds = float(request.params.get("duration", 5)) * RENDER_FACTOR
        return Estimate(cost_usd=cost, seconds=seconds, notes="Billed only for clips that succeed.")

    def body(self, request: GenerationRequest) -> dict[str, Any]:
        """The create-task request for ``request`` (images become data URIs)."""
        content: list[dict[str, Any]] = [
            {"type": "text", "text": request.prompt or "A short film shot."}
        ]
        if request.start_frame is not None:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": _data_uri(request.start_frame)},
                    "role": "first_frame",
                }
            )
            if request.end_frame is not None:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": _data_uri(request.end_frame)},
                        "role": "last_frame",
                    }
                )
        else:
            for path in request.references:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": _data_uri(path)},
                        "role": "reference_image",
                    }
                )
        params = request.params
        body: dict[str, Any] = {
            "model": request.provider_model or request.model,
            "content": content,
            "watermark": False,
        }
        for ours, theirs in (
            ("aspect", "ratio"),
            ("resolution", "resolution"),
            ("duration", "duration"),
        ):
            if ours in params:
                body[theirs] = params[ours]
        if "audio" in params:
            body["generate_audio"] = bool(params["audio"])
        if "camera_fixed" in params:
            body["camera_fixed"] = bool(params["camera_fixed"])
        if body["model"].startswith("seedance-1"):  # only the 1.x models take a seed
            body["seed"] = request.seed
        return body

    async def submit(self, request: GenerationRequest, ctx: JobContext) -> str:
        body = await asyncio.to_thread(self.body, request)
        try:
            async with self._client() as client:
                response = await client.post(TASKS, json=body)
        except httpx2.TimeoutException as exc:
            raise ProviderError(
                "unknown",
                "BytePlus did not answer in time; the task may have been created and charged. "
                "Check the ModelArk console before trying again.",
                provider=PROVIDER,
                code="submit_timeout",
            ) from exc
        except httpx2.RequestError as exc:
            raise connection_error(PROVIDER, exc) from exc
        if not response.is_success:
            raise _http_error(response)
        task_id = str(response.json().get("id") or "")
        if not task_id:
            raise ProviderError("unknown", "BytePlus returned no task ID.", provider=PROVIDER)
        self._started[task_id] = time.monotonic()
        return task_id

    async def _task(self, task_id: str) -> dict[str, Any]:
        try:
            async with self._client() as client:
                response = await client.get(f"{TASKS}/{task_id}")
        except httpx2.RequestError as exc:
            raise connection_error(
                PROVIDER, exc, timeout=isinstance(exc, httpx2.TimeoutException)
            ) from exc
        if response.status_code == 404:
            raise ProviderError(
                "not_found", f"BytePlus does not know task {task_id}.", provider=PROVIDER
            )
        if not response.is_success:
            raise _http_error(response)
        return response.json()  # type: ignore[no-any-return]

    async def status(self, task_id: str, request: GenerationRequest, ctx: JobContext) -> TaskStatus:
        task = await self._task(task_id)
        state = task.get("status")
        if state == "queued":
            return TaskStatus("queued", progress=0.0, message="Queued at BytePlus")
        if state == "running":
            started = self._started.setdefault(task_id, time.monotonic())
            expected = float(request.params.get("duration", 5)) * RENDER_FACTOR
            progress = min(0.95, (time.monotonic() - started) / expected) if expected else None
            return TaskStatus("running", progress=progress, message="Rendering at BytePlus")
        if state == "succeeded":
            return TaskStatus("succeeded", progress=1.0)
        if state == "cancelled":
            return TaskStatus("cancelled")
        if state == "expired":
            error = ProviderError(
                "transient",
                "BytePlus gave up on the task before it ran (it expired in the queue). Retry it.",
                provider=PROVIDER,
                code="task_expired",
            )
            return TaskStatus("failed", error=error)
        failure = task.get("error") or {}
        error = _error(
            str(failure.get("code", "")), str(failure.get("message", "The task failed."))
        )
        return TaskStatus("failed", error=error)

    async def collect(
        self, task_id: str, request: GenerationRequest, ctx: JobContext
    ) -> list[GenerationOutput]:
        task = await self._task(task_id)
        url = (task.get("content") or {}).get("video_url")
        if not url:
            raise ProviderError(
                "unknown", "BytePlus finished without a video URL.", provider=PROVIDER
            )
        path = ctx.services.media.temp_path(".mp4")
        try:
            # The result lives on BytePlus storage; it is fetched without the key.
            async with (
                httpx2.AsyncClient(transport=self._transport, timeout=300) as client,
                client.stream("GET", url) as response,
            ):
                if not response.is_success:
                    raise _http_error(response)
                with path.open("wb") as handle:
                    async for chunk in response.aiter_bytes():
                        handle.write(chunk)
        except httpx2.RequestError as exc:
            path.unlink(missing_ok=True)
            raise connection_error(
                PROVIDER, exc, timeout=isinstance(exc, httpx2.TimeoutException)
            ) from exc
        return [GenerationOutput(path=path, ext="mp4", mime="video/mp4", kind="video")]

    async def cancel(self, task_id: str, request: GenerationRequest, ctx: JobContext) -> None:
        try:
            async with self._client() as client:
                response = await client.delete(f"{TASKS}/{task_id}")
        except httpx2.RequestError as exc:
            raise connection_error(PROVIDER, exc) from exc
        if response.status_code == 404:
            raise ProviderError("not_found", provider=PROVIDER)
        if not response.is_success:
            # A running task cannot be stopped: it finishes and is charged.
            raise _http_error(response)
