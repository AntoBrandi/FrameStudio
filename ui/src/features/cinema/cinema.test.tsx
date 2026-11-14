import { QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { makeQueryClient } from "@/app/App";
import { Composer } from "@/features/generate/Composer";
import { useComposerStore } from "@/features/generate/store";
import { mockApi, requests } from "@/test/api-mock";
import { STILLS } from "@/test/models";
import { AdherenceSection } from "./AdherenceSection";

// M5.1: the composer sends the shot's cinema controls, Preview shows the
// compiled prompt by section with its notes, and Settings shows which
// controls each model follows.

function withRouter(children: ReactNode) {
  const root = createRootRoute({ component: () => <>{children}</> });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: ["/image"] }) });
  return (
    <QueryClientProvider client={makeQueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}

const SHOT = { film: { genre: "noir" }, lighting: { preset: "contre-jour" } };

describe("cinema controls", () => {
  it("the composer sends the shot and Preview shows the sections and notes", async () => {
    localStorage.clear();
    useComposerStore.setState({ models: {}, params: {}, batch: 1, seed: null, drafts: {}, shots: {} });
    mockApi({
      "GET /api/models": [STILLS],
      "POST /api/generations/estimate": { ok: true, problems: [], cost_usd: null, seconds: 2, notes: null },
      "POST /api/generations/preview": {
        model: "stills",
        strategy: "description",
        prompt: "A diner at night. Look: film noir, with hard low-key light. Lighting: contre-jour backlight.",
        references: [],
        elements: [],
        warnings: [],
        problems: [],
        negative_prompt: null,
        native: {},
        sections: [
          { family: "subject", controls: [], text: "A diner at night", included: true, note: null },
          { family: "film", controls: ["genre"], text: "Look: film noir.", included: true, note: null },
          {
            family: "lighting",
            controls: ["lighting"],
            text: "Lighting: contre-jour backlight.",
            included: true,
            note: "Stills tends to ignore this (lighting).",
          },
        ],
      },
    });
    const user = userEvent.setup();
    render(withRouter(<Composer mode="image" />));
    act(() => {
      useComposerStore.getState().setShot("image", SHOT);
      useComposerStore.getState().setDraft("image", [{ type: "text", text: "A diner at night" }]);
    });
    await user.click(await screen.findByTestId("request-preview-button"));
    const sections = await screen.findByTestId("request-sections");
    expect(
      within(sections)
        .getAllByRole("listitem")
        .map((li) => li.dataset.family),
    ).toEqual(["subject", "film", "lighting"]);
    expect(within(sections).getByTestId("section-note")).toHaveTextContent("tends to ignore this (lighting)");
    await waitFor(() =>
      expect(requests.find((r) => r.method === "POST" && r.path === "/api/generations/preview")?.body).toMatchObject({
        shot: SHOT,
      }),
    );
  });

  it("Settings shows which controls each model follows", async () => {
    mockApi({
      "GET /api/cinema/adherence": [
        {
          model: "mock",
          name: "Mock engine",
          controls: {
            palette: { status: "ignored", score: 0.06, method: "metric", checked_at: "2026-11-14", note: null },
            lens: { status: "untested", score: null, method: null, checked_at: null, note: null },
          },
        },
        {
          model: "nano-banana-pro",
          name: "Nano Banana Pro",
          controls: {
            palette: { status: "followed", score: 21, method: "metric", checked_at: "2026-11-20", note: null },
          },
        },
      ],
    });
    render(
      <QueryClientProvider client={makeQueryClient()}>
        <AdherenceSection />
      </QueryClientProvider>,
    );
    const table = await screen.findByTestId("adherence-table");
    const mock = await within(table).findByText("Mock engine");
    const row = mock.closest("tr")!;
    expect(row.querySelector('[data-control="palette"]')).toHaveAttribute("data-status", "ignored");
    expect(row.querySelector('[data-control="palette"]')).toHaveAttribute(
      "aria-label",
      "Palette: Ignored · measured 2026-11-14",
    );
    expect(row.querySelector('[data-control="moves"]')).toBeNull(); // does not apply
    const nano = within(table).getByText("Nano Banana Pro").closest("tr")!;
    expect(nano.querySelector('[data-control="palette"]')).toHaveAttribute("data-status", "followed");
  });
});
