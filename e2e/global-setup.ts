import { spawnSync } from "node:child_process";
import { existsSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

/** Newest modification time among a workspace's sources. */
function newestSource(dir: string): number {
  let newest = 0;
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (["node_modules", "dist", "out"].includes(entry.name) || entry.name.startsWith(".")) continue;
    const full = path.join(dir, entry.name);
    newest = Math.max(newest, entry.isDirectory() ? newestSource(full) : statSync(full).mtimeMs);
  }
  return newest;
}

/**
 * The E2E suite runs against built artifacts; build them when missing or older
 * than their sources (a stale build would test yesterday's UI).
 * FRAMESTUDIO_E2E_REBUILD=1 forces a build.
 */
export default function globalSetup(): void {
  const builds: [string, string][] = [
    ["ui", path.join(ROOT, "ui", "dist", "index.html")],
    ["desktop", path.join(ROOT, "desktop", "out", "main.js")],
  ];
  for (const [workspace, artifact] of builds) {
    const fresh = existsSync(artifact) && statSync(artifact).mtimeMs >= newestSource(path.join(ROOT, workspace));
    if (fresh && !process.env.FRAMESTUDIO_E2E_REBUILD) continue;
    const result = spawnSync("bun", ["run", "--cwd", workspace, "build"], { cwd: ROOT, stdio: "inherit" });
    if (result.status !== 0) throw new Error(`Building ${workspace} failed`);
  }
}
