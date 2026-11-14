import type { ModelOut, Mode, ParamSpec, Rule } from "@/features/generate/params";

/** Synthetic catalog models for composer tests (plan step M2.5): five
 * schemas covering what the catalog can express. */
export function model(
  id: string,
  modes: Mode[],
  params: Record<string, Partial<ParamSpec>>,
  rules: Rule[] = [],
  extra: Partial<ModelOut> = {},
): ModelOut {
  return {
    id,
    name: id,
    description: "",
    provider: "test",
    runtime: "api",
    modes,
    capabilities: [],
    params: Object.fromEntries(
      Object.entries(params).map(([name, p]) => [name, { label: name, advanced: false, ...p } as ParamSpec]),
    ),
    rules,
    limits: { max_references: 0, reference_kinds: [], max_batch: 4, max_prompt_chars: null },
    license: {
      kind: "test_double",
      name: "test",
      commercial_use: "yes",
      credit: "test",
      source_url: "https://example.com",
    },
    pricing: null,
    variants: [],
    tags: [],
    available: true,
    unavailable_reason: null,
    parameter_schema: {},
    ...extra,
  } as ModelOut;
}

// Five synthetic schemas covering what the catalog can express.
export const STILLS = model("stills", ["image"], {
  aspect: { type: "enum", values: ["1:1", "16:9", "9:16"], default: "1:1", control: "pills" },
  hdr: { type: "boolean", default: false, control: "toggle" },
});
export const CLIPS = model(
  "clips",
  ["video"],
  {
    aspect: { type: "enum", values: ["16:9", "9:16", "21:9"], default: "16:9", control: "pills" },
    duration: { type: "integer", min: 2, max: 12, default: 5, unit: "s", control: "stepper" },
    audio: { type: "boolean", default: true, control: "toggle" },
  },
  [{ when: { aspect: "21:9" }, allow: { duration: { max: 10 } }, reason: "21:9 clips run 10 s at most" }],
);
export const SIZES = model(
  "sizes",
  ["image", "video"],
  {
    resolution: { type: "enum", values: ["720p", "1080p", "4k"], default: "1080p", control: "select" },
    aspect: { type: "enum", values: ["1:1", "16:9", "9:16"], default: "1:1", default_by_mode: { video: "16:9" } },
  },
  [{ when: { resolution: "4k" }, allow: { aspect: ["16:9", "9:16"] }, reason: "4K is widescreen or vertical only" }],
);
export const KNOBS = model("knobs", ["image"], {
  guidance: { type: "number", min: 1, max: 20, step: 0.5, default: 7, control: "slider", advanced: true },
  quality: { type: "enum", values: ["draft", "final"], default: "draft", control: "pills" },
  style: { type: "string", default: "", control: "text", advanced: true },
});
export const MIXED = model(
  "mixed",
  ["image", "video"],
  {
    aspect: {
      type: "enum",
      values: ["1:1", "16:9", "9:16", "4:3"],
      default: "1:1",
      default_by_mode: { video: "16:9" },
    },
    motion: { type: "enum", values: ["low", "high"], default: "low", modes: ["video"] },
    duration: { type: "integer", min: 1, max: 8, default: 4, modes: ["video"] },
  },
  [
    {
      when: { motion: "high", aspect: ["9:16", "4:3"] },
      allow: { duration: { max: 4 } },
      reason: "Fast motion in tall frames runs 4 s at most",
    },
  ],
);
export const ALL = [STILLS, CLIPS, SIZES, KNOBS, MIXED];
