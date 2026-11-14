/**
 * M1 (Library) end to end, in the desktop shell, against a backend of its own.
 * Tests run in order and build on each other's data.
 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, readdirSync, readFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test as base, expect, type Page } from "@playwright/test";
import { appWindow, launchShell, ROOT, startBackend } from "./fixtures";

type Backend = Awaited<ReturnType<typeof startBackend>>;

const test = base.extend<{ app: Page }, { lib: Backend }>({
  lib: [
    // eslint-disable-next-line no-empty-pattern -- Playwright fixtures must destructure
    async ({}, use) => {
      const backend = await startBackend();
      await use(backend);
      await backend.stop();
    },
    { scope: "worker" },
  ],
  app: async ({ lib }, use) => {
    const shell = await launchShell({
      FRAMESTUDIO_BACKEND_URL: lib.url,
      FRAMESTUDIO_TOKEN: lib.token,
      FRAMESTUDIO_DATA_DIR: lib.dataDir,
    });
    const window = await appWindow(shell, lib.url);
    await use(window);
    await shell.close();
  },
});
test.describe.configure({ mode: "serial" });

const CORPUS = path.join(ROOT, "backend", "tests", "fixtures", "media");
const MEDIA = ["clip.mp4", "silent.mov", "clip.webm", "tone.wav", "song.mp3", "rotated.jpg", "anim.gif"];

function importSet(count: number): string[] {
  const dir = mkdtempSync(path.join(os.tmpdir(), "framestudio-import-"));
  execFileSync("uv", ["run", "--directory", path.join(ROOT, "backend"), "python",
    "tests/fixtures/make_import_set.py", dir, String(count)]); // prettier-ignore
  return readdirSync(dir).map((name) => path.join(dir, name));
}

async function total(page: Page): Promise<number> {
  return Number((await page.getByTestId("asset-total").textContent())?.match(/\d+/)?.[0] ?? -1);
}

async function goToAssets(page: Page) {
  await page.getByTestId("nav-all-assets").click();
  await expect(page.getByTestId("filter-bar")).toBeVisible();
}

test("M1.1: importing 50 mixed files shows thumbnails and metadata; a duplicate adds nothing", async ({ app }) => {
  await goToAssets(app);
  const files = [...importSet(50 - MEDIA.length), ...MEDIA.map((name) => path.join(CORPUS, name))];
  await app.getByTestId("import-input").setInputFiles(files);
  await expect(app.getByText("50 added, 0 already here, 0 failed")).toBeVisible({ timeout: 60_000 });
  await expect.poll(() => total(app)).toBe(50);

  // Thumbnails decode (not just exist) for the cards on screen.
  const decoded = await app.evaluate(async () => {
    const images = [...document.querySelectorAll<HTMLImageElement>("[data-testid=asset-card] img")];
    await Promise.all(images.map((img) => img.decode().catch(() => undefined)));
    return images.filter((img) => img.naturalWidth > 0).length;
  });
  expect(decoded).toBeGreaterThan(10);

  // Metadata: the video reports its real size and duration.
  await app.getByTestId("library-search").fill("silent");
  await expect.poll(() => total(app)).toBe(1);
  await app.getByTestId("asset-open").first().click();
  const viewer = app.getByTestId("asset-viewer");
  await expect(viewer.getByText("160×90")).toBeVisible();
  await expect(viewer.getByText("0:01 · 24 fps")).toBeVisible();
  await app.keyboard.press("Escape");
  await app.getByTestId("library-search").fill("");

  // Dropping a file from the desktop: a duplicate of something already here adds nothing.
  const bytes = readFileSync(path.join(CORPUS, "rotated.jpg")).toString("base64");
  await app.evaluate((b64) => {
    const data = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    const transfer = new DataTransfer();
    transfer.items.add(new File([data], "rotated-again.jpg", { type: "image/jpeg" }));
    const target = document.querySelector("[data-testid=gallery]")!;
    for (const type of ["dragenter", "dragover", "drop"]) {
      target.dispatchEvent(new DragEvent(type, { dataTransfer: transfer, bubbles: true, cancelable: true }));
    }
  }, bytes);
  await expect(app.getByText("0 added, 1 already here, 0 failed")).toBeVisible();
  await expect.poll(() => total(app)).toBe(50);
});

test("M1.1: damaged and unsupported files are refused with a clear reason", async ({ app }) => {
  await goToAssets(app);
  await app
    .getByTestId("import-input")
    .setInputFiles([path.join(CORPUS, "truncated.png"), path.join(CORPUS, "notes.txt")]);
  await expect(app.getByText("0 added, 0 already here, 2 failed")).toBeVisible();
  await expect(app.getByText(/truncated\.png could not be read/)).toBeVisible();
  await expect.poll(() => total(app)).toBe(50);
});

test("M1.3: moving 20 selected assets into Characters updates the counts at once", async ({ app }) => {
  await goToAssets(app);
  for (const name of ["Characters", "Locations"]) {
    await app.getByTestId("new-folder").click();
    await app.getByLabel("Folder name").fill(name);
    await app.getByLabel("Folder name").press("Enter");
    await expect(app.locator(`[data-folder="${name}"]`)).toBeVisible();
  }
  const cards = app.getByTestId("asset-card");
  await cards.nth(0).locator("input[type=checkbox]").click();
  await cards
    .nth(19)
    .locator("input[type=checkbox]")
    .click({ modifiers: ["Shift"] });
  await expect(app.getByTestId("bulk-bar")).toContainText("20 selected");
  await app.getByTestId("bulk-bar").getByRole("button", { name: "Move to…" }).click();
  await app.getByRole("menuitem", { name: "Characters" }).click();
  await expect(app.locator('[data-folder="Characters"]')).toContainText("20");

  // Drag and drop one asset onto a sidebar folder.
  await app.locator('[data-folder="Characters"]').getByRole("link").click();
  await expect.poll(() => total(app)).toBe(20);
  await app.getByTestId("asset-card").first().dragTo(app.locator('[data-folder="Locations"]'));
  await expect(app.locator('[data-folder="Locations"]')).toContainText("1");
  await expect(app.locator('[data-folder="Characters"]')).toContainText("19");
});

test("M1.4 + M1.5: filters, layout options and search", async ({ app, lib }) => {
  await lib.api("/api/generations", {
    method: "POST",
    body: JSON.stringify({ prompt: "a tuna sandwich in a neon kitchen", params: { latency_seconds: 0 } }),
  });
  await goToAssets(app);
  await expect.poll(() => total(app)).toBe(51);
  const filters = app.getByRole("group", { name: "Filters" });
  await filters.getByRole("button", { name: "Video" }).click();
  await expect.poll(() => total(app)).toBe(3);
  await filters.getByRole("button", { name: "Audio" }).click();
  await expect.poll(() => total(app)).toBe(5);
  await filters.getByLabel("Model").selectOption("mock");
  await expect.poll(() => total(app)).toBe(0);
  await app.getByRole("button", { name: "Clear filters" }).click();
  await expect.poll(() => total(app)).toBe(51);

  await app.getByTestId("library-search").fill("tuna kitchen");
  await expect.poll(() => total(app)).toBe(1);
  await app.getByTestId("library-search").fill("type:video");
  await expect.poll(() => total(app)).toBe(3);
  await app.getByTestId("library-search").fill("");

  await app.getByRole("button", { name: "View" }).click();
  await app.getByRole("button", { name: "Square" }).click();
  const box = await app.getByTestId("asset-card").first().boundingBox();
  expect(Math.abs(box!.width - box!.height)).toBeLessThan(2);
  await app.keyboard.press("Escape");
});

test("M1.6: the viewer opens every kind, its actions work, and a link reopens the asset", async ({ app }) => {
  await goToAssets(app);
  await app.getByTestId("library-search").fill("tuna");
  await expect.poll(() => total(app)).toBe(1);
  await app.getByTestId("asset-open").first().click();
  const viewer = app.getByTestId("asset-viewer");
  await expect(viewer.getByRole("img", { name: /tuna sandwich/ })).toBeVisible();

  // Zoom with the toolbar, then back to fit.
  await viewer.getByRole("button", { name: "Zoom in" }).click();
  await expect(viewer.getByTestId("zoom-stage")).toHaveAttribute("data-scale", "1.50");
  await viewer.getByRole("button", { name: "Fit" }).click();

  // Like, notes (autosaved, then searchable), download.
  await viewer.getByTestId("viewer-like").click();
  await expect(viewer.getByTestId("viewer-like")).toHaveAttribute("aria-pressed", "true");
  await viewer.getByRole("tab", { name: "Notes" }).click();
  await viewer.getByTestId("asset-notes").fill("hero frame for the opening");
  await expect(viewer.getByText("Saved")).toBeVisible();
  // In the desktop shell a download opens the system Save dialog; check the
  // link's contract (an attachment with a proper name) with the window's credentials.
  const href = await viewer.getByTestId("viewer-download").getAttribute("href");
  expect(href).toMatch(/\?download=true$/);
  const disposition = await app.evaluate(async (url) => (await fetch(url!)).headers.get("content-disposition"), href);
  expect(disposition).toMatch(/^attachment; filename=.*\.png/);

  // The URL is a deep link: reloading reopens the same asset.
  const url = app.url();
  expect(url).toMatch(/view=/);
  await app.reload();
  await expect(app.getByTestId("asset-viewer").getByRole("img", { name: /tuna sandwich/ })).toBeVisible();
  await app.keyboard.press("Escape");

  // Video and audio open too, with frame stepping for video.
  await app.getByTestId("library-search").fill("type:video");
  await app.getByTestId("asset-open").first().click();
  await expect(app.getByTestId("viewer-video")).toBeVisible();
  await app.getByRole("button", { name: "Next frame" }).click();
  await app.keyboard.press("Escape");
  await app.getByTestId("library-search").fill("type:audio");
  await app.getByTestId("asset-open").first().click();
  await expect(app.getByTestId("asset-viewer").locator("audio")).toBeVisible();
  await app.keyboard.press("Escape");

  await app.getByTestId("library-search").fill("hero frame opening");
  await expect.poll(() => total(app)).toBe(1);
  await app.getByTestId("library-search").fill("");
  await app.getByRole("link", { name: "Favorites" }).click();
  await expect.poll(() => total(app)).toBe(1);
});

test("M1.2: create a project, set its cover, write its brief, move it to the trash", async ({ app, lib }) => {
  await app.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Studio" }).click();
  await app.getByTestId("new-project").click();
  await app.getByLabel("Name").fill("Night Diner");
  await app.getByLabel("Description").fill("A short noir about a 3 a.m. diner");
  await app.getByRole("button", { name: "Create project" }).click();
  await expect(app.getByTestId("ws-project-name")).toHaveText("Night Diner");

  // Import into the project, then make it the cover from the viewer.
  await app.getByTestId("import-input").setInputFiles(path.join(CORPUS, "alpha.webp"));
  await expect(app.getByText("1 added, 0 already here, 0 failed")).toBeVisible();
  await app.getByTestId("asset-open").first().click();
  await app.getByRole("button", { name: "Set as cover" }).click();
  await expect(app.getByText("Cover updated")).toBeVisible();
  await app.keyboard.press("Escape");

  // The brief autosaves and survives a reload.
  await app.getByTestId("ws-brief").click();
  await app.getByRole("textbox", { name: /Context/ }).fill("A diner at 3 a.m.; one waitress, one stranger.");
  await expect(app.getByTestId("brief-state")).toHaveText("Saved");
  await app.reload();
  await expect(app.getByRole("textbox", { name: /Context/ })).toHaveValue(
    "A diner at 3 a.m.; one waitress, one stranger.",
  );

  const [project] = (await (await lib.api("/api/projects")).json()) as { cover_url: string | null }[];
  expect(project?.cover_url).toMatch(/\/thumb$/);
  await app.getByTestId("ws-settings").click();
  await expect(app.getByTestId("project-name")).toHaveValue("Night Diner");
  await app.getByTestId("trash-project").click();
  await expect(app.getByText("Moved Night Diner to the trash")).toBeVisible();
  await expect(app.getByTestId("project-card")).toHaveCount(0);
});

test("M1.7: delete and restore round-trip; emptying the trash asks first", async ({ app }) => {
  await goToAssets(app);
  const before = await total(app);
  const cards = app.getByTestId("asset-card");
  await cards.nth(0).locator("input[type=checkbox]").click();
  await cards.nth(1).locator("input[type=checkbox]").click();
  await app.getByTestId("bulk-bar").getByRole("button", { name: "Move to trash" }).click();
  await expect.poll(() => total(app)).toBe(before - 2);

  await app.getByTestId("nav-trash").click();
  await expect.poll(() => total(app)).toBe(2);
  await app.getByTestId("asset-card").nth(0).locator("input[type=checkbox]").click();
  await app.getByTestId("bulk-bar").getByRole("button", { name: "Restore" }).click();
  await expect.poll(() => total(app)).toBe(1);

  await app.getByTestId("empty-trash").click();
  await expect(app.getByRole("alertdialog")).toContainText("Permanently delete 1 assets and 1 projects");
  await app.getByTestId("confirm-empty-trash").click();
  await expect(app.getByText(/Deleted 1 assets and 1 projects/)).toBeVisible();
  await expect(app.getByText("The trash is empty.")).toBeVisible();
  await goToAssets(app);
  // One asset is gone for good; the deleted project's own image stayed in the library.
  await expect.poll(() => total(app)).toBe(before - 1);
});

test("M1: the library pages pass an axe accessibility check", async ({ app }) => {
  const { default: AxeBuilder } = await import("@axe-core/playwright");
  await app.emulateMedia({ reducedMotion: "reduce" });
  const check = async (where: string) => {
    const results = await new AxeBuilder({ page: app }).setLegacyMode(true).analyze();
    const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(
      serious.map((v) => `${where}: ${v.id}: ${v.help} at ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`),
    ).toEqual([]);
  };
  await goToAssets(app);
  await expect(app.getByTestId("asset-card").first()).toBeVisible();
  await app.getByTestId("asset-card").first().locator("input[type=checkbox]").click();
  await check("assets with a selection");
  await app.keyboard.press("Escape");
  await app.getByTestId("asset-open").first().click();
  await expect(app.getByTestId("asset-viewer")).toBeVisible();
  await check("viewer");
  await app.keyboard.press("Escape");
  await app.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Studio" }).click();
  await check("studio");
  await app.getByTestId("nav-trash").click();
  await expect(app.getByTestId("trash-controls")).toBeVisible();
  await check("trash");
});
