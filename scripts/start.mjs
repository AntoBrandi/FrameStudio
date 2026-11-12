#!/usr/bin/env node
// `bun run start`: build if needed, then open the desktop app the way an
// installed copy runs. Electron starts its own backend, which serves the built
// UI, on the per-user data directory (or FRAMESTUDIO_DATA_DIR).
import { existsSync } from "node:fs";
import path from "node:path";
import { electronBinary, electronSandboxArgs, require_, ROOT, run, start } from "./lib.mjs";

const bun = require_("bun", "Install it from https://bun.sh.");
require_("uv", "Install it from https://docs.astral.sh/uv/.");
if (!existsSync(path.join(ROOT, "ui", "dist", "index.html")) || process.argv.includes("--build")) {
  run("app", bun, ["run", "--cwd", "ui", "build"]);
}
run("app", bun, ["run", "--cwd", "desktop", "build"]);
const binary = electronBinary();
const app = start("app", binary, [...electronSandboxArgs(binary), path.join(ROOT, "desktop")]);
app.on("exit", (code) => process.exit(code ?? 0));
process.on("SIGINT", () => app.kill("SIGTERM"));
