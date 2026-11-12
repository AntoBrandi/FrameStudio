"""What a job kind implements, and what it gets while running."""

from __future__ import annotations

from abc import ABC
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Literal

if TYPE_CHECKING:
    from framestudio.services import Services

Lane = Literal["gpu", "network"]
JobState = Literal[
    "queued", "submitting", "running", "succeeded", "failed", "cancelled", "interrupted"
]
ACTIVE_STATES: frozenset[str] = frozenset({"queued", "submitting", "running"})
TERMINAL_STATES: frozenset[str] = frozenset({"succeeded", "failed", "cancelled", "interrupted"})
RETRYABLE_STATES: frozenset[str] = frozenset({"failed", "cancelled", "interrupted"})


class JobError(Exception):
    """A failure with a stable code the UI can explain."""

    def __init__(self, code: str, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.transient = transient


@dataclass
class PollResult:
    state: Literal["running", "succeeded", "failed"]
    progress: float | None = None
    message: str | None = None
    result: dict[str, Any] | None = None
    error: JobError | None = None


@dataclass
class JobSnapshot:
    """Plain copy of a job row, safe to pass between threads."""

    id: str
    kind: str
    lane: str
    state: str
    payload: dict[str, Any]
    attempts: int
    provider_task_id: str | None
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass
class JobContext:
    job_id: str
    payload: dict[str, Any]
    attempts: int
    services: Services
    _report: Callable[[str, float, str | None], None] = field(repr=False)

    def progress(self, fraction: float, message: str | None = None) -> None:
        """Report progress in [0, 1]. Safe to call from any thread."""
        self._report(self.job_id, max(0.0, min(1.0, fraction)), message)


class JobKind(ABC):
    """A kind of work. Local kinds implement :meth:`run`; remote kinds (paid
    API tasks) implement :meth:`submit`, :meth:`poll` and :meth:`cancel_remote`."""

    kind: ClassVar[str]
    lane: ClassVar[Lane]
    remote: ClassVar[bool] = False
    poll_interval: ClassVar[float] = 2.0

    def title(self, payload: dict[str, Any]) -> str:
        return self.kind

    async def run(self, ctx: JobContext) -> dict[str, Any] | None:
        raise NotImplementedError

    async def submit(self, ctx: JobContext) -> str:
        raise NotImplementedError

    async def poll(self, ctx: JobContext, task_id: str) -> PollResult:
        raise NotImplementedError

    async def cancel_remote(self, ctx: JobContext, task_id: str) -> None:
        return None

    def on_change(self, services: Services, job: JobSnapshot) -> None:
        """Called in a worker thread after every persisted state change."""
        return None
