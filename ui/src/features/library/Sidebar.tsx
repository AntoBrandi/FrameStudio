import { useQueryClient } from "@tanstack/react-query";
import { Link, useRouterState } from "@tanstack/react-router";
import {
  ArrowLeft,
  FileText,
  Folder,
  FolderOpen,
  MoreHorizontal,
  Plus,
  SquareKanban,
  Star,
  Trash2,
} from "lucide-react";
import { type DragEvent, type FormEvent, type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { $api, errorMessage, fetchClient } from "@/api/client";
import { Button } from "@/components/ui/button";
import { inputClass, Popover, PopoverContent, PopoverTrigger } from "@/components/ui/misc";
import { cn } from "@/lib/utils";
import {
  ASSET_DRAG_TYPE,
  type FolderOut,
  invalidateLibrary,
  type MoveTarget,
  useFolders,
  useLibraryActions,
  useProjects,
  useSummary,
} from "./api";
import { FOLDER_COLORS, folderColor } from "./colors";

/** Accept assets dragged from the grid. */
function useAssetDrop(onIds: (ids: string[]) => void) {
  const [over, setOver] = useState(false);
  const accepts = (event: DragEvent) => Array.from(event.dataTransfer.types).includes(ASSET_DRAG_TYPE);
  return {
    over,
    props: {
      onDragOver: (event: DragEvent) => {
        if (!accepts(event)) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
        setOver(true);
      },
      onDragLeave: () => setOver(false),
      onDrop: (event: DragEvent) => {
        setOver(false);
        if (!accepts(event)) return;
        event.preventDefault();
        const ids = JSON.parse(event.dataTransfer.getData(ASSET_DRAG_TYPE) || "[]") as string[];
        if (ids.length) onIds(ids);
      },
    },
  };
}

const itemClass =
  "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-muted hover:bg-raised hover:text-ink [&_svg]:size-4 [&_svg]:shrink-0";
const activeClass = "bg-raised text-ink";

function Section({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <div className="flex items-center justify-between px-2 pb-1 pt-4">
        <span className="font-mono text-[11px] uppercase tracking-widest text-muted">{title}</span>
        {action}
      </div>
      {children}
    </div>
  );
}

function Count({ value }: { value?: number }) {
  return value === undefined ? null : <span className="ml-auto font-mono text-[11px]">{value}</span>;
}

function DropLink({
  to,
  params,
  search,
  active,
  icon,
  label,
  count,
  onDropIds,
  testId,
}: {
  to: string;
  params?: Record<string, string>;
  search?: Record<string, unknown>;
  active: boolean;
  icon: ReactNode;
  label: string;
  count?: number;
  onDropIds?: (ids: string[]) => void;
  testId?: string;
}) {
  const drop = useAssetDrop(onDropIds ?? (() => undefined));
  return (
    <Link
      // Router paths are typed per route; these links are built from data.
      to={to as "/assets"}
      params={params as never}
      search={search as never}
      className={cn(itemClass, active && activeClass, drop.over && "bg-tungsten-soft text-ink ring-1 ring-tungsten")}
      data-testid={testId}
      {...(onDropIds ? drop.props : {})}
    >
      {icon}
      <span className="truncate">{label}</span>
      <Count value={count} />
    </Link>
  );
}

function FolderMenu({ folder }: { folder: FolderOut }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [name, setName] = useState(folder.name);
  const [open, setOpen] = useState(false);
  async function patch(body: { name?: string; color?: FolderOut["color"] }) {
    const { error } = await fetchClient.PATCH("/api/folders/{folder_id}", {
      params: { path: { folder_id: folder.id } },
      body,
    });
    if (error) toast.error(errorMessage(error));
    await invalidateLibrary(queryClient);
  }
  async function remove() {
    setOpen(false);
    await fetchClient.DELETE("/api/folders/{folder_id}", { params: { path: { folder_id: folder.id } } });
    await invalidateLibrary(queryClient);
  }
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="rounded p-0.5 text-muted opacity-0 hover:text-ink focus-visible:opacity-100 group-hover:opacity-100"
          aria-label={t("shell.folderMenu", { name: folder.name })}
        >
          <MoreHorizontal className="size-4" />
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        className="flex w-60 flex-col gap-3"
        aria-label={t("shell.folderMenu", { name: folder.name })}
      >
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (name.trim() && name !== folder.name) void patch({ name: name.trim() });
          }}
          className="flex gap-1"
        >
          <input
            aria-label={t("shell.rename")}
            className={cn(inputClass, "h-8 flex-1")}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <Button type="submit" size="sm">
            {t("shell.rename")}
          </Button>
        </form>
        <div role="radiogroup" aria-label={t("shell.color")} className="flex flex-wrap gap-1.5">
          {FOLDER_COLORS.map((color) => (
            <button
              key={color}
              type="button"
              role="radio"
              aria-checked={folder.color === color}
              aria-label={color}
              onClick={() => void patch({ color })}
              className={cn(
                "size-6 rounded-full border-2",
                folder.color === color ? "border-ink" : "border-transparent",
              )}
              style={{ background: folderColor(color) }}
            />
          ))}
        </div>
        <div className="flex flex-col gap-1 border-t border-line pt-2">
          <Button size="sm" variant="danger" onClick={() => void remove()}>
            <Trash2 />
            {t("shell.deleteFolder")}
          </Button>
          <span className="text-xs text-muted">{t("shell.deleteFolderHint")}</span>
        </div>
      </PopoverContent>
    </Popover>
  );
}

