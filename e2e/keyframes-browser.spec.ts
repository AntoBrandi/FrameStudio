import { expect, test } from "./fixtures";

// M5.7: keyframe then shot on the mock engine. Keyframes from the prompt and
// its cinema controls; the approved one (and an end frame) animates with the
// same settings; the clip is checked to open on the keyframe and close on the
// end frame; a finished clip extends from its last frame.

interface Shot {
  id: string;
  title: string;
  status: string;
  keyframe_id: string | null;
  end_frame_id: string | null;
  continues_from: string | null;
  keyframes: { asset: { id: string } | null; params: { aspect?: string }; shot: unknown }[];
  clips: { id: string; start_frame: string | null; end_frame: string | null; shot: unknown }[];
  checks: Record<string, { start: number | null; end: number | null; matches: boolean }>;
}

test("M5.7: keyframes, approve, animate with continuity intact, extend", async ({ page, backend }) => {
  test.setTimeout(120_000);
  const run = Array.from({ length: 5 }, () => "abcdefghijklmnopqrstuvwxyz"[Math.floor(Math.random() * 26)]).join("");
  const shots = async (): Promise<Shot[]> =>
    ((await (await backend.api("/api/shots?project_id=none")).json()) as Shot[]).filter((s) => s.title.includes(run));

  await page.goto(`${backend.url}/auth/login?token=${backend.token}&next=/video`);
  const composer = page.getByTestId("composer");
  await composer.getByTestId("keyframe-first").click();
  await composer.getByTestId("keyframe-model").selectOption("mock");
  await composer.getByTestId("chip-lighting").click();
  await page.getByTestId("lighting-picker").getByTestId("lighting-preset-silhouette").click();
  await page.getByTestId("lighting-picker").getByTestId("lighting-done").click();
  await composer.getByTestId("prompt-input").click();
  await page.keyboard.type(`A stranger in the diner doorway, keyframes e2e ${run}`);
  await composer.getByRole("button", { name: "Batch size +" }).click();
  await expect(composer.getByTestId("generate-button")).toHaveText(/Keyframes/);
  await composer.getByTestId("generate-button").click();

  // Two keyframes to choose from, at the clip's aspect ratio, with the shot's lighting.
  const card = page.getByTestId("shot-card").filter({ hasText: run }).filter({ hasNotText: "(continued)" });
  const options = card.getByTestId("keyframe-option");
  await expect(options).toHaveCount(2, { timeout: 30_000 });
  await expect(card.getByTestId("shot-status")).toHaveText("Choose");
  const [planned] = await shots();
  expect(planned!.keyframes.every((k) => k.params.aspect === "16:9")).toBe(true);
  expect(planned!.keyframes[0]!.shot).toMatchObject({ lighting: { preset: "silhouette" } });

  // Start on the first, end on the second, animate.
  await options.nth(0).click();
  await expect(options.nth(0)).toHaveAttribute("aria-pressed", "true");
  await card.getByTestId("keyframe-end").click();
  await card.getByTestId("animate-shot").click();
  const badge = card.getByTestId("shot-continuity");
  await expect(badge).toHaveAttribute("data-matches", "true", { timeout: 60_000 });
  await expect(badge).toHaveText(/opens (9\d|100)% · closes (9\d|100)%/);
  await expect(card.getByTestId("shot-status")).toHaveText("Done");
  const [animated] = await shots();
  const [clip] = animated!.clips;
  expect(clip!.start_frame).toBe(animated!.keyframes[0]!.asset!.id);
  expect(clip!.end_frame).toBe(animated!.keyframes[1]!.asset!.id);
  expect(clip!.shot).toEqual(animated!.keyframes[0]!.shot);
  expect(animated!.checks[clip!.id]!.start).toBeGreaterThan(0.9);

  // Extend: a new shot continues from the clip's last frame.
  await card.getByTestId("extend-clip").click();
  const next = page.getByTestId("shot-card").filter({ hasText: `${run} (continued)` });
  await expect(next.getByTestId("shot-continuity")).toHaveAttribute("data-matches", "true", { timeout: 60_000 });
  const following = (await shots()).find((s) => s.continues_from === clip!.id)!;
  expect(following.clips[0]!.start_frame).toBe(following.keyframe_id);

  // Extend from the viewer: the clip's last frame becomes the start frame in the composer.
  await card.getByRole("button", { name: "Open the clip" }).click();
  await page.getByTestId("handoff-extend").click();
  await expect(page.getByText("The last frame is the start of the next clip")).toBeVisible();
  await expect(composer.getByTestId("keyframe-first")).toHaveAttribute("aria-pressed", "false");
  await expect(composer.getByTestId("start-frame")).toBeVisible();
  await expect(composer.getByTestId("prompt-input")).toContainText(`keyframes e2e ${run}`);
});
