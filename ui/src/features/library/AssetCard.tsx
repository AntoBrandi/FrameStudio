import { AudioLines, Heart, Play } from "lucide-react";
import { type DragEvent, memo, type MouseEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import type { AssetOut } from "@/api/client";
import { cn } from "@/lib/utils";
import { ASSET_DRAG_TYPE, assetName } from "./api";

export type Badge = "new" | "lastViewed" | "lastDownloaded";

export function formatDuration(seconds: number | null | undefined): string {
  if (!seconds || seconds < 0) return "";
  const total = Math.round(seconds);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export interface AssetCardProps {
  asset: AssetOut;
  selected: boolean;
  selecting: boolean;
  badge?: Badge;
  onActivate: (asset: AssetOut, event: MouseEvent) => void;
  onToggleSelect: (asset: AssetOut, shift: boolean) => void;
  onFavorite?: (asset: AssetOut) => void;
  /** Ids to drag along when this card is dragged (the selection, if it includes it). */
  dragIds: (asset: AssetOut) => string[];
}

export const AssetCard = memo(function AssetCard({
  asset,
  selected,
  selecting,
  badge,
  onActivate,
  onToggleSelect,
  onFavorite,
  dragIds,
}: AssetCardProps) {
  const { t } = useTranslation();
  const [hover, setHover] = useState(false);
  const name = assetName(asset, t("library.untitled"));
  const isVideo = asset.kind === "video";

  function onDragStart(event: DragEvent) {
    const ids = dragIds(asset);
    event.dataTransfer.setData(ASSET_DRAG_TYPE, JSON.stringify(ids));
    event.dataTransfer.setData("text/plain", ids.join("\n"));
    event.dataTransfer.effectAllowed = "move";
  }

  return (
    <article
      data-testid="asset-card"
      data-id={asset.id}
      data-selected={selected || undefined}
      className={cn(
        "group relative size-full overflow-hidden rounded-lg bg-raised ring-offset-2 ring-offset-stage",
        selected && "ring-2 ring-tungsten",
      )}
      draggable
      onDragStart={onDragStart}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      <button
        type="button"
        className="block size-full"
        aria-label={name}
        onClick={(event) => onActivate(asset, event)}
        data-testid="asset-open"
      >
        {asset.thumb_url ? (
          <img
            src={asset.thumb_url}
            alt=""
            loading="lazy"
            decoding="async"
            draggable={false}
            className={cn("size-full", asset.kind === "audio" ? "object-contain p-3" : "object-cover")}
          />
        ) : (
          <span className="flex size-full items-center justify-center text-xs text-muted">{asset.mime}</span>
        )}
        {isVideo && hover && asset.preview_url ? (
          <video
            src={asset.preview_url}
            muted
            loop
            autoPlay
            playsInline
            className="pointer-events-none absolute inset-0 size-full object-cover"
          />
        ) : null}
      </button>

      <label
        className={cn(
          "absolute left-2 top-2 flex size-6 cursor-pointer items-center justify-center rounded-md bg-black/50 transition-opacity",
          selected || selecting ? "opacity-100" : "opacity-0 group-hover:opacity-100 focus-within:opacity-100",
        )}
      >
        <input
          type="checkbox"
          className="size-4 accent-[var(--tungsten)]"
          checked={selected}
          aria-label={t("library.select", { name })}
          onClick={(event) => {
            event.stopPropagation();
            event.preventDefault();
            onToggleSelect(asset, event.shiftKey);
          }}
          onChange={() => undefined}
        />
      </label>

      {badge ? (
        <span
          className={cn(
            "absolute left-10 top-2.5 rounded-full px-2 py-px font-mono text-[10px] uppercase tracking-wide",
            badge === "new" ? "bg-tungsten text-tungsten-ink" : "bg-black/60 text-white",
          )}
          data-testid="asset-badge"
        >
          {t(`library.badges.${badge}`)}
        </span>
      ) : null}

      {onFavorite ? (
        <button
          type="button"
          onClick={() => onFavorite(asset)}
          aria-pressed={asset.favorite}
          aria-label={asset.favorite ? t("library.unfavorite") : t("library.favorite")}
          className={cn(
            "absolute right-2 top-2 flex size-7 items-center justify-center rounded-md bg-black/50 text-white transition-opacity",
            asset.favorite ? "opacity-100" : "opacity-0 group-hover:opacity-100 focus-visible:opacity-100",
          )}
        >
          <Heart className={cn("size-4", asset.favorite && "fill-rose-500 text-rose-500")} />
        </button>
      ) : null}

      {asset.kind !== "image" ? (
        <span className="pointer-events-none absolute bottom-2 right-2 flex items-center gap-1 rounded bg-black/60 px-1.5 py-px font-mono text-[11px] text-white">
          {isVideo ? <Play className="size-3" aria-hidden /> : <AudioLines className="size-3" aria-hidden />}
          {formatDuration(asset.duration)}
        </span>
      ) : null}

      <div className="pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/70 to-transparent p-2 opacity-0 transition-opacity group-hover:opacity-100">
        <p className="line-clamp-2 pr-12 text-xs text-white">{name}</p>
      </div>
    </article>
  );
});
