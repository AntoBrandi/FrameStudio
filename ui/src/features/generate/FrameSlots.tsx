import { ImagePlus, X } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { type Reference, ReferencePicker } from "./ReferencePicker";

export interface Frames {
  start: Reference | null;
  end: Reference | null;
}

function Slot({
  label,
  frame,
  disabledReason,
  onPick,
  onClear,
  testId,
}: {
  label: string;
  frame: Reference | null;
  disabledReason: string | null;
  onPick: () => void;
  onClear: () => void;
  testId: string;
}) {
  const { t } = useTranslation();
  if (frame) {
    return (
      <span
        className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-line bg-raised pl-0.5 pr-1.5"
        data-testid={testId}
      >
        <span className="size-8 overflow-hidden rounded-md">
          {frame.thumb ? <img src={frame.thumb} alt={frame.name} className="size-full object-cover" /> : null}
        </span>
        <span className="font-mono text-[11px] text-muted">{label}</span>
        <button
          type="button"
          className="rounded-full p-0.5 text-muted hover:text-ink"
          aria-label={t("frames.clear", { slot: label })}
          onClick={onClear}
        >
          <X className="size-3.5" />
        </button>
      </span>
    );
  }
  return (
    <button
      type="button"
      onClick={disabledReason ? undefined : onPick}
      aria-disabled={disabledReason ? true : undefined}
      title={disabledReason ?? undefined}
      className={cn(
        "inline-flex h-7 items-center gap-1 rounded-full border border-dashed border-line px-2.5 text-xs text-muted hover:text-ink",
        disabledReason && "cursor-not-allowed opacity-40 hover:text-muted",
      )}
      data-testid={testId}
    >
      <ImagePlus className="size-3.5" aria-hidden />
      {label}
    </button>
  );
}

/** Image to video (plan step M2.8): a start frame, and an end frame for
 * models that take one. Frames and reference images cannot be mixed. */
export function FrameSlots({
  frames,
  withEnd,
  blockedBy,
  onChange,
}: {
  frames: Frames;
  withEnd: boolean;
  /** Why frames cannot be set now (references are in use), or null. */
  blockedBy: string | null;
  onChange: (frames: Frames) => void;
}) {
  const { t } = useTranslation();
  const [picking, setPicking] = useState<"start" | "end" | null>(null);
  const chosen = [frames.start, frames.end].filter((f): f is Reference => Boolean(f));
  return (
    <div className="flex flex-wrap items-center gap-1.5" data-testid="frame-slots">
      <Slot
        label={t("frames.start")}
        frame={frames.start}
        disabledReason={blockedBy}
        onPick={() => setPicking("start")}
        onClear={() => onChange({ start: null, end: null })}
        testId="start-frame"
      />
      {withEnd ? (
        <Slot
          label={t("frames.end")}
          frame={frames.end}
          disabledReason={blockedBy ?? (frames.start ? null : t("frames.endNeedsStart"))}
          onPick={() => setPicking("end")}
          onClear={() => onChange({ ...frames, end: null })}
          testId="end-frame"
        />
      ) : null}
      <ReferencePicker
        open={picking !== null}
        onOpenChange={(open) => !open && setPicking(null)}
        taken={chosen.map((c) => c.id)}
        room={1}
        kinds={["image"]}
        labels={{ title: t("frames.pickTitle"), hint: t("frames.pickHint") }}
        onAdd={([picked]) => {
          if (!picked || !picking) return;
          onChange(picking === "start" ? { ...frames, start: picked } : { ...frames, end: picked });
        }}
      />
    </div>
  );
}
