import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

interface StudioState {
  /** Failed generations hidden from a project's in-progress strip. */
  dismissed: Record<string, true>;
  dismiss: (ids: string[]) => void;
}

/** What the Studio workspace remembers in this browser (M5.6). */
export const useStudioStore = create<StudioState>()(
  persist(
    (set) => ({
      dismissed: {},
      dismiss: (ids) =>
        set((s) => {
          const dismissed = { ...s.dismissed };
          for (const id of ids) dismissed[id] = true;
          // Keep the list short: only the most recent few hundred matter.
          const keys = Object.keys(dismissed);
          for (const key of keys.slice(0, Math.max(0, keys.length - 500))) delete dismissed[key];
          return { dismissed };
        }),
    }),
    { name: "framestudio.studio", storage: createJSONStorage(() => localStorage) },
  ),
);
