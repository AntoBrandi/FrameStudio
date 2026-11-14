import { useQueryClient } from "@tanstack/react-query";
import { ScanText, Tags } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { $api, type AssetOut, errorMessage, fetchClient } from "@/api/client";
import { Button } from "@/components/ui/button";
import { invalidateLibrary } from "./api";

/** Viewer actions from M2.6: describe an image with the captions model, and
 * name suggestions from the prompt-help model. Both say which model is used,
 * since describing sends a downscaled copy of the picture to it. */
export function DescribeActions({ asset }: { asset: AssetOut }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { data: overview } = $api.useQuery("get", "/api/llm");
  const [names, setNames] = useState<string[] | null>(null);
  const [busy, setBusy] = useState<"describe" | "names" | null>(null);
  const tasks = new Map((overview?.tasks ?? []).map((task) => [task.task, task.effective]));
  const captions = tasks.get("captions") ?? null;
  const helper = tasks.get("prompt_help") ?? null;
  const describable = asset.kind === "image" || (asset.kind === "video" && Boolean(asset.poster_url));
  if (!captions && !helper) return null;

  async function describe() {
    setBusy("describe");
    const { error } = await fetchClient.POST("/api/assets/describe", { body: { asset_ids: [asset.id] } });
    setBusy(null);
    if (error) toast.error(errorMessage(error));
    else toast.success(t("describe.queued"));
  }

  async function suggest() {
    setBusy("names");
    const { data, error } = await fetchClient.POST("/api/assets/{asset_id}/name-suggestions", {
      params: { path: { asset_id: asset.id } },
    });
    setBusy(null);
    if (error || !data) return toast.error(errorMessage(error));
    setNames(data.names);
  }

  async function rename(name: string) {
    const { error } = await fetchClient.PATCH("/api/assets/{asset_id}", {
      params: { path: { asset_id: asset.id } },
      body: { original_name: name },
    });
    if (error) return toast.error(errorMessage(error));
    setNames(null);
    await invalidateLibrary(queryClient);
  }

  return (
    <div className="flex flex-col gap-2 border-t border-line pt-3" data-testid="describe-actions">
      <div className="flex flex-wrap gap-2">
        {captions && describable ? (
          <Button
            size="sm"
            variant="outline"
            onClick={() => void describe()}
            disabled={busy !== null}
            title={t("describe.sends", { model: captions })}
            data-testid="describe-asset"
          >
            <ScanText />
            {asset.caption ? t("describe.again") : t("describe.label")}
          </Button>
        ) : null}
        {helper ? (
          <Button
            size="sm"
            variant="outline"
            onClick={() => void suggest()}
            disabled={busy !== null}
            data-testid="suggest-names"
          >
            <Tags />
            {busy === "names" ? t("describe.thinking") : t("describe.names")}
          </Button>
        ) : null}
      </div>
      {names ? (
        <div className="flex flex-wrap gap-1.5" role="group" aria-label={t("describe.names")}>
          {names.map((name) => (
            <button
              key={name}
              type="button"
              className="rounded-full border border-line px-2.5 py-0.5 text-xs hover:bg-raised"
              onClick={() => void rename(name)}
              data-testid="name-option"
            >
              {name}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
