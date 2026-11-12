/**
 * Multi-select with a range anchor: click toggles, Shift+click selects the
 * range from the anchor, Ctrl/Cmd+click toggles without moving the range.
 */

export interface SelectionState {
  selected: ReadonlySet<string>;
  anchor: string | null;
}

export const EMPTY_SELECTION: SelectionState = { selected: new Set(), anchor: null };

export function toggle(state: SelectionState, id: string): SelectionState {
  const selected = new Set(state.selected);
  if (selected.has(id)) selected.delete(id);
  else selected.add(id);
  return { selected, anchor: id };
}

/** Select every id between the anchor and ``id`` (inclusive) in display order. */
export function selectRange(state: SelectionState, order: readonly string[], id: string): SelectionState {
  const end = order.indexOf(id);
  const start = state.anchor ? order.indexOf(state.anchor) : -1;
  if (end === -1) return state;
  if (start === -1) return toggle(state, id);
  const [from, to] = start <= end ? [start, end] : [end, start];
  const selected = new Set(state.selected);
  for (const value of order.slice(from, to + 1)) selected.add(value);
  return { selected, anchor: state.anchor };
}

export function selectAll(order: readonly string[]): SelectionState {
  return { selected: new Set(order), anchor: order[0] ?? null };
}

/** Drop ids that are no longer listed (e.g. moved out of the current folder). */
export function prune(state: SelectionState, order: readonly string[]): SelectionState {
  const present = new Set(order);
  const selected = new Set([...state.selected].filter((id) => present.has(id)));
  if (selected.size === state.selected.size) return state;
  return { selected, anchor: state.anchor && present.has(state.anchor) ? state.anchor : null };
}
