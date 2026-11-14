import { QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from "@tanstack/react-router";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import type { GenerationOut } from "@/api/client";
import { listsGeneration } from "@/api/events";
import { makeQueryClient } from "@/app/App";
import { useComposerStore } from "@/features/generate/store";
import { mockApi, requests } from "@/test/api-mock";
import { model } from "@/test/models";
import { inProgress } from "./InProgress";
import { projectNameFrom } from "./projects";
import { StudioHome } from "./StudioHome";

// M5.6: the Studio home and workspace.

const MOCK = model("mock", ["image", "video"], {}, [], { name: "Mock engine", runtime: "local" });

function generation(patch: Partial<GenerationOut>): GenerationOut {
  return {
    id: "g1",
    batch_id: "b",
    batch_index: 0,
    mode: "image",
    model_id: "mock",
    provider: "mock",
    prompt: "a door",
    prompt_tokens: [],
    resolved: null,
    params: {},
    references: [],
    start_frame: null,
    end_frame: null,
    seed: 1,
    status: "queued",
    error_code: null,
    error_message: null,
    job_id: null,
    parent_asset_id: null,
    project_id: null,
    folder_id: null,
    asset: null,
    created_at: new Date().toISOString(),
    started_at: null,
    finished_at: null,
    ...patch,
  } as GenerationOut;
}

const PROJECT = {
  id: "p0",
  name: "Night Diner",
  slug: "night-diner",
  description: "",
  cover_asset_id: null,
  cover_url: null,
  asset_count: 3,
  describe_images: false,
  created_at: "2026-11-14T10:00:00Z",
  updated_at: "2026-11-14T10:00:00Z",
  deleted_at: null,
};

function Harness() {
  const [client] = useState(makeQueryClient);
  const [router] = useState(() => {
    const root = createRootRoute({ component: () => <Outlet /> });
    const home = createRoute({ getParentRoute: () => root, path: "/studio", component: StudioHome });
    const workspace = createRoute({
      getParentRoute: () => root,
      path: "/studio/$projectId",
      component: function Workspace() {
        return <p data-testid="opened">{workspace.useParams().projectId}</p>;
      },
    });
    return createRouter({
      routeTree: root.addChildren([home, workspace]),
      history: createMemoryHistory({ initialEntries: ["/studio"] }),
    });
  });
  return (
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}

function setup() {
  localStorage.clear();
  useComposerStore.setState({ models: {}, params: {}, batch: 1, seed: null, drafts: {}, studioMode: "image" });
  mockApi({
    "GET /api/models": [MOCK],
    "GET /api/projects": [PROJECT],
    "POST /api/projects": { ...PROJECT, id: "p1", name: "A waitress wipes the counter", brief: "" },
    "POST /api/generations/estimate": { ok: true, problems: [], cost_usd: null, seconds: 2, notes: null },
    "POST /api/generations": (request: Request) =>
      request.json().then((body: { project_id?: string }) => [generation({ project_id: body.project_id ?? null })]),
  });
}

const posts = (path: string) => requests.filter((r) => r.method === "POST" && r.path === path);

describe("studio helpers", () => {
  it("names a project after its first prompt", () => {
    expect(
      projectNameFrom(
        [
          { type: "element", handle: "maya-lee", element_id: "e1" },
          { type: "text", text: " pushes through the door, rain on glass " },
          { type: "move", name: "dolly-in" },
        ],
        "Untitled project",
      ),
    ).toBe("Maya lee pushes through the door");
    expect(projectNameFrom([{ type: "text", text: "  …  " }], "Untitled project")).toBe("Untitled project");
    expect(projectNameFrom([{ type: "text", text: "a waitress wipes the counter of a diner" }], "x")).toBe(
      "A waitress wipes the counter",
    );
  });

  it("shows running takes, and failures from the last day until dismissed", () => {
    const now = Date.parse("2026-11-14T12:00:00Z");
    const list = [
      generation({ id: "run", status: "running" }),
      generation({ id: "done", status: "succeeded" }),
      generation({ id: "failed", status: "failed", created_at: "2026-11-14T11:00:00Z" }),
      generation({ id: "old", status: "failed", created_at: "2026-11-12T11:00:00Z" }),
      generation({ id: "gone", status: "cancelled", created_at: "2026-11-14T11:30:00Z" }),
    ];
    expect(inProgress(list, { gone: true }, now).map((g) => g.id)).toEqual(["run", "failed"]);
  });

  it("puts a generation only in the lists that show it", () => {
    const key = (query: object) => ["get", "/api/generations", { params: { query } }] as const;
    const inProject = generation({ project_id: "p1", mode: "video" });
    expect(listsGeneration(key({ project_id: "p1" }), inProject)).toBe(true);
    expect(listsGeneration(key({ project_id: "p2" }), inProject)).toBe(false);
    expect(listsGeneration(key({ project_id: "none" }), inProject)).toBe(false);
    expect(listsGeneration(key({ mode: "image" }), inProject)).toBe(false);
    expect(listsGeneration(key({ mode: "video" }), inProject)).toBe(true);
    expect(listsGeneration(key({ project_id: "none" }), generation({}))).toBe(true);
  });
});

describe("studio home", () => {
  it("Generate starts a project named after the prompt and opens it", async () => {
    setup();
    const user = userEvent.setup();
    render(<Harness />);
    await screen.findByTestId("studio-hero");
    act(() =>
      useComposerStore.getState().setDraft("image", [{ type: "text", text: "A waitress wipes the counter at 3 a.m." }]),
    );
    const generate = await screen.findByTestId("generate-button");
    await waitFor(() => expect(generate).toBeEnabled());
    await user.click(generate);
    expect(await screen.findByTestId("opened")).toHaveTextContent("p1");
    expect(posts("/api/projects")[0]?.body).toEqual({ name: "A waitress wipes the counter" });
    expect(posts("/api/generations")[0]?.body).toMatchObject({ project_id: "p1", mode: "image" });
  });

  it("adds to a chosen project, and switches mode in place", async () => {
    setup();
    const user = userEvent.setup();
    render(<Harness />);
    const target = await screen.findByTestId("studio-target");
    await screen.findByRole("option", { name: "Night Diner" });
    await user.selectOptions(target, "p0");
    await user.click(screen.getByTestId("composer-mode-video"));
    expect(screen.getByTestId("composer-mode-video")).toHaveAttribute("aria-pressed", "true");
    expect(useComposerStore.getState().studioMode).toBe("video");
    act(() => useComposerStore.getState().setDraft("video", [{ type: "text", text: "rain on the window" }]));
    const generate = screen.getByTestId("generate-button");
    await waitFor(() => expect(generate).toBeEnabled());
    await user.click(generate);
    expect(await screen.findByTestId("opened")).toHaveTextContent("p0");
    expect(posts("/api/projects")).toHaveLength(0);
    expect(posts("/api/generations")[0]?.body).toMatchObject({ project_id: "p0", mode: "video" });
  });
});
