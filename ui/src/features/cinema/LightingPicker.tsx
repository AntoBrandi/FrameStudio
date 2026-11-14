import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { lazy, Suspense, useDeferredValue, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { $api, errorMessage, fetchClient } from "@/api/client";
import { refresh } from "@/api/events";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { inputClass } from "@/components/ui/misc";
import { cn } from "@/lib/utils";
import {
  addLight,
  canUseWebGL,
  DEFAULT_RIG,
  type LightingSpec,
  QUICK,
  type Rig,
  removeLight,
  sameRig,
  updateLight,
} from "./lighting";
import { useVocab } from "./shot";

const LightSphere = lazy(() => import("./LightSphere"));

/** A flat view of the rig from above (where WebGL is not available): the
 * camera at the bottom, each light a dot at its azimuth, higher lights
 * nearer the middle. */
export function RigDiagram({
  rig,
  selected,
  onSelect,
}: {
  rig: Rig;
  selected: number;
  onSelect: (index: number) => void;
}) {
  const { t } = useTranslation();
  return (
    <svg viewBox="-110 -110 220 220" className="aspect-square w-full" role="group" aria-label={t("lighting.diagram")}>
      <circle r="100" fill="none" stroke="currentColor" strokeOpacity="0.2" />
      <circle r="30" fill="currentColor" fillOpacity="0.25" />
      <text y="106" textAnchor="middle" fontSize="9" fill="currentColor" opacity="0.6">
        {t("lighting.camera")}
      </text>
      {rig.lights.map((light, index) => {
        const reach = 100 * Math.cos((light.elevation * Math.PI) / 180);
        const x = reach * Math.sin((light.azimuth * Math.PI) / 180);
        const y = reach * Math.cos((light.azimuth * Math.PI) / 180);
        return (
          <circle
            key={index}
            cx={x}
            cy={y}
            r={index === selected ? 9 : 6}
            fill={light.colour}
            stroke={index === selected ? "var(--color-tungsten)" : "black"}
            strokeWidth="2"
            onClick={() => onSelect(index)}
            data-light={index}
          />
        );
      })}
    </svg>
  );
}

function Slider({
  label,
  value,
  min,
  max,
  step = 1,
  unit = "",
  onChange,
  testId,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  unit?: string;
  onChange: (value: number) => void;
  testId?: string;
}) {
  return (
    <label className="flex items-center gap-2 text-xs">
      <span className="w-20 shrink-0 text-muted">{label}</span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="min-w-0 flex-1 accent-[var(--tungsten)]"
        aria-label={label}
        data-testid={testId}
      />
      <span className="w-12 text-right font-mono">
        {Math.round(value * (unit === "%" ? 100 : 1))}
        {unit}
      </span>
    </label>
  );
}

/** Lighting (M5.5): presets, saved rigs and the rig editor. Picking a preset
 * uses its words; editing its rig (or any rig) uses the rig's own words. */
export function LightingPicker({
  open,
  onOpenChange,
  lighting,
  onChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  lighting: LightingSpec | null | undefined;
  onChange: (lighting: LightingSpec | null) => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { data: vocab } = useVocab();
  const { data: saved = [] } = $api.useQuery("get", "/api/cinema/presets", { params: { query: { kind: "lighting" } } });
  const presetRig = (id: string | null | undefined) => vocab?.lighting.find((p) => p.id === id)?.rig ?? null;
  const rig: Rig = lighting?.rig ?? presetRig(lighting?.preset) ?? DEFAULT_RIG;
  const [selected, setSelected] = useState(0);
  const [name, setName] = useState("");
  const webgl = useState(canUseWebGL)[0];
  const current = Math.min(selected, rig.lights.length - 1);
  const light = rig.lights[current]!;

  // The words this rig becomes, from the backend's describer (what the prompt gets).
  const shown = useDeferredValue(rig);
  const { data: words } = useQuery({
    queryKey: ["lighting-words", shown],
    queryFn: async () => (await fetchClient.POST("/api/cinema/lighting/describe", { body: shown })).data?.words ?? "",
    enabled: open,
    staleTime: Number.POSITIVE_INFINITY,
  });

  /** An edit: the rig becomes the truth (the preset stays as where it began). */
  function edit(next: Rig) {
    onChange({ preset: lighting?.preset ?? null, rig: next, label: null });
  }

  async function save() {
    const label = name.trim();
    if (!label) return;
    const { error } = await fetchClient.POST("/api/cinema/presets", {
      body: { kind: "lighting", name: label, data: rig },
    });
    if (error) return void toast.error(errorMessage(error));
    await refresh(queryClient, { queryKey: ["get", "/api/cinema/presets"] });
    onChange({ rig, label });
    setName("");
    toast.success(t("lighting.saved", { name: label }));
  }

  async function remove(id: string, label: string) {
    await fetchClient.DELETE("/api/cinema/presets/{preset_id}", { params: { path: { preset_id: id } } });
    await refresh(queryClient, { queryKey: ["get", "/api/cinema/presets"] });
    toast.success(t("lighting.deleted", { name: label }));
  }

  const editor = webgl ? (
    <Suspense fallback={<RigDiagram rig={rig} selected={current} onSelect={setSelected} />}>
      <LightSphere
        rig={rig}
        selected={current}
        onSelect={setSelected}
        onMove={(index, azimuth, elevation) => edit(updateLight(rig, index, { azimuth, elevation }))}
      />
    </Suspense>
  ) : (
    <RigDiagram rig={rig} selected={current} onSelect={setSelected} />
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-[min(96vw,60rem)] max-w-none" closeLabel={t("shot.close")}>
        <div className="flex flex-col gap-4" data-testid="lighting-picker">
          <div className="flex flex-col gap-1 pr-8">
            <DialogTitle>{t("lighting.title")}</DialogTitle>
            <DialogDescription>{t("lighting.hint")}</DialogDescription>
          </div>
          <div className="grid gap-4 md:grid-cols-[13rem_minmax(0,1fr)]">
            <div className="flex flex-col gap-3">
              <section className="flex flex-col gap-1">
                <span className="text-xs font-medium uppercase tracking-wide text-muted">{t("lighting.presets")}</span>
                <ul role="radiogroup" aria-label={t("lighting.presets")} className="flex flex-col gap-1">
                  {(vocab?.lighting ?? []).map((preset) => {
                    const on = lighting?.preset === preset.id && !lighting?.rig;
                    return (
                      <li key={preset.id}>
                        <button
                          type="button"
                          role="radio"
                          aria-checked={on}
                          onClick={() => onChange({ preset: preset.id })}
                          className={cn(
                            "w-full rounded-lg border px-3 py-1.5 text-left text-sm",
                            on ? "border-tungsten bg-tungsten/10" : "border-line hover:border-ink/40",
                          )}
                          data-testid={`lighting-preset-${preset.id}`}
                        >
                          {preset.label}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </section>
              {saved.length ? (
                <section className="flex flex-col gap-1">
                  <span className="text-xs font-medium uppercase tracking-wide text-muted">
                    {t("lighting.saved_heading")}
                  </span>
                  <ul role="radiogroup" aria-label={t("lighting.saved_heading")} className="flex flex-col gap-1">
                    {saved.map((preset) => {
                      const on = sameRig(lighting?.rig, preset.data as Rig);
                      return (
                        <li key={preset.id} className="flex items-center gap-1">
                          <button
                            type="button"
                            role="radio"
                            aria-checked={on}
                            onClick={() => onChange({ rig: preset.data as Rig, label: preset.name })}
                            className={cn(
                              "min-w-0 flex-1 truncate rounded-lg border px-3 py-1.5 text-left text-sm",
                              on ? "border-tungsten bg-tungsten/10" : "border-line hover:border-ink/40",
                            )}
                            data-testid={`lighting-saved-${preset.id}`}
                          >
                            {preset.name}
                          </button>
                          <button
                            type="button"
                            className="rounded p-1 text-muted hover:text-danger"
                            aria-label={t("lighting.delete", { name: preset.name })}
                            onClick={() => void remove(preset.id, preset.name)}
                          >
                            <Trash2 className="size-3.5" />
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </section>
              ) : null}
            </div>

            <div className="grid gap-3 sm:grid-cols-[minmax(0,15rem)_minmax(0,1fr)]">
              <div className="mx-auto w-full max-w-60 rounded-xl bg-stage text-muted">{editor}</div>
              <div className="flex flex-col gap-2">
                <div className="flex flex-wrap items-center gap-1" role="group" aria-label={t("lighting.lights")}>
                  {rig.lights.map((l, index) => (
                    <button
                      key={index}
                      type="button"
                      aria-pressed={index === current}
                      onClick={() => setSelected(index)}
                      className={cn(
                        "inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-xs",
                        index === current ? "border-ink/60 bg-raised" : "border-line text-muted",
                      )}
                    >
                      <span className="size-3 rounded-full" style={{ background: l.colour }} />
                      {t("lighting.light", { n: index + 1 })}
                    </button>
                  ))}
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={rig.lights.length >= 4}
                    onClick={() => {
                      edit(addLight(rig));
                      setSelected(rig.lights.length);
                    }}
                    data-testid="lighting-add"
                  >
                    <Plus /> {t("lighting.add")}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={rig.lights.length <= 1}
                    onClick={() => {
                      edit(removeLight(rig, current));
                      setSelected(0);
                    }}
                    aria-label={t("lighting.remove", { n: current + 1 })}
                  >
                    <Trash2 />
                  </Button>
                </div>
                <div className="flex flex-wrap gap-1" role="group" aria-label={t("lighting.quick")}>
                  {(Object.keys(QUICK) as (keyof typeof QUICK)[]).map((key) => (
                    <button
                      key={key}
                      type="button"
                      onClick={() => edit(updateLight(rig, current, QUICK[key]))}
                      className="h-6 rounded-full border border-line px-2 text-[11px] text-muted hover:text-ink"
                      data-testid={`lighting-quick-${key}`}
                    >
                      {t(`lighting.quickKeys.${key}`)}
                    </button>
                  ))}
                </div>
                <Slider
                  label={t("lighting.azimuth")}
                  value={light.azimuth}
                  min={0}
                  max={359}
                  unit="°"
                  onChange={(azimuth) => edit(updateLight(rig, current, { azimuth }))}
                  testId="lighting-azimuth"
                />
                <Slider
                  label={t("lighting.elevation")}
                  value={light.elevation}
                  min={-90}
                  max={90}
                  unit="°"
                  onChange={(elevation) => edit(updateLight(rig, current, { elevation }))}
                  testId="lighting-elevation"
                />
                <Slider
                  label={t("lighting.brightness")}
                  value={light.intensity ?? 1}
                  min={0}
                  max={1}
                  step={0.05}
                  unit="%"
                  onChange={(intensity) => edit(updateLight(rig, current, { intensity }))}
                  testId="lighting-brightness"
                />
                <Slider
                  label={t("lighting.softness")}
                  value={light.softness ?? 0.5}
                  min={0}
                  max={1}
                  step={0.05}
                  unit="%"
                  onChange={(softness) => edit(updateLight(rig, current, { softness }))}
                  testId="lighting-softness"
                />
                <label className="flex items-center gap-2 text-xs">
                  <span className="w-20 shrink-0 text-muted">{t("lighting.colour")}</span>
                  <input
                    type="color"
                    value={(light.colour ?? "#FFFFFF").toLowerCase()}
                    onChange={(e) => edit(updateLight(rig, current, { colour: e.target.value.toUpperCase() }))}
                    className="h-7 w-14 cursor-pointer rounded border border-line bg-transparent"
                    aria-label={t("lighting.colour")}
                    data-testid="lighting-colour"
                  />
                </label>
                <Slider
                  label={t("lighting.ambient")}
                  value={rig.ambient ?? 0.1}
                  min={0}
                  max={1}
                  step={0.05}
                  unit="%"
                  onChange={(ambient) => edit({ ...rig, ambient })}
                  testId="lighting-ambient"
                />
              </div>
            </div>
          </div>
          <p className="rounded-lg bg-stage p-2 text-sm" data-testid="lighting-words">
            <span className="text-xs font-medium uppercase tracking-wide text-muted">{t("lighting.words")}: </span>
            {words || "…"}
          </p>
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line pt-3">
            <div className="flex items-center gap-2">
              <Button variant="ghost" onClick={() => onChange(null)} disabled={!lighting} data-testid="lighting-clear">
                {t("shot.reset")}
              </Button>
              <input
                className={cn(inputClass, "h-8 w-44")}
                value={name}
                maxLength={80}
                placeholder={t("lighting.saveName")}
                aria-label={t("lighting.saveName")}
                onChange={(e) => setName(e.target.value)}
                data-testid="lighting-save-name"
              />
              <Button
                variant="outline"
                size="sm"
                disabled={!name.trim()}
                onClick={() => void save()}
                data-testid="lighting-save"
              >
                {t("lighting.save")}
              </Button>
            </div>
            <Button variant="primary" onClick={() => onOpenChange(false)} data-testid="lighting-done">
              {t("shot.done")}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
