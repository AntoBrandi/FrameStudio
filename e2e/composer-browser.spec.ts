import { expect, type Page, png, test } from "./fixtures";

// M2.5: the composer draws its controls from each model's schema, shows an
// estimate, takes references, and cannot be set to a combination a model refuses.

async function pickModel(page: Page, name: string, id: string) {
  await page.getByTestId("model-picker").click();
  await page.getByPlaceholder("Search models…").fill(name);
  await page.getByTestId(`model-option-${id}`).click();
  await expect(page.getByTestId("model-picker")).toContainText(name);
}

test("M2.5: a hosted model with an estimate and a reference image", async ({ page, backend }) => {
  const form = new FormData();
  form.append("files", new Blob([png(220, 30, 30)], { type: "image/png" }), "red-reference.png");
  const uploaded = await fetch(`${backend.url}/api/assets/upload`, {
    method: "POST",
    headers: { Authorization: `Bearer ${backend.token}` },
    body: form,
  });
  expect(uploaded.status).toBe(200);

  await page.goto(`${backend.url}/auth/login?token=${backend.token}&next=/image`);
  await expect(page.getByTestId("model-picker")).toContainText("Mock engine");
  await expect(page.getByTestId("reference-tray")).toHaveCount(0); // the local mock takes none

  await pickModel(page, "Mock cloud", "mock-cloud");
  await expect(page.getByTestId("estimate")).toContainText("about $0.01");
  await page.getByRole("button", { name: "Batch size +" }).click();
  await expect(page.getByTestId("estimate")).toContainText("about $0.02");
  await page.getByRole("button", { name: "Batch size −" }).click();

  await expect(page.getByTestId("reference-count")).toHaveText("References 0/4");
  await page.getByTestId("pick-references").click();
  await page.getByTestId("reference-picker").getByRole("button", { name: "red-reference.png" }).click();
  await page.getByTestId("add-references").click();
  await expect(page.getByTestId("reference-count")).toHaveText("References 1/4");

  await page.getByRole("group", { name: "Aspect ratio" }).getByRole("button", { name: "16:9" }).click();
  await page.getByTestId("prompt-input").fill("a lighthouse with a red reference, e2e");
  await page.getByTestId("generate-button").click();
  const card = page
    .getByTestId("generation-card")
    .filter({ has: page.getByRole("img", { name: "a lighthouse with a red reference, e2e" }) });
  await expect(card).toHaveAttribute("data-status", "succeeded", { timeout: 20_000 });

  const generations = await (await backend.api("/api/generations")).json();
  const made = generations.find((g: { prompt: string }) => g.prompt === "a lighthouse with a red reference, e2e");
  expect(made.model_id).toBe("mock-cloud");
  expect(made.references).toHaveLength(1);
  expect(made.params.aspect).toBe("16:9");
});

test("M2.5: video settings the model refuses cannot be picked", async ({ page, backend }) => {
  await page.goto(`${backend.url}/auth/login?token=${backend.token}&next=/video`);
  await expect(page.getByTestId("model-picker")).toContainText("Mock engine");
  const aspect = page.getByRole("group", { name: "Aspect ratio" });
  const duration = page.getByRole("group", { name: "Duration" });
  // Push the clip past 10 s: 21:9 (at most 10 s on the mock) becomes unavailable.
  while (!(await duration.textContent())?.includes("12s")) {
    await duration.getByRole("button", { name: "Duration +" }).click();
  }
  const wide = aspect.getByRole("button", { name: "21:9" });
  await expect(wide).toHaveAttribute("aria-disabled", "true");
  await expect(wide).toHaveAttribute("title", /10 seconds/);
  // Back to 10 s, pick 21:9, and the duration cannot go up again.
  await duration.getByRole("button", { name: "Duration −" }).click();
  await duration.getByRole("button", { name: "Duration −" }).click();
  await wide.click();
  await expect(wide).toHaveAttribute("aria-pressed", "true");
  await expect(duration.getByRole("button", { name: "Duration +" })).toBeDisabled();
  await expect(page.getByTestId("composer-problems")).toHaveCount(0);
  // The mode toggle moves to the Image page.
  await page.getByRole("group", { name: "Mode" }).getByRole("link", { name: "Image" }).click();
  await expect(page).toHaveURL(/\/image$/);
});
