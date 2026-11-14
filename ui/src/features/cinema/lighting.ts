import type { Schemas } from "@/api/client";

export type Rig = Schemas["Rig"];
export type Light = Schemas["Light"];
export type LightingSpec = Schemas["LightingSpec"];

export const MAX_LIGHTS = 4;

export const DEFAULT_RIG: Rig = {
  lights: [{ azimuth: 330, elevation: 20, colour: "#FFFFFF", intensity: 0.9, softness: 0.5 }],
  ambient: 0.15,
};

/** Quick placements for the selected light. */
export const QUICK: Record<
  "top" | "front" | "left" | "right" | "back" | "bottom",
  { azimuth: number; elevation: number }
> = {
  top: { azimuth: 0, elevation: 80 },
  front: { azimuth: 0, elevation: 10 },
  left: { azimuth: 270, elevation: 10 },
  right: { azimuth: 90, elevation: 10 },
  back: { azimuth: 180, elevation: 15 },
  bottom: { azimuth: 0, elevation: -70 },
};

export function updateLight(rig: Rig, index: number, patch: Partial<Light>): Rig {
  return { ...rig, lights: rig.lights.map((light, i) => (i === index ? { ...light, ...patch } : light)) };
}

export function addLight(rig: Rig): Rig {
  if (rig.lights.length >= MAX_LIGHTS) return rig;
  return {
    ...rig,
    lights: [...rig.lights, { azimuth: 45, elevation: 15, colour: "#FFFFFF", intensity: 0.4, softness: 0.7 }],
  };
}

export function removeLight(rig: Rig, index: number): Rig {
  if (rig.lights.length <= 1) return rig;
  return { ...rig, lights: rig.lights.filter((_, i) => i !== index) };
}

export const sameRig = (a: Rig | null | undefined, b: Rig | null | undefined) =>
  JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

/** WebGL is there (a browser); jsdom and some locked-down setups have none. */
export function canUseWebGL(): boolean {
  if (typeof window === "undefined" || typeof WebGLRenderingContext === "undefined") return false;
  try {
    const canvas = document.createElement("canvas");
    return Boolean(canvas.getContext("webgl2") ?? canvas.getContext("webgl"));
  } catch {
    return false;
  }
}
