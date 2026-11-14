import { Aperture, Camera, Clapperboard, Gauge, History, X } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { Carousel, type CarouselOption } from "./Carousel";
import { Drum } from "./Drum";
import { type MovesEditor, MovesPicker } from "./MovesPicker";
import { PalettePicker, Swatches, usePaletteLook } from "./PalettePicker";
import { PreviewArt } from "./PreviewArt";
import {
  cameraSummary,
  cleanShot,
  filmSummary,
  type ShotSpec,
  type Term,
  useVocab,
  type VocabOut,
  withCamera,
  withFilm,
} from "./shot";

type Family = "genres" | "eras" | "tempos" | "bodies" | "lenses";
const ICONS = { genres: Clapperboard, eras: History, tempos: Gauge, bodies: Camera, lenses: Aperture } as const;

function cards(vocab: VocabOut | undefined, family: Family): CarouselOption[] {
  const items: Term[] = vocab?.[family] ?? [];
  return items.map((term) => ({
    id: term.id,
    label: term.label,
    art: (
      <PreviewArt
        id={term.id}
        label={term.label}
        preview={vocab?.previews?.[family]?.[term.id]}
        icon={ICONS[family]}
        monochrome={term.id === "1920s" || term.id === "noir"}
      />
    ),
  }));
}

/** Film setup (M5.2): genre, era and pace (video) as carousels of previews. */
export function FilmPicker({
  open,
  onOpenChange,
  shot,
  onChange,
  video,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  shot: ShotSpec | null | undefined;
  onChange: (shot: ShotSpec | null) => void;
  video: boolean;
}) {
  const { t } = useTranslation();
  const { data: vocab } = useVocab();
  const film = shot?.film;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-[min(94vw,52rem)] max-w-none" closeLabel={t("shot.close")}>
        <div className="flex flex-col gap-4" data-testid="film-picker">
          <div className="flex flex-col gap-1 pr-8">
            <DialogTitle>{t("shot.film")}</DialogTitle>
            <DialogDescription>{t("shot.filmHint")}</DialogDescription>
          </div>
          <Carousel
            label={t("shot.genre")}
            options={cards(vocab, "genres")}
            value={film?.genre}
            onChange={(genre) => onChange(withFilm(shot, { genre }))}
            noneLabel={t("shot.none")}
          />
          <Carousel
            label={t("shot.era")}
            options={cards(vocab, "eras")}
            value={film?.era}
            onChange={(era) => onChange(withFilm(shot, { era }))}
            noneLabel={t("shot.none")}
          />
          {video ? (
            <Carousel
              label={t("shot.tempo")}
              options={cards(vocab, "tempos")}
              value={film?.tempo}
              onChange={(tempo) => onChange(withFilm(shot, { tempo }))}
              noneLabel={t("shot.none")}
            />
          ) : null}
          <Footer
            onReset={() => onChange(withFilm(shot, { genre: null, era: null, tempo: null }))}
            onDone={() => onOpenChange(false)}
          />
        </div>
      </DialogContent>
    </Dialog>
  );
}

/** Camera setup (M5.2): body, lens, focal length and aperture on drums. */
export function CameraPicker({
  open,
  onOpenChange,
  shot,
  onChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  shot: ShotSpec | null | undefined;
  onChange: (shot: ShotSpec | null) => void;
}) {
  const { t } = useTranslation();
  const { data: vocab } = useVocab();
  const camera = shot?.camera;
  const lens = vocab?.lenses.find((l) => l.id === camera?.lens);
  const body = vocab?.bodies.find((b) => b.id === camera?.body);
  const shown = lens ?? body;
  const shownFamily: Family = lens ? "lenses" : "bodies";
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-[min(94vw,46rem)] max-w-none" closeLabel={t("shot.close")}>
        <div className="flex flex-col gap-4" data-testid="camera-picker">
          <div className="flex flex-col gap-1 pr-8">
            <DialogTitle>{t("shot.camera")}</DialogTitle>
            <DialogDescription>{t("shot.cameraHint")}</DialogDescription>
          </div>
          <div className="flex flex-col gap-4 sm:flex-row">
            <div className="grid flex-1 grid-cols-2 gap-3 sm:grid-cols-4">
              <Drum
                label={t("shot.body")}
                options={(vocab?.bodies ?? []).map((b) => ({ id: b.id, label: b.label }))}
                value={camera?.body}
                onChange={(id) => onChange(withCamera(shot, { body: id }))}
                noneLabel={t("shot.none")}
              />
              <Drum
                label={t("shot.lens")}
                options={(vocab?.lenses ?? []).map((l) => ({ id: l.id, label: l.label }))}
                value={camera?.lens}
                onChange={(id) => onChange(withCamera(shot, { lens: id }))}
                noneLabel={t("shot.none")}
              />
              <Drum
                label={t("shot.focal")}
                options={(vocab?.focal_lengths ?? []).map((mm) => ({ id: String(mm), label: `${mm}mm` }))}
                value={camera?.focal_length ? String(camera.focal_length) : null}
                onChange={(id) => onChange(withCamera(shot, { focal_length: id ? Number(id) : null }))}
                noneLabel={t("shot.none")}
              />
              <Drum
                label={t("shot.aperture")}
                options={(vocab?.apertures ?? []).map((f) => ({ id: f, label: f }))}
                value={camera?.aperture}
                onChange={(id) => onChange(withCamera(shot, { aperture: id }))}
                noneLabel={t("shot.none")}
              />
            </div>
            <div className="mx-auto w-36 shrink-0 overflow-hidden rounded-lg bg-raised sm:mx-0">
              <span className="block aspect-[3/4]">
                {shown ? (
                  <PreviewArt
                    id={shown.id}
                    label={shown.label}
                    preview={vocab?.previews?.[shownFamily]?.[shown.id]}
                    icon={ICONS[shownFamily]}
                    showLabel
                  />
                ) : (
                  <span className="flex size-full items-center justify-center p-3 text-center text-xs text-muted">
                    {t("shot.cameraEmpty")}
                  </span>
                )}
              </span>
            </div>
          </div>
          <Footer
            onReset={() => onChange(withCamera(shot, { body: null, lens: null, focal_length: null, aperture: null }))}
            onDone={() => onOpenChange(false)}
          />
        </div>
      </DialogContent>
    </Dialog>
  );
}

