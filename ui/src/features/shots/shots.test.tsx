import { QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { type ReactNode, useState } from "react";
import { describe, expect, it } from "vitest";
import type { GenerationOut } from "@/api/client";
import { makeQueryClient } from "@/app/App";
import { Composer } from "@/features/generate/Composer";
import { useComposerStore } from "@/features/generate/store";
import { mockApi, requests } from "@/test/api-mock";
import { model } from "@/test/models";
import { type ShotOut, ShotsTray } from "./ShotsTray";

// M5.7: keyframe then shot.

const MOCK = model("mock", ["image", "video"], {}, [], {
  name: "Mock engine",
  runtime: "local",
  capabilities: ["text_to_image", "text_to_video", "image_to_video", "start_end_frames"],
});

function generation(id: string, patch: Partial<GenerationOut> = {}): GenerationOut {
  return {
    id,
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
    status: "succeeded",
    error_code: null,
    error_message: null,
    job_id: null,
    parent_asset_id: null,
    project_id: "p1",
    folder_id: null,
    shot_id: "s1",
    shot_role: "keyframe",
    asset: {
      id: `a-${id}`,
      kind: "image",
      thumb_url: `/api/assets/a-${id}/thumb`,
    } as GenerationOut["asset"],
    created_at: "2026-11-14T10:00:00Z",
    started_at: null,
    finished_at: null,
    ...patch,
  } as GenerationOut;
}

function shot(patch: Partial<ShotOut> = {}): ShotOut {
  return {
    id: "s1",
    project_id: "p1",
    folder_id: null,
    title: "The door opens",
    prompt: "The door opens",
    prompt_tokens: [],
    spec: null,
    video_model: "mock",
    video_params: { aspect: "16:9" },
    keyframe_model: "mock",
    keyframe_params: { aspect: "16:9" },
    keyframe_id: null,
    end_frame_id: null,
    continues_from: null,
    status: "choose",
    keyframes: [generation("k1"), generation("k2"), generation("k3", { status: "running", asset: null })],
    clips: [],
    checks: {},
    archived: false,
    created_at: "2026-11-14T10:00:00Z",
    updated_at: "2026-11-14T10:00:00Z",
    ...patch,
  } as ShotOut;
}

function Wrap({ children }: { children: ReactNode }) {
  const [client] = useState(makeQueryClient);
  const [router] = useState(() =>
    createRouter({
      routeTree: createRootRoute({ component: () => <>{children}</> }),
      history: createMemoryHistory({ initialEntries: ["/video"] }),
    }),
  );
  return (
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}

const posted = (path: string) => requests.filter((r) => r.method === "POST" && r.path === path);
const ESTIMATE = { ok: true, problems: [], cost_usd: null, seconds: 2, notes: null };

describe("shot cards", () => {
  it("pick a start and an end keyframe, then animate", async () => {
    mockApi({
      "GET /api/models": [MOCK],
      "GET /api/shots": [shot()],
      "POST /api/generations/estimate": ESTIMATE,
      "POST /api/shots/s1/animate": shot({
        status: "animating",
        clips: [generation("c1", { mode: "video", shot_role: "clip", status: "queued", asset: null })],
      }),
    });
    const user = userEvent.setup();
    render(
      <Wrap>
        <ShotsTray projectId="p1" />
      </Wrap>,
    );
    const card = await screen.findByTestId("shot-card");
    const animate = within(card).getByTestId("animate-shot");
    expect(animate).toBeDisabled(); // nothing chosen yet
    expect(within(card).getAllByTestId("shot-pending")).toHaveLength(1); // the keyframe still running
    const [first, second] = within(card).getAllByTestId("keyframe-option");
    await user.click(first!);
    expect(first).toHaveAttribute("aria-pressed", "true");
    // The mock takes end frames: the other keyframe can be the end.
    await user.click(within(card).getByTestId("keyframe-end"));
    expect(card).toHaveTextContent("End");
    expect(second).toHaveAttribute("aria-pressed", "false");
    await user.click(animate);
    await waitFor(() =>
      expect(posted("/api/shots/s1/animate")[0]?.body).toEqual({ keyframe_id: "a-k1", end_frame_id: "a-k2" }),
    );
  });

  it("shows each clip's continuity and extends a finished clip", async () => {
    const clip = generation("c1", { mode: "video", shot_role: "clip", start_frame: "a-k1" });
    mockApi({
      "GET /api/models": [MOCK],
      "GET /api/shots": [
        shot({
          status: "done",
          keyframe_id: "a-k1",
          clips: [clip],
          checks: { c1: { start: 0.99, end: null, matches: true } },
        }),
      ],
      "POST /api/generations/estimate": ESTIMATE,
      "POST /api/shots/s1/extend": new Response(JSON.stringify(shot({ id: "s2", status: "animating" })), {
        status: 201,
      }),
    });
    const user = userEvent.setup();
    render(
      <Wrap>
        <ShotsTray projectId="p1" />
      </Wrap>,
    );
    const badge = await screen.findByTestId("shot-continuity");
    expect(badge).toHaveTextContent("opens 99%");
    expect(badge).toHaveAttribute("data-matches", "true");
    // The approved keyframe is shown as the start.
    expect(screen.getAllByTestId("keyframe-option")[0]).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByTestId("extend-clip"));
    await waitFor(() => expect(posted("/api/shots/s1/extend")[0]?.body).toEqual({ generation_id: "c1" }));
  });
});

describe("keyframe first", () => {
  it("Generate plans a shot with the composer's prompt, controls and settings", async () => {
    localStorage.clear();
    useComposerStore.setState({
      models: {},
      params: {},
      batch: 3,
      seed: null,
      drafts: {},
      shots: { video: { lighting: { preset: "practicals" } } } as never,
      keyframeFirst: false,
      keyframeModel: "auto",
    });
    mockApi({
      "GET /api/models": [MOCK],
      "GET /api/shots": [],
      "POST /api/generations/estimate": ESTIMATE,
      "POST /api/shots/estimate": {
        ok: true,
        problems: [],
        keyframes: ESTIMATE,
        clip: ESTIMATE,
        cost_usd: null,
        seconds: 8,
        keyframe_model: "mock",
        video_model: "mock",
      },
      "POST /api/shots": new Response(JSON.stringify(shot()), { status: 201 }),
    });
    const user = userEvent.setup();
    render(
      <Wrap>
        <Composer mode="video" projectId="p1" />
      </Wrap>,
    );
    const toggle = await screen.findByTestId("keyframe-first");
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("keyframe-model")).toHaveValue("auto");
    expect(screen.queryByTestId("frame-slots")).not.toBeInTheDocument(); // the keyframe is the start
    act(() => useComposerStore.getState().setDraft("video", [{ type: "text", text: "the door opens" }]));
    const generate = screen.getByTestId("generate-button");
    await waitFor(() => expect(generate).toHaveTextContent(/Keyframes.*~8 s/));
    await user.click(generate);
    await waitFor(() =>
      expect(posted("/api/shots")[0]?.body).toMatchObject({
        prompt: "the door opens",
        prompt_tokens: [{ type: "text", text: "the door opens" }],
        spec: { lighting: { preset: "practicals" } },
        project_id: "p1",
        video_model: "mock",
        keyframe_model: "auto",
        count: 3,
      }),
    );
    expect(posted("/api/generations")).toHaveLength(0);
  });
});
