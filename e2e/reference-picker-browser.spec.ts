import { expect, type Page, png, test } from "./fixtures";

// M4.5: references from any source in one picker, with a counter that stops
// at the model's limit, and a new element made without leaving it.

async function pickModel(page: Page, name: string, id: string) {
  await page.getByTestId("model-picker").click();
  await page.getByPlaceholder("Search models…").fill(name);
  await page.getByTestId(`model-option-${id}`).click();
  await expect(page.getByTestId("model-picker")).toContainText(name);
}

test("M4.5: pick pictures and elements from every source; the counter stops overflow", async ({ page, backend }) => {
  const run = Array.from({ length: 5 }, () => "abcdefghijklmnopqrstuvwxyz"[Math.floor(Math.random() * 26)]).join("");
  await backend.api("/api/connections/google", {
    method: "PUT",
    body: JSON.stringify({ api_key: "fs-test-ok-google-0123456789" }),
  });
  const ids: Record<string, string> = {};
  for (const [name, colour] of [
    [`pk${run}-a.png`, [200, 40, 40]],
    [`pk${run}-b.png`, [40, 200, 40]],
    [`pk${run}-c.png`, [40, 40, 200]],
    [`pk${run}-lamp.png`, [220, 200, 60]],
  ] as const) {
    const form = new FormData();
    form.append("files", new Blob([png(...colour)], { type: "image/png" }), name);
    const response = await fetch(`${backend.url}/api/assets/upload`, {
      method: "POST",
      headers: { Authorization: `Bearer ${backend.token}` },
      body: form,
    });
    ids[name] = (await response.json())[0].asset.id;
  }
  const lamp = await (
    await backend.api("/api/elements", {
      method: "POST",
      body: JSON.stringify({ name: `Pk${run} Lamp`, category: "prop", asset_ids: [ids[`pk${run}-lamp.png`]] }),
    })
  ).json();

  await page.goto(`${backend.url}/auth/login?token=${backend.token}&next=/image`);
  await pickModel(page, "Nano Banana", "nano-banana"); // takes 3 reference images
  await page.getByTestId("prompt-input").click();
  await page.keyboard.type(`A still life, picker e2e ${run}`);

  await page.getByTestId("pick-references").click();
  const counter = page.getByTestId("picker-counter");
  await expect(counter).toHaveText("0/3 pictures");
  await page.getByTestId("picker-tab-uploads").click();
  await page.getByTestId("picker-search").fill(`pk${run}`);
  const grid = page.getByTestId("reference-picker");
  await grid.getByRole("button", { name: `pk${run}-a.png` }).click();
  await grid.getByRole("button", { name: `pk${run}-b.png` }).click();
  await expect(counter).toHaveText("2/3 pictures");

  // An element with a picture takes the last place.
  await page.getByTestId("picker-tab-elements").click();
  await page.getByTestId("picker-search").fill(`pk${run}`);
  await page.getByRole("button", { name: `@${lamp.handle}` }).click();
  await expect(counter).toHaveText("3/3 pictures · the model's limit");
  await page.getByTestId("picker-tab-uploads").click();
  await page.getByTestId("picker-search").fill(`pk${run}`);
  await expect(grid.getByRole("button", { name: `pk${run}-c.png` })).toBeDisabled();
  await page.getByTestId("add-references").click();

  await expect(page.getByTestId("reference-count")).toHaveText("References 2/3");
  const chip = page.getByTestId("prompt-input").getByTestId("prompt-token");
  await expect(chip).toHaveText(`@${lamp.handle}`);
  await page.getByTestId("request-preview-button").click();
  await expect(page.getByTestId("request-pictures").getByRole("listitem")).toHaveCount(3);
  await page.keyboard.press("Escape");

  // A new element made inside the picker goes straight into the prompt.
  await page.getByTestId("pick-elements").click();
  await page.getByTestId("picker-new-element").click();
  await page.getByTestId("element-name").fill(`Pk${run} Window`);
  await page.getByTestId("element-save").click();
  await expect(page.getByRole("button", { name: `@pk${run}-window` })).toHaveAttribute("aria-pressed", "true");
  await page.getByTestId("add-references").click();
  await expect(chip).toHaveCount(2);
  await expect(chip.nth(1)).toHaveText(`@pk${run}-window`);

  await page.getByTestId("generate-button").click();
  const card = page
    .getByTestId("generation-card")
    .filter({ hasText: `picker e2e ${run}` })
    .first();
  await expect(card).toHaveAttribute("data-status", "succeeded", { timeout: 30_000 });
  const generations = await (await backend.api("/api/generations")).json();
  const made = generations.find((g: { prompt: string }) => g.prompt.includes(`picker e2e ${run}`));
  expect(made.references).toEqual([ids[`pk${run}-a.png`], ids[`pk${run}-b.png`]]);
  expect(made.resolved.references.map((r: { asset_id: string }) => r.asset_id)).toEqual([
    ids[`pk${run}-a.png`],
    ids[`pk${run}-b.png`],
    ids[`pk${run}-lamp.png`],
  ]);
});
