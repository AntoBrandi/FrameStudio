import { X } from "lucide-react";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { $api, type GenerationOut, type JobOut } from "@/api/client";
import { useJobs } from "@/features/jobs/JobTray";
import { GenerationCard } from "@/features/generate/GenerationCard";
import { useStudioStore } from "./store";

const ACTIVE = new Set(["queued", "running"]);
const ENDED = new Set(["failed", "interrupted", "cancelled"]);
const DAY = 86_400_000;

/** Which of a project's generations belong in the strip: those still running,
 * and those that failed in the last day and were not dismissed. Finished
 * results are in the grid itself; planned shots have their own cards. */
export function inProgress(generations: GenerationOut[], dismissed: Record<string, true>, now = Date.now()) {
  return generations.filter(
    (g) =>
      // A planned shot's keyframes and clips show on its card instead (M5.7).
      !g.shot_id &&
      (ACTIVE.has(g.status) ||
        (ENDED.has(g.status) && !dismissed[g.id] && now - new Date(g.created_at).getTime() < DAY)),
  );
}

/** The project's generations in flight (M5.6), above its asset grid. */
export function InProgress({ projectId }: { projectId: string }) {
  const { t } = useTranslation();
  const { data: generations = [] } = $api.useQuery("get", "/api/generations", {
    params: { query: { project_id: projectId, limit: 60 } },
  });
  const { data: jobs = [] } = useJobs();
  const jobsById = useMemo(() => new Map<string, JobOut>(jobs.map((job) => [job.id, job])), [jobs]);
  const dismissed = useStudioStore((s) => s.dismissed);
  const dismiss = useStudioStore((s) => s.dismiss);
  const shown = useMemo(() => inProgress(generations, dismissed), [generations, dismissed]);
  if (!shown.length) return null;
  const failed = shown.filter((g) => ENDED.has(g.status));
  return (
    <section
      aria-label={t("studio.inProgress")}
      className="flex flex-col gap-2 border-b border-line px-4 py-2"
      data-testid="in-progress"
    >
      <div className="flex items-center gap-2">
        <h2 className="font-mono text-[11px] uppercase tracking-widest text-muted">{t("studio.inProgress")}</h2>
        {failed.length ? (
          <button
            type="button"
            onClick={() => dismiss(failed.map((g) => g.id))}
            className="ml-auto inline-flex items-center gap-1 text-xs text-muted hover:text-ink"
            data-testid="dismiss-failed"
          >
            <X className="size-3.5" aria-hidden />
            {t("studio.dismissFailed", { count: failed.length })}
          </button>
        ) : null}
      </div>
      <div className="flex gap-2 overflow-x-auto pb-1">
        {shown.map((generation) => (
          <div key={generation.id} className="h-28 shrink-0">
            <div className="h-full" style={{ aspectRatio: aspectFor(generation) }}>
              <GenerationCard
                fill
                generation={generation}
                job={generation.job_id ? jobsById.get(generation.job_id) : undefined}
                onOpen={() => undefined}
              />
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function aspectFor(generation: GenerationOut): string {
  const [w, h] = String(generation.params.aspect ?? (generation.mode === "video" ? "16:9" : "1:1")).split(":");
  return `${Number(w) || 1} / ${Number(h) || 1}`;
}
