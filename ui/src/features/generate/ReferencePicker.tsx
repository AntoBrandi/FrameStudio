import { Check, Images, Plus, Search } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { $api, type AssetOut } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { inputClass } from "@/components/ui/misc";
import { CATEGORIES, type Category, type ElementOut } from "@/features/elements/api";
import { ElementEditor } from "@/features/elements/ElementEditor";
import { assetName } from "@/features/library/api";
import { cn } from "@/lib/utils";

/** A picture picked from the library. */
export interface Reference {
  id: string;
  thumb: string | null;
  name: string;
}

export function toReference(asset: AssetOut): Reference {
  return { id: asset.id, thumb: asset.thumb_url ?? asset.url, name: assetName(asset, asset.id) };
}

type Tab = "recent" | "uploads" | "generations" | "liked" | "elements" | "presets";
const ASSET_TABS: Tab[] = ["recent", "uploads", "generations", "liked"];

export interface PickerElements {
  /** Elements already named in the prompt (they cannot be picked twice). */
  inPrompt: string[];
  /** Where a new element goes (undefined: global). */
  projectId?: string;
}

/** The reference picker (plan step M4.5): pictures from recent work,
 * uploads, generations or liked items, and elements, in one dialog. A
 * counter shows how many pictures the model will get against its limit, and
 * stops the selection there. Without `elements` it picks pictures only (start
 * frames, an element's own pictures). */
