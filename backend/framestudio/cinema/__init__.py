"""Cinema controls (plan milestone M5): the ShotSpec and its vocabulary.

A ShotSpec holds the choices the film, camera, move, palette and lighting
pickers make. With the prompt's tokens and the model's parameters (aspect,
duration, audio) it describes a shot; the compiler (``cinema.compiler``) turns
it into each model's own request. Every choice comes from ``vocab.yaml``.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

VOCAB_FILE = Path(__file__).resolve().parent / "vocab.yaml"
Family = Literal[
    "subject", "moves", "camera", "film", "lighting", "palette", "elements", "negative"
]
#: The controls whose adherence is measured per model (M5.1).
CONTROL_FAMILIES: tuple[str, ...] = (
    "genre", "era", "tempo", "body", "lens", "focal_length", "aperture", "moves", "lighting",
    "palette",
)  # fmt: skip
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


class Term(BaseModel):
    """One choice: what the picker shows and what it becomes in a prompt."""

    model_config = ConfigDict(extra="forbid")
    id: str
    label: str
    phrase: str = ""
    tags: str = ""
    negative: str | None = None
    #: Wording for a compiler profile (e.g. "seedance") instead of ``phrase``.
    profiles: dict[str, str] = {}

    def phrase_for(self, profile: str) -> str:
        return self.profiles.get(profile, self.phrase)


class PaletteTerm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    label: str
    words: str
    swatches: list[str]
    negative: str | None = None


class Vocab(BaseModel):
    model_config = ConfigDict(extra="forbid")
    genres: list[Term]
    eras: list[Term]
    tempos: list[Term]
    bodies: list[Term]
    lenses: list[Term]
    apertures: list[str]
    focal_lengths: list[int]
    moves: list[Term]
    lighting: list[Term]
    palettes: list[PaletteTerm]

    def find(self, group: str, term_id: str) -> Term | None:
        items: list[Term] = getattr(self, group)
        return next((t for t in items if t.id == term_id), None)

    def palette(self, palette_id: str) -> PaletteTerm | None:
        return next((p for p in self.palettes if p.id == palette_id), None)

    @property
    def move_ids(self) -> set[str]:
        return {m.id for m in self.moves}


@cache
def vocab() -> Vocab:
    return Vocab.model_validate(yaml.safe_load(VOCAB_FILE.read_text(encoding="utf-8")))


def _known(group: str, value: str | None) -> str | None:
    if value is not None and vocab().find(group, value) is None:
        raise ValueError(f"unknown {group[:-1]} '{value}'")
    return value


class FilmSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    genre: str | None = None
    era: str | None = None
    #: Video only.
    tempo: str | None = None

    @field_validator("genre")
    @classmethod
    def _genre(cls, v: str | None) -> str | None:
        return _known("genres", v)

    @field_validator("era")
    @classmethod
    def _era(cls, v: str | None) -> str | None:
        return _known("eras", v)

    @field_validator("tempo")
    @classmethod
    def _tempo(cls, v: str | None) -> str | None:
        return _known("tempos", v)


class CameraSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str | None = None
    lens: str | None = None
    #: Millimetres (full-frame equivalent).
    focal_length: int | None = Field(default=None, ge=8, le=800)
    #: An f-number such as "f/1.4".
    aperture: str | None = None

    @field_validator("body")
    @classmethod
    def _body(cls, v: str | None) -> str | None:
        return _known("bodies", v)

    @field_validator("lens")
    @classmethod
    def _lens(cls, v: str | None) -> str | None:
        return _known("lenses", v)

    @field_validator("aperture")
    @classmethod
    def _aperture(cls, v: str | None) -> str | None:
        if v is not None and not re.fullmatch(r"f/\d+(\.\d+)?", v):
            raise ValueError(f"'{v}' is not an f-number such as f/2.8")
        return v


class PaletteSpec(BaseModel):
    """A preset palette by id, or a custom one (M5.4: extracted from a still)."""

    model_config = ConfigDict(extra="forbid")
    id: str | None = None
    label: str | None = Field(default=None, max_length=60)
    words: str | None = Field(default=None, max_length=200)
    swatches: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("swatches")
    @classmethod
    def _hex(cls, v: list[str]) -> list[str]:
        for swatch in v:
            if not _HEX.match(swatch):
                raise ValueError(f"'{swatch}' is not a colour such as #1F6F78")
        return [s.upper() for s in v]

    @model_validator(mode="after")
    def _some(self) -> PaletteSpec:
        if self.id is not None and vocab().palette(self.id) is None:
            raise ValueError(f"unknown palette '{self.id}'")
        if self.id is None and not (self.words or self.swatches):
            raise ValueError("a palette needs a preset id, or words or swatches")
        return self


class LightingSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preset: str | None = None
    #: Words from the rig editor (M5.5), used after the preset's.
    description: str | None = Field(default=None, max_length=400)

    @field_validator("preset")
    @classmethod
    def _preset(cls, v: str | None) -> str | None:
        return _known("lighting", v)


class ShotSpec(BaseModel):
    """The cinema controls of one shot (M5.1). The prompt's tokens, its
    elements and the model's own parameters (aspect, duration, audio) travel
    beside it in the generation request."""

    model_config = ConfigDict(extra="forbid")
    film: FilmSpec = Field(default_factory=FilmSpec)
    camera: CameraSpec = Field(default_factory=CameraSpec)
    #: Camera moves in order, after any #move tokens in the prompt.
    moves: list[str] = Field(default_factory=list, max_length=6)
    palette: PaletteSpec | None = None
    lighting: LightingSpec | None = None

    @field_validator("moves")
    @classmethod
    def _moves(cls, v: list[str]) -> list[str]:
        for move in v:
            _known("moves", move)
        return v

    def is_empty(self) -> bool:
        return self == ShotSpec()


def depth_of_field(aperture: str) -> str:
    number = float(aperture.removeprefix("f/"))
    if number <= 1.4:
        return "very shallow depth of field"
    if number <= 2.8:
        return "shallow depth of field"
    if number <= 5.6:
        return "moderate depth of field"
    return "deep focus"


def focal_character(mm: int) -> str:
    if mm <= 18:
        return "ultra-wide"
    if mm <= 35:
        return "wide"
    if mm <= 60:
        return "normal perspective"
    if mm <= 105:
        return "short telephoto"
    return "long telephoto, compressed depth"
