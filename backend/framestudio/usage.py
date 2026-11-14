"""Usage ledger and monthly spending caps (plan step M2.3).

Every billable request to a hosted provider becomes a row: tokens, images,
video seconds and an estimated cost. A connection may carry a monthly cap in
US dollars; a request that would pass it is refused before anything is sent.

Paid generations *reserve* their estimated cost when they are submitted (the
check and the row happen under one lock, so two jobs cannot both slip under
the cap) and the reservation is voided if the provider refuses the task.
Months are calendar months in UTC.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel
from sqlalchemy import func, select

from framestudio.db.models import Connection, UsageRecord
from framestudio.providers.base import ProviderError

if TYPE_CHECKING:
    from framestudio.services import Services

CAP_CODE = "spend_cap"


def current_month(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).strftime("%Y-%m")


def _usd(value: float) -> str:
    return f"${value:,.2f}"


class ProviderUsage(BaseModel):
    provider: str
    requests: int
    input_tokens: int
    output_tokens: int
    images: int
    video_seconds: float
    cost_usd: float
    cap_usd: float | None


class UsageSummary(BaseModel):
    month: str
    providers: list[ProviderUsage]
    total_cost_usd: float


class UsageLedger:
    def __init__(self, services: Services) -> None:
        self._services = services
        self._lock = threading.Lock()

    # ------------------------------------------------------------ reading

    def month_spend(self, provider: str, month: str | None = None) -> float:
        with self._services.db.session() as session:
            total = session.scalar(
                select(func.coalesce(func.sum(UsageRecord.cost_usd), 0.0)).where(
                    UsageRecord.provider == provider,
                    UsageRecord.month == (month or current_month()),
                )
            )
        return float(total or 0.0)

    def cap(self, provider: str) -> float | None:
        with self._services.db.session() as session:
            row = session.get(Connection, provider)
            return row.monthly_cap_usd if row else None

    def summary(self, month: str | None = None) -> UsageSummary:
        month = month or current_month()
        with self._services.db.session() as session:
            rows = session.execute(
                select(
                    UsageRecord.provider,
                    func.sum(UsageRecord.requests),
                    func.sum(UsageRecord.input_tokens),
                    func.sum(UsageRecord.output_tokens),
                    func.sum(UsageRecord.images),
                    func.sum(UsageRecord.video_seconds),
                    func.coalesce(func.sum(UsageRecord.cost_usd), 0.0),
                )
                .where(UsageRecord.month == month)
                .group_by(UsageRecord.provider)
                .order_by(UsageRecord.provider)
            ).all()
            caps = {c.provider: c.monthly_cap_usd for c in session.scalars(select(Connection))}
        providers = [
            ProviderUsage(
                provider=provider,
                requests=int(requests or 0),
                input_tokens=int(tokens_in or 0),
                output_tokens=int(tokens_out or 0),
                images=int(images or 0),
                video_seconds=float(seconds or 0.0),
                cost_usd=round(float(cost or 0.0), 6),
                cap_usd=caps.get(provider),
            )
            for provider, requests, tokens_in, tokens_out, images, seconds, cost in rows
        ]
        return UsageSummary(
            month=month,
            providers=providers,
            total_cost_usd=round(sum(p.cost_usd for p in providers), 6),
        )

    # ------------------------------------------------------------ caps

    def _refusal(self, provider: str, estimate: float | None) -> ProviderError | None:
        cap = self.cap(provider)
        if cap is None:
            return None
        spent = self.month_spend(provider)
        if spent >= cap:
            message = (
                f"The {_usd(cap)} monthly cap for {provider} is reached ({_usd(spent)} spent). "
                "Raise it in Settings › Connections."
            )
        elif estimate is not None and spent + estimate > cap:
            message = (
                f"This would pass the {_usd(cap)} monthly cap for {provider}: {_usd(spent)} spent, "
                f"about {_usd(estimate)} more. Raise the cap in Settings › Connections."
            )
        else:
            return None
        return ProviderError("quota", message, provider=provider, code=CAP_CODE)

    def check_cap(self, provider: str, estimate: float | None = None) -> None:
        """Raise :class:`ProviderError` (code ``spend_cap``) if ``estimate``
        more would pass the provider's cap, or the cap is already reached."""
        with self._lock:
            refusal = self._refusal(provider, estimate)
        if refusal:
            raise refusal

    # ------------------------------------------------------------ writing

    def record(
        self,
        provider: str,
        *,
        model: str = "",
        source: str = "composer",
        cost_usd: float | None = None,
        requests: int = 1,
        input_tokens: int = 0,
        output_tokens: int = 0,
        images: int = 0,
        video_seconds: float = 0.0,
        generation_id: str | None = None,
    ) -> str:
        with self._services.db.session() as session:
            row = UsageRecord(
                provider=provider,
                model=model,
                source=source,
                month=current_month(),
                requests=requests,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                images=images,
                video_seconds=video_seconds,
                cost_usd=cost_usd,
                generation_id=generation_id,
            )
            session.add(row)
            session.flush()
            usage_id = row.id
        self._services.events.publish("usage.recorded", {"provider": provider})
        return usage_id

    def reserve(self, provider: str, cost_usd: float | None, **fields: object) -> str:
        """Check the cap and record the spend in one step (paid submissions)."""
        with self._lock:
            refusal = self._refusal(provider, cost_usd)
            if refusal:
                raise refusal
            return self.record(provider, cost_usd=cost_usd, **fields)  # type: ignore[arg-type]

    def void(self, usage_id: str) -> None:
        """Drop a reservation for a request the provider refused."""
        with self._services.db.session() as session:
            row = session.get(UsageRecord, usage_id)
            if row is not None:
                session.delete(row)
        self._services.events.publish("usage.recorded", {})
