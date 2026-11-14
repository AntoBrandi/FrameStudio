"""Estimated prices of language models, for the usage ledger and spending caps
(plan step M2.3). US dollars per million tokens.

Only rates with a source are listed. A model that matches no entry is priced
at its provider's *fallback*: deliberately at the top of the provider's range,
so a spending cap still stops requests early rather than late. Every figure
is an estimate; the provider's bill is the final word.
"""

from __future__ import annotations

from dataclasses import dataclass

from framestudio.llm.base import Usage

CHECKED = "2026-11-13"


@dataclass(frozen=True)
class Rate:
    input: float
    output: float
    cache_read: float | None = None
    cache_write: float | None = None

    def cost(self, usage: Usage) -> float:
        cache_read = self.cache_read if self.cache_read is not None else self.input
        cache_write = self.cache_write if self.cache_write is not None else self.input
        total = (
            usage.input_tokens * self.input
            + usage.output_tokens * self.output
            + usage.cache_read_tokens * cache_read
            + usage.cache_write_tokens * cache_write
        )
        return round(total / 1_000_000, 6)


#: provider -> (model id prefix, rate), longest prefix wins.
RATES: dict[str, list[tuple[str, Rate]]] = {
    # Anthropic list prices (cached 2026-11-03 in Anthropic's API reference).
    "anthropic": [
        ("claude-fable-5-2", Rate(8.0, 40.0, 0.20, 10.0)),
        ("claude-fable-5-1", Rate(10.0, 50.0, 1.0, 12.5)),
        ("claude-fable-5", Rate(10.0, 50.0, 1.0, 12.5)),
        ("claude-opus-5-5", Rate(4.0, 20.0, 0.20, 5.0)),
        ("claude-opus-5", Rate(5.0, 25.0, 0.50, 6.25)),
        ("claude-opus-4", Rate(5.0, 25.0, 0.50, 6.25)),
        ("claude-sonnet-5-5", Rate(2.0, 10.0, 0.10, 2.5)),
        ("claude-sonnet-5", Rate(2.0, 10.0, 0.20, 2.5)),
        ("claude-sonnet-4", Rate(3.0, 15.0, 0.30, 3.75)),
        ("claude-haiku-5-5", Rate(0.10, 0.50, 0.01, 0.125)),
        ("claude-haiku-4-5", Rate(1.0, 5.0, 0.10, 1.25)),
    ],
    # https://developers.openai.com/api/docs/pricing (GPT-5 family).
    "openai": [
        ("gpt-5-nano", Rate(0.05, 0.40, 0.005)),
        ("gpt-5-mini", Rate(0.25, 2.0, 0.025)),
        ("gpt-5", Rate(1.25, 10.0, 0.125)),
    ],
    # https://ai.google.dev/gemini-api/docs/pricing (prompts up to 200K tokens).
    "google": [
        ("gemini-3-pro", Rate(2.0, 12.0)),
        ("gemini-3-flash", Rate(0.50, 3.0)),
    ],
}

#: Unknown models: the top of each provider's range (see the module notes).
FALLBACK: dict[str, Rate] = {
    "anthropic": Rate(10.0, 50.0, 1.0, 12.5),
    "openai": Rate(10.0, 50.0),
    "google": Rate(4.0, 18.0),
}


def rate_for(provider: str, model: str) -> Rate | None:
    matches = [
        (prefix, rate) for prefix, rate in RATES.get(provider, []) if model.startswith(prefix)
    ]
    if matches:
        return max(matches, key=lambda item: len(item[0]))[1]
    return FALLBACK.get(provider)


def price(provider: str):  # type: ignore[no-untyped-def]
    """A ``MeteredLLM`` price function for ``provider``."""

    def estimate(model: str, usage: Usage) -> float | None:
        rate = rate_for(provider, model)
        return rate.cost(usage) if rate else None

    return estimate
