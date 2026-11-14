"""Language models: what each connection offers and which one each task uses."""

from __future__ import annotations

from fastapi import APIRouter

from framestudio.api.deps import ServicesDep
from framestudio.api.llm_errors import http_error
from framestudio.llm.prompt_help import EnhanceRequest, EnhanceResult, enhance_prompt
from framestudio.llm.registry import LLMOverview
from framestudio.providers.base import ProviderError

router = APIRouter(prefix="/api", tags=["llm"])


@router.get("/llm")
async def get_llm_overview(services: ServicesDep, refresh: bool = False) -> LLMOverview:
    """Models read from each connected provider's API (cached for ten minutes;
    ``refresh`` reads them again), and the model each task will use."""
    registry = services.llm_registry
    providers = await registry.models(refresh=refresh)
    return LLMOverview(providers=providers, tasks=registry.tasks())


@router.post("/llm/enhance")
async def enhance(body: EnhanceRequest, services: ServicesDep) -> EnhanceResult:
    """Rewrite a prompt with the model chosen for prompt help; the reply
    lists what changed so the composer can show a diff and undo it."""
    try:
        return await enhance_prompt(services, body)
    except ProviderError as exc:
        raise http_error(exc) from None
