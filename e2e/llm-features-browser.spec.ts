import { deflateSync } from "node:zlib";
import { expect, test } from "./fixtures";

// M2.6 on the offline stand-ins (FRAMESTUDIO_FAKE_PROVIDERS): a rough prompt
// comes back improved with a diff and one-click undo, and images can be found
// by what they show once described.

function png(): Buffer {
  const size = 16;
  const row = Buffer.concat([Buffer.from([0]), Buffer.alloc(size * 3, 120)]);
  const raw = Buffer.concat(Array.from({ length: size }, () => row));
  const table = Array.from({ length: 256 }, (_, n) => {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    return c >>> 0;
  });
  const crc = (data: Buffer) => {
    let c = 0xffffffff;
    for (const byte of data) c = table[(c ^ byte) & 0xff]! ^ (c >>> 8);
    return (c ^ 0xffffffff) >>> 0;
  };
  const chunk = (type: string, data: Buffer) => {
    const length = Buffer.alloc(4);
    length.writeUInt32BE(data.length);
    const body = Buffer.concat([Buffer.from(type), data]);
    const sum = Buffer.alloc(4);
    sum.writeUInt32BE(crc(body));
    return Buffer.concat([length, body, sum]);
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(size, 0);
  header.writeUInt32BE(size, 4);
  header.set([8, 2, 0, 0, 0], 8);
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk("IHDR", header),
    chunk("IDAT", deflateSync(raw)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

test("M2.6: enhance a prompt, see the diff, undo it", async ({ page, backend }) => {
  await backend.api("/api/connections/anthropic", {
    method: "PUT",
    body: JSON.stringify({ api_key: "fs-test-ok-anthropic-0123456789" }),
  });
  await page.goto(`${backend.url}/auth/login?token=${backend.token}&next=/image`);
  const prompt = page.getByTestId("prompt-input");
  await prompt.fill("red door at night");
  await page.getByTestId("enhance-button").click();
  const panel = page.getByTestId("enhance-panel");
  await expect(panel).toBeVisible();
  await expect(panel.getByTestId("enhance-diff").locator("ins").first()).toBeVisible();
  await expect(panel).toContainText("Described the light");
  await expect(prompt).toContainText("red door at night, framed as a medium-wide shot");
  await panel.getByTestId("enhance-undo").click();
  await expect(prompt).toHaveText("red door at night");
  await expect(panel).toHaveCount(0);
});

test("M2.6: describe an image and find it by what it shows", async ({ page, backend }) => {
  await backend.api("/api/connections/anthropic", {
    method: "PUT",
    body: JSON.stringify({ api_key: "fs-test-ok-anthropic-0123456789" }),
  });
  const form = new FormData();
  form.append("files", new Blob([png()], { type: "image/png" }), "IMG_2041.png");
  const uploaded = await fetch(`${backend.url}/api/assets/upload`, {
    method: "POST",
    headers: { Authorization: `Bearer ${backend.token}` },
    body: form,
  });
  const [{ asset }] = await uploaded.json();
  await page.goto(`${backend.url}/auth/login?token=${backend.token}&next=/assets?view=${asset.id}`);
  const viewer = page.getByTestId("asset-viewer");
  await viewer.getByTestId("describe-asset").click();
  await expect(viewer.getByTestId("asset-caption")).toHaveText("A picture from the library, described offline.", {
    timeout: 15_000,
  });
  await viewer.getByTestId("suggest-names").click();
  await viewer.getByTestId("name-option").filter({ hasText: "Quiet frame" }).click();
  await expect(viewer).toContainText("Quiet frame");
  await page.keyboard.press("Escape");
  const results = await (await backend.api("/api/assets?q=described%20offline")).json();
  expect(results.items.map((item: { id: string }) => item.id)).toContain(asset.id);

  // The switches: per project, and for the library outside projects.
  const project = await (
    await backend.api("/api/projects", { method: "POST", body: JSON.stringify({ name: "Coast" }) })
  ).json();
  await page.goto(`${backend.url}/studio/${project.id}?tab=settings`);
  const toggle = page.getByTestId("project-describe");
  await expect(toggle).toHaveAttribute("aria-pressed", "false");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-pressed", "true");
  await page.goto(`${backend.url}/settings`);
  await page.getByTestId("describe-library").check();
  await expect.poll(async () => (await (await backend.api("/api/settings")).json()).describe_library).toBe(true);
});
