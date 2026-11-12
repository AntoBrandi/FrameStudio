import { Cloud, Cpu, ListChecks, LoaderCircle, RotateCcw, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { $api, type JobOut } from "@/api/client";
import { isActive } from "@/api/events";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger, Progress } from "@/components/ui/misc";
import { cn } from "@/lib/utils";

export function useJobs() {
  return $api.useQuery("get", "/api/jobs", { params: { query: { limit: 30 } } }, { staleTime: 30_000 });
}

export function JobRow({ job }: { job: JobOut }) {
  const { t } = useTranslation();
  const cancel = $api.useMutation("post", "/api/jobs/{job_id}/cancel");
  const retry = $api.useMutation("post", "/api/jobs/{job_id}/retry");
  const active = isActive(job.state);
  const retryable = job.state === "failed" || job.state === "cancelled" || job.state === "interrupted";
  const params = { params: { path: { job_id: job.id } } };
  return (
    <li
      className="flex flex-col gap-1.5 rounded-lg px-2 py-2 hover:bg-raised/60"
      data-testid="job-row"
      data-state={job.state}
    >
      <div className="flex items-center gap-2">
        {job.lane === "network" ? (
          <Cloud aria-label={t("jobs.remote")} className="size-3.5 shrink-0 text-daylight" />
        ) : (
          <Cpu aria-label={t("jobs.local")} className="size-3.5 shrink-0 text-tungsten" />
        )}
        <span className="min-w-0 flex-1 truncate text-sm">{job.title}</span>
        <span
          className={cn(
            "font-mono text-[11px] uppercase",
            job.state === "succeeded" && "text-ok",
            (job.state === "failed" || job.state === "interrupted") && "text-danger",
            active && "text-muted",
          )}
        >
          {t(`jobs.states.${job.state}`)}
        </span>
        {active ? (
          <Button
            size="icon"
            variant="ghost"
            className="size-6"
            aria-label={`${t("jobs.cancel")}: ${job.title}`}
            onClick={() => cancel.mutate(params)}
          >
            <X />
          </Button>
        ) : retryable ? (
          <Button
            size="icon"
            variant="ghost"
            className="size-6"
            aria-label={`${t("jobs.retry")}: ${job.title}`}
            onClick={() => retry.mutate(params)}
          >
            <RotateCcw />
          </Button>
        ) : null}
      </div>
      {job.state === "running" || job.state === "submitting" ? (
        <Progress value={job.progress} label={job.title} />
      ) : null}
      {job.error_message && !active ? <p className="text-xs text-danger">{job.error_message}</p> : null}
    </li>
  );
}

export function JobTray() {
  const { t } = useTranslation();
  const { data: jobs = [] } = useJobs();
  const activeCount = jobs.filter((job) => isActive(job.state)).length;
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          aria-label={t("jobs.trayLabel", { count: activeCount })}
          data-testid="job-tray-button"
        >
          {activeCount > 0 ? <LoaderCircle className="animate-spin text-tungsten" /> : <ListChecks />}
          <span className="font-mono">{activeCount}</span>
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-96" aria-label={t("jobs.title")}>
        <h2 className="px-2 pb-2 font-display text-lg font-semibold uppercase tracking-wide">{t("jobs.title")}</h2>
        {jobs.length === 0 ? (
          <p className="px-2 pb-2 text-sm text-muted">{t("jobs.empty")}</p>
        ) : (
          <ul className="flex max-h-96 flex-col overflow-auto" data-testid="job-list">
            {jobs.map((job) => (
              <JobRow key={job.id} job={job} />
            ))}
          </ul>
        )}
      </PopoverContent>
    </Popover>
  );
}
