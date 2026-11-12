import { type ChildProcess, spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { _electron, type ElectronApplication, type Page, test as base } from "@playwright/test";
import { electronBinary, electronSandboxArgs } from "../scripts/lib.mjs";

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

export interface Backend {
  url: string;
  token: string;
  dataDir: string;
  api: (route: string, init?: RequestInit) => Promise<Response>;
}

export async function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen({ port: 0, host: "127.0.0.1" }, () => {
      const address = server.address();
      server.close(() => resolve(typeof address === "object" && address ? address.port : 0));
    });
  });
}

export async function waitForHealth(url: string, timeoutMs = 60_000, alive = () => true): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!alive()) throw new Error("backend exited before it was ready");
    try {
      const response = await fetch(`${url}/api/health`);
      if (response.ok) return;
    } catch {
      // not yet
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error(`backend at ${url} did not start`);
}

export async function startBackend(
  extraEnv: Record<string, string> = {},
): Promise<Backend & { child: ChildProcess; stop: () => Promise<void> }> {
  const dataDir = mkdtempSync(path.join(os.tmpdir(), "framestudio-e2e-"));
  const port = await freePort();
  const token = `e2e-${Math.random().toString(36).slice(2)}${"x".repeat(32)}`;
  const url = `http://127.0.0.1:${port}`;
  const child = spawn(
    "uv",
    [
      "run",
      "--directory",
      path.join(ROOT, "backend"),
      "framestudio",
      "serve",
      "--port",
      String(port),
      "--data-dir",
      dataDir,
      "--exit-on-stdin-eof",
      "--log-level",
      "warning",
    ],
    { env: { ...process.env, FRAMESTUDIO_TOKEN: token, ...extraEnv }, stdio: ["pipe", "inherit", "inherit"] },
  );
  await waitForHealth(url, 90_000, () => child.exitCode === null);
  const api = (route: string, init: RequestInit = {}) =>
    fetch(`${url}${route}`, {
      ...init,
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", ...init.headers },
    });
  await api("/api/settings", { method: "PATCH", body: JSON.stringify({ mock_latency_seconds: 0.4 }) });
  const stop = async () => {
    const exited = new Promise((resolve) => child.once("exit", resolve));
    child.stdin?.end();
    const timer = setTimeout(() => child.kill("SIGKILL"), 8000);
    await exited;
    clearTimeout(timer);
    rmSync(dataDir, { recursive: true, force: true });
  };
  return { url, token, dataDir, api, child, stop };
}

export async function launchShell(env: Record<string, string>): Promise<ElectronApplication> {
  const binary = electronBinary();
  return _electron.launch({
    executablePath: binary,
    args: [...electronSandboxArgs(binary), path.join(ROOT, "desktop")],
    env: { ...(process.env as Record<string, string>), FRAMESTUDIO_SHELL_HIDDEN: "1", ...env },
  });
}

/** The shell's window once it has left the loading page for the app. */
export async function appWindow(app: ElectronApplication, origin: string): Promise<Page> {
  const page = await app.firstWindow();
  await page.waitForURL((url) => url.origin === origin, { timeout: 60_000 });
  return page;
}

export const test = base.extend<{ shell: ElectronApplication; window: Page }, { backend: Backend }>({
  backend: [
    // eslint-disable-next-line no-empty-pattern -- Playwright fixtures must destructure
    async ({}, use) => {
      const backend = await startBackend();
      await use(backend);
      await backend.stop();
    },
    { scope: "worker" },
  ],
  shell: async ({ backend }, use) => {
    const app = await launchShell({
      FRAMESTUDIO_BACKEND_URL: backend.url,
      FRAMESTUDIO_TOKEN: backend.token,
      FRAMESTUDIO_DATA_DIR: backend.dataDir,
    });
    await use(app);
    await app.close();
  },
  window: async ({ shell, backend }, use) => {
    await use(await appWindow(shell, backend.url));
  },
});

export { expect } from "@playwright/test";
