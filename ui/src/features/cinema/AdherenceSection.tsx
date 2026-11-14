import { useTranslation } from "react-i18next";
import { $api, type Schemas } from "@/api/client";
import { cn } from "@/lib/utils";

type Measurement = Schemas["Measurement"];

const CONTROLS = [
  "genre",
  "era",
  "tempo",
  "body",
  "lens",
  "focal_length",
  "aperture",
  "moves",
  "lighting",
  "palette",
] as const;

const STYLE: Record<Measurement["status"], string> = {
  followed: "bg-ok/15 text-ok",
  weak: "bg-tungsten/15 text-tungsten",
  ignored: "bg-danger/15 text-danger",
  untested: "text-muted",
};
const MARK: Record<Measurement["status"], string> = { followed: "✓", weak: "~", ignored: "✕", untested: "·" };

/** Which cinema controls each model follows (plan step M5.1): measured with
 * fixed-seed A/B pairs, or untested until measured. */
export function AdherenceSection() {
  const { t } = useTranslation();
  const { data: rows = [] } = $api.useQuery("get", "/api/cinema/adherence");
  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-muted">{t("adherence.hint")}</p>
      <div className="overflow-x-auto rounded-xl border border-line">
        <table className="w-full min-w-[620px] text-xs" data-testid="adherence-table">
          <thead>
            <tr className="bg-raised text-left text-muted">
              <th className="px-3 py-2 font-medium">{t("adherence.model")}</th>
              {CONTROLS.map((control) => (
                <th key={control} className="px-1 py-2 text-center font-medium">
                  {t(`adherence.controls.${control}`)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.model} className="border-t border-line" data-model={row.model}>
                <td className="whitespace-nowrap px-3 py-1.5">{row.name}</td>
                {CONTROLS.map((control) => {
                  const m = row.controls[control];
                  if (!m) {
                    return (
                      <td key={control} className="px-1 py-1.5 text-center text-muted/50">
                        –
                      </td>
                    );
                  }
                  const title = [
                    t(`adherence.status.${m.status}`),
                    m.checked_at ? t("adherence.checked", { date: m.checked_at }) : null,
                    m.note,
                  ]
                    .filter(Boolean)
                    .join(" · ");
                  return (
                    <td key={control} className="px-1 py-1.5 text-center">
                      <span
                        className={cn("inline-block min-w-6 rounded px-1.5 py-0.5 font-mono", STYLE[m.status])}
                        title={title}
                        aria-label={`${t(`adherence.controls.${control}`)}: ${title}`}
                        data-status={m.status}
                        data-control={control}
                      >
                        {MARK[m.status]}
                      </span>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-muted">{t("adherence.legend")}</p>
    </div>
  );
}
