import * as DialogPrimitive from "@radix-ui/react-dialog";
import { Download } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { GenerationOut } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { formatBytes } from "@/lib/utils";

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="font-mono text-[11px] uppercase tracking-wider text-muted">{label}</dt>
      <dd className="text-sm">{children}</dd>
    </div>
  );
}

/** Media left, inspector right. Opened from a card or a ``?view=`` link. */
export function Viewer({ generation, onClose }: { generation: GenerationOut | undefined; onClose: () => void }) {
  const { t, i18n } = useTranslation();
  const asset = generation?.asset;
  return (
    <Dialog open={Boolean(generation && asset)} onOpenChange={(open) => (open ? undefined : onClose())}>
      {generation && asset ? (
        <DialogContent
          className="grid w-[min(96vw,80rem)] grid-cols-1 gap-5 p-4 md:grid-cols-[minmax(0,1fr)_18rem]"
          closeLabel={t("viewer.close")}
          data-testid="viewer"
        >
          <div className="flex min-h-0 items-center justify-center rounded-lg bg-stage">
            {asset.kind === "video" ? (
              <video src={asset.url} controls autoPlay loop className="max-h-[78vh] w-full object-contain" />
            ) : (
              <img src={asset.url} alt={generation.prompt} className="max-h-[78vh] w-full object-contain" />
            )}
          </div>
          <div className="flex flex-col gap-4 pt-6">
            <DialogTitle>{t("viewer.title")}</DialogTitle>
            <DialogPrimitive.Description className="sr-only">{generation.prompt}</DialogPrimitive.Description>
            <dl className="flex flex-col gap-3">
              <Row label={t("viewer.prompt")}>{generation.prompt || "—"}</Row>
              <Row label={t("viewer.model")}>{generation.model_id}</Row>
              <Row label={t("viewer.seed")}>
                <span className="font-mono">{generation.seed ?? "—"}</span>
              </Row>
              <Row label={t("viewer.size")}>
                <span className="font-mono">
                  {asset.width}×{asset.height}
                </span>
              </Row>
              {asset.duration ? (
                <Row label={t("viewer.duration")}>
                  <span className="font-mono">
                    {asset.duration.toFixed(1)} s{asset.fps ? ` · ${Math.round(asset.fps)} fps` : ""}
                  </span>
                </Row>
              ) : null}
              <Row label={t("viewer.fileSize")}>
                <span className="font-mono">
                  {asset.mime} · {formatBytes(asset.size_bytes)}
                </span>
              </Row>
              <Row label={t("viewer.created")}>{new Date(generation.created_at).toLocaleString(i18n.language)}</Row>
            </dl>
            <Button asChild variant="outline" className="mt-auto">
              <a href={asset.url} download={`framestudio-${generation.id}.${asset.mime.split("/")[1] ?? "bin"}`}>
                <Download />
                {t("viewer.download")}
              </a>
            </Button>
          </div>
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
