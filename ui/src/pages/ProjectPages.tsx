import { useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { Film, Plus, ScanText, Trash2 } from "lucide-react";
import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { $api, type AssetOut, errorMessage, fetchClient, type Schemas } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Field, inputClass } from "@/components/ui/misc";
import { invalidateLibrary, type ProjectDetail, useFolders, useProjects } from "@/features/library/api";
import { LibraryView } from "@/features/library/LibraryView";
import { cn } from "@/lib/utils";
import { NotFound } from "./LibraryPages";

export function NewProjectDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    const { data, error } = await fetchClient.POST("/api/projects", { body: { name, description } });
    if (error || !data) return toast.error(errorMessage(error));
    await invalidateLibrary(queryClient);
    onOpenChange(false);
    setName("");
    setDescription("");
    void navigate({ to: "/studio/$projectId", params: { projectId: data.id } });
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <form onSubmit={submit} className="flex flex-col gap-4">
          <DialogTitle>{t("projects.new")}</DialogTitle>
          <Field label={t("projects.name")}>
            <input
              className={inputClass}
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={120}
              required
              autoFocus
            />
          </Field>
          <Field label={t("projects.description")} hint={`${t("projects.descriptionHint")} ${description.length}/140`}>
            <input
              className={inputClass}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              maxLength={140}
            />
          </Field>
          <Button type="submit" variant="primary" disabled={!name.trim()}>
            {t("projects.create")}
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function ProjectsPage() {
  const { t } = useTranslation();
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<"updated" | "created" | "name">("updated");
  const [creating, setCreating] = useState(false);
  const { data: projects = [], isPending } = useProjects({ q: q || undefined, sort });

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6 px-6 py-10">
      <header className="flex flex-wrap items-end gap-3">
        <div className="mr-auto flex flex-col gap-1">
          <h1 className="font-display text-4xl font-bold uppercase">{t("projects.title")}</h1>
          <p className="text-muted">{t("projects.lede")}</p>
        </div>
        <input
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={t("projects.search")}
          aria-label={t("projects.search")}
          className={cn(inputClass, "w-56")}
        />
        <select
          aria-label={t("projects.sort")}
          className={inputClass}
          value={sort}
          onChange={(e) => setSort(e.target.value as typeof sort)}
        >
          {(["updated", "created", "name"] as const).map((value) => (
            <option key={value} value={value}>
              {t(`projects.sorts.${value}`)}
            </option>
          ))}
        </select>
        <Button variant="primary" onClick={() => setCreating(true)} data-testid="new-project">
          <Plus />
          {t("projects.new")}
        </Button>
      </header>
      {!isPending && projects.length === 0 ? <p className="text-muted">{t("projects.empty")}</p> : null}
      <div className="grid grid-cols-[repeat(auto-fill,minmax(16rem,1fr))] gap-4" data-testid="project-grid">
        {projects.map((project) => (
          <Link
            key={project.id}
            to="/studio/$projectId"
            params={{ projectId: project.id }}
            className="group flex flex-col overflow-hidden rounded-xl border border-line bg-flat hover:border-ink/40"
            data-testid="project-card"
          >
            <div className="aspect-video bg-raised">
              {project.cover_url ? (
                <img src={project.cover_url} alt="" className="size-full object-cover" />
              ) : (
                <div className="flex size-full items-center justify-center text-muted">
                  <Film className="size-8" aria-hidden />
                </div>
              )}
            </div>
            <div className="flex flex-col gap-0.5 p-3">
              <span className="font-medium">{project.name}</span>
              {project.description ? (
                <span className="line-clamp-2 text-sm text-muted">{project.description}</span>
              ) : null}
              <span className="font-mono text-[11px] text-muted">
                {t("projects.count", { count: project.asset_count })}
              </span>
            </div>
          </Link>
        ))}
      </div>
      <NewProjectDialog open={creating} onOpenChange={setCreating} />
    </div>
  );
}

