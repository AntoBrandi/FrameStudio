"""The model catalog (plan step M2.1, decision D4).

Every generation model, local or hosted, is a YAML entry under
``catalog/models/``: what it can do, the parameters it takes (with ranges and
the combinations it refuses), its limits, its license and, for hosted models,
its pricing. The composer renders its controls from these entries, the backend
validates every request against them, and the router reads them. Adding a
model means adding an entry (plus a workflow or an adapter), never UI code.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

CATALOG_DIR = Path(__file__).resolve().parent / "models"
#: FrameStudio inputs a provider_inputs map can name ("reference": one input image).
INPUT_KEYS = frozenset(
    {"prompt", "negative_prompt", "start_frame", "end_frame", "references", "reference", "seed"}
)

Mode = Literal["image", "video"]
Capability = Literal[
    "text_to_image",
    "image_edit",
    "multi_reference",
    "text_to_video",
    "image_to_video",
    "start_end_frames",
    "extend",
    "native_audio",
]
ParamType = Literal["enum", "integer", "number", "boolean", "seed", "string"]
Control = Literal["pills", "select", "slider", "toggle", "stepper", "seed", "text"]
_ID = re.compile(r"^[a-z0-9]+(?:[-.][a-z0-9]+)*$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LicenseRecord(_Strict):
    """Who made the model and on what terms, shown before use."""

    kind: Literal["open_weights", "hosted_service", "test_double"]
    name: str = Field(min_length=1)
    commercial_use: Literal["yes", "no", "restricted", "unknown"]
    credit: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    evidence_url: str | None = None
    notes: str = ""


class ParamSpec(_Strict):
    type: ParamType
    label: str = Field(min_length=1)
    default: Any = None
    #: A different default in some modes, e.g. a wide aspect for video.
    default_by_mode: dict[Mode, Any] | None = None
    values: list[str | int | float] | None = None
    min: float | None = None
    max: float | None = None
    step: float | None = None
    unit: str | None = None
    #: Only offered in these modes (all of the model's modes when omitted).
    modes: list[Mode] | None = None
    control: Control | None = None
    #: Shown under "More" in the composer.
    advanced: bool = False
    help: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> ParamSpec:
        if self.type == "enum":
            if not self.values:
                raise ValueError("enum parameters need 'values'")
        elif self.values is not None:
            raise ValueError("'values' is only for enum parameters")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("'min' is greater than 'max'")
        defaults = [self.default, *(self.default_by_mode or {}).values()]
        for default in defaults:
            if default is not None:
                problem = check_value(self, default)
                if problem:
                    raise ValueError(f"default {default!r} {problem}")
        return self

    def default_for(self, mode: str) -> Any:
        if self.default_by_mode and mode in self.default_by_mode:
            return self.default_by_mode[mode]  # type: ignore[index]
        return self.default


class Rule(_Strict):
    """A combination the model refuses: when ``when`` matches, the parameters
    in ``allow`` may only take the listed values (or range)."""

    when: dict[str, Any] = Field(min_length=1)
    allow: dict[str, list[Any] | dict[str, float]] = Field(min_length=1)
    reason: str = Field(min_length=1)


class Limits(_Strict):
    max_references: int = Field(default=0, ge=0)
    reference_kinds: list[Literal["image", "video", "audio"]] = Field(default_factory=list)
    max_batch: int = Field(default=4, ge=1, le=16)
    max_prompt_chars: int | None = Field(default=None, ge=1)


class Pricing(_Strict):
    """What a request costs, for the estimate shown before Generate."""

    unit: Literal["image", "video_second", "request"]
    usd: float = Field(ge=0)
    #: Price per unit by the value of one parameter, e.g. resolution.
    by: str | None = None
    table: dict[str, float] | None = None
    source_url: str = Field(min_length=1)
    checked_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class Variant(_Strict):
    """A downloadable form of a local model (filled in by M3)."""

    id: str
    format: Literal["fp32", "bf16", "fp16", "fp8", "gguf"]
    quant: str | None = None
    size_gb: float = Field(gt=0)
    vram_min_gb: float = Field(ge=0)
    vram_recommended_gb: float | None = None
    ram_min_gb: float = Field(ge=0)
    accelerators: list[Literal["cuda", "rocm", "mps", "vulkan", "cpu"]]
    engines: list[Literal["comfyui", "sdcpp", "python"]]
    curated_on: list[str] = Field(default_factory=list)


class Fixture(_Strict):
    """A request the model must accept; checked in CI and used by tests."""

    mode: Mode
    prompt: str = "a lighthouse at dusk"
    params: dict[str, Any] = Field(default_factory=dict)


class ModelSpec(_Strict):
    id: str
    name: str = Field(min_length=1)
    description: str = ""
    provider: str = Field(min_length=1)
    runtime: Literal["local", "api"]
    #: The model's id at the provider, when it is fixed (hosted models whose ids
    #: change are discovered from the provider's API instead).
    provider_model: str | None = None
    modes: list[Mode] = Field(min_length=1)
    capabilities: list[Capability] = Field(min_length=1)
    params: dict[str, ParamSpec] = Field(default_factory=dict)
    rules: list[Rule] = Field(default_factory=list)
    limits: Limits = Field(default_factory=Limits)
    license: LicenseRecord
    pricing: Pricing | None = None
    variants: list[Variant] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    #: How a request becomes the provider's own input (aggregators, M2.10):
    #: FrameStudio's names (prompt, start_frame, end_frame, references, seed,
    #: and parameter names) to the endpoint's field names.
    provider_inputs: dict[str, str] = Field(default_factory=dict)
    #: Auto picks the lowest-ranked model that fits and can run; models
    #: without a rank are never picked automatically (M2.9).
    auto_rank: int | None = Field(default=None, ge=0, le=1000)
    #: How the prompt compiler writes for this model (M5.1): gemini, seedance,
    #: diffusion or generic. Default: by provider.
    prompt_profile: Literal["gemini", "seedance", "diffusion", "generic"] | None = None
    fixtures: list[Fixture] = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not _ID.fullmatch(value):
            raise ValueError("use lowercase letters, digits, '-' and '.'")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> ModelSpec:
        if self.runtime == "api" and self.pricing is None:
            raise ValueError("hosted models need 'pricing' for the cost estimate")
        if self.pricing and self.pricing.by and self.pricing.by not in self.params:
            raise ValueError(f"pricing.by names unknown parameter '{self.pricing.by}'")
        for name, param in self.params.items():
            for mode in param.modes or []:
                if mode not in self.modes:
                    raise ValueError(f"parameter '{name}' is for mode '{mode}' the model lacks")
        for rule in self.rules:
            for name, value in [*rule.when.items(), *((k, None) for k in rule.allow)]:
                if name not in self.params:
                    raise ValueError(f"rule '{rule.reason}' names unknown parameter '{name}'")
                if value is not None:
                    for item in value if isinstance(value, list) else [value]:
                        problem = check_value(self.params[name], item)
                        if problem:
                            raise ValueError(f"rule '{rule.reason}': {name}={item!r} {problem}")
        known = INPUT_KEYS | set(self.params)
        for name in self.provider_inputs:
            if name not in known:
                raise ValueError(f"provider_inputs names '{name}', which is no input or parameter")
        for fixture in self.fixtures:
            try:
                validate_params(self, fixture.mode, fixture.params)
            except ParamError as exc:
                raise ValueError(f"fixture for {fixture.mode} is refused: {exc}") from exc
        return self

    def params_for(self, mode: Mode) -> dict[str, ParamSpec]:
        return {k: p for k, p in self.params.items() if p.modes is None or mode in p.modes}


# ------------------------------------------------------------- validation


class ParamError(ValueError):
    """Parameters the model refuses; ``problems`` names each one."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def check_value(param: ParamSpec, value: Any) -> str | None:
    """Why ``value`` is not acceptable for ``param``, or ``None``."""
    if param.type == "enum":
        return None if value in (param.values or []) else f"is not one of {param.values}"
    if param.type == "boolean":
        return None if isinstance(value, bool) else "must be true or false"
    if param.type == "string":
        return None if isinstance(value, str) else "must be text"
    if isinstance(value, bool) or not isinstance(value, int | float):
        return "must be a number"
    if param.type in {"integer", "seed"} and not float(value).is_integer():
        return "must be a whole number"
    if param.type == "seed" and not 0 <= value <= 2**31 - 1:
        return "must be between 0 and 2147483647"
    if param.min is not None and value < param.min:
        return f"must be at least {param.min:g}"
    if param.max is not None and value > param.max:
        return f"must be at most {param.max:g}"
    return None