function FolderRow({
  folder,
  active,
  projectId,
  onDropIds,
}: {
  folder: FolderOut;
  active: boolean;
  projectId?: string;
  onDropIds: (ids: string[]) => void;
}) {
  const drop = useAssetDrop(onDropIds);
  const linkProps = projectId
    ? { to: "/studio/$projectId" as const, params: { projectId }, search: { folder: folder.id } }
    : { to: "/assets" as const, search: { folder: folder.id } };
  return (
    <div
      className={cn(
        "group flex items-center gap-1 rounded-md pr-1",
        active && activeClass,
        drop.over && "bg-tungsten-soft ring-1 ring-tungsten",
      )}
      data-testid="folder-row"
      data-folder={folder.name}
      {...drop.props}
    >
      <Link
        {...(linkProps as { to: "/assets" })}
        className={cn(itemClass, "min-w-0 flex-1 hover:bg-transparent", active && "text-ink")}
      >
        <Folder style={{ color: folderColor(folder.color) }} aria-hidden />
        <span className="truncate">{folder.name}</span>
        <Count value={folder.asset_count} />
      </Link>
      <FolderMenu folder={folder} />
    </div>
  );
}

function NewFolder({ projectId }: { projectId?: string }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) return setEditing(false);
    const color = FOLDER_COLORS[Math.floor(Math.random() * FOLDER_COLORS.length)]!;
    const { error } = await fetchClient.POST("/api/folders", {
      body: { name: name.trim(), color, project_id: projectId ?? null },
    });
    if (error) return toast.error(errorMessage(error));
    setName("");
    setEditing(false);
    await invalidateLibrary(queryClient);
  }
  if (!editing) {
    return (
      <button
        type="button"
        className="rounded p-0.5 text-muted hover:text-ink"
        aria-label={t("shell.newFolder")}
        onClick={() => setEditing(true)}
        data-testid="new-folder"
      >
        <Plus className="size-4" />
      </button>
    );
  }
  return (
    <form onSubmit={submit} className="absolute inset-x-2 z-10 mt-6">
      <input
        autoFocus
        aria-label={t("shell.folderName")}
        placeholder={t("shell.folderName")}
        className={cn(inputClass, "h-8 w-full")}
        value={name}
        onChange={(e) => setName(e.target.value)}
        onBlur={(e) => void submit(e as unknown as FormEvent)}
        onKeyDown={(e) => e.key === "Escape" && setEditing(false)}
        maxLength={80}
      />
    </form>
  );
}

