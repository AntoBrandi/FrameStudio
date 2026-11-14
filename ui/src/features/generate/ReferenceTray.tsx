import { ImagePlus, Upload, X } from "lucide-react";
import { type DragEvent, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { fetchClient } from "@/api/client";
import { ASSET_DRAG_TYPE } from "@/features/library/api";
import { uploadFiles } from "@/features/library/upload";
import { cn } from "@/lib/utils";
import { type Reference, toReference } from "./ReferencePicker";

export { type Reference, toReference };

/** The attachments tray: reference images for models that take them, up to
 * the model's own limit. Add from the library, by upload, or by dragging
 * library cards onto it. */
export function ReferenceTray({
  references,
  limit,
  kinds,
  onChange,
  onOpenPicker,
}: {
  references: Reference[];
  limit: number;
  kinds: string[];
  onChange: (references: Reference[]) => void;
  /** Opens the composer's reference picker (M4.5). */
  onOpenPicker: () => void;
}) {
  const { t } = useTranslation();
  const [over, setOver] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const room = Math.max(0, limit - references.length);

  function add(more: Reference[]) {
    const known = new Set(references.map((r) => r.id));
    const fresh = more.filter((r) => !known.has(r.id));
    if (fresh.length > room) toast.warning(t("references.tooMany", { count: limit }));
    onChange([...references, ...fresh.slice(0, room)]);
  }

  async function upload(files: File[]) {
    const results = await uploadFiles(files.slice(0, room), {});
    const added = results.flatMap((item) => (item.asset ? [toReference(item.asset)] : []));
    const failed = results.filter((item) => !item.asset);
    if (failed.length) toast.error(t("references.uploadFailed", { error: failed[0]!.error }));
    add(added);
  }

  async function onDrop(event: DragEvent) {
    event.preventDefault();
    setOver(false);
    const raw = event.dataTransfer.getData(ASSET_DRAG_TYPE);
    if (raw) {
      const ids = JSON.parse(raw) as string[];
      const assets = await Promise.all(
        ids.map((id) => fetchClient.GET("/api/assets/{asset_id}", { params: { path: { asset_id: id } } })),
      );
      add(assets.flatMap(({ data }) => (data && kinds.includes(data.kind) ? [toReference(data)] : [])));
    } else if (event.dataTransfer.files.length) {
      await upload(Array.from(event.dataTransfer.files).filter((file) => file.type.startsWith("image/")));
    }
  }

  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-1.5 rounded-xl border border-dashed px-1.5 py-1",
        over ? "border-tungsten bg-tungsten/5" : "border-transparent",
      )}
      onDragOver={(event) => {
        event.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(event) => void onDrop(event)}
      data-testid="reference-tray"
    >
      <span className="font-mono text-[11px] text-muted" data-testid="reference-count">
        {t("references.count", { count: references.length, limit })}
      </span>
      {references.map((reference) => (
        <span
          key={reference.id}
          className="group relative size-9 overflow-hidden rounded-md border border-line bg-raised"
        >
          {reference.thumb ? (
            <img src={reference.thumb} alt={reference.name} className="size-full object-cover" />
          ) : null}
          <button
            type="button"
            className="absolute right-0 top-0 hidden rounded-bl bg-stage/80 p-0.5 group-hover:block group-focus-within:block"
            aria-label={t("references.remove", { name: reference.name })}
            onClick={() => onChange(references.filter((r) => r.id !== reference.id))}
          >
            <X className="size-3" />
          </button>
        </span>
      ))}
      {room > 0 ? (
        <>
          <button
            type="button"
            className="inline-flex h-7 items-center gap-1 rounded-full border border-line px-2 text-xs text-muted hover:text-ink"
            onClick={onOpenPicker}
            data-testid="pick-references"
          >
            <ImagePlus className="size-3.5" aria-hidden />
            {t("references.fromLibrary")}
          </button>
          <button
            type="button"
            className="inline-flex h-7 items-center gap-1 rounded-full border border-line px-2 text-xs text-muted hover:text-ink"
            onClick={() => fileInput.current?.click()}
          >
            <Upload className="size-3.5" aria-hidden />
            {t("references.upload")}
          </button>
          <input
            ref={fileInput}
            type="file"
            accept="image/*"
            multiple
            hidden
            onChange={(event) => {
              void upload(Array.from(event.target.files ?? []));
              event.target.value = "";
            }}
          />
        </>
      ) : null}
    </div>
  );
}
