import { useQueryClient } from "@tanstack/react-query";
import { Plus, Search } from "lucide-react";
import { type FormEvent, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { $api, errorMessage, fetchClient, type Schemas } from "@/api/client";
import { refresh } from "@/api/events";
import { Button } from "@/components/ui/button";
import { inputClass } from "@/components/ui/misc";
import { cn } from "@/lib/utils";

type Summary = Schemas["FalModelSummary"];
const CATEGORIES = ["", "text-to-image", "image-to-image", "text-to-video", "image-to-video"] as const;

/** Add models from fal.ai (plan step M2.10): search fal's catalog and add a
 * model; its parameters and price are read from fal. */
export function FalModels() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { data: models = [] } = $api.useQuery("get", "/api/models");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<(typeof CATEGORIES)[number]>("");
  const [results, setResults] = useState<Summary[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const searchId = useId();
  const added = new Set(models.filter((m) => m.provider === "fal").map((m) => m.id));
  const idFor = (endpoint: string) =>
    `fal-${endpoint
      .toLowerCase()
      .replace(/^fal-ai\//, "")
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "")}`;

  async function search(event: FormEvent) {
    event.preventDefault();
    setBusy("search");
    setProblem(null);
    const { data, error } = await fetchClient.GET("/api/catalog/fal/search", {
      params: { query: { q: query, ...(category ? { category } : {}) } },
    });
    setBusy(null);
    if (error || !data) return setProblem(errorMessage(error));
    setResults(data);
  }

  async function add(model: Summary) {
    setBusy(model.endpoint_id);
    const { data, error } = await fetchClient.POST("/api/catalog/fal/import", {
      body: { endpoint_ids: [model.endpoint_id] },
    });
    setBusy(null);
    if (error || !data) return toast.error(errorMessage(error));
    toast.success(t("fal.added", { name: data[0]?.name ?? model.name }));
    await refresh(queryClient, { queryKey: ["get", "/api/models"] });
  }

  return (
    <div className="flex flex-col gap-2 border-t border-line pt-3" data-testid="fal-models">
      <label htmlFor={searchId} className="text-sm font-medium">
        {t("fal.title")}
      </label>
      <p className="text-xs text-muted">{t("fal.hint")}</p>
      <form className="flex flex-wrap gap-2" onSubmit={(e) => void search(e)}>
        <input
          id={searchId}
          className={cn(inputClass, "min-w-0 flex-1")}
          placeholder={t("fal.placeholder")}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          data-testid="fal-search"
        />
        <select
          className={inputClass}
          value={category}
          onChange={(e) => setCategory(e.target.value as (typeof CATEGORIES)[number])}
          aria-label={t("fal.category")}
        >
          {CATEGORIES.map((value) => (
            <option key={value} value={value}>
              {value ? t(`fal.categories.${value}`) : t("fal.anyCategory")}
            </option>
          ))}
        </select>
        <Button type="submit" variant="outline" disabled={busy === "search"}>
          <Search />
          {t("fal.search")}
        </Button>
      </form>
      {problem ? (
        <p role="alert" className="text-sm text-danger">
          {problem}
        </p>
      ) : null}
      {results ? (
        results.length === 0 ? (
          <p className="text-sm text-muted">{t("fal.none")}</p>
        ) : (
          <ul className="flex flex-col divide-y divide-line rounded-lg border border-line" data-testid="fal-results">
            {results.map((model) => {
              const done = added.has(idFor(model.endpoint_id));
              return (
                <li key={model.endpoint_id} className="flex items-start gap-3 px-3 py-2">
                  <div className="flex min-w-0 flex-1 flex-col">
                    <span className="text-sm font-medium">{model.name}</span>
                    <span className="truncate font-mono text-[11px] text-muted">
                      {model.endpoint_id} · {model.category ?? "?"}
                    </span>
                    {model.description ? (
                      <span className="line-clamp-2 text-xs text-muted">{model.description}</span>
                    ) : null}
                  </div>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={done || !model.supported || busy !== null}
                    title={!model.supported ? t("fal.unsupported") : undefined}
                    onClick={() => void add(model)}
                    data-testid="fal-add"
                  >
                    <Plus />
                    {done ? t("fal.inCatalog") : t("fal.add")}
                  </Button>
                </li>
              );
            })}
          </ul>
        )
      ) : null}
    </div>
  );
}
