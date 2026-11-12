import type { QueryClient, QueryKey } from "@tanstack/react-query";
import type { AppSettings, GenerationOut, JobOut } from "./client";

/** Wire format of one Server-Sent Event's ``data`` field. */
export interface EventEnvelope<T> {
  type: string;
  data: T;
  at: string;
}

export interface JobProgress {
  id: string;
  progress: number;
  message: string | null;
}

export const JOBS_KEY = ["get", "/api/jobs"] as const;
export const GENERATIONS_KEY = ["get", "/api/generations"] as const;
export const ASSETS_KEY = ["get", "/api/assets"] as const;
export const SETTINGS_KEY = ["get", "/api/settings"] as const;

const ACTIVE = new Set(["queued", "submitting", "running"]);

export function isActive(state: string): boolean {
  return ACTIVE.has(state);
}

export function upsertById<T extends { id: string }>(list: T[] | undefined, item: T, insert = true): T[] | undefined {
  if (!list) return list;
  const index = list.findIndex((entry) => entry.id === item.id);
  if (index === -1) return insert ? [item, ...list] : list;
  const next = list.slice();
  next[index] = item;
  return next;
}

export function applyProgress(list: JobOut[] | undefined, update: JobProgress): JobOut[] | undefined {
  if (!list) return list;
  return list.map((job) =>
    job.id === update.id && job.state === "running"
      ? { ...job, progress: update.progress, message: update.message }
      : job,
  );
}

/** The ``mode`` filter a generations query was made with, if any. */
function modeFilter(queryKey: QueryKey): string | undefined {
  const init = queryKey[2] as { params?: { query?: { mode?: string } } } | undefined;
  return init?.params?.query?.mode;
}

export interface EventHandlers {
  onJobFinished?: (job: JobOut) => void;
  onAuthLost?: () => void;
}

/** Feed the event stream into the query cache. Returns a disconnect function. */
export function connectEvents(queryClient: QueryClient, handlers: EventHandlers = {}): () => void {
  const source = new EventSource("/api/events");
  const parse = <T>(event: MessageEvent<string>): T => (JSON.parse(event.data) as EventEnvelope<T>).data;

  source.addEventListener("job.updated", (event) => {
    const job = parse<JobOut>(event as MessageEvent<string>);
    const lists = queryClient.getQueriesData<JobOut[]>({ queryKey: JOBS_KEY });
    const previous = lists.flatMap(([, list]) => list ?? []).find((entry) => entry.id === job.id);
    queryClient.setQueriesData<JobOut[]>({ queryKey: JOBS_KEY }, (list) => upsertById(list, job));
    if (previous && isActive(previous.state) && !isActive(job.state)) handlers.onJobFinished?.(job);
  });

  source.addEventListener("job.progress", (event) => {
    const update = parse<JobProgress>(event as MessageEvent<string>);
    queryClient.setQueriesData<JobOut[]>({ queryKey: JOBS_KEY }, (list) => applyProgress(list, update));
  });

  source.addEventListener("generation.updated", (event) => {
    const generation = parse<GenerationOut>(event as MessageEvent<string>);
    for (const query of queryClient.getQueryCache().findAll({ queryKey: GENERATIONS_KEY })) {
      const mode = modeFilter(query.queryKey);
      const matches = mode === undefined || mode === generation.mode;
      queryClient.setQueryData<GenerationOut[]>(query.queryKey, (list) => upsertById(list, generation, matches));
    }
  });

  source.addEventListener("asset.created", () => {
    void queryClient.invalidateQueries({ queryKey: ASSETS_KEY });
  });

  source.addEventListener("settings.updated", (event) => {
    queryClient.setQueryData<AppSettings>(SETTINGS_KEY, parse<AppSettings>(event as MessageEvent<string>));
  });

  source.addEventListener("resync", () => {
    void queryClient.invalidateQueries();
  });

  let checking = false;
  source.onerror = () => {
    // EventSource reconnects on its own; only find out whether we were signed out.
    if (checking) return;
    checking = true;
    fetch("/api/system/info", { credentials: "same-origin" })
      .then((response) => {
        if (response.status === 401) {
          source.close();
          handlers.onAuthLost?.();
        }
      })
      .catch(() => undefined)
      .finally(() => {
        checking = false;
      });
  };

  return () => source.close();
}
