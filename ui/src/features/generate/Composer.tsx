import { useQueryClient } from "@tanstack/react-query";
import { Cpu, Minus, Plus, Sparkles } from "lucide-react";
import { type FormEvent, type KeyboardEvent, type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { $api, errorMessage, type GenerationOut } from "@/api/client";
import { GENERATIONS_KEY, upsertById } from "@/api/events";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type Mode = "image" | "video";

const ASPECTS: Record<Mode, string[]> = {
  image: ["1:1", "16:9", "9:16", "4:3", "3:4"],
  video: ["16:9", "9:16", "1:1", "21:9"],
};
const DURATIONS = [3, 5, 8];

function Pill({
  active,
  onClick,
  children,
  label,
}: {
  active?: boolean;
  onClick?: () => void;
  children: ReactNode;
  label?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      aria-label={label}
      className={cn(
        "h-7 rounded-full border px-2.5 font-mono text-xs transition-colors",
        active ? "border-ink/60 bg-raised text-ink" : "border-line text-muted hover:text-ink",
      )}
    >
      {children}
    </button>
  );
}

/** Floating composer docked at the bottom of a generation page. The model's
 * parameter schema drives these controls from M2.5; for now it is the mock. */
export function Composer({ mode }: { mode: Mode }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { data: settings } = $api.useQuery("get", "/api/settings");
  const create = $api.useMutation("post", "/api/generations");
  const [prompt, setPrompt] = useState("");
  const [aspect, setAspect] = useState(ASPECTS[mode][0]!);
  const [batch, setBatch] = useState(1);
  const [duration, setDuration] = useState(3);
  const [audio, setAudio] = useState(true);

  function submit(event?: FormEvent) {
    event?.preventDefault();
    const params: Record<string, unknown> = { aspect };
    if (mode === "video") Object.assign(params, { duration, audio });
    create.mutate(
      { body: { mode, model: "mock", prompt: prompt.trim(), batch, params } },
      {
        onSuccess: (created) => {
          for (const generation of [...created].reverse()) {
            queryClient.setQueriesData<GenerationOut[]>({ queryKey: GENERATIONS_KEY }, (list) =>
              upsertById(list, generation),
            );
          }
        },
        onError: (error) => toast.error(t("composer.failed", { error: errorMessage(error) })),
      },
    );
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  }

  const latency = settings?.mock_latency_seconds ?? 2;
  return (
    <form
      onSubmit={submit}
      aria-label={t("composer.label")}
      className="flex flex-col gap-2 rounded-2xl border border-ink/25 bg-flat/95 p-3 shadow-[0_10px_40px_rgba(0,0,0,0.35)] backdrop-blur"
    >
      <textarea
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        onKeyDown={onKeyDown}
        rows={2}
        aria-label={t("composer.label")}
        placeholder={mode === "image" ? t("composer.placeholderImage") : t("composer.placeholderVideo")}
        className="w-full resize-none bg-transparent px-1 text-[15px] leading-relaxed outline-none placeholder:text-muted"
        data-testid="prompt-input"
      />
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="inline-flex h-7 items-center gap-1.5 rounded-full border border-line px-2.5 font-mono text-xs text-muted">
          <Cpu className="size-3.5 text-tungsten" aria-hidden />
          {t("composer.mock")}
        </span>
        <div role="group" aria-label={t("composer.aspect")} className="flex gap-1">
          {ASPECTS[mode].map((value) => (
            <Pill key={value} active={aspect === value} onClick={() => setAspect(value)}>
              {value}
            </Pill>
          ))}
        </div>
        {mode === "video" ? (
          <>
            <div role="group" aria-label={t("composer.duration")} className="flex gap-1">
              {DURATIONS.map((value) => (
                <Pill key={value} active={duration === value} onClick={() => setDuration(value)}>
                  {value}s
                </Pill>
              ))}
            </div>
            <Pill active={audio} onClick={() => setAudio(!audio)}>
              {t("composer.audio")} {audio ? "on" : "off"}
            </Pill>
          </>
        ) : null}
        <div
          role="group"
          aria-label={t("composer.batch")}
          className="flex items-center gap-1 rounded-full border border-line px-1"
        >
          <button
            type="button"
            className="p-1 text-muted hover:text-ink disabled:opacity-40"
            onClick={() => setBatch(Math.max(1, batch - 1))}
            disabled={batch <= 1}
            aria-label={`${t("composer.batch")} −`}
          >
            <Minus className="size-3.5" />
          </button>
          <span className="w-4 text-center font-mono text-xs" aria-live="polite">
            {batch}
          </span>
          <button
            type="button"
            className="p-1 text-muted hover:text-ink disabled:opacity-40"
            onClick={() => setBatch(Math.min(4, batch + 1))}
            disabled={batch >= 4}
            aria-label={`${t("composer.batch")} +`}
          >
            <Plus className="size-3.5" />
          </button>
        </div>
        <Button
          type="submit"
          variant="primary"
          className="ml-auto uppercase tracking-wide"
          disabled={create.isPending}
          data-testid="generate-button"
        >
          <Sparkles />
          {t("composer.generate")}
          <span className="font-mono text-[11px] font-normal normal-case tracking-normal opacity-80">
            {t("composer.estimate", { seconds: latency })}
          </span>
        </Button>
      </div>
    </form>
  );
}
