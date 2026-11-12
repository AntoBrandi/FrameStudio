import { useNavigate } from "@tanstack/react-router";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { $api, type JobOut } from "@/api/client";
import { useJobs } from "@/features/jobs/JobTray";
import { Composer, type Mode } from "./Composer";
import { GenerationCard } from "./GenerationCard";
import { Viewer } from "./Viewer";

/** History feed with the composer docked at the bottom (Image and Video pages). */
export function CreatePage({ mode, view }: { mode: Mode; view?: string }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { data: generations = [] } = $api.useQuery("get", "/api/generations", {
    params: { query: { mode, limit: 60 } },
  });
  const { data: jobs = [] } = useJobs();
  const jobsById = useMemo(() => new Map<string, JobOut>(jobs.map((job) => [job.id, job])), [jobs]);
  const viewing = generations.find((g) => g.id === view);
  const to = mode === "image" ? "/image" : "/video";

  return (
    <div className="flex min-h-full flex-col">
      <h1 className="sr-only">{t(`nav.${mode}`)}</h1>
      <section className="flex-1 px-4 pb-48 pt-4" aria-live="polite">
        {generations.length === 0 ? (
          <p className="mt-24 text-center text-muted">
            {mode === "image" ? t("feed.emptyImage") : t("feed.emptyVideo")}
          </p>
        ) : (
          <div
            className="grid grid-cols-[repeat(auto-fill,minmax(14rem,1fr))] items-start gap-3"
            data-testid="generation-feed"
          >
            {generations.map((generation) => (
              <GenerationCard
                key={generation.id}
                generation={generation}
                job={generation.job_id ? jobsById.get(generation.job_id) : undefined}
                onOpen={(g) => void navigate({ to, search: { view: g.id } })}
              />
            ))}
          </div>
        )}
      </section>
      <div className="pointer-events-none sticky bottom-0 z-10 px-4 pb-4">
        <div className="pointer-events-auto mx-auto max-w-4xl">
          <Composer mode={mode} />
        </div>
      </div>
      <Viewer generation={viewing} onClose={() => void navigate({ to, search: {} })} />
    </div>
  );
}
