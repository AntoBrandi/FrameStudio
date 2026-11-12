import { AlertTriangle, RotateCcw, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { $api, type GenerationOut, type JobOut } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/misc";
import { cn } from "@/lib/utils";

function aspectOf(generation: GenerationOut): string {
  const asset = generation.asset;
  if (asset?.width && asset.height) return `${asset.width} / ${asset.height}`;
  const aspect = String(generation.params.aspect ?? (generation.mode === "video" ? "16:9" : "1:1"));
  const [w, h] = aspect.split(":");
  return `${w ?? 1} / ${h ?? 1}`;
}

export function GenerationCard({
  generation,
  job,
  onOpen,
}: {
  generation: GenerationOut;
  job?: JobOut;
  onOpen: (generation: GenerationOut) => void;
}) {
  const { t } = useTranslation();
  const retry = $api.useMutation("post", "/api/generations/{generation_id}/retry");
  const cancel = $api.useMutation("post", "/api/generations/{generation_id}/cancel");
  const params = { params: { path: { generation_id: generation.id } } };
  const { status, asset } = generation;
  const failed = status === "failed" || status === "interrupted" || status === "cancelled";
  const progress = job?.state === "running" ? job.progress : 0;

  return (
    <article
      data-testid="generation-card"
      data-status={status}
      aria-label={generation.prompt || generation.id}
      className="group relative overflow-hidden rounded-xl border border-line bg-flat"
      style={{ aspectRatio: aspectOf(generation) }}
    >
      {status === "succeeded" && asset ? (
        <button
          type="button"
          className="block size-full"
          onClick={() => onOpen(generation)}
          aria-label={`${t("feed.open")}: ${generation.prompt || generation.id}`}
          data-testid="open-viewer"
        >
          {asset.kind === "video" ? (
            <video
              src={asset.url}
              muted
              loop
              playsInline
              preload="metadata"
              className="size-full object-cover"
              onMouseEnter={(e) => void e.currentTarget.play().catch(() => undefined)}
              onMouseLeave={(e) => e.currentTarget.pause()}
            />
          ) : (
            <img src={asset.url} alt={generation.prompt} loading="lazy" className="size-full object-cover" />
          )}
        </button>
      ) : failed ? (
        <div className="flex size-full flex-col items-start justify-end gap-2 bg-raised/40 p-3">
          <AlertTriangle className={cn("size-5", status === "cancelled" ? "text-muted" : "text-danger")} aria-hidden />
          <p className="text-sm font-medium">
            {status === "failed"
              ? t("feed.failedTitle")
              : status === "interrupted"
                ? t("feed.interruptedTitle")
                : t("feed.cancelledTitle")}
          </p>
          {generation.prompt ? <p className="line-clamp-2 text-xs">{generation.prompt}</p> : null}
          {generation.error_message ? (
            <p className="line-clamp-3 text-xs text-muted">{generation.error_message}</p>
          ) : null}
          <Button
            size="sm"
            variant="outline"
            onClick={() => retry.mutate(params)}
            disabled={retry.isPending}
            data-testid="retry-generation"
          >
            <RotateCcw />
            {t("feed.retry")}
          </Button>
        </div>
      ) : (
        <div className="flex size-full flex-col justify-end gap-2 bg-[linear-gradient(110deg,var(--flat),var(--raised),var(--flat))] bg-[length:200%_100%] p-3 motion-safe:animate-[shimmer_1.6s_linear_infinite]">
          <div className="flex items-center justify-between gap-2">
            <span className="font-mono text-xs text-muted">
              {status === "queued" ? t("feed.queued") : t("feed.running")}
            </span>
            <Button
              size="icon"
              variant="ghost"
              className="size-6"
              aria-label={t("feed.cancel")}
              onClick={() => cancel.mutate(params)}
            >
              <X />
            </Button>
          </div>
          {generation.prompt ? <p className="line-clamp-2 text-xs">{generation.prompt}</p> : null}
          <Progress value={progress} label={t("feed.running")} />
        </div>
      )}
      {status === "succeeded" && generation.prompt ? (
        <div className="pointer-events-none absolute inset-x-0 bottom-0 translate-y-1 bg-gradient-to-t from-black/70 to-transparent p-2 opacity-0 transition group-hover:translate-y-0 group-hover:opacity-100">
          <p className="line-clamp-2 text-xs text-white">{generation.prompt}</p>
        </div>
      ) : null}
    </article>
  );
}
