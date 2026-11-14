import { type QueryClient, useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { $api, type AssetOut, errorMessage, fetchClient, type Schemas } from "@/api/client";
import { libraryFilter, refresh } from "@/api/events";
import type { paths } from "@/api/schema";

export type AssetQuery = NonNullable<paths["/api/assets"]["get"]["parameters"]["query"]>;
export type AssetPage = Schemas["AssetPage"];
export type FolderOut = Schemas["FolderOut"];
export type ProjectOut = Schemas["ProjectOut"];
export type ProjectDetail = Schemas["ProjectDetail"];
export type IngestItem = Schemas["IngestItem"];
export type Kind = AssetOut["kind"];

/** Refetch everything the library shows (lists, counts, summaries). */
export function invalidateLibrary(queryClient: QueryClient): Promise<void> {
  return refresh(queryClient, libraryFilter);
}

/** Assets page by page (cursor pagination), newest first or by relevance. */
export function useAssetPages(query: Omit<AssetQuery, "cursor">) {
  return useInfiniteQuery({
    queryKey: ["get", "/api/assets", { params: { query } }],
    queryFn: async ({ pageParam, signal }) => {
      const { data, error } = await fetchClient.GET("/api/assets", {
        params: { query: { ...query, ...(pageParam ? { cursor: pageParam } : {}) } },
        signal,
      });
      if (error) throw error;
      return data;
    },
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor ?? null,
    staleTime: 15_000,
  });
}

export function useSummary(projectId?: string) {
  return $api.useQuery("get", "/api/assets/summary", {
    params: { query: projectId ? { project_id: projectId } : {} },
  });
}

export function useFolders(projectId?: string) {
  return $api.useQuery("get", "/api/folders", { params: { query: projectId ? { project_id: projectId } : {} } });
}

export function useProjects(query: { q?: string; sort?: "updated" | "created" | "name"; trashed?: boolean } = {}) {
  return $api.useQuery("get", "/api/projects", { params: { query } });
}

export interface MoveTarget {
  folder_id?: string | null;
  project_id?: string | null;
}

/** Library mutations with cache refresh and an Undo where it makes sense. */
export function useLibraryActions() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const done = useCallback(() => invalidateLibrary(queryClient), [queryClient]);

  const fail = useCallback((error: unknown) => toast.error(errorMessage(error)), []);

  const restore = useCallback(
    async (ids: string[], quiet = false) => {
      const { error } = await fetchClient.POST("/api/assets/restore", { body: { asset_ids: ids } });
      if (error) return fail(error);
      if (!quiet) toast.success(t("library.restored", { count: ids.length }));
      await done();
    },
    [done, fail, t],
  );

  const trash = useCallback(
    async (ids: string[]) => {
      const { error } = await fetchClient.POST("/api/assets/trash", { body: { asset_ids: ids } });
      if (error) return fail(error);
      toast.success(t("library.trashed", { count: ids.length }), {
        action: { label: t("library.undo"), onClick: () => void restore(ids, true) },
      });
      await done();
    },
    [done, fail, restore, t],
  );

  const move = useCallback(
    async (ids: string[], target: MoveTarget) => {
      const { error } = await fetchClient.POST("/api/assets/move", {
        body: { asset_ids: ids, folder_id: target.folder_id ?? null, project_id: target.project_id ?? null },
      });
      if (error) return fail(error);
      toast.success(t("library.moved", { count: ids.length }));
      await done();
    },
    [done, fail, t],
  );

  const favorite = useCallback(
    async (ids: string[], value: boolean) => {
      const { error } = await fetchClient.POST("/api/assets/favorite", {
        body: { asset_ids: ids, favorite: value },
      });
      if (error) return fail(error);
      await done();
    },
    [done, fail],
  );

  const patch = useCallback(
    async (id: string, body: Schemas["AssetPatch"]) => {
      const { data, error } = await fetchClient.PATCH("/api/assets/{asset_id}", {
        params: { path: { asset_id: id } },
        body,
      });
      if (error) {
        fail(error);
        return undefined;
      }
      await done();
      return data;
    },
    [done, fail],
  );

  return useMemo(() => ({ trash, restore, move, favorite, patch }), [favorite, move, patch, restore, trash]);
}

/** Flatten loaded pages into one list. */
export function flattenPages(pages: AssetPage[] | undefined): AssetOut[] {
  if (!pages) return [];
  const seen = new Set<string>();
  const items: AssetOut[] = [];
  for (const page of pages) {
    for (const item of page.items) {
      if (!seen.has(item.id)) {
        seen.add(item.id);
        items.push(item);
      }
    }
  }
  return items;
}

/** A short human name for an asset. */
export function assetName(asset: AssetOut, fallback: string): string {
  return asset.original_name || asset.generation?.prompt?.trim() || fallback;
}

export const ASSET_DRAG_TYPE = "application/x-framestudio-assets";
