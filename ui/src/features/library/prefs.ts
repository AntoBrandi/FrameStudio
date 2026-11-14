import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import type { Kind } from "./api";

export interface ViewPrefs {
  mode: "masonry" | "square";
  size: number;
  groupByDay: boolean;
}

export interface FilterPrefs {
  kinds: Kind[];
  models: string[];
  from: string;
  to: string;
  liked: boolean;
  downloaded: boolean;
  /** Generation feeds only. */
  hideFailed: boolean;
}

export const DEFAULT_VIEW: ViewPrefs = { mode: "masonry", size: 240, groupByDay: true };
export const DEFAULT_FILTERS: FilterPrefs = {
  kinds: [],
  models: [],
  from: "",
  to: "",
  liked: false,
  downloaded: false,
  hideFailed: false,
};

interface PrefsState {
  views: Record<string, ViewPrefs>;
  filters: Record<string, FilterPrefs>;
  setView: (page: string, patch: Partial<ViewPrefs>) => void;
  setFilters: (page: string, patch: Partial<FilterPrefs>) => void;
  resetFilters: (page: string) => void;
}

/** Layout and filters, remembered per page in this browser. */
export const usePrefsStore = create<PrefsState>()(
  persist(
    (set) => ({
      views: {},
      filters: {},
      setView: (page, patch) =>
        set((s) => ({ views: { ...s.views, [page]: { ...DEFAULT_VIEW, ...s.views[page], ...patch } } })),
      setFilters: (page, patch) =>
        set((s) => ({ filters: { ...s.filters, [page]: { ...DEFAULT_FILTERS, ...s.filters[page], ...patch } } })),
      resetFilters: (page) => set((s) => ({ filters: { ...s.filters, [page]: DEFAULT_FILTERS } })),
    }),
    { name: "framestudio.library", storage: createJSONStorage(() => localStorage) },
  ),
);

export function usePagePrefs(page: string) {
  const view = usePrefsStore((s) => s.views[page]) ?? DEFAULT_VIEW;
  const filters = usePrefsStore((s) => s.filters[page]) ?? DEFAULT_FILTERS;
  const setView = usePrefsStore((s) => s.setView);
  const setFilters = usePrefsStore((s) => s.setFilters);
  const resetFilters = usePrefsStore((s) => s.resetFilters);
  return {
    view: { ...DEFAULT_VIEW, ...view },
    filters: { ...DEFAULT_FILTERS, ...filters },
    setView: (patch: Partial<ViewPrefs>) => setView(page, patch),
    setFilters: (patch: Partial<FilterPrefs>) => setFilters(page, patch),
    resetFilters: () => resetFilters(page),
  };
}

export function hasActiveFilters(filters: FilterPrefs): boolean {
  return (
    filters.kinds.length > 0 ||
    filters.models.length > 0 ||
    Boolean(filters.from) ||
    Boolean(filters.to) ||
    filters.liked ||
    filters.downloaded
  );
}

/** Local calendar dates → the UTC instants the API filters on. */
export function dateRange(filters: FilterPrefs): { created_after?: string; created_before?: string } {
  const range: { created_after?: string; created_before?: string } = {};
  if (filters.from) range.created_after = new Date(`${filters.from}T00:00:00`).toISOString();
  if (filters.to) {
    const end = new Date(`${filters.to}T00:00:00`);
    end.setDate(end.getDate() + 1);
    range.created_before = end.toISOString();
  }
  return range;
}