export function LibrarySidebar() {
  const { t } = useTranslation();
  const location = useRouterState({ select: (s) => s.location });
  const projectId = /^\/studio\/([^/]+)/.exec(location.pathname)?.[1];
  const search = location.search as { folder?: string; liked?: boolean; tab?: string };
  const actions = useLibraryActions();
  const { data: summary } = useSummary(projectId);
  const { data: folders = [] } = useFolders(projectId);
  const { data: projects = [] } = useProjects({ sort: "updated" });
  const { data: project } = $api.useQuery(
    "get",
    "/api/projects/{project_id}",
    { params: { path: { project_id: projectId ?? "" } } },
    { enabled: Boolean(projectId) },
  );
  const move = (target: MoveTarget) => (ids: string[]) => void actions.move(ids, target);
  const onAssets = location.pathname === "/assets";

  return (
    <aside
      aria-label={t("shell.library")}
      className="relative flex w-60 shrink-0 flex-col overflow-y-auto border-r border-line bg-flat p-3"
      data-testid="sidebar"
    >
      {projectId ? (
        <>
          <Link to="/studio" className={itemClass}>
            <ArrowLeft aria-hidden />
            {t("shell.backToProjects")}
          </Link>
          <Section title={project?.name ?? ""}>
            <DropLink
              to="/studio/$projectId"
              params={{ projectId }}
              search={{}}
              active={!search.folder && search.tab !== "brief"}
              icon={<FolderOpen aria-hidden />}
              label={t("library.allAssets")}
              count={summary?.total}
              onDropIds={move({ project_id: projectId })}
            />
            <DropLink
              to="/studio/$projectId"
              params={{ projectId }}
              search={{ tab: "brief" }}
              active={search.tab === "brief"}
              icon={<FileText aria-hidden />}
              label={t("shell.brief")}
            />
          </Section>
        </>
      ) : (
        <Section title={t("shell.library")}>
          <DropLink
            to="/assets"
            search={{}}
            active={onAssets && !search.folder && !search.liked}
            icon={<FolderOpen aria-hidden />}
            label={t("library.allAssets")}
            count={summary?.total}
            onDropIds={move({ project_id: null })}
            testId="nav-all-assets"
          />
          <DropLink
            to="/assets"
            search={{ liked: true }}
            active={onAssets && Boolean(search.liked)}
            icon={<Star aria-hidden />}
            label={t("library.favorites")}
            count={summary?.favorites}
          />
          <DropLink
            to="/trash"
            active={location.pathname === "/trash"}
            icon={<Trash2 aria-hidden />}
            label={t("library.trash")}
            count={summary?.trashed}
            testId="nav-trash"
          />
        </Section>
      )}
      <div className="relative">
        <Section
          title={projectId ? t("shell.projectFolders") : t("shell.folders")}
          action={<NewFolder projectId={projectId} />}
        >
          {folders.length === 0 ? <p className="px-2 text-xs text-muted">{t("shell.noFolders")}</p> : null}
          {folders.map((folder) => (
            <FolderRow
              key={folder.id}
              folder={folder}
              projectId={projectId}
              active={search.folder === folder.id}
              onDropIds={move({ folder_id: folder.id })}
            />
          ))}
        </Section>
      </div>
      {projectId ? null : (
        <Section title={t("shell.projects")}>
          {projects.length === 0 ? <p className="px-2 text-xs text-muted">{t("shell.noProjects")}</p> : null}
          {projects.slice(0, 6).map((p) => (
            <DropLink
              key={p.id}
              to="/studio/$projectId"
              params={{ projectId: p.id }}
              search={{}}
              active={false}
              icon={<SquareKanban aria-hidden />}
              label={p.name}
              count={p.asset_count}
              onDropIds={move({ project_id: p.id })}
            />
          ))}
          <Link to="/studio" className={cn(itemClass, "text-xs")}>
            {t("shell.allProjects")}
          </Link>
        </Section>
      )}
    </aside>
  );
}
