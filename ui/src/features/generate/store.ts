import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import type { Mode, Params } from "./params";
import type { Reference } from "./ReferenceTray";

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
  /** The prompt being written, per mode (not remembered across restarts). */
  drafts: Partial<Record<Mode, string>>;
  attachments: Partial<Record<Mode, Attachments>>;
  setModel: (mode: Mode, model: string) => void;
  setParams: (model: string, mode: Mode, params: Params) => void;
  setBatch: (batch: number) => void;
  setSeed: (seed: number | null) => void;
  setDraft: (mode: Mode, text: string) => void;
  setAttachments: (mode: Mode, patch: Partial<Attachments>) => void;
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
      setModel: (mode, model) => set((s) => ({ models: { ...s.models, [mode]: model } })),
      setParams: (model, mode, params) => set((s) => ({ params: { ...s.params, [paramsKey(model, mode)]: params } })),
      setBatch: (batch) => set({ batch }),
      setSeed: (seed) => set({ seed }),
      setDraft: (mode, text) => set((s) => ({ drafts: { ...s.drafts, [mode]: text } })),
      setAttachments: (mode, patch) =>
        set((s) => ({
          attachments: { ...s.attachments, [mode]: { ...NO_ATTACHMENTS, ...s.attachments[mode], ...patch } },
        })),
    }),
    {
      name: "framestudio.composer",
      storage: createJSONStorage(() => localStorage),
      partialize: ({ models, params, batch, seed }) => ({ models, params, batch, seed }),
    },
  ),
);
