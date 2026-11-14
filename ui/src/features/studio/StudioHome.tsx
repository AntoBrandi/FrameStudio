import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { FolderInput } from "lucide-react";
import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { errorMessage, fetchClient } from "@/api/client";
import { invalidateLibrary, useProjects } from "@/features/library/api";
import { Composer } from "@/features/generate/Composer";
import { useComposerStore } from "@/features/generate/store";
import { MyProjects, projectNameFrom } from "./projects";

const NEW = "new";

/** The Studio home (M5.6): a hero composer that starts a project (or adds to
 * one), and the projects below. */
export function StudioHome() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const mode = useComposerStore((s) => s.studioMode);
  const setMode = useComposerStore((s) => s.setStudioMode);
  const { data: projects = [] } = useProjects({ sort: "updated" });
  const [chosen, setChosen] = useState(NEW);
  // A project that was trashed meanwhile is no longer a choice.
  const target = chosen === NEW || projects.some((p) => p.id === chosen) ? chosen : NEW;

  const prepare = useCallback(async () => {
    if (target !== NEW) return { projectId: target };
    const draft = useComposerStore.getState().drafts[useComposerStore.getState().studioMode] ?? [];
    const name = projectNameFrom(draft, t("studio.untitled"));
    const { data, error } = await fetchClient.POST("/api/projects", { body: { name } });
    if (error || !data) {
      toast.error(errorMessage(error));
      return null;
    }
    await invalidateLibrary(queryClient);
    return { projectId: data.id };
  }, [queryClient, t, target]);

  const onCreated = useCallback(
    (_: unknown, projectId: string | undefined) => {
      if (projectId) void navigate({ to: "/studio/$projectId", params: { projectId } });
    },
    [navigate],
  );

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-12 px-6 py-10">
      <section className="flex flex-col gap-5" aria-labelledby="studio-title">
        <div className="flex flex-col gap-3">
          <span className="font-mono text-xs uppercase tracking-[0.2em] text-tungsten">{t("studio.eyebrow")}</span>
          <h1
            id="studio-title"
            className="max-w-3xl font-display text-5xl font-bold uppercase leading-[0.95] tracking-tight sm:text-6xl"
          >
            {t("studio.title")}
          </h1>
          <p className="max-w-2xl text-muted">{t("studio.lede")}</p>
        </div>
        <div data-testid="studio-hero">
          <Composer
            mode={mode}
            onMode={setMode}
            projectId={target === NEW ? undefined : target}
            prepare={prepare}
            onCreated={onCreated}
            leading={
              <label className="inline-flex h-7 items-center gap-1 rounded-full border border-line pl-2 text-xs text-muted">
                <FolderInput className="size-3.5" aria-hidden />
                <span className="sr-only">{t("studio.target")}</span>
                <select
                  value={target}
                  onChange={(e) => setChosen(e.target.value)}
                  className="h-full max-w-44 rounded-full bg-transparent pr-2 text-ink outline-none"
                  data-testid="studio-target"
                  aria-label={t("studio.target")}
                >
                  <option value={NEW}>{t("studio.newProject")}</option>
                  {projects.map((project) => (
                    <option key={project.id} value={project.id}>
                      {project.name}
                    </option>
                  ))}
                </select>
              </label>
            }
          />
        </div>
      </section>
      <MyProjects />
    </div>
  );
}
