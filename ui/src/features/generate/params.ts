import type { Schemas } from "@/api/client";

/** Parameter logic for the composer (plan step M2.5): which controls a model
 * offers in a mode, which values are allowed given the others, and how to bring
 * stored values in line after a model or mode change. It mirrors
 * `validate_params` in `backend/framestudio/catalog`, so whatever the composer
 * lets you pick, the backend accepts. */

export type ModelOut = Schemas["ModelOut"];
export type ParamSpec = Schemas["ParamSpec"];
export type Rule = Schemas["Rule"];
export type Mode = "image" | "video";
export type Params = Record<string, unknown>;
type Allow = Rule["allow"][string];

export const MAX_SEED = 2 ** 31 - 1;

/** The parameters a model offers in `mode`, in catalog order. */
export function paramsFor(model: ModelOut, mode: Mode): [string, ParamSpec][] {
  return Object.entries(model.params).filter(([, param]) => !param.modes || param.modes.includes(mode));
}

export function defaultFor(param: ParamSpec, mode: Mode): unknown {
  const byMode = param.default_by_mode as Partial<Record<Mode, unknown>> | null | undefined;
  if (byMode && mode in byMode) return byMode[mode];
  return param.default ?? null;
}

/** Why `value` is not acceptable for `param`, or null (mirrors `check_value`). */
export function checkValue(param: ParamSpec, value: unknown): string | null {
  if (param.type === "enum") return (param.values ?? []).includes(value as never) ? null : "is not offered";
  if (param.type === "boolean") return typeof value === "boolean" ? null : "must be on or off";
  if (param.type === "string") return typeof value === "string" ? null : "must be text";
  if (typeof value !== "number" || Number.isNaN(value)) return "must be a number";
  if ((param.type === "integer" || param.type === "seed") && !Number.isInteger(value)) return "must be a whole number";
  if (param.type === "seed" && (value < 0 || value > MAX_SEED)) return `must be between 0 and ${MAX_SEED}`;
  if (param.min != null && value < param.min) return `must be at least ${param.min}`;
  if (param.max != null && value > param.max) return `must be at most ${param.max}`;
  return null;
}

function matches(value: unknown, condition: unknown): boolean {
  return Array.isArray(condition) ? condition.includes(value) : value === condition;
}

function allowedBy(value: unknown, allow: Allow): boolean {
  if (Array.isArray(allow)) return allow.includes(value as never);
  const { min, max } = allow as { min?: number; max?: number };
  return typeof value === "number" && (min == null || value >= min) && (max == null || value <= max);
}

/** The rules that `params` break, as their reasons. */
export function violations(model: ModelOut, params: Params): string[] {
  const reasons: string[] = [];
  for (const rule of model.rules) {
    const applies = Object.entries(rule.when).every(([name, cond]) => name in params && matches(params[name], cond));
    if (!applies) continue;
    for (const [name, allow] of Object.entries(rule.allow)) {
      if (name in params && !allowedBy(params[name], allow)) reasons.push(rule.reason);
    }
  }
  return [...new Set(reasons)];
}

/** Whether `name` may take `value` with the other parameters as they are;
 * if not, why. A value is refused if it breaks the parameter's own limits or
 * would leave the combination breaking a rule, in either direction. */
export function whyNot(model: ModelOut, mode: Mode, params: Params, name: string, value: unknown): string | null {
  const param = model.params[name];
  if (!param || (param.modes && !param.modes.includes(mode))) return "is not offered";
  const problem = checkValue(param, value);
  if (problem) return problem;
  const reasons = violations(model, { ...params, [name]: value });
  return reasons[0] ?? null;
}

/** The nearest value `allow` accepts. */
function repair(value: unknown, allow: Allow, fallback: unknown): unknown {
  if (Array.isArray(allow)) return allow.includes(fallback as never) ? fallback : allow[0];
  const { min, max } = allow as { min?: number; max?: number };
  let next = typeof value === "number" ? value : Number(fallback);
  if (min != null) next = Math.max(min, next);
  if (max != null) next = Math.min(max, next);
  return next;
}

/** `params` brought in line with `model` in `mode`: unknown names dropped,
 * defaults filled in, invalid values reset, and rule conflicts settled by
 * moving the constrained parameter (the one the user did not just change). */
export function normalize(model: ModelOut, mode: Mode, params: Params, changed?: string): Params {
  const out: Params = {};
  for (const [name, param] of paramsFor(model, mode)) {
    const value = params[name];
    if (value !== undefined && value !== null && checkValue(param, value) === null) {
      out[name] = value;
    } else {
      const fallback = defaultFor(param, mode);
      if (fallback !== null) out[name] = fallback;
    }
  }
  for (let pass = 0; pass < 4; pass++) {
    let fixed = false;
    for (const rule of model.rules) {
      const applies = Object.entries(rule.when).every(([name, cond]) => name in out && matches(out[name], cond));
      if (!applies) continue;
      for (const [name, allow] of Object.entries(rule.allow)) {
        if (!(name in out) || allowedBy(out[name], allow)) continue;
        if (name === changed) {
          // The user just chose this value: move the parameters that made the rule apply.
          for (const whenName of Object.keys(rule.when)) {
            const param = model.params[whenName];
            if (!param || whenName === changed) continue;
            const options = param.values ?? [];
            const free = options.find((option) => !matches(option, rule.when[whenName]));
            if (free !== undefined) out[whenName] = free;
          }
        } else {
          out[name] = repair(out[name], allow, defaultFor(model.params[name]!, mode));
        }
        fixed = true;
      }
    }
    if (!fixed) break;
  }
  return out;
}

/** Options for an enum parameter, each with the reason it cannot be chosen. */
export function options(
  model: ModelOut,
  mode: Mode,
  params: Params,
  name: string,
): { value: string | number; disabled: string | null }[] {
  const param = model.params[name];
  return (param?.values ?? []).map((value) => ({ value, disabled: whyNot(model, mode, params, name, value) }));
}

/** The range a numeric parameter may take now, after rules. */
export function range(model: ModelOut, params: Params, name: string): { min: number | null; max: number | null } {
  const param = model.params[name];
  let min = param?.min ?? null;
  let max = param?.max ?? null;
  for (const rule of model.rules) {
    const allow = rule.allow[name];
    if (!allow || Array.isArray(allow)) continue;
    const applies = Object.entries(rule.when).every(
      ([other, cond]) => other !== name && other in params && matches(params[other], cond),
    );
    if (!applies) continue;
    const limits = allow as { min?: number; max?: number };
    if (limits.min != null) min = min == null ? limits.min : Math.max(min, limits.min);
    if (limits.max != null) max = max == null ? limits.max : Math.min(max, limits.max);
  }
  return { min, max };
}
