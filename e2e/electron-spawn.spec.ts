import { mkdtempSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test as base, expect } from "@playwright/test";
import { appWindow, freePort, launchShell } from "./fixtures";

/** The installed-app path: Electron starts its own backend, which serves the
 * built UI, and stops it on quit. */
base("M0.1: the shell spawns its own backend and stops it on quit", async () => {
  const dataDir = mkdtempSync(path.join(os.tmpdir(), "framestudio-spawn-"));
  const port = await freePort();
  const app = await launchShell({ FRAMESTUDIO_DATA_DIR: dataDir, FRAMESTUDIO_PORT: String(port) });
  try {
    const window = await appWindow(app, `http://127.0.0.1:${port}`);
    await expect(window.getByRole("main").getByText("Backend: connected")).toBeVisible();
  } finally {
    await app.close();
  }
  await expect
    .poll(
      async () => {
        try {
          await fetch(`http://127.0.0.1:${port}/api/health`);
          return "up";
        } catch {
          return "down";
        }
      },
      { timeout: 15_000 },
    )
    .toBe("down");
  rmSync(dataDir, { recursive: true, force: true });
});
