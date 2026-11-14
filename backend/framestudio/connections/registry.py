"""The hosted providers FrameStudio can connect to, what each may reach, and
how a key is checked (one free, read-only request)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

import httpx2

from framestudio.net import OutboundBlocked
from framestudio.providers.base import ProviderError, classify_http

ValidationState = Literal["ok", "refused", "unreachable"]


@dataclass(frozen=True)
class Validation:
    state: ValidationState
    message: str
    error: ProviderError | None = None


Validator = Callable[[httpx2.AsyncClient, str], Awaitable[Validation]]


@dataclass(frozen=True)
class ProviderInfo:
    id: str
    name: str
    description: str
    #: The only hosts this provider's client may reach.
    hosts: frozenset[str]
    key_url: str | None = None
    requires_key: bool = True
    #: A built-in test double (no network, no key).
    test: bool = False
    validator: Validator | None = field(default=None, repr=False)


async def check_with(
    client: httpx2.AsyncClient, name: str, url: str, headers: dict[str, str]
) -> Validation:
    try:
        response = await client.get(url, headers=headers)
    except OutboundBlocked as exc:
        return Validation("unreachable", str(exc))
    except httpx2.TimeoutException:
        return Validation(
            "unreachable", f"{name} did not answer in time. Check the network and try again."
        )
    except httpx2.RequestError:
        return Validation(
            "unreachable", f"{name} could not be reached. Check the network and try again."
        )
    if response.is_success:
        return Validation("ok", f"{name} accepted the key.")
    error = classify_http(response.status_code, response.text, response.headers.get("retry-after"))
    if error.kind == "transient":
        return Validation(
            "unreachable",
            f"{name} is busy right now (HTTP {response.status_code}). Try again shortly.",
            error,
        )
    if error.kind == "quota":
        return Validation(
            "ok", f"{name} accepted the key, but the account is out of credit.", error
        )
    if error.kind == "auth":
        return Validation(
            "refused",
            f"{name} refused this key. Check that it was copied whole and is still active.",
            error,
        )
    return Validation(
        "refused", f"{name} did not accept the key (HTTP {response.status_code}).", error
    )


async def _openai(client: httpx2.AsyncClient, key: str) -> Validation:
    return await check_with(
        client, "OpenAI", "https://api.openai.com/v1/models", {"Authorization": f"Bearer {key}"}
    )


async def _anthropic(client: httpx2.AsyncClient, key: str) -> Validation:
    return await check_with(
        client,
        "Anthropic",
        "https://api.anthropic.com/v1/models?limit=1",
        {"x-api-key": key, "anthropic-version": "2023-06-01"},
    )


async def _google(client: httpx2.AsyncClient, key: str) -> Validation:
    # The key goes in a header, not the ?key= query string, so it never sits in a URL.
    return await check_with(
        client,
        "Google Gemini",
        "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1",
        {"x-goog-api-key": key},
    )


async def _byteplus(client: httpx2.AsyncClient, key: str) -> Validation:
    # Listing video tasks is free and needs a valid key. To be confirmed
    # against the live API in M2.8 (LIVE_API=1).
    return await check_with(
        client,
        "BytePlus ModelArk",
        "https://ark.ap-southeast.bytepluses.com/api/v3/contents/generations/tasks?page_num=1&page_size=1",
        {"Authorization": f"Bearer {key}"},
    )


async def _fal(client: httpx2.AsyncClient, key: str) -> Validation:
    # Reading a price needs a valid key and costs nothing.
    return await check_with(
        client,
        "fal.ai",
        "https://api.fal.ai/v1/models/pricing?endpoint_id=fal-ai/flux/dev",
        {"Authorization": f"Key {key}"},
    )


PROVIDERS: dict[str, ProviderInfo] = {
    p.id: p
    for p in (
        ProviderInfo(
            "openai",
            "OpenAI",
            "GPT models for the Director, prompt help and captions.",
            frozenset({"api.openai.com"}),
            "https://platform.openai.com/api-keys",
            validator=_openai,
        ),
        ProviderInfo(
            "anthropic",
            "Anthropic",
            "Claude models for the Director, prompt help and captions.",
            frozenset({"api.anthropic.com"}),
            "https://console.anthropic.com/settings/keys",
            validator=_anthropic,
        ),
        ProviderInfo(
            "google",
            "Google Gemini",
            "Gemini models, and Nano Banana for images.",
            frozenset({"generativelanguage.googleapis.com"}),
            "https://aistudio.google.com/app/apikey",
            validator=_google,
        ),
        ProviderInfo(
            "byteplus",
            "BytePlus ModelArk",
            "Seedance for video.",
            # The API, and BytePlus storage where finished clips are downloaded.
            frozenset({"ark.ap-southeast.bytepluses.com", "*.bytepluses.com"}),
            "https://console.byteplus.com/ark",
            validator=_byteplus,
        ),
        ProviderInfo(
            "fal",
            "fal.ai",
            "Many hosted image and video models with one key, added from fal's own catalog; "
            "a second route when another provider is unavailable.",
            # The queue, the platform API (models, prices), and fal's media CDN.
            frozenset({"queue.fal.run", "api.fal.ai", "*.fal.media"}),
            "https://fal.ai/dashboard/keys",
            validator=_fal,
        ),
        ProviderInfo(
            "mock-cloud",
            "Mock cloud",
            "The built-in hosted test model. Needs no key and makes no network call; "
            "a spending cap here shows how caps behave.",
            frozenset(),
            requires_key=False,
            test=True,
        ),
    )
}


def host_allowlist() -> dict[str, frozenset[str]]:
    return {pid: info.hosts for pid, info in PROVIDERS.items()}
