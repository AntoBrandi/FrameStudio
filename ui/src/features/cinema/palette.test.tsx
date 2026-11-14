import { QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { makeQueryClient } from "@/app/App";
import { mockApi, requests } from "@/test/api-mock";
import { GradeDialog } from "./PalettePicker";
import type { ShotSpec } from "./shot";
import { ShotChips } from "./ShotPickers";

// M5.4: palettes from presets, saved ones and pictures; grading toward one.

const VOCAB = {
  genres: [],
  eras: [],
  tempos: [],
  bodies: [],
  lenses: [],
  apertures: [],
  focal_lengths: [],
  moves: [],
  lighting: [],
  palettes: [
    {
      id: "teal-night",
      label: "Teal night",
      words: "teal",
      swatches: ["#0B1D26", "#1F6F78", "#F2A65A"],
      negative: null,
    },
    {
      id: "golden-hour",
      label: "Golden hour",
      words: "gold",
      swatches: ["#3B1F0E", "#E0913A", "#FBE6B8"],
      negative: null,
    },
  ],
  previews: {},
};
const ASSET = {
  id: "a1",
  kind: "image",
  mime: "image/png",
  size_bytes: 1,
  width: 10,
  height: 10,
  duration: null,
  original_name: "sunset.png",
  url: "/api/assets/a1/file",
  thumb_url: "/api/assets/a1/thumb",
  provenance: "uploaded",
  favorite: false,
};

function Harness() {
  const [shot, setShot] = useState<ShotSpec | null>(null);
  const [client] = useState(makeQueryClient);
  return (
    <QueryClientProvider client={client}>
      <ShotChips shot={shot} onChange={setShot} video={false} />
      <output data-testid="shot">{JSON.stringify(shot)}</output>
    </QueryClientProvider>
  );
}

const shotNow = () => JSON.parse(screen.getByTestId("shot").textContent!) as ShotSpec | null;

describe("palettes", () => {
  it("a preset, then a new palette from a picture, saved and used", async () => {
    const saved: unknown[] = [];
    mockApi({
      "GET /api/cinema/vocab": VOCAB,
      "GET /api/cinema/presets": () => saved,
      "GET /api/assets": { items: [ASSET], next_cursor: null, total: 1 },
      "POST /api/cinema/palettes/extract": {
        swatches: ["#2A0F1F", "#D9653B", "#F2B880"],
        shares: [0.5, 0.3, 0.2],
        words: "rich, warm tones: plum, rust and cream",
      },
      "POST /api/cinema/presets": async (request: Request) => {
        const body = (await request.json()) as { name: string; data: unknown };
        const preset = {
          id: "p1",
          kind: "palette",
          name: body.name,
          data: body.data,
          preview_url: "/api/assets/a1/thumb",
        };
        saved.push(preset);
        return new Response(JSON.stringify(preset), { status: 201 });
      },
    });
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(await screen.findByTestId("chip-palette"));
    const picker = await screen.findByTestId("palette-picker");
    await user.click(within(picker).getByRole("radio", { name: "Teal night" }));
    expect(shotNow()).toEqual({ palette: { id: "teal-night" } });
    expect(within(picker).getByRole("radio", { name: "Teal night" })).toHaveAttribute("aria-checked", "true");

    await user.click(within(picker).getByTestId("palette-from-picture"));
    await user.click(await screen.findByRole("button", { name: "sunset.png" }));
    await user.click(screen.getByTestId("add-references"));
    const fresh = await within(picker).findByTestId("palette-new");
    expect(within(fresh).getByTestId("palette-name")).toHaveValue("From sunset.png");
    expect(within(fresh).getByTestId("palette-words")).toHaveValue("rich, warm tones: plum, rust and cream");
    await user.clear(within(fresh).getByTestId("palette-name"));
    await user.type(within(fresh).getByTestId("palette-name"), "Sunset");
    await user.click(within(fresh).getByTestId("palette-save"));
    await waitFor(() =>
      expect(shotNow()).toEqual({
        palette: {
          label: "Sunset",
          words: "rich, warm tones: plum, rust and cream",
          swatches: ["#2A0F1F", "#D9653B", "#F2B880"],
        },
      }),
    );
    expect(requests.find((r) => r.method === "POST" && r.path === "/api/cinema/presets")?.body).toMatchObject({
      kind: "palette",
      name: "Sunset",
      preview_asset_id: "a1",
    });
    expect(await within(picker).findByRole("radio", { name: "Sunset" })).toHaveAttribute("aria-checked", "true");
    await user.click(within(picker).getByTestId("palette-done"));
    expect(screen.getByTestId("chip-palette")).toHaveAccessibleName("Palette: Sunset");
    await user.click(screen.getByTestId("chip-palette-clear"));
    expect(shotNow()).toBeNull();
  });

  it("grades a picture toward a chosen palette at a strength", async () => {
    mockApi({
      "GET /api/cinema/vocab": VOCAB,
      "GET /api/cinema/presets": [],
      "POST /api/cinema/grade/a1": new Response(JSON.stringify({ job_id: "j1" }), { status: 202 }),
    });
    const user = userEvent.setup();
    let open = true;
    render(
      <QueryClientProvider client={makeQueryClient()}>
        <GradeDialog assetId="a1" open onOpenChange={(next) => (open = next)} />
      </QueryClientProvider>,
    );
    const dialog = await screen.findByTestId("grade-dialog");
    expect(within(dialog).getByTestId("grade-start")).toBeDisabled();
    await user.click(await within(dialog).findByRole("radio", { name: "Golden hour" }));
    // jsdom does not move a range input with the keys: set it as a drag would.
    fireEvent.change(within(dialog).getByTestId("grade-strength"), { target: { value: "70" } });
    await user.click(within(dialog).getByTestId("grade-start"));
    await waitFor(() =>
      expect(requests.find((r) => r.method === "POST" && r.path === "/api/cinema/grade/a1")?.body).toEqual({
        palette: { id: "golden-hour", label: "Golden hour" },
        strength: 0.7,
      }),
    );
    await waitFor(() => expect(open).toBe(false));
  });
});
