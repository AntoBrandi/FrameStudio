import { Command } from "cmdk";
import type { TFunction } from "i18next";
import { Check, ChevronDown, Cloud, Cpu, Sparkles } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/misc";
import { cn } from "@/lib/utils";
import type { Mode, ModelOut } from "./params";

/** Capabilities worth a badge in each mode. */
const BADGES: Record<Mode, ModelOut["capabilities"][number][]> = {
  image: ["text_to_image", "image_edit", "multi_reference"],
  video: ["text_to_video", "image_to_video", "start_end_frames", "extend", "native_audio"],
};

export function price(model: ModelOut, t: TFunction): string | null {
  const pricing = model.pricing;
  if (!pricing) return null;
  const amount = `$${pricing.usd < 0.1 ? pricing.usd.toFixed(3).replace(/0$/, "") : pricing.usd.toFixed(2)}`;
  return t(`picker.per.${pricing.unit}`, { amount });
}

function RuntimeIcon({ model, className }: { model: ModelOut; className?: string }) {
  return model.runtime === "local" ? (
    <Cpu className={cn("size-3.5 text-tungsten", className)} aria-hidden />
  ) : (
    <Cloud className={cn("size-3.5 text-daylight", className)} aria-hidden />
  );
}

/** Model picker with search, groups (this machine / hosted) and capability
 * badges. Models that cannot run say why and cannot be picked. */
export function ModelPicker({
  models,
  mode,
  value,
  auto = false,
  onChange,
}: {
  models: ModelOut[];
  mode: Mode;
  value: ModelOut | undefined;
  /** Auto is chosen: ``value`` is what the router picked. */
  auto?: boolean;
  onChange: (id: string) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const offered = models.filter((m) => m.modes.includes(mode));
  const groups = [
    { key: "local", label: t("picker.local"), items: offered.filter((m) => m.runtime === "local") },
    { key: "api", label: t("picker.api"), items: offered.filter((m) => m.runtime === "api") },
  ].filter((group) => group.items.length > 0);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="inline-flex h-7 max-w-56 items-center gap-1.5 rounded-full border border-line px-2.5 font-mono text-xs text-ink hover:bg-raised"
          aria-label={t("picker.label", { model: value?.name ?? t("picker.none") })}
          data-testid="model-picker"
        >
          {auto ? (
            <Sparkles className="size-3.5 text-tungsten" aria-hidden />
          ) : value ? (
            <RuntimeIcon model={value} />
          ) : null}
          <span className="truncate">
            {auto
              ? t("picker.autoLabel", { model: value?.name ?? t("picker.none") })
              : (value?.name ?? t("picker.none"))}
          </span>
          <ChevronDown className="size-3.5 text-muted" aria-hidden />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" side="top" className="w-[26rem] p-0">
        <Command label={t("picker.title")} className="flex flex-col">
          <Command.Input
            autoFocus
            placeholder={t("picker.search")}
            className="h-10 border-b border-line bg-transparent px-3 text-sm outline-none placeholder:text-muted"
          />
          <Command.List className="max-h-80 overflow-y-auto p-1.5">
            <Command.Empty className="px-3 py-6 text-center text-sm text-muted">{t("picker.empty")}</Command.Empty>
            <Command.Item
              value={`auto ${t("picker.auto")}`}
              onSelect={() => {
                onChange("auto");
                setOpen(false);
              }}
              className="flex cursor-pointer flex-col gap-0.5 rounded-lg px-2 py-2 data-[selected=true]:bg-raised"
              data-testid="model-option-auto"
            >
              <span className="flex items-center gap-2">
                <Sparkles className="size-3.5 text-tungsten" aria-hidden />
                <span className="text-sm font-medium">{t("picker.auto")}</span>
                {auto ? <Check className="ml-auto size-3.5 text-tungsten" aria-hidden /> : null}
              </span>
              <span className="text-xs text-muted">{t("picker.autoHint")}</span>
            </Command.Item>
            {groups.map((group) => (
              <Command.Group
                key={group.key}
                heading={group.label}
                className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:pb-1 [&_[cmdk-group-heading]]:pt-2 [&_[cmdk-group-heading]]:font-mono [&_[cmdk-group-heading]]:text-[11px] [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-widest [&_[cmdk-group-heading]]:text-muted"
              >
                {group.items.map((model) => {
                  const cost = price(model, t);
                  return (
                    <Command.Item
                      key={model.id}
                      value={`${model.name} ${model.id} ${model.tags.join(" ")}`}
                      disabled={!model.available}
                      onSelect={() => {
                        onChange(model.id);
                        setOpen(false);
                      }}
                      className="flex cursor-pointer flex-col gap-1 rounded-lg px-2 py-2 data-[disabled=true]:cursor-not-allowed data-[selected=true]:bg-raised data-[disabled=true]:opacity-60"
                      data-testid={`model-option-${model.id}`}
                    >
                      <span className="flex items-center gap-2">
                        <RuntimeIcon model={model} />
                        <span className="text-sm font-medium">{model.name}</span>
                        {model.tags.includes("test") ? (
                          <span className="rounded-full border border-line px-1.5 font-mono text-[10px] text-muted">
                            {t("picker.test")}
                          </span>
                        ) : null}
                        {cost ? <span className="ml-auto font-mono text-[11px] text-muted">{cost}</span> : null}
                        {!auto && value?.id === model.id ? (
                          <Check className="size-3.5 text-tungsten" aria-hidden />
                        ) : null}
                      </span>
                      <span className="line-clamp-2 text-xs text-muted">
                        {model.available ? model.description : model.unavailable_reason}
                      </span>
                      <span className="flex flex-wrap gap-1">
                        {BADGES[mode]
                          .filter((capability) => model.capabilities.includes(capability))
                          .map((capability) => (
                            <span
                              key={capability}
                              className="rounded border border-line px-1.5 py-px font-mono text-[10px] text-muted"
                            >
                              {t(`picker.capabilities.${capability}`)}
                            </span>
                          ))}
                      </span>
                    </Command.Item>
                  );
                })}
              </Command.Group>
            ))}
          </Command.List>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
