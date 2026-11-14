import { QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import type { GenerationOut } from "@/api/client";
import { makeQueryClient } from "@/app/App";
import { mockApi, requests } from "@/test/api-mock";
import { CLIPS, model, SIZES } from "@/test/models";
import { Composer } from "./Composer";
import { GenerationCard } from "./GenerationCard";
import type { ModelOut } from "./params";
import { useComposerStore } from "./store";

function wrap(children: ReactNode) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}

const base: GenerationOut = {
  id: "g1",
  batch_id: "b",
  batch_index: 0,
  mode: "image",
  model_id: "mock",
  provider: "mock",
  prompt: "a red door",
  prompt_tokens: [{ type: "text", text: "a red door" }],
  resolved: null,
  params: { aspect: "16:9" },
  references: [],
  start_frame: null,
  end_frame: null,
  seed: 7,
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
};

const MOCK = model(
  "mock",
  ["image", "video"],
  {
    aspect: {
      type: "enum",
      values: ["1:1", "16:9"],
      default: "1:1",
      default_by_mode: { video: "16:9" },
      control: "pills",
    },
    duration: { type: "integer", min: 2, max: 12, default: 3, unit: "s", modes: ["video"], control: "stepper" },
  },
  [],
  { name: "Mock engine", runtime: "local" },
);
const CLOUD = model(
  "cloud",
  ["image", "video"],
  {
    aspect: { type: "enum", values: ["1:1", "16:9", "9:16"], default: "1:1", control: "pills" },
    quality: { type: "enum", values: ["draft", "final"], default: "draft", control: "pills" },
    guidance: { type: "number", min: 1, max: 20, default: 7, control: "slider", advanced: true },
  },
  [],
  {
    name: "Cloud model",
    limits: { max_references: 2, reference_kinds: ["image"], max_batch: 2, max_prompt_chars: null },
    pricing: {
      unit: "image",
      usd: 0.04,
      by: null,
      table: null,
      source_url: "https://example.com",
      checked_at: "2026-11-14",
    },
  },
);
const OFFLINE = model("offline", ["image"], {}, [], {
  name: "Offline model",
  available: false,
  unavailable_reason: "Connect it in Settings › Connections.",
});

function withRouter(children: ReactNode) {
  const root = createRootRoute({ component: () => <>{children}</> });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: ["/image"] }) });
  return wrap(<RouterProvider router={router} />);
}

function setup(models: ModelOut[] = [MOCK, CLOUD, OFFLINE, CLIPS, SIZES]) {
  localStorage.clear();
  useComposerStore.setState({ models: {}, params: {}, batch: 1, seed: null, drafts: {} });
  mockApi({
    "GET /api/models": models,
    "POST /api/generations/estimate": { ok: true, problems: [], cost_usd: null, seconds: 2, notes: null },
    "POST /api/generations": [base],
  });
}

async function pickModel(user: ReturnType<typeof userEvent.setup>, id: string) {
  await user.click(await screen.findByTestId("model-picker"));
  await user.click(await screen.findByTestId(`model-option-${id}`));
}

