import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { makeQueryClient } from "@/app/App";
import { mockApi, requests } from "@/test/api-mock";
import { ReferencePicker } from "./ReferencePicker";

// M4.5: the reference picker: sources in tabs, search, multi-select across
// tabs, elements, and a counter that stops at the model's limit.

const asset = (id: string, name: string, extra: Record<string, unknown> = {}) => ({
  id,
  kind: "image",
  mime: "image/png",
  size_bytes: 10,
  width: 32,
  height: 32,
  duration: null,
  original_name: name,
  url: `/api/assets/${id}/file`,
  thumb_url: `/api/assets/${id}/thumb`,
  provenance: "uploaded",
  favorite: false,
  ...extra,
});

const element = (id: string, handle: string, name: string, pictures: number, category = "character") => ({
  id,
  handle,
  name,
  category,
  status: "ready",
  version: 1,
  description: "",
  properties: {},
  project_id: null,
  folder_id: null,
  media: Array.from({ length: pictures }, (_, i) => ({
    asset_id: `${id}-m${i}`,
    role: i ? "angle" : "primary",
    position: i,
    kind: "image",
    url: "",
    thumb_url: null,
  })),
  cover_url: null,
  used_in: 0,
  created_at: "2026-11-14T00:00:00Z",
  updated_at: "2026-11-14T00:00:00Z",
});

const page = (items: unknown[]) => ({ items, next_cursor: null, total: items.length });

function setup() {
  mockApi({
    "GET /api/assets": (request: Request) => {
      const url = new URL(request.url);
      if (url.searchParams.get("provenance") === "generated") return page([asset("g1", "dawn.png")]);
      if (url.searchParams.get("liked") === "true") return page([asset("l1", "liked.png")]);
      return page([asset("a1", "one.png"), asset("a2", "two.png"), asset("a3", "three.png")]);
    },
    "GET /api/elements": [element("e1", "maya", "Maya", 2), element("e2", "pier", "Pier", 0, "location")],
  });
}

function open(props: Partial<Parameters<typeof ReferencePicker>[0]> = {}) {
  const onAdd = vi.fn();
  render(
    <QueryClientProvider client={makeQueryClient()}>
      <ReferencePicker
        open
        onOpenChange={() => undefined}
        taken={[]}
        room={2}
        limit={4}
        kinds={["image"]}
        elements={{ inPrompt: [] }}
        onAdd={onAdd}
        {...props}
      />
    </QueryClientProvider>,
  );
  return onAdd;
}

describe("reference picker", () => {
  it("counts against the model's limit and stops there", async () => {
    setup();
    const user = userEvent.setup();
    const onAdd = open({ room: 2, limit: 4 });
    await screen.findByRole("button", { name: "one.png" });
    const grid = screen.getByTestId("reference-picker");
    expect(screen.getByTestId("picker-counter")).toHaveTextContent("2/4 pictures");
    await user.click(within(grid).getByRole("button", { name: "one.png" }));
    await user.click(within(grid).getByRole("button", { name: "two.png" }));
    expect(screen.getByTestId("picker-counter")).toHaveTextContent("4/4 pictures · the model's limit");
    expect(within(grid).getByRole("button", { name: "three.png" })).toBeDisabled();
    // Deselecting frees a place again.
    await user.click(within(grid).getByRole("button", { name: "two.png" }));
    expect(within(grid).getByRole("button", { name: "three.png" })).toBeEnabled();
    await user.click(screen.getByTestId("add-references"));
    expect(onAdd).toHaveBeenCalledWith([expect.objectContaining({ id: "a1", name: "one.png" })], []);
  });

  it("keeps the selection across tabs, and each tab asks for its own source", async () => {
    setup();
    const user = userEvent.setup();
    const onAdd = open({ room: 3 });
    await user.click(await screen.findByRole("button", { name: "one.png" }));
    await user.click(screen.getByTestId("picker-tab-generations"));
    await user.click(await screen.findByRole("button", { name: "dawn.png" }));
    await user.click(screen.getByTestId("picker-tab-liked"));
    expect(await screen.findByRole("button", { name: "liked.png" })).toBeInTheDocument();
    await user.click(screen.getByTestId("picker-tab-elements"));
    const elements = await screen.findByTestId("picker-elements");
    await user.click(within(elements).getByRole("button", { name: "@maya" }));
    expect(screen.getByTestId("picker-counter")).toHaveTextContent("4/4 pictures");
    // Full: another element with pictures cannot be added; one without can.
    expect(within(elements).getByRole("button", { name: "@pier" })).toBeEnabled();
    await user.click(within(elements).getByRole("button", { name: "@pier" }));
    await user.click(screen.getByTestId("add-references"));
    const [pictures, picked] = onAdd.mock.calls[0]!;
    expect(pictures.map((p: { id: string }) => p.id)).toEqual(["a1", "g1"]);
    expect(picked.map((e: { handle: string }) => e.handle)).toEqual(["maya", "pier"]);
    const queries = requests.filter((r) => r.method === "GET" && r.path === "/api/assets");
    expect(queries.length).toBeGreaterThanOrEqual(3);
  });

  it("searches, filters elements by category and marks those already in the prompt", async () => {
    setup();
    const user = userEvent.setup();
    open({ elements: { inPrompt: ["e1"] }, initialTab: "elements" });
    const maya = await screen.findByRole("button", { name: "@maya" });
    expect(maya).toBeDisabled();
    expect(maya).toHaveTextContent("in the prompt");
    await user.click(screen.getByRole("button", { name: "Locations" }));
    await user.type(screen.getByTestId("picker-search"), "pi");
    await waitFor(() => expect(fetchedElements()).toContainEqual(expect.stringContaining("category=location")));
    expect(fetchedElements().some((url) => url.includes("q=pi"))).toBe(true);
  });

  it("with a model that takes no pictures, elements go in words and are not limited", async () => {
    setup();
    const user = userEvent.setup();
    open({ room: 0, limit: 0, initialTab: "elements" });
    await user.click(await screen.findByRole("button", { name: "@maya" }));
    expect(screen.getByTestId("picker-counter")).toHaveTextContent(
      "0/0 pictures · this model takes no pictures; elements go in words",
    );
    expect(screen.getByTestId("add-references")).toBeEnabled();
  });

  it("presets wait for the cinema controls", async () => {
    setup();
    const user = userEvent.setup();
    open();
    await user.click(screen.getByTestId("picker-tab-presets"));
    expect(screen.getByText(/arrive with the cinema controls/)).toBeInTheDocument();
  });
});

const fetchedElements = () =>
  (globalThis.fetch as unknown as { mock: { calls: [Request | string][] } }).mock.calls
    .map(([input]) => (typeof input === "string" ? input : input.url))
    .filter((url) => url.includes("/api/elements"));
