import { useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { Bot, Download, LayoutPanelTop, ScanText, Trash2, Workflow, X } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { $api, type AssetOut, errorMessage, fetchClient, type Schemas } from "@/api/client";
import { Button, buttonVariants } from "@/components/ui/button";
import { Field, inputClass } from "@/components/ui/misc";
import { ElementsPage, type ElementsSearch } from "@/features/elements/ElementsPage";
import { Composer } from "@/features/generate/Composer";
import { useComposerStore } from "@/features/generate/store";
import { invalidateLibrary, type ProjectDetail, useFolders } from "@/features/library/api";
import { LibraryView } from "@/features/library/LibraryView";
import { cn } from "@/lib/utils";
import { NotFound } from "@/pages/LibraryPages";
import { BriefEditor } from "./Brief";
import { InProgress } from "./InProgress";
import { projectExportUrl } from "./projects";

export const WORKSPACE_TABS = ["assets", "brief", "settings", "elements", "trash", "storyboard", "canvas"] as const;
export type WorkspaceTab = (typeof WORKSPACE_TABS)[number];

export interface ProjectSearch {
  tab?: WorkspaceTab;
  folder?: string;
  view?: string;
  q?: string;
  /** The Director panel on the right. */
  director?: boolean;
}

type SetSearch = (patch: Partial<ProjectSearch>) => void;
type Save = (body: Schemas["ProjectPatch"]) => Promise<boolean>;

/** Room under the grid for the floating composer. */
const COMPOSER_SPACE = 260;

function ProjectAssets({
  project,
  search,
  setSearch,
  save,
}: {
  project: ProjectDetail;
  search: ProjectSearch;
  setSearch: SetSearch;
  save: Save;
}) {
  const { t } = useTranslation();
  const { data: folders = [] } = useFolders(project.id);
  const mode = useComposerStore((s) => s.studioMode);
  const setMode = useComposerStore((s) => s.setStudioMode);
  const [selecting, setSelecting] = useState(false);
  const folder = folders.find((f) => f.id === search.folder);
  const setCover = (asset: AssetOut) =>
    void save({ cover_asset_id: asset.id }).then((ok) => ok && toast.success(t("projects.coverSet")));
  return (
    <>
      <LibraryView
        key={`${project.id}:${search.folder ?? "all"}`}
        title={folder?.name ?? t("library.allAssets")}
        pageKey={`project:${project.id}`}
        scope={{ projectId: project.id, folderId: search.folder }}
        query={search.q ?? ""}
        onQuery={(q) => setSearch({ q: q || undefined })}
        view={search.view}
        onView={(view) => setSearch({ view })}
        onSetCover={setCover}
        exportHref={projectExportUrl(project.id, folder?.id)}
        banner={<InProgress projectId={project.id} />}
        bottomSpace={COMPOSER_SPACE}
        onSelecting={setSelecting}
      />
      {selecting ? null : (
        <div className="pointer-events-none absolute inset-x-0 bottom-0 z-10 px-4 pb-4">
          <div className="pointer-events-auto mx-auto max-w-4xl" data-testid="workspace-composer">
            <Composer mode={mode} onMode={setMode} projectId={project.id} folderId={folder?.id} />
          </div>
        </div>
      )}
    </>
  );
}

/** Name, description, slug, cover, captions and the trash (M5.6). */
function ProjectSettings({ project, save }: { project: ProjectDetail; save: Save }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [name, setName] = useState(project.name);
  const [description, setDescription] = useState(project.description);
  const [slug, setSlug] = useState(project.slug);
  const slugValid = /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug);

  async function trash() {
    await fetchClient.POST("/api/projects/{project_id}/trash", { params: { path: { project_id: project.id } } });
    toast.success(t("projects.trashed", { name: project.name }));
    await invalidateLibrary(queryClient);
    void navigate({ to: "/studio" });
  }

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-6 px-6 py-8" data-testid="project-settings">
      <h2 className="font-display text-2xl font-bold uppercase">{t("studio.settings")}</h2>
      <Field label={t("projects.name")}>
        <input
          className={inputClass}
          value={name}
          maxLength={120}
          onChange={(e) => setName(e.target.value)}
          onBlur={() => name.trim() && name !== project.name && void save({ name })}
          onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
          data-testid="project-name"
        />
      </Field>
      <Field label={t("projects.description")} hint={`${t("projects.descriptionHint")} ${description.length}/140`}>
        <input
          className={inputClass}
          value={description}
          maxLength={140}
          onChange={(e) => setDescription(e.target.value)}
          onBlur={() => description !== project.description && void save({ description })}
          onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
        />
      </Field>
      <Field label={t("projects.slug")} hint={t("studio.slugHint")}>
        <input
          className={cn(inputClass, "font-mono", !slugValid && "border-danger")}
          value={slug}
          maxLength={60}
          aria-invalid={!slugValid}
          onChange={(e) => setSlug(e.target.value.toLowerCase())}
          onBlur={() => {
            if (slugValid && slug !== project.slug) void save({ slug }).then((ok) => !ok && setSlug(project.slug));
          }}
          onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
          data-testid="project-slug"
        />
      </Field>
      <div className="flex flex-col gap-2">
        <span className="text-sm font-medium">{t("studio.cover")}</span>
        <div className="flex items-center gap-3">
          <div className="aspect-video w-40 overflow-hidden rounded-lg border border-line bg-raised">
            {project.cover_url ? <img src={project.cover_url} alt="" className="size-full object-cover" /> : null}
          </div>
          <p className="text-xs text-muted">{t("studio.coverHint")}</p>
        </div>
      </div>
      <div className="flex flex-col gap-2">
        <button
          type="button"
          aria-pressed={project.describe_images}
          onClick={() => void save({ describe_images: !project.describe_images })}
          className={cn(
            "inline-flex h-8 w-fit items-center gap-1.5 rounded-full border px-3 text-sm",
            project.describe_images ? "border-ink/60 bg-raised text-ink" : "border-line text-muted hover:text-ink",
          )}
          data-testid="project-describe"
        >
          <ScanText className="size-4" aria-hidden />
          {t("projects.describe")}
        </button>
        <p className="text-xs text-muted">{t("projects.describeHint")}</p>
      </div>
      <div className="flex flex-col gap-2">
        <a
          href={projectExportUrl(project.id)}
          download
          className={cn(buttonVariants({ variant: "outline", size: "sm" }), "w-fit")}
        >
          <Download />
          {t("studio.exportAll")}
        </a>
        <p className="text-xs text-muted">{t("studio.exportHint", { slug: project.slug })}</p>
      </div>
      <div className="flex flex-col gap-2 border-t border-line pt-5">
        <Button size="sm" variant="danger" className="w-fit" onClick={() => void trash()} data-testid="trash-project">
          <Trash2 />
          {t("projects.trash")}
        </Button>
        <p className="text-xs text-muted">{t("studio.trashHint")}</p>
      </div>
    </div>
  );
}

