"""Spending caps and the usage ledger around any LLM provider (plan step M2.3).

Each call checks the provider's monthly cap first (refused with code
``spend_cap`` once it is reached) and records tokens and estimated cost when
the reply is done. Adapters (M2.4) supply ``price``: model and usage to USD.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, replace

from framestudio.llm.base import (
    ChatRequest,
    ChatResponse,
    Done,
    LLMProvider,
    ModelInfo,
    StreamEvent,
    Usage,
    collect,
)
from framestudio.usage import UsageLedger

Price = Callable[[str, Usage], float | None]


@dataclass(frozen=True)
class MeteredLLM:
    inner: LLMProvider
    ledger: UsageLedger
    #: What the spending is for in the ledger: director, enhance, describe…
    source: str = "llm"
    price: Price | None = None

    @property
    def id(self) -> str:
        return self.inner.id

    def for_source(self, source: str) -> MeteredLLM:
        return replace(self, source=source)

    async def list_models(self) -> list[ModelInfo]:
        return await self.inner.list_models()

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        await asyncio.to_thread(self.ledger.check_cap, self.id, None)
        async for event in self.inner.stream(request):
            if isinstance(event, Done):
                response = event.response
                usage = response.usage
                cost = self.price(response.model, usage) if self.price else None
                await asyncio.to_thread(
                    self.ledger.record,
                    self.id,
                    model=response.model,
                    source=self.source,
                    cost_usd=cost,
                    input_tokens=usage.input_tokens
                    + usage.cache_read_tokens
                    + usage.cache_write_tokens,
                    output_tokens=usage.output_tokens,
                )
            yield event

    async def complete(self, request: ChatRequest) -> ChatResponse:
        return await collect(self.stream(request))
