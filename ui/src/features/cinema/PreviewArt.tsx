import type { LucideIcon } from "lucide-react";
import { useState } from "react";
import type { Preview } from "./shot";

function hue(text: string): number {
  let h = 0;
  for (const char of text) h = (h * 31 + char.charCodeAt(0)) % 360;
  return h;
}

/** A choice's preview: its generated still (and clip, played on hover), or
 * a drawn placeholder until the artwork has been made (M5.2). */
export function PreviewArt({
  id,
  label,
  preview,
  icon: Icon,
  monochrome = false,
  showLabel = false,
}: {
  id: string;
  label: string;
  preview?: Preview;
  icon: LucideIcon;
  monochrome?: boolean;
  /** Write the label on the placeholder (where no caption sits under it). */
  showLabel?: boolean;
}) {
  const [hover, setHover] = useState(false);
  if (preview?.image) {
    return (
      <span
        className="block size-full"
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        data-art="preview"
      >
        {hover && preview.video ? (
          <video src={preview.video} autoPlay muted loop playsInline className="size-full object-cover" />
        ) : (
          <img src={preview.image} alt="" loading="lazy" className="size-full object-cover" />
        )}
      </span>
    );
  }
  const h = hue(id);
  const background = monochrome
    ? "linear-gradient(160deg, #3a3a3a, #111)"
    : `linear-gradient(160deg, hsl(${h} 45% 34%), hsl(${(h + 40) % 360} 50% 16%))`;
  return (
    <span
      className="flex size-full flex-col items-center justify-center gap-2 p-2 text-center text-white/85"
      style={{ background }}
      aria-hidden
      data-art="placeholder"
    >
      <Icon className={showLabel ? "size-6 opacity-80" : "size-8 opacity-70"} aria-hidden />
      {showLabel ? (
        <span className="font-display text-sm font-semibold uppercase leading-tight tracking-wide">{label}</span>
      ) : null}
    </span>
  );
}