def _matches(value: Any, condition: Any) -> bool:
    return value in condition if isinstance(condition, list) else value == condition


def _allowed(value: Any, allow: list[Any] | dict[str, float]) -> bool:
    if isinstance(allow, list):
        return value in allow
    low, high = allow.get("min"), allow.get("max")
    return (low is None or value >= low) and (high is None or value <= high)


def validate_params(spec: ModelSpec, mode: str, params: dict[str, Any]) -> dict[str, Any]:
    """Check ``params`` for ``mode`` and return them with defaults filled in."""
    if mode not in spec.modes:
        raise ParamError([f"{spec.name} does not make {mode}s"])
    allowed = spec.params_for(mode)  # type: ignore[arg-type]
    problems = [
        f"'{name}' is not a parameter of {spec.name}" for name in params if name not in allowed
    ]
    resolved: dict[str, Any] = {}
    for name, param in allowed.items():
        if name in params and params[name] is not None:
            problem = check_value(param, params[name])
            if problem:
                problems.append(f"{param.label} {problem}")
                continue
            value = params[name]
            resolved[name] = int(value) if param.type in {"integer", "seed"} else value
        elif param.default_for(mode) is not None:
            resolved[name] = param.default_for(mode)
    for rule in spec.rules:
        if all(
            name in resolved and _matches(resolved[name], cond) for name, cond in rule.when.items()
        ):
            for name, allow in rule.allow.items():
                if name in resolved and not _allowed(resolved[name], allow):
                    problems.append(rule.reason)
    if problems:
        raise ParamError(problems)
    return resolved


