import type { InvalidateQueryFilters, QueryClient, QueryKey } from "@tanstack/react-query";
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

/** The filters a generations query was made with. */
function queryFilters(queryKey: QueryKey): { mode?: string; hide_failed?: boolean } {
  const init = queryKey[2] as { params?: { query?: { mode?: string; hide_failed?: boolean } } } | undefined;
  return init?.params?.query ?? {};
}

/**
 * Refetch queries so the result reflects every change made so far. A plain
 * invalidation joins a first fetch that is still in flight (and may have read
 * the database before the change); cancelling first makes it start over.
 */
export async function refresh(queryClient: QueryClient, filters?: InvalidateQueryFilters): Promise<void> {
  await queryClient.cancelQueries(filters);
  await queryClient.invalidateQueries(filters);
}

const LIBRARY_PATHS = [
  "/api/assets",
  "/api/folders",
  "/api/projects",
  "/api/generations",
  "/api/storage",
  "/api/elements",
];

/** Everything the library shows: lists, counts, summaries, feeds. */
export const libraryFilter: InvalidateQueryFilters = {
  predicate: (query) => {
    const path = query.queryKey[1];
    return typeof path === "string" && LIBRARY_PATHS.some((prefix) => path.startsWith(prefix));
  },
};

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
    const failed = ["failed", "cancelled", "interrupted"].includes(generation.status);
    for (const query of queryClient.getQueryCache().findAll({ queryKey: GENERATIONS_KEY })) {
      // A fetch in flight (say, started by the asset event a moment earlier)
      // may have read the database before this change and would overwrite it.
      const inFlight = query.state.fetchStatus === "fetching";
      const filters = queryFilters(query.queryKey);
      if (filters.hide_failed && failed) {
        queryClient.setQueryData<GenerationOut[]>(query.queryKey, (list) =>
          list?.filter((entry) => entry.id !== generation.id),
        );
      } else {
        const matches = filters.mode === undefined || filters.mode === generation.mode;
        queryClient.setQueryData<GenerationOut[]>(query.queryKey, (list) => upsertById(list, generation, matches));
      }
      // Data set during a fetch is what a cancel restores, so this keeps the
      // update on screen while the fetch starts over.
      if (inFlight) void refresh(queryClient, { queryKey: query.queryKey, exact: true });
    }
  });

  // Library changes (here or in another window) refresh lists, counts and summaries.
  const refreshLibrary = () => void refresh(queryClient, libraryFilter);
  source.addEventListener("asset.created", refreshLibrary);
  source.addEventListener("library.changed", refreshLibrary);

  // Connections and spending (another window, or a job that just paid).
  const refreshConnections = () => {
    void refresh(queryClient, { queryKey: ["get", "/api/connections"] });
    void refresh(queryClient, { queryKey: ["get", "/api/usage"] });
    void refresh(queryClient, { queryKey: ["get", "/api/llm"] });
  };
  source.addEventListener("connections.changed", refreshConnections);
  source.addEventListener("catalog.changed", () => void refresh(queryClient, { queryKey: ["get", "/api/models"] }));
  source.addEventListener("usage.recorded", refreshConnections);

  source.addEventListener("settings.updated", (event) => {
    queryClient.setQueryData<AppSettings>(SETTINGS_KEY, parse<AppSettings>(event as MessageEvent<string>));
    void refresh(queryClient, { queryKey: ["get", "/api/llm"] }); // model choices live in settings
  });

  source.addEventListener("resync", () => {
    void queryClient.invalidateQueries();
  });

  // Whatever happened before the stream (re)connected was not delivered:
  // refetch what is on screen once the subscription exists. Fetches still in
  // flight may have read the database before those changes (an invalidation
  // would only join them), so cancel them first and start over.
  source.onopen = () => void refresh(queryClient);

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
