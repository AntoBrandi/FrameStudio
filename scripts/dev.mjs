#!/usr/bin/env node
// `bun run dev`: backend (with reload) + Vite dev server + the Electron window.
// `bun run dev:web`: the same without Electron; open the printed link in a browser.
import { existsSync } from "node:fs";
import path from "node:path";
import {
  devDataDir,
  electronBinary,
  electronSandboxArgs,
  log,
  portFree,
  readToken,
  require_,
  ROOT,
  run,
  start,
  stopTree,
  waitForHttp,
} from "./lib.mjs";

const web = process.argv.includes("--web");
const apiPort = Number(process.env.FRAMESTUDIO_PORT ?? 8790);
const uiPort = Number(process.env.FRAMESTUDIO_UI_PORT ?? 5173);
const dataDir = devDataDir();
const backendUrl = `http://127.0.0.1:${apiPort}`;
const uiUrl = `http://localhost:${uiPort}`;

const uv = require_("uv", "Install it from https://docs.astral.sh/uv/ and run `bun run setup`.");
const bun = require_("bun", "Install it from https://bun.sh and run `bun run setup`.");
if (!existsSync(path.join(ROOT, "node_modules"))) {
  console.error("Dependencies are missing. Run `bun run setup` first.");
  process.exit(1);
}
for (const [port, what] of [
  [apiPort, "FRAMESTUDIO_PORT"],
  [uiPort, "FRAMESTUDIO_UI_PORT"],
]) {
  if (!(await portFree(port))) {
    console.error(`Port ${port} is in use. Stop what is using it, or set ${what} to another port.`);
    process.exit(1);
  }
}

const children = [];
let shuttingDown = false;
function shutdown(code = 0) {
  if (shuttingDown) return;
  shuttingDown = true;
  log("dev", "stopping…");
  for (const child of children.reverse()) stopTree(child);
  setTimeout(() => process.exit(code), 1500).unref();
}
process.on("SIGINT", () => shutdown(0));
process.on("SIGTERM", () => shutdown(0));

const shared = { FRAMESTUDIO_DATA_DIR: dataDir };
log("dev", `data directory: ${dataDir}`);

const api = start(
  "api",
  uv,
  ["run", "--directory", "backend", "framestudio", "serve", "--reload", "--dev", "--port", String(apiPort)],
  { env: { ...shared, FRAMESTUDIO_ALLOWED_ORIGINS: uiUrl, PYTHONUNBUFFERED: "1" } },
);
children.push(api);
const ui = start("ui", bun, ["run", "--cwd", "ui", "dev"], {
  env: { FRAMESTUDIO_BACKEND_URL: backendUrl, FRAMESTUDIO_UI_PORT: String(uiPort) },
});
children.push(ui);
for (const child of [api, ui]) {
  child.on("exit", (code) => {
    if (!shuttingDown) {
      log("dev", `a dev process exited (code ${code}); stopping the rest`);
      shutdown(code ?? 1);
    }
  });
}

try {
  await waitForHttp(`${backendUrl}/api/health`, { alive: () => api.exitCode === null });
  await waitForHttp(uiUrl, { alive: () => ui.exitCode === null });
} catch (error) {
  log("dev", String(error));
  shutdown(1);
}

const token = readToken(dataDir);
log("dev", `UI with hot reload: ${uiUrl}`);
if (token) log("dev", `Sign in from a browser: ${uiUrl}/auth/login?token=${token}`);

if (!web && !shuttingDown) {
  run("app", bun, ["run", "--cwd", "desktop", "build"]);
  const binary = electronBinary();
  const app = start("app", binary, [...electronSandboxArgs(binary), path.join(ROOT, "desktop")], {
    env: { ...shared, FRAMESTUDIO_BACKEND_URL: backendUrl, FRAMESTUDIO_UI_URL: uiUrl },
  });
  children.push(app);
  app.on("exit", () => shutdown(0));
}
