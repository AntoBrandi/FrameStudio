import { Eye } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { Schemas } from "@/api/client";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/misc";

export type Resolved = Schemas["Resolved"];

/** What the model will be given (plan step M4.4): the prompt as it is sent,
 * with elements named and described, and the pictures with the labels the
 * prompt uses. */
export function RequestPreview({ resolved, modelName }: { resolved: Resolved | undefined; modelName?: string }) {
  const { t } = useTranslation();
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="inline-flex h-7 items-center gap-1 rounded-full border border-line px-2.5 text-xs text-muted hover:text-ink"
          data-testid="request-preview-button"
          title={t("preview.title")}
        >
          <Eye className="size-3.5" aria-hidden />
          {t("preview.button")}
          {resolved && resolved.references.length ? (
            <span className="font-mono text-[10px] text-tungsten">{resolved.references.length}</span>
          ) : null}
        </button>
      </PopoverTrigger>
      <PopoverContent side="top" align="start" className="flex w-[min(92vw,30rem)] flex-col gap-3">
        <div data-testid="request-preview" className="flex flex-col gap-3">
          <div className="flex flex-col gap-0.5">
            <h3 className="text-sm font-semibold">{t("preview.title")}</h3>
            {resolved ? (
              <p className="text-xs text-muted" data-testid="request-strategy" data-strategy={resolved.strategy}>
                {t(`preview.strategies.${resolved.strategy}`, { model: modelName ?? resolved.model })}
              </p>
            ) : null}
          </div>
          {!resolved || (!resolved.prompt && resolved.references.length === 0) ? (
            <p className="text-sm text-muted">{t("preview.empty")}</p>
          ) : (
            <>
              <section className="flex flex-col gap-1">
                <span className="text-xs font-medium uppercase tracking-wide text-muted">{t("preview.prompt")}</span>
                <p
                  className="max-h-48 overflow-y-auto whitespace-pre-wrap rounded-lg bg-stage p-2 text-sm"
                  data-testid="request-prompt"
                >
                  {resolved.prompt || t("preview.noPrompt")}
                </p>
              </section>
              <section className="flex flex-col gap-1">
                <span className="text-xs font-medium uppercase tracking-wide text-muted">
                  {t("preview.pictures", { count: resolved.references.length })}
                </span>
                {resolved.references.length === 0 ? (
                  <p className="text-xs text-muted">{t("preview.noPictures")}</p>
                ) : (
                  <ul className="grid grid-cols-4 gap-2" data-testid="request-pictures">
                    {resolved.references.map((reference) => (
                      <li key={reference.asset_id} className="flex flex-col gap-1" data-label={reference.label}>
                        <span className="aspect-square overflow-hidden rounded-md bg-raised">
                          {reference.thumb_url ? (
                            <img src={reference.thumb_url} alt="" className="size-full object-cover" />
                          ) : null}
                        </span>
                        <span className="truncate text-[11px]">{reference.label}</span>
                        <span className="truncate font-mono text-[10px] text-muted">
                          {reference.handle ? `@${reference.handle}` : t("preview.attached")}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
