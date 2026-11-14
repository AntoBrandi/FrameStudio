import { type KeyboardEvent, useId, useRef, type WheelEvent } from "react";
import { cn } from "@/lib/utils";

export interface DrumOption {
  id: string | null;
  label: string;
}

const ROW = 32; // px per row
const VISIBLE = 5;

/** A drum (M5.2): a vertical wheel of choices, like a camera's dial. A
 * listbox: Up and Down turn it, Page Up and Page Down jump three, Home and End
 * go to the ends, the mouse wheel turns it, a click picks. The first option is
 * "None". */
export function Drum({
  label,
  options,
  value,
  onChange,
  noneLabel,
}: {
  label: string;
  options: DrumOption[];
  value: string | null | undefined;
  onChange: (id: string | null) => void;
  noneLabel: string;
}) {
  const id = useId();
  const wheelAt = useRef(0);
  const all: DrumOption[] = [{ id: null, label: noneLabel }, ...options];
  const current = Math.max(
    0,
    all.findIndex((o) => o.id === (value ?? null)),
  );
  const go = (index: number) => onChange(all[Math.min(all.length - 1, Math.max(0, index))]!.id);

  function onKeyDown(event: KeyboardEvent) {
    const keys: Record<string, number> = {
      ArrowDown: current + 1,
      ArrowUp: current - 1,
      PageDown: current + 3,
      PageUp: current - 3,
      Home: 0,
      End: all.length - 1,
    };
    if (!(event.key in keys)) return;
    event.preventDefault();
    go(keys[event.key]!);
  }

  function onWheel(event: WheelEvent) {
    // One step per wheel notch, however fast the wheel spins.
    const now = event.timeStamp;
    if (Math.abs(event.deltaY) < 4 || now - wheelAt.current < 80) return;
    wheelAt.current = now;
    go(current + Math.sign(event.deltaY));
  }

  return (
    <div className="flex min-w-0 flex-1 flex-col items-stretch gap-1.5">
      <span className="text-center text-xs font-medium uppercase tracking-wide text-muted" id={`${id}-label`}>
        {label}
      </span>
      <div
        role="listbox"
        tabIndex={0}
        aria-labelledby={`${id}-label`}
        aria-activedescendant={`${id}-${current}`}
        onKeyDown={onKeyDown}
        onWheel={onWheel}
        className="relative overflow-hidden rounded-xl border border-line bg-stage outline-none focus-visible:ring-2 focus-visible:ring-tungsten"
        style={{ height: ROW * VISIBLE }}
        data-testid={`drum-${label.toLowerCase()}`}
      >
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-1 rounded-md border border-tungsten/60 bg-tungsten/10"
          style={{ top: ROW * Math.floor(VISIBLE / 2), height: ROW }}
        />
        <div
          className="transition-transform duration-150 ease-out motion-reduce:transition-none"
          style={{ transform: `translateY(${(Math.floor(VISIBLE / 2) - current) * ROW}px)` }}
        >
          {all.map((option, index) => (
            <div
              key={option.id ?? "none"}
              id={`${id}-${index}`}
              role="option"
              aria-selected={index === current}
              onClick={() => go(index)}
              className={cn(
                "flex cursor-pointer items-center justify-center truncate px-2 text-sm",
                index === current ? "font-semibold text-ink" : "text-muted",
                Math.abs(index - current) > 1 && "opacity-50",
              )}
              style={{ height: ROW }}
              data-option={option.id ?? "none"}
            >
              {option.label}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
