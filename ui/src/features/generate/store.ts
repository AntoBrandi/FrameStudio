import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import type { Mode, Params } from "./params";
import type { Reference } from "./ReferenceTray";
import type { Token } from "./tokens";
import type { Schemas } from "@/api/client";

export type ShotSpec = Schemas["ShotSpec"];

/** What a generation is given besides the prompt (not remembered across restarts). */
export interface Attachments {
  references: Reference[];
  start: Reference | null;
  end: Reference | null;
}

export const NO_ATTACHMENTS: Attachments = { references: [], start: null, end: null };

/** The model choice that lets the router pick (M2.9). */
export const AUTO = "auto";

interface ComposerState {
  /** The chosen model per mode. */
  models: Partial<Record<Mode, string>>;
  /** Settings per model and mode ("model|mode"), so switching back restores them. */
  params: Record<string, Params>;
  batch: number;
  /** A fixed seed, or null for a new random one each time. */
  seed: number | null;
  /** The prompt being written, as tokens, per mode (not remembered across restarts). */
  drafts: Partial<Record<Mode, Token[]>>;
  attachments: Partial<Record<Mode, Attachments>>;
  /** Cinema controls per mode (M5): film, camera, moves, palette, lighting. */
  shots: Partial<Record<Mode, ShotSpec>>;
  /** The mode of the Studio's composer (M5.6), which switches in place. */
  studioMode: Mode;
  /** Video: make keyframe stills first, then animate the approved one (M5.7). */
  keyframeFirst: boolean;
  /** The image model for keyframes, or "auto". */
  keyframeModel: string;
  setModel: (mode: Mode, model: string) => void;
  setParams: (model: string, mode: Mode, params: Params) => void;
  setBatch: (batch: number) => void;
  setSeed: (seed: number | null) => void;
  setDraft: (mode: Mode, tokens: Token[]) => void;
  setAttachments: (mode: Mode, patch: Partial<Attachments>) => void;
  setShot: (mode: Mode, shot: ShotSpec | null) => void;
  setStudioMode: (mode: Mode) => void;
  setKeyframeFirst: (on: boolean) => void;
  setKeyframeModel: (model: string) => void;
}

export const paramsKey = (model: string, mode: Mode) => `${model}|${mode}`;

/** What the composer remembers in this browser. */
export const useComposerStore = create<ComposerState>()(
  persist(
    (set) => ({
      models: {},
      params: {},
      batch: 1,
      seed: null,
      drafts: {},
      attachments: {},
      shots: {},
      studioMode: "image",
      setStudioMode: (studioMode) => set({ studioMode }),
      keyframeFirst: false,
      keyframeModel: AUTO,
      setKeyframeFirst: (keyframeFirst) => set({ keyframeFirst }),
      setKeyframeModel: (keyframeModel) => set({ keyframeModel }),
      setModel: (mode, model) => set((s) => ({ models: { ...s.models, [mode]: model } })),
      setParams: (model, mode, params) => set((s) => ({ params: { ...s.params, [paramsKey(model, mode)]: params } })),
      setBatch: (batch) => set({ batch }),
      setSeed: (seed) => set({ seed }),
      setDraft: (mode, tokens) => set((s) => ({ drafts: { ...s.drafts, [mode]: tokens } })),
      setShot: (mode, shot) =>
        set((s) => {
          const shots = { ...s.shots };
          if (shot) shots[mode] = shot;
          else delete shots[mode];
          return { shots };
        }),
      setAttachments: (mode, patch) =>
        set((s) => ({
          attachments: { ...s.attachments, [mode]: { ...NO_ATTACHMENTS, ...s.attachments[mode], ...patch } },
        })),
    }),
    {
      name: "framestudio.composer",
      storage: createJSONStorage(() => localStorage),
      partialize: ({ models, params, batch, seed, shots, studioMode, keyframeFirst, keyframeModel }) => ({
        models,
        params,
        batch,
        seed,
        shots,
        studioMode,
        keyframeFirst,
        keyframeModel,
      }),
    },
  ),
);
