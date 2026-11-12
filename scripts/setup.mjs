#!/usr/bin/env node
// `bun run setup` (or `node scripts/setup.mjs`): make a fresh clone runnable on
// any machine. Installs JS and Python dependencies, fetches the Electron
// binary and builds the UI. Nothing here needs a GPU or an API key.
import { spawnSync } from "node:child_process";
import path from "node:path";
import { log, require_, ROOT, run, which } from "./lib.mjs";

const uv = require_(
  "uv",
  "Install it: https://docs.astral.sh/uv/getting-started/installation/ (Linux/macOS: curl -LsSf https://astral.sh/uv/install.sh | sh).",
);
const bun = require_("bun", "Install it: https://bun.sh (Linux/macOS: curl -fsSL https://bun.sh/install | bash).");

run("setup", bun, ["install"]);
run("setup", uv, ["sync", "--directory", "backend"]);

// Electron downloads its binary on first use; do it now so the first run is offline-safe.
const installer = path.join(ROOT, "desktop", "node_modules", "electron", "install.js");
run("setup", process.execPath, [installer]);

run("setup", bun, ["run", "--cwd", "ui", "build"]);
run("setup", bun, ["run", "--cwd", "desktop", "build"]);

if (!which("ffmpeg") || !which("ffprobe")) {
  log(
    "setup",
    "ffmpeg was not found. Images work without it; video and media metadata need it. " +
      "Install it (apt install ffmpeg · brew install ffmpeg · winget install ffmpeg) and make sure it is on PATH.",
  );
}
if (process.argv.includes("--with-browsers")) {
  spawnSync(bun, ["x", "playwright", "install", "chromium"], { cwd: ROOT, stdio: "inherit" });
}
log("setup", "Done. Run `bun run dev` for the app with hot reload, or `bun run start` for the built app.");
