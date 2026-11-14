import { Undo2, WandSparkles, X } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { $api, errorMessage, fetchClient } from "@/api/client";
import { type DiffPart, diffWords } from "@/lib/diff";
import { cn } from "@/lib/utils";
import type { Mode } from "./params";

export interface Enhancement {
  before: string;
  after: string;
  changes: string[];
  llm: string;
}

/** The Enhance button: rewrites the prompt with the model chosen for prompt
 * help (plan step M2.6). The new prompt replaces the old one at once; the
 * panel shows what changed and undoes it in one click. */
export function EnhanceButton({
  prompt,
  mode,
  model,
  onEnhanced,
}: {
  prompt: string;
  mode: Mode;
  model: string | undefined;
  onEnhanced: (enhancement: Enhancement) => void;
}) {
  const { t } = useTranslation();
  const { data: overview } = $api.useQuery("get", "/api/llm");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const helper = overview?.tasks.find((task) => task.task === "prompt_help")?.effective ?? null;
  const reason = !helper ? t("enhance.noModel") : !prompt.trim() ? t("enhance.empty") : null;

  async function run() {
    if (reason || busy) return;
    setBusy(true);
    setProblem(null);
    const { data, error } = await fetchClient.POST("/api/llm/enhance", {
      body: { prompt, mode, ...(model ? { model } : {}) },
    });
    setBusy(false);
    if (error || !data) return setProblem(errorMessage(error));
    onEnhanced({ before: prompt, after: data.prompt, changes: data.changes, llm: data.llm });
  }

  return (
    <>
      <button
        type="button"
        onClick={() => void run()}
        aria-disabled={reason ? true : undefined}
        title={reason ?? (helper ? t("enhance.with", { model: helper }) : undefined)}
        className={cn(
          "inline-flex h-7 items-center gap-1 rounded-full border border-line px-2.5 text-xs text-muted hover:text-ink",
          (reason || busy) && "cursor-not-allowed opacity-50 hover:text-muted",
        )}
        data-testid="enhance-button"
      >
        <WandSparkles className={cn("size-3.5", busy && "animate-pulse")} aria-hidden />
        {busy ? t("enhance.working") : t("enhance.label")}
      </button>
      {problem ? (
        <span role="alert" className="text-xs text-danger">
          {problem}
        </span>
      ) : null}
    </>
  );
}

function DiffView({ parts }: { parts: DiffPart[] }) {
  return (
    <p className="text-sm leading-relaxed" data-testid="enhance-diff">
      {parts.map((part, index) =>
        part.kind === "same" ? (
          <span key={index}>{part.text}</span>
        ) : part.kind === "added" ? (
          <ins key={index} className="rounded bg-ok/15 text-ok no-underline">
            {part.text}
          </ins>
        ) : (
          <del key={index} className="rounded bg-danger/10 text-danger/80">
            {part.text}
          </del>
        ),
      )}
    </p>
  );
}

export function EnhancePanel({
  enhancement,
  onUndo,
  onClose,
}: {
  enhancement: Enhancement;
  onUndo: () => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  return (
    <section
      aria-label={t("enhance.title")}
      className="flex flex-col gap-2 rounded-xl border border-line bg-stage/80 p-3"
      data-testid="enhance-panel"
    >
      <header className="flex items-center gap-2">
        <WandSparkles className="size-4 text-tungsten" aria-hidden />
        <h3 className="text-sm font-medium">{t("enhance.title")}</h3>
        <span className="font-mono text-[11px] text-muted">{enhancement.llm}</span>
        <button
          type="button"
          className="ml-auto inline-flex h-7 items-center gap-1 rounded-full border border-line px-2.5 text-xs hover:bg-raised"
          onClick={onUndo}
          data-testid="enhance-undo"
        >
          <Undo2 className="size-3.5" aria-hidden />
          {t("enhance.undo")}
        </button>
        <button
          type="button"
          className="rounded-full p-1 text-muted hover:text-ink"
          aria-label={t("enhance.close")}
          onClick={onClose}
        >
          <X className="size-4" />
        </button>
      </header>
      <DiffView parts={diffWords(enhancement.before, enhancement.after)} />
      {enhancement.changes.length ? (
        <ul className="flex flex-wrap gap-1.5">
          {enhancement.changes.map((change) => (
            <li key={change} className="rounded-full border border-line px-2 py-0.5 text-xs text-muted">
              {change}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
