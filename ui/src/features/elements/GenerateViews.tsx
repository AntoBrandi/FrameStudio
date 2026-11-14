import { useQueryClient } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { $api, errorMessage, fetchClient, type GenerationOut, type Schemas } from "@/api/client";
import { GENERATIONS_KEY, upsertById } from "@/api/events";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { inputClass } from "@/components/ui/misc";
import { cn } from "@/lib/utils";
import type { ElementOut } from "./api";

type RecipeId = Schemas["ElementGenerate"]["recipe"];

/** Element generators (plan step M4.6): make a character sheet, location
 * set, prop turnaround or turntable clip from the element's pictures; the
 * results are added to the element as they finish. */
export function GenerateViews({
  element,
  open,
  onOpenChange,
  onStarted,
}: {
  element: ElementOut;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onStarted?: () => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { data: recipes = [] } = $api.useQuery("get", "/api/elements/recipes", {}, { enabled: open });
  const { data: models = [] } = $api.useQuery("get", "/api/models", {}, { enabled: open });
  // The recipes that suit this kind of element first.
  const ordered = [...recipes].sort(
    (a, b) => Number(!a.categories.includes(element.category)) - Number(!b.categories.includes(element.category)),
  );
  const [chosen, setChosen] = useState<RecipeId | null>(null);
  const recipe = ordered.find((r) => r.id === chosen) ?? ordered[0];
  const [views, setViews] = useState<Record<string, string[]>>({});
  const [modelChoice, setModelChoice] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const pickedViews = recipe ? (views[recipe.id] ?? recipe.views.map((v) => v.id)) : [];
  const model = recipe ? (modelChoice[recipe.id] ?? recipe.model ?? undefined) : undefined;
  const usable = models.filter((m) => recipe && m.available && m.modes.includes(recipe.mode));
  const body = recipe ? { recipe: recipe.id, views: pickedViews, ...(model ? { model } : {}) } : null;
  const { data: estimate } = $api.useQuery(
    "post",
    "/api/elements/{element_id}/generate/estimate",
    { params: { path: { element_id: element.id } }, body: body! },
    { enabled: open && body !== null && pickedViews.length > 0, placeholderData: (previous) => previous },
  );
  const pictures = element.media.filter((m) => m.kind === "image").slice(0, 3);

  async function start() {
    if (!body || busy) return;
    setBusy(true);
    const { data, error } = await fetchClient.POST("/api/elements/{element_id}/generate", {
      params: { path: { element_id: element.id } },
      body,
    });
    setBusy(false);
    if (error || !data) return void toast.error(errorMessage(error));
    for (const generation of data) {
      queryClient.setQueriesData<GenerationOut[]>({ queryKey: GENERATIONS_KEY }, (list) =>
        upsertById(list, generation),
      );
    }
    toast.success(t("generators.started", { count: data.length, handle: element.handle }));
    onOpenChange(false);
    onStarted?.();
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-[min(94vw,36rem)]" closeLabel={t("elements.close")}>
        <div className="flex flex-col gap-4" data-testid="generate-views">
          <div className="flex flex-col gap-1 pr-8">
            <DialogTitle>{t("generators.title", { name: element.name })}</DialogTitle>
            <DialogDescription>{t("generators.hint")}</DialogDescription>
          </div>

          <div role="radiogroup" aria-label={t("generators.recipe")} className="grid gap-2 sm:grid-cols-2">
            {ordered.map((r) => (
              <button
                key={r.id}
                type="button"
                role="radio"
                aria-checked={recipe?.id === r.id}
                onClick={() => setChosen(r.id)}
                className={cn(
                  "flex flex-col items-start rounded-lg border px-3 py-2 text-left",
                  recipe?.id === r.id ? "border-tungsten bg-tungsten/10" : "border-line hover:border-ink/40",
                )}
                data-testid={`recipe-${r.id}`}
              >
                <span className="text-sm font-medium">{t(`generators.recipes.${r.id}`)}</span>
                <span className="text-xs text-muted">{r.views.map((v) => v.label).join(" · ")}</span>
              </button>
            ))}
          </div>

          {recipe && recipe.views.length > 1 ? (
            <div role="group" aria-label={t("generators.views")} className="flex flex-wrap gap-1.5">
              {recipe.views.map((view) => {
                const on = pickedViews.includes(view.id);
                return (
                  <button
                    key={view.id}
                    type="button"
                    aria-pressed={on}
                    onClick={() =>
                      setViews({
                        ...views,
                        [recipe.id]: on ? pickedViews.filter((v) => v !== view.id) : [...pickedViews, view.id],
                      })
                    }
                    className={cn(
                      "h-7 rounded-full border px-2.5 text-xs",
                      on ? "border-ink/60 bg-raised text-ink" : "border-line text-muted hover:text-ink",
                    )}
                  >
                    {view.label}
                  </button>
                );
              })}
            </div>
          ) : null}

          <div className="flex flex-wrap items-end gap-3">
            <label className="flex min-w-48 flex-1 flex-col gap-1.5 text-sm">
              <span className="font-medium">{t("generators.model")}</span>
              <select
                className={inputClass}
                value={model ?? ""}
                onChange={(e) => recipe && setModelChoice({ ...modelChoice, [recipe.id]: e.target.value })}
                data-testid="generator-model"
              >
                {usable.length === 0 ? <option value="">{t("generators.noModel")}</option> : null}
                {usable.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
              </select>
            </label>
            <div className="flex flex-col gap-1.5 text-sm">
              <span className="font-medium">{t("generators.from")}</span>
              <div className="flex gap-1">
                {pictures.length ? (
                  pictures.map((m) => (
                    <span key={m.asset_id} className="size-9 overflow-hidden rounded-md bg-raised">
                      {m.thumb_url ? <img src={m.thumb_url} alt="" className="size-full object-cover" /> : null}
                    </span>
                  ))
                ) : (
                  <span className="text-xs text-muted">{t("generators.noPictures")}</span>
                )}
              </div>
            </div>
          </div>

          {estimate && !estimate.ok ? (
            <p role="status" className="text-xs text-danger" data-testid="generator-problems">
              {estimate.problems.join(" ")}
            </p>
          ) : null}

          <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line pt-3">
            <span className="mr-auto text-xs text-muted" data-testid="generator-estimate">
              {estimate?.ok
                ? [
                    t("generators.count", { count: estimate.views }),
                    estimate.cost_usd != null ? t("composer.cost", { cost: estimate.cost_usd.toFixed(2) }) : null,
                    estimate.seconds != null ? t("composer.seconds", { seconds: Math.round(estimate.seconds) }) : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")
                : null}
            </span>
            <Button variant="ghost" onClick={() => onOpenChange(false)}>
              {t("elements.cancel")}
            </Button>
            <Button
              variant="primary"
              disabled={busy || !estimate?.ok || pickedViews.length === 0}
              onClick={() => void start()}
              data-testid="generator-start"
            >
              <Sparkles /> {t("generators.start")}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
