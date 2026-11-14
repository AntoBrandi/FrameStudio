import { expect, png, test } from "./fixtures";

// M5.4: a palette extracted from a still is saved, used in a prompt, and
// applied to a clip as a colour grade.

test("M5.4: a palette from a still, into the prompt and onto a clip", async ({ page, backend }) => {
  const run = Array.from({ length: 5 }, () => "abcdefghijklmnopqrstuvwxyz"[Math.floor(Math.random() * 26)]).join("");
  const name = `Sunset ${run}`;
  const form = new FormData();
  form.append("files", new Blob([png(214, 101, 59)], { type: "image/png" }), `sunset-${run}.png`);
  const uploaded = await fetch(`${backend.url}/api/assets/upload`, {
    method: "POST",
    headers: { Authorization: `Bearer ${backend.token}` },
    body: form,
  });
  expect(uploaded.status).toBe(200);

  await page.goto(`${backend.url}/auth/login?token=${backend.token}&next=/image`);
  await page.getByTestId("chip-palette").click();
  const picker = page.getByTestId("palette-picker");
  await picker.getByTestId("palette-from-picture").click();
  await page.getByTestId("picker-tab-uploads").click();
  await page.getByTestId("picker-search").fill(`sunset-${run}`);
  await page.getByRole("button", { name: `sunset-${run}.png` }).click();
  await page.getByTestId("add-references").click();
  const fresh = picker.getByTestId("palette-new");
  await expect(fresh.getByTestId("palette-swatches").locator("span")).not.toHaveCount(0);
  await expect(fresh.getByTestId("palette-words")).toHaveValue(/warm/);
  await fresh.getByTestId("palette-name").fill(name);
  await fresh.getByTestId("palette-save").click();
  await expect(picker.getByRole("radio", { name })).toHaveAttribute("aria-checked", "true");
  await picker.getByTestId("palette-done").click();
  await expect(page.getByTestId("chip-palette")).toHaveAccessibleName(`Palette: ${name}`);

  // Its words go into the prompt.
  await page.getByTestId("prompt-input").click();
  await page.keyboard.type(`A harbour at dusk, palette e2e ${run}`);
  await page.getByTestId("request-preview-button").click();
  await expect(page.getByTestId("request-sections").locator('[data-family="palette"]')).toContainText(
    "Colour palette: ",
  );
  await page.keyboard.press("Escape");

  // A clip to grade.
  const made = await backend.api("/api/generations", {
    method: "POST",
    body: JSON.stringify({ model: "mock", mode: "video", prompt: `a clip to grade ${run}`, params: { duration: 2 } }),
  });
  const [queued] = await made.json();
  let clip: { asset: { id: string } | null; status: string } = { asset: null, status: "queued" };
  await expect
    .poll(
      async () => {
        clip = await (await backend.api(`/api/generations/${queued.id}`)).json();
        return clip.status;
      },
      { timeout: 30_000 },
    )
    .toBe("succeeded");

  await page.goto(`${backend.url}/assets?view=${clip.asset!.id}`);
  await page.getByTestId("viewer-grade").click();
  const dialog = page.getByTestId("grade-dialog");
  await dialog.getByRole("radio", { name }).click();
  await dialog.getByTestId("grade-strength").fill("80");
  await dialog.getByTestId("grade-start").click();
  await expect(page.getByText(`Grading with ${name}`)).toBeVisible();

  // The graded copy lands in the library beside the original.
  await expect
    .poll(
      async () => {
        const assets = await (await backend.api(`/api/assets?q=${encodeURIComponent("graded")}`)).json();
        return assets.items.some(
          (a: { original_name: string; kind: string }) =>
            a.kind === "video" && a.original_name.endsWith(`graded ${name}.mp4`),
        );
      },
      { timeout: 30_000 },
    )
    .toBe(true);
});
