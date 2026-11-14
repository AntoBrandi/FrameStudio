import { CheckCheck, Heart, HeartOff, RotateCcw, Trash2, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import type { MoveTarget } from "./api";
import { MoveMenu } from "./MoveMenu";

export interface BulkBarProps {
  count: number;
  trashed: boolean;
  projectId?: string;
  onSelectAll: () => void;
  onClear: () => void;
  onMove: (target: MoveTarget) => void;
  onFavorite: (value: boolean) => void;
  onTrash: () => void;
  onRestore: () => void;
}

/** Actions for the selected assets, floating above the grid. */
export function BulkBar(props: BulkBarProps) {
  const { t } = useTranslation();
  if (props.count === 0) return null;
  return (
    <div
      role="toolbar"
      aria-label={t("library.selected", { count: props.count })}
      data-testid="bulk-bar"
      className="pointer-events-auto absolute bottom-4 left-1/2 z-20 flex w-max max-w-[calc(100%-2rem)] -translate-x-1/2 flex-wrap items-center gap-1.5 rounded-2xl border border-ink/25 bg-flat/95 px-3 py-2 shadow-2xl backdrop-blur"
    >
      <span className="mr-1 font-mono text-xs" aria-live="polite">
        {t("library.selected", { count: props.count })}
      </span>
      <Button size="sm" variant="ghost" onClick={props.onSelectAll}>
        <CheckCheck />
        {t("library.selectAll")}
      </Button>
      {props.trashed ? (
        <Button size="sm" variant="outline" onClick={props.onRestore}>
          <RotateCcw />
          {t("library.restore")}
        </Button>
      ) : (
        <>
          <MoveMenu projectId={props.projectId} onMove={props.onMove} />
          <Button size="sm" variant="outline" onClick={() => props.onFavorite(true)}>
            <Heart />
            {t("library.favorite")}
          </Button>
          <Button size="sm" variant="outline" onClick={() => props.onFavorite(false)}>
            <HeartOff />
            {t("library.unfavorite")}
          </Button>
          <Button size="sm" variant="danger" onClick={props.onTrash}>
            <Trash2 />
            {t("library.toTrash")}
          </Button>
        </>
      )}
      <Button
        size="icon"
        variant="ghost"
        className="size-7"
        aria-label={t("library.clearSelection")}
        onClick={props.onClear}
      >
        <X />
      </Button>
    </div>
  );
}
