"""A stand-in for the Gemini API's image models (plan step M2.7), answering
through the real ``google-genai`` SDK: the model list, and ``generateContent``
returning an image at the requested aspect ratio (or a safety refusal, or a
bad-key error). It records every request so tests can check what was sent.

Until a key is available this is what the contract suite runs against; with
``LIVE_API=1`` the same tests call the real API.
"""

from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass, field
from typing import Any

import httpx2
from PIL import Image

MODELS = [
    "gemini-3-pro-image-preview",
    "gemini-2.5-flash-image",
    "gemini-3-pro",  # a text model: not offered for images
]
SIZES = {"1K": 1024, "2K": 2048, "4K": 4096}


def _png(aspect: str, long_side: int, colour: tuple[int, int, int]) -> bytes:
    w, h = (float(x) for x in aspect.split(":"))
    size = (
        (long_side, round(long_side * h / w)) if w >= h else (round(long_side * w / h), long_side)
    )
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="PNG")
    return buffer.getvalue()


@dataclass
class FakeGemini:
    key: str = "AIza-test-key"
    #: Answer the next generateContent with this HTTP status (then go back to normal).
    fail_next: list[int] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)
    models: list[str] = field(default_factory=lambda: list(MODELS))
    #: Scale down returned images so tests stay fast (aspect is kept).
    long_side: int = 64

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        if request.headers.get("x-goog-api-key") != self.key:
            return httpx2.Response(
                400,
                json={
                    "error": {
                        "code": 400,
                        "message": "API key not valid. Please pass a valid API key.",
                        "status": "INVALID_ARGUMENT",
                    }
                },
            )
        path = request.url.path
        if request.method == "GET" and path.endswith("/models"):
            return httpx2.Response(
                200,
                json={
                    "models": [
                        {
                            "name": f"models/{name}",
                            "displayName": name,
                            "supportedGenerationMethods": ["generateContent"],
                        }
                        for name in self.models
                    ]
                },
            )
        if request.method == "POST" and path.endswith(":generateContent"):
            body = json.loads(request.content)
            model = path.rsplit("/", 1)[-1].split(":")[0]
            self.requests.append({"model": model, "body": body})
            if self.fail_next:
                status = self.fail_next.pop(0)
                return httpx2.Response(
                    status,
                    json={"error": {"code": status, "message": "busy", "status": "UNAVAILABLE"}},
                )
            parts = body["contents"][0]["parts"]
            prompt = next((p.get("text", "") for p in parts if "text" in p), "")
            if "blocked" in prompt:
                return httpx2.Response(
                    200, json={"candidates": [{"finishReason": "IMAGE_SAFETY", "index": 0}]}
                )
            config = body.get("generationConfig", {}).get("imageConfig", {})
            aspect = config.get("aspectRatio") or config.get("aspect_ratio") or "1:1"
            resolution = config.get("imageSize") or config.get("image_size") or "1K"
            references = sum(1 for p in parts if "inlineData" in p or "inline_data" in p)
            colour = (40 * references % 255, 120, 200)
            data = _png(aspect, min(self.long_side, SIZES.get(resolution, 1024)), colour)
            return httpx2.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {
                                "role": "model",
                                "parts": [
                                    {
                                        "inlineData": {
                                            "mimeType": "image/png",
                                            "data": base64.b64encode(data).decode(),
                                        }
                                    }
                                ],
                            },
                            "finishReason": "STOP",
                            "index": 0,
                        }
                    ],
                    "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 1290},
                    "modelVersion": model,
                },
            )
        return httpx2.Response(404, json={"error": {"code": 404, "message": "not found"}})
