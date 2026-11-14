import { Ban } from "lucide-react";
import { type KeyboardEvent, type ReactNode, useRef } from "react";
import { cn } from "@/lib/utils";

export interface CarouselOption {
  id: string | null;
  label: string;
  art: ReactNode;
}

/** A row of preview cards to pick one from (M5.2): a radio group, so the
 * arrow keys move and choose, Home and End jump to the ends, and Tab leaves.
 * The first option is "None". */
export function Carousel({
  label,
  options,
  value,
  onChange,
  noneLabel,
  note,
}: {
  label: string;
  options: CarouselOption[];
  value: string | null | undefined;
  onChange: (id: string | null) => void;
  noneLabel: string;
  note?: string;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const all: CarouselOption[] = [
    {
      id: null,
      label: noneLabel,
      art: (
        <span className="flex size-full items-center justify-center text-muted">
          <Ban className="size-6" aria-hidden />
        </span>
      ),
    },
    ...options,
  ];
  const current = Math.max(
    0,
    all.findIndex((o) => o.id === (value ?? null)),
  );

  function move(index: number) {
    const next = (index + all.length) % all.length;
    onChange(all[next]!.id);
    const button = refs.current[next];
    button?.focus();
    button?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }

  function onKeyDown(event: KeyboardEvent) {
    const keys: Record<string, number> = {
      ArrowRight: current + 1,
      ArrowDown: current + 1,
      ArrowLeft: current - 1,
      ArrowUp: current - 1,
      Home: 0,
      End: all.length - 1,
    };
    if (!(event.key in keys)) return;
    event.preventDefault();
    move(keys[event.key]!);
  }

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline gap-2">
        <span className="text-sm font-medium">{label}</span>
        {note ? <span className="text-xs text-muted">{note}</span> : null}
      </div>
      <div
        role="radiogroup"
        aria-label={label}
        onKeyDown={onKeyDown}
        className="flex snap-x gap-2 overflow-x-auto pb-2"
        data-testid={`carousel-${label.toLowerCase()}`}
      >
        {all.map((option, index) => {
          const checked = index === current;
          return (
            <button
              key={option.id ?? "none"}
              ref={(node) => {
                refs.current[index] = node;
              }}
              type="button"
              role="radio"
              aria-checked={checked}
              aria-label={option.label}
              tabIndex={checked ? 0 : -1}
              onClick={() => onChange(option.id)}
              className={cn(
                "flex w-24 shrink-0 snap-start flex-col overflow-hidden rounded-lg border-2 bg-raised text-left outline-none transition-colors focus-visible:ring-2 focus-visible:ring-tungsten",
                checked ? "border-tungsten" : "border-transparent hover:border-line",
              )}
              data-option={option.id ?? "none"}
            >
              <span className="relative block aspect-[3/4] w-full overflow-hidden">{option.art}</span>
              <span className="truncate px-2 py-1 text-xs">{option.label}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
