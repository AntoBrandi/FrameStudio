"""Generation provider interface (grows into the M2.2 protocol)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from framestudio.jobs.base import JobContext

Mode = Literal["image", "video"]


@dataclass(frozen=True)
class GenerationRequest:
    mode: Mode
    prompt: str
    seed: int
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GenerationOutput:
    """A finished file in the media store's temp directory."""

    path: Path
    ext: str
    mime: str
    kind: Literal["image", "video", "audio"]


class GenerationProvider(Protocol):
    id: str

    async def generate(self, request: GenerationRequest, ctx: JobContext) -> GenerationOutput: ...
