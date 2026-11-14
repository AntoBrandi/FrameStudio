"""Generation provider protocol and the error taxonomy (plan step M2.2).

Three shapes of provider share one job pipeline:

* **Local** providers (``remote = False``) render on this machine inside a job
  on the GPU lane: ``generate`` returns the finished files.
* **Hosted** providers (``remote = True``) run as paid asynchronous tasks on the
  network lane: ``submit`` returns the provider's task ID (stored before the
  first poll, so a restart resumes instead of paying twice), ``status`` reports
  progress, ``collect`` downloads the results, ``cancel`` stops the task. Each
  also receives the original request. ``submit`` is never retried
  automatically: a timeout cannot tell whether the provider accepted the task,
  and a second submit could be paid twice. Status and collect are retried.

* **Hosted, one call** (``remote = True`` with ``generate``): a paid model
  that answers in a single request, on the network lane. It is retried only
  when the provider refused it before doing any work (429, 503), since a
  timeout may already have been charged.

Every provider also ``estimate``s cost and time before Generate. Failures are
:class:`ProviderError` with a kind from one taxonomy, so retries, messages and
the UI treat all providers alike.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from framestudio.jobs.base import JobContext, JobError

if TYPE_CHECKING:
    from framestudio.catalog import ModelSpec

Mode = Literal["image", "video"]
ErrorKind = Literal[
    "auth",  # the key is missing, wrong or revoked
    "quota",  # out of credit or over a plan limit
    "invalid_params",  # the provider refused the request as given
    "content_policy",  # refused for what was asked
    "transient",  # rate limits, timeouts, 5xx: worth retrying
    "not_found",  # unknown task or model
    "unavailable",  # region, activation or maintenance
    "unknown",
]
_MESSAGES: dict[str, str] = {
    "auth": "The provider refused the key. Check it in Settings › Connections.",
    "quota": "The provider account is out of credit or over its limit.",
    "invalid_params": "The provider refused these settings.",
    "content_policy": "The provider declined this request under its content policy.",
    "transient": "The provider is busy or did not answer in time.",
    "not_found": "The provider does not know this task or model.",
    "unavailable": "This model is not available on your provider account.",
    "unknown": "The provider reported an error.",
}


class ProviderError(Exception):
    """A provider failure with a kind every part of the app understands."""

    def __init__(
        self,
        kind: ErrorKind,
        message: str | None = None,
        *,
        provider: str = "",
        code: str | None = None,
        status: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        self.kind = kind
        self.message = message or _MESSAGES[kind]
        self.provider = provider
        self.code = code or f"provider_{kind}"
        self.status = status
        self.retry_after = retry_after
        super().__init__(self.message)

    @property
    def transient(self) -> bool:
        return self.kind == "transient"

    def to_job_error(self) -> JobError:
        return JobError(self.code, self.message, transient=self.transient)


def classify_http(status: int, body: str = "", retry_after: str | None = None) -> ProviderError:
    """Map an HTTP failure from a provider to the taxonomy."""
    text = body.lower()
    wait = None
    if retry_after:
        try:
            wait = float(retry_after)
        except ValueError:
            wait = None
    if status in {401, 403} or any(
        w in text
        for w in ("api key not valid", "api_key_invalid", "invalid api key", "incorrect api key")
    ):
        return ProviderError("auth", status=status)
    if status == 402 or (
        status == 429 and any(w in text for w in ("quota", "credit", "billing", "balance"))
    ):
        return ProviderError("quota", status=status)
    if status == 429 or status >= 500 or status == 408:
        return ProviderError("transient", status=status, retry_after=wait)
    if status == 404:
        return ProviderError("not_found", status=status)
    if any(
        w in text for w in ("safety", "content policy", "moderation", "sensitive", "prohibited")
    ):
        return ProviderError("content_policy", status=status)
    if status in {400, 409, 413, 415, 422}:
        return ProviderError("invalid_params", body.strip()[:300] or None, status=status)
    return ProviderError("unknown", body.strip()[:300] or None, status=status)


async def with_retries[T](
    call: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    on_retry: Callable[[int, ProviderError], None] | None = None,
) -> T:
    """Run ``call``, retrying transient provider errors with jittered backoff."""
    for attempt in range(1, attempts + 1):
        try:
            return await call()
        except ProviderError as exc:
            if not exc.transient or attempt == attempts:
                raise
            jitter = 0.8 + 0.4 * random.random()  # noqa: S311 (timing, not secrets)
            delay = exc.retry_after or base_delay * 2 ** (attempt - 1) * jitter
            if on_retry:
                on_retry(attempt, exc)
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")


@dataclass(frozen=True)
class GenerationRequest:
    mode: Mode
    prompt: str
    seed: int
    params: dict[str, Any] = field(default_factory=dict)
    #: The catalog id; ``provider_model`` is what the provider calls it.
    model: str = ""
    provider_model: str | None = None
    references: tuple[Path, ...] = ()
    #: Image to video: the clip starts on this picture (and ends on the other).
    start_frame: Path | None = None
    end_frame: Path | None = None


@dataclass(frozen=True)
class GenerationOutput:
    """A finished file in the media store's temp directory."""

    path: Path
    ext: str
    mime: str
    kind: Literal["image", "video", "audio"]


@dataclass(frozen=True)
class Estimate:
    cost_usd: float | None = None
    seconds: float | None = None
    notes: str | None = None


@dataclass(frozen=True)
class TaskStatus:
    state: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    progress: float | None = None
    message: str | None = None
    error: ProviderError | None = None


@runtime_checkable
class GenerationProvider(Protocol):
    id: str
    remote: bool

    async def estimate(
        self, spec: ModelSpec, request: GenerationRequest, batch: int = 1
    ) -> Estimate: ...


@runtime_checkable
class LocalProvider(GenerationProvider, Protocol):
    async def generate(
        self, request: GenerationRequest, ctx: JobContext
    ) -> list[GenerationOutput]: ...


@runtime_checkable
class HostedProvider(GenerationProvider, Protocol):
    """A paid hosted model that answers in one call (no task to poll), such
    as Gemini's image models. Runs on the network lane; never retried after a
    timeout, which may already have been charged."""

    async def generate(
        self, request: GenerationRequest, ctx: JobContext
    ) -> list[GenerationOutput]: ...


@runtime_checkable
class RemoteProvider(GenerationProvider, Protocol):
    async def submit(self, request: GenerationRequest, ctx: JobContext) -> str: ...

    async def status(
        self, task_id: str, request: GenerationRequest, ctx: JobContext
    ) -> TaskStatus: ...

    async def collect(
        self, task_id: str, request: GenerationRequest, ctx: JobContext
    ) -> list[GenerationOutput]: ...

    async def cancel(self, task_id: str, request: GenerationRequest, ctx: JobContext) -> None: ...
