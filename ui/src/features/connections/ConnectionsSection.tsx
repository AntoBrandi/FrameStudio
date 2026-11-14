import { useQueryClient } from "@tanstack/react-query";
import { ExternalLink, KeyRound, RefreshCw, Trash2 } from "lucide-react";
import { type FormEvent, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { $api, errorMessage, fetchClient, type Schemas } from "@/api/client";
import { refresh } from "@/api/events";
import { Button } from "@/components/ui/button";
import { inputClass, Progress } from "@/components/ui/misc";
import { cn } from "@/lib/utils";
import { FalModels } from "./FalModels";

type Connection = Schemas["ConnectionOut"];

export const CONNECTIONS_KEY = ["get", "/api/connections"] as const;
export const USAGE_KEY = ["get", "/api/usage"] as const;

const usd = (value: number) =>
  new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: value > 0 && value < 0.01 ? 4 : 2,
  }).format(value);

function StatusChip({ connection }: { connection: Connection }) {
  const { t } = useTranslation();
  let tone = "border-line text-muted";
  let label = t("connections.notConnected");
  if (connection.connected && !connection.enabled) {
    label = t("connections.off");
  } else if (connection.connected) {
    const status = connection.status ?? "unchecked";
    label = t(`connections.status.${status}`);
    tone =
      status === "ok"
        ? "border-ok/40 bg-ok/10 text-ok"
        : status === "refused" || status === "missing"
          ? "border-danger/40 bg-danger/10 text-danger"
          : "border-tungsten/40 bg-tungsten/10 text-tungsten";
  }
  return (
    <span className={cn("rounded-full border px-2 py-px font-mono text-[11px]", tone)} data-testid="connection-status">
      {label}
    </span>
  );
}

function KeyForm({
  connection,
  onDone,
  onCancel,
}: {
  connection: Connection;
  onDone: () => void;
  onCancel?: () => void;
}) {
  const { t } = useTranslation();
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const inputId = useId();

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!key.trim()) return;
    setBusy(true);
    setProblem(null);
    const { error } = await fetchClient.PUT("/api/connections/{provider}", {
      params: { path: { provider: connection.provider } },
      body: { api_key: key.trim() },
    });
    setBusy(false);
    if (error) return setProblem(errorMessage(error));
    setKey(""); // the key leaves the page as soon as the backend has it
    onDone();
  }

  return (
    <form className="flex flex-col gap-2" onSubmit={(e) => void submit(e)} aria-label={t("connections.addKey")}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <label htmlFor={inputId} className="text-sm font-medium">
          {t("connections.keyLabel", { name: connection.name })}
        </label>
        {connection.key_url ? (
          <a
            className="inline-flex items-center gap-1 text-sm text-daylight hover:underline"
            href={connection.key_url}
            target="_blank"
            rel="noreferrer noopener"
          >
            {t("connections.getKey")}
            <ExternalLink className="size-3.5" aria-hidden />
          </a>
        ) : null}
      </div>
      <div className="flex flex-wrap gap-2">
        <input
          id={inputId}
          className={cn(inputClass, "min-w-0 flex-1 font-mono")}
          type="password"
          autoComplete="off"
          spellCheck={false}
          placeholder={t("connections.keyPlaceholder")}
          value={key}
          onChange={(e) => setKey(e.target.value)}
          data-testid="api-key-input"
        />
        <Button type="submit" variant="primary" disabled={busy || !key.trim()} data-testid="connect-key">
          <KeyRound />
          {busy ? t("connections.checking") : t("connections.connect")}
        </Button>
        {onCancel ? (
          <Button type="button" variant="ghost" onClick={onCancel}>
            {t("connections.cancel")}
          </Button>
        ) : null}
      </div>
      {problem ? (
        <p role="alert" className="text-sm text-danger">
          {problem}
        </p>
      ) : (
        <p className="text-xs text-muted">{t("connections.keyHint")}</p>
      )}
    </form>
  );
}

