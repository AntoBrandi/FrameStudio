import { Download, LayoutGrid, Search, SlidersHorizontal, Upload, X } from "lucide-react";
import { type ChangeEvent, type ReactNode, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, buttonVariants } from "@/components/ui/button";
import { inputClass, Popover, PopoverContent, PopoverTrigger } from "@/components/ui/misc";
import { cn } from "@/lib/utils";
import type { Kind } from "./api";
import { type FilterPrefs, hasActiveFilters, type ViewPrefs } from "./prefs";

const KINDS: Kind[] = ["image", "video", "audio"];

function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "h-7 rounded-full border px-2.5 text-xs transition-colors",
        active ? "border-ink/60 bg-raised text-ink" : "border-line text-muted hover:text-ink",
      )}
    >
      {children}
    </button>
  );
}

/** Layout, card size and day grouping for a gallery. */
export function ViewOptions({ view, onView }: { view: ViewPrefs; onView: (patch: Partial<ViewPrefs>) => void }) {
  const { t } = useTranslation();
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm" aria-label={t("library.view")}>
          <LayoutGrid />
          {t("library.view")}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="flex w-64 flex-col gap-3" aria-label={t("library.view")}>
        <div role="group" aria-label={t("library.view")} className="flex gap-1">
          <Chip active={view.mode === "masonry"} onClick={() => onView({ mode: "masonry" })}>
            {t("library.masonry")}
          </Chip>
          <Chip active={view.mode === "square"} onClick={() => onView({ mode: "square" })}>
            {t("library.square")}
          </Chip>
        </div>
        <label className="flex flex-col gap-1 text-xs">
          {t("library.cardSize")}
          <input
            type="range"
            min={120}
            max={420}
            step={20}
            value={view.size}
            onChange={(e) => onView({ size: Number(e.target.value) })}
            className="accent-[var(--tungsten)]"
          />
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            className="accent-[var(--tungsten)]"
            checked={view.groupByDay}
            onChange={(e) => onView({ groupByDay: e.target.checked })}
          />
          {t("library.splitByDay")}
        </label>
      </PopoverContent>
    </Popover>
  );
}

export interface FilterBarProps {
  title: string;
  total?: number;
  query: string;
  onQuery: (value: string) => void;
  filters: FilterPrefs;
  onFilters: (patch: Partial<FilterPrefs>) => void;
  onResetFilters: () => void;
  view: ViewPrefs;
  onView: (patch: Partial<ViewPrefs>) => void;
  models: string[];
  onImport?: (files: File[]) => void;
  /** A download link for everything here (a project's export zip, M5.6). */
  exportHref?: string;
  /** Hide the liked toggle (the Favorites view is liked by definition). */
  hideLiked?: boolean;
}

export function FilterBar(props: FilterBarProps) {
  const { t } = useTranslation();
  const { filters, onFilters, view, onView } = props;
  const [draft, setDraft] = useState(props.query);
  const fileInput = useRef<HTMLInputElement>(null);
  const onQuery = props.onQuery;

  // Follow the URL when it changes elsewhere (back button, a sidebar link).
  const [seenQuery, setSeenQuery] = useState(props.query);
  if (props.query !== seenQuery) {
    setSeenQuery(props.query);
    setDraft(props.query);
  }
  useEffect(() => {
    if (draft === props.query) return;
    const timer = setTimeout(() => onQuery(draft), 250);
    return () => clearTimeout(timer);
  }, [draft, onQuery, props.query]);

  const toggleKind = (kind: Kind) =>
    onFilters({
      kinds: filters.kinds.includes(kind) ? filters.kinds.filter((k) => k !== kind) : [...filters.kinds, kind],
    });

  function onPick(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (files.length) props.onImport?.(files);
  }

  return (
    <div className="flex flex-col gap-2 border-b border-line px-4 py-3" data-testid="filter-bar">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="mr-2 font-display text-2xl font-bold uppercase tracking-wide">{props.title}</h1>
        {props.total !== undefined ? (
          <span className="font-mono text-xs text-muted" data-testid="asset-total">
            {t("library.count", { count: props.total })}
          </span>
        ) : null}
        <label className="relative ml-auto min-w-56 flex-1 sm:max-w-md">
          <span className="sr-only">{t("library.search")}</span>
          <Search className="pointer-events-none absolute left-2.5 top-2.5 size-4 text-muted" aria-hidden />
          <input
            type="search"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") onQuery(draft);
            }}
            placeholder={t("library.search")}
            title={t("library.searchHelp")}
            className={cn(inputClass, "w-full pl-8")}
            data-testid="library-search"
          />
        </label>
        <ViewOptions view={view} onView={onView} />
        {props.onImport ? (
          <>
            <input
              ref={fileInput}
              type="file"
              multiple
              accept="image/*,video/*,audio/*"
              className="hidden"
              onChange={onPick}
              data-testid="import-input"
            />
            <Button variant="primary" size="sm" onClick={() => fileInput.current?.click()}>
              <Upload />
              {t("library.import")}
            </Button>
          </>
        ) : null}
        {props.exportHref ? (
          <a
            href={props.exportHref}
            download
            className={buttonVariants({ variant: "outline", size: "sm" })}
            data-testid="export-project"
          >
            <Download />
            {t("library.export")}
          </a>
        ) : null}
      </div>
      <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label={t("library.filters")}>
        <SlidersHorizontal className="mr-1 size-4 text-muted" aria-hidden />
        {KINDS.map((kind) => (
          <Chip key={kind} active={filters.kinds.includes(kind)} onClick={() => toggleKind(kind)}>
            {t(`library.kinds.${kind}`)}
          </Chip>
        ))}
        <select
          aria-label={t("library.model")}
          className="h-7 rounded-full border border-line bg-transparent px-2 text-xs text-muted"
          value={filters.models[0] ?? ""}
          onChange={(e) => onFilters({ models: e.target.value ? [e.target.value] : [] })}
        >
          <option value="">{t("library.anyModel")}</option>
          {props.models.map((model) => (
            <option key={model} value={model}>
              {model === "upload" ? t("library.uploaded") : model}
            </option>
          ))}
        </select>
        <label className="flex items-center gap-1 text-xs text-muted">
          {t("library.from")}
          <input
            type="date"
            value={filters.from}
            onChange={(e) => onFilters({ from: e.target.value })}
            className="h-7 rounded-md border border-line bg-transparent px-1.5 text-xs text-ink"
          />
        </label>
        <label className="flex items-center gap-1 text-xs text-muted">
          {t("library.to")}
          <input
            type="date"
            value={filters.to}
            onChange={(e) => onFilters({ to: e.target.value })}
            className="h-7 rounded-md border border-line bg-transparent px-1.5 text-xs text-ink"
          />
        </label>
        {props.hideLiked ? null : (
          <Chip active={filters.liked} onClick={() => onFilters({ liked: !filters.liked })}>
            {t("library.liked")}
          </Chip>
        )}
        <Chip active={filters.downloaded} onClick={() => onFilters({ downloaded: !filters.downloaded })}>
          {t("library.downloaded")}
        </Chip>
        {hasActiveFilters(filters) ? (
          <Button variant="ghost" size="sm" onClick={props.onResetFilters}>
            <X />
            {t("library.clearFilters")}
          </Button>
        ) : null}
      </div>
    </div>
  );
}
