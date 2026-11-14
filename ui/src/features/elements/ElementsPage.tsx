import {
  Download,
  FolderPlus,
  Globe,
  Image as ImageIcon,
  MapPin,
  Package,
  Plus,
  Search,
  Sparkles,
  Upload,
  UserRound,
  X,
} from "lucide-react";
import { type FormEvent, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { inputClass, Popover, PopoverContent, PopoverTrigger } from "@/components/ui/misc";
import { useProjects } from "@/features/library/api";
import { cn } from "@/lib/utils";
import {
  CATEGORIES,
  type Category,
  type ElementOut,
  type ElementStatus,
  exportUrl,
  useElementActions,
  useElementFolders,
  useElements,
} from "./api";
import { ElementEditor } from "./ElementEditor";

export interface ElementsSearch {
  /** A project id; absent for the global elements. */
  scope?: string;
  category?: Category;
  status?: ElementStatus;
  folder?: string;
  q?: string;
}

const ICONS = { character: UserRound, location: MapPin, prop: Package, auto: Sparkles } as const;

function ElementCard({ element, showScope, onOpen }: { element: ElementOut; showScope: boolean; onOpen: () => void }) {
  const { t } = useTranslation();
  const Icon = ICONS[element.category];
  return (
    <button
      type="button"
      onClick={onOpen}
      data-testid="element-card"
      data-handle={element.handle}
      className="group flex flex-col overflow-hidden rounded-xl border border-line bg-flat text-left transition-colors hover:border-ink/40 focus-visible:border-tungsten focus-visible:outline-none"
    >
      <div className="relative aspect-[4/5] w-full bg-raised">
        {element.cover_url ? (
          <img src={element.cover_url} alt="" loading="lazy" className="size-full object-cover" />
        ) : (
          <div className="flex size-full items-center justify-center text-muted">
            <Icon className="size-10 opacity-50" aria-hidden />
          </div>
        )}
        <span
          className={cn(
            "absolute left-2 top-2 rounded-full px-2 py-px text-[10px] font-semibold uppercase tracking-wide",
            element.status === "ready" ? "bg-ok/90 text-black" : "bg-black/60 text-white",
          )}
        >
          {t(`elements.statuses.${element.status}`)}
        </span>
        {showScope && !element.project_id ? (
          <span
            className="absolute right-2 top-2 rounded-full bg-black/60 p-1 text-white"
            title={t("elements.globalBadge")}
          >
            <Globe className="size-3" aria-label={t("elements.globalBadge")} />
          </span>
        ) : null}
        {element.media.length > 1 ? (
          <span className="absolute bottom-2 right-2 flex items-center gap-1 rounded-full bg-black/60 px-1.5 py-px text-[10px] text-white">
            <ImageIcon className="size-3" aria-hidden />
            {element.media.length}
          </span>
        ) : null}
      </div>
      <div className="flex flex-col gap-0.5 p-2.5">
        <span className="truncate text-sm font-medium">{element.name}</span>
        <span className="truncate font-mono text-xs text-tungsten">@{element.handle}</span>
        <span className="flex items-center gap-1 text-[11px] text-muted">
          <Icon className="size-3" aria-hidden />
          {t(`elements.categories.${element.category}`)}
          {element.used_in > 0 ? ` · ${t("elements.usedIn", { count: element.used_in })}` : ""}
        </span>
      </div>
    </button>
  );
}

function NewFolder({ onCreate }: { onCreate: (name: string) => void }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  function submit(event: FormEvent) {
    event.preventDefault();
    if (name.trim()) onCreate(name.trim());
    setName("");
    setOpen(false);
  }
  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex h-7 items-center gap-1 rounded-full border border-dashed border-line px-2.5 text-xs text-muted hover:text-ink"
        data-testid="element-new-folder"
      >
        <FolderPlus className="size-3.5" aria-hidden /> {t("elements.newFolder")}
      </button>
    );
  }
  return (
    <form onSubmit={submit} className="flex items-center gap-1">
      <input
        autoFocus
        value={name}
        maxLength={80}
        onChange={(e) => setName(e.target.value)}
        onBlur={() => !name.trim() && setOpen(false)}
        placeholder={t("elements.folderName")}
        aria-label={t("elements.folderName")}
        className={cn(inputClass, "h-7 w-36 rounded-full text-xs")}
      />
    </form>
  );
}

