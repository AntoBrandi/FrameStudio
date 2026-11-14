import { expect, png, test } from "./fixtures";

// M4.6: from one photo, generate a character sheet and find it on the
// element (offline: the hosted test double stands in for Nano Banana).

test("M4.6: a character sheet from one photo is added to the element", async ({ page, backend }) => {
  const run = Array.from({ length: 5 }, () => "abcdefghijklmnopqrstuvwxyz"[Math.floor(Math.random() * 26)]).join("");
  const form = new FormData();
  form.append("files", new Blob([png(170, 80, 60)], { type: "image/png" }), `gen${run}.png`);
  const upload = await fetch(`${backend.url}/api/assets/upload`, {
    method: "POST",
    headers: { Authorization: `Bearer ${backend.token}` },
    body: form,
  });
  const photo = (await upload.json())[0].asset.id as string;
  const created = await backend.api("/api/elements", {
    method: "POST",
    body: JSON.stringify({
      name: `Gen${run} Maya`,
      category: "character",
      description: "Thirties, short dark hair, yellow raincoat.",
      asset_ids: [photo],
    }),
  });
  const maya = await created.json();

  await page.goto(`${backend.url}/auth/login?token=${backend.token}&next=/elements?q=gen${run}`);
  const card = page.locator(`[data-testid="element-card"][data-handle="${maya.handle}"]`);
  await card.click();
  await page.getByTestId("element-generate").click();
  const dialog = page.getByTestId("generate-views");
  await expect(dialog.getByTestId("recipe-character_sheet")).toHaveAttribute("aria-checked", "true");
  await expect(dialog.getByTestId("generator-estimate")).toContainText("3 views");
  // Two of the three views.
  await dialog.getByRole("group", { name: "Views" }).getByRole("button", { name: "Back" }).click();
  await expect(dialog.getByTestId("generator-estimate")).toContainText("2 views");
  await dialog.getByTestId("generator-start").click();
  await expect(page.getByText(`Making 2 views of @${maya.handle}`)).toBeVisible();
  await expect(page.getByTestId("element-editor")).toHaveCount(0);

  // The views arrive on the element as they finish.
  await expect(card).toContainText("3", { timeout: 30_000 }); // picture count badge
  const element = await (await backend.api(`/api/elements/${maya.id}`)).json();
  expect(element.media.map((m: { role: string }) => m.role)).toEqual(["primary", "sheet", "sheet"]);
  expect(element.used_in).toBe(2);
  const generations = await (await backend.api("/api/generations")).json();
  const mine = generations.filter((g: { attach_element_id: string | null }) => g.attach_element_id === maya.id);
  expect(mine).toHaveLength(2);
  expect(mine.map((g: { prompt: string }) => g.prompt.split(" of ")[0]).sort()).toEqual([
    "Full-body front view",
    "Head-and-shoulders portrait",
  ]);
  for (const generation of mine) {
    expect(generation.resolved.references.map((r: { asset_id: string }) => r.asset_id)).toEqual([photo]);
  }

  // Reopened, the editor shows the new pictures.
  await card.click();
  await expect(page.getByTestId("element-media").getByRole("listitem")).toHaveCount(3);
});
