import { QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { makeQueryClient } from "@/app/App";
import { mockApi, requests } from "@/test/api-mock";
import { addLight, type Rig, removeLight, updateLight } from "./lighting";
import type { ShotSpec } from "./shot";
import { ShotChips } from "./ShotPickers";

// M5.5: lighting presets and the rig editor (jsdom has no WebGL, so the
// flat diagram stands in for the sphere).

const PRACTICALS = {
  lights: [
    { azimuth: 300, elevation: 20, colour: "#FFB46B", intensity: 0.8, softness: 0.5 },
    { azimuth: 60, elevation: 10, colour: "#FFC27A", intensity: 0.4, softness: 0.6 },
  ],
  ambient: 0.1,
};
const VOCAB = {
  genres: [],
  eras: [],
  tempos: [],
  bodies: [],
  lenses: [],
  apertures: [],
  focal_lengths: [],
  moves: [],
  palettes: [],
  lighting: [
    { id: "practicals", label: "Practicals", phrase: "lamps", tags: "", negative: null, profiles: {}, rig: PRACTICALS },
    {
      id: "silhouette",
      label: "Silhouette",
      phrase: "backlit",
      tags: "",
      negative: null,
      profiles: {},
      rig: { lights: [{ azimuth: 180, elevation: 10, colour: "#FFF2DD", intensity: 1, softness: 0.3 }], ambient: 0.03 },
    },
  ],
  previews: {},
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

describe("rig helpers", () => {
  it("edit, add and remove lights within the limits", () => {
    expect(updateLight(PRACTICALS, 1, { azimuth: 90 }).lights[1]!.azimuth).toBe(90);
    let rig: Rig = PRACTICALS;
    rig = addLight(rig);
    rig = addLight(rig);
    expect(addLight(rig).lights).toHaveLength(4);
    expect(removeLight({ ...PRACTICALS, lights: [PRACTICALS.lights[0]!] }, 0).lights).toHaveLength(1);
  });
});

describe("lighting picker", () => {
  it("a preset, an edit of its rig, saved and reloaded", async () => {
    const saved: unknown[] = [];
    mockApi({
      "GET /api/cinema/vocab": VOCAB,
      "GET /api/cinema/presets": () => saved,
      "POST /api/cinema/lighting/describe": async (request: Request) => {
        const rig = (await request.json()) as typeof PRACTICALS;
        return { words: `${rig.lights.length} lights, first at ${rig.lights[0]!.azimuth}°` };
      },
      "POST /api/cinema/presets": async (request: Request) => {
        const body = (await request.json()) as { name: string; data: unknown };
        const preset = { id: "l1", kind: "lighting", name: body.name, data: body.data, preview_url: null };
        saved.push(preset);
        return new Response(JSON.stringify(preset), { status: 201 });
      },
    });
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(await screen.findByTestId("chip-lighting"));
    const picker = await screen.findByTestId("lighting-picker");
    await user.click(within(picker).getByTestId("lighting-preset-practicals"));
    expect(shotNow()).toEqual({ lighting: { preset: "practicals" } });
    // The preset's rig is in the editor (here as the flat diagram), and its words.
    expect(within(picker).getAllByRole("button", { name: /^Light \d$/ })).toHaveLength(2);
    expect(await within(picker).findByTestId("lighting-words")).toHaveTextContent("2 lights, first at 300°");

    // Editing makes the rig the truth; the preset is kept as where it began.
    fireEvent.change(within(picker).getByTestId("lighting-azimuth"), { target: { value: "45" } });
    expect(shotNow()?.lighting).toEqual({
      preset: "practicals",
      rig: { ...PRACTICALS, lights: [{ ...PRACTICALS.lights[0], azimuth: 45 }, PRACTICALS.lights[1]] },
    });
    await waitFor(() => expect(within(picker).getByTestId("lighting-words")).toHaveTextContent("first at 45°"));
    await user.click(within(picker).getByTestId("lighting-quick-back"));
    expect(shotNow()?.lighting?.rig?.lights[0]).toMatchObject({ azimuth: 180, elevation: 15 });
    await user.click(within(picker).getByTestId("lighting-add"));
    expect(shotNow()?.lighting?.rig?.lights).toHaveLength(3);
    expect(screen.getByTestId("chip-lighting")).toHaveAccessibleName("Lighting: Practicals (edited)");

    // Saved, it reloads exactly.
    const rig = shotNow()!.lighting!.rig;
    await user.type(within(picker).getByTestId("lighting-save-name"), "Diner neon");
    await user.click(within(picker).getByTestId("lighting-save"));
    await waitFor(() => expect(shotNow()?.lighting).toEqual({ rig, label: "Diner neon" }));
    expect(requests.find((r) => r.method === "POST" && r.path === "/api/cinema/presets")?.body).toEqual({
      kind: "lighting",
      name: "Diner neon",
      data: rig,
    });
    await user.click(within(picker).getByTestId("lighting-preset-silhouette"));
    expect(shotNow()).toEqual({ lighting: { preset: "silhouette" } });
    await user.click(await within(picker).findByTestId("lighting-saved-l1"));
    expect(shotNow()?.lighting).toEqual({ rig, label: "Diner neon" });
    expect(within(picker).getByTestId("lighting-saved-l1")).toHaveAttribute("aria-checked", "true");
    await user.click(within(picker).getByTestId("lighting-done"));
    expect(screen.getByTestId("chip-lighting")).toHaveAccessibleName("Lighting: Diner neon");
  });
});
