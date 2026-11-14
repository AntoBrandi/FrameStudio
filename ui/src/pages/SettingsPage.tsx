import { useQueryClient } from "@tanstack/react-query";
import { Cloud, Cpu } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { $api, type AppSettings, type AppSettingsPatch, fetchClient } from "@/api/client";
import { SETTINGS_KEY } from "@/api/events";
import { useTheme } from "@/app/theme";
import { Button } from "@/components/ui/button";
import { Field, inputClass } from "@/components/ui/misc";
import { useAuthStore } from "@/features/auth/store";
import { ConnectionsSection, UsageSection } from "@/features/connections/ConnectionsSection";
import { AdherenceSection } from "@/features/cinema/AdherenceSection";
import { LanguageModelsSection } from "@/features/llm/LanguageModelsSection";
import { StorageSection } from "@/features/library/StorageSection";
import { LANGUAGES } from "@/i18n";

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section aria-label={title} className="flex flex-col gap-4 rounded-2xl border border-line bg-flat p-5">
      <h2 className="font-display text-xl font-semibold uppercase tracking-wide">{title}</h2>
      {children}
    </section>
  );
}

function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <label className="flex cursor-pointer items-center gap-3 text-sm">
      <input
        type="checkbox"
        className="size-4 accent-[var(--tungsten)]"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      {label}
    </label>
  );
}

export function SettingsPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { data: settings } = $api.useQuery("get", "/api/settings");
  const { data: info } = $api.useQuery("get", "/api/system/info");
  const patch = $api.useMutation("patch", "/api/settings");
  const fakeJob = $api.useMutation("post", "/api/jobs/fake");
  const { theme, setTheme } = useTheme();
  const setNeedsToken = useAuthStore((s) => s.setNeedsToken);

  function update(body: AppSettingsPatch) {
    queryClient.setQueryData<AppSettings>(SETTINGS_KEY, (old) => (old ? ({ ...old, ...body } as AppSettings) : old));
    patch.mutate(
      { body },
      { onSettled: () => void queryClient.invalidateQueries({ queryKey: ["get", "/api/system/info"] }) },
    );
  }

  async function signOut() {
    await fetchClient.DELETE("/api/auth/session");
    setNeedsToken(true);
  }

  if (!settings) return null;
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-5 px-6 py-10">
      <h1 className="font-display text-4xl font-bold uppercase">{t("settings.title")}</h1>
      <Section title={t("settings.general")}>
        <Field label={t("settings.theme")}>
          <select
            className={inputClass}
            value={theme}
            onChange={(e) => setTheme(e.target.value as AppSettings["theme"])}
          >
            {(["dark", "light", "system"] as const).map((value) => (
              <option key={value} value={value}>
                {t(`settings.themes.${value}`)}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("settings.language")}>
          <select
            className={inputClass}
            value={settings.language}
            onChange={(e) => update({ language: e.target.value })}
          >
            {LANGUAGES.map((language) => (
              <option key={language.code} value={language.code}>
                {language.name}
              </option>
            ))}
          </select>
        </Field>
        <Toggle
          checked={settings.notify_on_job_finish}
          label={t("settings.notify")}
          onChange={(value) => {
            if (value && typeof Notification !== "undefined" && Notification.permission === "default") {
              void Notification.requestPermission();
            }
            update({ notify_on_job_finish: value });
          }}
        />
      </Section>
      <Section title={t("settings.connections")}>
        <ConnectionsSection />
      </Section>
      <Section title={t("settings.languageModels")}>
        <LanguageModelsSection />
      </Section>
      <Section title={t("settings.cinema")}>
        <AdherenceSection />
      </Section>
      <Section title={t("settings.usage")}>
        <UsageSection />
      </Section>
      <Section title={t("settings.server")}>
        <Toggle
          checked={settings.remote_access}
          label={t("settings.remoteAccess")}
          onChange={(value) => update({ remote_access: value })}
        />
        {info?.restart_required ? <p className="text-sm text-tungsten">{t("settings.restartRequired")}</p> : null}
        <div>
          <Button variant="outline" size="sm" onClick={() => void signOut()}>
            {t("settings.signOut")}
          </Button>
        </div>
      </Section>
      <Section title={t("settings.developer")}>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t("settings.mockLatency")}>
            <input
              className={inputClass}
              type="number"
              min={0}
              max={120}
              step={0.5}
              value={settings.mock_latency_seconds}
              onChange={(e) => update({ mock_latency_seconds: Number(e.target.value) })}
            />
          </Field>
          <Field label={t("settings.mockFailure")}>
            <input
              className={inputClass}
              type="number"
              min={0}
              max={1}
              step={0.1}
              value={settings.mock_failure_rate}
              onChange={(e) => update({ mock_failure_rate: Number(e.target.value) })}
            />
          </Field>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => fakeJob.mutate({ body: { kind: "local", seconds: 10 } })}
            data-testid="fake-local"
          >
            <Cpu />
            {t("settings.fakeLocal")}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => fakeJob.mutate({ body: { kind: "remote", seconds: 10 } })}
            data-testid="fake-remote"
          >
            <Cloud />
            {t("settings.fakeRemote")}
          </Button>
        </div>
      </Section>
      <Section title={t("settings.storage")}>
        <StorageSection />
      </Section>
      {info ? (
        <Section title={t("settings.about")}>
          <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 font-mono text-xs">
            <dt className="text-muted">version</dt>
            <dd>{info.version}</dd>
            <dt className="text-muted">data</dt>
            <dd className="break-all">{info.data_dir}</dd>
            <dt className="text-muted">schema</dt>
            <dd>{info.schema_revision}</dd>
            <dt className="text-muted">listening</dt>
            <dd>
              {info.bind_host}:{info.port}
            </dd>
          </dl>
        </Section>
      ) : null}
    </div>
  );
}
