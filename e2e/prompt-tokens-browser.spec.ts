import { expect, type Page, test } from "./fixtures";

// M4.3: typing @ suggests elements, picking one makes a chip, pasted prompts
// become chips, unknown handles warn, and the tokens survive a generation
// and Recreate.

async function pickMock(page: Page) {
  await page.getByTestId("model-picker").click();
  await page.getByPlaceholder("Search models…").fill("Mock engine");
  await page.getByTestId("model-option-mock").click();
}

/** A paste with this plain text, as the browser sends it. */
async function paste(page: Page, text: string) {
  await page.getByTestId("prompt-input").evaluate((element, value) => {
    const data = new DataTransfer();
    data.setData("text/plain", value);
    element.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
  }, text);
}

test("M4.3: @ suggests elements, chips go into the prompt and come back on Recreate", async ({ page, backend }) => {
  const run = Array.from({ length: 5 }, () => "abcdefghijklmnopqrstuvwxyz"[Math.floor(Math.random() * 26)]).join("");
  const created = await backend.api("/api/elements", {
    method: "POST",
    // A name of its own per run: other tests share this backend.
    body: JSON.stringify({ name: `Tk${run} Maya`, category: "character", status: "ready" }),
  });
  expect(created.status).toBe(201);
  const maya = await created.json();
  const h = maya.handle as string;
  expect(h).toBe(`tk${run}-maya`);

  await page.goto(`${backend.url}/auth/login?token=${backend.token}&next=/image`);
  await pickMock(page);
  const input = page.getByTestId("prompt-input");
  await input.click();
  await page.keyboard.type(`A portrait of @tk${run}-ma`);
  const suggestions = page.getByTestId("element-suggestions");
  await expect(suggestions.locator(`[data-handle="${h}"]`)).toBeVisible();
  await expect(suggestions).toContainText(`Tk${run} Maya`);
  await page.keyboard.press("Enter"); // picks the element, does not send
  const chip = input.getByTestId("prompt-token");
  await expect(chip).toHaveText(`@${h}`);
  await expect(chip).not.toHaveAttribute("data-unknown");
  await page.keyboard.type("at dusk, tokens e2e");
  await expect(input).toHaveText(`A portrait of @${h} at dusk, tokens e2e`);

  // Backspace removes a chip whole.
  await page.keyboard.press("End");
  for (let i = 0; i < "at dusk, tokens e2e".length + 1; i += 1) await page.keyboard.press("Backspace");
  await page.keyboard.press("Backspace");
  await expect(input.getByTestId("prompt-token")).toHaveCount(0);
  await expect(input).toHaveText("A portrait of ");

  // A pasted prompt becomes chips; an unknown handle is marked and warned about.
  await paste(page, `@${h} meets @nobody-e2e at the pier, tokens e2e`);
  await expect(input.getByTestId("prompt-token")).toHaveCount(2);
  await expect(input.locator('[data-unknown="true"]')).toHaveText("@nobody-e2e");
  await expect(page.getByTestId("prompt-warnings")).toContainText("@nobody-e2e is not an element here");

  await page.getByTestId("generate-button").click();
  const card = page.getByTestId("generation-card").filter({ hasText: "tokens e2e" }).first();
  await expect(card).toHaveAttribute("data-status", "succeeded", { timeout: 30_000 });

  const generations = await (await backend.api("/api/generations")).json();
  // Enter on the suggestion picked it; nothing was sent then.
  expect(generations.filter((g: { prompt: string }) => g.prompt.startsWith(`A portrait of @${h}`))).toHaveLength(1);
  const made = generations.find((g: { prompt: string }) => g.prompt.endsWith("at the pier, tokens e2e"));
  expect(made.prompt).toBe(`A portrait of @${h} meets @nobody-e2e at the pier, tokens e2e`);
  expect(made.prompt_tokens).toEqual([
    { type: "text", text: "A portrait of " },
    { type: "element", handle: h, element_id: maya.id },
    { type: "text", text: " meets " },
    { type: "element", handle: "nobody-e2e", element_id: null },
    { type: "text", text: " at the pier, tokens e2e" },
  ]);
  const stored = await (await backend.api(`/api/elements/${maya.id}`)).json();
  expect(stored.used_in).toBe(1);

  // Typing fast and pressing Enter at once still picks the element whole.
  await input.click();
  await page.keyboard.press("Control+A");
  await page.keyboard.press("Backspace");
  await page.keyboard.type(`Fast @tk${run}-maya`);
  await page.keyboard.press("Enter");
  await expect(input.getByTestId("prompt-token")).toHaveText(`@${h}`);
  await expect(input).toHaveText(`Fast @${h} `);

  // Handles typed by hand become chips when the box loses focus.
  await input.click();
  await page.keyboard.press("Control+A");
  await page.keyboard.press("Backspace");
  await page.keyboard.type(`@${h} by hand `);
  await page.keyboard.press("Escape");
  await page.getByRole("heading").first().click({ force: true });
  await expect(input.getByTestId("prompt-token")).toHaveText(`@${h}`);

  // Recreate brings the chips back from the stored tokens.
  await card.getByTestId("open-viewer").click();
  await page.getByTestId("handoff-recreate").click();
  await expect(input.getByTestId("prompt-token")).toHaveCount(2);
  await expect(input).toHaveText(`A portrait of @${h} meets @nobody-e2e at the pier, tokens e2e`);
});
