import { useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Minus, Plus, SlidersHorizontal, Sparkles } from "lucide-react";
import { type FormEvent, useCallback, useDeferredValue, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { $api, errorMessage, type GenerationOut, type Schemas } from "@/api/client";
import { GENERATIONS_KEY, upsertById } from "@/api/events";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/misc";
import { cn } from "@/lib/utils";
import { type Enhancement, EnhanceButton, EnhancePanel } from "./Enhance";
import { type Frames, FrameSlots } from "./FrameSlots";
import { ModelPicker } from "./ModelPicker";
import { ParamControls, SeedField } from "./ParamControls";
import { type Mode, type ModelOut, normalize, type Params, paramsFor } from "./params";
import { PromptEditor } from "./PromptEditor";
import { type Reference, ReferenceTray } from "./ReferenceTray";
import { AUTO, NO_ATTACHMENTS, paramsKey, useComposerStore } from "./store";

export type { Mode } from "./params";

/** The default model for a mode: the first local one that can run, else the
 * first that can run at all. */
function fallbackModel(models: ModelOut[], mode: Mode): ModelOut | undefined {
  const offered = models.filter((m) => m.modes.includes(mode) && m.available);
  return offered.find((m) => m.runtime === "local") ?? offered[0];
}

function ModeToggle({ mode }: { mode: Mode }) {
  const { t } = useTranslation();
  return (
    <div role="group" aria-label={t("composer.mode")} className="flex rounded-full border border-line p-0.5">
      {(["image", "video"] as const).map((value) => (
        <Link
          key={value}
          to={value === "image" ? "/image" : "/video"}
          aria-current={value === mode ? "page" : undefined}
          className={cn(
            "rounded-full px-2.5 py-0.5 text-xs",
            value === mode ? "bg-raised font-medium text-ink" : "text-muted hover:text-ink",
          )}
        >
          {t(`composer.${value}`)}
        </Link>
      ))}
    </div>
  );
}

function Estimate({ estimate, pending }: { estimate: Schemas["GenerationEstimate"] | undefined; pending: boolean }) {
  const { t } = useTranslation();
  if (!estimate || !estimate.ok) return null;
  const parts = [];
  if (estimate.cost_usd != null) parts.push(t("composer.cost", { cost: estimate.cost_usd.toFixed(2) }));
  if (estimate.seconds != null) parts.push(t("composer.seconds", { seconds: +estimate.seconds.toFixed(1) }));
  return (
    <span
      className={cn(
        "font-mono text-[11px] font-normal normal-case tracking-normal opacity-80",
        pending && "opacity-50",
      )}
      data-testid="estimate"
    >
      {parts.join(" · ")}
    </span>
  );
}

/** Floating composer docked at the bottom of the Image and Video pages
 * (plan step M2.5). Every control comes from the chosen model's parameter
 * schema; combinations the model refuses cannot be picked, and Generate
 * shows what it will cost and how long it should take. */
export function Composer({ mode }: { mode: Mode }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { data: models = [] } = $api.useQuery("get", "/api/models");
  const create = $api.useMutation("post", "/api/generations");
  const store = useComposerStore();
  const prompt = store.drafts[mode] ?? "";
  const [enhancement, setEnhancement] = useState<Enhancement | null>(null);
  const attachments = store.attachments[mode] ?? NO_ATTACHMENTS;
  const references = attachments.references;
  const frames: Frames = { start: attachments.start, end: attachments.end };
  const setReferences = (next: Reference[]) => store.setAttachments(mode, { references: next });
  const setFrames = (next: Frames) => store.setAttachments(mode, next);

  // Auto (M2.9): the router picks a model for what is given.
  const auto = store.models[mode] === AUTO;
  const { data: routed } = $api.useQuery(
    "get",
    "/api/route",
    {
      params: {
        query: {
          mode,
          references: references.length,
          start_frame: Boolean(attachments.start),
          end_frame: Boolean(attachments.end),
        },
      },
    },
    { enabled: auto, placeholderData: (previous) => previous },
  );
  const model = useMemo(() => {
    if (auto) return models.find((m) => m.id === routed?.model);
    const chosen = models.find((m) => m.id === store.models[mode] && m.modes.includes(mode));
    return chosen?.available ? chosen : (fallbackModel(models, mode) ?? chosen);
  }, [auto, routed, models, store.models, mode]);

  const params: Params = useMemo(
    () => (model ? normalize(model, mode, store.params[paramsKey(model.id, mode)] ?? {}) : {}),
    [model, mode, store.params],
  );
  const maxBatch = model?.limits.max_batch ?? 4;
  const batch = Math.min(store.batch, maxBatch);
  const maxRefs = model?.limits.max_references ?? 0;
  // A model that takes fewer references uses the first ones.
  const usedRefs = useMemo(() => references.slice(0, maxRefs), [references, maxRefs]);
  const takesFrames = mode === "video" && Boolean(model?.capabilities.includes("image_to_video"));
  const takesEnd = takesFrames && Boolean(model?.capabilities.includes("start_end_frames"));
  const start = takesFrames ? frames.start : null;
  const end = takesEnd && start ? frames.end : null;

  const setParam = useCallback(
    (name: string, value: unknown) => {
      if (!model) return;
      store.setParams(model.id, mode, normalize(model, mode, { ...params, [name]: value }, name));
    },
    [model, mode, params, store],
  );

  const body = useMemo(
    () =>
      model
        ? {
            mode,
            model: model.id,
            prompt: prompt.trim(),
            batch,
            params,
            references: usedRefs.map((r) => r.id),
            ...(start ? { start_frame: start.id } : {}),
            ...(end ? { end_frame: end.id } : {}),
            ...(store.seed != null ? { seed: store.seed } : {}),
          }
        : null,
    [model, mode, prompt, batch, params, usedRefs, start, end, store.seed],
  );
  // The estimate follows the settings, not every keystroke of the prompt.
  const estimateBody = useDeferredValue(body ? { ...body, prompt: "" } : null);
  const { data: estimate, isFetching } = $api.useQuery(
    "post",
    "/api/generations/estimate",
    { body: estimateBody! },
    { enabled: estimateBody !== null, placeholderData: (previous) => previous },
  );

  const submit = useCallback(
    (event?: FormEvent) => {
      event?.preventDefault();
      if (!body || create.isPending) return;
      if (estimate && !estimate.ok) {
        toast.error(estimate.problems[0] ?? t("composer.failed", { error: "" }));
        return;
      }
      create.mutate(
        { body },
        {
          onSuccess: (created) => {
            for (const generation of [...created].reverse()) {
              queryClient.setQueriesData<GenerationOut[]>({ queryKey: GENERATIONS_KEY }, (list) =>
                upsertById(list, generation),
              );
            }
          },
          onError: (error) => toast.error(t("composer.failed", { error: errorMessage(error) })),
        },
      );
    },
    [body, create, estimate, queryClient, t],
  );

  const advanced = model ? paramsFor(model, mode).filter(([, p]) => p.advanced) : [];
  const blocked = auto && routed && !routed.model ? [routed.reason] : estimate && !estimate.ok ? estimate.problems : [];
  return (
    <form
      onSubmit={submit}
      aria-label={t("composer.label")}
      className="flex flex-col gap-2 rounded-2xl border border-ink/25 bg-flat/95 p-3 shadow-[0_10px_40px_rgba(0,0,0,0.35)] backdrop-blur"
      data-testid="composer"
    >
      {takesFrames ? (
        <FrameSlots
          frames={{ start, end }}
          withEnd={takesEnd}
          blockedBy={usedRefs.length ? t("frames.blockedByReferences") : null}
          onChange={setFrames}
        />
      ) : null}
      {maxRefs > 0 && !start ? (
        <ReferenceTray
          references={usedRefs}
          limit={maxRefs}
          kinds={model?.limits.reference_kinds ?? ["image"]}
          onChange={setReferences}
        />
      ) : null}
      {enhancement ? (
        <EnhancePanel
          enhancement={enhancement}
          onUndo={() => {
            store.setDraft(mode, enhancement.before);
            setEnhancement(null);
          }}
          onClose={() => setEnhancement(null)}
        />
      ) : null}
      <PromptEditor
        value={prompt}
        onChange={(text) => store.setDraft(mode, text)}
        onSubmit={submit}
        label={t("composer.label")}
        placeholder={mode === "image" ? t("composer.placeholderImage") : t("composer.placeholderVideo")}
        maxLength={model?.limits.max_prompt_chars ?? 4000}
      />
      <div className="flex flex-wrap items-center gap-1.5">
        <ModeToggle mode={mode} />
        <ModelPicker
          models={models}
          mode={mode}
          value={model}
          auto={auto}
          onChange={(id) => store.setModel(mode, id)}
        />
        <EnhanceButton
          prompt={prompt}
          mode={mode}
          model={model?.id}
          onEnhanced={(result) => {
            store.setDraft(mode, result.after);
            setEnhancement(result);
          }}
        />
        {model ? (
          <ParamControls model={model} mode={mode} params={params} onChange={setParam} advanced={false} />
        ) : null}
        <div
          role="group"
          aria-label={t("composer.batch")}
          className="flex items-center gap-1 rounded-full border border-line px-1"
        >
          <button
            type="button"
            className="p-1 text-muted hover:text-ink disabled:opacity-40"
            onClick={() => store.setBatch(Math.max(1, batch - 1))}
            disabled={batch <= 1}
            aria-label={`${t("composer.batch")} −`}
          >
            <Minus className="size-3.5" />
          </button>
          <span className="w-4 text-center font-mono text-xs" aria-live="polite">
            {batch}
          </span>
          <button
            type="button"
            className="p-1 text-muted hover:text-ink disabled:opacity-40"
            onClick={() => store.setBatch(Math.min(maxBatch, batch + 1))}
            disabled={batch >= maxBatch}
            aria-label={`${t("composer.batch")} +`}
          >
            <Plus className="size-3.5" />
          </button>
        </div>
        <Popover>
          <PopoverTrigger asChild>
            <button
              type="button"
              className="inline-flex h-7 items-center gap-1 rounded-full border border-line px-2.5 text-xs text-muted hover:text-ink"
              data-testid="more-settings"
            >
              <SlidersHorizontal className="size-3.5" aria-hidden />
              {t("composer.more")}
            </button>
          </PopoverTrigger>
          <PopoverContent side="top" align="start" className="flex w-auto min-w-72 flex-col gap-3">
            <SeedField label={t("composer.seed")} value={store.seed} onChange={store.setSeed} />
            {model && advanced.length ? (
              <ParamControls model={model} mode={mode} params={params} onChange={setParam} advanced />
            ) : null}
          </PopoverContent>
        </Popover>
        <Button
          type="submit"
          variant="primary"
          className="ml-auto uppercase tracking-wide"
          disabled={create.isPending || !model || blocked.length > 0}
          data-testid="generate-button"
        >
          <Sparkles />
          {t("composer.generate")}
          <Estimate estimate={estimate} pending={isFetching} />
        </Button>
      </div>
      {blocked.length ? (
        <p role="status" className="px-1 text-xs text-danger" data-testid="composer-problems">
          {blocked.join(" ")}
        </p>
      ) : null}
    </form>
  );
}
