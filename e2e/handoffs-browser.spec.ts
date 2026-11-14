import { expect, type Page, test } from "./fixtures";

// M2.9: viewer hand-offs and Auto, on the offline stand-ins.

async function generated(page: Page, prompt: string) {
  const card = page.getByTestId("generation-card").filter({ hasText: prompt });
  await expect(card).toHaveAttribute("data-status", "succeeded", { timeout: 30_000 });
  return card;
}

async function connect(backend: { api: (route: string, init?: RequestInit) => Promise<Response> }) {
  for (const provider of ["google", "byteplus"]) {
    const response = await backend.api(`/api/connections/${provider}`, {
      method: "PUT",
      body: JSON.stringify({ api_key: `fs-test-ok-${provider}-0123456789` }),
    });
    expect(response.status).toBe(200);
  }
}

test("M2.9: text → Nano Banana still → Turn to video → Seedance clip, from the UI", async ({ page, backend }) => {
  await connect(backend);
  await page.goto(`${backend.url}/auth/login?token=${backend.token}&next=/image`);
  await page.getByTestId("model-picker").click();
  await page.getByTestId("model-option-auto").click();
  await expect(page.getByTestId("model-picker")).toHaveText(/Auto · Nano Banana Pro/);
  await page.getByTestId("prompt-input").fill("a harbour at blue hour, handoff e2e");
  await page.getByTestId("generate-button").click();
  const card = await generated(page, "handoff e2e");

  await card.getByTestId("open-viewer").click();
  await page.getByTestId("handoff-video").click();
  await expect(page).toHaveURL(/\/video$/);
  await expect(page.getByTestId("model-picker")).toHaveText(/Auto · Seedance 2\.0/);
  await expect(page.getByTestId("start-frame")).toContainText("Start frame");
  await expect(page.getByTestId("prompt-input")).toHaveText("a harbour at blue hour, handoff e2e");
  await page.getByTestId("generate-button").click();
  await generated(page, "handoff e2e");

  const generations = await (await backend.api("/api/generations")).json();
  const still = generations.find((g: { mode: string }) => g.mode === "image");
  const clip = generations.find((g: { mode: string }) => g.mode === "video");
  expect(still.model_id).toBe("nano-banana-pro");
  expect(clip.model_id).toBe("seedance-2-0");
  expect(clip.start_frame).toBe(still.asset.id);
  expect(clip.asset.has_audio).toBe(true);
});

test("M2.9: Recreate reproduces the stored spec", async ({ page, backend }) => {
  await page.goto(`${backend.url}/auth/login?token=${backend.token}&next=/image`);
  await page.getByTestId("model-picker").click();
  await page.getByPlaceholder("Search models…").fill("Mock engine");
  await page.getByTestId("model-option-mock").click();
  await page.getByRole("group", { name: "Aspect ratio" }).getByRole("button", { name: "4:3" }).click();
  await page.getByTestId("more-settings").click();
  await page.getByTestId("seed-input").fill("424242");
  await page.keyboard.press("Escape");
  await page.getByTestId("prompt-input").fill("a recreated teapot, recreate e2e");
  await page.getByTestId("generate-button").click();
  const card = await generated(page, "recreate e2e");

  // Change everything, then Recreate from the viewer.
  await page.getByRole("group", { name: "Aspect ratio" }).getByRole("button", { name: "1:1" }).click();
  await page.getByTestId("more-settings").click();
  await page.getByTestId("seed-input").fill("");
  await page.keyboard.press("Escape");
  await page.getByTestId("prompt-input").fill("something else");
  await card.getByTestId("open-viewer").click();
  await page.getByTestId("handoff-recreate").click();
  await expect(page.getByTestId("prompt-input")).toHaveText("a recreated teapot, recreate e2e");
  await page.getByTestId("generate-button").click();
  await expect(page.getByTestId("generation-card").filter({ hasText: "recreate e2e" })).toHaveCount(2);
  await expect(page.getByTestId("generation-card").filter({ hasText: "recreate e2e" }).first()).toHaveAttribute(
    "data-status",
    "succeeded",
    { timeout: 20_000 },
  );

  const generations = (await (await backend.api("/api/generations")).json()).filter(
    (g: { prompt: string }) => g.prompt === "a recreated teapot, recreate e2e",
  );
  expect(generations).toHaveLength(2);
  const [again, first] = generations;
  for (const key of ["model_id", "prompt", "params", "seed", "references", "start_frame", "end_frame"]) {
    expect(again[key]).toEqual(first[key]);
  }
  expect(first.seed).toBe(424242);
  expect(first.params.aspect).toBe("4:3");
});

test("M2.9: Use as reference switches to a model that takes it", async ({ page, backend }) => {
  await page.goto(`${backend.url}/auth/login?token=${backend.token}&next=/image`);
  await page.getByTestId("model-picker").click();
  await page.getByPlaceholder("Search models…").fill("Mock engine");
  await page.getByTestId("model-option-mock").click();
  await page.getByTestId("prompt-input").fill("a reference card, reference e2e");
  await page.getByTestId("generate-button").click();
  const card = await generated(page, "reference e2e");
  await card.getByTestId("open-viewer").click();
  await page.getByTestId("handoff-reference").click();
  await expect(page.getByTestId("asset-viewer")).toHaveCount(0);
  // The mock engine takes no references, so Auto takes over with a model that
  // does (which one depends on what this worker's backend has connected).
  await expect(page.getByTestId("model-picker")).toHaveText(/^Auto · (Mock cloud|Nano Banana Pro)$/);
  await expect(page.getByTestId("reference-count")).toHaveText(/^References 1\/\d+$/);
});