/** Splits a brief into its "## Heading" sections and joins them back. */
export function parseBrief(markdown: string): { title: string; body: string }[] {
  const sections: { title: string; body: string }[] = [];
  let current: { title: string; lines: string[] } | null = null;
  const preamble: string[] = [];
  for (const line of markdown.split("\n")) {
    const heading = /^## (.+)$/.exec(line);
    if (heading) {
      if (current) sections.push({ title: current.title, body: current.lines.join("\n").trim() });
      current = { title: heading[1]!.trim(), lines: [] };
    } else if (current) current.lines.push(line);
    else preamble.push(line);
  }
  if (current) sections.push({ title: current.title, body: current.lines.join("\n").trim() });
  if (preamble.join("").trim()) sections.unshift({ title: "Overview", body: preamble.join("\n").trim() });
  return sections;
}

export function joinBrief(sections: { title: string; body: string }[]): string {
  return sections.map((s) => `## ${s.title}\n${s.body ? `${s.body}\n` : ""}`).join("\n");
}

function BriefEditor({ project }: { project: ProjectDetail }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [sections, setSections] = useState(() => parseBrief(project.brief));
  const [state, setState] = useState<"idle" | "saving" | "saved">("idle");
  // Compare with the normalized brief, so opening it never triggers a save.
  const saved = useRef(joinBrief(parseBrief(project.brief)));
  const markdown = useMemo(() => joinBrief(sections), [sections]);

  useEffect(() => {
    if (markdown === saved.current) return;
    setState("saving");
    const timer = setTimeout(async () => {
      const { error } = await fetchClient.PATCH("/api/projects/{project_id}", {
        params: { path: { project_id: project.id } },
        body: { brief: markdown },
      });
      if (error) return toast.error(errorMessage(error));
      saved.current = markdown;
      setState("saved");
      void queryClient.invalidateQueries({ queryKey: ["get", "/api/projects/{project_id}"] });
    }, 700);
    return () => clearTimeout(timer);
  }, [markdown, project.id, queryClient]);

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-5 px-6 py-8" data-testid="brief-editor">
      <div className="flex items-center justify-between">
        <h2 className="font-display text-2xl font-bold uppercase">{t("brief.title")}</h2>
        <span className="font-mono text-xs text-muted" aria-live="polite" data-testid="brief-state">
          {state === "saving" ? t("brief.saving") : state === "saved" ? t("brief.saved") : ""}
        </span>
      </div>
      {sections.map((section, index) => (
        <label key={section.title} className="flex flex-col gap-1.5">
          <span className="font-display text-lg font-semibold uppercase tracking-wide">{section.title}</span>
          <span className="text-xs text-muted">{t(`brief.sections.${section.title}`, { defaultValue: "" })}</span>
          <textarea
            value={section.body}
            onChange={(e) =>
              setSections((all) => all.map((s, i) => (i === index ? { ...s, body: e.target.value } : s)))
            }
            rows={Math.max(3, section.body.split("\n").length + 1)}
            className="resize-y rounded-lg border border-line bg-stage p-3 text-sm outline-none focus:border-tungsten"
          />
        </label>
      ))}
    </div>
  );
}