def to_json_schema(spec: ModelSpec, mode: Mode) -> dict[str, Any]:
    """The parameters as JSON Schema (rules become if/then), for agents and tools."""
    properties: dict[str, Any] = {}
    for name, param in spec.params_for(mode).items():
        schema: dict[str, Any] = {"title": param.label}
        if param.type == "enum":
            schema["enum"] = param.values
        elif param.type == "boolean":
            schema["type"] = "boolean"
        elif param.type == "string":
            schema["type"] = "string"
        else:
            schema["type"] = "integer" if param.type in {"integer", "seed"} else "number"
            if param.type == "seed":
                schema |= {"minimum": 0, "maximum": 2**31 - 1}
            if param.min is not None:
                schema["minimum"] = param.min
            if param.max is not None:
                schema["maximum"] = param.max
        if param.default_for(mode) is not None:
            schema["default"] = param.default_for(mode)
        if param.help:
            schema["description"] = param.help
        properties[name] = schema
    clauses = []
    for rule in spec.rules:
        if not all(name in properties for name in [*rule.when, *rule.allow]):
            continue
        condition = {
            name: {"enum": cond} if isinstance(cond, list) else {"const": cond}
            for name, cond in rule.when.items()
        }
        then: dict[str, Any] = {}
        for name, allow in rule.allow.items():
            if isinstance(allow, list):
                then[name] = {"enum": allow}
            else:
                then[name] = {
                    k: v
                    for k, v in (("minimum", allow.get("min")), ("maximum", allow.get("max")))
                    if v is not None
                }
        clauses.append(
            {
                "if": {"properties": condition, "required": list(rule.when)},
                "then": {"properties": then},
                "description": rule.reason,
            }
        )
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
    }
    if clauses:
        schema["allOf"] = clauses
    return schema


def estimate_cost(
    spec: ModelSpec, mode: str, params: dict[str, Any], batch: int = 1
) -> float | None:
    """Estimated price in USD for one request, or ``None`` for local models."""
    pricing = spec.pricing
    if pricing is None:
        return None
    unit_price = pricing.usd
    if pricing.by and pricing.table:
        unit_price = pricing.table.get(str(params.get(pricing.by)), pricing.usd)
    units = 1.0
    if pricing.unit == "video_second":
        units = float(params.get("duration", 1))
    return round(unit_price * units * batch, 4)


# ----------------------------------------------------------------- loading


class CatalogError(ValueError):
    """A catalog that does not load; the message names file, model and field."""


class Catalog:
    def __init__(self, models: Iterable[ModelSpec]) -> None:
        self._models = {model.id: model for model in models}
        #: Problems with entries in the user's own catalog folder (they are skipped).
        self.warnings: list[str] = []

    def __contains__(self, model_id: object) -> bool:
        return model_id in self._models

    def get(self, model_id: str) -> ModelSpec | None:
        return self._models.get(model_id)

    def all(self) -> list[ModelSpec]:
        return list(self._models.values())

    def add(self, model: ModelSpec) -> None:
        self._models[model.id] = model


def _describe(error: ValidationError, file: str, model_id: str) -> str:
    lines = []
    for item in error.errors():
        where = ".".join(str(part) for part in item["loc"]) or "(entry)"
        lines.append(f"  {file} › {model_id} › {where}: {item['msg']}")
    return "\n".join(lines)


def _read(directory: Path, seen: dict[str, str]) -> tuple[list[ModelSpec], list[str]]:
    problems: list[str] = []
    models: list[ModelSpec] = []
    for path in sorted(directory.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            problems.append(f"  {path.name}: not valid YAML ({exc})")
            continue
        entries = data if isinstance(data, list) else [data]
        for entry in entries:
            model_id = entry.get("id", "?") if isinstance(entry, dict) else "?"
            try:
                model = ModelSpec.model_validate(entry)
            except ValidationError as exc:
                problems.append(_describe(exc, path.name, str(model_id)))
                continue
            if model.id in seen:
                problems.append(f"  {path.name} › {model.id}: id already used in {seen[model.id]}")
                continue
            seen[model.id] = path.name
            models.append(model)
    return models, problems


def load_catalog(directory: Path = CATALOG_DIR, user_dir: Path | None = None) -> Catalog:
    """Load every ``*.yaml`` under ``directory`` (one file may hold one entry
    or a list), then the user's own entries from ``user_dir`` (models imported
    from an aggregator, M2.10). Built-in problems are errors; a broken user
    entry is skipped and reported in ``Catalog.warnings``."""
    seen: dict[str, str] = {}
    models, problems = _read(directory, seen)
    if problems:
        raise CatalogError("The model catalog has problems:\n" + "\n".join(problems))
    catalog = Catalog(models)
    if user_dir is not None and user_dir.is_dir():
        extra, warnings = _read(user_dir, seen)
        for model in extra:
            catalog.add(model)
        catalog.warnings = warnings
    return catalog