function Footer({ onReset, onDone }: { onReset: () => void; onDone: () => void }) {
  const { t } = useTranslation();
  return (
    <div className="flex items-center justify-between gap-2 border-t border-line pt-3">
      <Button variant="ghost" onClick={onReset} data-testid="shot-reset">
        {t("shot.reset")}
      </Button>
      <Button variant="primary" onClick={onDone} data-testid="shot-done">
        {t("shot.done")}
      </Button>
    </div>
  );
}

/** The shot's choices as chips above the prompt (M5.2); each opens its picker. */
export function ShotChips({
  shot,
  onChange,
  video,
  moves,
}: {
  shot: ShotSpec | null | undefined;
  onChange: (shot: ShotSpec | null) => void;
  video: boolean;
  /** The prompt's camera moves (video only, M5.3). */
  moves?: MovesEditor;
}) {
  const { t } = useTranslation();
  const { data: vocab } = useVocab();
  const [open, setOpen] = useState<"film" | "camera" | "moves" | "palette" | null>(null);
  const look = usePaletteLook(shot?.palette);
  const moveLabel = (id: string) => vocab?.moves.find((m) => m.id === id)?.label ?? id;
  const chips = [
    {
      key: "film" as const,
      label: t("shot.film"),
      values: filmSummary(shot, vocab, video),
      clear: () => onChange(withFilm(shot, { genre: null, era: null, tempo: null })),
    },
    {
      key: "camera" as const,
      label: t("shot.camera"),
      values: cameraSummary(shot, vocab),
      clear: () => onChange(withCamera(shot, { body: null, lens: null, focal_length: null, aperture: null })),
    },
    {
      key: "palette" as const,
      label: t("colourPalette.chip"),
      values: look ? [look.label] : [],
      swatches: look?.colours,
      clear: () => onChange(cleanShot({ ...shot, palette: null })),
    },
    ...(video && moves
      ? [
          {
            key: "moves" as const,
            label: t("shot.moves"),
            values: moves.list.length ? [moves.list.map(moveLabel).join(" → ")] : [],
            clear: moves.clear,
          },
        ]
      : []),
  ];
  return (
    <div className="flex flex-wrap items-center gap-1.5" data-testid="shot-chips">
      {chips.map((chip) => {
        const set = chip.values.length > 0;
        return (
          <span
            key={chip.key}
            className={cn(
              "inline-flex h-7 items-center rounded-full border text-xs",
              set ? "border-tungsten/50 bg-tungsten/10 text-ink" : "border-line text-muted",
            )}
          >
            <button
              type="button"
              onClick={() => setOpen(chip.key)}
              className="flex h-full items-center gap-1 rounded-full px-2.5 hover:text-ink"
              aria-label={set ? `${chip.label}: ${chip.values.join(", ")}` : chip.label}
              data-testid={`chip-${chip.key}`}
            >
              <span className={cn(set && "text-muted")}>{chip.label}</span>
              {"swatches" in chip && chip.swatches?.length ? (
                <Swatches colours={chip.swatches.slice(0, 6)} className="h-3 w-10" />
              ) : null}
              {set ? <span className="font-medium">{chip.values.join(" · ")}</span> : <span aria-hidden>+</span>}
            </button>
            {set ? (
              <button
                type="button"
                onClick={chip.clear}
                className="mr-1 rounded-full p-0.5 text-muted hover:text-ink"
                aria-label={t("shot.clear", { name: chip.label })}
                data-testid={`chip-${chip.key}-clear`}
              >
                <X className="size-3" />
              </button>
            ) : null}
          </span>
        );
      })}
      <FilmPicker
        open={open === "film"}
        onOpenChange={(next) => !next && setOpen(null)}
        shot={shot}
        onChange={onChange}
        video={video}
      />
      <PalettePicker
        open={open === "palette"}
        onOpenChange={(next) => !next && setOpen(null)}
        shot={shot}
        onChange={(palette) => onChange(cleanShot({ ...shot, palette }))}
      />
      {moves ? (
        <MovesPicker open={open === "moves"} onOpenChange={(next) => !next && setOpen(null)} moves={moves} />
      ) : null}
      <CameraPicker
        open={open === "camera"}
        onOpenChange={(next) => !next && setOpen(null)}
        shot={shot}
        onChange={onChange}
      />
    </div>
  );
}
