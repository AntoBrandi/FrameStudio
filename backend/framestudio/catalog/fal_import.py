"""Catalog entries read from fal.ai (plan step M2.10): models, parameters and
prices come from fal's platform API instead of being written by hand.

``GET api.fal.ai/v1/models?endpoint_id=…&expand=openapi-3.0`` gives each
model's metadata and OpenAPI schema; ``GET …/v1/models/pricing`` its unit
price. The endpoint's input schema becomes parameters (enums, numbers,
switches) and a map from FrameStudio's inputs (prompt, start and end frames,
references, seed) to the endpoint's field names. Entries are written to the
user's catalog folder, so they survive restarts and can be read or removed.
"""

from __future__ import annotations

import re
from datetime import date
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import BaseModel, Field, ValidationError

from framestudio.catalog import ModelSpec

if TYPE_CHECKING:
    from framestudio.services import Services

PLATFORM = "https://api.fal.ai/v1"
CATEGORIES: dict[str, tuple[str, list[str]]] = {
    "text-to-image": ("image", ["text_to_image"]),
    "image-to-image": ("image", ["image_edit"]),
    "text-to-video": ("video", ["text_to_video"]),
    "image-to-video": ("video", ["image_to_video"]),
}
#: Endpoint field names for FrameStudio's inputs, most usual first.
FIELDS: dict[str, tuple[str, ...]] = {
    "prompt": ("prompt",),
    "start_frame": (
        "image_url",
        "start_image_url",
        "first_frame_url",
        "first_frame_image_url",
        "input_image_url",
    ),
    "end_frame": (
        "end_image_url",
        "tail_image_url",
        "last_frame_url",
        "last_frame_image_url",
        "end_frame_url",
    ),
    "references": ("image_urls", "reference_image_urls", "reference_images", "input_image_urls"),
    "seed": ("seed",),
}
#: Endpoint fields that become FrameStudio's usual parameter names.
PARAMS: dict[str, tuple[str, ...]] = {
    "aspect": ("aspect_ratio",),
    "duration": ("duration",),
    "resolution": ("resolution",),
    "audio": ("generate_audio", "enable_audio", "with_audio", "audio"),
}
_SKIP = ("url", "webhook", "sync_mode", "num_images", "output_format", "negative_prompt")


class FalImportError(ValueError):
    pass


class FalModelSummary(BaseModel):
    endpoint_id: str
    name: str
    category: str | None
    description: str
    #: Whether FrameStudio can make a catalog entry from it.
    supported: bool


class FalImport(BaseModel):
    endpoint_ids: list[str] = Field(min_length=1, max_length=20)


def model_id(endpoint: str) -> str:
    """The catalog id for a fal endpoint: ``fal-ai/kling/v2`` → ``fal-kling-v2``."""
    slug = re.sub(r"[^a-z0-9]+", "-", endpoint.lower().removeprefix("fal-ai/")).strip("-")
    return f"fal-{slug}"


