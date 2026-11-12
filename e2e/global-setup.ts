import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

/** The E2E suite runs against built artifacts; build them when missing. */
export default function globalSetup(): void {
  const builds: [string, string][] = [
    ["ui", path.join(ROOT, "ui", "dist", "index.html")],
    ["desktop", path.join(ROOT, "desktop", "out", "main.js")],
  ];
  for (const [workspace, artifact] of builds) {
    if (existsSync(artifact) && !process.env.FRAMESTUDIO_E2E_REBUILD) continue;
    const result = spawnSync("bun", ["run", "--cwd", workspace, "build"], { cwd: ROOT, stdio: "inherit" });
    if (result.status !== 0) throw new Error(`Building ${workspace} failed`);
  }
}
