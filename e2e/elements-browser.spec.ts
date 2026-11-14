import { readFileSync } from "node:fs";
import { expect, png, test } from "./fixtures";

// M4.1: elements (characters, locations, props) are created, edited, filtered,
// deleted, and exported then imported without loss, from the UI.

interface Element {
  id: string;
  handle: string;
  status: string;
  version: number;
  category: string;
  description: string;
  properties: Record<string, string>;
  media: { asset_id: string; role: string }[];
  project_id: string | null;
}

test("M4.1: create, edit, filter and delete an element", async ({ page, backend }) => {
  await page.goto(`${backend.url}/auth/login?token=${backend.token}&next=/elements`);
  await expect(page.getByTestId("elements-page")).toBeVisible();

  await page.getByTestId("element-new").first().click();
  const editor = page.getByTestId("element-editor");
  await editor.getByTestId("element-name").fill("Maya Ortíz E2E");
  await expect(editor.getByTestId("element-handle")).toHaveAttribute("placeholder", "maya-ortiz-e2e");
  await editor.getByTestId("element-description").fill("Thirties, short dark hair, yellow raincoat.");
  await editor.getByTestId("element-upload").setInputFiles({
    name: "maya-front.png",
    mimeType: "image/png",
    buffer: png(200, 60, 40),
  });
  await expect(editor.getByTestId("element-media").getByRole("listitem")).toHaveCount(1);
  await expect(editor.getByTestId("element-media")).toContainText("Primary");
  await editor.getByTestId("element-add-property").click();
  await editor.getByLabel("Name", { exact: true }).last().fill("wardrobe");
  await editor.getByLabel("Value").fill("raincoat");
  await editor.getByTestId("element-save").click();

  const card = page.locator('[data-testid="element-card"][data-handle="maya-ortiz-e2e"]');
  await expect(card).toBeVisible();
  await expect(card).toContainText("Draft");
  await expect(card.locator("img")).toBeVisible();

  // Edit: a new handle, ready, a second picture made primary.
  await card.click();
  await editor.getByTestId("element-handle").fill("maya_e2e");
  await editor.getByRole("radio", { name: "Ready" }).click();
  await editor.getByTestId("element-upload").setInputFiles({
    name: "maya-side.png",
    mimeType: "image/png",
    buffer: png(40, 60, 200),
  });
  const media = editor.getByTestId("element-media").getByRole("listitem");
  await expect(media).toHaveCount(2);
  await media.nth(1).getByRole("button", { name: "Make primary" }).click();
  await editor.getByTestId("element-description").fill("Thirties, buzz cut, yellow raincoat.");
  await editor.getByTestId("element-save").click();
  const edited = page.locator('[data-testid="element-card"][data-handle="maya_e2e"]');
  await expect(edited).toContainText("Ready");
  await expect(card).toHaveCount(0);

  const [stored] = (await (await backend.api("/api/elements?q=maya_e2e")).json()) as Element[];
  expect(stored).toMatchObject({
    handle: "maya_e2e",
    status: "ready",
    category: "character",
    description: "Thirties, buzz cut, yellow raincoat.",
    properties: { wardrobe: "raincoat" },
  });
  expect(stored!.media.map((m) => m.role)).toEqual(["primary", "angle"]);
  expect(stored!.version).toBeGreaterThanOrEqual(3); // description and media changed

  // A handle already taken is refused, and the editor stays open.
  await backend.api("/api/elements", { method: "POST", body: JSON.stringify({ name: "Harbour E2E" }) });
  await edited.click();
  await editor.getByTestId("element-handle").fill("harbour-e2e");
  await editor.getByTestId("element-save").click();
  await expect(page.getByText("@harbour-e2e is already taken here.")).toBeVisible();
  await page.keyboard.press("Escape");

  // Filters: kind tabs, status, search.
  await page.getByRole("tab", { name: "Locations" }).click();
  await expect(edited).toHaveCount(0);
  await page.getByRole("tab", { name: "Characters" }).click();
  await expect(edited).toBeVisible();
  await page.getByRole("tab", { name: "All" }).click();
  await page.getByTestId("elements-search").fill("buzz cut");
  await expect(page.getByTestId("element-card")).toHaveCount(1);
  await page.getByTestId("elements-search").fill("");

  // Delete: the element goes, its pictures stay in the library.
  await edited.click();
  await editor.getByTestId("element-delete").click();
  await editor.getByTestId("element-delete-confirm").click();
  await expect(edited).toHaveCount(0);
  for (const { asset_id } of stored!.media) {
    expect((await backend.api(`/api/assets/${asset_id}`)).status).toBe(200);
  }
});

test("M4.1: export a zip, import it into a project, nothing lost", async ({ page, backend }) => {
  const form = new FormData();
  form.append("files", new Blob([png(20, 160, 90)], { type: "image/png" }), "pier.png");
  const response = await fetch(`${backend.url}/api/assets/upload`, {
    method: "POST",
    headers: { Authorization: `Bearer ${backend.token}` },
    body: form,
  });
  const uploaded = await response.json();
  const assetId = uploaded[0].asset.id as string;
  const folder = await (
    await backend.api("/api/elements/folders", { method: "POST", body: JSON.stringify({ name: "Places" }) })
  ).json();
  const created = await backend.api("/api/elements", {
    method: "POST",
    body: JSON.stringify({
      name: "Pier Export E2E",
      category: "location",
      status: "ready",
      description: "Wooden pier, fog, sodium lamps.",
      properties: { time: "night" },
      asset_ids: [assetId],
      folder_id: folder.id,
    }),
  });
  expect(created.status).toBe(201);
  const project = await (
    await backend.api("/api/projects", { method: "POST", body: JSON.stringify({ name: "Import Target E2E" }) })
  ).json();

  await page.goto(`${backend.url}/auth/login?token=${backend.token}&next=/elements`);
  await page.getByTestId("elements-export").click();
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByTestId("elements-export-zip").click(),
  ]);
  expect(download.suggestedFilename()).toBe("global-elements.zip");
  const zip = readFileSync(await download.path());
  await page.keyboard.press("Escape");

  await page.getByTestId("elements-scope").selectOption(project.id);
  await expect(page).toHaveURL(new RegExp(`scope=${project.id}`));
  await page.getByTestId("elements-import").setInputFiles({
    name: "global-elements.zip",
    mimeType: "application/zip",
    buffer: zip,
  });
  await expect(page.getByText(/Imported \d+ elements?/)).toBeVisible();

  const imported = (
    (await (await backend.api(`/api/elements?project_id=${project.id}&include_global=false`)).json()) as Element[]
  ).find((e) => e.handle === "pier-export-e2e");
  expect(imported).toMatchObject({
    category: "location",
    status: "ready",
    description: "Wooden pier, fog, sodium lamps.",
    properties: { time: "night" },
    project_id: project.id,
  });
  expect(imported!.media).toHaveLength(1);
  // The project now has its own copy of the picture.
  const projectAssets = await (await backend.api(`/api/assets?project_id=${project.id}`)).json();
  expect(projectAssets.items.map((a: { id: string }) => a.id)).toContain(imported!.media[0]!.asset_id);
  // The folder came along, and the project view shows the copy.
  const folders = await (await backend.api(`/api/elements/folders?project_id=${project.id}`)).json();
  expect(folders.map((f: { name: string }) => f.name)).toContain("Places");
  const copy = page.locator(`[data-testid="element-card"][data-handle="pier-export-e2e"]`);
  await expect(copy).toHaveCount(2); // the project's and the global one
});
