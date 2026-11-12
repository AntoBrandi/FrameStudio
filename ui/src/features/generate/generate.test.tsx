import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import type { GenerationOut } from "@/api/client";
import { makeQueryClient } from "@/app/App";
import { mockApi, requests } from "@/test/api-mock";
import { Composer } from "./Composer";
import { GenerationCard } from "./GenerationCard";

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
  params: { aspect: "16:9" },
  seed: 7,
  status: "queued",
  error_code: null,
  error_message: null,
  job_id: "j1",
  parent_asset_id: null,
  asset: null,
  created_at: "2026-11-12T10:00:00Z",
  started_at: null,
  finished_at: null,
};

describe("composer", () => {
  it("sends the prompt and controls on Enter", async () => {
    mockApi({ "POST /api/generations": [base] });
    const user = userEvent.setup();
    render(wrap(<Composer mode="image" />));
    await user.click(screen.getByRole("button", { name: "16:9" }));
    await user.click(screen.getByRole("button", { name: "Batch size +" }));
    await user.type(screen.getByTestId("prompt-input"), "a red door{Enter}");
    await waitFor(() =>
      expect(requests).toContainEqual({
        method: "POST",
        path: "/api/generations",
        body: { mode: "image", model: "mock", prompt: "a red door", batch: 2, params: { aspect: "16:9" } },
      }),
    );
  });

  it("keeps the batch between 1 and 4", async () => {
    const user = userEvent.setup();
    render(wrap(<Composer mode="video" />));
    const minus = screen.getByRole("button", { name: "Batch size −" });
    const plus = screen.getByRole("button", { name: "Batch size +" });
    expect(minus).toBeDisabled();
    for (let i = 0; i < 5; i += 1) await user.click(plus);
    expect(plus).toBeDisabled();
    expect(screen.getByRole("group", { name: "Batch size" })).toHaveTextContent("4");
  });

  it("shows video controls only in video mode", () => {
    render(wrap(<Composer mode="video" />));
    expect(screen.getByRole("group", { name: "Duration" })).toBeInTheDocument();
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
    };
    render(wrap(<GenerationCard generation={{ ...base, status: "succeeded", asset }} onOpen={onOpen} />));
    expect(screen.getByRole("img")).toHaveAttribute("src", "/api/assets/a1/file");
    await user.click(screen.getByTestId("open-viewer"));
    expect(onOpen).toHaveBeenCalledOnce();
  });
});
