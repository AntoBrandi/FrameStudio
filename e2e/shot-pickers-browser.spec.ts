import { expect, type Page, test } from "./fixtures";

// M5.2: the film and camera pickers write to the shot, work from the
// keyboard, show as chips above the prompt, and reach the model's prompt.

async function pickMock(page: Page) {
  await page.getByTestId("model-picker").click();
  await page.getByPlaceholder("Search models…").fill("Mock engine");
  await page.getByTestId("model-option-mock").click();
}

test("M5.2: film and camera pickers from the keyboard, into the prompt", async ({ page, backend }) => {
  const run = Array.from({ length: 5 }, () => "abcdefghijklmnopqrstuvwxyz"[Math.floor(Math.random() * 26)]).join("");
  await page.goto(`${backend.url}/auth/login?token=${backend.token}&next=/video`);
  await pickMock(page);
  await page.getByTestId("prompt-input").click();
  await page.keyboard.type(`A diner at night, pickers e2e ${run}`);

  // Film: open the chip with the keyboard, choose with the arrows.
  await page.getByTestId("chip-film").focus();
  await page.keyboard.press("Enter");
  const film = page.getByTestId("film-picker");
  await expect(film).toBeVisible();
  await film.getByRole("radiogroup", { name: "Genre" }).getByRole("radio", { name: "None" }).focus();
  await page.keyboard.press("ArrowRight"); // Noir
  await page.keyboard.press("Tab");
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowRight"); // 1970s
  await page.keyboard.press("Tab");
  await page.keyboard.press("ArrowRight"); // Calm
  await expect(film.getByRole("radio", { name: "Noir" })).toHaveAttribute("aria-checked", "true");
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("chip-film")).toHaveAccessibleName("Film: Noir, 1970s, Calm");

  // Camera: turn the drums.
  await page.getByTestId("chip-camera").click();
  const camera = page.getByTestId("camera-picker");
  await camera.getByRole("listbox", { name: "Camera" }).focus();
  await page.keyboard.press("ArrowDown"); // 35mm film
  await camera.getByRole("listbox", { name: "Lens" }).focus();
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("ArrowDown"); // Anamorphic
  await camera.getByRole("listbox", { name: "Aperture" }).focus();
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("ArrowDown"); // f/1.4
  await camera.getByTestId("shot-done").click();
  await expect(page.getByTestId("chip-camera")).toHaveAccessibleName("Camera: 35mm film, Anamorphic, f/1.4");

  // What the model sees.
  await page.getByTestId("request-preview-button").click();
  const sections = page.getByTestId("request-sections");
  await expect(sections.locator('[data-family="camera"]')).toContainText("shot on 35mm film, an anamorphic lens");
  await expect(sections.locator('[data-family="film"]').first()).toContainText("film noir");
  await page.keyboard.press("Escape");

  await page.getByTestId("generate-button").click();
  const card = page
    .getByTestId("generation-card")
    .filter({ hasText: `pickers e2e ${run}` })
    .first();
  await expect(card).toHaveAttribute("data-status", "succeeded", { timeout: 30_000 });
  const generations = await (await backend.api("/api/generations")).json();
  const made = generations.find((g: { prompt: string }) => g.prompt.includes(`pickers e2e ${run}`));
  expect(made.shot).toMatchObject({
    film: { genre: "noir", era: "1970s", tempo: "calm" },
    camera: { body: "film-35", lens: "anamorphic", aperture: "f/1.4" },
  });
  expect(made.resolved.prompt).toContain("Camera: shot on 35mm film");

  // Clearing a chip clears its part of the shot; the choices survive a reload.
  await page.getByTestId("chip-camera-clear").click();
  await expect(page.getByTestId("chip-camera")).toHaveAccessibleName("Camera");
  await page.reload();
  await expect(page.getByTestId("chip-film")).toHaveAccessibleName("Film: Noir, 1970s, Calm");
});
