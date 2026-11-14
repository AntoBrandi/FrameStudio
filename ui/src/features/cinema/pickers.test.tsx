import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { makeQueryClient } from "@/app/App";
import { mockApi } from "@/test/api-mock";
import { cleanShot, type ShotSpec } from "./shot";
import { ShotChips } from "./ShotPickers";

// M5.2: every picker writes to the ShotSpec and works from the keyboard.

const term = (id: string, label: string) => ({ id, label, phrase: "", tags: "", negative: null });
const VOCAB = {
  genres: [term("noir", "Noir"), term("western", "Western"), term("drama", "Drama")],
  eras: [term("1920s", "1920s"), term("1970s", "1970s")],
  tempos: [term("calm", "Calm"), term("frantic", "Frantic")],
  bodies: [term("film-35", "35mm film"), term("vhs", "VHS camcorder")],
  lenses: [term("spherical", "Spherical prime"), term("anamorphic", "Anamorphic")],
  apertures: ["f/1.4", "f/2", "f/2.8"],
  focal_lengths: [24, 50, 85],
  moves: [],
  lighting: [],
  palettes: [],
  previews: { genres: { noir: { image: "/api/cinema/previews/genres/noir.webp", video: null } } },
};

function Harness({ video = false, initial = null }: { video?: boolean; initial?: ShotSpec | null }) {
  const [shot, setShot] = useState<ShotSpec | null>(initial);
  return (
    <QueryClientProvider client={makeQueryClient()}>
      <ShotChips shot={shot} onChange={setShot} video={video} />
      <output data-testid="shot">{JSON.stringify(shot)}</output>
    </QueryClientProvider>
  );
}

const shotNow = () => JSON.parse(screen.getByTestId("shot").textContent!) as ShotSpec | null;

describe("shot pickers", () => {
  it("film: carousels work from the keyboard and write the shot", async () => {
    mockApi({ "GET /api/cinema/vocab": VOCAB });
    const user = userEvent.setup();
    render(<Harness video />);
    const chip = await screen.findByTestId("chip-film");
    chip.focus();
    await user.keyboard("{Enter}");
    const picker = await screen.findByTestId("film-picker");
    const genres = within(picker).getByRole("radiogroup", { name: "Genre" });
    // "None" is checked and focusable first; the arrows move and choose.
    const none = within(genres).getByRole("radio", { name: "None" });
    expect(none).toHaveAttribute("aria-checked", "true");
    await user.click(none);
    await user.keyboard("{ArrowRight}");
    expect(shotNow()).toEqual({ film: { genre: "noir" } });
    expect(within(genres).getByRole("radio", { name: "Noir" })).toHaveFocus();
    await user.keyboard("{End}");
    expect(shotNow()).toEqual({ film: { genre: "drama" } });
    await user.keyboard("{ArrowRight}"); // wraps to None
    expect(shotNow()).toBeNull();
    await user.keyboard("{ArrowLeft}");
    // Tab leaves the group, into the next one.
    await user.tab();
    expect(within(picker).getByRole("radiogroup", { name: "Era" })).toContainElement(
      document.activeElement as HTMLElement,
    );
    await user.keyboard("{ArrowRight}{ArrowRight}");
    await user.tab();
    await user.keyboard("{ArrowRight}");
    expect(shotNow()).toEqual({ film: { genre: "drama", era: "1970s", tempo: "calm" } });
    // The generated still is shown where it exists; a drawn card otherwise.
    const noir = within(genres).getByRole("radio", { name: "Noir" });
    expect(noir.querySelector("img")).toHaveAttribute("src", "/api/cinema/previews/genres/noir.webp");
    expect(
      within(genres).getByRole("radio", { name: "Western" }).querySelector('[data-art="placeholder"]'),
    ).not.toBeNull();
    await user.click(within(picker).getByTestId("shot-done"));
    expect(screen.getByTestId("chip-film")).toHaveAccessibleName("Film: Drama, 1970s, Calm");
  });

  it("camera: drums turn with the arrow keys and the wheel", async () => {
    mockApi({ "GET /api/cinema/vocab": VOCAB });
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(await screen.findByTestId("chip-camera"));
    const picker = await screen.findByTestId("camera-picker");
    const body = within(picker).getByRole("listbox", { name: "Camera" });
    body.focus();
    await user.keyboard("{ArrowDown}");
    const lens = within(picker).getByRole("listbox", { name: "Lens" });
    lens.focus();
    await user.keyboard("{End}");
    within(picker).getByRole("listbox", { name: "Focal length" }).focus();
    await user.keyboard("{ArrowDown}{ArrowDown}");
    within(picker).getByRole("listbox", { name: "Aperture" }).focus();
    await user.keyboard("{PageDown}");
    expect(shotNow()).toEqual({
      camera: { body: "film-35", lens: "anamorphic", focal_length: 50, aperture: "f/2.8" },
    });
    expect(within(lens).getByRole("option", { name: "Anamorphic" })).toHaveAttribute("aria-selected", "true");
    expect(lens).toHaveAttribute("aria-activedescendant", within(lens).getByRole("option", { name: "Anamorphic" }).id);
    // A click picks too.
    await user.click(within(body).getByRole("option", { name: "VHS camcorder" }));
    expect(shotNow()?.camera?.body).toBe("vhs");
    await user.click(within(picker).getByTestId("shot-reset"));
    expect(shotNow()).toBeNull();
  });

  it("chips show the choices, clear them, and pace only for video", async () => {
    mockApi({ "GET /api/cinema/vocab": VOCAB });
    const user = userEvent.setup();
    const initial = {
      film: { genre: "noir", era: "1920s", tempo: "calm" },
      camera: { body: "film-35", aperture: "f/1.4" },
      moves: ["orbit"],
    } as ShotSpec;
    render(<Harness initial={initial} />);
    await screen.findByRole("button", { name: "Film: Noir, 1920s" });
    expect(screen.getByTestId("chip-camera")).toHaveAccessibleName("Camera: 35mm film, f/1.4");
    await user.click(screen.getByTestId("chip-film"));
    expect(within(await screen.findByTestId("film-picker")).queryByRole("radiogroup", { name: "Pace" })).toBeNull();
    await user.keyboard("{Escape}");
    await user.click(screen.getByTestId("chip-film-clear"));
    expect(shotNow()).toEqual({ camera: { body: "film-35", aperture: "f/1.4" }, moves: ["orbit"] });
  });

  it("cleanShot drops empty parts", () => {
    expect(cleanShot({ film: { genre: null, era: null }, camera: {}, moves: [] } as unknown as ShotSpec)).toBeNull();
    expect(cleanShot({ film: { genre: "noir", era: null } } as unknown as ShotSpec)).toEqual({
      film: { genre: "noir" },
    });
  });
});