/** The project's elements (M4.1), without leaving the workspace. */
function ProjectElements({ projectId }: { projectId: string }) {
  const [search, setSearch] = useState<ElementsSearch>({ scope: projectId });
  return (
    <ElementsPage
      search={{ ...search, scope: projectId }}
      setSearch={(patch) => setSearch((prev) => ({ ...prev, ...patch }))}
      fixedScope
    />
  );
}

/** A tool that arrives with a later step. */
function ToolPlaceholder({ tool }: { tool: "storyboard" | "canvas" }) {
  const { t } = useTranslation();
  const Icon = tool === "storyboard" ? LayoutPanelTop : Workflow;
  return (
    <div className="mx-auto flex max-w-xl flex-col items-start gap-3 px-6 py-16" data-testid={`tool-${tool}`}>
      <Icon className="size-8 text-muted" aria-hidden />
      <h2 className="font-display text-2xl font-bold uppercase">{t(`studio.${tool}`)}</h2>
      <p className="text-muted">{t(`studio.${tool}Soon`)}</p>
    </div>
  );
}

/** The Director's place on the right (its chat arrives with M6). */
function DirectorPanel({ projectId, onClose }: { projectId: string; onClose: () => void }) {
  const { t } = useTranslation();
  return (
    <aside
      aria-label={t("studio.director")}
      className="flex w-80 shrink-0 flex-col border-l border-line bg-flat"
      data-testid="director-panel"
    >
      <div className="flex items-center gap-2 border-b border-line px-4 py-3">
        <Bot className="size-4 text-tungsten" aria-hidden />
        <h2 className="font-display text-lg font-semibold uppercase tracking-wide">{t("studio.director")}</h2>
        <Button variant="ghost" size="icon" className="ml-auto" aria-label={t("shot.close")} onClick={onClose}>
          <X />
        </Button>
      </div>
      <div className="flex flex-col gap-3 p-4 text-sm">
        <p>{t("studio.directorSoon")}</p>
        <p className="text-muted">{t("studio.directorReady")}</p>
        <div className="flex flex-wrap gap-2">
          <Link
            to="/studio/$projectId"
            params={{ projectId }}
            search={(prev) => ({ ...prev, tab: "brief" as const })}
            className={buttonVariants({ variant: "outline", size: "sm" })}
          >
            {t("shell.brief")}
          </Link>
          <Link
            to="/studio/$projectId"
            params={{ projectId }}
            search={(prev) => ({ ...prev, tab: "elements" as const })}
            className={buttonVariants({ variant: "outline", size: "sm" })}
          >
            {t("nav.elements")}
          </Link>
        </div>
      </div>
    </aside>
  );
}

