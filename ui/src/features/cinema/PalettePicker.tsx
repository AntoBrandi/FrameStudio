import { useQueryClient } from "@tanstack/react-query";
import { ImagePlus, Trash2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { $api, errorMessage, fetchClient, type Schemas } from "@/api/client";
import { refresh } from "@/api/events";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { inputClass } from "@/components/ui/misc";
import { type Reference, ReferencePicker } from "@/features/generate/ReferencePicker";
import { cn } from "@/lib/utils";
import { type ShotSpec, useVocab } from "./shot";

export type PaletteSpec = Schemas["PaletteSpec"];
type Extracted = Schemas["ExtractedPalette"];

/** A row of colour swatches. */
export function Swatches({ colours, className }: { colours: string[]; className?: string }) {
  return (
    <span className={cn("flex overflow-hidden rounded", className)} aria-hidden>
      {colours.map((colour, index) => (
        <span key={`${colour}-${index}`} className="h-full flex-1" style={{ background: colour }} />
      ))}
    </span>
  );
}

/** The colours and name of a palette in a shot (preset by id, or custom). */
export function usePaletteLook(palette: PaletteSpec | null | undefined): { label: string; colours: string[] } | null {
  const { data: vocab } = useVocab();
  if (!palette) return null;
  const preset = palette.id ? vocab?.palettes.find((p) => p.id === palette.id) : undefined;
  return {
    label: palette.label ?? preset?.label ?? palette.id ?? "",
    colours: palette.swatches?.length ? palette.swatches : (preset?.swatches ?? []),
  };
}

function PaletteCard({
  label,
  colours,
  image,
  selected,
  onClick,
  onDelete,
  testId,
}: {
  label: string;
  colours: string[];
  image?: string | null;
  selected: boolean;
  onClick: () => void;
  onDelete?: () => void;
  testId?: string;
}) {
  const { t } = useTranslation();
  return (
    <li className="relative">
      <button
        type="button"
        role="radio"
        aria-checked={selected}
        aria-label={label}
        onClick={onClick}
        className={cn(
          "flex w-full flex-col overflow-hidden rounded-lg border-2 bg-raised text-left outline-none focus-visible:ring-2 focus-visible:ring-tungsten",
          selected ? "border-tungsten" : "border-transparent hover:border-line",
        )}
        data-testid={testId}
      >
        <span className="relative block aspect-video w-full">
          {image ? (
            <img src={image} alt="" className="size-full object-cover" />
          ) : (
            <Swatches colours={colours} className="size-full rounded-none" />
          )}
        </span>
        <Swatches colours={colours} className="h-2 rounded-none" />
        <span className="truncate px-2 py-1 text-xs">{label}</span>
      </button>
      {onDelete ? (
        <button
          type="button"
          onClick={onDelete}
          className="absolute right-1 top-1 rounded bg-black/60 p-1 text-white hover:text-danger"
          aria-label={t("colourPalette.delete", { name: label })}
        >
          <Trash2 className="size-3.5" />
        </button>
      ) : null}
    </li>
  );
}

/** Colour palettes (M5.4): presets, saved palettes, and new ones from any picture. */
export function PalettePicker({
  open,
  onOpenChange,
  shot,
  onChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  shot: ShotSpec | null | undefined;
  onChange: (palette: PaletteSpec | null) => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { data: vocab } = useVocab();
  const { data: saved = [] } = $api.useQuery("get", "/api/cinema/presets", { params: { query: { kind: "palette" } } });
  const [picking, setPicking] = useState(false);
  const [draft, setDraft] = useState<{ source: Reference; extracted: Extracted; name: string; words: string } | null>(
    null,
  );
  const current = shot?.palette;
  const same = (p: PaletteSpec) =>
    Boolean(current) &&
    (p.id ? current?.id === p.id : current?.label === p.label && current?.swatches?.join() === p.swatches?.join());

  async function extractFrom(source: Reference) {
    const { data, error } = await fetchClient.POST("/api/cinema/palettes/extract", {
      body: { asset_id: source.id, count: 6 },
    });
    if (error || !data) return void toast.error(errorMessage(error));
    setDraft({ source, extracted: data, name: t("colourPalette.fromName", { name: source.name }), words: data.words });
  }

  async function saveDraft(use: boolean) {
    if (!draft) return;
    const spec: PaletteSpec = { label: draft.name.trim(), words: draft.words, swatches: draft.extracted.swatches };
    const { error } = await fetchClient.POST("/api/cinema/presets", {
      body: { kind: "palette", name: spec.label!, data: spec, preview_asset_id: draft.source.id },
    });
    if (error) return void toast.error(errorMessage(error));
    await refresh(queryClient, { queryKey: ["get", "/api/cinema/presets"] });
    toast.success(t("colourPalette.saved", { name: spec.label }));
    if (use) onChange(spec);
    setDraft(null);
  }

  async function remove(id: string, name: string) {
    await fetchClient.DELETE("/api/cinema/presets/{preset_id}", { params: { path: { preset_id: id } } });
    await refresh(queryClient, { queryKey: ["get", "/api/cinema/presets"] });
    toast.success(t("colourPalette.deleted", { name }));
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-[min(94vw,52rem)] max-w-none" closeLabel={t("shot.close")}>
        <div className="flex flex-col gap-4" data-testid="palette-picker">
          <div className="flex flex-col gap-1 pr-8">
            <DialogTitle>{t("colourPalette.title")}</DialogTitle>
            <DialogDescription>{t("colourPalette.hint")}</DialogDescription>
          </div>

          {draft ? (
            <section className="flex flex-col gap-3 rounded-xl border border-tungsten/40 p-3" data-testid="palette-new">
              <div className="flex items-start gap-3">
                <span className="size-20 shrink-0 overflow-hidden rounded-lg bg-raised">
                  {draft.source.thumb ? (
                    <img src={draft.source.thumb} alt="" className="size-full object-cover" />
                  ) : null}
                </span>
                <div className="flex min-w-0 flex-1 flex-col gap-2">
                  <input
                    className={inputClass}
                    value={draft.name}
                    maxLength={60}
                    onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                    aria-label={t("colourPalette.name")}
                    data-testid="palette-name"
                  />
                  <span className="flex h-8 gap-1" data-testid="palette-swatches">
                    {draft.extracted.swatches.map((colour, index) => (
                      <span
                        key={colour}
                        className="flex flex-1 items-end justify-center rounded pb-0.5 font-mono text-[9px] text-white/80 [text-shadow:0_0_2px_black]"
                        style={{ background: colour }}
                        title={`${colour} · ${Math.round((draft.extracted.shares[index] ?? 0) * 100)}%`}
                      >
                        {colour}
                      </span>
                    ))}
                  </span>
                </div>
              </div>
              <label className="flex flex-col gap-1 text-sm">
                <span className="font-medium">{t("colourPalette.words")}</span>
                <textarea
                  value={draft.words}
                  maxLength={200}
                  onChange={(e) => setDraft({ ...draft, words: e.target.value })}
                  className="min-h-14 resize-y rounded-lg border border-line bg-stage p-2 text-sm outline-none focus:border-tungsten"
                  data-testid="palette-words"
                />
              </label>
              <div className="flex justify-end gap-2">
                <Button variant="ghost" onClick={() => setDraft(null)}>
                  {t("elements.cancel")}
                </Button>
                <Button
                  variant="outline"
                  onClick={() => {
                    onChange({ label: draft.name.trim(), words: draft.words, swatches: draft.extracted.swatches });
                    setDraft(null);
                  }}
                >
                  {t("colourPalette.useOnly")}
                </Button>
                <Button
                  variant="primary"
                  onClick={() => void saveDraft(true)}
                  disabled={!draft.name.trim()}
                  data-testid="palette-save"
                >
                  {t("colourPalette.saveUse")}
                </Button>
              </div>
            </section>
          ) : (
            <Button
              variant="outline"
              className="self-start"
              onClick={() => setPicking(true)}
              data-testid="palette-from-picture"
            >
              <ImagePlus /> {t("colourPalette.fromPicture")}
            </Button>
          )}

          {saved.length ? (
            <section className="flex flex-col gap-1.5">
              <span className="text-sm font-medium">{t("colourPalette.saved_heading")}</span>
              <ul
                role="radiogroup"
                aria-label={t("colourPalette.saved_heading")}
                className="grid grid-cols-3 gap-2 sm:grid-cols-5"
              >
                {saved.map((preset) => {
                  const spec = preset.data as PaletteSpec;
                  return (
                    <PaletteCard
                      key={preset.id}
                      label={preset.name}
                      colours={spec.swatches ?? []}
                      image={preset.preview_url}
                      selected={same(spec)}
                      onClick={() => onChange(spec)}
                      onDelete={() => void remove(preset.id, preset.name)}
                      testId={`palette-saved-${preset.id}`}
                    />
                  );
                })}
              </ul>
            </section>
          ) : null}

          <section className="flex flex-col gap-1.5">
            <span className="text-sm font-medium">{t("colourPalette.presets")}</span>
            <ul
              role="radiogroup"
              aria-label={t("colourPalette.presets")}
              className="grid grid-cols-3 gap-2 sm:grid-cols-6"
            >
              {(vocab?.palettes ?? []).map((preset) => (
                <PaletteCard
                  key={preset.id}
                  label={preset.label}
                  colours={preset.swatches}
                  image={vocab?.previews?.palettes?.[preset.id]?.image}
                  selected={same({ id: preset.id })}
                  onClick={() => onChange({ id: preset.id })}
                  testId={`palette-preset-${preset.id}`}
                />
              ))}
            </ul>
          </section>

          <div className="flex items-center justify-between gap-2 border-t border-line pt-3">
            <Button variant="ghost" onClick={() => onChange(null)} disabled={!current} data-testid="palette-clear">
              {t("shot.reset")}
            </Button>
            <Button variant="primary" onClick={() => onOpenChange(false)} data-testid="palette-done">
              {t("shot.done")}
            </Button>
          </div>
        </div>
        <ReferencePicker
          open={picking}
          onOpenChange={setPicking}
          taken={[]}
          room={1}
          kinds={["image", "video"]}
          labels={{ title: t("colourPalette.pickTitle"), hint: t("colourPalette.pickHint") }}
          onAdd={([picked]) => picked && void extractFrom(picked)}
        />
      </DialogContent>
    </Dialog>
  );
}

/** Grade a picture or clip toward a palette (M5.4): a new asset beside it. */
export function GradeDialog({
  assetId,
  open,
  onOpenChange,
}: {
  assetId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const { data: vocab } = useVocab();
  const { data: saved = [] } = $api.useQuery(
    "get",
    "/api/cinema/presets",
    { params: { query: { kind: "palette" } } },
    { enabled: open },
  );
  const choices: { key: string; label: string; colours: string[]; spec: PaletteSpec; image?: string | null }[] = [
    ...saved.map((p) => ({
      key: p.id,
      label: p.name,
      colours: (p.data as PaletteSpec).swatches ?? [],
      spec: p.data as PaletteSpec,
      image: p.preview_url,
    })),
    ...(vocab?.palettes ?? []).map((p) => ({
      key: p.id,
      label: p.label,
      colours: p.swatches,
      spec: { id: p.id },
      image: vocab?.previews?.palettes?.[p.id]?.image,
    })),
  ];
  const [choice, setChoice] = useState<string | null>(null);
  const [strength, setStrength] = useState(60);
  const [busy, setBusy] = useState(false);
  const picked = choices.find((c) => c.key === choice);

  async function start() {
    if (!picked) return;
    setBusy(true);
    const { error } = await fetchClient.POST("/api/cinema/grade/{asset_id}", {
      params: { path: { asset_id: assetId } },
      body: { palette: { ...picked.spec, label: picked.spec.label ?? picked.label }, strength: strength / 100 },
    });
    setBusy(false);
    if (error) return void toast.error(errorMessage(error));
    toast.success(t("grade.started", { name: picked.label }));
    onOpenChange(false);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-[min(94vw,44rem)] max-w-none" closeLabel={t("shot.close")}>
        <div className="flex flex-col gap-4" data-testid="grade-dialog">
          <div className="flex flex-col gap-1 pr-8">
            <DialogTitle>{t("grade.title")}</DialogTitle>
            <DialogDescription>{t("grade.hint")}</DialogDescription>
          </div>
          <ul
            role="radiogroup"
            aria-label={t("colourPalette.title")}
            className="grid max-h-[40vh] grid-cols-3 gap-2 overflow-y-auto sm:grid-cols-5"
          >
            {choices.map((c) => (
              <PaletteCard
                key={c.key}
                label={c.label}
                colours={c.colours}
                image={c.image}
                selected={choice === c.key}
                onClick={() => setChoice(c.key)}
              />
            ))}
          </ul>
          <label className="flex items-center gap-3 text-sm">
            <span className="font-medium">{t("grade.strength")}</span>
            <input
              type="range"
              min={0}
              max={100}
              step={5}
              value={strength}
              onChange={(e) => setStrength(Number(e.target.value))}
              className="flex-1 accent-[var(--tungsten)]"
              aria-label={t("grade.strength")}
              data-testid="grade-strength"
            />
            <span className="w-10 text-right font-mono text-xs">{strength}%</span>
          </label>
          <div className="flex justify-end gap-2 border-t border-line pt-3">
            <Button variant="ghost" onClick={() => onOpenChange(false)}>
              {t("elements.cancel")}
            </Button>
            <Button variant="primary" disabled={!picked || busy} onClick={() => void start()} data-testid="grade-start">
              {t("grade.start")}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
