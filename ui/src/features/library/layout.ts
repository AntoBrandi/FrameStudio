/**
 * Gallery layout: positions for every card, computed up front so the grid can
 * render only what is on screen (10,000+ items stay smooth). Pure and tested.
 */

export interface LayoutItem {
  id: string;
  /** width / height; clamped so extreme panoramas stay usable. */
  aspect: number;
  date: Date;
}

export interface LayoutOptions {
  width: number;
  /** Target card width; columns are derived from it. */
  cardSize: number;
  gap: number;
  mode: "masonry" | "square";
  groupByDay: boolean;
  headerHeight: number;
  /** Space after each day group. */
  sectionGap: number;
  now: Date;
  locale?: string;
}

export type Block =
  | { kind: "header"; key: string; label: string; x: number; y: number; w: number; h: number }
  | { kind: "item"; key: string; index: number; x: number; y: number; w: number; h: number };

export interface Layout {
  blocks: Block[];
  height: number;
  columns: number;
}

const MIN_ASPECT = 0.4;
const MAX_ASPECT = 2.5;

export function dayKey(date: Date): string {
  return `${date.getFullYear()}-${date.getMonth() + 1}-${date.getDate()}`;
}

export function dayLabel(date: Date, now: Date, locale?: string): string {
  const startOf = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((startOf(now) - startOf(date)) / 86_400_000);
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  const sameYear = date.getFullYear() === now.getFullYear();
  return new Intl.DateTimeFormat(locale, {
    weekday: "short",
    month: "short",
    day: "numeric",
    ...(sameYear ? {} : { year: "numeric" }),
  }).format(date);
}

export function computeLayout(items: LayoutItem[], options: LayoutOptions): Layout {
  const { width, gap, mode, groupByDay, headerHeight, sectionGap } = options;
  if (width <= 0) return { blocks: [], height: 0, columns: 1 };
  const columns = Math.max(1, Math.floor((width + gap) / (options.cardSize + gap)));
  const colWidth = (width - gap * (columns - 1)) / columns;
  const blocks: Block[] = [];
  let y = 0;
  let index = 0;

  while (index < items.length) {
    const first = items[index]!;
    const groupKey = groupByDay ? dayKey(first.date) : "all";
    let end = index + 1;
    if (groupByDay) {
      while (end < items.length && dayKey(items[end]!.date) === groupKey) end += 1;
    } else {
      end = items.length;
    }
    if (groupByDay) {
      blocks.push({
        kind: "header",
        key: `h:${groupKey}`,
        label: dayLabel(first.date, options.now, options.locale),
        x: 0,
        y,
        w: width,
        h: headerHeight,
      });
      y += headerHeight;
    }
    const heights = new Array<number>(columns).fill(y);
    for (let i = index; i < end; i += 1) {
      const item = items[i]!;
      let column: number;
      let h: number;
      if (mode === "square") {
        column = (i - index) % columns;
        h = colWidth;
        // Rows stay aligned: every column of a row starts at the row's top.
        if (column === 0 && i !== index) {
          const top = Math.max(...heights);
          heights.fill(top);
        }
      } else {
        column = heights.indexOf(Math.min(...heights));
        const aspect = Math.min(MAX_ASPECT, Math.max(MIN_ASPECT, item.aspect || 1));
        h = colWidth / aspect;
      }
      const top = heights[column]!;
      blocks.push({ kind: "item", key: item.id, index: i, x: column * (colWidth + gap), y: top, w: colWidth, h });
      heights[column] = top + h + gap;
    }
    y = Math.max(...heights) - gap + sectionGap;
    index = end;
  }
  return { blocks, height: Math.max(0, y - sectionGap), columns };
}

/** Blocks overlapping the window [top, bottom], plus an overscan margin. */
export function visibleBlocks(layout: Layout, top: number, bottom: number, overscan = 600): Block[] {
  const from = top - overscan;
  const to = bottom + overscan;
  return layout.blocks.filter((block) => block.y + block.h >= from && block.y <= to);
}