/** A project's workspace (M5.6, wireframe "Studio project workspace"): the
 * sidebar (in the shell) picks the page; assets get the floating composer;
 * the Director sits on the right. */
export function ProjectWorkspace({
  projectId,
  search,
  setSearch,
}: {
  projectId: string;
  search: ProjectSearch;
  setSearch: SetSearch;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { data: project, isError } = $api.useQuery("get", "/api/projects/{project_id}", {
    params: { path: { project_id: projectId } },
  });
  if (isError) return <NotFound message={t("projects.notFound")} />;
  if (!project) return null;

  const save: Save = async (body) => {
    const { error } = await fetchClient.PATCH("/api/projects/{project_id}", {
      params: { path: { project_id: projectId } },
      body,
    });
    if (error) toast.error(errorMessage(error));
    await invalidateLibrary(queryClient);
    return !error;
  };
  const tab = search.tab ?? "assets";

  return (
    <div className="flex h-full min-h-0">
      <h1 className="sr-only">{project.name}</h1>
      <div className="relative flex min-w-0 flex-1 flex-col" data-testid="workspace" data-tab={tab}>
        {tab === "assets" ? (
          <ProjectAssets project={project} search={search} setSearch={setSearch} save={save} />
        ) : tab === "brief" ? (
          <div className="min-h-0 flex-1 overflow-auto">
            <BriefEditor key={project.id} project={project} />
          </div>
        ) : tab === "settings" ? (
          <div className="min-h-0 flex-1 overflow-auto">
            <ProjectSettings
              key={`${project.name}\u0000${project.description}\u0000${project.slug}`}
              project={project}
              save={save}
            />
          </div>
        ) : tab === "elements" ? (
          <ProjectElements projectId={project.id} />
        ) : tab === "trash" ? (
          <LibraryView
            title={t("library.trash")}
            pageKey={`project-trash:${project.id}`}
            scope={{ projectId: project.id, trashed: true }}
            query={search.q ?? ""}
            onQuery={(q) => setSearch({ q: q || undefined })}
            view={search.view}
            onView={(view) => setSearch({ view })}
          />
        ) : (
          <ToolPlaceholder tool={tab} />
        )}
      </div>
      {search.director ? (
        <DirectorPanel projectId={project.id} onClose={() => setSearch({ director: undefined })} />
      ) : null}
    </div>
  );
}