export function ElementsPage({
  search,
  setSearch,
}: {
  search: ElementsSearch;
  setSearch: (patch: Partial<ElementsSearch>) => void;
}) {
  const { t } = useTranslation();
  const actions = useElementActions();
  const projectId = search.scope;
  const { data: projects = [] } = useProjects({ sort: "updated" });
  const { data: folders = [] } = useElementFolders(projectId);
  const { data: elements, isPending } = useElements({
    project_id: projectId,
    category: search.category,
    status: search.status,
    folder_id: search.folder,
    q: search.q,
  });
  const [editing, setEditing] = useState<ElementOut | null>(null);
  const [creating, setCreating] = useState(false);
  const importInput = useRef<HTMLInputElement>(null);
  const folder = folders.find((f) => f.id === search.folder);
  const filtered = Boolean(search.category || search.status || search.folder || search.q);

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="elements-page">
      <header className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
        <h1 className="mr-2 font-display text-2xl font-bold uppercase tracking-wide">{t("nav.elements")}</h1>
        <select
          aria-label={t("elements.scope")}
          value={projectId ?? ""}
          onChange={(e) => setSearch({ scope: e.target.value || undefined, folder: undefined })}
          className={cn(inputClass, "max-w-56")}
          data-testid="elements-scope"
        >
          <option value="">{t("elements.global")}</option>
          {projects.map((project) => (
            <option key={project.id} value={project.id}>
              {project.name}
            </option>
          ))}
        </select>
        <label className={cn(inputClass, "flex min-w-40 flex-1 items-center gap-2 sm:max-w-72")}>
          <Search className="size-4 shrink-0 text-muted" aria-hidden />
          <input
            type="search"
            value={search.q ?? ""}
            onChange={(e) => setSearch({ q: e.target.value || undefined })}
            placeholder={t("elements.search")}
            aria-label={t("elements.search")}
            className="min-w-0 flex-1 bg-transparent outline-none"
            data-testid="elements-search"
          />
        </label>
        <div className="ml-auto flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => importInput.current?.click()}>
            <Upload /> {t("elements.import")}
          </Button>
          <input
            ref={importInput}
            type="file"
            accept=".zip,.csv,application/zip,text/csv"
            hidden
            data-testid="elements-import"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void actions.importFile(file, projectId);
              e.target.value = "";
            }}
          />
          <Popover>
            <PopoverTrigger asChild>
              <Button variant="outline" size="sm" data-testid="elements-export">
                <Download /> {t("elements.export")}
              </Button>
            </PopoverTrigger>
            <PopoverContent className="flex w-64 flex-col gap-1 p-1.5">
              <a
                href={exportUrl(projectId, "zip")}
                download
                className="rounded-lg px-3 py-2 text-sm hover:bg-raised"
                data-testid="elements-export-zip"
              >
                <span className="block font-medium">{t("elements.exportZip")}</span>
                <span className="block text-xs text-muted">{t("elements.exportZipHint")}</span>
              </a>
              <a
                href={exportUrl(projectId, "csv")}
                download
                className="rounded-lg px-3 py-2 text-sm hover:bg-raised"
                data-testid="elements-export-csv"
              >
                <span className="block font-medium">{t("elements.exportCsv")}</span>
                <span className="block text-xs text-muted">{t("elements.exportCsvHint")}</span>
              </a>
            </PopoverContent>
          </Popover>
          <Button variant="primary" size="sm" onClick={() => setCreating(true)} data-testid="element-new">
            <Plus /> {t("elements.new")}
          </Button>
        </div>
      </header>

      <div className="flex flex-col gap-2 border-b border-line px-4 py-2">
        <div className="flex flex-wrap items-center gap-2">
          <div
            role="tablist"
            aria-label={t("elements.category")}
            className="flex flex-wrap rounded-lg border border-line p-0.5"
          >
            {([undefined, ...CATEGORIES] as const).map((category) => (
              <button
                key={category ?? "all"}
                type="button"
                role="tab"
                aria-selected={search.category === category}
                onClick={() => setSearch({ category })}
                className={cn(
                  "rounded-md px-3 py-1 text-sm",
                  search.category === category ? "bg-raised text-ink" : "text-muted hover:text-ink",
                )}
              >
                {category ? t(`elements.tabs.${category}`) : t("elements.tabs.all")}
              </button>
            ))}
          </div>
          <div className="flex gap-1" role="group" aria-label={t("elements.status")}>
            {(["draft", "ready"] as const).map((status) => (
              <button
                key={status}
                type="button"
                aria-pressed={search.status === status}
                onClick={() => setSearch({ status: search.status === status ? undefined : status })}
                className={cn(
                  "h-7 rounded-full border px-2.5 text-xs",
                  search.status === status
                    ? "border-ink/60 bg-raised text-ink"
                    : "border-line text-muted hover:text-ink",
                )}
              >
                {t(`elements.statuses.${status}`)}
              </button>
            ))}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label={t("elements.folders")}>
          <button
            type="button"
            aria-pressed={!search.folder}
            onClick={() => setSearch({ folder: undefined })}
            className={cn(
              "h-7 rounded-full border px-2.5 text-xs",
              !search.folder ? "border-ink/60 bg-raised text-ink" : "border-line text-muted hover:text-ink",
            )}
          >
            {t("elements.allFolders")}
          </button>
          {folders.map((f) => (
            <span key={f.id} className="inline-flex items-center">
              <button
                type="button"
                aria-pressed={search.folder === f.id}
                onClick={() => setSearch({ folder: search.folder === f.id ? undefined : f.id })}
                className={cn(
                  "h-7 rounded-full border px-2.5 text-xs",
                  search.folder === f.id ? "border-ink/60 bg-raised text-ink" : "border-line text-muted hover:text-ink",
                )}
              >
                {f.name} <span className="text-muted">{f.element_count}</span>
              </button>
              {search.folder === f.id ? (
                <button
                  type="button"
                  className="ml-0.5 rounded-full p-1 text-muted hover:text-danger"
                  aria-label={t("elements.deleteFolder", { name: f.name })}
                  title={t("elements.deleteFolderHint")}
                  onClick={() => {
                    setSearch({ folder: undefined });
                    void actions.deleteFolder(f.id);
                  }}
                >
                  <X className="size-3.5" />
                </button>
              ) : null}
            </span>
          ))}
          <NewFolder onCreate={(name) => void actions.createFolder(name, projectId)} />
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {!isPending && elements?.length === 0 ? (
          <div className="mx-auto flex max-w-md flex-col items-center gap-3 py-16 text-center">
            <UserRound className="size-10 text-muted" aria-hidden />
            <p className="text-sm text-muted">{filtered ? t("elements.noMatch") : t("elements.empty")}</p>
            {filtered ? (
              <Button
                variant="ghost"
                onClick={() => setSearch({ category: undefined, status: undefined, folder: undefined, q: undefined })}
              >
                {t("elements.clearFilters")}
              </Button>
            ) : (
              <Button variant="primary" onClick={() => setCreating(true)}>
                <Plus /> {t("elements.new")}
              </Button>
            )}
          </div>
        ) : (
          <div
            className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6"
            data-testid="element-grid"
          >
            {(elements ?? []).map((element) => (
              <ElementCard
                key={element.id}
                element={element}
                showScope={Boolean(projectId)}
                onOpen={() => setEditing(element)}
              />
            ))}
          </div>
        )}
      </div>

      {creating ? (
        <ElementEditor open onOpenChange={setCreating} projectId={projectId} defaultFolderId={folder?.id} />
      ) : null}
      {editing ? (
        <ElementEditor key={editing.id} open onOpenChange={(open) => !open && setEditing(null)} element={editing} />
      ) : null}
    </div>
  );
}
