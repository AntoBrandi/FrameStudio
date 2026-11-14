import { expect, type Page, test } from "./fixtures";

// M2.8 "Done when", on the offline stand-ins for Google and BytePlus
// (FRAMESTUDIO_FAKE_PROVIDERS): a Nano Banana still animates into a Seedance
// clip with sound.

async function pickModel(page: Page, name: string, id: string) {
  await page.getByTestId("model-picker").click();
  await page.getByPlaceholder("Search models…").fill(name);
  await page.getByTestId(`model-option-${id}`).click();
  await expect(page.getByTestId("model-picker")).toContainText(name);
}

test("M2.8: a Nano Banana still becomes a Seedance clip with sound", async ({ page, backend }) => {
  for (const provider of ["google", "byteplus"]) {
    const response = await backend.api(`/api/connections/${provider}`, {
      method: "PUT",
      body: JSON.stringify({ api_key: `fs-test-ok-${provider}-0123456789` }),
    });
    expect(response.status).toBe(200);
  }
  await page.goto(`${backend.url}/auth/login?token=${backend.token}&next=/image`);
  await pickModel(page, "Nano Banana Pro", "nano-banana-pro");
  await page.getByRole("group", { name: "Aspect ratio" }).getByRole("button", { name: "16:9" }).click();
  await page.getByTestId("prompt-input").fill("a lighthouse at dusk, seedance e2e still");
  await page.getByTestId("generate-button").click();
  await expect(
    page
      .getByTestId("generation-card")
      .filter({ has: page.getByRole("img", { name: "a lighthouse at dusk, seedance e2e still" }) }),
  ).toHaveAttribute("data-status", "succeeded", { timeout: 20_000 });

  await page.getByRole("group", { name: "Mode" }).getByRole("link", { name: "Video" }).click();
  await pickModel(page, "Seedance 2.0", "seedance-2-0");
  await expect(page.getByTestId("estimate")).toContainText("about $0.75");
  await expect(page.getByTestId("end-frame")).toHaveAttribute("aria-disabled", "true");
  await page.getByTestId("start-frame").click();
  await page.getByTestId("reference-picker").locator("button").first().click();
  await page.getByTestId("add-references").click();
  await expect(page.getByTestId("start-frame")).toContainText("Start frame");
  await expect(page.getByTestId("reference-tray")).toHaveCount(0); // frames and references don't mix
  await expect(page.getByRole("button", { name: /^Audio on/ })).toHaveAttribute("aria-pressed", "true");

  await page.getByTestId("prompt-input").fill("waves crash, the light turns, seedance e2e clip");
  await page.getByTestId("generate-button").click();
  const card = page.getByTestId("generation-card").filter({ hasText: "seedance e2e clip" });
  await expect(card).toHaveAttribute("data-status", "succeeded", { timeout: 30_000 });

  const generations = await (await backend.api("/api/generations")).json();
  const still = generations.find((g: { prompt: string }) => g.prompt.endsWith("seedance e2e still"));
  const clip = generations.find((g: { prompt: string }) => g.prompt.endsWith("seedance e2e clip"));
  expect(clip.model_id).toBe("seedance-2-0");
  expect(clip.provider).toBe("byteplus");
  expect(clip.start_frame).toBe(still.asset.id);
  expect(clip.asset.kind).toBe("video");
  expect(clip.asset.has_audio).toBe(true);
});
