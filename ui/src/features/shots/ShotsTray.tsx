import { useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Check, CircleAlert, Clapperboard, FastForward, Plus, Play, X } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { $api, errorMessage, type GenerationOut, type Schemas } from "@/api/client";
import { insertGenerations, refresh, SHOTS_KEY } from "@/api/events";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/misc";
import { cn } from "@/lib/utils";

export type ShotOut = Schemas["ShotOut"];
type Continuity = Schemas["Continuity"];

const ACTIVE = new Set(["queued", "running"]);

const pct = (value: number | null | undefined) => (value == null ? "" : `${Math.round(value * 100)}%`);

/** A keyframe or clip that is not finished: progress, or why it failed. */
function Pending({ generation }: { generation: GenerationOut }) {
  const { t } = useTranslation();
  const failed = !ACTIVE.has(generation.status);
  return (
    <div
      className="flex size-full flex-col justify-end gap-1 rounded-md bg-raised p-1.5"
      data-testid="shot-pending"
      data-status={generation.status}
      title={generation.error_message ?? undefined}
    >
      {failed ? (
        <span className="flex items-center gap-1 text-[10px] text-danger">
          <CircleAlert className="size-3" aria-hidden />
          {t("shots.failed")}
        </span>
      ) : (
        <>
          <span className="text-[10px] text-muted">{t(`shots.statuses.${generation.status}`)}</span>
          <Progress value={generation.status === "running" ? 0.5 : 0.05} />
        </>
      )}
    </div>
  );
}

function ContinuityBadge({ check }: { check: Continuity | undefined }) {
  const { t } = useTranslation();
  if (!check) return <span className="text-[10px] text-muted">{t("shots.checking")}</span>;
  const scores = [
    check.start != null ? `${t("shots.opens")} ${pct(check.start)}` : null,
    check.end != null ? `${t("shots.closes")} ${pct(check.end)}` : null,
  ].filter(Boolean);
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-1.5 py-px text-[10px]",
        check.matches ? "bg-ok/20 text-ok" : "bg-danger/15 text-danger",
      )}
      title={t(check.matches ? "shots.matchHint" : "shots.driftHint")}
      data-testid="shot-continuity"
      data-matches={check.matches}
    >
      {check.matches ? <Check className="size-3" aria-hidden /> : <CircleAlert className="size-3" aria-hidden />}
      {scores.join(" · ")}
    </span>
  );
}

/** One planned shot (M5.7): its keyframes to choose from, Animate, its clips
 * with their continuity, Extend. */
