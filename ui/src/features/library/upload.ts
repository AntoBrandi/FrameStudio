import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useEffectEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { useAuthStore } from "@/features/auth/store";
import { type IngestItem, invalidateLibrary } from "./api";

export interface UploadTarget {
  projectId?: string;
  folderId?: string;
}

const CONCURRENCY = 3;

async function uploadOne(file: File, target: UploadTarget): Promise<IngestItem> {
  const form = new FormData();
  form.append("files", file, file.name);
  if (target.projectId) form.append("project_id", target.projectId);
  if (target.folderId) form.append("folder_id", target.folderId);
  try {
    const response = await fetch(`${location.origin}/api/assets/upload`, {
      method: "POST",
      body: form,
      credentials: "same-origin",
    });
    if (response.status === 401) useAuthStore.getState().setNeedsToken(true);
    if (!response.ok) {
      const body = (await response.json().catch(() => ({}))) as { detail?: string };
      return { name: file.name, error: body.detail ?? `HTTP ${response.status}`, error_code: "http" } as IngestItem;
    }
    const [item] = (await response.json()) as IngestItem[];
    return item ?? ({ name: file.name, error: "No result", error_code: "http" } as IngestItem);
  } catch (error) {
    return { name: file.name, error: String(error), error_code: "network" } as IngestItem;
  }
}

/** Upload files a few at a time; ``onProgress`` gets (finished, total). */
export async function uploadFiles(
  files: File[],
  target: UploadTarget,
  onProgress: (done: number, total: number) => void = () => undefined,
): Promise<IngestItem[]> {
  const results: IngestItem[] = new Array(files.length);
  let next = 0;
  let done = 0;
  async function worker() {
    while (next < files.length) {
      const index = next++;
      results[index] = await uploadOne(files[index]!, target);
      done += 1;
      onProgress(done, files.length);
    }
  }
  await Promise.all(Array.from({ length: Math.min(CONCURRENCY, files.length) }, worker));
  return results;
}

/** Import with a progress toast and a summary of what happened. */
export function useUploader(target: UploadTarget) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { projectId, folderId } = target;

  return useCallback(
    async (files: File[]) => {
      if (files.length === 0) return [];
      const id = toast.loading(t("library.importing", { done: 0, total: files.length }));
      const results = await uploadFiles(files, { projectId, folderId }, (done, total) =>
        toast.loading(t("library.importing", { done, total }), { id }),
      );
      const added = results.filter((r) => r.asset && !r.duplicate).length;
      const duplicates = results.filter((r) => r.duplicate).length;
      const failed = results.filter((r) => r.error);
      const summary = t("library.importSummary", { added, duplicates, failed: failed.length });
      const description = failed.map((r) => r.error).join("\n") || undefined;
      if (failed.length) toast.warning(summary, { id, description, duration: 12_000 });
      else toast.success(summary, { id });
      await invalidateLibrary(queryClient);
      return results;
    },
    [folderId, projectId, queryClient, t],
  );
}

function hasFiles(event: DragEvent): boolean {
  return Array.from(event.dataTransfer?.types ?? []).includes("Files");
}

/** Files dragged in from the desktop onto ``element``: returns whether a drag is over it. */
export function useFileDrop(element: HTMLElement | null, onFiles: (files: File[]) => void, enabled = true) {
  const [over, setOver] = useState(false);
  const deliver = useEffectEvent((files: File[]) => onFiles(files));
  useEffect(() => {
    if (!element || !enabled) return;
    let depth = 0;
    const enter = (event: DragEvent) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      depth += 1;
      setOver(true);
    };
    const overHandler = (event: DragEvent) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
    };
    const leave = (event: DragEvent) => {
      if (!hasFiles(event)) return;
      depth = Math.max(0, depth - 1);
      if (depth === 0) setOver(false);
    };
    const drop = (event: DragEvent) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      depth = 0;
      setOver(false);
      const files = Array.from(event.dataTransfer?.files ?? []);
      if (files.length) deliver(files);
    };
    element.addEventListener("dragenter", enter);
    element.addEventListener("dragover", overHandler);
    element.addEventListener("dragleave", leave);
    element.addEventListener("drop", drop);
    return () => {
      element.removeEventListener("dragenter", enter);
      element.removeEventListener("dragover", overHandler);
      element.removeEventListener("dragleave", leave);
      element.removeEventListener("drop", drop);
    };
  }, [element, enabled]);
  return over;
}

/** Images (or any files) pasted while the page is open. Ignores pastes into text fields. */
export function usePasteFiles(onFiles: (files: File[]) => void, enabled = true) {
  const deliver = useEffectEvent((files: File[]) => onFiles(files));
  useEffect(() => {
    if (!enabled) return;
    const paste = (event: ClipboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest("input, textarea, [contenteditable=true]")) return;
      const files = Array.from(event.clipboardData?.files ?? []);
      if (files.length === 0) return;
      event.preventDefault();
      const named = files.map((file, index) =>
        file.name && file.name !== "image.png"
          ? file
          : new File(
              [file],
              `pasted-${new Date().toISOString().replace(/[:.]/g, "-")}-${index}.${file.type.split("/")[1] ?? "bin"}`,
              {
                type: file.type,
              },
            ),
      );
      deliver(named);
    };
    window.addEventListener("paste", paste);
    return () => window.removeEventListener("paste", paste);
  }, [enabled]);
}