describe("composer", () => {
  it("draws the controls from the model's schema", async () => {
    setup();
    render(withRouter(<Composer mode="video" />));
    expect(await screen.findByRole("group", { name: "duration" })).toHaveTextContent("3s");
    expect(screen.getByRole("group", { name: "aspect" })).toBeInTheDocument();
    expect(screen.getByTestId("model-picker")).toHaveTextContent("Mock engine");
    expect(screen.queryByTestId("reference-tray")).not.toBeInTheDocument();
  });

  it("switching models re-renders only the controls the new model has", async () => {
    setup();
    const user = userEvent.setup();
    render(withRouter(<Composer mode="video" />));
    await screen.findByRole("group", { name: "duration" });
    await pickModel(user, "clips");
    expect(screen.getByTestId("model-picker")).toHaveTextContent("clips");
    expect(screen.getByRole("group", { name: "aspect" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "21:9" })).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "quality" })).not.toBeInTheDocument();
    await pickModel(user, "mock");
    expect(screen.queryByRole("button", { name: "21:9" })).not.toBeInTheDocument();
  });

  it("does not let a refused combination be chosen", async () => {
    setup();
    const user = userEvent.setup();
    render(withRouter(<Composer mode="video" />));
    await pickModel(user, "clips");
    const plus = screen.getByRole("button", { name: "duration +" });
    for (let i = 0; i < 10; i += 1) await user.click(plus);
    expect(screen.getByRole("group", { name: "duration" })).toHaveTextContent("12s");
    const wide = screen.getByRole("button", { name: "21:9" });
    expect(wide).toHaveAttribute("aria-disabled", "true");
    expect(wide).toHaveAttribute("title", "21:9 clips run 10 s at most");
    await user.click(wide);
    expect(wide).toHaveAttribute("aria-pressed", "false");
    // Shorter clips make 21:9 available again, and then cap the duration.
    for (let i = 0; i < 2; i += 1) await user.click(screen.getByRole("button", { name: "duration −" }));
    await user.click(screen.getByRole("button", { name: "21:9" }));
    expect(screen.getByRole("button", { name: "21:9" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "duration +" })).toBeDisabled();
  });

  it("lists models by where they run and explains the ones that cannot", async () => {
    setup();
    const user = userEvent.setup();
    render(withRouter(<Composer mode="image" />));
    await user.click(await screen.findByTestId("model-picker"));
    const offline = await screen.findByTestId("model-option-offline");
    expect(offline).toHaveAttribute("data-disabled", "true");
    expect(offline).toHaveTextContent("Connect it in Settings › Connections.");
    expect(screen.getByTestId("model-option-cloud")).toHaveTextContent("$0.04 / image");
    expect(screen.getByText("On this machine")).toBeInTheDocument();
    expect(screen.getByText("Hosted")).toBeInTheDocument();
    await user.type(screen.getByPlaceholderText("Search models…"), "sizes");
    expect(screen.queryByTestId("model-option-cloud")).not.toBeInTheDocument();
    expect(screen.getByTestId("model-option-sizes")).toBeInTheDocument();
  });

  it("sends the prompt, model and settings on Enter", async () => {
    setup();
    const user = userEvent.setup();
    render(withRouter(<Composer mode="image" />));
    await user.click(await screen.findByRole("button", { name: "16:9" }));
    await user.click(screen.getByRole("button", { name: "Batch size +" }));
    // jsdom does not feed typed text to Lexical; the browser E2E tests type for real.
    act(() => useComposerStore.getState().setDraft("image", [{ type: "text", text: "a red door" }]));
    await waitFor(() => expect(screen.getByTestId("prompt-input")).toHaveTextContent("a red door"));
    await user.click(screen.getByTestId("prompt-input"));
    await user.keyboard("{Enter}");
    await waitFor(() =>
      expect(requests).toContainEqual({
        method: "POST",
        path: "/api/generations",
        body: {
          mode: "image",
          model: "mock",
          prompt: "a red door",
          prompt_tokens: [{ type: "text", text: "a red door" }],
          batch: 2,
          params: { aspect: "16:9" },
          references: [],
        },
      }),
    );
  });

  it("sends element chips as tokens; shows what the model sees and its warnings", async () => {
    setup();
    const ghost = { type: "element" as const, handle: "ghost", element_id: null };
    const maya = { type: "element" as const, handle: "maya", element_id: "e1" };
    mockApi({
      "POST /api/generations/preview": {
        model: "mock",
        strategy: "description",
        prompt: "Maya Ortiz meets @ghost\n\nMaya Ortiz: short dark hair.",
        references: [],
        elements: [{ id: "e1", handle: "maya", name: "Maya Ortiz", version: 2, images: 0, described: true }],
        warnings: ["@ghost is not an element here; it is sent as plain text."],
        problems: [],
      },
    });
    const user = userEvent.setup();
    render(withRouter(<Composer mode="image" />));
    act(() => useComposerStore.getState().setDraft("image", [maya, { type: "text", text: " meets " }, ghost]));
    expect(await screen.findByTestId("prompt-warnings")).toHaveTextContent("@ghost is not an element here");
    await user.click(screen.getByTestId("request-preview-button"));
    const preview = await screen.findByTestId("request-preview");
    expect(preview).toHaveTextContent("elements are named and described in words");
    expect(screen.getByTestId("request-prompt")).toHaveTextContent("Maya Ortiz: short dark hair.");
    await user.keyboard("{Escape}");
    await user.click(screen.getByTestId("prompt-input"));
    await user.keyboard("{Enter}");
    await waitFor(() =>
      expect(requests.find((r) => r.method === "POST" && r.path === "/api/generations")?.body).toMatchObject({
        prompt: "@maya meets @ghost",
        prompt_tokens: [maya, { type: "text", text: " meets " }, ghost],
      }),
    );
  });

  it("keeps the batch within the model's limit", async () => {
    setup();
    const user = userEvent.setup();
    render(withRouter(<Composer mode="image" />));
    const plus = await screen.findByRole("button", { name: "Batch size +" });
    for (let i = 0; i < 5; i += 1) await user.click(plus);
    expect(screen.getByRole("group", { name: "Batch size" })).toHaveTextContent("4");
    await pickModel(user, "cloud");
    expect(screen.getByRole("group", { name: "Batch size" })).toHaveTextContent("2");
    expect(screen.getByRole("button", { name: "Batch size +" })).toBeDisabled();
  });

  it("shows the estimate, and the reasons Generate is held back", async () => {
    setup();
    mockApi({
      "POST /api/generations/estimate": { ok: true, problems: [], cost_usd: 0.08, seconds: 4, notes: null },
    });
    const user = userEvent.setup();
    render(withRouter(<Composer mode="image" />));
    await pickModel(user, "cloud");
    expect(await screen.findByTestId("estimate")).toHaveTextContent("about $0.08 · ~4 s");
    mockApi({
      "POST /api/generations/estimate": {
        ok: false,
        problems: ["This would pass the $1.00 monthly cap."],
        cost_usd: 0.08,
        seconds: 4,
        notes: null,
      },
    });
    await user.click(screen.getByRole("button", { name: "final" }));
    expect(await screen.findByTestId("composer-problems")).toHaveTextContent("monthly cap");
    expect(screen.getByTestId("generate-button")).toBeDisabled();
  });

  it("offers a reference tray sized to the model", async () => {
    setup();
    const user = userEvent.setup();
    render(withRouter(<Composer mode="image" />));
    await pickModel(user, "cloud");
    expect(screen.getByTestId("reference-count")).toHaveTextContent("References 0/2");
    expect(screen.getByTestId("more-settings")).toBeInTheDocument();
    await user.click(screen.getByTestId("more-settings"));
    expect(await screen.findByTestId("param-guidance")).toBeInTheDocument();
    expect(screen.getByTestId("seed-input")).toHaveAttribute("placeholder", "random");
  });
});