export function ShotCard({ shot, onOpen }: { shot: ShotOut; onOpen?: (assetId: string) => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { data: models = [] } = $api.useQuery("get", "/api/models");
  const video = models.find((m) => m.id === shot.video_model);
  const stills = models.find((m) => m.id === shot.keyframe_model);
  const takesEnd = Boolean(video?.capabilities.includes("start_end_frames"));
  const [start, setStart] = useState<string | null>(shot.keyframe_id);
  const [end, setEnd] = useState<string | null>(null);
  const path = { params: { path: { shot_id: shot.id } } };
  const animate = $api.useMutation("post", "/api/shots/{shot_id}/animate");
  const more = $api.useMutation("post", "/api/shots/{shot_id}/keyframes");
  const extend = $api.useMutation("post", "/api/shots/{shot_id}/extend");
  const archive = $api.useMutation("patch", "/api/shots/{shot_id}");
  const { data: clipEstimate } = $api.useQuery("post", "/api/generations/estimate", {
    body: { mode: "video", model: shot.video_model, params: shot.video_params, prompt: "" },
  });
  const done = (made: ShotOut) => {
    insertGenerations(queryClient, [...made.keyframes, ...made.clips]);
    void refresh(queryClient, { queryKey: SHOTS_KEY });
  };
  const failed = (error: unknown) => toast.error(t("shots.actionFailed", { error: errorMessage(error) }));
  const price = clipEstimate?.ok
    ? [
        clipEstimate.cost_usd != null ? `$${clipEstimate.cost_usd.toFixed(2)}` : null,
        clipEstimate.seconds != null ? `~${Math.round(clipEstimate.seconds)} s` : null,
      ]
        .filter(Boolean)
        .join(" · ")
    : "";

  function pick(assetId: string) {
    if (start === assetId) return setStart(null);
    if (end === assetId) setEnd(null);
    setStart(assetId);
  }

  return (
    <article
      className="flex min-w-0 flex-col gap-2 rounded-xl border border-line bg-flat p-2.5"
      data-testid="shot-card"
      data-status={shot.status}
      aria-label={shot.title}
    >
      <header className="flex items-center gap-2">
        <Clapperboard className="size-4 shrink-0 text-tungsten" aria-hidden />
        <span className="min-w-0 flex-1 truncate text-sm font-medium" title={shot.prompt}>
          {shot.title}
        </span>
        <span className="font-mono text-[10px] uppercase tracking-wider text-muted" data-testid="shot-status">
          {t(`shots.states.${shot.status}`)}
        </span>
        <button
          type="button"
          className="rounded p-0.5 text-muted hover:text-ink"
          aria-label={t("shots.archive", { title: shot.title })}
          onClick={() => archive.mutate({ ...path, body: { archived: true } }, { onSuccess: done, onError: failed })}
        >
          <X className="size-4" />
        </button>
      </header>
      <div className="flex items-stretch gap-2 overflow-x-auto pb-1">
        <div className="flex shrink-0 flex-col gap-1">
          <span className="text-[10px] uppercase tracking-wider text-muted">
            {t("shots.keyframesOn", { model: stills?.name ?? shot.keyframe_model })}
          </span>
          <div className="flex gap-1.5" role="group" aria-label={t("shots.keyframes")}>
            {shot.keyframes.map((generation) => {
              const asset = generation.asset;
              if (!asset) {
                return (
                  <div key={generation.id} className="h-20 w-32">
                    <Pending generation={generation} />
                  </div>
                );
              }
              const role = start === asset.id ? "start" : end === asset.id ? "end" : null;
              return (
                <div key={generation.id} className="relative h-20 w-32 shrink-0">
                  <button
                    type="button"
                    aria-pressed={role === "start"}
                    onClick={() => pick(asset.id)}
                    className={cn(
                      "size-full overflow-hidden rounded-md border-2",
                      role === "start" ? "border-tungsten" : role === "end" ? "border-daylight" : "border-transparent",
                    )}
                    aria-label={t("shots.useAsStart")}
                    data-testid="keyframe-option"
                    data-asset={asset.id}
                  >
                    {asset.thumb_url ? <img src={asset.thumb_url} alt="" className="size-full object-cover" /> : null}
                  </button>
                  {role ? (
                    <span
                      className={cn(
                        "pointer-events-none absolute left-1 top-1 rounded-full px-1.5 text-[10px] font-semibold uppercase",
                        role === "start" ? "bg-tungsten text-tungsten-ink" : "bg-daylight text-black",
                      )}
                    >
                      {t(`shots.${role}`)}
                    </span>
                  ) : null}
                  {takesEnd && start && start !== asset.id ? (
                    <button
                      type="button"
                      aria-pressed={role === "end"}
                      onClick={() => setEnd(role === "end" ? null : asset.id)}
                      className="absolute bottom-1 right-1 rounded-full bg-black/70 px-1.5 text-[10px] text-white hover:bg-black"
                      data-testid="keyframe-end"
                    >
                      {t(role === "end" ? "shots.noEnd" : "shots.asEnd")}
                    </button>
                  ) : null}
                </div>
              );
            })}
            <button
              type="button"
              className="flex h-20 w-10 shrink-0 items-center justify-center rounded-md border border-dashed border-line text-muted hover:text-ink"
              aria-label={t("shots.more")}
              title={t("shots.more")}
              disabled={more.isPending}
              onClick={() => more.mutate({ ...path, body: { count: 2 } }, { onSuccess: done, onError: failed })}
              data-testid="more-keyframes"
            >
              <Plus className="size-4" />
            </button>
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-center justify-center gap-1 px-1">
          <ArrowRight className="size-4 text-muted" aria-hidden />
          <Button
            size="sm"
            variant="primary"
            disabled={!start || animate.isPending}
            onClick={() =>
              start &&
              animate.mutate(
                { ...path, body: { keyframe_id: start, ...(end ? { end_frame_id: end } : {}) } },
                { onSuccess: done, onError: failed },
              )
            }
            data-testid="animate-shot"
            title={start ? undefined : t("shots.pickFirst")}
          >
            <Play />
            {t("shots.animate")}
          </Button>
          <span className="max-w-28 truncate text-center text-[10px] text-muted">
            {video?.name ?? shot.video_model}
            {price ? ` · ${price}` : ""}
          </span>
        </div>
        {shot.clips.length ? (
          <div className="flex shrink-0 flex-col gap-1">
            <span className="text-[10px] uppercase tracking-wider text-muted">{t("shots.clips")}</span>
            <div className="flex gap-1.5">
              {shot.clips.map((clip) => (
                <div
                  key={clip.id}
                  className="flex w-36 flex-col gap-1"
                  data-testid="shot-clip"
                  data-status={clip.status}
                >
                  <div className="h-20 w-36">
                    {clip.asset ? (
                      <button
                        type="button"
                        className="size-full overflow-hidden rounded-md"
                        onClick={() => onOpen?.(clip.asset!.id)}
                        aria-label={t("shots.openClip")}
                      >
                        {clip.asset.thumb_url ? (
                          <img src={clip.asset.thumb_url} alt="" className="size-full object-cover" />
                        ) : null}
                      </button>
                    ) : (
                      <Pending generation={clip} />
                    )}
                  </div>
                  {clip.status === "succeeded" ? (
                    <div className="flex flex-wrap items-center gap-1">
                      <ContinuityBadge check={shot.checks[clip.id]} />
                      <button
                        type="button"
                        className="inline-flex items-center gap-0.5 text-[10px] text-muted hover:text-ink"
                        disabled={extend.isPending}
                        onClick={() =>
                          extend.mutate(
                            { ...path, body: { generation_id: clip.id } },
                            { onSuccess: done, onError: failed },
                          )
                        }
                        title={t("shots.extendHint")}
                        data-testid="extend-clip"
                      >
                        <FastForward className="size-3" aria-hidden />
                        {t("shots.extend")}
                      </button>
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </article>
  );
}

/** Open planned shots of a project (or of the library outside projects). */
export function ShotsTray({ projectId, onOpen }: { projectId: string; onOpen?: (assetId: string) => void }) {
  const { t } = useTranslation();
  const { data: shots = [] } = $api.useQuery("get", "/api/shots", {
    params: { query: { project_id: projectId, limit: 6 } },
  });
  if (!shots.length) return null;
  return (
    <section
      aria-label={t("shots.title")}
      className="flex max-h-[45vh] flex-col gap-2 overflow-y-auto border-b border-line px-4 py-2"
      data-testid="shots-tray"
    >
      <h2 className="font-mono text-[11px] uppercase tracking-widest text-muted">{t("shots.title")}</h2>
      {shots.map((shot) => (
        <ShotCard key={shot.id} shot={shot} onOpen={onOpen} />
      ))}
    </section>
  );
}
