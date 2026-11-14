import { expect, test } from "./fixtures";

// M5.5: lighting presets and the rig editor: a saved rig reloads exactly
// and changes the compiled prompt.

test("M5.5: edit a rig on the sphere, save it, reload it, into the prompt", async ({ page, backend }) => {
  const run = Array.from({ length: 5 }, () => "abcdefghijklmnopqrstuvwxyz"[Math.floor(Math.random() * 26)]).join("");
  const name = `Diner ${run}`;
  await page.goto(`${backend.url}/auth/login?token=${backend.token}&next=/image`);
  await page.getByTestId("chip-lighting").click();
  const picker = page.getByTestId("lighting-picker");
  await picker.getByTestId("lighting-preset-practicals").click();
  await expect(picker.getByTestId("lighting-words")).toContainText("warm amber key light from the front left");

  // Put the first light in front, then drag it to the right on the sphere.
  await picker.getByTestId("lighting-quick-front").click();
  await expect(picker.getByTestId("lighting-azimuth")).toHaveValue("0");
  const sphere = picker.getByTestId("light-sphere");
  await expect(sphere.locator("canvas")).toBeVisible();
  const box = (await sphere.boundingBox())!;
  // A light at azimuth 0, elevation 10 sits just above the centre of the view.
  await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.447);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.62, box.y + box.height * 0.45, { steps: 6 });
  await page.mouse.move(box.x + box.width * 0.75, box.y + box.height * 0.45, { steps: 6 });
  await page.mouse.up();
  const azimuth = Number(await picker.getByTestId("lighting-azimuth").inputValue());
  expect(azimuth).toBeGreaterThan(20);
  expect(azimuth).toBeLessThan(90);
  await expect(picker.getByTestId("lighting-words")).toContainText("from the front right");
  await picker.getByTestId("lighting-softness").fill("0.1");
  // The words come back from the backend a moment after the edit.
  await expect(picker.getByTestId("lighting-words")).toContainText("hard warm amber key light from the front right");
  const words = (await picker.getByTestId("lighting-words").textContent())!.replace(/^In the prompt:\s*/, "");

  await picker.getByTestId("lighting-save-name").fill(name);
  await picker.getByTestId("lighting-save").click();
  await expect(picker.getByRole("radio", { name })).toHaveAttribute("aria-checked", "true");

  // Another preset, then the saved rig again: it comes back exactly.
  await picker.getByTestId("lighting-preset-silhouette").click();
  await expect(picker.getByTestId("lighting-words")).toContainText("silhouette");
  await picker.getByRole("radio", { name }).click();
  await expect(picker.getByTestId("lighting-words")).toHaveText(`In the prompt: ${words}`);
  await picker.getByTestId("lighting-done").click();
  await expect(page.getByTestId("chip-lighting")).toHaveAccessibleName(`Lighting: ${name}`);

  // The same words reach the model.
  await page.getByTestId("prompt-input").click();
  await page.keyboard.type(`A woman at a diner counter, lighting e2e ${run}`);
  await page.getByTestId("request-preview-button").click();
  await expect(page.getByTestId("request-sections").locator('[data-family="lighting"]')).toContainText(
    `Lighting: ${words}`,
  );
  await page.keyboard.press("Escape");
  await page.getByTestId("generate-button").click();
  const card = page
    .getByTestId("generation-card")
    .filter({ hasText: `lighting e2e ${run}` })
    .first();
  await expect(card).toHaveAttribute("data-status", "succeeded", { timeout: 30_000 });
  const presets = await (await backend.api("/api/cinema/presets?kind=lighting")).json();
  const saved = presets.find((p: { name: string }) => p.name === name);
  const generations = await (await backend.api("/api/generations")).json();
  const made = generations.find((g: { prompt: string }) => g.prompt.includes(`lighting e2e ${run}`));
  expect(made.shot.lighting.rig).toEqual(saved.data);
  expect(made.resolved.prompt).toContain(`Lighting: ${words}`);
});
