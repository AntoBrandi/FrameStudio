import { useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { Film, Plus } from "lucide-react";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { errorMessage, fetchClient } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Field, inputClass } from "@/components/ui/misc";
import { invalidateLibrary, useProjects } from "@/features/library/api";
import { cn } from "@/lib/utils";
import type { Token } from "@/features/generate/tokens";

/** A project's export zip (M5.6), or one folder of it. */
export function projectExportUrl(projectId: string, folderId?: string): string {
  const query = folderId && folderId !== "none" ? `?folder_id=${encodeURIComponent(folderId)}` : "";
  return `/api/projects/${encodeURIComponent(projectId)}/export${query}`;
}

const TRAILING = new Set([
  "a",
  "an",
  "the",
  "of",
  "at",
  "in",
  "on",
  "to",
  "and",
  "or",
  "with",
  "for",
  "by",
  "from",
  "as",
]);

/** A name for a project started from its first prompt: its first words,
 * element handles as names, camera moves and skills left out. */
export function projectNameFrom(tokens: Token[], fallback: string): string {
  const words = tokens
    .flatMap((token) => {
      if (token.type === "text") return [token.text];
      if (token.type === "element") return [token.handle.replace(/[-_]+/g, " ")];
      return [];
    })
    .join("")
    .replace(/[^\p{L}\p{N}'’ -]+/gu, " ")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 6);
  // A name cut mid-phrase should not end on "of" or "the".
  while (words.length > 1 && TRAILING.has(words[words.length - 1]!.toLowerCase())) words.pop();
  let name = words.join(" ");
  if (name.length > 60) name = name.slice(0, 60).replace(/\s+\S*$/, "");
  return name ? name[0]!.toUpperCase() + name.slice(1) : fallback;
}

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

/** "My projects" on the Studio home: search, sort, new, and the cards. */
export function MyProjects() {
  const { t } = useTranslation();
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<"updated" | "created" | "name">("updated");
  const [creating, setCreating] = useState(false);
  const { data: projects = [], isPending } = useProjects({ q: q || undefined, sort });

  return (
    <section aria-labelledby="my-projects" className="flex flex-col gap-4">
      <header className="flex flex-wrap items-end gap-3">
        <div className="mr-auto flex flex-col gap-1">
          <h2 id="my-projects" className="font-display text-3xl font-bold uppercase">
            {t("studio.myProjects")}
          </h2>
          <p className="text-sm text-muted">{t("projects.lede")}</p>
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
        <Button variant="outline" onClick={() => setCreating(true)} data-testid="new-project">
          <Plus />
          {t("projects.new")}
        </Button>
      </header>
      {!isPending && projects.length === 0 ? <p className="text-muted">{t("projects.empty")}</p> : null}
      <div className="grid grid-cols-[repeat(auto-fill,minmax(15rem,1fr))] gap-4" data-testid="project-grid">
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
    </section>
  );
}
