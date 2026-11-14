"""A stand-in for BytePlus ModelArk's video task API (plan step M2.8),
following the documented behaviour: create → ``queued`` → ``running`` →
``succeeded`` with ``content.video_url`` on BytePlus storage; only queued
tasks can be cancelled; unknown tasks are 404. Clips are rendered with
ffmpeg at the requested length, ratio and audio, so they pass the same
media checks as real ones. Prompts steer it: "slow" never leaves the queue,
"blocked" fails on safety, "inactive" fails as a model not activated.
"""

from __future__ import annotations

import json
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx2

from framestudio.providers.base import GenerationRequest
from framestudio.providers.mock import VIDEO_SIZES, render_mp4

API = "ark.ap-southeast.bytepluses.com"
STORAGE = "ark-content-generation-ap-southeast-1.tos-ap-southeast-1.bytepluses.com"


@dataclass
class FakeBytePlus:
    key: str = "ark-test-key-0123456789"
    #: Seconds a task spends queued, then running.
    queued_for: float = 0.05
    running_for: float = 0.25
    tasks: dict[str, dict[str, Any]] = field(default_factory=dict)
    created: list[dict[str, Any]] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    downloads: list[str] = field(default_factory=list)

    def _status(self, task: dict[str, Any]) -> str:
        if task.get("final"):
            return str(task["final"])
        prompt = task["prompt"]
        elapsed = time.time() - task["created"]
        if "slow" in prompt or elapsed < self.queued_for:
            return "queued"
        if elapsed < self.queued_for + self.running_for:
            return "running"
        if "blocked" in prompt:
            return "failed"
        if "inactive" in prompt:
            return "failed"
        return "succeeded"

    def _view(self, task_id: str) -> dict[str, Any]:
        task = self.tasks[task_id]
        status = self._status(task)
        view: dict[str, Any] = {
            "id": task_id,
            "model": task["body"]["model"],
            "status": status,
            "created_at": int(task["created"]),
            "updated_at": int(time.time()),
            "error": None,
        }
        if status == "succeeded":
            view["content"] = {
                "video_url": f"https://{STORAGE}/videos/{task_id}.mp4?X-Tos-Signature=abc"
            }
            view["usage"] = {"completion_tokens": 108000, "total_tokens": 108000}
        if status == "failed":
            if "inactive" in task["prompt"]:
                view["error"] = {"code": "ModelNotOpen", "message": "The model is not activated."}
            else:
                view["error"] = {
                    "code": "OutputVideoSensitiveContentDetected",
                    "message": "The output video may contain sensitive content.",
                }
        return view

    def _render(self, task_id: str) -> bytes:
        body = self.tasks[task_id]["body"]
        aspect = str(body.get("ratio", "16:9"))
        size = VIDEO_SIZES.get(aspect, VIDEO_SIZES["16:9"])
        request = GenerationRequest(mode="video", prompt="", seed=7)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "clip.mp4"
            render_mp4(
                request,
                str(out),
                size,
                float(body.get("duration", 5)),
                bool(body.get("generate_audio", True)),
            )
            return out.read_bytes()

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        url = request.url
        if url.host == STORAGE:
            task_id = url.path.rsplit("/", 1)[-1].removesuffix(".mp4")
            if task_id not in self.tasks:
                return httpx2.Response(404)
            self.downloads.append(task_id)
            return httpx2.Response(
                200, headers={"content-type": "video/mp4"}, content=self._render(task_id)
            )
        if request.headers.get("authorization") != f"Bearer {self.key}":
            return httpx2.Response(
                401,
                json={
                    "error": {"code": "AuthenticationError", "message": "The API key is invalid."}
                },
            )
        path = url.path.removeprefix("/api/v3/contents/generations/tasks")
        if request.method == "POST" and path == "":
            body = json.loads(request.content)
            text = next((c["text"] for c in body["content"] if c["type"] == "text"), "")
            task_id = f"cgt-{uuid.uuid4().hex[:16]}"
            self.tasks[task_id] = {"body": body, "prompt": text, "created": time.time()}
            self.created.append(body)
            return httpx2.Response(200, json={"id": task_id})
        if request.method == "GET" and path == "":
            items = [self._view(t) for t in self.tasks]
            return httpx2.Response(200, json={"total": len(items), "items": items})
        task_id = path.removeprefix("/")
        if task_id not in self.tasks:
            return httpx2.Response(
                404, json={"error": {"code": "NotFound", "message": "The task was not found."}}
            )
        if request.method == "GET":
            return httpx2.Response(200, json=self._view(task_id))
        if request.method == "DELETE":
            status = self._status(self.tasks[task_id])
            if status == "running":
                return httpx2.Response(
                    400,
                    json={
                        "error": {
                            "code": "InvalidParameter.TaskRunning",
                            "message": "A running task cannot be deleted.",
                        }
                    },
                )
            self.deleted.append(task_id)
            if status == "queued":
                self.tasks[task_id]["final"] = "cancelled"
            else:
                del self.tasks[task_id]
            return httpx2.Response(200, json={})
        return httpx2.Response(405)
