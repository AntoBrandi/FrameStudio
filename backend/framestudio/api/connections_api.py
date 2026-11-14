"""Connections to hosted providers and what they cost (M2.3). Keys go in;
they never come back out."""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status

from framestudio.api.deps import ServicesDep
from framestudio.connections.service import (
    ConnectionOut,
    ConnectionUpdate,
    KeyRefusedError,
    UnknownProviderError,
)
from framestudio.usage import UsageSummary

router = APIRouter(prefix="/api", tags=["connections"])
MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _unknown(provider: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown provider '{provider}'")


@router.get("/connections")
def list_connections(services: ServicesDep) -> list[ConnectionOut]:
    return services.connections.list()


@router.get("/connections/{provider}")
def get_connection(provider: str, services: ServicesDep) -> ConnectionOut:
    try:
        return services.connections.get(provider)
    except UnknownProviderError:
        raise _unknown(provider) from None


@router.put("/connections/{provider}")
async def update_connection(
    provider: str, body: ConnectionUpdate, services: ServicesDep
) -> ConnectionOut:
    """Add or replace the key (checked with the provider first), turn the
    connection on or off, or set its monthly cap."""
    try:
        return await services.connections.update(provider, body)
    except UnknownProviderError:
        raise _unknown(provider) from None
    except KeyRefusedError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.message) from None


@router.post("/connections/{provider}/check")
async def check_connection(provider: str, services: ServicesDep) -> ConnectionOut:
    try:
        return await services.connections.check(provider)
    except UnknownProviderError:
        raise _unknown(provider) from None


@router.delete("/connections/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_connection(provider: str, services: ServicesDep) -> Response:
    """Forget the key (from the keyring or key file) and the connection.
    Usage history stays."""
    try:
        await services.connections.remove(provider)
    except UnknownProviderError:
        raise _unknown(provider) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/usage")
def get_usage(
    services: ServicesDep,
    month: Annotated[
        str | None, Query(description="YYYY-MM (UTC); this month when omitted")
    ] = None,
) -> UsageSummary:
    if month is not None and not MONTH.match(month):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "month must look like 2026-11")
    return services.usage.summary(month)
