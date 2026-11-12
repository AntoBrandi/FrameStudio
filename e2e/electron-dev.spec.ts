import { type ChildProcess, spawn } from "node:child_process";
import path from "node:path";
import { test as base, expect } from "@playwright/test";
import { appWindow, freePort, launchShell, ROOT, startBackend } from "./fixtures";

/** `bun run dev`: the window loads the UI from Vite's dev server, which proxies
 * the API, the session cookie and the event stream to the backend. */
base("M0.1: the dev topology (Vite proxy) signs in, streams events and generates", async () => {
  const uiPort = await freePort();
  const uiUrl = `http://localhost:${uiPort}`;
  const backend = await startBackend({ FRAMESTUDIO_ALLOWED_ORIGINS: uiUrl });
  let vite: ChildProcess | undefined;
  try {
    vite = spawn("bun", ["run", "--cwd", path.join(ROOT, "ui"), "dev"], {
      env: { ...process.env, FRAMESTUDIO_BACKEND_URL: backend.url, FRAMESTUDIO_UI_PORT: String(uiPort) },
      stdio: "ignore",
      detached: process.platform !== "win32",
    });
    await expect
      .poll(
        () =>
          fetch(uiUrl)
            .then((r) => r.status)
            .catch(() => 0),
        { timeout: 30_000 },
      )
      .toBe(200);

    const app = await launchShell({
      FRAMESTUDIO_BACKEND_URL: backend.url,
      FRAMESTUDIO_UI_URL: uiUrl,
      FRAMESTUDIO_TOKEN: backend.token,
      FRAMESTUDIO_DATA_DIR: backend.dataDir,
    });
    try {
      const window = await appWindow(app, uiUrl);
      await expect(window.getByRole("main").getByText("Backend: connected")).toBeVisible();
      await window.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Image" }).click();
      await window.getByTestId("prompt-input").fill("through the dev proxy");
      await window.getByTestId("generate-button").click();
      // The card flips to the finished image only through the proxied event stream.
      await expect(window.getByRole("img", { name: "through the dev proxy" })).toBeVisible({ timeout: 15_000 });
    } finally {
      await app.close();
    }
  } finally {
    if (vite?.pid) {
      try {
        process.kill(process.platform === "win32" ? vite.pid : -vite.pid, "SIGTERM");
      } catch {
        vite.kill();
      }
    }
    await backend.stop();
  }
});
