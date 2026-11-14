import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { errorMessage, fetchClient } from "@/api/client";
import type { ProjectDetail } from "@/features/library/api";

/** Splits a brief into its "## Heading" sections and joins them back. */
export function parseBrief(markdown: string): { title: string; body: string }[] {
  const sections: { title: string; body: string }[] = [];
  let current: { title: string; lines: string[] } | null = null;
  const preamble: string[] = [];
  for (const line of markdown.split("\n")) {
    const heading = /^## (.+)$/.exec(line);
    if (heading) {
      if (current) sections.push({ title: current.title, body: current.lines.join("\n").trim() });
      current = { title: heading[1]!.trim(), lines: [] };
    } else if (current) current.lines.push(line);
    else preamble.push(line);
  }
  if (current) sections.push({ title: current.title, body: current.lines.join("\n").trim() });
  if (preamble.join("").trim()) sections.unshift({ title: "Overview", body: preamble.join("\n").trim() });
  return sections;
}

export function joinBrief(sections: { title: string; body: string }[]): string {
  return sections.map((s) => `## ${s.title}\n${s.body ? `${s.body}\n` : ""}`).join("\n");
}

export function BriefEditor({ project }: { project: ProjectDetail }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [sections, setSections] = useState(() => parseBrief(project.brief));
  const [state, setState] = useState<"idle" | "saving" | "saved">("idle");
  // Compare with the normalized brief, so opening it never triggers a save.
  const saved = useRef(joinBrief(parseBrief(project.brief)));
  const markdown = useMemo(() => joinBrief(sections), [sections]);

  useEffect(() => {
    if (markdown === saved.current) return;
    setState("saving");
    const timer = setTimeout(async () => {
      const { error } = await fetchClient.PATCH("/api/projects/{project_id}", {
        params: { path: { project_id: project.id } },
        body: { brief: markdown },
      });
      if (error) return toast.error(errorMessage(error));
      saved.current = markdown;
      setState("saved");
      void queryClient.invalidateQueries({ queryKey: ["get", "/api/projects/{project_id}"] });
    }, 700);
    return () => clearTimeout(timer);
  }, [markdown, project.id, queryClient]);

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-5 px-6 py-8" data-testid="brief-editor">
      <div className="flex items-center justify-between">
        <h2 className="font-display text-2xl font-bold uppercase">{t("brief.title")}</h2>
        <span className="font-mono text-xs text-muted" aria-live="polite" data-testid="brief-state">
          {state === "saving" ? t("brief.saving") : state === "saved" ? t("brief.saved") : ""}
        </span>
      </div>
      {sections.map((section, index) => (
        <label key={section.title} className="flex flex-col gap-1.5">
          <span className="font-display text-lg font-semibold uppercase tracking-wide">{section.title}</span>
          <span className="text-xs text-muted">{t(`brief.sections.${section.title}`, { defaultValue: "" })}</span>
          <textarea
            value={section.body}
            onChange={(e) =>
              setSections((all) => all.map((s, i) => (i === index ? { ...s, body: e.target.value } : s)))
            }
            rows={Math.max(3, section.body.split("\n").length + 1)}
            className="resize-y rounded-lg border border-line bg-stage p-3 text-sm outline-none focus:border-tungsten"
          />
        </label>
      ))}
    </div>
  );
}
