import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "./fixtures";

const ROUTES = [
  ["Image", "/image"],
  ["Video", "/video"],
  ["Studio", "/studio"],
  ["Assets", "/assets"],
  ["Elements", "/elements"],
  ["Canvas", "/canvas"],
  ["Home", "/"],
] as const;

test("M0.1: the window shows the backend as connected", async ({ window }) => {
  await expect(window.getByRole("main").getByText("Backend: connected")).toBeVisible();
  await expect(window).toHaveTitle("FrameStudio");
});

test("M0.4: every route is reachable by mouse and by keyboard", async ({ window }) => {
  const nav = window.getByRole("navigation", { name: "Main" });
  for (const [name, path] of ROUTES) {
    await nav.getByRole("link", { name }).click();
    await expect(window).toHaveURL((url) => url.pathname === path);
  }
  await window.getByRole("link", { name: "Settings" }).click();
  await expect(window.getByRole("heading", { name: "Settings", level: 1 })).toBeVisible();

  for (const [name, path] of ROUTES) {
    await window.keyboard.press("Control+k");
    await window.getByPlaceholder("Type a command or search…").fill(`Go to ${name}`);
    await window.keyboard.press("Enter");
    await expect(window).toHaveURL((url) => url.pathname === path);
  }
});

test("M0.4: the shell passes an axe accessibility check", async ({ window }) => {
  await expect(window.getByRole("main").getByText("Backend: connected")).toBeVisible();
  await window.emulateMedia({ reducedMotion: "reduce" }); // no colour transitions mid-check
  for (const theme of ["dark", "light"]) {
    await window.evaluate((value) => {
      document.documentElement.dataset.theme = value;
      return new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    }, theme);
    const results = await new AxeBuilder({ page: window }).setLegacyMode(true).analyze();
    const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(
      serious.map((v) => `${theme}: ${v.id}: ${v.help} at ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`),
    ).toEqual([]);
  }
});

test("M0.5: generate, the card appears, open the viewer", async ({ window }) => {
  await window.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Image" }).click();
  await window.getByTestId("prompt-input").fill("a lighthouse at dusk, e2e");
  await window.getByTestId("generate-button").click();
  const card = window
    .getByTestId("generation-card")
    .filter({ has: window.getByRole("img", { name: "a lighthouse at dusk, e2e" }) });
  await expect(card).toHaveAttribute("data-status", "succeeded", { timeout: 15_000 });
  await card.getByTestId("open-viewer").click();
  const viewer = window.getByTestId("asset-viewer");
  await expect(viewer).toBeVisible();
  await expect(viewer.getByRole("img")).toBeVisible();
  await expect(viewer.getByText("a lighthouse at dusk, e2e").first()).toBeVisible();
  await expect(window).toHaveURL(/view=/);
  await window.keyboard.press("Escape");
  await expect(viewer).toBeHidden();
});

test("M0.5: a failed generation shows an error card with Retry", async ({ window, backend }) => {
  await backend.api("/api/settings", { method: "PATCH", body: JSON.stringify({ mock_failure_rate: 1 }) });
  await window.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Image" }).click();
  await window.getByTestId("prompt-input").fill("this one fails, e2e");
  await window.getByTestId("generate-button").click();
  const card = window.getByTestId("generation-card").filter({ hasText: "Generation failed" }).first();
  await expect(card).toBeVisible({ timeout: 15_000 });
  await backend.api("/api/settings", { method: "PATCH", body: JSON.stringify({ mock_failure_rate: 0 }) });
  await card.getByTestId("retry-generation").click();
  await expect(window.getByRole("img", { name: "this one fails, e2e" })).toBeVisible({ timeout: 15_000 });
});

test("M0.3: a fake job shows live progress and can be cancelled", async ({ window }) => {
  await window.getByRole("link", { name: "Settings" }).click();
  await window.getByTestId("fake-local").click();
  await window.getByTestId("job-tray-button").click();
  const row = window.getByTestId("job-row").filter({ hasText: "Fake local job" }).first();
  await expect(row).toHaveAttribute("data-state", "running");
  const bar = row.getByRole("progressbar");
  await expect
    .poll(async () => Number(await bar.getAttribute("aria-valuenow")), { timeout: 10_000 })
    .toBeGreaterThan(5);
  await row.getByRole("button", { name: /Cancel/ }).click();
  await expect(row).toHaveAttribute("data-state", "cancelled");
});
