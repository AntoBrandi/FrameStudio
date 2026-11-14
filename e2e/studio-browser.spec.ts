import { readFileSync } from "node:fs";
import { expect, png, test } from "./fixtures";

// M5.6: the Studio workspace, one journey on the mock engine: a project
// started from the hero composer, its brief, settings and elements, the
// floating composer in both modes with cinema chips, folders, trash, export,
// the tools and the Director's panel, and a viewer hand-off that stays put.

interface Generation {
  id: string;
  status: string;
  mode: string;
  prompt: string;
  folder_id: string | null;
  prompt_tokens: { type: string; handle?: string; name?: string }[];
  shot: { lighting?: { preset?: string } } | null;
  asset: { id: string; kind: string } | null;
}

test("M5.6: a full journey through the Studio workspace", async ({ page, backend }) => {
  test.setTimeout(120_000);
  const run = Array.from({ length: 5 }, () => "abcdefghijklmnopqrstuvwxyz"[Math.floor(Math.random() * 26)]).join("");
  const generations = async (projectId: string): Promise<Generation[]> =>
    (await backend.api(`/api/generations?project_id=${projectId}`)).json();

  // The hero composer starts a project named after the prompt and opens it.
  await page.goto(`${backend.url}/auth/login?token=${backend.token}&next=/studio`);
  const hero = page.getByTestId("studio-hero");
  await expect(hero.getByTestId("studio-target")).toHaveValue("new");
  await hero.getByTestId("prompt-input").click();
  await page.keyboard.type(`Rain on the diner window ${run}`);
  await hero.getByTestId("generate-button").click();
  await expect(page).toHaveURL(/\/studio\/[0-9a-f]+$/);
  const projectId = new URL(page.url()).pathname.split("/").pop()!;
  await expect(page.getByTestId("ws-project-name")).toHaveText(`Rain on the diner window ${run}`);
  const cards = page.getByTestId("asset-card");
  await expect(cards).toHaveCount(1, { timeout: 30_000 });
  await expect(page.getByTestId("in-progress")).toHaveCount(0);

  // Brief, settings and elements, from the project's sidebar.
  await page.getByTestId("ws-brief").click();
  await page.getByRole("textbox", { name: /Context/ }).fill("Rain, neon, one waitress at 3 a.m.");
  await expect(page.getByTestId("brief-state")).toHaveText("Saved");
  await page.getByTestId("ws-settings").click();
  await page.getByTestId("project-name").fill(`Diner ${run}`);
  await page.getByTestId("project-name").press("Enter");
  await expect(page.getByTestId("ws-project-name")).toHaveText(`Diner ${run}`);
  await page.getByTestId("ws-elements").click();
  await expect(page.getByTestId("elements-scope")).toHaveCount(0);
  await page.getByTestId("element-new").first().click();
  const editor = page.getByTestId("element-editor");
  await editor.getByTestId("element-name").fill(`Maya ${run}`);
  await editor
    .getByTestId("element-upload")
    .setInputFiles({ name: "maya.png", mimeType: "image/png", buffer: png(200, 60, 40) });
  await expect(editor.getByTestId("element-media").getByRole("listitem")).toHaveCount(1);
  await editor.getByTestId("element-save").click();
  const handle = `maya-${run}`;
  await expect(page.locator(`[data-testid="element-card"][data-handle="${handle}"]`)).toBeVisible();

  // The floating composer in Video mode: the project's element, a camera move
  // and a lighting preset.
  await page.getByTestId("ws-assets").click();
  const composer = page.getByTestId("workspace-composer");
  await composer.getByTestId("composer-mode-video").click();
  await expect(composer.getByTestId("composer-mode-video")).toHaveAttribute("aria-pressed", "true");
  await composer.getByTestId("prompt-input").click();
  await page.keyboard.type(`@${handle}`);
  await expect(page.getByTestId("element-suggestions").locator(`[data-handle="${handle}"]`)).toBeVisible();
  await page.keyboard.press("Enter");
  await page.keyboard.type(` waits at the counter, studio e2e ${run} #dol`);
  await expect(page.getByTestId("move-suggestions").locator('[data-move="dolly-in"]')).toBeVisible();
  await page.keyboard.press("Enter");
  await composer.getByTestId("chip-lighting").click();
  const lighting = page.getByTestId("lighting-picker");
  await lighting.getByTestId("lighting-preset-practicals").click();
  await lighting.getByTestId("lighting-done").click();
  await composer.getByTestId("generate-button").click();
  await expect
    .poll(async () => (await generations(projectId)).find((g) => g.prompt.includes(`studio e2e ${run}`))?.status, {
      timeout: 60_000,
    })
    .toBe("succeeded");
  const clip = (await generations(projectId)).find((g) => g.prompt.includes(`studio e2e ${run}`))!;
  expect(clip.mode).toBe("video");
  expect(clip.shot?.lighting?.preset).toBe("practicals");
  expect(clip.prompt_tokens).toContainEqual(expect.objectContaining({ type: "element", handle }));
  expect(clip.prompt_tokens).toContainEqual(expect.objectContaining({ type: "move", name: "dolly-in" }));

  // A folder: move the clip in (the bulk bar takes the composer's place),
  // then generate from inside the folder, in Image mode.
  await page.getByTestId("new-folder").click();
  await page.getByLabel("Folder name").fill("Shots");
  await page.getByLabel("Folder name").press("Enter");
  const shots = page.locator('[data-testid="folder-row"][data-folder="Shots"]');
  await expect(shots).toBeVisible();
  await page.locator(`[data-testid="asset-card"][data-id="${clip.asset!.id}"]`).locator("input[type=checkbox]").click();
  await expect(composer).toHaveCount(0);
  await page.getByTestId("bulk-bar").getByRole("button", { name: "Move to…" }).click();
  await page.getByRole("menuitem", { name: "Shots" }).click();
  await expect(shots).toContainText("1");
  await expect(composer).toBeVisible();
  await shots.getByRole("link").click();
  await expect(cards).toHaveCount(1);
  await composer.getByTestId("composer-mode-image").click();
  await composer.getByTestId("prompt-input").click();
  await page.keyboard.press("ControlOrMeta+a");
  await page.keyboard.type(`A neon sign over the door, folder e2e ${run}`);
  await composer.getByTestId("generate-button").click();
  await expect(cards).toHaveCount(2, { timeout: 30_000 });
  await expect(shots).toContainText("2");
  const still = (await generations(projectId)).find((g) => g.prompt.includes(`folder e2e ${run}`))!;
  expect(still.folder_id).not.toBeNull();

  // Trash: one asset out, counted in the project's trash.
  await cards.first().locator("input[type=checkbox]").click();
  await page.getByTestId("bulk-bar").getByRole("button", { name: "Move to trash" }).click();
  await expect(cards).toHaveCount(1);
  await expect(page.getByTestId("ws-trash")).toContainText("1");
  await page.getByTestId("ws-trash").click();
  await expect(page.getByTestId("workspace")).toHaveAttribute("data-tab", "trash");
  await expect(cards).toHaveCount(1);

  // Export: a zip named by the slug (set when the project was made), a
  // directory per folder, and the brief.
  const { slug } = await (await backend.api(`/api/projects/${projectId}`)).json();
  await page.getByTestId("ws-assets").click();
  const downloading = page.waitForEvent("download");
  await page.getByTestId("export-project").click();
  const download = await downloading;
  expect(download.suggestedFilename()).toBe(`${slug}.zip`);
  const zip = readFileSync(await download.path());
  for (const entry of [`${slug}/brief.md`, `${slug}/shots/`]) expect(zip.includes(Buffer.from(entry))).toBe(true);

  // Tools: the Director's panel stays open from page to page; Storyboard and
  // Canvas say which step brings them.
  await page.getByTestId("ws-director").click();
  await expect(page.getByTestId("director-panel")).toBeVisible();
  await page.getByTestId("ws-storyboard").click();
  await expect(page.getByTestId("tool-storyboard")).toContainText("M6.5");
  await expect(page.getByTestId("director-panel")).toBeVisible();
  await page.getByTestId("ws-canvas").click();
  await expect(page.getByTestId("tool-canvas")).toBeVisible();
  await page.getByTestId("director-panel").getByRole("button", { name: "Close" }).click();
  await expect(page.getByTestId("director-panel")).toHaveCount(0);

  // A viewer hand-off stays in the workspace: Turn to video switches its composer.
  const picture = (await generations(projectId)).find((g) => g.status === "succeeded" && g.mode === "image")!;
  await page.goto(`${backend.url}/studio/${projectId}?view=${picture.asset!.id}`);
  await page.getByTestId("handoff-video").click();
  await expect(page).toHaveURL(new RegExp(`/studio/${projectId}$`));
  await expect(page.getByTestId("workspace-composer").getByTestId("composer-mode-video")).toHaveAttribute(
    "aria-pressed",
    "true",
  );

  // Back on the Studio home, the project is listed under its new name.
  await page.getByTestId("sidebar").getByRole("link", { name: "All projects" }).click();
  await expect(page.getByTestId("project-card").filter({ hasText: `Diner ${run}` })).toBeVisible();
});
