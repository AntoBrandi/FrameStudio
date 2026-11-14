import { useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { $api, errorMessage, fetchClient, type Schemas } from "@/api/client";
import { refresh } from "@/api/events";
import { useAuthStore } from "@/features/auth/store";

export type ElementOut = Schemas["ElementOut"];
export type ElementCreate = Schemas["ElementCreate"];
export type ElementPatch = Schemas["ElementPatch"];
export type ElementFolderOut = Schemas["ElementFolderOut"];
export type ImportResult = Schemas["ImportResult"];
export type Category = ElementOut["category"];
export type ElementStatus = ElementOut["status"];

export const CATEGORIES: Category[] = ["character", "location", "prop", "auto"];

export interface ElementQuery {
  project_id?: string;
  include_global?: boolean;
  category?: Category;
  status?: ElementStatus;
  folder_id?: string;
  q?: string;
}

export function useElements(query: ElementQuery) {
  return $api.useQuery("get", "/api/elements", { params: { query } });
}

export function useElement(id: string | undefined) {
  return $api.useQuery(
    "get",
    "/api/elements/{element_id}",
    { params: { path: { element_id: id ?? "" } } },
    { enabled: Boolean(id) },
  );
}

export function useElementFolders(projectId: string | undefined) {
  return $api.useQuery("get", "/api/elements/folders", {
    params: { query: projectId ? { project_id: projectId } : {} },
  });
}

const refreshElements = (queryClient: ReturnType<typeof useQueryClient>) =>
  refresh(queryClient, {
    predicate: (query) => typeof query.queryKey[1] === "string" && query.queryKey[1].startsWith("/api/elements"),
  });

/** Where an export is downloaded from (a plain link: the session cookie signs it). */
export function exportUrl(projectId: string | undefined, format: "zip" | "csv"): string {
  const params = new URLSearchParams({ format });
  if (projectId) params.set("project_id", projectId);
  return `/api/elements/export?${params.toString()}`;
}

/** Element and folder changes, with a toast on failure and a cache refresh. */
export function useElementActions() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const done = useCallback(() => refreshElements(queryClient), [queryClient]);

  const create = useCallback(
    async (body: ElementCreate) => {
      const { data, error } = await fetchClient.POST("/api/elements", { body });
      if (error || !data) {
        toast.error(errorMessage(error));
        return undefined;
      }
      await done();
      return data;
    },
    [done],
  );

  const patch = useCallback(
    async (id: string, body: ElementPatch) => {
      const { data, error } = await fetchClient.PATCH("/api/elements/{element_id}", {
        params: { path: { element_id: id } },
        body,
      });
      if (error || !data) {
        toast.error(errorMessage(error));
        return undefined;
      }
      await done();
      return data;
    },
    [done],
  );

  const remove = useCallback(
    async (element: ElementOut) => {
      const { error } = await fetchClient.DELETE("/api/elements/{element_id}", {
        params: { path: { element_id: element.id } },
      });
      if (error) return toast.error(errorMessage(error));
      toast.success(t("elements.deleted", { name: element.name }));
      await done();
    },
    [done, t],
  );

  /** Set an element's media: order (first is primary), attach, detach. */
  const setMedia = useCallback(
    async (id: string, assetIds: string[]) => {
      const { data, error } = await fetchClient.PUT("/api/elements/{element_id}/media", {
        params: { path: { element_id: id } },
        body: { asset_ids: assetIds },
      });
      if (error) toast.error(errorMessage(error));
      await done();
      return data;
    },
    [done],
  );

  const createFolder = useCallback(
    async (name: string, projectId: string | undefined) => {
      const { data, error } = await fetchClient.POST("/api/elements/folders", {
        body: { name, project_id: projectId ?? null },
      });
      if (error) toast.error(errorMessage(error));
      await done();
      return data;
    },
    [done],
  );

  const deleteFolder = useCallback(
    async (id: string) => {
      const { error } = await fetchClient.DELETE("/api/elements/folders/{folder_id}", {
        params: { path: { folder_id: id } },
      });
      if (error) toast.error(errorMessage(error));
      await done();
    },
    [done],
  );

  /** Import a CSV or export zip; reports what was created, renamed or missing. */
  const importFile = useCallback(
    async (file: File, projectId: string | undefined) => {
      const form = new FormData();
      form.append("file", file, file.name);
      if (projectId) form.append("project_id", projectId);
      const id = toast.loading(t("elements.importing", { name: file.name }));
      const response = await fetch(`${location.origin}/api/elements/import`, {
        method: "POST",
        body: form,
        credentials: "same-origin",
      }).catch((error: unknown) => error);
      if (!(response instanceof Response)) return void toast.error(String(response), { id });
      if (response.status === 401) useAuthStore.getState().setNeedsToken(true);
      const body = (await response.json().catch(() => ({}))) as ImportResult & { detail?: unknown };
      if (!response.ok) return void toast.error(errorMessage(body), { id });
      const renamed = Object.keys(body.renamed).length;
      const summary = t("elements.imported", { count: body.created.length, renamed });
      if (body.problems.length) {
        toast.warning(summary, { id, description: body.problems.join("\n"), duration: 12_000 });
      } else {
        toast.success(summary, { id });
      }
      await refresh(queryClient);
      return body;
    },
    [queryClient, t],
  );

  return { create, patch, remove, setMedia, createFolder, deleteFolder, importFile };
}
