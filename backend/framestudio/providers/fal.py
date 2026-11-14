"""fal.ai, the aggregator (plan step M2.10): many hosted image and video
models behind one queue API, a second route when a native provider is
unavailable.

A request is submitted to ``queue.fal.run/<endpoint>`` with the endpoint's own
input fields (each catalog entry's ``provider_inputs`` maps FrameStudio's
inputs onto them), polled at ``…/requests/<id>/status`` and its output fetched
from ``…/requests/<id>``. fal keeps a result only briefly (six minutes when it
is large), so it is downloaded as soon as the request completes. Images go
inline as data URIs.

The task ID stored for a restart is ``<owner>/<app>#<request id>``: status,
result and cancel live under the endpoint's owner and app, without its path.
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

PROVIDER = "fal"
QUEUE = "https://queue.fal.run"
#: Failures fal reports that are worth trying again (the run itself broke).
_TRANSIENT = {
    "request_timeout", "startup_timeout", "runner_scheduling_failure", "runner_connection_timeout",
    "runner_disconnected", "runner_connection_refused", "runner_connection_error",
    "runner_incomplete_response", "runner_server_error",
}  # fmt: skip
_VIDEO_EXT = {"video/mp4": "mp4", "video/webm": "webm", "video/quicktime": "mov"}
_IMAGE_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


def _http_error(response: httpx2.Response) -> ProviderError:
    detail: Any = None
    try:
        body = response.json()
        detail = body.get("detail") or body.get("error")
    except ValueError:
        pass
    if isinstance(detail, list):  # validation errors: [{loc, msg}]
        detail = "; ".join(str(item.get("msg", item)) for item in detail if isinstance(item, dict))
    error = classify_http(
        response.status_code, str(detail or response.text), response.headers.get("retry-after")
    )
    error.provider = PROVIDER
    if response.status_code == 403:
        error.message = (
            "fal refused this request: the key cannot use this model, "
            "or a spending limit was reached."
        )
    return error


def _data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def _split(endpoint: str) -> tuple[str, str]:
    """``owner/app`` (for status, result and cancel) of an endpoint id."""
    parts = endpoint.split("/")
    if len(parts) < 2:
        raise ProviderError(
            "invalid_params", f"'{endpoint}' is not a fal endpoint id.", provider=PROVIDER
        )
    return parts[0], parts[1]


def find_media(output: Any) -> list[tuple[str, str | None]]:
    """``(url, content type)`` of every file in a model's output, wherever the
    endpoint puts it (``video``, ``images``, ``image``…)."""
    found: list[tuple[str, str | None]] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            url = node.get("url")
            if isinstance(url, str) and url.startswith("https://"):
                found.append((url, node.get("content_type")))
                return
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    for key in ("video", "videos", "images", "image"):
        if key in (output or {}):
            visit(output[key])
    if not found:
        visit(output)
    return found


class FalProvider:
    id = PROVIDER
    remote = True

    def __init__(self, api_key: str, transport: httpx2.AsyncBaseTransport) -> None:
        # The transport comes from the outbound policy (framestudio.net).
        self._key = api_key
        self._transport = transport
        self._started: dict[str, float] = {}

    def _client(self, timeout: float = 60.0, auth: bool = True) -> httpx2.AsyncClient:
        headers = {"Authorization": f"Key {self._key}"} if auth else {}
        return httpx2.AsyncClient(transport=self._transport, timeout=timeout, headers=headers)

    async def estimate(
        self, spec: ModelSpec, request: GenerationRequest, batch: int = 1
    ) -> Estimate:
        cost = estimate_cost(spec, request.mode, request.params, batch)
        seconds = 60.0 if request.mode == "video" else 15.0
        return Estimate(
            cost_usd=cost, seconds=seconds, notes="Price read from fal.ai when the model was added."
        )

    def body(self, request: GenerationRequest, inputs: dict[str, str]) -> dict[str, Any]:
        """The endpoint's input for ``request``, through the entry's field names."""
        body: dict[str, Any] = {}
        if "prompt" in inputs:
            body[inputs["prompt"]] = request.prompt
        if request.start_frame is not None and "start_frame" in inputs:
            body[inputs["start_frame"]] = _data_uri(request.start_frame)
        if request.end_frame is not None and "end_frame" in inputs:
            body[inputs["end_frame"]] = _data_uri(request.end_frame)
        if request.references and "references" in inputs:
            body[inputs["references"]] = [_data_uri(path) for path in request.references]
        elif request.references and "reference" in inputs:  # one input image
            body[inputs["reference"]] = _data_uri(request.references[0])
        if request.negative_prompt and "negative_prompt" in inputs:
            body[inputs["negative_prompt"]] = request.negative_prompt
        if "seed" in inputs:
            body[inputs["seed"]] = request.seed
        for name, value in request.params.items():
            if name in inputs:
                body[inputs[name]] = value
        return body

    async def submit(self, request: GenerationRequest, ctx: JobContext) -> str:
        endpoint = request.provider_model or request.model
        spec = ctx.services.catalog.get(request.model)
        inputs = spec.provider_inputs if spec else {"prompt": "prompt"}
        body = await asyncio.to_thread(self.body, request, inputs)
        try:
            async with self._client() as client:
                response = await client.post(f"{QUEUE}/{endpoint}", json=body)
        except httpx2.TimeoutException as exc:
            raise ProviderError(
                "unknown",
                "fal did not answer in time; the request may have been queued and charged.",
                provider=PROVIDER,
                code="submit_timeout",
            ) from exc
        except httpx2.RequestError as exc:
            raise connection_error(PROVIDER, exc) from exc
        if not response.is_success:
            raise _http_error(response)
        request_id = str(response.json().get("request_id") or "")
        if not request_id:
            raise ProviderError("unknown", "fal returned no request ID.", provider=PROVIDER)
        owner, app = _split(endpoint)
        task_id = f"{owner}/{app}#{request_id}"
        self._started[task_id] = time.monotonic()
        return task_id

    def _urls(self, task_id: str) -> tuple[str, str]:
        base, _, request_id = task_id.partition("#")
        if not request_id or "/" not in base:
            raise ProviderError(
                "not_found", f"'{task_id}' is not a fal request.", provider=PROVIDER
            )
        return f"{QUEUE}/{base}/requests/{request_id}", request_id

    async def status(self, task_id: str, request: GenerationRequest, ctx: JobContext) -> TaskStatus:
        url, _ = self._urls(task_id)
        try:
            async with self._client() as client:
                response = await client.get(f"{url}/status")
        except httpx2.RequestError as exc:
            raise connection_error(
                PROVIDER, exc, timeout=isinstance(exc, httpx2.TimeoutException)
            ) from exc
        if response.status_code == 404:
            raise ProviderError("not_found", "fal does not know this request.", provider=PROVIDER)
        if response.status_code not in (200, 202):
            raise _http_error(response)
        data = response.json()
        state = data.get("status")
        if state == "IN_QUEUE":
            position = data.get("queue_position")
            message = (
                f"In fal's queue (position {position})"
                if position is not None
                else "In fal's queue"
            )
            return TaskStatus("queued", progress=0.0, message=message)
        if state == "IN_PROGRESS":
            started = self._started.setdefault(task_id, time.monotonic())
            expected = 60.0 if request.mode == "video" else 15.0
            return TaskStatus(
                "running",
                progress=min(0.95, (time.monotonic() - started) / expected),
                message="Rendering at fal",
            )
        error_type = data.get("error_type")
        if data.get("error") or error_type:
            if error_type == "client_cancelled":
                return TaskStatus("cancelled")
            kind = "transient" if error_type in _TRANSIENT else "unknown"
            message = str(data.get("error") or "fal reported a failure.")
            return TaskStatus(
                "failed",
                error=ProviderError(
                    kind, message, provider=PROVIDER, code=f"fal_{error_type or 'failed'}"
                ),
            )
        return TaskStatus("succeeded", progress=1.0)

    async def collect(
        self, task_id: str, request: GenerationRequest, ctx: JobContext
    ) -> list[GenerationOutput]:
        url, _ = self._urls(task_id)
        try:
            async with self._client() as client:
                response = await client.get(url)
        except httpx2.RequestError as exc:
            raise connection_error(
                PROVIDER, exc, timeout=isinstance(exc, httpx2.TimeoutException)
            ) from exc
        if response.status_code == 404:
            raise ProviderError(
                "not_found",
                "fal no longer has this result (it keeps results for a few minutes).",
                provider=PROVIDER,
                code="fal_result_expired",
            )
        if response.status_code == 499:
            raise ProviderError(
                "unknown",
                "The request was cancelled at fal.",
                provider=PROVIDER,
                code="fal_cancelled",
            )
        if not response.is_success:
            raise _http_error(response)
        files = find_media(response.json())
        if not files:
            raise ProviderError(
                "unknown", "fal returned no file.", provider=PROVIDER, code="no_output"
            )
        outputs = []
        try:
            async with httpx2.AsyncClient(transport=self._transport, timeout=300) as client:
                for file_url, content_type in files:
                    kind = request.mode
                    guessed = content_type or mimetypes.guess_type(file_url.split("?")[0])[0] or ""
                    table = _VIDEO_EXT if kind == "video" else _IMAGE_EXT
                    ext = table.get(guessed, "mp4" if kind == "video" else "png")
                    path = ctx.services.media.temp_path(f".{ext}")
                    async with client.stream("GET", file_url) as download:
                        if not download.is_success:
                            raise _http_error(download)
                        with path.open("wb") as handle:
                            async for chunk in download.aiter_bytes():
                                handle.write(chunk)
                    mime = guessed or ("video/mp4" if kind == "video" else "image/png")
                    outputs.append(GenerationOutput(path=path, ext=ext, mime=mime, kind=kind))
        except httpx2.RequestError as exc:
            for output in outputs:
                output.path.unlink(missing_ok=True)
            raise connection_error(
                PROVIDER, exc, timeout=isinstance(exc, httpx2.TimeoutException)
            ) from exc
        return outputs

    async def cancel(self, task_id: str, request: GenerationRequest, ctx: JobContext) -> None:
        url, _ = self._urls(task_id)
        try:
            async with self._client() as client:
                response = await client.put(f"{url}/cancel")
        except httpx2.RequestError as exc:
            raise connection_error(PROVIDER, exc) from exc
        if response.status_code == 404:
            raise ProviderError("not_found", provider=PROVIDER)
        if response.status_code == 400:  # ALREADY_COMPLETED: nothing left to stop
            return
        if not response.is_success:
            raise _http_error(response)
