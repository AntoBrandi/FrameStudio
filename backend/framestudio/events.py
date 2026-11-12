"""In-process event bus streamed to the UI over Server-Sent Events.

Event IDs are ``<boot>:<seq>``. A client that reconnects with ``Last-Event-ID``
gets the events it missed from a ring buffer; if the server restarted or the
buffer no longer reaches back far enough, it gets a single ``resync`` event and
refetches state over REST instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
import uuid
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

HISTORY = 1024
SUBSCRIBER_BUFFER = 2048


@dataclass(frozen=True)
class Event:
    id: str
    type: str
    data: dict[str, Any]
    at: str

    def to_sse(self) -> str:
        body = json.dumps({"type": self.type, "data": self.data, "at": self.at}, default=str)
        return f"id: {self.id}\nevent: {self.type}\ndata: {body}\n\n"


class EventBus:
    def __init__(self, history: int = HISTORY) -> None:
        self.boot = uuid.uuid4().hex[:8]
        self._seq = 0
        self._history: deque[tuple[int, Event]] = deque(maxlen=history)
        self._subscribers: set[asyncio.Queue[Event | None]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: int | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._loop_thread = threading.get_ident()

    def publish(self, type_: str, data: dict[str, Any]) -> None:
        """Publish from the event loop or from any worker thread."""
        if self._loop is None or threading.get_ident() == self._loop_thread:
            self._publish(type_, data)
        else:
            with contextlib.suppress(RuntimeError):  # loop already closed at shutdown
                self._loop.call_soon_threadsafe(self._publish, type_, data)

    def _publish(self, type_: str, data: dict[str, Any]) -> None:
        self._seq += 1
        event = Event(
            id=f"{self.boot}:{self._seq}",
            type=type_,
            data=data,
            at=datetime.now(UTC).isoformat(),
        )
        self._history.append((self._seq, event))
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A stalled client: end its stream so it reconnects and resyncs.
                self._subscribers.discard(queue)
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(None)

    def _replay(self, last_event_id: str | None) -> list[Event] | None:
        """Missed events, or ``None`` when the client must resync."""
        if not last_event_id:
            return []
        boot, _, seq_text = last_event_id.partition(":")
        if boot != self.boot or not seq_text.isdigit():
            return None
        seq = int(seq_text)
        if seq > self._seq:
            return None
        oldest = self._history[0][0] if self._history else self._seq + 1
        if seq + 1 < oldest:
            return None
        return [event for event_seq, event in self._history if event_seq > seq]

    async def subscribe(
        self, last_event_id: str | None = None, heartbeat: float = 15.0
    ) -> AsyncIterator[Event | None]:
        """Yield events; yields ``None`` as a keep-alive tick after ``heartbeat``
        seconds of silence. Ends when the subscriber falls too far behind."""
        queue: asyncio.Queue[Event | None] = asyncio.Queue(maxsize=SUBSCRIBER_BUFFER)
        missed = self._replay(last_event_id)
        if missed is None:
            self._seq += 1
            yield Event(
                id=f"{self.boot}:{self._seq}",
                type="resync",
                data={},
                at=datetime.now(UTC).isoformat(),
            )
        else:
            for event in missed:
                yield event
        self._subscribers.add(queue)
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=heartbeat)
                except TimeoutError:
                    yield None
                    continue
                if item is None:
                    return
                yield item
        finally:
            self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
