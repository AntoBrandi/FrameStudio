import { useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { $api, type AppSettingsPatch, errorMessage, fetchClient, type Schemas } from "@/api/client";
import { refresh } from "@/api/events";
import { Button } from "@/components/ui/button";
import { inputClass } from "@/components/ui/misc";
import { cn } from "@/lib/utils";

type Overview = Schemas["LLMOverview"];
type Model = Schemas["LLMModelOut"];
type Task = Schemas["LLMTaskOut"]["task"];

export const LLM_KEY = ["get", "/api/llm"] as const;

const SETTING: Record<Task, keyof AppSettingsPatch> = {
  default: "llm_default",
  prompt_help: "llm_prompt_help",
  captions: "llm_captions",
  director: "llm_director",
};

function usd(value: number) {
  return `$${value < 1 ? value.toFixed(2) : value.toFixed(value % 1 ? 2 : 0)}`;
}

function contextSize(tokens: number) {
  return tokens >= 1_000_000 ? `${+(tokens / 1_000_000).toFixed(1)}M` : `${Math.round(tokens / 1000)}K`;
}

function ModelFacts({ model }: { model: Model | undefined }) {
  const { t } = useTranslation();
  if (!model) return null;
  const facts = [
    model.vision ? t("llm.vision") : null,
    model.tools ? t("llm.tools") : null,
    model.structured_output ? t("llm.structured") : null,
    model.context_tokens ? t("llm.context", { size: contextSize(model.context_tokens) }) : null,
    model.usd_per_mtok_in !== null && model.usd_per_mtok_out !== null
      ? t("llm.price", { input: usd(model.usd_per_mtok_in), output: usd(model.usd_per_mtok_out) })
      : null,
  ].filter(Boolean);
  return <p className="text-xs text-muted">{facts.join(" · ")}</p>;
}

function TaskRow({
  task,
  overview,
  models,
  onSaved,
}: {
  task: Schemas["LLMTaskOut"];
  overview: Overview;
  models: Map<string, Model>;
  onSaved: () => void;
}) {
  const { t } = useTranslation();
  const selectId = useId();
  const [problem, setProblem] = useState<string | null>(null);
  const effective = task.effective ? models.get(task.effective) : undefined;
  const effectiveName = effective?.name ?? task.effective ?? t("llm.none");
  const stale = task.choice !== "" && !models.has(task.choice);

  async function choose(value: string) {
    const { error } = await fetchClient.PATCH("/api/settings", { body: { [SETTING[task.task]]: value } });
    setProblem(error ? errorMessage(error) : null);
    onSaved();
  }

  return (
    <div className="flex flex-col gap-1.5" data-testid={`llm-task-${task.task}`}>
      <label htmlFor={selectId} className="text-sm font-medium">
        {t(`llm.tasks.${task.task}`)}
      </label>
      <p className="text-xs text-muted">{t(`llm.taskHints.${task.task}`)}</p>
      <select
        id={selectId}
        className={inputClass}
        value={task.choice}
        onChange={(e) => void choose(e.target.value)}
        data-testid="llm-select"
      >
        <option value="">
          {task.task === "default"
            ? t("llm.automatic", { model: effectiveName })
            : t("llm.sameAsDefault", { model: effectiveName })}
        </option>
        {stale ? <option value={task.choice}>{t("llm.unavailable", { model: task.choice })}</option> : null}
        {overview.providers.map((provider) => (
          <optgroup key={provider.provider} label={provider.name}>
            {provider.models.map((model) => (
              <option key={model.ref} value={model.ref}>
                {model.name}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
      <ModelFacts model={effective} />
      {problem ? (
        <p role="alert" className="text-sm text-danger">
          {problem}
        </p>
      ) : null}
    </div>
  );
}

function DescribeLibraryToggle({ captions }: { captions: string | null }) {
  const { t } = useTranslation();
  const { data: settings } = $api.useQuery("get", "/api/settings");
  const [draft, setDraft] = useState<boolean | null>(null);
  if (!settings) return null;
  const on = draft ?? settings.describe_library;
  async function toggle(value: boolean) {
    setDraft(value); // show it at once; the settings event confirms it
    const { error } = await fetchClient.PATCH("/api/settings", { body: { describe_library: value } });
    if (error) setDraft(null);
  }
  return (
    <label className="flex cursor-pointer items-start gap-3 text-sm">
      <input
        type="checkbox"
        className="mt-0.5 size-4 accent-[var(--tungsten)]"
        checked={on}
        onChange={(e) => void toggle(e.target.checked)}
        data-testid="describe-library"
      />
      <span className="flex flex-col gap-0.5">
        {t("llm.describeLibrary")}
        <span className="text-xs text-muted">{t("llm.describeHint", { model: captions ?? t("llm.none") })}</span>
      </span>
    </label>
  );
}

/** Settings › Language models: the default model and a choice per task. */
export function LanguageModelsSection() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { data: overview } = $api.useQuery("get", "/api/llm");
  const [refreshing, setRefreshing] = useState(false);
  const saved = () => void refresh(queryClient, { queryKey: LLM_KEY });

  async function reload() {
    setRefreshing(true);
    const { data } = await fetchClient.GET("/api/llm", { params: { query: { refresh: true } } });
    if (data) queryClient.setQueriesData({ queryKey: LLM_KEY }, data);
    setRefreshing(false);
  }

  if (!overview) return null;
  if (overview.providers.length === 0) {
    return (
      <p className="text-sm text-muted" data-testid="llm-none">
        {t("llm.connectFirst")}
      </p>
    );
  }
  const models = new Map(overview.providers.flatMap((p) => p.models.map((m) => [m.ref, m] as const)));
  return (
    <div className="flex flex-col gap-5" data-testid="language-models">
      <div className="grid gap-5 sm:grid-cols-2">
        {overview.tasks.map((task) => (
          <TaskRow key={task.task} task={task} overview={overview} models={models} onSaved={saved} />
        ))}
      </div>
      <DescribeLibraryToggle captions={overview.tasks.find((task) => task.task === "captions")?.effective ?? null} />
      {overview.providers
        .filter((provider) => provider.error)
        .map((provider) => (
          <p key={provider.provider} role="status" className="text-sm text-tungsten">
            {t("llm.listFailed", { name: provider.name, error: provider.error })}
          </p>
        ))}
      <div className="flex flex-wrap items-center gap-3">
        <Button size="sm" variant="outline" onClick={() => void reload()} disabled={refreshing}>
          <RefreshCw className={cn(refreshing && "animate-spin")} />
          {t("llm.refresh")}
        </Button>
        <p className="text-xs text-muted">{t("llm.listNote")}</p>
      </div>
    </div>
  );
}
