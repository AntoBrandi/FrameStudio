/** Start (or find) the backend and wait until it answers. */
import { type ChildProcess, spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { API_VERSION, type Health, isFrameStudioHealth, type ShellConfig } from "./config";

export interface BackendHandle {
  url: string;
  child?: ChildProcess;
  logTail: () => string;
  stop: () => Promise<void>;
}

const LOG_LINES = 200;

function portIsFree(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once("error", () => resolve(false));
    server.listen({ port, host: "127.0.0.1", exclusive: true }, () => server.close(() => resolve(true)));
  });
}

async function anyFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen({ port: 0, host: "127.0.0.1" }, () => {
      const address = server.address();
      server.close(() => resolve(typeof address === "object" && address ? address.port : 0));
    });
  });
}

export async function choosePort(preferred: number): Promise<number> {
  return (await portIsFree(preferred)) ? preferred : anyFreePort();
}

/** Locate uv: PATH first, then the installers' default locations. */
export function findUv(env: NodeJS.ProcessEnv = process.env): string {
  if (env.FRAMESTUDIO_UV) return env.FRAMESTUDIO_UV;
  const exe = process.platform === "win32" ? "uv.exe" : "uv";
  const dirs = (env.PATH ?? "").split(path.delimiter).filter(Boolean);
  dirs.push(path.join(os.homedir(), ".local", "bin"), path.join(os.homedir(), ".cargo", "bin"));
  for (const dir of dirs) {
    const candidate = path.join(dir, exe);
    if (existsSync(candidate)) return candidate;
  }
  return exe;
}

export async function waitForHealth(
  url: string,
  timeoutMs: number,
  isAlive: () => boolean = () => true,
): Promise<Health> {
  const deadline = Date.now() + timeoutMs;
  let lastError = "no answer yet";
  while (Date.now() < deadline) {
    if (!isAlive()) throw new Error("The backend process exited before it was ready.");
    try {
      const response = await fetch(`${url}/api/health`, { signal: AbortSignal.timeout(2000) });
      const body: unknown = await response.json();
      if (response.ok && isFrameStudioHealth(body)) {
        if (body.api_version !== API_VERSION) {
          throw new Error(`Backend API version ${body.api_version} does not match this app (${API_VERSION}).`);
        }
        return body;
      }
      lastError = `HTTP ${response.status}`;
    } catch (error) {
      if (error instanceof Error && error.message.startsWith("Backend API version")) throw error;
      lastError = error instanceof Error ? error.message : String(error);
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`The backend at ${url} did not become ready (${lastError}).`);
}

export function readToken(dataDir: string): string | undefined {
  try {
    return readFileSync(path.join(dataDir, "auth_token"), "utf8").trim() || undefined;
  } catch {
    return undefined;
  }
}

export function attachBackend(url: string): BackendHandle {
  return { url, logTail: () => "", stop: async () => undefined };
}

/** Spawn the backend from the source checkout (packaged builds bundle Python in M10). */
export async function spawnBackend(config: ShellConfig): Promise<BackendHandle> {
  const port = await choosePort(config.port);
  const lines: string[] = [];
  const remember = (chunk: Buffer, stream: NodeJS.WriteStream) => {
    stream.write(chunk);
    for (const line of chunk.toString("utf8").split(/\r?\n/)) {
      if (line) lines.push(line);
    }
    lines.splice(0, Math.max(0, lines.length - LOG_LINES));
  };
  const child = spawn(
    findUv(),
    [
      "run",
      "--directory",
      path.join(config.repoRoot, "backend"),
      "framestudio",
      "serve",
      "--port",
      String(port),
      "--data-dir",
      config.dataDir,
      "--exit-on-stdin-eof",
    ],
    { stdio: ["pipe", "pipe", "pipe"], env: { ...process.env, PYTHONUNBUFFERED: "1" }, windowsHide: true },
  );
  child.stdout?.on("data", (chunk: Buffer) => remember(chunk, process.stdout));
  child.stderr?.on("data", (chunk: Buffer) => remember(chunk, process.stderr));
  child.on("error", (error) => lines.push(`Could not start the backend: ${error.message}`));

  const stop = async () => {
    if (child.exitCode !== null || child.signalCode !== null) return;
    const exited = new Promise<void>((resolve) => child.once("exit", () => resolve()));
    child.stdin?.end(); // the backend exits on stdin EOF
    const timer = setTimeout(() => child.kill(), 5000);
    await exited;
    clearTimeout(timer);
  };
  return { url: `http://127.0.0.1:${port}`, child, logTail: () => lines.join("\n"), stop };
}
