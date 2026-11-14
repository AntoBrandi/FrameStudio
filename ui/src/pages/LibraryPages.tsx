import { useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { RotateCcw, Trash2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { errorMessage, fetchClient } from "@/api/client";
import { Button } from "@/components/ui/button";
import { invalidateLibrary, useFolders, useProjects, useSummary } from "@/features/library/api";
import { LibraryView } from "@/features/library/LibraryView";
import { formatBytes } from "@/lib/utils";

export interface LibrarySearch {
  folder?: string;
  view?: string;
  q?: string;
  liked?: boolean;
}

type SetSearch = (patch: Partial<LibrarySearch>) => void;

export function AssetsPage({ search, setSearch }: { search: LibrarySearch; setSearch: SetSearch }) {
  const { t } = useTranslation();
  const { data: folders = [] } = useFolders(undefined);
  const folder = folders.find((f) => f.id === search.folder);
  const title = search.liked ? t("library.favorites") : (folder?.name ?? t("library.allAssets"));
  return (
    <LibraryView
      key={`${search.folder ?? "all"}:${search.liked ? "liked" : ""}`}
      title={title}
      pageKey={search.liked ? "favorites" : "assets"}
      scope={{ folderId: search.folder, liked: search.liked }}
      query={search.q ?? ""}
      onQuery={(q) => setSearch({ q: q || undefined })}
      view={search.view}
      onView={(view) => setSearch({ view })}
    />
  );
}

export function TrashPage({ search, setSearch }: { search: LibrarySearch; setSearch: SetSearch }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { data: summary } = useSummary(undefined);
  const { data: projects = [] } = useProjects({ trashed: true });
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const nothing = (summary?.trashed ?? 0) === 0 && projects.length === 0;

  async function empty() {
    setBusy(true);
    const { data, error } = await fetchClient.POST("/api/trash/empty");
    setBusy(false);
    setConfirming(false);
    if (error || !data) return toast.error(errorMessage(error));
    toast.success(
      t("trash.done", {
        assets: data.assets_deleted,
        projects: data.projects_deleted,
        size: formatBytes(data.bytes_freed),
      }),
    );
    await invalidateLibrary(queryClient);
  }

  async function restoreProject(id: string) {
    await fetchClient.POST("/api/projects/{project_id}/restore", { params: { path: { project_id: id } } });
    await invalidateLibrary(queryClient);
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-3" data-testid="trash-controls">
        {confirming ? (
          <div
            role="alertdialog"
            aria-labelledby="empty-trash-text"
            className="flex flex-1 flex-wrap items-center gap-3 rounded-lg border border-danger/50 bg-danger/10 px-3 py-2"
          >
            <p id="empty-trash-text" className="flex-1 text-sm">
              {t("trash.confirm", { assets: summary?.trashed ?? 0, projects: projects.length })}
            </p>
            <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>
              {t("trash.cancel")}
            </Button>
            <Button
              size="sm"
              variant="danger"
              onClick={() => void empty()}
              disabled={busy}
              data-testid="confirm-empty-trash"
            >
              <Trash2 />
              {t("trash.confirmButton")}
            </Button>
          </div>
        ) : (
          <>
            <p className="flex-1 text-sm text-muted">{t("trash.projectHint")}</p>
            <Button
              size="sm"
              variant="danger"
              onClick={() => setConfirming(true)}
              disabled={nothing}
              data-testid="empty-trash"
            >
              <Trash2 />
              {t("trash.empty")}
            </Button>
          </>
        )}
      </div>
      {projects.length ? (
        <section aria-label={t("trash.projects")} className="flex flex-wrap gap-2 border-b border-line px-4 py-3">
          {projects.map((project) => (
            <span
              key={project.id}
              className="flex items-center gap-2 rounded-lg border border-line px-3 py-1.5 text-sm"
            >
              {project.name}
              <Button size="sm" variant="ghost" onClick={() => void restoreProject(project.id)}>
                <RotateCcw />
                {t("library.restore")}
              </Button>
            </span>
          ))}
        </section>
      ) : null}
      <div className="min-h-0 flex-1">
        <LibraryView
          title={t("trash.title")}
          pageKey="trash"
          scope={{ trashed: true }}
          query={search.q ?? ""}
          onQuery={(q) => setSearch({ q: q || undefined })}
          view={search.view}
          onView={(view) => setSearch({ view })}
        />
      </div>
    </div>
  );
}

export function NotFound({ message }: { message: string }) {
  return (
    <div className="mx-auto flex max-w-xl flex-col items-start gap-3 px-6 py-16">
      <p className="text-muted">{message}</p>
      <Link to="/studio" className="text-tungsten underline">
        Studio
      </Link>
    </div>
  );
}
