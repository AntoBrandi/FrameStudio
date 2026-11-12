"""M0.3: the SSE stream over a real HTTP server, and event replay."""

from __future__ import annotations

import json

import httpx2 as httpx
import pytest

from framestudio.events import EventBus

from .conftest import AUTH, LiveServer


def _parse(lines: list[str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in lines:
        key, _, value = line.partition(": ")
        fields[key] = value
    return fields


def test_sse_streams_job_events(live_server: LiveServer) -> None:
    events: list[dict[str, str]] = []
    with (
        httpx.Client(base_url=live_server.url, headers=AUTH, timeout=10) as client,
        client.stream("GET", "/api/events") as stream,
    ):
        assert stream.status_code == 200
        assert stream.headers["content-type"].startswith("text/event-stream")
        job = client.post("/api/jobs/fake", json={"seconds": 0.3}).json()
        block: list[str] = []
        for line in stream.iter_lines():
            if line:
                block.append(line)
                continue
            if block and block[0].startswith("id: "):
                event = _parse(block)
                events.append(event)
                payload = json.loads(event["data"])
                if (
                    event["event"] == "job.updated"
                    and payload["data"]["id"] == job["id"]
                    and payload["data"]["state"] == "succeeded"
                ):
                    break
            block = []
    kinds = [event["event"] for event in events]
    assert "job.updated" in kinds
    assert "job.progress" in kinds
    ids = [event["id"] for event in events]
    assert len(set(ids)) == len(ids)


def test_sse_requires_token(live_server: LiveServer) -> None:
    response = httpx.get(f"{live_server.url}/api/events", timeout=5)
    assert response.status_code == 401


async def _first(bus: EventBus, last_event_id: str | None, count: int) -> list[str]:
    seen: list[str] = []
    async for event in bus.subscribe(last_event_id, heartbeat=0.05):
        if event is None:
            break
        seen.append(event.type)
        if len(seen) == count:
            break
    return seen


@pytest.mark.anyio
async def test_replay_after_reconnect() -> None:
    bus = EventBus()
    bus.publish("a", {})
    bus.publish("b", {})
    bus.publish("c", {})
    assert await _first(bus, f"{bus.boot}:1", 2) == ["b", "c"]


@pytest.mark.anyio
async def test_resync_after_restart_or_overflow() -> None:
    bus = EventBus(history=2)
    for name in "abcd":
        bus.publish(name, {})
    assert await _first(bus, "otherboot:3", 1) == ["resync"]
    assert await _first(bus, f"{bus.boot}:1", 1) == ["resync"]
    assert await _first(bus, f"{bus.boot}:2", 2) == ["c", "d"]
