"""Fake jobs that ship with the product (decision D8): they exercise the
queue, progress, cancel and restart recovery without a GPU or an API key."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, ClassVar

from framestudio.jobs.base import JobContext, JobError, JobKind, PollResult


class FakeSleepJob(JobKind):
    """Local work on the GPU lane: sleeps in small steps, reporting progress."""

    kind: ClassVar[str] = "fake.sleep"
    lane = "gpu"

    def title(self, payload: dict[str, Any]) -> str:
        return f"Fake local job · {payload.get('seconds', 10):g}s"

    async def run(self, ctx: JobContext) -> dict[str, Any]:
        seconds = float(ctx.payload.get("seconds", 10))
        steps = max(1, int(seconds * 10))
        for step in range(steps):
            await asyncio.sleep(seconds / steps)
            ctx.progress((step + 1) / steps, f"Step {step + 1} of {steps}")
        if ctx.payload.get("fail"):
            raise JobError("fake_failure", "This fake job was asked to fail.")
        return {"slept": seconds}


class FakeRemoteJob(JobKind):
    """A remote task on the network lane, as a paid API would run it.

    The task ID encodes when the fake provider "accepted" the task and how long
    it takes, so progress is computed from wall-clock time and a task picks up
    where it left off after the backend restarts. ``submissions`` counts submit
    calls so tests can prove a restarted job is resumed, not resubmitted.
    """

    kind: ClassVar[str] = "fake.remote"
    lane = "network"
    remote = True
    poll_interval: ClassVar[float] = 0.25

    def __init__(self) -> None:
        self.submissions: list[str] = []
        self.cancelled: list[str] = []

    def title(self, payload: dict[str, Any]) -> str:
        return f"Fake remote task · {payload.get('seconds', 10):g}s"

    async def submit(self, ctx: JobContext) -> str:
        await asyncio.sleep(float(ctx.payload.get("submit_delay", 0.05)))
        seconds = float(ctx.payload.get("seconds", 10))
        task_id = f"fake-{time.time():.3f}-{seconds:.3f}-{uuid.uuid4().hex[:8]}"
        self.submissions.append(task_id)
        return task_id

    async def poll(self, ctx: JobContext, task_id: str) -> PollResult:
        _, started, seconds, _ = task_id.split("-", 3)
        elapsed = time.time() - float(started)
        duration = float(seconds)
        if elapsed >= duration:
            if ctx.payload.get("fail"):
                return PollResult(
                    "failed", error=JobError("fake_failure", "The fake provider failed the task.")
                )
            return PollResult("succeeded", result={"task_id": task_id, "seconds": duration})
        fraction = elapsed / duration if duration else 1.0
        return PollResult("running", progress=fraction, message="Rendering remotely")

    async def cancel_remote(self, ctx: JobContext, task_id: str) -> None:
        self.cancelled.append(task_id)