def _resolve(openapi: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
        return openapi.get("components", {}).get("schemas", {}).get(ref.rsplit("/", 1)[-1], {})
    return schema


def input_schema(openapi: dict[str, Any], endpoint: str) -> dict[str, Any]:
    """The endpoint's input object schema inside its OpenAPI document."""
    candidates = []
    for path, operations in (openapi.get("paths") or {}).items():
        post = (operations or {}).get("post")
        if not post or "/requests/" in path:
            continue
        body = (
            post.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
        )
        if body:
            exact = path.strip("/") == endpoint.strip("/")
            candidates.append((not exact, path, body))
    if not candidates:
        raise FalImportError(f"{endpoint}: no input schema in its OpenAPI document")
    candidates.sort()
    schema = _resolve(openapi, candidates[0][2])
    if schema.get("type", "object") != "object" or "properties" not in schema:
        raise FalImportError(f"{endpoint}: the input is not an object with properties")
    return schema


def _label(name: str, prop: dict[str, Any]) -> str:
    title = prop.get("title")
    return str(title) if title else name.replace("_", " ").capitalize()


def _param(name: str, prop: dict[str, Any]) -> dict[str, Any] | None:
    kind = prop.get("type")
    label = _label(name, prop)
    if "enum" in prop or ("anyOf" not in prop and kind == "string" and prop.get("enum")):
        values = [
            v
            for v in prop.get("enum", [])
            if isinstance(v, str | int | float) and not isinstance(v, bool)
        ]
        if not values:
            return None
        default = prop.get("default", values[0])
        return {
            "type": "enum",
            "label": label,
            "values": values,
            "default": default if default in values else values[0],
            "control": "pills" if len(values) <= 6 else "select",
        }
    if kind == "boolean":
        return {
            "type": "boolean",
            "label": label,
            "default": bool(prop.get("default", False)),
            "control": "toggle",
        }
    if kind in ("integer", "number"):
        spec: dict[str, Any] = {"type": kind, "label": label}
        for ours, theirs in (("min", "minimum"), ("max", "maximum")):
            if theirs in prop:
                spec[ours] = prop[theirs]
        if "default" in prop:
            spec["default"] = prop["default"]
        if kind == "integer":
            spec["control"] = "stepper"
        elif "min" in spec and "max" in spec:
            spec["control"] = "slider"
        else:
            return None
        return spec
    return None


def spec_from_fal(model: dict[str, Any], price: dict[str, Any] | None) -> ModelSpec:
    """A catalog entry for one fal model (metadata + OpenAPI) and its price."""
    endpoint = str(model.get("endpoint_id", ""))
    meta = model.get("metadata") or {}
    category = meta.get("category")
    if category not in CATEGORIES:
        raise FalImportError(
            f"{endpoint}: FrameStudio cannot use {category or 'this kind of'} models yet"
        )
    mode, capabilities = CATEGORIES[category]
    openapi = model.get("openapi") or {}
    if "error" in openapi or not openapi:
        raise FalImportError(f"{endpoint}: fal did not return its schema")
    schema = input_schema(openapi, endpoint)
    props: dict[str, dict[str, Any]] = {
        name: _resolve(openapi, prop) | {k: v for k, v in prop.items() if k != "$ref"}
        for name, prop in schema["properties"].items()
    }
    inputs: dict[str, str] = {}
    for ours, names in FIELDS.items():
        found = next((n for n in names if n in props), None)
        if found:
            inputs[ours] = found
    if "prompt" not in inputs:
        raise FalImportError(f"{endpoint}: it takes no prompt")
    if mode == "video" and category == "image-to-video" and "start_frame" not in inputs:
        raise FalImportError(f"{endpoint}: no start image field was found")
    params: dict[str, dict[str, Any]] = {}
    used = set(inputs.values())
    for ours, names in PARAMS.items():
        found = next((n for n in names if n in props), None)
        if found:
            param = _param(found, props[found])
            if param:
                param["label"] = {"aspect": "Aspect ratio", "audio": "Audio"}.get(
                    ours, param["label"]
                )
                if ours == "duration" and param["type"] in ("integer", "number"):
                    param["unit"] = "s"
                params[ours] = param
                inputs[ours] = found
                used.add(found)
    for name, prop in props.items():
        if name in used or any(word in name for word in _SKIP):
            continue
        param = _param(name, prop)
        if param:
            param["advanced"] = True
            params[name] = param
            inputs[name] = name
    if "end_frame" in inputs:
        capabilities = [*capabilities, "start_end_frames"]
    if "references" in inputs:
        capabilities = [*capabilities, "multi_reference"]
    if "audio" in params:
        capabilities = [*capabilities, "native_audio"]
    max_refs = (
        int(props.get(inputs.get("references", ""), {}).get("maxItems", 4))
        if "references" in inputs
        else 0
    )
    unit = str((price or {}).get("unit", "")).lower()
    usd = float((price or {}).get("unit_price", 0) or 0)
    if not price:
        raise FalImportError(f"{endpoint}: fal returned no price for it")
    pricing: dict[str, Any] = {
        "unit": "image"
        if unit.startswith("image")
        else "video_second"
        if unit.startswith("second")
        else "request",
        "usd": usd,
        "source_url": f"https://fal.ai/models/{endpoint}",
        "checked_at": date.today().isoformat(),
    }
    license_type = meta.get("license_type")
    entry = {
        "id": model_id(endpoint),
        "name": f"{meta.get('display_name') or endpoint} (fal)",
        "description": str(meta.get("description") or f"{endpoint} on fal.ai")[:300],
        "provider": "fal",
        "runtime": "api",
        "provider_model": endpoint,
        "provider_inputs": inputs,
        "modes": [mode],
        "capabilities": capabilities,
        "params": params,
        "limits": {
            "max_references": max_refs,
            "reference_kinds": ["image"] if max_refs else [],
            "max_batch": 4,
        },
        "pricing": pricing,
        "license": {
            "kind": "hosted_service",
            "name": f"fal.ai terms; model license: {license_type or 'not stated'}",
            "commercial_use": {"commercial": "yes", "research": "no"}.get(
                license_type or "", "unknown"
            ),
            "credit": endpoint.split("/")[0],
            "source_url": meta.get("model_url") or f"https://fal.ai/models/{endpoint}",
        },
        "tags": ["fal", str(category)],
        "auto_rank": 50,  # after native providers, before test doubles
        "fixtures": [{"mode": mode, "params": {}}],
    }
    try:
        return ModelSpec.model_validate(entry)
    except ValidationError as exc:
        raise FalImportError(f"{endpoint}: {exc.errors()[0]['msg']}") from exc


async def search_fal(
    services: Services, query: str, category: str | None = None
) -> list[FalModelSummary]:
    params: dict[str, Any] = {"q": query, "limit": 20, "status": "active"}
    if category:
        params["category"] = category
    async with services.outbound.client("fal", timeout=30) as client:
        response = await client.get(f"{PLATFORM}/models", params=params)
    response.raise_for_status()
    out = []
    for item in response.json().get("models", []):
        meta = item.get("metadata") or {}
        out.append(
            FalModelSummary(
                endpoint_id=item["endpoint_id"],
                name=meta.get("display_name") or item["endpoint_id"],
                category=meta.get("category"),
                description=str(meta.get("description") or "")[:200],
                supported=meta.get("category") in CATEGORIES,
            )
        )
    return out


async def import_fal(services: Services, endpoints: list[str]) -> list[ModelSpec]:
    """Read the models from fal, add them to the catalog and the user's
    catalog folder, and return them. Raises FalImportError for any that
    cannot be used (nothing is added then)."""
    key = services.connections.api_key("fal")
    headers = {"Authorization": f"Key {key}"} if key else {}
    async with services.outbound.client("fal", timeout=60, headers=headers) as client:
        models = await client.get(
            f"{PLATFORM}/models", params={"endpoint_id": endpoints, "expand": "openapi-3.0"}
        )
        prices = await client.get(f"{PLATFORM}/models/pricing", params={"endpoint_id": endpoints})
    if not models.is_success:
        raise FalImportError(f"fal could not list these models (HTTP {models.status_code}).")
    if not prices.is_success:
        raise FalImportError(
            f"fal could not price these models (HTTP {prices.status_code}); check the key."
        )
    by_price = {p["endpoint_id"]: p for p in prices.json().get("prices", [])}
    found = {m["endpoint_id"]: m for m in models.json().get("models", [])}
    missing = [e for e in endpoints if e not in found]
    if missing:
        raise FalImportError(f"fal has no model {', '.join(missing)}")
    specs = [spec_from_fal(found[e], by_price.get(e)) for e in endpoints]
    folder = services.paths.user_catalog
    folder.mkdir(parents=True, exist_ok=True)
    for spec in specs:
        data = spec.model_dump(mode="json", exclude_none=True, exclude_defaults=False)
        (folder / f"{spec.id}.yaml").write_text(
            "# Read from fal.ai's platform API by FrameStudio.\n"
            "# Remove this file to remove the model.\n"
            + yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        services.catalog.add(spec)
    services.events.publish("catalog.changed", {"added": [s.id for s in specs]})
    return specs