describe("generation card", () => {
  it("shows progress while running", () => {
    render(
      wrap(
        <GenerationCard
          generation={{ ...base, status: "running" }}
          job={{
            id: "j1",
            kind: "generate",
            lane: "gpu",
            state: "running",
            title: "",
            progress: 0.5,
            message: null,
            error_code: null,
            error_message: null,
            attempts: 1,
            remote: false,
            result: null,
            created_at: "",
            started_at: null,
            finished_at: null,
          }}
          onOpen={() => undefined}
        />,
      ),
    );
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "50");
  });

  it("shows the error and retries a failed generation", async () => {
    mockApi({ "POST /api/generations/g1/retry": { ...base, status: "queued" } });
    const user = userEvent.setup();
    render(
      wrap(
        <GenerationCard
          generation={{ ...base, status: "failed", error_code: "mock_failure", error_message: "Boom" }}
          onOpen={() => undefined}
        />,
      ),
    );
    expect(screen.getByText("Generation failed")).toBeInTheDocument();
    expect(screen.getByText("Boom")).toBeInTheDocument();
    await user.click(screen.getByTestId("retry-generation"));
    await waitFor(() => expect(requests.some((r) => r.path === "/api/generations/g1/retry")).toBe(true));
  });

  it("opens the viewer from a finished image", async () => {
    const onOpen = vi.fn();
    const user = userEvent.setup();
    const asset = {
      id: "a1",
      kind: "image" as const,
      mime: "image/png",
      size_bytes: 10,
      width: 1024,
      height: 576,
      duration: null,
      fps: null,
      has_audio: null,
      sha256: "x",
      url: "/api/assets/a1/file",
      source_generation_id: "g1",
      created_at: "",
    } as unknown as NonNullable<GenerationOut["asset"]>;
    render(wrap(<GenerationCard generation={{ ...base, status: "succeeded", asset }} onOpen={onOpen} />));
    expect(screen.getByRole("img")).toHaveAttribute("src", "/api/assets/a1/file");
    await user.click(screen.getByTestId("open-viewer"));
    expect(onOpen).toHaveBeenCalledOnce();
  });
});
