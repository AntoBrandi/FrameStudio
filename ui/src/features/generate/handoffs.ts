import type { AssetOut } from "@/api/client";
import type { Mode, Params } from "./params";
import { type Reference, toReference } from "./ReferenceTray";
import { AUTO, NO_ATTACHMENTS, paramsKey, useComposerStore } from "./store";
import { type Token, tokensFromText } from "./tokens";

type Spec = NonNullable<AssetOut["generation"]>;
const promptOf = (spec: Spec): Token[] =>
  (spec.prompt_tokens as Token[] | null | undefined) ?? tokensFromText(spec.prompt);

/** Viewer hand-offs (plan step M2.9): each fills the composer of a page and
 * returns where to go. */

const thumbOf = (id: string): Reference => ({ id, thumb: `/api/assets/${id}/thumb`, name: id });

export function pageOf(mode: Mode): "/image" | "/video" {
  return mode === "image" ? "/image" : "/video";
}

/** Recreate: the same model, prompt, settings, seed and inputs as the
 * generation that made ``asset``, so Generate reproduces it. */
export function recreate(asset: AssetOut): Mode | null {
  const spec = asset.generation;
  if (!spec) return null;
  const mode = spec.mode as Mode;
  const store = useComposerStore.getState();
  store.setModel(mode, spec.model_id);
  store.setParams(spec.model_id, mode, spec.params as Params);
  store.setSeed(spec.seed ?? null);
  store.setDraft(mode, promptOf(spec));
  store.setAttachments(mode, {
    references: (spec.references ?? []).map(thumbOf),
    start: spec.start_frame ? thumbOf(spec.start_frame) : null,
    end: spec.end_frame ? thumbOf(spec.end_frame) : null,
  });
  return mode;
}

/** Use as reference: add the picture to the image composer's references.
 * Auto takes over when the chosen model takes no references. */
export function addAsReference(asset: AssetOut, modelTakesReferences: boolean): void {
  const store = useComposerStore.getState();
  const current = store.attachments.image ?? NO_ATTACHMENTS;
  if (current.references.some((r) => r.id === asset.id)) return;
  store.setAttachments("image", { references: [...current.references, toReference(asset)] });
  if (!modelTakesReferences) store.setModel("image", AUTO);
}

/** Turn to video: the picture becomes the start frame of a clip, with its
 * prompt as a starting point, and Auto picks a model that starts from images. */
export function turnToVideo(asset: AssetOut): void {
  const store = useComposerStore.getState();
  store.setAttachments("video", { ...NO_ATTACHMENTS, start: toReference(asset) });
  store.setModel("video", AUTO);
  if (!store.drafts.video?.length && asset.generation?.prompt) store.setDraft("video", promptOf(asset.generation));
}

export { paramsKey };
