import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

interface ShellState {
  sidebarOpen: boolean;
  inspectorOpen: boolean;
  paletteOpen: boolean;
  toggleSidebar: () => void;
  toggleInspector: () => void;
  setPaletteOpen: (open: boolean) => void;
}

/** Layout state, remembered per browser (a convenience, never required). */
export const useShellStore = create<ShellState>()(
  persist(
    (set) => ({
      sidebarOpen: true,
      inspectorOpen: false,
      paletteOpen: false,
      toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),
      toggleInspector: () => set((s) => ({ inspectorOpen: !s.inspectorOpen })),
      setPaletteOpen: (paletteOpen) => set({ paletteOpen }),
    }),
    {
      name: "framestudio.shell",
      storage: createJSONStorage(() => localStorage),
      partialize: (s) => ({ sidebarOpen: s.sidebarOpen, inspectorOpen: s.inspectorOpen }),
    },
  ),
);
