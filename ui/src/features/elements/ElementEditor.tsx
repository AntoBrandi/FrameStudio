import { ImagePlus, Play, Plus, RefreshCw, Star, Trash2, Upload, X } from "lucide-react";
import { type FormEvent, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Field, inputClass } from "@/components/ui/misc";
import { LibraryPicker, type Reference } from "@/features/generate/ReferenceTray";
import { uploadFiles } from "@/features/library/upload";
import { cn } from "@/lib/utils";
import {
  CATEGORIES,
  type Category,
  type ElementOut,
  type ElementStatus,
  useElementActions,
  useElementFolders,
} from "./api";

/** The handle a name would get (the backend adds a suffix when it is taken). */
export function slugify(name: string): string {
  return name
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40)
    .replace(/-+$/g, "");
}

export const HANDLE = /^[a-z0-9][a-z0-9_-]{0,47}$/;
const MAX_MEDIA = 50;

interface Media {
  id: string;
  thumb: string | null;
  kind: string;
}

interface Draft {
  name: string;
  handle: string;
  category: Category;
  status: ElementStatus;
  description: string;
  folderId: string;
  properties: [string, string][];
  media: Media[];
}

function draftOf(element: ElementOut | undefined): Draft {
  return {
    name: element?.name ?? "",
    handle: element?.handle ?? "",
    category: element?.category ?? "character",
    status: element?.status ?? "draft",
    description: element?.description ?? "",
    folderId: element?.folder_id ?? "",
    properties: Object.entries(element?.properties ?? {}),
    media: (element?.media ?? []).map((m) => ({ id: m.asset_id, thumb: m.thumb_url ?? null, kind: m.kind })),
  };
}

const sameList = (a: string[], b: string[]) => a.length === b.length && a.every((v, i) => v === b[i]);

/** Create or edit an element: its name and handle, kind, status, folder,
 * description, custom properties and pictures (the first is the primary). */
