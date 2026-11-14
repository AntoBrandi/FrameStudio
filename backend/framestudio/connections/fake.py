"""A stand-in for the providers' key checks (decision D8), used by E2E tests
and offline demos (``FRAMESTUDIO_FAKE_PROVIDERS=1``): no request leaves the
machine. Keys starting ``fs-test-ok`` are accepted, ``fs-test-down`` makes the
provider look unreachable, anything else is refused."""

from __future__ import annotations

from typing import Any

import httpx2

ACCEPTED = "fs-test-ok"
DOWN = "fs-test-down"


def _key(request: httpx2.Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:]
    if auth.lower().startswith("key "):
        return auth[4:]
    return request.headers.get("x-api-key") or request.headers.get("x-goog-api-key") or ""


def _handle(request: httpx2.Request) -> httpx2.Response:
    key = _key(request)
    if key.startswith(DOWN):
        return httpx2.Response(503, json={"error": {"message": "Service unavailable"}})
    if request.url.host == "api.fal.ai" and request.url.path.startswith("/v1/models"):
        if key and not key.startswith(ACCEPTED):
            return httpx2.Response(
                401, json={"error": {"type": "authorization_error", "message": "bad key"}}
            )
        return fal_platform(request)
    if key.startswith(ACCEPTED):
        return httpx2.Response(200, json={"data": [], "models": [], "items": []})
    return httpx2.Response(401, json={"error": {"message": "Incorrect API key provided"}})


def fake_provider_transport() -> httpx2.MockTransport:
    return httpx2.MockTransport(_handle)


#: fal's platform API in the offline demo: two models with OpenAPI input
#: schemas and prices, enough to try adding models from fal end to end.
FAL_DEMO: list[dict[str, Any]] = [
    {
        "endpoint_id": "fal-ai/demo-video/image-to-video",
        "category": "image-to-video",
        "name": "Demo Video",
        "price": {"unit_price": 0.08, "unit": "seconds"},
        "input": {
            "prompt": {"type": "string"},
            "image_url": {"type": "string"},
            "end_image_url": {"type": "string"},
            "duration": {"type": "string", "enum": ["5", "10"], "default": "5"},
            "aspect_ratio": {"type": "string", "enum": ["16:9", "9:16", "1:1"], "default": "16:9"},
            "generate_audio": {"type": "boolean", "default": True},
            "seed": {"type": "integer"},
        },
    },
    {
        "endpoint_id": "fal-ai/demo-image",
        "category": "text-to-image",
        "name": "Demo Image",
        "price": {"unit_price": 0.03, "unit": "image"},
        "input": {
            "prompt": {"type": "string"},
            "aspect_ratio": {"type": "string", "enum": ["1:1", "16:9", "9:16"], "default": "1:1"},
            "guidance_scale": {"type": "number", "minimum": 1, "maximum": 20, "default": 3.5},
        },
    },
]


def fal_model(demo: dict[str, Any], *, openapi: bool) -> dict[str, Any]:
    endpoint = demo["endpoint_id"]
    model: dict[str, Any] = {
        "endpoint_id": endpoint,
        "metadata": {
            "display_name": demo["name"],
            "category": demo["category"],
            "description": f"{demo['name']}, an offline stand-in for a fal.ai model.",
            "status": "active",
            "license_type": "commercial",
            "model_url": f"https://fal.ai/models/{endpoint}",
        },
    }
    if openapi:
        model["openapi"] = {
            "openapi": "3.0.4",
            "paths": {
                f"/{endpoint}": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Input"}
                                }
                            }
                        }
                    }
                }
            },
            "components": {
                "schemas": {
                    "Input": {"type": "object", "properties": demo["input"], "required": ["prompt"]}
                }
            },
        }
    return model


def fal_platform(request: httpx2.Request) -> httpx2.Response:
    params = request.url.params
    wanted = params.get_list("endpoint_id")
    if request.url.path.endswith("/pricing"):
        prices = [
            {"endpoint_id": d["endpoint_id"], "currency": "USD", **d["price"]}
            for d in FAL_DEMO
            if d["endpoint_id"] in wanted
        ]
        if not prices and "fal-ai/flux/dev" in wanted:  # the key check
            prices = [
                {
                    "endpoint_id": "fal-ai/flux/dev",
                    "unit_price": 0.025,
                    "unit": "image",
                    "currency": "USD",
                }
            ]
        return httpx2.Response(200, json={"prices": prices, "next_cursor": None, "has_more": False})
    query = (params.get("q") or "").lower()
    openapi = "openapi-3.0" in params.get_list("expand")
    found = [
        d
        for d in FAL_DEMO
        if (d["endpoint_id"] in wanted)
        or (not wanted and (query in d["name"].lower() or query in d["endpoint_id"]))
    ]
    if params.get("category"):
        found = [d for d in found if d["category"] == params.get("category")]
    return httpx2.Response(
        200,
        json={
            "models": [fal_model(d, openapi=openapi) for d in found],
            "next_cursor": None,
            "has_more": False,
        },
    )
