"""Enhance prompt (plan step M2.6): a rough prompt comes back improved, with a
short list of what changed, from the model chosen for prompt help."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from framestudio.llm.base import ChatRequest, Message
from framestudio.providers.base import ProviderError

if TYPE_CHECKING:
    from framestudio.services import Services

ENHANCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "prompt": {"type": "string", "description": "The improved prompt, ready to send."},
        "changes": {
            "type": "array",
            "description": "What changed, a few words each, at most five.",
            "items": {"type": "string"},
        },
    },
    "required": ["prompt", "changes"],
    "additionalProperties": False,
}

SYSTEM = """\
You rewrite prompts for image and video generation models so they produce what \
the person means.

Keep everything the person asked for: subjects, names, numbers, wording in \
quotes, the language they wrote in. Make it concrete where it is vague: subject \
and action, setting, framing and composition, light, colour, mood, medium or \
style. For video, also say how the camera moves and what happens over time, in \
order. Write one flowing description in plain sentences, not a list of tags. \
Do not add quality boilerplate ("8k", "masterpiece", "trending"), signatures, \
watermarks or text in the image unless asked. Do not add people, brands or \
styles of living artists that the person did not mention. Stay within the \
length limit given.

Words starting with @, # or / (such as @maya, #dolly-in, /storyboard) name \
characters, places, props, camera moves or tools the app fills in: copy each \
one exactly as written, wherever it fits in the new prompt, and do not describe \
what it looks like.

Reply with the improved prompt and a short list of what you changed."""


class EnhanceRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    mode: Literal["image", "video"] = "image"
    #: The catalog model the prompt is for, so length limits and strengths apply.
    model: str | None = None


class EnhanceResult(BaseModel):
    prompt: str
    changes: list[str]
    #: The language model that rewrote it, as provider/model.
    llm: str


async def enhance_prompt(services: Services, body: EnhanceRequest) -> EnhanceResult:
    llm, model = services.llm_registry.for_task("prompt_help")
    spec = services.catalog.get(body.model) if body.model else None
    limit = (spec.limits.max_prompt_chars if spec else None) or 1500
    target = f"{spec.name}: {spec.description}" if spec else "a general image or video model"
    text = (
        f"Target: {body.mode} generation with {target}\n"
        f"Length limit: {limit} characters\n\n"
        f"Prompt to improve:\n{body.prompt.strip()}"
    )
    request = ChatRequest(
        model=model,
        system=SYSTEM,
        messages=(Message.user(text),),
        response_schema=ENHANCE_SCHEMA,
        max_tokens=4000,
        effort="low",  # a quick rewrite; deeper thinking adds wait, not quality
    )
    response = await llm.complete(request)
    data = response.parsed()  # ProviderError on a refusal or a malformed reply
    improved = str(data.get("prompt", "")).strip()
    if not improved:
        raise ProviderError("unknown", "The model returned an empty prompt.", code="llm_empty")
    changes = [str(item).strip() for item in data.get("changes", []) if str(item).strip()][:5]
    return EnhanceResult(prompt=improved[:limit], changes=changes, llm=f"{llm.id}/{model}")