function CapField({ connection, onSaved }: { connection: Connection; onSaved: () => void }) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const inputId = useId();
  const value = draft ?? (connection.monthly_cap_usd === null ? "" : String(connection.monthly_cap_usd));

  async function save() {
    if (draft === null) return;
    const trimmed = draft.trim();
    const cap = trimmed === "" ? null : Number(trimmed);
    if (cap !== null && (!Number.isFinite(cap) || cap < 0)) return setProblem(t("connections.capInvalid"));
    const { error } = await fetchClient.PUT("/api/connections/{provider}", {
      params: { path: { provider: connection.provider } },
      body: { monthly_cap_usd: cap },
    });
    if (error) return setProblem(errorMessage(error));
    setProblem(null);
    setDraft(null);
    onSaved();
  }

  const cap = connection.monthly_cap_usd;
  const spent = connection.spent_this_month_usd;
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex flex-col gap-1.5">
          <label htmlFor={inputId} className="text-sm font-medium">
            {t("connections.cap")}
          </label>
          <input
            id={inputId}
            className={cn(inputClass, "w-32 font-mono")}
            type="number"
            min={0}
            step={1}
            inputMode="decimal"
            placeholder={t("connections.noCap")}
            value={value}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={() => void save()}
            onKeyDown={(e) => {
              if (e.key === "Enter") void save();
            }}
            data-testid="cap-input"
          />
        </div>
        <p className="pb-2 text-sm text-muted" data-testid="spent">
          {cap === null
            ? t("connections.spentNoCap", { spent: usd(spent) })
            : t("connections.spentOfCap", { spent: usd(spent), cap: usd(cap) })}
        </p>
      </div>
      {cap !== null ? (
        <Progress value={cap > 0 ? spent / cap : 1} label={t("connections.capUsed")} className="max-w-sm" />
      ) : null}
      {problem ? (
        <p role="alert" className="text-sm text-danger">
          {problem}
        </p>
      ) : null}
    </div>
  );
}

function ConnectionCard({ connection, onChanged }: { connection: Connection; onChanged: () => void }) {
  const { t, i18n } = useTranslation();
  const [replacing, setReplacing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const path = { params: { path: { provider: connection.provider } } };
  const titleId = useId();

  // The toggle shows the new state at once; it falls back if the change fails.
  const [enabledDraft, setEnabledDraft] = useState<boolean | null>(null);
  const enabled = enabledDraft ?? connection.enabled;
  if (enabledDraft !== null && !busy && connection.enabled === enabledDraft) setEnabledDraft(null);

  async function run(action: () => Promise<{ error?: unknown }>): Promise<boolean> {
    setBusy(true);
    setProblem(null);
    const { error } = await action();
    setBusy(false);
    if (error) setProblem(errorMessage(error));
    onChanged();
    return !error;
  }

  async function toggle(value: boolean) {
    setEnabledDraft(value);
    const ok = await run(() => fetchClient.PUT("/api/connections/{provider}", { ...path, body: { enabled: value } }));
    if (!ok) setEnabledDraft(null);
  }

  const checked = connection.validated_at
    ? new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium", timeStyle: "short" }).format(
        new Date(connection.validated_at),
      )
    : null;
  const showKeyForm = connection.requires_key && (!connection.connected || replacing);

  return (
    <article
      aria-labelledby={titleId}
      className="flex flex-col gap-3 rounded-xl border border-line bg-stage p-4"
      data-testid={`connection-${connection.provider}`}
    >
      <header className="flex flex-wrap items-center gap-2">
        <h3 id={titleId} className="font-display text-lg font-semibold">
          {connection.name}
        </h3>
        {connection.test ? (
          <span className="rounded-full border border-line px-2 py-px font-mono text-[11px] text-muted">
            {t("connections.testProvider")}
          </span>
        ) : null}
        <StatusChip connection={{ ...connection, enabled }} />
        {connection.connected && !connection.test ? (
          <label className="ml-auto flex cursor-pointer items-center gap-2 text-sm">
            <input
              type="checkbox"
              className="size-4 accent-[var(--tungsten)]"
              checked={enabled}
              disabled={busy}
              onChange={(e) => void toggle(e.target.checked)}
              data-testid="connection-enabled"
            />
            {t("connections.use")}
          </label>
        ) : null}
      </header>
      <p className="text-sm text-muted">{connection.description}</p>
      <p className="font-mono text-[11px] text-muted">
        {connection.hosts.length
          ? t("connections.hosts", { hosts: connection.hosts.join(", ") })
          : t("connections.noHosts")}
      </p>

      {connection.connected && connection.requires_key ? (
        <div className="flex flex-col gap-1 text-sm">
          <p>
            {t("connections.keyStored", {
              hint: connection.key_hint ?? "",
              store: t(`connections.stores.${connection.key_store ?? "file"}`),
            })}
          </p>
          {connection.status_message ? (
            <p className={cn("text-sm", connection.status === "ok" ? "text-muted" : "text-tungsten")}>
              {connection.status_message}
              {checked && connection.status === "ok" ? ` ${t("connections.checkedAt", { when: checked })}` : ""}
            </p>
          ) : null}
        </div>
      ) : null}

      {showKeyForm ? (
        <KeyForm
          connection={connection}
          onDone={() => {
            setReplacing(false);
            onChanged();
          }}
          onCancel={replacing ? () => setReplacing(false) : undefined}
        />
      ) : null}

      {connection.connected || !connection.requires_key ? (
        <CapField connection={connection} onSaved={onChanged} />
      ) : null}
      {connection.provider === "fal" && connection.connected && connection.enabled ? <FalModels /> : null}

      {confirming ? (
        <div
          role="alertdialog"
          aria-label={t("connections.removeTitle", { name: connection.name })}
          className="flex flex-wrap items-center gap-3 rounded-lg border border-danger/50 bg-danger/10 px-3 py-2"
        >
          <p className="flex-1 text-sm">{t("connections.removeConfirm", { name: connection.name })}</p>
          <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>
            {t("connections.cancel")}
          </Button>
          <Button
            size="sm"
            variant="danger"
            disabled={busy}
            onClick={() => {
              setConfirming(false);
              void run(() => fetchClient.DELETE("/api/connections/{provider}", path));
            }}
            data-testid="confirm-remove-connection"
          >
            <Trash2 />
            {t("connections.remove")}
          </Button>
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        {connection.connected && connection.requires_key && !replacing ? (
          <>
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => void run(() => fetchClient.POST("/api/connections/{provider}/check", path))}
              data-testid="check-connection"
            >
              <RefreshCw />
              {busy ? t("connections.checking") : t("connections.check")}
            </Button>
            <Button size="sm" variant="outline" onClick={() => setReplacing(true)}>
              <KeyRound />
              {t("connections.replace")}
            </Button>
          </>
        ) : null}
        {connection.connected && !connection.test && !confirming ? (
          <Button size="sm" variant="danger" onClick={() => setConfirming(true)} data-testid="remove-connection">
            <Trash2 />
            {connection.requires_key ? t("connections.removeKey") : t("connections.remove")}
          </Button>
        ) : null}
      </div>
      {problem ? (
        <p role="alert" className="text-sm text-danger">
          {problem}
        </p>
      ) : null}
    </article>
  );
}

/** Settings › Connections: keys for hosted providers, on/off, monthly caps. */
export function ConnectionsSection() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { data: connections } = $api.useQuery("get", "/api/connections");
  const changed = () => {
    void refresh(queryClient, { queryKey: CONNECTIONS_KEY });
    void refresh(queryClient, { queryKey: USAGE_KEY });
  };
  if (!connections) return null;
  const real = connections.filter((c) => !c.test);
  const test = connections.filter((c) => c.test);
  return (
    <div className="flex flex-col gap-3" data-testid="connections">
      <p className="text-sm text-muted">{t("connections.intro")}</p>
      {[...real, ...test].map((connection) => (
        <ConnectionCard key={connection.provider} connection={connection} onChanged={changed} />
      ))}
    </div>
  );
}

