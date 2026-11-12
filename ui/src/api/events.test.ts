import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { FakeEventSource } from "@/test/setup";
import type { GenerationOut, JobOut } from "./client";
import { applyProgress, connectEvents, GENERATIONS_KEY, JOBS_KEY, upsertById } from "./events";

const job = (overrides: Partial<JobOut> = {}): JobOut => ({
  id: "j1",
  kind: "fake.sleep",
  lane: "gpu",
  state: "running",
  title: "Fake",
  progress: 0,
  message: null,
  error_code: null,
  error_message: null,
  attempts: 1,
  remote: false,
  result: null,
  created_at: "2026-11-12T10:00:00Z",
  started_at: null,
  finished_at: null,
  ...overrides,
});

const generation = (overrides: Partial<GenerationOut> = {}): GenerationOut => ({
  id: "g1",
  batch_id: "b",
  batch_index: 0,
  mode: "image",
  model_id: "mock",
  provider: "mock",
  prompt: "p",
  params: {},
  seed: 1,
  status: "queued",
  error_code: null,
  error_message: null,
  job_id: "j1",
  parent_asset_id: null,
  project_id: null,
  folder_id: null,
  asset: null,
  created_at: "2026-11-12T10:00:00Z",
  started_at: null,
  finished_at: null,
  ...overrides,
});

describe("cache helpers", () => {
  it("upserts by id, prepending new items", () => {
    expect(upsertById([job()], job({ id: "j2" }))?.map((j) => j.id)).toEqual(["j2", "j1"]);
    expect(upsertById([job()], job({ state: "succeeded" }))?.[0]?.state).toBe("succeeded");
    expect(upsertById(undefined, job())).toBeUndefined();
    expect(upsertById([job()], job({ id: "j2" }), false)).toHaveLength(1);
  });

  it("applies progress only to running jobs", () => {
    const list = [job(), job({ id: "j2", state: "queued" })];
    const next = applyProgress(list, { id: "j1", progress: 0.5, message: "half" });
    expect(next?.[0]?.progress).toBe(0.5);
    expect(applyProgress(list, { id: "j2", progress: 0.5, message: null })?.[1]?.progress).toBe(0);
  });
});

describe("event stream", () => {
  it("feeds jobs and generations into the cache and reports finished jobs", () => {
    const client = new QueryClient();
    client.setQueryData([...JOBS_KEY, { params: { query: { limit: 30 } } }], [job()]);
    client.setQueryData([...GENERATIONS_KEY, { params: { query: { mode: "image" } } }], []);
    client.setQueryData([...GENERATIONS_KEY, { params: { query: { mode: "video" } } }], []);
    const onJobFinished = vi.fn();
    const disconnect = connectEvents(client, { onJobFinished });
    const source = FakeEventSource.instances[0]!;
    expect(source.url).toBe("/api/events");

    source.emit("job.progress", { id: "j1", progress: 0.4, message: "x" });
    expect(client.getQueriesData<JobOut[]>({ queryKey: JOBS_KEY })[0]?.[1]?.[0]?.progress).toBe(0.4);

    source.emit("job.updated", job({ state: "succeeded", progress: 1 }));
    expect(onJobFinished).toHaveBeenCalledOnce();

    source.emit("generation.updated", generation());
    const lists = client.getQueriesData<GenerationOut[]>({ queryKey: GENERATIONS_KEY });
    const byMode = Object.fromEntries(
      lists.map(([key, data]) => [(key[2] as { params: { query: { mode: string } } }).params.query.mode, data]),
    );
    expect(byMode.image).toHaveLength(1);
    expect(byMode.video).toHaveLength(0);

    disconnect();
    expect(source.closed).toBe(true);
  });
});
