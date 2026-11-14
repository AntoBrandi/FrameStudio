"""The model catalog over HTTP (plan step M2.1)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

import httpx2
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from framestudio.api.deps import ServicesDep
from framestudio.catalog import (
    Capability,
    LicenseRecord,
    Limits,
    ModelSpec,
    ParamSpec,
    Pricing,
    Rule,
    Variant,
    to_json_schema,
)
from framestudio.catalog.fal_import import (
    FalImport,
    FalImportError,
    FalModelSummary,
    import_fal,
    search_fal,
)
from framestudio.generation import availability
from framestudio.router import RouteRequest, RouteResult, route

router = APIRouter(prefix="/api", tags=["models"])


class ModelOut(BaseModel):
    id: str
    name: str
    description: str
    provider: str
    runtime: Literal["local", "api"]
    modes: list[Literal["image", "video"]]
    capabilities: list[Capability]
    params: dict[str, ParamSpec]
    rules: list[Rule]
    limits: Limits
    license: LicenseRecord
    pricing: Pricing | None
    variants: list[Variant]
    tags: list[str]
    available: bool
    unavailable_reason: str | None
    #: The parameters per mode as JSON Schema, for agents and tools.
    parameter_schema: dict[str, dict[str, Any]]


def model_out(services: ServicesDep, spec: ModelSpec) -> ModelOut:
    available, reason = availability(services, spec)
    return ModelOut(
        **spec.model_dump(exclude={"fixtures", "provider_model"}),
        available=available,
        unavailable_reason=reason,
        parameter_schema={mode: to_json_schema(spec, mode) for mode in spec.modes},
    )


@router.get("/models")
def list_models(services: ServicesDep) -> list[ModelOut]:
    """Every catalog model, with whether it can run on this install right now."""
    return [model_out(services, spec) for spec in services.catalog.all()]


@router.get("/models/{model_id}", responses={404: {"description": "Not found"}})
def get_model(model_id: str, services: ServicesDep) -> ModelOut:
    spec = services.catalog.get(model_id)
    if spec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Model not found")
    return model_out(services, spec)


@router.get("/route")
def route_model(services: ServicesDep, query: Annotated[RouteRequest, Query()]) -> RouteResult:
    """What Auto would pick for these inputs, why, and every model considered."""
    return route(services, query)


@router.get("/catalog/fal/search")
async def search_fal_models(
    services: ServicesDep,
    q: Annotated[str, Query(max_length=200)] = "",
    category: str | None = None,
) -> list[FalModelSummary]:
    """Models on fal.ai matching ``q`` (needs the fal connection)."""
    try:
        return await search_fal(services, q, category)
    except httpx2.RequestError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    except httpx2.HTTPStatusError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"fal answered {exc.response.status_code}."
        ) from None


@router.post("/catalog/fal/import")
async def import_fal_models(body: FalImport, services: ServicesDep) -> list[ModelOut]:
    """Add fal models to the catalog: parameters and price read from fal."""
    try:
        specs = await import_fal(services, list(dict.fromkeys(body.endpoint_ids)))
    except FalImportError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    except httpx2.RequestError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    return [model_out(services, spec) for spec in specs]