/** What hosted providers cost this month. */
export function UsageSection() {
  const { t } = useTranslation();
  const { data } = $api.useQuery("get", "/api/usage");
  const { data: connections = [] } = $api.useQuery("get", "/api/connections");
  if (!data) return null;
  const names = Object.fromEntries(connections.map((c) => [c.provider, c.name]));
  if (data.providers.length === 0) return <p className="text-sm text-muted">{t("usage.none")}</p>;
  return (
    <div className="flex flex-col gap-2" data-testid="usage">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left font-mono text-[11px] uppercase tracking-widest text-muted">
              <th className="py-1 pr-3 font-normal">{t("usage.provider")}</th>
              <th className="py-1 pr-3 text-right font-normal">{t("usage.requests")}</th>
              <th className="py-1 pr-3 text-right font-normal">{t("usage.tokens")}</th>
              <th className="py-1 pr-3 text-right font-normal">{t("usage.images")}</th>
              <th className="py-1 pr-3 text-right font-normal">{t("usage.videoSeconds")}</th>
              <th className="py-1 text-right font-normal">{t("usage.cost")}</th>
            </tr>
          </thead>
          <tbody>
            {data.providers.map((row) => (
              <tr key={row.provider} className="border-t border-line">
                <th scope="row" className="py-1.5 pr-3 text-left font-normal">
                  {names[row.provider] ?? row.provider}
                </th>
                <td className="py-1.5 pr-3 text-right font-mono text-xs">{row.requests}</td>
                <td className="py-1.5 pr-3 text-right font-mono text-xs">
                  {(row.input_tokens + row.output_tokens).toLocaleString()}
                </td>
                <td className="py-1.5 pr-3 text-right font-mono text-xs">{row.images}</td>
                <td className="py-1.5 pr-3 text-right font-mono text-xs">{row.video_seconds}</td>
                <td className="py-1.5 text-right font-mono text-xs">{usd(row.cost_usd)}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="border-t border-line">
              <th scope="row" className="py-1.5 pr-3 text-left font-medium" colSpan={5}>
                {t("usage.total")}
              </th>
              <td className="py-1.5 text-right font-mono text-xs font-semibold">{usd(data.total_cost_usd)}</td>
            </tr>
          </tfoot>
        </table>
      </div>
      <p className="text-xs text-muted">{t("usage.note", { month: data.month })}</p>
    </div>
  );
}
