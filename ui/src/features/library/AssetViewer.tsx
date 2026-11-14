import * as DialogPrimitive from "@radix-ui/react-dialog";
import {
  ChevronLeft,
  ChevronRight,
  Clapperboard,
  Copy,
  Download,
  FolderSearch,
  Heart,
  ImagePlus,
  Images,
  Link2,
  Maximize,
  Minus,
  Palette,
  Plus,
  Repeat,
  RotateCcw,
  RotateCw,
  SkipBack,
  SkipForward,
  Trash2,
  X,
} from "lucide-react";
import { type PointerEvent, type ReactNode, useEffect, useRef, useState, type WheelEvent } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { useNavigate } from "@tanstack/react-router";
import { $api, type AssetOut, fetchClient } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/misc";
import { shellBridge } from "@/lib/shell";
import { cn, formatBytes } from "@/lib/utils";
import { addAsReference, pageOf, recreate, turnToVideo } from "@/features/generate/handoffs";
import { GradeDialog } from "@/features/cinema/PalettePicker";
import { useComposerStore } from "@/features/generate/store";
import { DescribeActions } from "./Describe";
import { assetName, type MoveTarget, useLibraryActions } from "./api";
import { formatDuration } from "./AssetCard";
import { clampPan, FIT, stepFrame, type ZoomState, zoomAt } from "./media";
import { MoveMenu } from "./MoveMenu";

function ZoomImage({ asset, alt }: { asset: AssetOut; alt: string }) {
  const { t } = useTranslation();
  const stage = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState<ZoomState>(FIT);
  const drag = useRef<{ x: number; y: number; start: ZoomState } | null>(null);
  const [dragging, setDragging] = useState(false);

  const bounds = () => stage.current?.getBoundingClientRect() ?? new DOMRect(0, 0, 1, 1);
  const zoomBy = (factor: number, clientX?: number, clientY?: number) => {
    const rect = bounds();
    const px = clientX === undefined ? 0 : clientX - rect.left - rect.width / 2;
    const py = clientY === undefined ? 0 : clientY - rect.top - rect.height / 2;
    setZoom((z) => clampPan(zoomAt(z, factor, px, py), rect.width, rect.height));
  };
  const onWheel = (event: WheelEvent) => zoomBy(event.deltaY < 0 ? 1.2 : 1 / 1.2, event.clientX, event.clientY);
  const onPointerDown = (event: PointerEvent) => {
    if (zoom.scale <= 1) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { x: event.clientX, y: event.clientY, start: zoom };
    setDragging(true);
  };
  const onPointerMove = (event: PointerEvent) => {
    const start = drag.current;
    if (!start) return;
    const rect = bounds();
    setZoom(
      clampPan(
        {
          scale: start.start.scale,
          x: start.start.x + event.clientX - start.x,
          y: start.start.y + event.clientY - start.y,
        },
        rect.width,
        rect.height,
      ),
    );
  };
  return (
    <div className="relative flex size-full items-center justify-center overflow-hidden">
      <div
        ref={stage}
        className={cn(
          "flex size-full items-center justify-center",
          zoom.scale > 1 ? "cursor-grab active:cursor-grabbing" : "cursor-zoom-in",
        )}
        onWheel={onWheel}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={() => {
          drag.current = null;
          setDragging(false);
        }}
        onDoubleClick={(e) => (zoom.scale > 1 ? setZoom(FIT) : zoomBy(2.5, e.clientX, e.clientY))}
        data-testid="zoom-stage"
        data-scale={zoom.scale.toFixed(2)}
      >
        <img
          src={asset.url}
          alt={alt}
          draggable={false}
          className="max-h-full max-w-full select-none object-contain"
          style={{
            transform: `translate(${zoom.x}px, ${zoom.y}px) scale(${zoom.scale})`,
            transition: dragging ? "none" : "transform 80ms",
          }}
        />
      </div>
      <div className="absolute bottom-3 left-3 flex gap-1 rounded-lg bg-black/60 p-1 text-white">
        <Button
          size="icon"
          variant="ghost"
          className="size-7 text-white"
          aria-label={t("viewer.zoomOut")}
          onClick={() => zoomBy(1 / 1.5)}
        >
          <Minus />
        </Button>
        <span className="flex w-12 items-center justify-center font-mono text-xs">{Math.round(zoom.scale * 100)}%</span>
        <Button
          size="icon"
          variant="ghost"
          className="size-7 text-white"
          aria-label={t("viewer.zoomIn")}
          onClick={() => zoomBy(1.5)}
        >
          <Plus />
        </Button>
        <Button
          size="icon"
          variant="ghost"
          className="size-7 text-white"
          aria-label={t("viewer.fit")}
          onClick={() => setZoom(FIT)}
        >
          <Maximize />
        </Button>
      </div>
    </div>
  );
}

