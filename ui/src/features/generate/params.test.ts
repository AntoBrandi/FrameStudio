import { describe, expect, it } from "vitest";
import { ALL, CLIPS, KNOBS, MIXED, SIZES, STILLS } from "@/test/models";
import { checkValue, normalize, options, type ParamSpec, paramsFor, range, violations, whyNot } from "./params";

describe("which controls a model offers", () => {
  it("filters parameters by mode", () => {
    expect(paramsFor(MIXED, "image").map(([name]) => name)).toEqual(["aspect"]);
    expect(paramsFor(MIXED, "video").map(([name]) => name)).toEqual(["aspect", "motion", "duration"]);
  });

  it("fills defaults per mode and drops what the model lacks", () => {
    expect(normalize(SIZES, "video", { seed_word: "x" })).toEqual({ resolution: "1080p", aspect: "16:9" });
    expect(normalize(SIZES, "image", {})).toEqual({ resolution: "1080p", aspect: "1:1" });
    expect(normalize(KNOBS, "image", { guidance: 99, quality: "final" })).toEqual({
      guidance: 7,
      quality: "final",
      style: "",
    });
  });

  it("keeps compatible values when switching models", () => {
    const fromStills = normalize(STILLS, "image", { aspect: "9:16" });
    expect(normalize(SIZES, "image", fromStills)).toEqual({ resolution: "1080p", aspect: "9:16" });
    expect(normalize(CLIPS, "video", { aspect: "1:1", duration: 7 })).toEqual({
      aspect: "16:9",
      duration: 7,
      audio: true,
    });
  });
});

describe("invalid combinations cannot be chosen", () => {
  it("disables an option that would break a rule, in both directions", () => {
    const long = normalize(CLIPS, "video", { duration: 12 });
    expect(options(CLIPS, "video", long, "aspect").find((o) => o.value === "21:9")?.disabled).toBe(
      "21:9 clips run 10 s at most",
    );
    const wide = normalize(CLIPS, "video", { aspect: "21:9" });
    expect(range(CLIPS, wide, "duration")).toEqual({ min: 2, max: 10 });
    expect(whyNot(CLIPS, "video", wide, "duration", 11)).toBe("21:9 clips run 10 s at most");
    expect(whyNot(CLIPS, "video", wide, "duration", 10)).toBeNull();
  });

  it("checks list conditions and list allowances", () => {
    const fourK = normalize(SIZES, "image", { resolution: "4k", aspect: "16:9" });
    const square = options(SIZES, "image", fourK, "aspect").find((o) => o.value === "1:1");
    expect(square?.disabled).toBe("4K is widescreen or vertical only");
    const tall = normalize(MIXED, "video", { motion: "high", aspect: "9:16", duration: 3 });
    expect(range(MIXED, tall, "duration").max).toBe(4);
    expect(range(MIXED, { ...tall, motion: "low" }, "duration").max).toBe(8);
  });

  it("repairs the other parameter when a choice forces it", () => {
    // The stored square aspect is not allowed at 4K: it moves, the chosen 4K stays.
    expect(normalize(SIZES, "image", { resolution: "4k", aspect: "1:1" }, "resolution")).toEqual({
      resolution: "4k",
      aspect: "16:9",
    });
    // Choosing a long clip with 21:9 set moves the aspect away from 21:9.
    expect(normalize(CLIPS, "video", { aspect: "21:9", duration: 12 }, "duration")).toMatchObject({
      aspect: "16:9",
      duration: 12,
    });
    // Without a changed parameter, the constrained one is clamped.
    expect(normalize(CLIPS, "video", { aspect: "21:9", duration: 12 })).toMatchObject({
      aspect: "21:9",
      duration: 10,
    });
  });

  it("checks values like the backend does", () => {
    const seed = { type: "seed", label: "Seed" } as ParamSpec;
    expect(checkValue(seed, 2 ** 31)).toMatch("between");
    expect(checkValue(seed, 1.5)).toMatch("whole");
    expect(checkValue({ type: "integer", label: "n", min: 2 } as ParamSpec, 1)).toBe("must be at least 2");
    expect(checkValue({ type: "boolean", label: "b" } as ParamSpec, "yes")).toMatch("on or off");
  });

  it("never leaves a combination that breaks a rule (fuzzed)", () => {
    let state = 7;
    const random = () => {
      state = (state * 1103515245 + 12345) % 2 ** 31;
      return state / 2 ** 31;
    };
    for (const spec of ALL) {
      for (const mode of spec.modes) {
        for (let i = 0; i < 200; i++) {
          const params: Record<string, unknown> = {};
          for (const [name, param] of paramsFor(spec, mode)) {
            if (param.values) params[name] = param.values[Math.floor(random() * param.values.length)];
            else if (param.type === "boolean") params[name] = random() > 0.5;
            else if (param.type === "string") params[name] = "x";
            else params[name] = Math.round(((param.min ?? 0) - 2 + random() * ((param.max ?? 10) + 4)) * 2) / 2;
          }
          const names = Object.keys(params);
          const changed = random() > 0.5 ? names[Math.floor(random() * names.length)] : undefined;
          const result = normalize(spec, mode, params, changed);
          expect(violations(spec, result)).toEqual([]);
          for (const [name, param] of paramsFor(spec, mode)) {
            expect(checkValue(param, result[name])).toBeNull();
          }
        }
      }
    }
  });
});
