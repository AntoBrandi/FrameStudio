import { expect, type Page, test } from "./fixtures";

// M5.3: moves insert as ordered chips and compile differently by model.

async function pickModel(page: Page, name: string, id: string) {
  await page.getByTestId("model-picker").click();
  await page.getByPlaceholder("Search models…").fill(name);
  await page.getByTestId(`model-option-${id}`).click();
  await expect(page.getByTestId("model-picker")).toContainText(name);
}

async function movesSection(page: Page) {
  await page.getByTestId("request-preview-button").click();
  const section = page.getByTestId("request-sections").locator('[data-family="moves"]');
  const text = (await section.textContent()) ?? "";
  await page.keyboard.press("Escape");
  return text;
}

test("M5.3: #moves as ordered chips, worded per model", async ({ page, backend }) => {
  const run = Array.from({ length: 5 }, () => "abcdefghijklmnopqrstuvwxyz"[Math.floor(Math.random() * 26)]).join("");
  await backend.api("/api/connections/byteplus", {
    method: "PUT",
    body: JSON.stringify({ api_key: "fs-test-ok-byteplus-0123456789" }),
  });
  await page.goto(`${backend.url}/auth/login?token=${backend.token}&next=/video`);
  await pickModel(page, "Mock engine", "mock");
  const input = page.getByTestId("prompt-input");
  await input.click();
  await page.keyboard.type(`Rain on the diner window, moves e2e ${run} #dol`);
  const menu = page.getByTestId("move-suggestions");
  await expect(menu.locator('[data-move="dolly-in"]')).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(input.locator('[data-kind="move"]')).toHaveText("#dolly-in");

  // The library adds a move after it; reorder so the orbit comes first.
  await page.getByTestId("chip-moves").click();
  const picker = page.getByTestId("moves-picker");
  await picker.getByTestId("moves-search").fill("orbit");
  await picker.getByRole("button", { name: "Add Drone orbit" }).click();
  await picker.getByRole("button", { name: "Move Drone orbit earlier" }).click();
  await expect(picker.getByTestId("moves-in-shot")).toHaveText(/1\. Drone orbit.*2\. Dolly in/);
  await picker.getByTestId("moves-done").click();
  await expect(page.getByTestId("chip-moves")).toHaveAccessibleName("Moves: Drone orbit → Dolly in");
  await expect(input.locator('[data-kind="move"]')).toHaveText(["#orbit", "#dolly-in"]);

  // The same moves, worded for each model.
  expect(await movesSection(page)).toContain(
    "the camera orbits a full circle around the subject, then the camera dollies in toward the subject",
  );
  await pickModel(page, "Seedance 2.0", "seedance-2-0");
  expect(await movesSection(page)).toContain(
    "the camera orbits around the subject, then the camera pushes in toward the subject",
  );

  await pickModel(page, "Mock engine", "mock");
  await page.getByTestId("generate-button").click();
  const card = page
    .getByTestId("generation-card")
    .filter({ hasText: `moves e2e ${run}` })
    .first();
  await expect(card).toHaveAttribute("data-status", "succeeded", { timeout: 30_000 });
  const generations = await (await backend.api("/api/generations")).json();
  const made = generations.find((g: { prompt: string }) => g.prompt.includes(`moves e2e ${run}`));
  const moves = made.prompt_tokens
    .filter((t: { type: string }) => t.type === "move")
    .map((t: { name: string }) => t.name);
  expect(moves).toEqual(["orbit", "dolly-in"]);
  expect(made.resolved.prompt).toContain("Camera movement: the camera orbits a full circle");
});
