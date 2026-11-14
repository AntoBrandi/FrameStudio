"""The prompt compiler (plan step M5.1): a shot (the prompt's subject, its
camera moves, the ShotSpec and the element descriptions) becomes the prompt a
model family reads best.

A profile per model family decides:

* **style**: sentences (Nano Banana, Seedance and other models that read
  language) or comma-separated tags (diffusion models);
* **order** of the sections (Seedance wants camera movement right after the
  subject, as its prompt guide does);
* what is **dropped first** when the prompt passes the model's limit;
* where **negatives** go: written into the prompt, a negative-prompt input,
  or left out;
* **native parameters** a control sets on models that have them.

Sections that do not apply (moves and tempo on a still) are left out, and
controls a model is measured to ignore (``cinema.adherence``) are flagged.
Every section is returned for the compiled preview.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from framestudio.catalog import ModelSpec
from framestudio.cinema import Family, ShotSpec, depth_of_field, focal_character, vocab

Style = Literal["sentences", "tags"]


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    style: Style
    order: list[Family]
    #: Dropped in this order when the prompt is too long (the subject never is).
    drop_order: list[Family]
    negatives: Literal["inline", "param", "drop"]
    #: Spell palette swatches out as hex codes.
    hex_codes: bool = False
    #: Control → (parameter, value), applied when the model has the parameter.
    native: dict[str, tuple[str, Any]] = {}
    #: Camera moves a clip follows well; more get a note (None: no limit known).
    max_moves: int | None = None


_DROP: list[Family] = ["elements", "palette", "lighting", "film", "camera", "moves"]
PROFILES: dict[str, Profile] = {
    p.id: p
    for p in [
        Profile(
            id="gemini",
            style="sentences",
            order=["subject", "camera", "film", "lighting", "palette", "moves", "elements"],
            drop_order=_DROP,
            negatives="inline",
            hex_codes=True,
        ),
        Profile(
            id="seedance",
            style="sentences",
            order=["subject", "moves", "camera", "film", "lighting", "palette", "elements"],
            drop_order=_DROP,
            negatives="drop",
            native={"moves:static": ("camera_fixed", True)},
            max_moves=2,
        ),
        Profile(
            id="diffusion",
            style="tags",
            order=["subject", "camera", "film", "lighting", "palette", "moves", "elements"],
            drop_order=_DROP,
            negatives="param",
        ),
        Profile(
            id="generic",
            style="sentences",
            order=["subject", "camera", "film", "lighting", "palette", "moves", "elements"],
            drop_order=_DROP,
            negatives="inline",
        ),
    ]
}
#: The profile of a model without its own, by provider.
BY_PROVIDER = {"google": "gemini", "byteplus": "seedance", "fal": "diffusion"}


def profile_for(spec: ModelSpec) -> Profile:
    name = spec.prompt_profile or BY_PROVIDER.get(spec.provider, "generic")
    return PROFILES.get(name, PROFILES["generic"])


class Section(BaseModel):
    family: Family
    #: The control the section comes from, for flags ("lens", "palette"…).
    controls: list[str] = []
    text: str
    #: Whether it is in the prompt sent.
    included: bool = True
    #: Why it is left out, or why it may not work (shown in the preview).
    note: str | None = None


class Compiled(BaseModel):
    prompt: str
    sections: list[Section]
    negative_prompt: str | None = None
    #: Parameters set by controls (only those the model has).
    native: dict[str, Any] = {}
    warnings: list[str] = []


def _sentence(label: str, body: str) -> str:
    body = body.strip().rstrip(".")
    return f"{label}: {body}." if label else f"{body[:1].upper()}{body[1:]}."


def _cinema_sections(
    profile: Profile, shot: ShotSpec, moves: list[str], mode: str
) -> list[Section]:
    words = vocab()
    tags = profile.style == "tags"
    out: list[Section] = []

    # Camera movement (video).
    if moves:
        terms = [words.find("moves", m) for m in moves]
        if tags:
            text = ", ".join(t.tags for t in terms if t)
        else:
            text = _sentence(
                "Camera movement", ", then ".join(t.phrase_for(profile.id) for t in terms if t)
            )
        section = Section(family="moves", controls=["moves"], text=text)
        if profile.max_moves and len(moves) > profile.max_moves:
            section.note = (
                f"This model follows {profile.max_moves} camera moves per clip best; "
                "the later ones may be ignored."
            )
        out.append(section)

    # Camera body, lens, focal length, aperture.
    camera, parts, controls = shot.camera, [], []
    if camera.body and (term := words.find("bodies", camera.body)):
        parts.append(term.tags if tags else term.phrase)
        controls.append("body")
    if camera.lens and (term := words.find("lenses", camera.lens)):
        parts.append(term.tags if tags else term.phrase)
        controls.append("lens")
    if camera.focal_length:
        mm = camera.focal_length
        parts.append(f"{mm}mm" if tags else f"{mm}mm ({focal_character(mm)})")
        controls.append("focal_length")
    if camera.aperture:
        dof = depth_of_field(camera.aperture)
        parts.append(f"{camera.aperture}, {dof}" if tags else f"{camera.aperture} ({dof})")
        controls.append("aperture")
    if parts:
        text = ", ".join(parts) if tags else _sentence("Camera", ", ".join(parts))
        out.append(Section(family="camera", controls=controls, text=text))

    # Film: genre, era, tempo.
    film, looks, controls = shot.film, [], []
    for group, value, control in (("genres", film.genre, "genre"), ("eras", film.era, "era")):
        if value and (term := words.find(group, value)):
            looks.append(term.tags if tags else term.phrase)
            controls.append(control)
    if looks:
        text = ", ".join(looks) if tags else _sentence("Look", "; ".join(looks))
        out.append(Section(family="film", controls=controls, text=text))
    if film.tempo and (term := words.find("tempos", film.tempo)):
        text = term.tags if tags else _sentence("Pace", term.phrase)
        out.append(Section(family="film", controls=["tempo"], text=text))

    # Lighting.
    if shot.lighting:
        bits = []
        if shot.lighting.preset and (term := words.find("lighting", shot.lighting.preset)):
            bits.append(term.tags if tags else term.phrase)
        if shot.lighting.description:
            bits.append(shot.lighting.description.strip().rstrip("."))
        if bits:
            text = ", ".join(bits) if tags else _sentence("Lighting", "; ".join(bits))
            out.append(Section(family="lighting", controls=["lighting"], text=text))

    # Palette.
    if shot.palette:
        preset = words.palette(shot.palette.id) if shot.palette.id else None
        text_words = shot.palette.words or (preset.words if preset else "")
        swatches = shot.palette.swatches or (preset.swatches if preset else [])
        if tags:
            text = f"{text_words} colour palette" if text_words else ""
        else:
            hexes = f" ({', '.join(swatches)})" if profile.hex_codes and swatches else ""
            text = (
                _sentence("Colour palette", f"{text_words}{hexes}") if text_words or hexes else ""
            )
        if text:
            out.append(Section(family="palette", controls=["palette"], text=text))

    if mode != "video":
        for section in out:
            if section.family == "moves" or section.controls == ["tempo"]:
                section.included = False
                section.note = "Applies to video only."
    return out


def _negatives(shot: ShotSpec) -> list[str]:
    words = vocab()
    found = []
    for group, value in (("genres", shot.film.genre), ("eras", shot.film.era)):
        if value and (term := words.find(group, value)) and term.negative:
            found.append(term.negative)
    if shot.palette and shot.palette.id:
        preset = words.palette(shot.palette.id)
        if preset and preset.negative:
            found.append(preset.negative)
    return list(dict.fromkeys(found))


def compile_prompt(
    spec: ModelSpec,
    mode: str,
    subject: str,
    *,
    shot: ShotSpec | None = None,
    token_moves: list[str] | None = None,
    descriptions: list[tuple[str, str]] | None = None,
    ignored: set[str] | None = None,
) -> Compiled:
    """The prompt for ``spec``. ``descriptions`` are (handle, line) pairs for
    the elements named; ``ignored`` the controls this model is known to ignore."""
    profile = profile_for(spec)
    shot = shot or ShotSpec()
    moves = list(dict.fromkeys([*(token_moves or []), *shot.moves]))
    sections = [Section(family="subject", text=subject.strip())]
    sections += _cinema_sections(profile, shot, moves, mode)
    if descriptions:
        sections += [
            Section(family="elements", controls=[handle], text=line)
            for handle, line in descriptions
        ]
    warnings: list[str] = []

    # Negatives: into the prompt, a negative-prompt input, or nowhere.
    negative_prompt = None
    negatives = _negatives(shot)
    if negatives:
        takes_param = "negative_prompt" in spec.provider_inputs
        how = profile.negatives
        if how == "param" and not takes_param:
            how = "drop"
        if how == "inline":
            sections.append(
                Section(family="negative", text=_sentence("Avoid", ", ".join(negatives)))
            )
        elif how == "param":
            negative_prompt = ", ".join(negatives)
            sections.append(
                Section(
                    family="negative",
                    text=negative_prompt,
                    included=False,
                    note="Sent as the negative prompt.",
                )
            )
        else:
            sections.append(
                Section(
                    family="negative",
                    text=", ".join(negatives),
                    included=False,
                    note=f"{spec.name} takes no negative prompt.",
                )
            )

    for section in sections:
        hit = [c for c in section.controls if c in (ignored or set())]
        if hit and section.included:
            section.note = f"{spec.name} tends to ignore this ({', '.join(hit).replace('_', ' ')})."

    if any(not s.included and s.note == "Applies to video only." for s in sections):
        warnings.append("Camera moves and pace apply to video; they are left out of a still.")

    def render() -> str:
        rank = {family: index for index, family in enumerate([*profile.order, "negative"])}
        live = sorted(
            (s for s in sections if s.included and s.text and s.family != "elements"),
            key=lambda s: rank.get(s.family, 99),
        )
        lines = [s.text for s in sections if s.included and s.family == "elements"]
        if profile.style == "tags":
            body = ", ".join(s.text.rstrip(".") for s in live if s.text)
        else:
            parts = [s.text for s in live]
            if parts and parts[0] and parts[0][-1] not in ".!?" and len(parts) > 1:
                parts[0] += "."
            body = " ".join(p for p in parts if p)
        if lines:
            return f"{body}\n\n" + "\n".join(lines) if body else "\n".join(lines)
        return body

    prompt = render()
    limit = spec.limits.max_prompt_chars
    if limit:
        for family in profile.drop_order:
            candidates = [s for s in sections if s.family == family and s.included]
            for section in reversed(candidates):
                if len(prompt) <= limit:
                    break
                section.included = False
                section.note = f"Left out to fit {spec.name}'s {limit}-character prompt."
                what = (
                    f"the description of @{section.controls[0]}"
                    if family == "elements"
                    else f"the {family} words"
                )
                warnings.append(
                    f"{what[:1].upper()}{what[1:]} {'was' if family == 'elements' else 'were'} "
                    f"left out to fit {spec.name}'s {limit}-character prompt."
                )
                prompt = render()
            if len(prompt) <= limit:
                break

    native = {}
    for control, (param, value) in profile.native.items():
        kind, _, term = control.partition(":")
        if kind == "moves" and term in moves and param in spec.params and mode == "video":
            native[param] = value
    return Compiled(
        prompt=prompt,
        sections=sections,
        negative_prompt=negative_prompt,
        native=native,
        warnings=warnings,
    )
