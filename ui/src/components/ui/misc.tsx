import * as PopoverPrimitive from "@radix-ui/react-popover";
import * as ProgressPrimitive from "@radix-ui/react-progress";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import type { ComponentProps, ReactNode } from "react";
import { cn } from "@/lib/utils";

export const TooltipProvider = TooltipPrimitive.Provider;

export function Tooltip({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <TooltipPrimitive.Root>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content
          sideOffset={6}
          className="z-50 rounded-md border border-line bg-raised px-2 py-1 text-xs text-ink shadow-lg"
        >
          {label}
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  );
}

export const Popover = PopoverPrimitive.Root;
export const PopoverTrigger = PopoverPrimitive.Trigger;

export function PopoverContent({ className, ...props }: ComponentProps<typeof PopoverPrimitive.Content>) {
  return (
    <PopoverPrimitive.Portal>
      <PopoverPrimitive.Content
        sideOffset={8}
        align="end"
        className={cn("z-50 w-80 rounded-xl border border-line bg-flat p-3 shadow-2xl", className)}
        {...props}
      />
    </PopoverPrimitive.Portal>
  );
}

export function Progress({ value, className, label }: { value: number; className?: string; label?: string }) {
  const percent = Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <ProgressPrimitive.Root
      value={percent}
      aria-label={label}
      className={cn("relative h-1.5 w-full overflow-hidden rounded-full bg-raised", className)}
    >
      <ProgressPrimitive.Indicator
        className="h-full bg-tungsten transition-[width] duration-200"
        style={{ width: `${percent}%` }}
      />
    </ProgressPrimitive.Root>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="rounded border border-line bg-raised px-1.5 py-px font-mono text-[11px] text-muted">{children}</kbd>
  );
}

export function Field({ label, children, hint }: { label: ReactNode; children: ReactNode; hint?: ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5 text-sm">
      <span className="font-medium">{label}</span>
      {children}
      {hint ? <span className="text-xs text-muted">{hint}</span> : null}
    </label>
  );
}

export const inputClass =
  "h-9 rounded-lg border border-line bg-stage px-3 text-sm text-ink placeholder:text-muted focus:border-tungsten focus:outline-none";
