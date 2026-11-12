// Helpers shared by the dev, start and setup scripts. Plain Node, no
// dependencies, so they run on Linux, macOS and Windows before `bun install`.
import { spawn, spawnSync } from "node:child_process";
import { existsSync, readFileSync, statSync } from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const IS_WINDOWS = process.platform === "win32";

const COLORS = { api: 32, ui: 36, app: 35, setup: 33, dev: 33 };

export function log(label, message) {
  const color = COLORS[label] ?? 37;
  const prefix = process.stdout.isTTY ? `\x1b[${color}m[${label}]\x1b[0m` : `[${label}]`;
  console.log(`${prefix} ${message}`);
}

/** Path of an executable on PATH (or in the usual installer folders), or null. */
export function which(name) {
  const exts = IS_WINDOWS ? (process.env.PATHEXT ?? ".EXE;.CMD;.BAT").split(";") : [""];
  const dirs = (process.env.PATH ?? "").split(path.delimiter).filter(Boolean);
  dirs.push(path.join(os.homedir(), ".local", "bin"), path.join(os.homedir(), ".cargo", "bin"));
  dirs.push(path.join(os.homedir(), ".bun", "bin"));
  for (const dir of dirs) {
    for (const ext of exts) {
      const candidate = path.join(dir, name + ext.toLowerCase());
      if (existsSync(candidate)) return candidate;
      const upper = path.join(dir, name + ext);
      if (existsSync(upper)) return upper;
    }
  }
  return null;
}

export function require_(tool, hint) {
  const found = which(tool);
  if (!found) {
    console.error(`FrameStudio needs '${tool}'. ${hint}`);
    process.exit(1);
  }
  return found;
}

/** Where development keeps its data, apart from an installed app's data. */
export function devDataDir() {
  return process.env.FRAMESTUDIO_DATA_DIR?.trim() || path.join(ROOT, ".data");
}

export function electronBinary() {
  const require = createRequire(path.join(ROOT, "desktop", "package.json"));
  return require("electron");
}

/**
 * Chromium's sandbox needs either unprivileged user namespaces or a root-owned
 * SUID `chrome-sandbox`. Ubuntu 24.04+ blocks the first through AppArmor, and
 * a node_modules install cannot provide the second, so development and test
 * runs there fall back to --no-sandbox (packaged builds install the helper or
 * an AppArmor profile instead). Set FRAMESTUDIO_ELECTRON_SANDBOX=1 to refuse
 * the fallback after fixing the helper yourself.
 */
export function electronSandboxArgs(binary = electronBinary()) {
  if (process.platform !== "linux" || process.env.FRAMESTUDIO_ELECTRON_SANDBOX === "1") return [];
  const helper = path.join(path.dirname(binary), "chrome-sandbox");
  try {
    const stat = statSync(helper);
    if (stat.uid === 0 && (stat.mode & 0o4000) !== 0) return [];
  } catch {
    // no helper: fall through to the namespace check
  }
  const read = (file) => {
    try {
      return readFileSync(file, "utf8").trim();
    } catch {
      return null;
    }
  };
  const restricted =
    read("/proc/sys/kernel/apparmor_restrict_unprivileged_userns") === "1" ||
    read("/proc/sys/kernel/unprivileged_userns_clone") === "0";
  if (!restricted) return [];
  if (!electronSandboxArgs.warned) {
    electronSandboxArgs.warned = true;
    log(
      "app",
      "Chromium's sandbox is unavailable here (user namespaces restricted, chrome-sandbox not SUID root); " +
        `running Electron with --no-sandbox for development. To enable it: sudo chown root:root '${helper}' && sudo chmod 4755 '${helper}'`,
    );
  }
  return ["--no-sandbox"];
}

/** Spawn a long-running child in its own process group, prefixing its output. */
export function start(label, command, args, options = {}) {
  const child = spawn(command, args, {
    cwd: ROOT,
    env: { ...process.env, FORCE_COLOR: "1", ...options.env },
    stdio: ["pipe", "pipe", "pipe"],
    detached: !IS_WINDOWS,
    windowsHide: true,
  });
  const relay = (stream) => (chunk) => {
    for (const line of chunk.toString().split(/\r?\n/)) if (line.trim()) stream.write(`${prefix(label)} ${line}\n`);
  };
  child.stdout.on("data", relay(process.stdout));
  child.stderr.on("data", relay(process.stderr));
  child.on("error", (error) => log(label, `failed to start: ${error.message}`));
  return child;
}

function prefix(label) {
  const color = COLORS[label] ?? 37;
  return process.stdout.isTTY ? `\x1b[${color}m[${label}]\x1b[0m` : `[${label}]`;
}

/** Stop a child and everything it started. */
export function stopTree(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  try {
    child.stdin?.end();
  } catch {
    // already closed
  }
  if (IS_WINDOWS) {
    spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"], { stdio: "ignore" });
    return;
  }
  try {
    process.kill(-child.pid, "SIGTERM");
  } catch {
    child.kill("SIGTERM");
  }
}

export async function waitForHttp(url, { timeoutMs = 120_000, ok = (r) => r.ok, alive = () => true } = {}) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!alive()) throw new Error(`process for ${url} exited`);
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(2000) });
      if (ok(response)) return response;
    } catch {
      // not up yet
    }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  throw new Error(`${url} did not answer within ${Math.round(timeoutMs / 1000)} s`);
}

export function readToken(dataDir) {
  try {
    return readFileSync(path.join(dataDir, "auth_token"), "utf8").trim();
  } catch {
    return null;
  }
}

/** Run a command to completion, inheriting output; exit on failure. */
export function run(label, command, args, options = {}) {
  log(label, [path.basename(command), ...args].join(" "));
  const result = spawnSync(command, args, { cwd: ROOT, stdio: "inherit", shell: false, ...options });
  if (result.status !== 0) {
    console.error(`[${label}] '${path.basename(command)} ${args.join(" ")}' failed (exit ${result.status}).`);
    process.exit(result.status ?? 1);
  }
}

export async function portFree(port, host = "127.0.0.1") {
  const net = await import("node:net");
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once("error", () => resolve(false));
    server.listen({ port, host, exclusive: true }, () => server.close(() => resolve(true)));
  });
}