/** Inline-editable name and description; remounts when the saved values change. */
function ProjectHeading({
  project,
  onSave,
}: {
  project: ProjectDetail;
  onSave: (body: { name?: string; description?: string }) => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState(project.name);
  const [description, setDescription] = useState(project.description);
  return (
    <div className="flex min-w-0 flex-1 flex-col">
      <input
        aria-label={t("projects.name")}
        value={name}
        onChange={(e) => setName(e.target.value)}
        onBlur={() => name.trim() && name !== project.name && onSave({ name })}
        onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
        className="bg-transparent font-display text-2xl font-bold uppercase outline-none focus:text-tungsten"
        data-testid="project-name"
      />
      <input
        aria-label={t("projects.description")}
        value={description}
        maxLength={140}
        onChange={(e) => setDescription(e.target.value)}
        onBlur={() => description !== project.description && onSave({ description })}
        onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
        placeholder={t("projects.description")}
        className="bg-transparent text-sm text-muted outline-none"
      />
    </div>
  );
}

export interface ProjectSearch {
  tab?: "assets" | "brief";
  folder?: string;
  view?: string;
  q?: string;
}

export function ProjectPage({
  projectId,
  search,
  setSearch,
}: {
  projectId: string;
  search: ProjectSearch;
  setSearch: (patch: Partial<ProjectSearch>) => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { data: project, isError } = $api.useQuery("get", "/api/projects/{project_id}", {
    params: { path: { project_id: projectId } },
  });
  const { data: folders = [] } = useFolders(projectId);

  if (isError) return <NotFound message={t("projects.notFound")} />;
  if (!project) return null;

  async function save(body: Schemas["ProjectPatch"]) {
    const { error } = await fetchClient.PATCH("/api/projects/{project_id}", {
      params: { path: { project_id: projectId } },
      body,
    });
    if (error) toast.error(errorMessage(error));
    await invalidateLibrary(queryClient);
  }
  async function trash() {
    await fetchClient.POST("/api/projects/{project_id}/trash", { params: { path: { project_id: projectId } } });
    toast.success(t("projects.trashed", { name: project!.name }));
    await invalidateLibrary(queryClient);
    void navigate({ to: "/studio" });
  }
  const setCover = (asset: AssetOut) =>
    void save({ cover_asset_id: asset.id }).then(() => toast.success(t("projects.coverSet")));
  const folder = folders.find((f) => f.id === search.folder);
  const tab = search.tab ?? "assets";

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-3">
        {project.cover_url ? <img src={project.cover_url} alt="" className="size-10 rounded-md object-cover" /> : null}
        <ProjectHeading
          key={`${project.name}\u0000${project.description}`}
          project={project}
          onSave={(body) => void save(body)}
        />
        <span className="font-mono text-[11px] text-muted" title={t("projects.slug")}>
          /{project.slug}
        </span>
        <div role="tablist" className="flex rounded-lg border border-line p-0.5">
          {(["assets", "brief"] as const).map((key) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={tab === key}
              onClick={() => setSearch({ tab: key === "assets" ? undefined : key })}
              className={cn("rounded-md px-3 py-1 text-sm", tab === key ? "bg-raised text-ink" : "text-muted")}
            >
              {t(`shell.${key}`)}
            </button>
          ))}
        </div>
        <button
          type="button"
          aria-pressed={project.describe_images}
          onClick={() => void save({ describe_images: !project.describe_images })}
          title={t("projects.describeHint")}
          className={cn(
            "inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-xs",
            project.describe_images ? "border-ink/60 bg-raised text-ink" : "border-line text-muted hover:text-ink",
          )}
          data-testid="project-describe"
        >
          <ScanText className="size-3.5" aria-hidden />
          {t("projects.describe")}
        </button>
        <Button size="sm" variant="danger" onClick={() => void trash()} data-testid="trash-project">
          <Trash2 />
          {t("projects.trash")}
        </Button>
      </header>
      <div className="min-h-0 flex-1 overflow-auto">
        {tab === "brief" ? (
          <BriefEditor key={project.id} project={project} />
        ) : (
          <LibraryView
            key={`${projectId}:${search.folder ?? "all"}`}
            title={folder?.name ?? t("library.allAssets")}
            pageKey={`project:${projectId}`}
            scope={{ projectId, folderId: search.folder }}
            query={search.q ?? ""}
            onQuery={(q) => setSearch({ q: q || undefined })}
            view={search.view}
            onView={(view) => setSearch({ view })}
            onSetCover={setCover}
          />
        )}
      </div>
    </div>
  );
}
