import { expect, type Page, png, test } from "./fixtures";

// M4.4: an element named in the prompt sends its pictures to a model that
// takes references, and the composer shows what the model will see first.

async function pickModel(page: Page, name: string, id: string) {
  await page.getByTestId("model-picker").click();
  await page.getByPlaceholder("Search models…").fill(name);
  await page.getByTestId(`model-option-${id}`).click();
  await expect(page.getByTestId("model-picker")).toContainText(name);
}

test("M4.4: the preview shows the element's picture; the generation sends it", async ({ page, backend }) => {
  // A name of its own per run: other tests share this backend.
  const run = Array.from({ length: 5 }, () => "abcdefghijklmnopqrstuvwxyz"[Math.floor(Math.random() * 26)]).join("");
  const name = `Rf${run} Harbour`;
  const google = await backend.api("/api/connections/google", {
    method: "PUT",
    body: JSON.stringify({ api_key: "fs-test-ok-google-0123456789" }),
  });
  expect(google.status).toBe(200);
  const form = new FormData();
  form.append("files", new Blob([png(30, 90, 200)], { type: "image/png" }), "harbour-front.png");
  const upload = await fetch(`${backend.url}/api/assets/upload`, {
    method: "POST",
    headers: { Authorization: `Bearer ${backend.token}` },
    body: form,
  });
  const picture = (await upload.json())[0].asset.id as string;
  const created = await backend.api("/api/elements", {
    method: "POST",
    body: JSON.stringify({
      name,
      category: "location",
      description: "Stone quay, fishing boats, grey morning light.",
      asset_ids: [picture],
    }),
  });
  const harbour = await created.json();

  await page.goto(`${backend.url}/auth/login?token=${backend.token}&next=/image`);
  await pickModel(page, "Nano Banana Pro", "nano-banana-pro");
  const input = page.getByTestId("prompt-input");
  await input.click();
  await page.keyboard.type(`Wide shot of @rf${run}-harb`);
  await expect(page.getByTestId("element-suggestions").locator(`[data-handle="${harbour.handle}"]`)).toBeVisible();
  await page.keyboard.press("Enter");
  await page.keyboard.type("at dawn, refs e2e");

  await page.getByTestId("request-preview-button").click();
  const preview = page.getByTestId("request-preview");
  await expect(page.getByTestId("request-strategy")).toHaveAttribute("data-strategy", "multi_reference");
  await expect(page.getByTestId("request-prompt")).toHaveText(
    `Wide shot of ${name} (image 1) at dawn, refs e2e\n\n${name}: Stone quay, fishing boats, grey morning light.`,
  );
  const shown = preview.getByTestId("request-pictures").getByRole("listitem");
  await expect(shown).toHaveCount(1);
  await expect(shown.first()).toContainText("image 1");
  await expect(shown.first()).toContainText(`@${harbour.handle}`);
  await page.keyboard.press("Escape");

  await page.getByTestId("generate-button").click();
  const card = page.getByTestId("generation-card").filter({ hasText: "refs e2e" }).first();
  await expect(card).toHaveAttribute("data-status", "succeeded", { timeout: 30_000 });
  const generations = await (await backend.api("/api/generations")).json();
  const made = generations.find((g: { prompt: string }) => g.prompt.includes(`@${harbour.handle} `));
  expect(made.resolved.strategy).toBe("multi_reference");
  expect(made.resolved.references.map((r: { asset_id: string }) => r.asset_id)).toEqual([picture]);
  expect(made.references).toEqual([]); // nothing was attached by hand

  // A model that takes no pictures: the element goes in words.
  await pickModel(page, "Mock engine", "mock");
  await page.getByTestId("request-preview-button").click();
  await expect(page.getByTestId("request-strategy")).toHaveAttribute("data-strategy", "description");
  await expect(page.getByTestId("request-prompt")).toContainText(`Wide shot of ${name} at dawn`);
  await expect(page.getByTestId("request-preview")).toContainText("No pictures.");
});
