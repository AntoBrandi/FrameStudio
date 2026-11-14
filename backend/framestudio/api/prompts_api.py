"""Prompt tokens API (plan step M4.3): the one parser, for pasted text,
prompts sent as text and prompts written by the Director."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from framestudio.api.deps import ServicesDep
from framestudio.prompts import PromptParse, PromptParsed, parse_request

router = APIRouter(prefix="/api", tags=["prompts"])


@router.post("/prompts/parse")
async def parse_prompt(body: PromptParse, services: ServicesDep) -> PromptParsed:
    """Text → tokens, or a token list → its canonical form, with the elements
    of ``project_id`` and the global ones; unknown handles come back as
    warnings."""

    def run() -> PromptParsed:
        with services.db.session() as session:
            return parse_request(session, body)

    return await asyncio.to_thread(run)
