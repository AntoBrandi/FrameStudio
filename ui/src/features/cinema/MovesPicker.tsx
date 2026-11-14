import { ChevronLeft, ChevronRight, Move, Search, X } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { inputClass } from "@/components/ui/misc";
import { cn } from "@/lib/utils";
import { PreviewArt } from "./PreviewArt";
import { useVocab } from "./shot";

export interface MovesEditor {
  /** The prompt's moves, in order. */
  list: string[];
  add: (id: string) => void;
  remove: (index: number) => void;
  swap: (a: number, b: number) => void;
  clear: () => void;
}

/** The camera move library (M5.3): about thirty moves with preview clips.
 * Picking one adds its #move chip to the prompt; the moves in the shot are
 * listed in order, to reorder or remove. */
export function MovesPicker({
  open,
  onOpenChange,
  moves,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  moves: MovesEditor;
}) {
  const { t } = useTranslation();
  const { data: vocab } = useVocab();
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();
  const all = vocab?.moves ?? [];
  const shown = all.filter((m) => !q || m.label.toLowerCase().includes(q) || m.id.includes(q));
  const labelOf = (id: string) => all.find((m) => m.id === id)?.label ?? id;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-[min(94vw,56rem)] max-w-none" closeLabel={t("shot.close")}>
        <div className="flex flex-col gap-3" data-testid="moves-picker">
          <div className="flex flex-col gap-1 pr-8">
            <DialogTitle>{t("shot.moves")}</DialogTitle>
            <DialogDescription>{t("shot.movesHint")}</DialogDescription>
          </div>
          <section aria-label={t("shot.inShot")} className="flex flex-wrap items-center gap-1.5">
            <span className="text-xs font-medium uppercase tracking-wide text-muted">{t("shot.inShot")}</span>
            {moves.list.length === 0 ? <span className="text-xs text-muted">{t("shot.noMoves")}</span> : null}
            <ol className="flex flex-wrap items-center gap-1.5" data-testid="moves-in-shot">
              {moves.list.map((id, index) => (
                <li
                  key={`${id}-${index}`}
                  className="inline-flex h-7 items-center gap-0.5 rounded-full border border-daylight/40 bg-daylight/10 pl-1 pr-1 text-xs"
                  data-move={id}
                >
                  <button
                    type="button"
                    className="rounded-full p-0.5 text-muted hover:text-ink disabled:opacity-30"
                    disabled={index === 0}
                    onClick={() => moves.swap(index, index - 1)}
                    aria-label={t("shot.earlier", { name: labelOf(id) })}
                  >
                    <ChevronLeft className="size-3.5" />
                  </button>
                  <span className="px-1 font-medium">
                    {index + 1}. {labelOf(id)}
                  </span>
                  <button
                    type="button"
                    className="rounded-full p-0.5 text-muted hover:text-ink disabled:opacity-30"
                    disabled={index === moves.list.length - 1}
                    onClick={() => moves.swap(index, index + 1)}
                    aria-label={t("shot.later", { name: labelOf(id) })}
                  >
                    <ChevronRight className="size-3.5" />
                  </button>
                  <button
                    type="button"
                    className="rounded-full p-0.5 text-muted hover:text-danger"
                    onClick={() => moves.remove(index)}
                    aria-label={t("shot.removeMove", { name: labelOf(id) })}
                  >
                    <X className="size-3.5" />
                  </button>
                </li>
              ))}
            </ol>
          </section>
          <label className={cn(inputClass, "flex items-center gap-2")}>
            <Search className="size-4 shrink-0 text-muted" aria-hidden />
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("shot.searchMoves")}
              aria-label={t("shot.searchMoves")}
              className="min-w-0 flex-1 bg-transparent outline-none"
              data-testid="moves-search"
            />
          </label>
          <ul className="grid max-h-[46vh] grid-cols-3 gap-2 overflow-y-auto sm:grid-cols-5" data-testid="move-library">
            {shown.map((move) => {
              const count = moves.list.filter((m) => m === move.id).length;
              return (
                <li key={move.id}>
                  <button
                    type="button"
                    onClick={() => moves.add(move.id)}
                    className={cn(
                      "flex w-full flex-col overflow-hidden rounded-lg border-2 bg-raised text-left outline-none focus-visible:ring-2 focus-visible:ring-tungsten",
                      count ? "border-daylight" : "border-transparent hover:border-line",
                    )}
                    aria-label={t("shot.addMove", { name: move.label })}
                    data-move={move.id}
                  >
                    <span className="relative block aspect-video w-full">
                      <PreviewArt
                        id={move.id}
                        label={move.label}
                        preview={vocab?.previews?.moves?.[move.id]}
                        icon={Move}
                      />
                      {count ? (
                        <span className="absolute right-1 top-1 rounded-full bg-daylight px-1.5 text-[10px] font-semibold text-stage">
                          {count}
                        </span>
                      ) : null}
                    </span>
                    <span className="truncate px-2 pt-1 text-xs">{move.label}</span>
                    <span className="truncate px-2 pb-1 font-mono text-[10px] text-daylight">#{move.id}</span>
                  </button>
                </li>
              );
            })}
          </ul>
          <div className="flex items-center justify-between gap-2 border-t border-line pt-3">
            <Button variant="ghost" onClick={moves.clear} disabled={!moves.list.length} data-testid="moves-clear">
              {t("shot.reset")}
            </Button>
            <Button variant="primary" onClick={() => onOpenChange(false)} data-testid="moves-done">
              {t("shot.done")}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
