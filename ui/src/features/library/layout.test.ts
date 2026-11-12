import { describe, expect, it } from "vitest";
import { computeLayout, dayLabel, type LayoutItem, type LayoutOptions, visibleBlocks } from "./layout";
import { EMPTY_SELECTION, prune, selectAll, selectRange, toggle } from "./selection";

const now = new Date(2026, 10, 12, 15, 0);
const day = (offset: number, hour = 12) => new Date(2026, 10, 12 - offset, hour);
const options: LayoutOptions = {
  width: 1000,
  cardSize: 240,
  gap: 10,
  mode: "masonry",
  groupByDay: true,
  headerHeight: 40,
  sectionGap: 24,
  now,
  locale: "en-US",
};

function items(count: number, aspect = 1, date = day(0)): LayoutItem[] {
  return Array.from({ length: count }, (_, i) => ({ id: `a${i}`, aspect, date }));
}

describe("gallery layout", () => {
  it("derives columns from the card size", () => {
    expect(computeLayout(items(1), options).columns).toBe(4);
    expect(computeLayout(items(1), { ...options, cardSize: 120 }).columns).toBe(7);
    expect(computeLayout(items(1), { ...options, width: 100 }).columns).toBe(1);
  });

  it("places masonry items in the shortest column", () => {
    const list: LayoutItem[] = [
      { id: "tall", aspect: 0.5, date: day(0) },
      { id: "w1", aspect: 2, date: day(0) },
      { id: "w2", aspect: 2, date: day(0) },
      { id: "w3", aspect: 2, date: day(0) },
      { id: "next", aspect: 1, date: day(0) },
    ];
    const layout = computeLayout(list, { ...options, groupByDay: false });
    const byId = Object.fromEntries(layout.blocks.map((b) => [b.key, b]));
    expect(byId.tall!.h).toBeCloseTo(byId.w1!.w * 2);
    // "next" goes under a short card, not under the tall one.
    expect(byId.next!.x).not.toBe(byId.tall!.x);
    expect(byId.next!.y).toBeCloseTo(byId.w1!.h + 10);
  });

  it("keeps square rows aligned", () => {
    const layout = computeLayout(items(6), { ...options, mode: "square", groupByDay: false });
    const tops = layout.blocks.map((b) => Math.round(b.y));
    expect(tops.slice(0, 4).every((t) => t === tops[0])).toBe(true);
    expect(tops[4]).toBe(tops[5]);
    expect(tops[4]).toBeGreaterThan(tops[0]!);
  });

  it("splits by day with readable headers", () => {
    const list = [...items(2, 1, day(0)), ...items(2, 1, day(1)).map((i) => ({ ...i, id: `y${i.id}` }))];
    list.push({ id: "old", aspect: 1, date: new Date(2025, 0, 3) });
    const layout = computeLayout(list, options);
    const headers = layout.blocks.filter((b) => b.kind === "header").map((b) => (b.kind === "header" ? b.label : ""));
    expect(headers).toEqual(["Today", "Yesterday", "Fri, Jan 3, 2025"]);
    expect(dayLabel(day(3), now, "en-US")).toBe("Mon, Nov 9");
  });

  it("clamps extreme aspect ratios", () => {
    const [block] = computeLayout([{ id: "pano", aspect: 20, date: day(0) }], { ...options, groupByDay: false }).blocks;
    expect(block!.h).toBeCloseTo(block!.w / 2.5);
  });

  it("lays out 10,000 items fast and windows them", () => {
    const list = items(10_000);
    const started = performance.now();
    const layout = computeLayout(list, options);
    const visible = visibleBlocks(layout, 50_000, 51_000, 200);
    const elapsed = performance.now() - started;
    expect(layout.blocks).toHaveLength(10_001);
    expect(visible.length).toBeLessThan(40);
    expect(visible.length).toBeGreaterThan(4);
    expect(elapsed).toBeLessThan(200);
  });
});

describe("selection", () => {
  const order = ["a", "b", "c", "d", "e"];

  it("toggles and selects ranges from the anchor", () => {
    let state = toggle(EMPTY_SELECTION, "b");
    state = selectRange(state, order, "d");
    expect([...state.selected].sort()).toEqual(["b", "c", "d"]);
    state = selectRange(state, order, "a"); // range from the same anchor, backwards
    expect([...state.selected].sort()).toEqual(["a", "b", "c", "d"]);
    state = toggle(state, "c");
    expect(state.selected.has("c")).toBe(false);
    expect(state.anchor).toBe("c");
  });

  it("starts a range without an anchor as a toggle", () => {
    expect([...selectRange(EMPTY_SELECTION, order, "c").selected]).toEqual(["c"]);
  });

  it("selects all and prunes ids that left the view", () => {
    const all = selectAll(order);
    expect(all.selected.size).toBe(5);
    const pruned = prune(all, ["a", "e"]);
    expect([...pruned.selected]).toEqual(["a", "e"]);
    expect(prune(pruned, ["a", "e"])).toBe(pruned);
  });
});