export function ReferencePicker({
  open,
  onOpenChange,
  taken,
  room,
  limit,
  kinds,
  elements,
  initialTab = "recent",
  labels,
  onAdd,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Pictures already chosen (shown as such, not offered again). */
  taken: string[];
  /** How many more pictures fit. */
  room: number;
  /** The model's limit, for the counter (omit to hide it). */
  limit?: number;
  kinds: string[];
  elements?: PickerElements;
  initialTab?: Tab;
  labels?: { title: string; hint: string };
  onAdd: (references: Reference[], elements: ElementOut[]) => void;
}) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<Tab>(initialTab);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<Category | undefined>(undefined);
  const [pickedRefs, setPickedRefs] = useState<Reference[]>([]);
  const [pickedElements, setPickedElements] = useState<ElementOut[]>([]);
  const [creating, setCreating] = useState(false);

  // Each element with pictures sends at least its primary, unless the model
  // takes no pictures at all: then elements go in words and do not count.
  const wordsOnly = limit === 0;
  const counts = (element: ElementOut) => !wordsOnly && element.media.length > 0;
  const used = pickedRefs.length + pickedElements.filter(counts).length;
  const full = used >= room;
  const tabs: Tab[] = elements ? [...ASSET_TABS, "elements", "presets"] : ASSET_TABS;

  const assetQuery = {
    kind: kinds as ("image" | "video" | "audio")[],
    limit: 60,
    ...(tab === "uploads" ? { provenance: "uploaded" as const } : {}),
    ...(tab === "generations" ? { provenance: "generated" as const } : {}),
    ...(tab === "liked" ? { liked: true } : {}),
    ...(query.trim() ? { q: query.trim() } : {}),
  };
  const { data: assets, isPending: assetsPending } = $api.useQuery(
    "get",
    "/api/assets",
    { params: { query: assetQuery } },
    { enabled: open && ASSET_TABS.includes(tab) },
  );
  const { data: found = [], isPending: elementsPending } = $api.useQuery(
    "get",
    "/api/elements",
    {
      params: {
        query: {
          ...(elements?.projectId ? { project_id: elements.projectId } : {}),
          ...(category ? { category } : {}),
          ...(query.trim() ? { q: query.trim() } : {}),
        },
      },
    },
    { enabled: open && tab === "elements" },
  );

  function close(next: boolean) {
    if (!next) {
      setPickedRefs([]);
      setPickedElements([]);
      setQuery("");
    }
    onOpenChange(next);
  }

  function toggleAsset(asset: AssetOut) {
    const on = pickedRefs.some((r) => r.id === asset.id);
    if (on) setPickedRefs(pickedRefs.filter((r) => r.id !== asset.id));
    else if (!full) setPickedRefs([...pickedRefs, toReference(asset)]);
  }

  function toggleElement(element: ElementOut) {
    const on = pickedElements.some((e) => e.id === element.id);
    if (on) setPickedElements(pickedElements.filter((e) => e.id !== element.id));
    else if (!full || !counts(element)) setPickedElements([...pickedElements, element]);
  }

  const count = pickedRefs.length + pickedElements.length;
  const items = (assets?.items ?? []).filter((asset) => !taken.includes(asset.id));

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="flex w-[min(94vw,52rem)] max-w-none flex-col gap-3" closeLabel={t("refPicker.close")}>
        <div className="flex flex-col gap-1 pr-8">
          <DialogTitle>{labels?.title ?? t("refPicker.title")}</DialogTitle>
          <DialogDescription>{labels?.hint ?? t("refPicker.hint")}</DialogDescription>
        </div>
        <div role="tablist" aria-label={t("refPicker.sources")} className="flex flex-wrap gap-1 border-b border-line">
          {tabs.map((key) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={tab === key}
              onClick={() => setTab(key)}
              className={cn(
                "-mb-px border-b-2 px-3 py-1.5 text-sm",
                tab === key ? "border-tungsten text-ink" : "border-transparent text-muted hover:text-ink",
              )}
              data-testid={`picker-tab-${key}`}
            >
              {t(`refPicker.tabs.${key}`)}
            </button>
          ))}
        </div>

        {tab !== "presets" ? (
          <div className="flex flex-wrap items-center gap-2">
            <label className={cn(inputClass, "flex min-w-48 flex-1 items-center gap-2")}>
              <Search className="size-4 shrink-0 text-muted" aria-hidden />
              <input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={tab === "elements" ? t("refPicker.searchElements") : t("refPicker.searchAssets")}
                aria-label={tab === "elements" ? t("refPicker.searchElements") : t("refPicker.searchAssets")}
                className="min-w-0 flex-1 bg-transparent outline-none"
                data-testid="picker-search"
              />
            </label>
            {tab === "elements" ? (
              <>
                <div role="group" aria-label={t("elements.category")} className="flex flex-wrap gap-1">
                  {([undefined, ...CATEGORIES.filter((c) => c !== "auto")] as const).map((c) => (
                    <button
                      key={c ?? "all"}
                      type="button"
                      aria-pressed={category === c}
                      onClick={() => setCategory(c)}
                      className={cn(
                        "h-7 rounded-full border px-2.5 text-xs",
                        category === c ? "border-ink/60 bg-raised text-ink" : "border-line text-muted hover:text-ink",
                      )}
                    >
                      {c ? t(`elements.tabs.${c}`) : t("elements.tabs.all")}
                    </button>
                  ))}
                </div>
                <Button size="sm" variant="outline" onClick={() => setCreating(true)} data-testid="picker-new-element">
                  <Plus /> {t("elements.new")}
                </Button>
              </>
            ) : null}
          </div>
        ) : null}

        <div className="max-h-[52vh] min-h-48 overflow-y-auto">
          {tab === "presets" ? (
            <div className="flex flex-col items-center gap-2 py-12 text-center text-sm text-muted">
              <Images className="size-8" aria-hidden />
              <p className="max-w-sm">{t("refPicker.presetsLater")}</p>
            </div>
          ) : tab === "elements" ? (
            !elementsPending && found.length === 0 ? (
              <p className="py-12 text-center text-sm text-muted">
                {query || category ? t("refPicker.noElementsMatch") : t("refPicker.noElements")}
              </p>
            ) : (
              <ul className="grid grid-cols-3 gap-2 sm:grid-cols-5" data-testid="picker-elements">
                {found.map((element) => {
                  const named = elements?.inPrompt.includes(element.id) ?? false;
                  const on = pickedElements.some((e) => e.id === element.id);
                  const blocked = !on && !named && full && counts(element);
                  return (
                    <li key={element.id}>
                      <button
                        type="button"
                        aria-pressed={on || named}
                        aria-label={`@${element.handle}`}
                        disabled={named || blocked}
                        onClick={() => toggleElement(element)}
                        className={cn(
                          "flex w-full flex-col overflow-hidden rounded-lg border-2 bg-flat text-left disabled:opacity-45",
                          on ? "border-tungsten" : "border-transparent hover:border-line",
                        )}
                        data-handle={element.handle}
                      >
                        <span className="relative block aspect-square w-full bg-raised">
                          {element.cover_url ? (
                            <img src={element.cover_url} alt="" className="size-full object-cover" />
                          ) : null}
                          {on || named ? (
                            <Check className="absolute right-1 top-1 size-4 rounded-full bg-tungsten p-0.5 text-tungsten-ink" />
                          ) : null}
                        </span>
                        <span className="truncate px-1.5 pt-1 text-xs">{element.name}</span>
                        <span className="truncate px-1.5 pb-1 font-mono text-[10px] text-tungsten">
                          {named ? t("refPicker.inPrompt") : `@${element.handle}`}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )
          ) : !assetsPending && items.length === 0 ? (
            <p className="py-12 text-center text-sm text-muted">
              {query ? t("refPicker.noMatch") : t(`refPicker.empty.${tab}`)}
            </p>
          ) : (
            <div className="grid grid-cols-4 gap-2 sm:grid-cols-6" data-testid="reference-picker">
              {items.map((asset) => {
                const on = pickedRefs.some((p) => p.id === asset.id);
                return (
                  <button
                    key={asset.id}
                    type="button"
                    aria-pressed={on}
                    disabled={!on && full}
                    onClick={() => toggleAsset(asset)}
                    className={cn(
                      "relative aspect-square overflow-hidden rounded-lg border-2 bg-raised disabled:opacity-40",
                      on ? "border-tungsten" : "border-transparent hover:border-line",
                    )}
                    aria-label={assetName(asset, asset.id)}
                  >
                    {asset.thumb_url ? <img src={asset.thumb_url} alt="" className="size-full object-cover" /> : null}
                  </button>
                );
              })}
            </div>
          )}
        </div>

        <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line pt-3">
          <p
            className={cn("mr-auto text-xs", full ? "text-tungsten" : "text-muted")}
            data-testid="picker-counter"
            aria-live="polite"
          >
            {limit != null
              ? t("refPicker.counter", { used: limit - room + used, limit })
              : t("refPicker.selected", { count })}
            {full && room > 0 ? ` · ${t("refPicker.full")}` : null}
            {wordsOnly && elements ? ` · ${t("refPicker.wordsOnly")}` : null}
          </p>
          <Button variant="ghost" onClick={() => close(false)}>
            {t("references.cancel")}
          </Button>
          <Button
            variant="primary"
            disabled={count === 0}
            onClick={() => {
              onAdd(pickedRefs, pickedElements);
              close(false);
            }}
            data-testid="add-references"
          >
            {t("refPicker.add", { count })}
          </Button>
        </div>
        {creating ? (
          <ElementEditor
            open
            onOpenChange={setCreating}
            projectId={elements?.projectId}
            onSaved={(element) => {
              setTab("elements");
              if (!full || !counts(element)) setPickedElements((prev) => [...prev, element]);
            }}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