function VideoStage({ asset }: { asset: AssetOut }) {
  const { t } = useTranslation();
  const video = useRef<HTMLVideoElement>(null);
  const [loop, setLoop] = useState(true);
  const step = (direction: 1 | -1) => {
    const element = video.current;
    if (!element) return;
    element.pause();
    element.currentTime = stepFrame(element.currentTime, asset.fps, direction, element.duration || asset.duration || 0);
  };
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.target as HTMLElement | null)?.closest("textarea, input")) return;
      if (event.key === ",") step(-1);
      if (event.key === ".") step(1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });
  return (
    <div className="flex size-full flex-col items-center justify-center gap-2">
      <video
        ref={video}
        key={asset.id}
        src={asset.url}
        poster={asset.poster_url ?? undefined}
        controls
        autoPlay
        loop={loop}
        className="max-h-[calc(100%-3rem)] max-w-full"
        data-testid="viewer-video"
      />
      <div className="flex items-center gap-1">
        <Button size="sm" variant="ghost" onClick={() => step(-1)} aria-label={t("viewer.frameBack")} title=",">
          <SkipBack />
        </Button>
        <Button size="sm" variant="ghost" onClick={() => step(1)} aria-label={t("viewer.frameForward")} title=".">
          <SkipForward />
        </Button>
        <Button size="sm" variant={loop ? "secondary" : "ghost"} aria-pressed={loop} onClick={() => setLoop(!loop)}>
          <Repeat />
          {t("viewer.loop")}
        </Button>
      </div>
    </div>
  );
}

/** Thumbnails of the assets a generation came from, each opening that asset. */
function Lineage({ ids }: { ids: string[] }) {
  return (
    <span className="flex flex-wrap gap-1.5" data-testid="lineage">
      {ids.map((id) => (
        <a
          key={id}
          href={`/assets?view=${id}`}
          className="size-10 overflow-hidden rounded-md border border-line bg-raised hover:border-ink/50"
        >
          <img src={`/api/assets/${id}/thumb`} alt="" className="size-full object-cover" />
        </a>
      ))}
    </span>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="font-mono text-[11px] uppercase tracking-wider text-muted">{label}</dt>
      <dd className="break-words text-sm">{children}</dd>
    </div>
  );
}

function Notes({ asset }: { asset: AssetOut }) {
  const { t } = useTranslation();
  const { patch } = useLibraryActions();
  const [value, setValue] = useState(asset.notes);
  const [state, setState] = useState<"idle" | "saving" | "saved">("idle");
  useEffect(() => {
    if (value === asset.notes) return;
    const timer = setTimeout(async () => {
      await patch(asset.id, { notes: value });
      setState("saved");
    }, 600);
    return () => clearTimeout(timer);
  }, [value, asset.id, asset.notes, patch]);
  return (
    <div className="flex flex-1 flex-col gap-2">
      <textarea
        value={value}
        onChange={(e) => {
          setValue(e.target.value);
          setState("saving");
        }}
        placeholder={t("viewer.notesPlaceholder")}
        aria-label={t("viewer.notes")}
        className="min-h-40 flex-1 resize-none rounded-lg border border-line bg-stage p-3 text-sm outline-none focus:border-tungsten"
        data-testid="asset-notes"
      />
      <span className="font-mono text-[11px] text-muted" aria-live="polite">
        {state === "saving" ? t("brief.saving") : state === "saved" ? t("brief.saved") : ""}
      </span>
    </div>
  );
}

export interface AssetViewerProps {
  asset: AssetOut | undefined;
  items: AssetOut[];
  projectId?: string;
  onNavigate: (id: string) => void;
  onClose: () => void;
  onSetCover?: (asset: AssetOut) => void;
}

