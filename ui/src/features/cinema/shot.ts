import { $api, type Schemas } from "@/api/client";

export type ShotSpec = Schemas["ShotSpec"];
export type FilmSpec = Schemas["FilmSpec"];
export type CameraSpec = Schemas["CameraSpec"];
export type VocabOut = Schemas["VocabOut"];
export type Term = Schemas["Term"];
export type Preview = Schemas["Preview"];

/** The cinema vocabulary: every picker choice (it never changes while the app runs). */
export function useVocab() {
  return $api.useQuery("get", "/api/cinema/vocab", {}, { staleTime: Number.POSITIVE_INFINITY });
}

function compact<T extends object>(value: T | null | undefined): T | undefined {
  if (!value) return undefined;
  const kept = Object.fromEntries(Object.entries(value).filter(([, v]) => v !== null && v !== undefined && v !== ""));
  return Object.keys(kept).length ? (kept as T) : undefined;
}

/** A shot without empty parts, or null when nothing is chosen. */
export function cleanShot(shot: Partial<ShotSpec> | null | undefined): ShotSpec | null {
  if (!shot) return null;
  const film = compact(shot.film);
  const camera = compact(shot.camera);
  const moves = shot.moves?.length ? shot.moves : undefined;
  const palette = compact(shot.palette);
  const lighting = compact(shot.lighting);
  const out = { film, camera, moves, palette, lighting };
  const kept = Object.fromEntries(Object.entries(out).filter(([, v]) => v !== undefined));
  return Object.keys(kept).length ? (kept as ShotSpec) : null;
}

export function withFilm(shot: ShotSpec | null | undefined, patch: Partial<FilmSpec>): ShotSpec | null {
  return cleanShot({ ...shot, film: { ...shot?.film, ...patch } });
}

export function withCamera(shot: ShotSpec | null | undefined, patch: Partial<CameraSpec>): ShotSpec | null {
  return cleanShot({ ...shot, camera: { ...shot?.camera, ...patch } });
}

const labelOf = (items: Term[] | undefined, id: string | null | undefined) =>
  id ? (items?.find((t) => t.id === id)?.label ?? id) : null;

/** "Noir · 1970s · Calm" (pace only for video). */
export function filmSummary(shot: ShotSpec | null | undefined, vocab: VocabOut | undefined, video: boolean): string[] {
  const film = shot?.film;
  return [
    labelOf(vocab?.genres, film?.genre),
    labelOf(vocab?.eras, film?.era),
    video ? labelOf(vocab?.tempos, film?.tempo) : null,
  ].filter((v): v is string => Boolean(v));
}

/** "35mm film · Anamorphic · 50mm · f/1.4". */
export function cameraSummary(shot: ShotSpec | null | undefined, vocab: VocabOut | undefined): string[] {
  const camera = shot?.camera;
  return [
    labelOf(vocab?.bodies, camera?.body),
    labelOf(vocab?.lenses, camera?.lens),
    camera?.focal_length ? `${camera.focal_length}mm` : null,
    camera?.aperture ?? null,
  ].filter((v): v is string => Boolean(v));
}
