"""Access control for the local API (plan decision D10).

A localhost server that holds API keys and can spend money is reachable by
every web page open in the user's browser. Three checks run on every request,
before routing, as plain ASGI middleware so streamed responses are untouched:

1. **Host.** The ``Host`` header must name a loopback address, a host listed
   in ``FRAMESTUDIO_ALLOWED_HOSTS``, or (only with remote access on) an IP
   literal. This defeats DNS rebinding, where an attacker's host name
   re-resolves to 127.0.0.1 and becomes same-origin with the API.
2. **Origin.** Browser requests that carry a foreign ``Origin``, or that the
   browser labels ``Sec-Fetch-Site: cross-site``/``same-site`` without an
   allowed origin, are refused for API paths. CORS alone would only hide the
   response; the request would still run.
3. **Token.** Every ``/api`` request except a short public list needs the
   per-install token, as ``Authorization: Bearer <token>`` (CLI, MCP, scripts)
   or the ``framestudio_session`` cookie (the UI, so that ``<img>``, ``<video>``
   and ``EventSource`` requests authenticate too).

The built UI itself is public: it contains no secrets and has to load before
the user can paste a token into it.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import os
import secrets
from collections.abc import Iterable
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlsplit

from starlette.types import ASGIApp, Receive, Scope, Send

COOKIE_NAME = "framestudio_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365
TOKEN_BYTES = 32
_MIN_TOKEN_LENGTH = 32

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
#: (method, path) pairs reachable without a token.
PUBLIC_API = frozenset({("GET", "/api/health"), ("POST", "/api/auth/session")})
_GUARDED_PREFIXES = ("/api/", "/auth/")


def load_or_create_token(path: Path) -> str:
    """Return the per-install token, creating it (mode 0600) on first use."""
    try:
        existing = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        existing = ""
    if len(existing) >= _MIN_TOKEN_LENGTH:
        return existing
    return write_new_token(path)


def write_new_token(path: Path) -> str:
    token = secrets.token_urlsafe(TOKEN_BYTES)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token)
    os.replace(tmp, path)
    return token


def tokens_match(supplied: str | None, expected: str) -> bool:
    if not supplied:
        return False
    return hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


def normalize_origin(origin: str) -> str | None:
    """``scheme://host[:port]`` in lowercase, with default ports dropped."""
    try:
        parts = urlsplit(origin.strip())
        scheme = parts.scheme.lower()
        hostname = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return None
    if scheme not in {"http", "https"} or not hostname:
        return None
    if port is None or (scheme, port) in {("http", 80), ("https", 443)}:
        netloc = f"[{hostname}]" if ":" in hostname else hostname
    else:
        netloc = f"[{hostname}]:{port}" if ":" in hostname else f"{hostname}:{port}"
    return f"{scheme}://{netloc}"


def split_host_header(host: str) -> str:
    """Host name from a ``Host`` header value, without port or brackets."""
    host = host.strip().lower()
    if host.startswith("["):
        return host[1 : host.find("]")] if "]" in host else host[1:]
    if host.count(":") == 1:
        return host.split(":", 1)[0]
    return host


def _is_ip_literal(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class AccessPolicy:
    token: str
    remote_access: bool = False
    allowed_origins: frozenset[str] = field(default_factory=frozenset)
    allowed_hosts: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def build(
        cls,
        token: str,
        *,
        remote_access: bool = False,
        allowed_origins: Iterable[str] = (),
        allowed_hosts: Iterable[str] = (),
    ) -> AccessPolicy:
        origins = {o for o in (normalize_origin(x) for x in allowed_origins) if o}
        hosts = {h.strip().lower() for h in allowed_hosts if h.strip()}
        return cls(token, remote_access, frozenset(origins), frozenset(hosts))

    def host_allowed(self, host_header: str | None) -> bool:
        if not host_header:
            return False
        hostname = split_host_header(host_header)
        if hostname in LOOPBACK_HOSTS or hostname.endswith(".localhost"):
            return True
        if hostname in self.allowed_hosts:
            return True
        return self.remote_access and _is_ip_literal(hostname)

    def origin_allowed(self, origin: str, scheme: str, host_header: str) -> bool:
        normalized = normalize_origin(origin)
        if normalized is None:
            return False
        if normalized == normalize_origin(f"{scheme}://{host_header}"):
            return True
        return normalized in self.allowed_origins


def _headers(scope: Scope) -> dict[str, str]:
    return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}


def bearer_token(headers: dict[str, str]) -> str | None:
    value = headers.get("authorization", "")
    scheme, _, credential = value.partition(" ")
    if scheme.lower() == "bearer" and credential.strip():
        return credential.strip()
    return None


def cookie_token(headers: dict[str, str]) -> str | None:
    raw = headers.get("cookie")
    if not raw:
        return None
    jar = SimpleCookie()
    try:
        jar.load(raw)
    except Exception:  # malformed cookie header from some other app on localhost
        return None
    morsel = jar.get(COOKIE_NAME)
    return morsel.value if morsel else None


class AccessGuard:
    """ASGI middleware enforcing :class:`AccessPolicy` on HTTP requests."""

    def __init__(self, app: ASGIApp, policy: AccessPolicy) -> None:
        self.app = app
        self.policy = policy

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        refusal = self.check(scope)
        if refusal is None:
            await self.app(scope, receive, send)
            return
        status, code, detail = refusal
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008, "reason": code})
            return
        await _send_json(send, status, {"detail": detail, "code": code})

    def check(self, scope: Scope) -> tuple[int, str, str] | None:
        headers = _headers(scope)
        host = headers.get("host")
        if not self.policy.host_allowed(host):
            return (
                403,
                "forbidden_host",
                "Request refused: FrameStudio was addressed by an unrecognized host name. "
                "Open it via localhost or 127.0.0.1, or add the name to FRAMESTUDIO_ALLOWED_HOSTS.",
            )
        path: str = scope.get("path", "")
        if not path.startswith(_GUARDED_PREFIXES):
            return None
        origin = headers.get("origin")
        scheme = scope.get("scheme", "http")
        if origin is not None:
            if not self.policy.origin_allowed(origin, scheme, host or ""):
                return (
                    403,
                    "forbidden_origin",
                    "Request refused: it came from another website. FrameStudio only accepts "
                    "browser requests from its own interface.",
                )
        elif headers.get("sec-fetch-site") in {"cross-site", "same-site"}:
            return (
                403,
                "forbidden_origin",
                "Request refused: it came from another website.",
            )
        method = scope.get("method", "GET")
        if (method, path) in PUBLIC_API or path == "/auth/login":
            return None
        supplied = bearer_token(headers) or cookie_token(headers)
        if not tokens_match(supplied, self.policy.token):
            return (
                401,
                "auth_required",
                "Missing or wrong access token. Send 'Authorization: Bearer <token>'; "
                "run 'framestudio token' to print it.",
            )
        return None


async def _send_json(send: Send, status: int, body: dict[str, str]) -> None:
    payload = json.dumps(body).encode("utf-8")
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(payload)).encode()),
        (b"cache-control", b"no-store"),
    ]
    if status == 401:
        headers.append((b"www-authenticate", b'Bearer realm="framestudio"'))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": payload})
