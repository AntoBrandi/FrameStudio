/** M1.4: 10,000 assets scroll smoothly (seeded rows, frame timing in Chrome). */
import { execFileSync } from "node:child_process";
import path from "node:path";
import { test as base, expect } from "@playwright/test";
import { ROOT, startBackend } from "./fixtures";

base("M1.4: a 10,000-asset library scrolls smoothly and stays virtualized", async ({ page }) => {
  base.setTimeout(180_000);
  const backend = await startBackend();
  try {
    const form = new FormData();
    const png = execFileSync("uv", ["run", "--directory", path.join(ROOT, "backend"), "python", "-c",
      "import sys,io;from PIL import Image;b=io.BytesIO();Image.new('RGB',(64,48),(90,120,200)).save(b,'PNG');sys.stdout.buffer.write(b.getvalue())"]); // prettier-ignore
    form.append("files", new Blob([png]), "template.png");
    await fetch(`${backend.url}/api/assets/upload`, {
      method: "POST",
      body: form,
      headers: { Authorization: `Bearer ${backend.token}` },
    });
    execFileSync("uv", ["run", "--directory", path.join(ROOT, "backend"), "python",
      "tests/fixtures/seed_library.py", backend.dataDir, "10000"]); // prettier-ignore

    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`${backend.url}/auth/login?token=${backend.token}&next=/assets`);
    await expect(page.getByTestId("asset-total")).toHaveText("10001 assets");

    const result = await page.evaluate(async () => {
      const gallery = document.querySelector<HTMLElement>("[data-testid=gallery]")!;
      const frames: number[] = [];
      let maxCards = 0;
      let last = performance.now();
      await new Promise<void>((resolve) => {
        const step = (now: number) => {
          frames.push(now - last);
          last = now;
          maxCards = Math.max(maxCards, document.querySelectorAll("[data-testid=asset-card]").length);
          gallery.scrollTop += 900;
          if (frames.length < 600) requestAnimationFrame(step);
          else resolve();
        };
        requestAnimationFrame(step);
      });
      frames.sort((a, b) => a - b);
      return {
        p95: frames[Math.floor(frames.length * 0.95)]!,
        maxCards,
        scrolled: gallery.scrollTop,
      };
    });
    console.log(
      `frame p95 ${result.p95.toFixed(1)} ms, max cards in DOM ${result.maxCards}, scrolled ${result.scrolled}px`,
    );
    expect(result.scrolled).toBeGreaterThan(100_000); // pages kept loading while scrolling
    expect(result.maxCards).toBeLessThan(200); // only what is near the viewport exists
    expect(result.p95).toBeLessThan(50); // smooth: 95% of frames under 50 ms
  } finally {
    await backend.stop();
  }
});
