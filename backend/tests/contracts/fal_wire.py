"""A stand-in for fal.ai (plan step M2.10), following its documented queue:
submit to ``queue.fal.run/<endpoint>`` → ``IN_QUEUE`` → ``IN_PROGRESS`` →
``COMPLETED`` (a failure is ``COMPLETED`` with ``error``/``error_type``), the
result at ``…/requests/<id>`` with files on ``*.fal.media``, cancel by PUT.
The platform API (models with OpenAPI schemas, prices) is the offline demo's
(framestudio.connections.fake). Prompts steer it: "slow" stays queued,
"flaky" fails with a runner error, "expired" loses its result.
"""

from __future__ import annotations

import io
import json
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx2
from PIL import Image

from framestudio.connections.fake import fal_platform
from framestudio.providers.base import GenerationRequest
from framestudio.providers.mock import VIDEO_SIZES, render_mp4

MEDIA = "v3b.fal.media"


@dataclass
class FakeFal:
    key: str = "fs-test-ok-fal-0123456789"
    queued_for: float = 0.05
    running_for: float = 0.2
    requests: dict[str, dict[str, Any]] = field(default_factory=dict)
    submitted: list[dict[str, Any]] = field(default_factory=list)
    cancelled: list[str] = field(default_factory=list)

    def _state(self, entry: dict[str, Any]) -> str:
        if entry.get("cancelled"):
            return "COMPLETED"
        elapsed = time.time() - entry["created"]
        if "slow" in entry["prompt"] or elapsed < self.queued_for:
            return "IN_QUEUE"
        if elapsed < self.queued_for + self.running_for:
            return "IN_PROGRESS"
        return "COMPLETED"

    def _file(self, request_id: str) -> tuple[bytes, str]:
        entry = self.requests[request_id]
        body = entry["body"]
        if entry["endpoint"].endswith("image-to-video") or "video" in entry["endpoint"]:
            size = VIDEO_SIZES.get(str(body.get("aspect_ratio", "16:9")), VIDEO_SIZES["16:9"])
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "clip.mp4"
                render_mp4(
                    GenerationRequest(mode="video", prompt="", seed=3),
                    str(out),
                    size,
                    float(body.get("duration", 5)),
                    bool(body.get("generate_audio", True)),
                )
                return out.read_bytes(), "video/mp4"
        w, h = (int(x) for x in str(body.get("aspect_ratio", "1:1")).split(":"))
        buffer = io.BytesIO()
        Image.new("RGB", (64 * w, 64 * h), (90, 140, 200)).save(buffer, format="PNG")
        return buffer.getvalue(), "image/png"

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        url = request.url
        if url.host == MEDIA:
            request_id = url.path.rsplit("/", 1)[-1].split(".")[0]
            content, mime = self._file(request_id)
            return httpx2.Response(200, headers={"content-type": mime}, content=content)
        auth = request.headers.get("authorization", "")
        if url.host == "api.fal.ai":
            if auth and auth != f"Key {self.key}":
                return httpx2.Response(
                    401, json={"error": {"type": "authorization_error", "message": "bad key"}}
                )
            return fal_platform(request)
        if url.host != "queue.fal.run":
            return httpx2.Response(404)
        if auth != f"Key {self.key}":
            return httpx2.Response(401, json={"detail": "Invalid key"})
        path = url.path.strip("/")
        if request.method == "POST" and "/requests/" not in url.path:
            body = json.loads(request.content)
            request_id = uuid.uuid4().hex
            owner, app = path.split("/")[:2]
            self.requests[request_id] = {
                "endpoint": path,
                "body": body,
                "prompt": body.get("prompt", ""),
                "created": time.time(),
            }
            self.submitted.append({"endpoint": path, "body": body})
            base = f"https://queue.fal.run/{owner}/{app}/requests/{request_id}"
            return httpx2.Response(
                200,
                json={
                    "status": "IN_QUEUE",
                    "request_id": request_id,
                    "response_url": base,
                    "status_url": f"{base}/status",
                    "cancel_url": f"{base}/cancel",
                    "queue_position": 0,
                },
            )
        parts = path.split("/")
        if len(parts) < 4 or parts[2] != "requests" or parts[3] not in self.requests:
            return httpx2.Response(404, json={"status": "NOT_FOUND"})
        request_id = parts[3]
        entry = self.requests[request_id]
        state = self._state(entry)
        tail = parts[4] if len(parts) > 4 else ""
        if request.method == "GET" and tail == "status":
            body: dict[str, Any] = {"status": state, "request_id": request_id}
            if state == "IN_QUEUE":
                body["queue_position"] = 2
            if state == "COMPLETED" and entry.get("cancelled"):
                body |= {"error": "Cancelled by the user.", "error_type": "client_cancelled"}
            elif state == "COMPLETED" and "flaky" in entry["prompt"]:
                body |= {"error": "The runner disconnected.", "error_type": "runner_disconnected"}
            return httpx2.Response(200 if state == "COMPLETED" else 202, json=body)
        if request.method == "GET" and tail == "":
            if state != "COMPLETED":
                return httpx2.Response(400, json={"detail": "Request is still in progress"})
            if "expired" in entry["prompt"]:
                return httpx2.Response(404, json={"detail": "Result not found"})
            kind = "video" if "video" in entry["endpoint"] else "images"
            url_ = f"https://{MEDIA}/files/b/abc/{request_id}.{'mp4' if kind == 'video' else 'png'}"
            if kind == "video":
                return httpx2.Response(
                    200, json={"video": {"url": url_, "content_type": "video/mp4"}, "seed": 1}
                )
            return httpx2.Response(
                200, json={"images": [{"url": url_, "content_type": "image/png"}], "seed": 1}
            )
        if request.method == "PUT" and tail == "cancel":
            if state == "COMPLETED":
                return httpx2.Response(400, json={"status": "ALREADY_COMPLETED"})
            entry["cancelled"] = True
            self.cancelled.append(request_id)
            return httpx2.Response(200 if state == "IN_QUEUE" else 202, json={"status": "OK"})
        return httpx2.Response(405)
