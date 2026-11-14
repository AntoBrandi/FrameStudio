"""HTTP answers for language-model failures in the routes that use them."""

from __future__ import annotations

from fastapi import HTTPException, status

from framestudio.providers.base import ProviderError


def http_error(exc: ProviderError) -> HTTPException:
    if exc.code == "no_llm":
        return HTTPException(status.HTTP_409_CONFLICT, exc.message)
    if exc.code == "spend_cap":
        return HTTPException(status.HTTP_402_PAYMENT_REQUIRED, exc.message)
    if exc.kind == "content_policy":
        return HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "The model declined this request."
        )
    if exc.kind in {"auth", "quota", "unavailable"}:
        return HTTPException(status.HTTP_409_CONFLICT, exc.message)
    return HTTPException(status.HTTP_502_BAD_GATEWAY, exc.message)
