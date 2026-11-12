"""Exchange the per-install token for an HttpOnly session cookie.

``GET /auth/login?token=…`` is the link the backend and the dev command print
(as Jupyter does); ``POST /api/auth/session`` backs the token form the UI shows
when it is not signed in. The desktop shell sets the cookie directly.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from framestudio.api.deps import ServicesDep
from framestudio.security import COOKIE_MAX_AGE, COOKIE_NAME, tokens_match

router = APIRouter()


class SessionCreate(BaseModel):
    token: str = Field(min_length=1, max_length=512)


def _set_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        path="/",
    )


def _safe_next(target: str) -> str:
    if not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return target


@router.get("/auth/login", include_in_schema=False)
def login(
    request: Request,
    services: ServicesDep,
    token: str = Query(default=""),
    next: str = Query(default="/"),
) -> Response:
    if not tokens_match(token, services.policy.token):
        return HTMLResponse(
            "<!doctype html><title>FrameStudio</title><p>That access link is not valid. "
            "Run <code>framestudio token --url</code> for a fresh one.</p>",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    response = RedirectResponse(_safe_next(next), status_code=status.HTTP_303_SEE_OTHER)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    _set_cookie(response, request, token)
    return response


@router.post(
    "/api/auth/session",
    tags=["auth"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses={401: {"description": "Wrong token"}},
)
def create_session(body: SessionCreate, request: Request, services: ServicesDep) -> Response:
    if not tokens_match(body.token.strip(), services.policy.token):
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _set_cookie(response, request, body.token.strip())
    return response


@router.delete("/api/auth/session", tags=["auth"], status_code=status.HTTP_204_NO_CONTENT)
def delete_session() -> Response:
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="strict")
    return response
