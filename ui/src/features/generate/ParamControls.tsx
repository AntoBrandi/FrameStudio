import { Dice5, Minus, Plus } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import {
  MAX_SEED,
  type Mode,
  type ModelOut,
  options,
  type ParamSpec,
  type Params,
  paramsFor,
  range,
  whyNot,
} from "./params";

type Control = NonNullable<ParamSpec["control"]>;

function controlFor(param: ParamSpec): Control {
  if (param.control) return param.control;
  if (param.type === "enum") return (param.values?.length ?? 0) <= 5 ? "pills" : "select";
  if (param.type === "boolean") return "toggle";
  if (param.type === "integer") return "stepper";
  if (param.type === "number") return "slider";
  if (param.type === "seed") return "seed";
  return "text";
}

export function Pill({
  active,
  onClick,
  children,
  label,
  disabledReason,
  testId,
}: {
  active?: boolean;
  onClick?: () => void;
  children: ReactNode;
  label?: string;
  disabledReason?: string | null;
  testId?: string;
}) {
  return (
    <button
      type="button"
      onClick={disabledReason ? undefined : onClick}
      aria-pressed={active}
      aria-label={label}
      aria-disabled={disabledReason ? true : undefined}
      title={disabledReason ?? undefined}
      data-testid={testId}
      className={cn(
        "h-7 rounded-full border px-2.5 font-mono text-xs transition-colors",
        active ? "border-ink/60 bg-raised text-ink" : "border-line text-muted hover:text-ink",
        disabledReason && "cursor-not-allowed opacity-40 hover:text-muted",
      )}
    >
      {children}
    </button>
  );
}

function suffix(param: ParamSpec, value: unknown) {
  return param.unit ? `${String(value)}${param.unit === "s" ? "s" : ` ${param.unit}`}` : String(value);
}

function ParamControl({
  model,
  mode,
  params,
  name,
  param,
  onChange,
}: {
  model: ModelOut;
  mode: Mode;
  params: Params;
  name: string;
  param: ParamSpec;
  onChange: (name: string, value: unknown) => void;
}) {
  const { t } = useTranslation();
  const value = params[name];
  const control = controlFor(param);
  const testId = `param-${name}`;

  if (control === "pills" || control === "select") {
    const choices = options(model, mode, params, name);
    if (control === "select") {
      return (
        <select
          aria-label={param.label}
          value={String(value ?? "")}
          onChange={(e) => {
            const picked = choices.find((choice) => String(choice.value) === e.target.value);
            if (picked) onChange(name, picked.value);
          }}
          className="h-7 rounded-full border border-line bg-transparent px-2 font-mono text-xs text-ink"
          data-testid={testId}
        >
          {choices.map((choice) => (
            <option key={String(choice.value)} value={String(choice.value)} disabled={Boolean(choice.disabled)}>
              {suffix(param, choice.value)}
            </option>
          ))}
        </select>
      );
    }
    return (
      <div role="group" aria-label={param.label} className="flex gap-1" data-testid={testId}>
        {choices.map((choice) => (
          <Pill
            key={String(choice.value)}
            active={value === choice.value}
            disabledReason={choice.disabled}
            onClick={() => onChange(name, choice.value)}
          >
            {suffix(param, choice.value)}
          </Pill>
        ))}
      </div>
    );
  }
  if (control === "toggle") {
    const next = !value;
    return (
      <Pill
        active={Boolean(value)}
        disabledReason={whyNot(model, mode, params, name, next)}
        onClick={() => onChange(name, next)}
        testId={testId}
      >
        {param.label} {value ? t("composer.on") : t("composer.off")}
      </Pill>
    );
  }
  if (control === "stepper") {
    const { min, max } = range(model, params, name);
    const step = param.step ?? 1;
    const current = Number(value ?? param.default ?? min ?? 0);
    return (
      <div
        role="group"
        aria-label={param.label}
        className="flex items-center gap-1 rounded-full border border-line px-1"
        data-testid={testId}
      >
        <button
          type="button"
          className="p-1 text-muted hover:text-ink disabled:opacity-40"
          onClick={() => onChange(name, current - step)}
          disabled={min != null && current - step < min}
          aria-label={`${param.label} −`}
        >
          <Minus className="size-3.5" />
        </button>
        <span className="min-w-8 text-center font-mono text-xs" aria-live="polite">
          {suffix(param, current)}
        </span>
        <button
          type="button"
          className="p-1 text-muted hover:text-ink disabled:opacity-40"
          onClick={() => onChange(name, current + step)}
          disabled={max != null && current + step > max}
          aria-label={`${param.label} +`}
        >
          <Plus className="size-3.5" />
        </button>
      </div>
    );
  }
  if (control === "slider") {
    const { min, max } = range(model, params, name);
    return (
      <label className="flex items-center gap-2 font-mono text-xs text-muted" data-testid={testId}>
        <span>{param.label}</span>
        <input
          type="range"
          min={min ?? 0}
          max={max ?? 100}
          step={param.step ?? 1}
          value={Number(value ?? param.default ?? min ?? 0)}
          onChange={(e) => onChange(name, Number(e.target.value))}
          className="w-28 accent-[var(--tungsten)]"
        />
        <span className="w-10 text-ink">{suffix(param, value)}</span>
      </label>
    );
  }
  if (control === "seed") {
    return (
      <SeedField
        label={param.label}
        value={typeof value === "number" ? value : null}
        onChange={(next) => onChange(name, next)}
      />
    );
  }
  return (
    <label className="flex items-center gap-2 font-mono text-xs text-muted" data-testid={testId}>
      <span>{param.label}</span>
      <input
        type="text"
        value={String(value ?? "")}
        onChange={(e) => onChange(name, e.target.value)}
        className="h-7 w-40 rounded-full border border-line bg-transparent px-2 text-ink"
      />
    </label>
  );
}

/** A seed: empty for a new random one each time, or a fixed number. */
export function SeedField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number | null;
  onChange: (seed: number | null) => void;
}) {
  const { t } = useTranslation();
  return (
    <label className="flex items-center gap-2 font-mono text-xs text-muted">
      <span>{label}</span>
      <input
        type="number"
        min={0}
        max={MAX_SEED}
        step={1}
        inputMode="numeric"
        placeholder={t("composer.randomSeed")}
        value={value ?? ""}
        onChange={(e) => {
          const raw = e.target.value.trim();
          const next = Number(raw);
          onChange(raw === "" || !Number.isInteger(next) ? null : Math.min(MAX_SEED, Math.max(0, next)));
        }}
        className="h-7 w-32 rounded-full border border-line bg-transparent px-2 text-ink"
        data-testid="seed-input"
      />
      <button
        type="button"
        className="rounded-full p-1 text-muted hover:text-ink"
        aria-label={t("composer.newSeed")}
        onClick={() => onChange(Math.floor(Math.random() * MAX_SEED))}
      >
        <Dice5 className="size-4" />
      </button>
    </label>
  );
}

/** The model's parameters for `mode`, drawn from its schema. */
export function ParamControls({
  model,
  mode,
  params,
  onChange,
  advanced,
}: {
  model: ModelOut;
  mode: Mode;
  params: Params;
  onChange: (name: string, value: unknown) => void;
  advanced: boolean;
}) {
  return (
    <>
      {paramsFor(model, mode)
        .filter(([, param]) => Boolean(param.advanced) === advanced)
        .map(([name, param]) => (
          <ParamControl
            key={name}
            model={model}
            mode={mode}
            params={params}
            name={name}
            param={param}
            onChange={onChange}
          />
        ))}
    </>
  );
}
