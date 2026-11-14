import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { computeLayout, type LayoutItem, visibleBlocks } from "./layout";
import type { ViewPrefs } from "./prefs";

export interface GalleryProps<T> {
  items: T[];
  toLayout: (item: T) => LayoutItem;
  renderItem: (item: T, index: number, box: { w: number; h: number }) => ReactNode;
  view: ViewPrefs;
  hasMore?: boolean;
  loadingMore?: boolean;
  onLoadMore?: () => void;
  empty?: ReactNode;
  footer?: ReactNode;
  label: string;
  className?: string;
  /** Space kept free at the bottom (for a docked composer or bulk bar). */
  bottomPadding?: number;
  /** Scroll position is restored for the same key. */
  scrollKey?: string;
  /** Exposes the scroll container (drop target for imports). */
  containerRef?: (element: HTMLDivElement | null) => void;
}

const GAP = 8;
const HEADER = 40;
const SECTION_GAP = 20;
const PADDING = 16;

/** Virtualized masonry or square grid: only cards near the viewport exist in
 * the DOM, so very large libraries scroll smoothly. */
export function Gallery<T>({
  items,
  toLayout,
  renderItem,
  view,
  hasMore,
  loadingMore,
  onLoadMore,
  empty,
  footer,
  label,
  className,
  bottomPadding = 24,
  containerRef,
}: GalleryProps<T>) {
  const scroller = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(0);
  const [viewport, setViewport] = useState({ top: 0, height: 800 });

  const setRef = useCallback(
    (element: HTMLDivElement | null) => {
      scroller.current = element;
      containerRef?.(element);
    },
    [containerRef],
  );

  useEffect(() => {
    const element = scroller.current;
    if (!element) return;
    const measure = () => {
      setWidth(Math.max(0, element.clientWidth - PADDING * 2));
      setViewport({ top: element.scrollTop, height: element.clientHeight || 800 });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    let frame = 0;
    const onScroll = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => setViewport({ top: element.scrollTop, height: element.clientHeight }));
    };
    element.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      observer.disconnect();
      element.removeEventListener("scroll", onScroll);
      cancelAnimationFrame(frame);
    };
  }, []);

  const layoutItems = useMemo(() => items.map(toLayout), [items, toLayout]);
  const layout = useMemo(
    () =>
      computeLayout(layoutItems, {
        width,
        cardSize: view.size,
        gap: GAP,
        mode: view.mode,
        groupByDay: view.groupByDay,
        headerHeight: HEADER,
        sectionGap: SECTION_GAP,
        now: new Date(),
      }),
    [layoutItems, width, view.size, view.mode, view.groupByDay],
  );
  const blocks = visibleBlocks(layout, viewport.top - PADDING, viewport.top + viewport.height);

  // Fetch the next page when the end of what is loaded comes into view.
  useEffect(() => {
    if (!hasMore || loadingMore || !onLoadMore) return;
    if (viewport.top + viewport.height * 2.5 >= layout.height) onLoadMore();
  }, [hasMore, loadingMore, onLoadMore, viewport, layout.height]);

  return (
    <div
      ref={setRef}
      className={cn("relative min-h-0 flex-1 overflow-y-auto overflow-x-hidden", className)}
      data-testid="gallery"
      aria-label={label}
      role="region"
      tabIndex={-1}
    >
      {items.length === 0 ? (
        <div className="flex h-full items-center justify-center p-8 text-center text-muted">{empty}</div>
      ) : (
        <div
          className="relative"
          style={{ height: layout.height + PADDING * 2 + bottomPadding, margin: `0 ${PADDING}px` }}
        >
          {blocks.map((block) =>
            block.kind === "header" ? (
              <h3
                key={block.key}
                className="absolute flex items-end pb-2 font-mono text-[11px] uppercase tracking-widest text-muted"
                style={{ top: block.y + PADDING, left: 0, width: block.w, height: block.h }}
              >
                {block.label}
              </h3>
            ) : (
              <div
                key={block.key}
                className="absolute"
                style={{ top: block.y + PADDING, left: block.x, width: block.w, height: block.h }}
              >
                {renderItem(items[block.index]!, block.index, { w: block.w, h: block.h })}
              </div>
            ),
          )}
        </div>
      )}
      {footer}
    </div>
  );
}
