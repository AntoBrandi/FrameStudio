import { expect, type Page, test } from "./fixtures";

// M2.7 on the offline stand-in for Google (FRAMESTUDIO_FAKE_PROVIDERS): a
// still, then an edit with two reference images, land in the library with
// their lineage.

async function generate(page: Page, prompt: string) {
  await page.getByTestId("prompt-input").fill(prompt);
  await page.getByTestId("generate-button").click();
  const card = page.getByTestId("generation-card").filter({ has: page.getByRole("img", { name: prompt }) });
  await expect(card).toHaveAttribute("data-status", "succeeded", { timeout: 20_000 });
  return card;
}

test("M2.7: a Nano Banana still and an edit with two references", async ({ page, backend }) => {
  await backend.api("/api/connections/google", {
    method: "PUT",
    body: JSON.stringify({ api_key: "fs-test-ok-google-0123456789" }),
  });
  await page.goto(`${backend.url}/auth/login?token=${backend.token}&next=/image`);
  await page.getByTestId("model-picker").click();
  await page.getByPlaceholder("Search models…").fill("Nano Banana Pro");
  await page.getByTestId("model-option-nano-banana-pro").click();
  await expect(page.getByRole("group", { name: "Resolution" })).toBeVisible();
  await expect(page.getByTestId("reference-count")).toHaveText("References 0/14");
  await expect(page.getByTestId("estimate")).toContainText("about $0.13");
  await page.getByRole("group", { name: "Resolution" }).getByRole("button", { name: "4K" }).click();
  await expect(page.getByTestId("estimate")).toContainText("about $0.24");
  await page.getByRole("group", { name: "Resolution" }).getByRole("button", { name: "1K" }).click();

  await generate(page, "a lighthouse at dusk, nano e2e");
  await generate(page, "a stormy sea, nano e2e");

  // The edit: both stills as references.
  await page.getByTestId("pick-references").click();
  const picker = page.getByTestId("reference-picker");
  await picker.locator("button").nth(0).click();
  await picker.locator("button").nth(1).click();
  await page.getByTestId("add-references").click();
  await expect(page.getByTestId("reference-count")).toHaveText("References 2/14");
  const edit = await generate(page, "the lighthouse in the storm, nano e2e");

  await edit.getByTestId("open-viewer").click();
  const viewer = page.getByTestId("asset-viewer");
  await expect(viewer.getByTestId("lineage").locator("a")).toHaveCount(2);
  const generations = await (await backend.api("/api/generations")).json();
  const made = generations.find((g: { prompt: string }) => g.prompt === "the lighthouse in the storm, nano e2e");
  expect(made.model_id).toBe("nano-banana-pro");
  expect(made.provider).toBe("google");
  expect(made.references).toHaveLength(2);
});
