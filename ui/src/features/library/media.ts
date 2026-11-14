/** Small pure helpers for the viewer's players (unit tested). */

/** Time of the neighbouring frame, clamped to the clip. */
export function stepFrame(
  current: number,
  fps: number | null | undefined,
  direction: 1 | -1,
  duration: number,
): number {
  const rate = fps && fps > 0 ? fps : 24;
  const frame = Math.round(current * rate) + direction;
  const time = frame / rate;
  return Math.min(Math.max(0, time), Math.max(0, duration - 1 / (rate * 2)));
}

export interface ZoomState {
  scale: number;
  x: number;
  y: number;
}

export const FIT: ZoomState = { scale: 1, x: 0, y: 0 };
export const MIN_SCALE = 1;
export const MAX_SCALE = 8;

/** Zoom by ``factor`` keeping the point (px, py) — relative to the stage centre — still. */
export function zoomAt(state: ZoomState, factor: number, px: number, py: number): ZoomState {
  const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, state.scale * factor));
  if (scale === MIN_SCALE) return FIT;
  const ratio = scale / state.scale;
  return { scale, x: px - (px - state.x) * ratio, y: py - (py - state.y) * ratio };
}

/** Keep the zoomed image covering the stage: no panning it out of view. */
export function clampPan(state: ZoomState, stageW: number, stageH: number): ZoomState {
  if (state.scale <= 1) return FIT;
  const maxX = (stageW * (state.scale - 1)) / 2;
  const maxY = (stageH * (state.scale - 1)) / 2;
  return {
    scale: state.scale,
    x: Math.min(maxX, Math.max(-maxX, state.x)),
    y: Math.min(maxY, Math.max(-maxY, state.y)),
  };
}
