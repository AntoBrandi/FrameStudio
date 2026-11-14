"""Which language models can be used, and which one each task uses (plan step
M2.4).

A provider becomes usable when its connection is on and has a key; the
registry builds its adapter (wrapped in :class:`MeteredLLM` for caps and the
usage ledger) and rebuilds whenever a connection changes. Settings hold a
default model and optional per-task choices as ``"provider/model"``; an empty
choice means "automatic": the first connected provider's preferred model.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Literal

import httpx2
from pydantic import BaseModel

from framestudio.connections.registry import PROVIDERS
from framestudio.llm.anthropic_api import AnthropicLLM
from framestudio.llm.base import LLMProvider, ModelInfo
from framestudio.llm.fake import FakeLLM, demo
from framestudio.llm.gemini_api import GeminiLLM
from framestudio.llm.metered import MeteredLLM
from framestudio.llm.openai_api import OpenAILLM
from framestudio.llm.pricing import price, rate_for
from framestudio.providers.base import GenerationProvider, ProviderError
from framestudio.providers.fal import FalProvider
from framestudio.providers.gemini_image import GeminiImageProvider
from framestudio.providers.mock import OfflineHostedProvider, OfflineTaskProvider
from framestudio.providers.seedance import SeedanceProvider

if TYPE_CHECKING:
    from framestudio.services import Services

log = logging.getLogger("framestudio.llm")

Task = Literal["prompt_help", "captions", "director"]
TASKS: tuple[Task, ...] = ("prompt_help", "captions", "director")
#: Provider order for "automatic", and each provider's preferred models.
ORDER = ("anthropic", "openai", "google")
PREFERRED: dict[str, tuple[str, ...]] = {
    "anthropic": ("claude-opus-5-5",),
    "openai": ("gpt-5",),
    "google": ("gemini-3-pro",),
}
MODEL_LIST_TTL = 600.0

Factory = Callable[[str, httpx2.AsyncBaseTransport], LLMProvider]
FACTORIES: dict[str, Factory] = {
    "anthropic": lambda key, transport: AnthropicLLM(key, transport),
    "openai": lambda key, transport: OpenAILLM(key, transport),
    "google": lambda key, transport: GeminiLLM(key, transport),
}
#: Offline stand-ins (FRAMESTUDIO_FAKE_PROVIDERS): the scripted model, named
#: like each provider's own, so every screen works without a network.
#: Hosted generation providers that come with a connection (M2.7, M2.8).
GENERATORS: dict[str, Callable[[str, httpx2.AsyncBaseTransport], GenerationProvider]] = {
    "google": lambda key, transport: GeminiImageProvider(key, transport),
    "byteplus": lambda key, transport: SeedanceProvider(key, transport),
    "fal": lambda key, transport: FalProvider(key, transport),
}
FAKE_MODELS: dict[str, tuple[ModelInfo, ...]] = {
    "anthropic": (
        ModelInfo(
            "claude-opus-5-5", "Claude Opus 5.5 (offline)", "anthropic", context_tokens=1_000_000
        ),
    ),
    "openai": (ModelInfo("gpt-5", "GPT-5 (offline)", "openai"),),
    "google": (ModelInfo("gemini-3-pro", "Gemini 3 Pro (offline)", "google"),),
}


class LLMModelOut(BaseModel):
    ref: str
    id: str
    name: str
    provider: str
    vision: bool
    tools: bool
    structured_output: bool
    effort: bool
    context_tokens: int | None
    max_output_tokens: int | None
    #: Estimated US$ per million input and output tokens.
    usd_per_mtok_in: float | None
    usd_per_mtok_out: float | None


class LLMProviderOut(BaseModel):
    provider: str
    name: str
    models: list[LLMModelOut]
    error: str | None


class LLMTaskOut(BaseModel):
    task: Literal["default", "prompt_help", "captions", "director"]
    #: The stored choice ("" = automatic, or the default for a task).
    choice: str
    #: The model that will answer, or None when nothing is connected.
    effective: str | None


class LLMOverview(BaseModel):
    providers: list[LLMProviderOut]
    tasks: list[LLMTaskOut]


def _model_out(info: ModelInfo) -> LLMModelOut:
    rate = rate_for(info.provider, info.id)
    return LLMModelOut(
        ref=f"{info.provider}/{info.id}",
        id=info.id,
        name=info.name,
        provider=info.provider,
        vision=info.vision,
        tools=info.tools,
        structured_output=info.structured_output,
        effort=info.effort,
        context_tokens=info.context_tokens,
        max_output_tokens=info.max_output_tokens,
        usd_per_mtok_in=rate.input if rate else None,
        usd_per_mtok_out=rate.output if rate else None,
    )


class LLMRegistry:
    def __init__(self, services: Services, fixed: dict[str, LLMProvider] | None = None) -> None:
        self._services = services
        self._fixed = fixed
        self._models: dict[str, tuple[float, list[ModelInfo], str | None]] = {}
        #: Generation providers handed in by the caller (tests, embedders) stay as given.
        self._explicit = set(services.providers) & set(GENERATORS)
        self.rebuild()

    # ------------------------------------------------------------ providers

    def rebuild(self) -> None:
        """Re-create the adapters for the connections that are on."""
        services = self._services
        if self._fixed is not None:  # tests and embedders pass their own providers
            services.llms = {
                pid: llm
                if isinstance(llm, MeteredLLM)
                else MeteredLLM(llm, services.usage, price=price(pid))
                for pid, llm in self._fixed.items()
            }
            self._rebuild_generators()
            return
        llms: dict[str, LLMProvider] = {}
        for provider in ORDER:
            if not services.connections.is_enabled(provider):
                continue
            key = services.connections.api_key(provider)
            if not key:
                continue
            if services.config.fake_providers:
                inner: LLMProvider = FakeLLM(
                    id=provider, models=FAKE_MODELS[provider], delay=0.01, default=demo
                )
            else:
                inner = FACTORIES[provider](key, services.outbound.transport(provider))
            llms[provider] = MeteredLLM(inner, services.usage, price=price(provider))
        services.llms = llms
        self._models = {p: v for p, v in self._models.items() if p in llms}
        self._rebuild_generators()

    def _rebuild_generators(self) -> None:
        """Hosted image and video providers of the connections that are on."""
        services = self._services
        for provider, factory in GENERATORS.items():
            if provider in self._explicit:
                continue
            on = services.connections.is_enabled(provider)
            key = services.connections.api_key(provider) if on else None
            if not key:
                services.providers.pop(provider, None)
                continue
            if services.config.fake_providers:
                tasks = {"byteplus", "fal"}
                stand_in = OfflineTaskProvider if provider in tasks else OfflineHostedProvider
                services.providers[provider] = stand_in(provider, services.settings)
            else:
                services.providers[provider] = factory(key, services.outbound.transport(provider))

    async def models(self, refresh: bool = False) -> list[LLMProviderOut]:
        """Each connected provider's models, read from its API (cached)."""

        async def one(provider: str, llm: LLMProvider) -> LLMProviderOut:
            cached = self._models.get(provider)
            if refresh or cached is None or time.monotonic() - cached[0] > MODEL_LIST_TTL:
                try:
                    found, error = await llm.list_models(), None
                except ProviderError as exc:
                    found, error = [], exc.message
                    log.warning("could not list %s models: %s", provider, exc.kind)
                if error is None or cached is None:
                    cached = (time.monotonic(), found, error)
                    self._models[provider] = cached
            info = PROVIDERS.get(provider)
            return LLMProviderOut(
                provider=provider,
                name=info.name if info else provider,
                models=[_model_out(m) for m in cached[1]],
                error=cached[2],
            )

        llms = self._services.llms
        return list(await asyncio.gather(*(one(p, llm) for p, llm in llms.items())))

    # ------------------------------------------------------------ choices

    def _known(self, provider: str) -> list[str]:
        cached = self._models.get(provider)
        return [m.id for m in cached[1]] if cached else []

    def _automatic(self) -> str | None:
        for provider in (*ORDER, *self._services.llms):
            if provider not in self._services.llms:
                continue
            known = self._known(provider)
            for model in PREFERRED.get(provider, ()):
                if not known or model in known:
                    return f"{provider}/{model}"
            if known:
                return f"{provider}/{known[0]}"
            if provider == "fake":
                return "fake/fake-large"
        return None

    def _usable(self, ref: str) -> bool:
        provider, _, model = ref.partition("/")
        return bool(model) and provider in self._services.llms

    def effective(self, task: Task | Literal["default"]) -> str | None:
        settings = self._services.settings.get()
        choices = [] if task == "default" else [getattr(settings, f"llm_{task}")]
        choices.append(settings.llm_default)
        for choice in choices:
            if choice and self._usable(choice):
                return choice  # type: ignore[no-any-return]
        return self._automatic()

    def tasks(self) -> list[LLMTaskOut]:
        settings = self._services.settings.get()
        out = [
            LLMTaskOut(
                task="default", choice=settings.llm_default, effective=self.effective("default")
            )
        ]
        for task in TASKS:
            out.append(
                LLMTaskOut(
                    task=task,
                    choice=getattr(settings, f"llm_{task}"),
                    effective=self.effective(task),
                )
            )
        return out

    def for_task(self, task: Task) -> tuple[LLMProvider, str]:
        """The provider and model that answer ``task``, metered as that task."""
        ref = self.effective(task)
        if ref is None:
            raise ProviderError(
                "unavailable",
                "No language model is connected. Add a key in Settings › Connections.",
                code="no_llm",
            )
        provider, _, model = ref.partition("/")
        llm = self._services.llms[provider]
        if isinstance(llm, MeteredLLM):
            llm = llm.for_source(task)
        return llm, model
