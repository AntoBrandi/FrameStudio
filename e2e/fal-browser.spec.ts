import { expect, type Page, test } from "./fixtures";

// M2.10 on the offline stand-ins: add a model from fal.ai in Settings, then
// the same still animates through a second video provider with only a model
// change in the composer.

async function pickModel(page: Page, name: string, id: string) {
  await page.getByTestId("model-picker").click();
  await page.getByPlaceholder("Search models…").fill(name);
  await page.getByTestId(`model-option-${id}`).click();
  await expect(page.getByTestId("model-picker")).toContainText(name);
}

test("M2.10: the same still through Seedance, then through fal", async ({ page, backend }) => {
  for (const provider of ["byteplus", "fal"]) {
    const response = await backend.api(`/api/connections/${provider}`, {
      method: "PUT",
      body: JSON.stringify({ api_key: `fs-test-ok-${provider}-0123456789` }),
    });
    expect(response.status).toBe(200);
  }
  await page.goto(`${backend.url}/auth/login?token=${backend.token}&next=/settings`);
  const falCard = page.getByTestId("connection-fal");
  await falCard.getByTestId("fal-search").fill("demo");
  await falCard.getByRole("button", { name: "Search" }).click();
  const result = falCard.getByTestId("fal-results").getByRole("listitem").filter({ hasText: "Demo Video" });
  await result.getByTestId("fal-add").click();
  await expect(result.getByTestId("fal-add")).toHaveText("Added");

  // A still to animate.
  await page.goto(`${backend.url}/image`);
  await pickModel(page, "Mock engine", "mock");
  await page.getByRole("group", { name: "Aspect ratio" }).getByRole("button", { name: "16:9" }).click();
  await page.getByTestId("prompt-input").fill("a cliff at dawn, fal e2e still");
  await page.getByTestId("generate-button").click();
  await expect(page.getByTestId("generation-card").filter({ hasText: "fal e2e still" })).toHaveAttribute(
    "data-status",
    "succeeded",
    { timeout: 20_000 },
  );

  await page.goto(`${backend.url}/video`);
  await pickModel(page, "Seedance 2.0", "seedance-2-0");
  await page.getByTestId("start-frame").click();
  await page.getByTestId("reference-picker").locator("button").first().click();
  await page.getByTestId("add-references").click();
  await page.getByTestId("prompt-input").fill("the mist lifts, fal e2e clip");
  await page.getByTestId("generate-button").click();
  // Only the model changes; the start frame and prompt stay.
  await pickModel(page, "Demo Video (fal)", "fal-demo-video-image-to-video");
  await expect(page.getByTestId("start-frame")).toContainText("Start frame");
  await expect(page.getByTestId("estimate")).toContainText("about $0.40");
  await page.getByTestId("generate-button").click();
  const clips = page.getByTestId("generation-card").filter({ hasText: "fal e2e clip" });
  await expect(clips).toHaveCount(2);
  for (const index of [0, 1]) {
    await expect(clips.nth(index)).toHaveAttribute("data-status", "succeeded", { timeout: 30_000 });
  }
  const generations = (await (await backend.api("/api/generations")).json()).filter(
    (g: { prompt: string }) => g.prompt === "the mist lifts, fal e2e clip",
  );
  expect(new Set(generations.map((g: { provider: string }) => g.provider))).toEqual(new Set(["byteplus", "fal"]));
  expect(generations[0].start_frame).toBe(generations[1].start_frame);
});
