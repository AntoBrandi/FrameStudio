import { type Backend, expect, type Page, startBackend, test } from "./fixtures";

// M2.3: add, check and remove a key for each provider; a monthly cap stops
// hosted requests once reached. Key checks are answered by the backend's
// built-in fake (FRAMESTUDIO_FAKE_PROVIDERS): keys starting fs-test-ok pass.

const PROVIDERS = [
  ["openai", "OpenAI"],
  ["anthropic", "Anthropic"],
  ["google", "Google Gemini"],
  ["byteplus", "BytePlus ModelArk"],
] as const;

const card = (page: Page, provider: string) => page.getByTestId(`connection-${provider}`);

test("M2.3: add, check, turn off and remove each provider's key", async ({ page, backend }) => {
  await page.goto(`${backend.url}/auth/login?token=${backend.token}&next=/settings`);
  await expect(page.getByRole("heading", { name: "Connections" })).toBeVisible();

  // A wrong key is refused and nothing is saved.
  const openai = card(page, "openai");
  await openai.getByTestId("api-key-input").fill("sk-wrong-0123456789abcdef");
  await openai.getByTestId("connect-key").click();
  await expect(openai.getByRole("alert")).toContainText("refused this key");
  await expect(openai.getByTestId("connection-status")).toHaveText("Not connected");

  for (const [provider, name] of PROVIDERS) {
    const key = `fs-test-ok-${provider}-SECRET-${"9".repeat(8)}${provider.length}`;
    const entry = card(page, provider);
    await entry.getByTestId("api-key-input").fill(key);
    await entry.getByTestId("connect-key").click();
    await expect(entry.getByTestId("connection-status")).toHaveText("Connected");
    await expect(entry).toContainText(`Key ending …${key.slice(-4)}, kept in the encrypted key file.`);
    await expect(entry).toContainText(`${name} accepted the key.`);
    await expect(page.getByText(key)).toHaveCount(0); // never shown again
    await expect(entry.getByTestId("api-key-input")).toHaveCount(0);
  }

  // Check again; turn one off and on.
  const anthropic = card(page, "anthropic");
  await anthropic.getByTestId("check-connection").click();
  await expect(anthropic.getByTestId("check-connection")).toHaveText("Check key"); // done checking
  await expect(anthropic.getByTestId("connection-status")).toHaveText("Connected");
  await anthropic.getByTestId("connection-enabled").uncheck();
  await expect(anthropic.getByTestId("connection-status")).toHaveText("Off");
  await anthropic.getByTestId("connection-enabled").check();
  await expect(anthropic.getByTestId("connection-status")).toHaveText("Connected");

  // Remove asks first, then forgets the key.
  const google = card(page, "google");
  await google.getByTestId("remove-connection").click();
  await expect(google.getByRole("alertdialog")).toContainText("Remove the Google Gemini key");
  await google.getByTestId("confirm-remove-connection").click();
  await expect(google.getByTestId("connection-status")).toHaveText("Not connected");
  await expect(google.getByTestId("api-key-input")).toBeVisible();

  // The API never hands a key back.
  const listed = await (await backend.api("/api/connections")).text();
  expect(listed).not.toContain("fs-test-ok");
});

test("M2.3: a monthly cap stops hosted generations once reached", async ({ page }) => {
  // Its own backend: spending by other tests in this worker would move the totals.
  const backend = await startBackend();
  try {
    await capScenario(page, backend);
  } finally {
    await page.goto("about:blank"); // close its event stream before it stops
    await backend.stop();
  }
});

async function capScenario(page: Page, backend: Backend) {
  await page.goto(`${backend.url}/auth/login?token=${backend.token}&next=/settings`);
  const cloud = card(page, "mock-cloud");
  await cloud.getByTestId("cap-input").fill("0.01");
  await cloud.getByTestId("cap-input").press("Enter");
  await expect(cloud.getByTestId("spent")).toHaveText("$0.00 of $0.01 spent this month.");

  const generate = () =>
    backend.api("/api/generations", {
      method: "POST",
      body: JSON.stringify({ model: "mock-cloud", params: { latency_seconds: 0 } }),
    });
  const first = await generate();
  expect(first.status).toBe(201);
  await expect(cloud.getByTestId("spent")).toHaveText("$0.01 of $0.01 spent this month.");
  const second = await generate();
  expect(second.status).toBe(402);
  expect((await second.json()).detail).toContain("monthly cap");

  const usage = page.getByTestId("usage");
  await expect(usage.getByRole("row", { name: /Mock cloud/ })).toContainText("$0.01");

  // Clearing the cap lets requests through again.
  await cloud.getByTestId("cap-input").fill("");
  await cloud.getByTestId("cap-input").press("Enter");
  await expect(cloud.getByTestId("spent")).toHaveText("$0.01 spent this month.");
  expect((await generate()).status).toBe(201);
}

test("M2.4: choose the default language model and one per task", async ({ page, backend }) => {
  for (const provider of ["anthropic", "openai"]) {
    const response = await backend.api(`/api/connections/${provider}`, {
      method: "PUT",
      body: JSON.stringify({ api_key: `fs-test-ok-${provider}-0123456789` }),
    });
    expect(response.status).toBe(200);
  }
  await page.goto(`${backend.url}/auth/login?token=${backend.token}&next=/settings`);
  const section = page.getByTestId("language-models");
  const row = (task: string) => section.getByTestId(`llm-task-${task}`);

  // Automatic picks Claude Opus 5.5 while Anthropic is connected (offline stand-ins here).
  await expect(row("default").getByTestId("llm-select")).toHaveValue("");
  await expect(row("default")).toContainText("Automatic (Claude Opus 5.5 (offline))");
  await expect(row("default")).toContainText("Sees images");

  // The Director gets its own model; the others follow the default.
  await row("director").getByTestId("llm-select").selectOption("openai/gpt-5");
  await expect(row("director").getByTestId("llm-select")).toHaveValue("openai/gpt-5");
  await row("default").getByTestId("llm-select").selectOption("anthropic/claude-opus-5-5");
  await expect(row("captions")).toContainText("Same as default (Claude Opus 5.5 (offline))");
  const settings = await (await backend.api("/api/settings")).json();
  expect(settings.llm_director).toBe("openai/gpt-5");

  // Disconnecting OpenAI keeps the choice visible but sends the Director to the default.
  await backend.api("/api/connections/openai", { method: "DELETE" });
  await expect(row("director").getByTestId("llm-select")).toHaveValue("openai/gpt-5");
  await expect(row("director").getByRole("option", { name: "openai/gpt-5 (not connected)" })).toBeAttached();
  const overview = await (await backend.api("/api/llm")).json();
  const director = overview.tasks.find((t: { task: string }) => t.task === "director");
  expect(director.effective).toBe("anthropic/claude-opus-5-5");
});
