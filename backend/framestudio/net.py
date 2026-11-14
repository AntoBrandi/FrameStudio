"""Outbound network policy (plan step M2.3, decision D7).

Every call to a hosted provider goes through :meth:`OutboundPolicy.client`,
which returns an HTTP client bound to one provider. Its transport refuses any
request to a host outside that provider's allowlist (redirects included, as
each hop passes through the transport) and any request while the provider's
connection is turned off. Nothing else in the backend may open outbound
connections; a test fails if another module imports the HTTP library.

Logs name the method, host and path of each request, never headers, query
strings or bodies, so keys cannot reach them.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

import httpx2

log = logging.getLogger("framestudio.net")


def host_allowed(host: str, hosts: frozenset[str]) -> bool:
    """``host`` is listed, or falls under a listed ``*.domain`` wildcard."""
    if host in hosts:
        return True
    return any(
        entry.startswith("*.") and host.endswith(entry[1:]) and host != entry[2:] for entry in hosts
    )


class OutboundBlocked(httpx2.RequestError):
    """A request the policy refused; nothing was sent."""


class GuardedTransport(httpx2.AsyncBaseTransport):
    def __init__(
        self,
        inner: httpx2.AsyncBaseTransport,
        provider: str,
        hosts: frozenset[str],
        allowed: Callable[[], bool],
    ) -> None:
        self._inner = inner
        self._provider = provider
        self._hosts = hosts
        self._allowed = allowed

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        url = request.url
        if url.scheme != "https" or not host_allowed(url.host, self._hosts):
            log.warning("blocked %s request to %s for %s", request.method, url.host, self._provider)
            raise OutboundBlocked(
                f"{self._provider} may only reach {', '.join(sorted(self._hosts)) or 'nothing'} "
                f"over HTTPS, not {url.scheme}://{url.host}.",
                request=request,
            )
        if not self._allowed():
            raise OutboundBlocked(
                f"The {self._provider} connection is turned off in Settings › Connections.",
                request=request,
            )
        log.info(
            "outbound %s https://%s%s (%s)", request.method, url.host, url.path, self._provider
        )
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        """The inner transport is shared; :class:`OutboundPolicy` closes it."""


class OutboundPolicy:
    def __init__(
        self,
        hosts: Mapping[str, frozenset[str]],
        is_enabled: Callable[[str], bool],
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._hosts = dict(hosts)
        self._is_enabled = is_enabled
        self._transport = transport

    def hosts(self, provider: str) -> frozenset[str]:
        return self._hosts.get(provider, frozenset())

    def _inner(self) -> httpx2.AsyncBaseTransport:
        if self._transport is None:
            self._transport = httpx2.AsyncHTTPTransport(retries=0)
        return self._transport

    def transport(self, provider: str, *, require_enabled: bool = True) -> GuardedTransport:
        def allowed() -> bool:
            return not require_enabled or self._is_enabled(provider)

        return GuardedTransport(self._inner(), provider, self.hosts(provider), allowed)

    def client(
        self,
        provider: str,
        *,
        require_enabled: bool = True,
        timeout: float = 30.0,
        follow_redirects: bool = False,
        **kwargs: Any,
    ) -> httpx2.AsyncClient:
        """An HTTP client that can only reach ``provider``'s hosts.

        ``require_enabled=False`` is for checking a key before its connection
        is saved; the host allowlist still applies.
        """
        return httpx2.AsyncClient(
            transport=self.transport(provider, require_enabled=require_enabled),
            timeout=timeout,
            follow_redirects=follow_redirects,  # each hop is checked again
            **kwargs,
        )

    async def aclose(self) -> None:
        if self._transport is not None:
            await self._transport.aclose()
            self._transport = None
