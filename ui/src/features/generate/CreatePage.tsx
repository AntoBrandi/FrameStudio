import { useNavigate } from "@tanstack/react-router";
import { useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { $api, type AssetOut, type GenerationOut, type JobOut } from "@/api/client";
import { useJobs } from "@/features/jobs/JobTray";
import { AssetViewer } from "@/features/library/AssetViewer";
import { ViewOptions } from "@/features/library/FilterBar";
import { Gallery } from "@/features/library/Gallery";
import type { LayoutItem } from "@/features/library/layout";
import { usePagePrefs } from "@/features/library/prefs";
import { ShotsTray } from "@/features/shots/ShotsTray";
import { cn } from "@/lib/utils";
import { Composer, type Mode } from "./Composer";
import { GenerationCard } from "./GenerationCard";

function toLayout(generation: GenerationOut): LayoutItem {
  const asset = generation.asset;
  const [w, h] = String(generation.params.aspect ?? (generation.mode === "video" ? "16:9" : "1:1")).split(":");
  const aspect = asset?.width && asset.height ? asset.width / asset.height : Number(w) / Number(h) || 1;
  return { id: generation.id, aspect, date: new Date(generation.created_at) };
}

/** History feed with the composer docked at the bottom (Image and Video pages). */
export function CreatePage({ mode, view }: { mode: Mode; view?: string }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const prefs = usePagePrefs(`feed:${mode}`);
  const hideFailed = prefs.filters.hideFailed;
  const { data: generations = [] } = $api.useQuery("get", "/api/generations", {
    params: { query: { mode, limit: 200, ...(hideFailed ? { hide_failed: true } : {}) } },
  });
  const { data: jobs = [] } = useJobs();
  const jobsById = useMemo(() => new Map<string, JobOut>(jobs.map((job) => [job.id, job])), [jobs]);
  const assets = useMemo(() => generations.map((g) => g.asset).filter((a): a is AssetOut => Boolean(a)), [generations]);
  const to = mode === "image" ? "/image" : "/video";
  const open = useCallback(
    (id: string | undefined) => void navigate({ to, search: id ? { view: id } : {} }),
    [navigate, to],
  );

  return (
    <div className="relative flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-2 px-4 pt-3">
        <h1 className="sr-only">{t(`nav.${mode}`)}</h1>
        <button
          type="button"
          aria-pressed={hideFailed}
          onClick={() => prefs.setFilters({ hideFailed: !hideFailed })}
          className={cn(
            "h-7 rounded-full border px-2.5 text-xs",
            hideFailed ? "border-ink/60 bg-raised text-ink" : "border-line text-muted hover:text-ink",
          )}
          data-testid="hide-failed"
        >
          {t("feed.hideFailed")}
        </button>
        <div className="ml-auto">
          <ViewOptions view={prefs.view} onView={prefs.setView} />
        </div>
      </div>
      {mode === "video" ? <ShotsTray projectId="none" onOpen={(id) => open(id)} /> : null}
      <Gallery
        items={generations}
        toLayout={toLayout}
        view={prefs.view}
        label={t(`nav.${mode}`)}
        bottomPadding={200}
        empty={mode === "image" ? t("feed.emptyImage") : t("feed.emptyVideo")}
        renderItem={(generation) => (
          <GenerationCard
            fill
            generation={generation}
            job={generation.job_id ? jobsById.get(generation.job_id) : undefined}
            onOpen={(g) => g.asset && open(g.asset.id)}
          />
        )}
      />
      <div className="pointer-events-none absolute inset-x-0 bottom-0 z-10 px-4 pb-4">
        <div className="pointer-events-auto mx-auto max-w-4xl">
          <Composer mode={mode} />
        </div>
      </div>
      <AssetViewer
        asset={assets.find((asset) => asset.id === view)}
        items={assets}
        onNavigate={(id) => open(id)}
        onClose={() => open(undefined)}
      />
    </div>
  );
}
