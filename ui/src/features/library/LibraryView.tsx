import { useQuery } from "@tanstack/react-query";
import { Upload } from "lucide-react";
import { type MouseEvent, type ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { type AssetOut, fetchClient } from "@/api/client";
import { AssetCard, type Badge } from "./AssetCard";
import { AssetViewer } from "./AssetViewer";
import { BulkBar } from "./BulkBar";
import { FilterBar } from "./FilterBar";
import { Gallery } from "./Gallery";
import { flattenPages, type MoveTarget, useAssetPages, useLibraryActions, useSummary } from "./api";
import type { LayoutItem } from "./layout";
import { dateRange, hasActiveFilters, usePagePrefs } from "./prefs";
import { EMPTY_SELECTION, prune, selectAll, selectRange, type SelectionState, toggle } from "./selection";
import { useFileDrop, usePasteFiles, useUploader } from "./upload";

export interface LibraryScope {
  projectId?: string;
  /** A folder id, or "none" for unfiled assets. */
  folderId?: string;
  liked?: boolean;
  trashed?: boolean;
}

export interface LibraryViewProps {
  title: string;
  /** Key for remembered layout and filters. */
  pageKey: string;
  scope: LibraryScope;
  query: string;
  onQuery: (q: string) => void;
  view?: string;
  onView: (id: string | undefined) => void;
  onSetCover?: (asset: AssetOut) => void;
  /** A download link for everything in this scope (a project's export). */
  exportHref?: string;
  /** Shown between the filter bar and the grid (the Studio's generations in progress). */
  banner?: ReactNode;
  /** Room kept below the grid, e.g. for a floating composer. */
  bottomSpace?: number;
  /** Told when a selection starts or ends (the bulk bar takes the bottom edge). */
  onSelecting?: (selecting: boolean) => void;
}

const DAY = 86_400_000;

function toLayout(asset: AssetOut): LayoutItem {
  const aspect = asset.kind === "audio" ? 4 / 3 : asset.width && asset.height ? asset.width / asset.height : 1;
  return { id: asset.id, aspect, date: new Date(asset.created_at) };
}

export function LibraryView({
  title,
  pageKey,
  scope,
  query,
  onQuery,
  view,
  onView,
  onSetCover,
  exportHref,
  banner,
  bottomSpace = 24,
  onSelecting,
}: LibraryViewProps) {
  const { t } = useTranslation();
  const prefs = usePagePrefs(pageKey);
  const actions = useLibraryActions();
  const [dropTarget, setDropTarget] = useState<HTMLDivElement | null>(null);
  const trashed = Boolean(scope.trashed);

  const apiQuery = useMemo(
    () => ({
      ...(scope.projectId ? { project_id: scope.projectId } : {}),
      ...(scope.folderId ? { folder_id: scope.folderId } : {}),
      ...(trashed ? { trashed: true } : {}),
      ...(scope.liked || prefs.filters.liked ? { liked: true } : {}),
      ...(prefs.filters.downloaded ? { downloaded: true } : {}),
      ...(prefs.filters.kinds.length ? { kind: prefs.filters.kinds } : {}),
      ...(prefs.filters.models.length ? { model: prefs.filters.models } : {}),
      ...dateRange(prefs.filters),
      ...(query.trim() ? { q: query.trim() } : {}),
      limit: 200,
    }),
    [scope.projectId, scope.folderId, scope.liked, trashed, prefs.filters, query],
  );
  const pages = useAssetPages(apiQuery);
  const items = useMemo(() => flattenPages(pages.data?.pages), [pages.data]);
  const order = useMemo(() => items.map((item) => item.id), [items]);
  const total = pages.data?.pages[0]?.total;
  const { data: summary } = useSummary(scope.projectId);

  // Assets that left the view (moved, trashed, filtered out) leave the selection.
  const [rawSelection, setSelection] = useState<SelectionState>(EMPTY_SELECTION);
  const selection = useMemo(() => prune(rawSelection, order), [rawSelection, order]);

  const upload = useUploader({
    projectId: scope.projectId,
    folderId: scope.folderId && scope.folderId !== "none" ? scope.folderId : undefined,
  });
  const canImport = !trashed;
  const dragging = useFileDrop(dropTarget, (files) => void upload(files), canImport);
  usePasteFiles((files) => void upload(files), canImport && !view);

  // The viewer may be opened on an asset that is not loaded (a deep link).
  const loaded = view ? items.find((item) => item.id === view) : undefined;
  const single = useQuery({
    queryKey: ["get", "/api/assets/{asset_id}", { params: { path: { asset_id: view } } }],
    queryFn: async () =>
      (await fetchClient.GET("/api/assets/{asset_id}", { params: { path: { asset_id: view! } } })).data,
    enabled: Boolean(view) && !loaded,
  });
  const viewing = loaded ?? (view ? (single.data ?? undefined) : undefined);

  const selecting = selection.selected.size > 0;
  useEffect(() => onSelecting?.(selecting), [onSelecting, selecting]);
  const badgeFor = useCallback(
    (asset: AssetOut): Badge | undefined => {
      if (summary?.last_viewed_id === asset.id) return "lastViewed";
      if (summary?.last_downloaded_id === asset.id) return "lastDownloaded";
      if (!asset.last_viewed_at && Date.now() - new Date(asset.created_at).getTime() < DAY) return "new";
      return undefined;
    },
    [summary?.last_viewed_id, summary?.last_downloaded_id],
  );

  const onToggleSelect = useCallback(
    (asset: AssetOut, shift: boolean) =>
      setSelection((s) => (shift ? selectRange(s, order, asset.id) : toggle(s, asset.id))),
    [order],
  );
  const onActivate = useCallback(
    (asset: AssetOut, event: MouseEvent) => {
      if (event.shiftKey) return setSelection((s) => selectRange(s, order, asset.id));
      if (event.metaKey || event.ctrlKey || selecting) return setSelection((s) => toggle(s, asset.id));
      onView(asset.id);
    },
    [onView, order, selecting],
  );
  const dragIds = useCallback(
    (asset: AssetOut) => (selection.selected.has(asset.id) ? [...selection.selected] : [asset.id]),
    [selection],
  );
  const onFavorite = useCallback((asset: AssetOut) => void actions.favorite([asset.id], !asset.favorite), [actions]);

  // Escape clears the selection (the viewer handles its own Escape).
  const selectionSize = selection.selected.size;
  useEffect(() => {
    if (selectionSize === 0 || view) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSelection(EMPTY_SELECTION);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectionSize, view]);

  const ids = [...selection.selected];
  const clear = () => setSelection(EMPTY_SELECTION);
  const filtered = hasActiveFilters(prefs.filters) || Boolean(query.trim());

  return (
    <div className="relative flex h-full min-h-0 flex-col">
      <FilterBar
        title={title}
        total={total}
        query={query}
        onQuery={onQuery}
        filters={prefs.filters}
        onFilters={prefs.setFilters}
        onResetFilters={prefs.resetFilters}
        view={prefs.view}
        onView={prefs.setView}
        models={summary?.models ?? []}
        onImport={canImport ? (files) => void upload(files) : undefined}
        exportHref={exportHref}
        hideLiked={scope.liked}
      />
      {banner}
      <Gallery
        items={items}
        toLayout={toLayout}
        view={prefs.view}
        label={title}
        hasMore={pages.hasNextPage}
        loadingMore={pages.isFetchingNextPage}
        onLoadMore={() => void pages.fetchNextPage()}
        bottomPadding={Math.max(bottomSpace, selecting ? 96 : 24)}
        containerRef={setDropTarget}
        empty={
          pages.isPending
            ? null
            : trashed
              ? t("library.emptyTrash")
              : filtered
                ? t("library.emptyFiltered")
                : t("library.empty")
        }
        renderItem={(asset) => (
          <AssetCard
            asset={asset}
            selected={selection.selected.has(asset.id)}
            selecting={selecting}
            badge={trashed ? undefined : badgeFor(asset)}
            onActivate={onActivate}
            onToggleSelect={onToggleSelect}
            onFavorite={trashed ? undefined : onFavorite}
            dragIds={dragIds}
          />
        )}
        footer={
          pages.isFetchingNextPage ? (
            <p className="pb-6 text-center text-xs text-muted">{t("library.loadingMore")}</p>
          ) : null
        }
      />
      {dragging ? (
        <div className="pointer-events-none absolute inset-3 top-28 z-30 flex items-center justify-center rounded-2xl border-2 border-dashed border-tungsten bg-tungsten-soft/60 text-lg font-medium">
          <Upload className="mr-2 size-5" aria-hidden />
          {t("library.dropToImport")}
        </div>
      ) : null}
      <BulkBar
        count={selection.selected.size}
        trashed={trashed}
        projectId={scope.projectId}
        onSelectAll={() => setSelection(selectAll(order))}
        onClear={clear}
        onMove={(target: MoveTarget) => void actions.move(ids, target).then(clear)}
        onFavorite={(value) => void actions.favorite(ids, value)}
        onTrash={() => void actions.trash(ids).then(clear)}
        onRestore={() => void actions.restore(ids).then(clear)}
      />
      <AssetViewer
        asset={viewing}
        items={items}
        projectId={scope.projectId}
        onNavigate={(id) => onView(id)}
        onClose={() => onView(undefined)}
        onSetCover={onSetCover}
      />
    </div>
  );
}
