"""Add, check, turn off and remove provider connections (plan step M2.3).

A new key is checked with the provider first and stored only if it is not
refused. Responses describe a connection (status, the key's last four
characters, which store holds it, the monthly cap and this month's spend) but
never contain the key; :meth:`Connections.api_key` is for adapters inside the
backend only.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from framestudio.connections.registry import PROVIDERS, ProviderInfo, Validation
from framestudio.db.base import utcnow
from framestudio.db.models import Connection
from framestudio.usage import current_month
from framestudio.vault import SecretStoreError

if TYPE_CHECKING:
    from framestudio.services import Services

ConnectionStatus = Literal["ok", "refused", "unreachable", "unchecked", "missing"]
MAX_KEY_LENGTH = 512


class ConnectionOut(BaseModel):
    provider: str
    name: str
    description: str
    key_url: str | None
    #: The only hosts FrameStudio contacts for this provider.
    hosts: list[str]
    requires_key: bool
    test: bool
    connected: bool
    enabled: bool
    key_hint: str | None
    key_store: Literal["keyring", "file", "memory"] | None
    status: ConnectionStatus | None
    status_message: str | None
    validated_at: datetime | None
    monthly_cap_usd: float | None
    spent_this_month_usd: float


class ConnectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: A new key. Checked with the provider before it is stored.
    api_key: SecretStr | None = None
    enabled: bool | None = None
    #: US dollars per calendar month; send null to remove the cap.
    monthly_cap_usd: float | None = Field(default=None, ge=0, le=1_000_000)


class UnknownProviderError(LookupError):
    pass


class KeyRefusedError(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _hint(key: str) -> str | None:
    return f"…{key[-4:]}" if len(key) >= 12 else None


class Connections:
    def __init__(self, services: Services) -> None:
        self._services = services
        self._enabled: dict[str, bool] = {}
        self._lock = asyncio.Lock()
        self.reload()

    def reload(self) -> None:
        from sqlalchemy import select

        with self._services.db.session() as session:
            self._enabled = {
                row.provider: row.enabled
                and (row.secret_name is not None or not _info(row.provider).requires_key)
                for row in session.scalars(select(Connection))
                if row.provider in PROVIDERS
            }

    # ------------------------------------------------------------ reading

    def is_enabled(self, provider: str) -> bool:
        """Whether outbound calls for ``provider`` are allowed right now."""
        return self._enabled.get(provider, False)

    def api_key(self, provider: str) -> str | None:
        """The stored key, for adapters inside the backend. Never return it
        from an API route or put it in a log message."""
        with self._services.db.session() as session:
            row = session.get(Connection, provider)
            if row is None or not row.secret_name or not row.secret_backend:
                return None
            name, backend = row.secret_name, row.secret_backend
        return self._services.vault.get(name, backend)

    def _out(self, info: ProviderInfo, row: Connection | None) -> ConnectionOut:
        connected = row is not None and (row.secret_name is not None or not info.requires_key)
        return ConnectionOut(
            provider=info.id,
            name=info.name,
            description=info.description,
            key_url=info.key_url,
            hosts=sorted(info.hosts),
            requires_key=info.requires_key,
            test=info.test,
            connected=connected,
            enabled=bool(row and row.enabled and connected),
            key_hint=row.key_hint if row else None,
            key_store=row.secret_backend if row else None,  # type: ignore[arg-type]
            status=row.status if row else None,  # type: ignore[arg-type]
            status_message=row.status_message if row else None,
            validated_at=row.validated_at if row else None,
            monthly_cap_usd=row.monthly_cap_usd if row else None,
            spent_this_month_usd=round(
                self._services.usage.month_spend(info.id, current_month()), 6
            ),
        )

    def list(self) -> list[ConnectionOut]:
        with self._services.db.session() as session:
            return [
                self._out(info, session.get(Connection, info.id)) for info in PROVIDERS.values()
            ]

    def get(self, provider: str) -> ConnectionOut:
        info = _info(provider)
        with self._services.db.session() as session:
            return self._out(info, session.get(Connection, provider))

    # ------------------------------------------------------------ changing

    async def _validate(self, info: ProviderInfo, key: str) -> Validation:
        if info.validator is None:
            return Validation("ok", "No key needed.")
        async with self._services.outbound.client(
            info.id, require_enabled=False, timeout=20
        ) as client:
            return await info.validator(client, key)

    async def update(self, provider: str, body: ConnectionUpdate) -> ConnectionOut:
        info = _info(provider)
        async with self._lock:
            validation: Validation | None = None
            key = body.api_key.get_secret_value().strip() if body.api_key is not None else None
            if key is not None:
                if not info.requires_key:
                    raise KeyRefusedError(f"{info.name} does not use a key.")
                if not key or len(key) > MAX_KEY_LENGTH or any(c.isspace() for c in key):
                    raise KeyRefusedError(
                        "That does not look like an API key: paste it without spaces."
                    )
                validation = await self._validate(info, key)
                if validation.state == "refused":
                    raise KeyRefusedError(validation.message)
            changes = body.model_fields_set
            await asyncio.to_thread(self._save, info, key, validation, body, changes)
        self._changed()
        return await asyncio.to_thread(self.get, provider)

    def _save(
        self,
        info: ProviderInfo,
        key: str | None,
        validation: Validation | None,
        body: ConnectionUpdate,
        changes: set[str],
    ) -> None:
        vault = self._services.vault
        with self._services.db.session() as session:
            row = session.get(Connection, info.id)
            if row is None:
                if info.requires_key and key is None:
                    raise KeyRefusedError(f"Add a key for {info.name} first.")
                row = Connection(provider=info.id, enabled=True, status="unchecked")
                if not info.requires_key:
                    row.status, row.validated_at = "ok", utcnow()
                session.add(row)
            if key is not None and validation is not None:
                name = f"connection.{info.id}.{uuid.uuid4().hex[:12]}"
                try:
                    backend = vault.put(name, key)
                except SecretStoreError as exc:
                    raise KeyRefusedError(f"The key could not be stored: {exc}") from None
                old = (row.secret_name, row.secret_backend)
                row.secret_name, row.secret_backend, row.key_hint = name, backend, _hint(key)
                row.status = "ok" if validation.state == "ok" else "unreachable"
                row.status_message = validation.message
                row.validated_at = utcnow() if validation.state == "ok" else None
                if old[0] and old[1]:
                    # The new key is saved; a failure here only leaves clutter.
                    with contextlib.suppress(SecretStoreError):
                        vault.delete(old[0], old[1])
            if "enabled" in changes and body.enabled is not None:
                row.enabled = body.enabled
            if "monthly_cap_usd" in changes:
                row.monthly_cap_usd = body.monthly_cap_usd

    async def check(self, provider: str) -> ConnectionOut:
        """Check the stored key with the provider again."""
        info = _info(provider)
        async with self._lock:
            key = await asyncio.to_thread(self.api_key, provider)
            if info.requires_key and key is None:
                await asyncio.to_thread(
                    self._set_status, provider, "missing", "No key is stored. Add it again."
                )
            else:
                validation = await self._validate(info, key or "")
                state = {"ok": "ok", "refused": "refused", "unreachable": "unreachable"}[
                    validation.state
                ]
                await asyncio.to_thread(self._set_status, provider, state, validation.message)
        self._changed()
        return await asyncio.to_thread(self.get, provider)

    def _set_status(self, provider: str, status: str, message: str) -> None:
        with self._services.db.session() as session:
            row = session.get(Connection, provider)
            if row is None:
                raise UnknownProviderError(provider)
            row.status, row.status_message = status, message
            if status == "ok":
                row.validated_at = utcnow()

    async def remove(self, provider: str) -> None:
        _info(provider)
        async with self._lock:
            await asyncio.to_thread(self._remove, provider)
        self._changed()

    def _remove(self, provider: str) -> None:
        with self._services.db.session() as session:
            row = session.get(Connection, provider)
            if row is None:
                return
            if row.secret_name and row.secret_backend:
                self._services.vault.delete(row.secret_name, row.secret_backend)
            session.delete(row)

    def _changed(self) -> None:
        self.reload()
        registry = getattr(self._services, "llm_registry", None)
        if registry is not None:
            registry.rebuild()
        self._services.events.publish("connections.changed", {})


def _info(provider: str) -> ProviderInfo:
    info = PROVIDERS.get(provider)
    if info is None:
        raise UnknownProviderError(provider)
    return info