export function ElementEditor({
  open,
  onOpenChange,
  element,
  projectId,
  defaultFolderId,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Absent when creating. */
  element?: ElementOut;
  /** Where a new element goes (undefined: global). */
  projectId?: string;
  defaultFolderId?: string;
  onSaved?: (element: ElementOut) => void;
}) {
  const { t } = useTranslation();
  const actions = useElementActions();
  const [draft, setDraft] = useState<Draft>(() => ({
    ...draftOf(element),
    folderId: element?.folder_id ?? defaultFolderId ?? "",
  }));
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [picking, setPicking] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const descriptionId = useId();
  const creating = !element;
  const update = (patch: Partial<Draft>) => setDraft((prev) => ({ ...prev, ...patch }));
  const handleOk = draft.handle === "" ? creating : HANDLE.test(draft.handle);
  const canSave = draft.name.trim() !== "" && handleOk && !busy;
  const targetProject = element ? (element.project_id ?? undefined) : projectId;
  const { data: folders = [] } = useElementFolders(targetProject);

  async function upload(files: File[]) {
    if (files.length === 0) return;
    setBusy(true);
    const results = await uploadFiles(files, { projectId: targetProject });
    setBusy(false);
    const added = results.filter((r) => r.asset).map((r) => r.asset!);
    for (const failed of results.filter((r) => r.error)) toast.error(`${failed.name}: ${failed.error}`);
    addMedia(added.map((a) => ({ id: a.id, thumb: a.thumb_url ?? a.url, kind: a.kind })));
  }

  function addMedia(items: Media[]) {
    setDraft((prev) => {
      const have = new Set(prev.media.map((m) => m.id));
      const fresh = items.filter((m) => !have.has(m.id));
      return { ...prev, media: [...prev.media, ...fresh].slice(0, MAX_MEDIA) };
    });
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!canSave) return;
    setBusy(true);
    const properties = Object.fromEntries(
      draft.properties.filter(([key]) => key.trim() !== "").map(([key, value]) => [key.trim(), value]),
    );
    const common = {
      name: draft.name.trim(),
      category: draft.category,
      status: draft.status,
      description: draft.description,
      properties,
      folder_id: draft.folderId || null,
    };
    let saved: ElementOut | undefined;
    if (creating) {
      saved = await actions.create({
        ...common,
        handle: draft.handle || null,
        project_id: projectId ?? null,
        asset_ids: draft.media.map((m) => m.id),
      });
    } else {
      saved = await actions.patch(element.id, {
        ...common,
        handle: draft.handle !== element.handle ? draft.handle : null,
      });
      const ids = draft.media.map((m) => m.id);
      if (
        saved &&
        !sameList(
          ids,
          saved.media.map((m) => m.asset_id),
        )
      ) {
        saved = (await actions.setMedia(element.id, ids)) ?? saved;
      }
    }
    setBusy(false);
    if (!saved) return;
    toast.success(t(creating ? "elements.created" : "elements.saved", { handle: saved.handle }));
    onSaved?.(saved);
    onOpenChange(false);
  }

  async function regenerate() {
    if (creating) return update({ handle: "" });
    setBusy(true);
    const saved = await actions.patch(element.id, { name: draft.name.trim() || null, regenerate_handle: true });
    setBusy(false);
    if (saved) update({ handle: saved.handle });
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-[min(94vw,46rem)]" closeLabel={t("elements.close")} aria-describedby={descriptionId}>
        <form onSubmit={(e) => void save(e)} className="flex flex-col gap-4" data-testid="element-editor">
          <div className="flex flex-col gap-1 pr-8">
            <DialogTitle>{creating ? t("elements.new") : t("elements.edit", { name: element.name })}</DialogTitle>
            <DialogDescription id={descriptionId}>
              {element
                ? t("elements.meta", { version: element.version, count: element.used_in })
                : t(projectId ? "elements.newInProject" : "elements.newGlobal")}
            </DialogDescription>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={t("elements.name")}>
              <input
                className={inputClass}
                value={draft.name}
                maxLength={120}
                autoFocus
                onChange={(e) => update({ name: e.target.value })}
                data-testid="element-name"
              />
            </Field>
            <Field
              label={t("elements.handle")}
              hint={!handleOk ? t("elements.handleRule") : creating && !draft.handle ? t("elements.handleAuto") : null}
            >
              <div className="flex gap-1.5">
                <div className={cn(inputClass, "flex flex-1 items-center gap-0.5 px-2", !handleOk && "border-danger")}>
                  <span className="text-muted">@</span>
                  <input
                    className="min-w-0 flex-1 bg-transparent outline-none"
                    value={draft.handle}
                    placeholder={creating ? slugify(draft.name) || t("elements.handlePlaceholder") : undefined}
                    maxLength={48}
                    onChange={(e) => update({ handle: e.target.value.toLowerCase() })}
                    aria-invalid={!handleOk}
                    data-testid="element-handle"
                  />
                </div>
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  onClick={() => void regenerate()}
                  aria-label={t("elements.regenerate")}
                  title={t("elements.regenerate")}
                  disabled={busy}
                  data-testid="element-regenerate"
                >
                  <RefreshCw />
                </Button>
              </div>
            </Field>
          </div>

          <div className="flex flex-wrap items-end gap-4">
            <div className="flex flex-col gap-1.5 text-sm">
              <span className="font-medium">{t("elements.category")}</span>
              <div role="radiogroup" aria-label={t("elements.category")} className="flex flex-wrap gap-1">
                {CATEGORIES.map((category) => (
                  <button
                    key={category}
                    type="button"
                    role="radio"
                    aria-checked={draft.category === category}
                    onClick={() => update({ category })}
                    className={cn(
                      "h-9 rounded-full border px-3 text-xs",
                      draft.category === category
                        ? "border-tungsten bg-tungsten/15 text-ink"
                        : "border-line text-muted hover:text-ink",
                    )}
                  >
                    {t(`elements.categories.${category}`)}
                  </button>
                ))}
              </div>
            </div>
            <div className="flex flex-col gap-1.5 text-sm">
              <span className="font-medium">{t("elements.status")}</span>
              <div role="radiogroup" aria-label={t("elements.status")} className="flex gap-1">
                {(["draft", "ready"] as const).map((status) => (
                  <button
                    key={status}
                    type="button"
                    role="radio"
                    aria-checked={draft.status === status}
                    onClick={() => update({ status })}
                    className={cn(
                      "h-9 rounded-full border px-3 text-xs",
                      draft.status === status ? "border-ink/60 bg-raised text-ink" : "border-line text-muted",
                    )}
                  >
                    {t(`elements.statuses.${status}`)}
                  </button>
                ))}
              </div>
            </div>
            <label className="flex flex-col gap-1.5 text-sm">
              <span className="font-medium">{t("elements.folder")}</span>
              <select
                className={cn(inputClass, "pr-8")}
                value={draft.folderId}
                onChange={(e) => update({ folderId: e.target.value })}
                data-testid="element-folder"
              >
                <option value="">{t("elements.noFolder")}</option>
                {folders.map((folder) => (
                  <option key={folder.id} value={folder.id}>
                    {folder.name}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <Field label={t("elements.description")} hint={t("elements.descriptionHint")}>
            <textarea
              value={draft.description}
              maxLength={10_000}
              onChange={(e) => update({ description: e.target.value })}
              className="min-h-24 resize-y rounded-lg border border-line bg-stage p-3 text-sm outline-none focus:border-tungsten"
              data-testid="element-description"
            />
          </Field>

          <section className="flex flex-col gap-2" aria-label={t("elements.media")}>
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="flex-1 text-sm font-medium">{t("elements.media")}</h3>
              <Button type="button" size="sm" variant="outline" onClick={() => setPicking(true)}>
                <ImagePlus /> {t("elements.fromLibrary")}
              </Button>
              <Button type="button" size="sm" variant="outline" onClick={() => fileInput.current?.click()}>
                <Upload /> {t("elements.upload")}
              </Button>
              <input
                ref={fileInput}
                type="file"
                accept="image/*,video/*"
                multiple
                hidden
                data-testid="element-upload"
                onChange={(e) => {
                  void upload(Array.from(e.target.files ?? []));
                  e.target.value = "";
                }}
              />
            </div>
            {draft.media.length === 0 ? (
              <p className="rounded-lg border border-dashed border-line px-3 py-6 text-center text-sm text-muted">
                {t("elements.noMedia")}
              </p>
            ) : (
              <ul className="grid grid-cols-3 gap-2 sm:grid-cols-5" data-testid="element-media">
                {draft.media.map((item, index) => (
                  <li key={item.id} className="group relative aspect-square overflow-hidden rounded-lg bg-raised">
                    {item.thumb ? <img src={item.thumb} alt="" className="size-full object-cover" /> : null}
                    {item.kind === "video" ? (
                      <Play className="absolute bottom-1.5 right-1.5 size-4 text-white drop-shadow" aria-hidden />
                    ) : null}
                    {index === 0 ? (
                      <span className="absolute left-1.5 top-1.5 rounded bg-tungsten px-1.5 py-px text-[10px] font-semibold uppercase text-tungsten-ink">
                        {t("elements.primary")}
                      </span>
                    ) : null}
                    <div className="absolute right-1 top-1 flex gap-1 opacity-100 sm:opacity-0 sm:group-focus-within:opacity-100 sm:group-hover:opacity-100">
                      {index > 0 ? (
                        <button
                          type="button"
                          className="rounded bg-black/60 p-1 text-white"
                          aria-label={t("elements.makePrimary")}
                          title={t("elements.makePrimary")}
                          onClick={() => update({ media: [item, ...draft.media.filter((m) => m.id !== item.id)] })}
                        >
                          <Star className="size-3.5" />
                        </button>
                      ) : null}
                      <button
                        type="button"
                        className="rounded bg-black/60 p-1 text-white"
                        aria-label={t("elements.removeMedia")}
                        title={t("elements.removeMedia")}
                        onClick={() => update({ media: draft.media.filter((m) => m.id !== item.id) })}
                      >
                        <X className="size-3.5" />
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="flex flex-col gap-2" aria-label={t("elements.properties")}>
            <div className="flex items-center gap-2">
              <h3 className="flex-1 text-sm font-medium">{t("elements.properties")}</h3>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => update({ properties: [...draft.properties, ["", ""]] })}
                data-testid="element-add-property"
              >
                <Plus /> {t("elements.addProperty")}
              </Button>
            </div>
            {draft.properties.map(([key, value], index) => (
              <div key={index} className="flex gap-2">
                <input
                  className={cn(inputClass, "w-1/3 min-w-0")}
                  value={key}
                  maxLength={80}
                  placeholder={t("elements.propertyName")}
                  aria-label={t("elements.propertyName")}
                  onChange={(e) =>
                    update({
                      properties: draft.properties.map((p, i) => (i === index ? [e.target.value, p[1]] : p)),
                    })
                  }
                />
                <input
                  className={cn(inputClass, "min-w-0 flex-1")}
                  value={value}
                  maxLength={2000}
                  placeholder={t("elements.propertyValue")}
                  aria-label={t("elements.propertyValue")}
                  onChange={(e) =>
                    update({
                      properties: draft.properties.map((p, i) => (i === index ? [p[0], e.target.value] : p)),
                    })
                  }
                />
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  aria-label={t("elements.removeProperty")}
                  onClick={() => update({ properties: draft.properties.filter((_, i) => i !== index) })}
                >
                  <X />
                </Button>
              </div>
            ))}
          </section>

          <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line pt-4">
            {element ? (
              confirmDelete ? (
                <div
                  role="alertdialog"
                  aria-label={t("elements.confirmDelete")}
                  className="mr-auto flex items-center gap-2"
                >
                  <span className="text-sm">{t("elements.confirmDelete")}</span>
                  <Button
                    type="button"
                    size="sm"
                    variant="danger"
                    onClick={() => {
                      void actions.remove(element);
                      onOpenChange(false);
                    }}
                    data-testid="element-delete-confirm"
                  >
                    {t("elements.delete")}
                  </Button>
                  <Button type="button" size="sm" variant="ghost" onClick={() => setConfirmDelete(false)}>
                    {t("elements.cancel")}
                  </Button>
                </div>
              ) : (
                <Button
                  type="button"
                  variant="ghost"
                  className="mr-auto text-danger"
                  onClick={() => setConfirmDelete(true)}
                  data-testid="element-delete"
                >
                  <Trash2 /> {t("elements.delete")}
                </Button>
              )
            ) : null}
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              {t("elements.cancel")}
            </Button>
            <Button type="submit" variant="primary" disabled={!canSave} data-testid="element-save">
              {creating ? t("elements.create") : t("elements.save")}
            </Button>
          </div>
        </form>
        <LibraryPicker
          open={picking}
          onOpenChange={setPicking}
          chosen={draft.media.map((m) => ({ id: m.id, thumb: m.thumb, name: m.id }))}
          room={MAX_MEDIA - draft.media.length}
          kinds={["image", "video"]}
          labels={{
            title: t("elements.pickTitle"),
            hint: t("elements.pickHint"),
            none: t("elements.pickNone"),
            add: (count) => t("elements.pickAdd", { count }),
          }}
          onAdd={(picked: Reference[]) => addMedia(picked.map((r) => ({ id: r.id, thumb: r.thumb, kind: "image" })))}
        />
      </DialogContent>
    </Dialog>
  );
}
