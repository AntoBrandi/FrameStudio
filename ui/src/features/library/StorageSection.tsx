import { useTranslation } from "react-i18next";
import { $api, type Schemas } from "@/api/client";
import { formatBytes } from "@/lib/utils";

function Rows({ title, rows }: { title: string; rows: Schemas["StorageRow"][] }) {
  const total = Math.max(1, ...rows.map((row) => row.bytes));
  return (
    <div className="flex flex-col gap-1.5">
      <h3 className="font-mono text-[11px] uppercase tracking-widest text-muted">{title}</h3>
      <table className="w-full text-sm">
        <tbody>
          {rows.map((row) => (
            <tr key={row.key}>
              <th scope="row" className="whitespace-nowrap py-1 pr-3 text-left font-normal">
                {row.label}
              </th>
              <td className="w-full py-1">
                <span
                  className="block h-1.5 rounded-full bg-tungsten"
                  style={{ width: `${Math.max(2, (row.bytes / total) * 100)}%` }}
                  aria-hidden
                />
              </td>
              <td className="whitespace-nowrap py-1 pl-3 text-right font-mono text-xs">
                {formatBytes(row.bytes)} · {row.count}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Where the disk space goes: media, derived files, database; by type and project. */
export function StorageSection() {
  const { t } = useTranslation();
  const { data } = $api.useQuery("get", "/api/storage");
  if (!data) return null;
  const totals = [
    [t("settings.onDisk"), data.disk_bytes],
    [t("settings.media"), data.media_bytes],
    [t("settings.derived"), data.derived_bytes],
    [t("settings.database"), data.database_bytes],
    [t("settings.inTrash"), data.trash.bytes],
  ] as const;
  return (
    <div className="flex flex-col gap-4" data-testid="storage-report">
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        {totals.map(([label, bytes]) => (
          <div key={label} className="flex flex-col">
            <dt className="text-xs text-muted">{label}</dt>
            <dd className="font-mono text-sm">{formatBytes(bytes)}</dd>
          </div>
        ))}
      </dl>
      {data.by_kind.length ? (
        <Rows
          title={t("settings.byKind")}
          rows={data.by_kind.map((row) => ({
            ...row,
            label: t(`library.kinds.${row.key}`, { defaultValue: row.label }),
          }))}
        />
      ) : null}
      {data.by_project.length ? (
        <Rows
          title={t("settings.byProject")}
          rows={data.by_project.map((row) => (row.key === "none" ? { ...row, label: t("library.libraryRoot") } : row))}
        />
      ) : null}
    </div>
  );
}