/** Lightbox: media left, inspector right, filmstrip below. */
export function AssetViewer({ asset, items, projectId, onNavigate, onClose, onSetCover }: AssetViewerProps) {
  const { t, i18n } = useTranslation();
  const actions = useLibraryActions();
  const navigate = useNavigate();
  const { data: models = [] } = $api.useQuery("get", "/api/models");
  const imageModel = useComposerStore((state) => state.models.image);
  const [grading, setGrading] = useState(false);

  // Hand-offs (M2.9): fill a composer and go to its page.
  function handOff(kind: "recreate" | "reference" | "video") {
    if (!asset) return;
    if (kind === "recreate") {
      const mode = recreate(asset);
      if (!mode) return;
      toast.success(t("viewer.recreated"));
      void navigate({ to: pageOf(mode) });
    } else if (kind === "reference") {
      const takesReferences = (models.find((m) => m.id === imageModel)?.limits.max_references ?? 0) > 0;
      addAsReference(asset, takesReferences);
      toast.success(t("viewer.referenceAdded"));
      void navigate({ to: "/image" });
    } else {
      turnToVideo(asset);
      void navigate({ to: "/video" });
    }
  }
  const [tab, setTab] = useState<"info" | "notes">("info");
  const strip = useRef<HTMLDivElement>(null);
  const index = asset ? items.findIndex((item) => item.id === asset.id) : -1;
  const shell = shellBridge();

  useEffect(() => {
    if (asset) void fetchClient.POST("/api/assets/{asset_id}/viewed", { params: { path: { asset_id: asset.id } } });
  }, [asset?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    strip.current?.querySelector("[aria-current=true]")?.scrollIntoView({ block: "nearest", inline: "center" });
  }, [asset?.id]);

  const go = (delta: number) => {
    const next = items[index + delta];
    if (next) onNavigate(next.id);
  };

  useEffect(() => {
    if (!asset) return;
    const onKey = (event: KeyboardEvent) => {
      if ((event.target as HTMLElement | null)?.closest("textarea, input, select")) return;
      if (event.key === "ArrowLeft") go(-1);
      if (event.key === "ArrowRight") go(1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (!asset) return null;
  const name = assetName(asset, t("library.untitled"));
  const generation = asset.generation;
  const trashed = asset.deleted_at !== null;

  async function copyPrompt() {
    await navigator.clipboard.writeText(generation?.prompt ?? name);
    toast.success(t("viewer.copied"));
  }
  async function copyImage() {
    const blob = await (await fetch(asset!.url, { credentials: "same-origin" })).blob();
    const png =
      blob.type === "image/png"
        ? blob
        : await new Promise<Blob>((resolve, reject) => {
            const image = new Image();
            image.onload = () => {
              const canvas = document.createElement("canvas");
              canvas.width = image.naturalWidth;
              canvas.height = image.naturalHeight;
              canvas.getContext("2d")?.drawImage(image, 0, 0);
              canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("encode"))), "image/png");
            };
            image.onerror = reject;
            image.src = URL.createObjectURL(blob);
          });
    await navigator.clipboard.write([new ClipboardItem({ "image/png": png })]);
    toast.success(t("viewer.copied"));
  }
  async function reveal() {
    const { data } = await fetchClient.GET("/api/assets/{asset_id}/location", {
      params: { path: { asset_id: asset!.id } },
    });
    if (data) await shell?.revealInFolder(data.path);
  }
  const move = (target: MoveTarget) => void actions.move([asset.id], target);

  const action = (label: string, icon: ReactNode, onClick: () => void, testId?: string, pressed?: boolean) => (
    <Tooltip label={label}>
      <Button
        size="icon"
        variant="ghost"
        aria-label={label}
        onClick={onClick}
        data-testid={testId}
        aria-pressed={pressed}
      >
        {icon}
      </Button>
    </Tooltip>
  );

  return (
    <DialogPrimitive.Root open onOpenChange={(open) => (open ? undefined : onClose())}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/85" />
        <DialogPrimitive.Content
          className="fixed inset-0 z-50 grid grid-cols-1 grid-rows-[auto_minmax(0,1fr)_auto] bg-stage/95 md:grid-cols-[minmax(0,1fr)_20rem]"
          data-testid="asset-viewer"
          aria-describedby={undefined}
          // Focus the viewer itself, not its first button: a focused button shows
          // its tooltip, and the tooltip would take the first Escape.
          onOpenAutoFocus={(event) => {
            event.preventDefault();
            (event.currentTarget as HTMLElement).focus();
          }}
        >
          <header className="col-span-full flex items-center gap-2 border-b border-line px-3 py-2">
            <DialogPrimitive.Title className="min-w-0 flex-1 truncate font-medium">{name}</DialogPrimitive.Title>
            {index >= 0 ? (
              <span className="font-mono text-xs text-muted">
                {t("viewer.of", { index: index + 1, total: items.length })}
              </span>
            ) : null}
            {action(
              asset.favorite ? t("viewer.unlike") : t("viewer.like"),
              <Heart className={cn(asset.favorite && "fill-rose-500 text-rose-500")} />,
              () => void actions.favorite([asset.id], !asset.favorite),
              "viewer-like",
              asset.favorite,
            )}
            <Tooltip label={t("viewer.download")}>
              <Button asChild size="icon" variant="ghost" aria-label={t("viewer.download")}>
                <a href={`${asset.url}?download=true`} download data-testid="viewer-download">
                  <Download />
                </a>
              </Button>
            </Tooltip>
            {!trashed && asset.generation
              ? action(t("viewer.recreate"), <RotateCw />, () => handOff("recreate"), "handoff-recreate")
              : null}
            {!trashed && asset.kind === "image" ? (
              <>
                {action(t("viewer.useAsReference"), <Images />, () => handOff("reference"), "handoff-reference")}
                {action(t("viewer.turnToVideo"), <Clapperboard />, () => handOff("video"), "handoff-video")}
              </>
            ) : null}
            {!trashed && (asset.kind === "image" || asset.kind === "video")
              ? action(t("grade.action"), <Palette />, () => setGrading(true), "viewer-grade")
              : null}
            {action(t("viewer.copyPrompt"), <Copy />, () => void copyPrompt())}
            {asset.kind === "image"
              ? action(
                  t("viewer.copyImage"),
                  <ImagePlus />,
                  () => void copyImage().catch(() => toast.error("Copy failed")),
                )
              : null}
            {action(
              t("viewer.copyLink"),
              <Link2 />,
              () => void navigator.clipboard.writeText(location.href).then(() => toast.success(t("viewer.copied"))),
            )}
            {shell?.canReveal ? action(t("viewer.reveal"), <FolderSearch />, () => void reveal()) : null}
            {!trashed ? <MoveMenu projectId={projectId} onMove={move} triggerProps={{ variant: "ghost" }} /> : null}
            {projectId && onSetCover && !trashed ? (
              <Button size="sm" variant="ghost" onClick={() => onSetCover(asset)}>
                {t("projects.setCover")}
              </Button>
            ) : null}
            {trashed
              ? action(t("viewer.restore"), <RotateCcw />, () => void actions.restore([asset.id]).then(onClose))
              : action(
                  t("viewer.trash"),
                  <Trash2 />,
                  () => {
                    const next = items[index + 1] ?? items[index - 1];
                    void actions.trash([asset.id]).then(() => (next ? onNavigate(next.id) : onClose()));
                  },
                  "viewer-trash",
                )}
            <DialogPrimitive.Close asChild>
              <Button size="icon" variant="ghost" aria-label={t("viewer.close")}>
                <X />
              </Button>
            </DialogPrimitive.Close>
          </header>

          <section className="relative min-h-0 p-3" aria-label={name}>
            {asset.kind === "image" ? (
              <ZoomImage key={asset.id} asset={asset} alt={name} />
            ) : asset.kind === "video" ? (
              <VideoStage asset={asset} />
            ) : (
              <div className="flex size-full flex-col items-center justify-center gap-4">
                {asset.thumb_url ? <img src={asset.thumb_url} alt="" className="w-full max-w-3xl" /> : null}
                <audio src={asset.url} controls autoPlay className="w-full max-w-3xl" />
              </div>
            )}
            <Button
              size="icon"
              variant="secondary"
              className="absolute left-4 top-1/2 -translate-y-1/2 opacity-70 hover:opacity-100"
              aria-label={t("viewer.previous")}
              disabled={index <= 0}
              onClick={() => go(-1)}
            >
              <ChevronLeft />
            </Button>
            <Button
              size="icon"
              variant="secondary"
              className="absolute right-4 top-1/2 -translate-y-1/2 opacity-70 hover:opacity-100"
              aria-label={t("viewer.next")}
              disabled={index < 0 || index >= items.length - 1}
              onClick={() => go(1)}
            >
              <ChevronRight />
            </Button>
          </section>

          <aside
            className="row-span-2 hidden min-h-0 flex-col border-l border-line md:flex"
            aria-label={t("viewer.info")}
          >
            <div role="tablist" className="flex border-b border-line">
              {(["info", "notes"] as const).map((key) => (
                <button
                  key={key}
                  role="tab"
                  type="button"
                  aria-selected={tab === key}
                  onClick={() => setTab(key)}
                  className={cn(
                    "flex-1 px-3 py-2 text-sm",
                    tab === key ? "border-b-2 border-tungsten text-ink" : "text-muted",
                  )}
                >
                  {t(`viewer.${key}`)}
                </button>
              ))}
            </div>
            <div role="tabpanel" className="flex min-h-0 flex-1 flex-col overflow-auto p-4">
              {tab === "info" ? (
                <>
                  <dl className="flex flex-col gap-3">
                    {generation ? <Row label={t("viewer.prompt")}>{generation.prompt || "—"}</Row> : null}
                    {asset.original_name ? <Row label={t("viewer.name")}>{asset.original_name}</Row> : null}
                    {asset.caption ? (
                      <Row label={t("viewer.caption")}>
                        <span data-testid="asset-caption">{asset.caption}</span>
                      </Row>
                    ) : null}
                    {asset.tags.length ? (
                      <Row label={t("viewer.tags")}>
                        <span className="flex flex-wrap gap-1">
                          {asset.tags.map((tag) => (
                            <span
                              key={tag}
                              className="rounded border border-line px-1.5 font-mono text-[11px] text-muted"
                            >
                              {tag}
                            </span>
                          ))}
                        </span>
                      </Row>
                    ) : null}
                    <Row label={t("viewer.source")}>
                      {asset.provenance === "uploaded"
                        ? t("viewer.uploaded")
                        : `${t("viewer.generated")} · ${generation?.model_id ?? ""}`}
                    </Row>
                    {generation?.parent_asset_id ? (
                      <Row label={t("viewer.madeFrom")}>
                        <Lineage ids={[generation.parent_asset_id]} />
                      </Row>
                    ) : null}
                    {generation?.references?.length ? (
                      <Row label={t("viewer.references")}>
                        <Lineage ids={generation.references} />
                      </Row>
                    ) : null}
                    {generation?.seed !== undefined && generation?.seed !== null ? (
                      <Row label={t("viewer.seed")}>
                        <span className="font-mono">{generation.seed}</span>
                      </Row>
                    ) : null}
                    {asset.width ? (
                      <Row label={t("viewer.size")}>
                        <span className="font-mono">
                          {asset.width}×{asset.height}
                        </span>
                      </Row>
                    ) : null}
                    {asset.duration ? (
                      <Row label={t("viewer.duration")}>
                        <span className="font-mono">
                          {formatDuration(asset.duration)}
                          {asset.fps ? ` · ${Math.round(asset.fps)} fps` : ""}
                        </span>
                      </Row>
                    ) : null}
                    {asset.video_codec || asset.audio_codec ? (
                      <Row label={t("viewer.codecs")}>
                        <span className="font-mono">
                          {[asset.video_codec, asset.audio_codec].filter(Boolean).join(" · ")}
                        </span>
                      </Row>
                    ) : null}
                    <Row label={t("viewer.fileSize")}>
                      <span className="font-mono">
                        {asset.mime} · {formatBytes(asset.size_bytes)}
                      </span>
                    </Row>
                    <Row label={t("viewer.created")}>{new Date(asset.created_at).toLocaleString(i18n.language)}</Row>
                  </dl>
                  <DescribeActions asset={asset} />
                </>
              ) : (
                <Notes key={asset.id} asset={asset} />
              )}
            </div>
          </aside>

          <div
            ref={strip}
            className="flex gap-1.5 overflow-x-auto border-t border-line px-3 py-2"
            aria-label={t("viewer.filmstrip")}
            role="list"
          >
            {items.slice(Math.max(0, index - 30), index + 31).map((item) => (
              <button
                key={item.id}
                role="listitem"
                type="button"
                aria-current={item.id === asset.id}
                aria-label={assetName(item, t("library.untitled"))}
                onClick={() => onNavigate(item.id)}
                className={cn(
                  "h-14 w-20 shrink-0 overflow-hidden rounded-md bg-raised opacity-60 hover:opacity-100",
                  item.id === asset.id && "opacity-100 ring-2 ring-tungsten",
                )}
              >
                {item.thumb_url ? <img src={item.thumb_url} alt="" className="size-full object-cover" /> : null}
              </button>
            ))}
          </div>
          {grading ? <GradeDialog assetId={asset.id} open onOpenChange={setGrading} /> : null}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
