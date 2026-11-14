import { Frame } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ModelOut } from "@/features/generate/params";
import { AUTO } from "@/features/generate/store";
import { cn } from "@/lib/utils";

/** Video: "Keyframe first" (M5.7) and the image model that makes the keyframes. */
export function KeyframeFirst({
  on,
  onToggle,
  models,
  model,
  onModel,
}: {
  on: boolean;
  onToggle: (on: boolean) => void;
  models: ModelOut[];
  model: string;
  onModel: (model: string) => void;
}) {
  const { t } = useTranslation();
  const stills = models.filter((m) => m.modes.includes("image") && m.available);
  return (
    <div className="inline-flex items-center gap-1">
      <button
        type="button"
        aria-pressed={on}
        onClick={() => onToggle(!on)}
        title={t("shots.keyframeFirstHint")}
        className={cn(
          "inline-flex h-7 items-center gap-1 rounded-full border px-2.5 text-xs",
          on ? "border-tungsten/70 bg-tungsten-soft text-ink" : "border-line text-muted hover:text-ink",
        )}
        data-testid="keyframe-first"
      >
        <Frame className="size-3.5" aria-hidden />
        {t("shots.keyframeFirst")}
      </button>
      {on ? (
        <select
          value={stills.some((m) => m.id === model) ? model : AUTO}
          onChange={(e) => onModel(e.target.value)}
          aria-label={t("shots.keyframeModel")}
          className="h-7 max-w-40 rounded-full border border-line bg-transparent px-2 text-xs text-ink"
          data-testid="keyframe-model"
        >
          <option value={AUTO}>{t("shots.autoStills")}</option>
          {stills.map((m) => (
            <option key={m.id} value={m.id}>
              {m.name}
            </option>
          ))}
        </select>
      ) : null}
    </div>
  );
}
