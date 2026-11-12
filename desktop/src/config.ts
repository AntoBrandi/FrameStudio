/**
 * Pure configuration logic for the desktop shell, kept free of Electron
 * imports so it can be unit-tested.
 *
 * The shell is a thin window on the UI the backend serves (plan decision D9).
 * It either attaches to a backend that is already running (development, or a
 * backend on another machine with a GPU) or spawns one itself.
 */
import path from "node:path";

export const APP_NAME = "FrameStudio";
export const DEFAULT_PORT = 8790;
export const COOKIE_NAME = "framestudio_session";
export const API_VERSION = 1;

export type Env = Record<string, string | undefined>;

export interface ShellConfig {
  mode: "attach" | "spawn";
  /** Backend to attach to (attach mode). */
  backendUrl?: string;
  /** Where the window loads the UI; defaults to the backend itself. */
  uiUrl?: string;
  dataDir: string;
  /** Token to sign the window in with; read from the data dir when absent. */
  token?: string;
  /** Preferred port when spawning a backend. */
  port: number;
  /** Keep the window hidden (automated tests). */
  hidden: boolean;
  repoRoot: string;
}

/** Mirrors platformdirs.user_data_dir("FrameStudio", appauthor=False) in the backend. */
export function defaultDataDir(platform: NodeJS.Platform, env: Env, home: string): string {
  const join = platform === "win32" ? path.win32.join : path.posix.join;
  if (platform === "win32") {
    const local = env.LOCALAPPDATA?.trim() || join(home, "AppData", "Local");
    return join(local, APP_NAME);
  }
  if (platform === "darwin") return join(home, "Library", "Application Support", APP_NAME);
  const xdg = env.XDG_DATA_HOME?.trim();
  return join(xdg || join(home, ".local", "share"), APP_NAME);
}

function normalizeUrl(value: string): string {
  const url = new URL(value);
  if (url.protocol !== "http:" && url.protocol !== "https:") throw new Error(`Unsupported backend URL: ${value}`);
  return url.origin;
}

export function resolveShellConfig(
  env: Env,
  options: { platform: NodeJS.Platform; home: string; repoRoot: string },
): ShellConfig {
  const backendUrl = env.FRAMESTUDIO_BACKEND_URL ? normalizeUrl(env.FRAMESTUDIO_BACKEND_URL) : undefined;
  const uiUrl = env.FRAMESTUDIO_UI_URL ? normalizeUrl(env.FRAMESTUDIO_UI_URL) : undefined;
  const port = Number.parseInt(env.FRAMESTUDIO_PORT ?? "", 10);
  return {
    mode: backendUrl ? "attach" : "spawn",
    backendUrl,
    uiUrl,
    dataDir: env.FRAMESTUDIO_DATA_DIR?.trim() || defaultDataDir(options.platform, env, options.home),
    token: env.FRAMESTUDIO_TOKEN?.trim() || undefined,
    port: Number.isInteger(port) && port > 0 && port < 65536 ? port : DEFAULT_PORT,
    hidden: env.FRAMESTUDIO_SHELL_HIDDEN === "1",
    repoRoot: options.repoRoot,
  };
}

/** Only the UI's own origin may be shown inside the window. */
export function isAppNavigation(url: string, appOrigin: string): boolean {
  try {
    return new URL(url).origin === appOrigin;
  } catch {
    return false;
  }
}

/** Links that leave the app open in the system browser, http(s) only. */
export function isExternalLink(url: string): boolean {
  try {
    const protocol = new URL(url).protocol;
    return protocol === "http:" || protocol === "https:";
  } catch {
    return false;
  }
}

export interface Health {
  status: string;
  app: string;
  version: string;
  api_version: number;
}

export function isFrameStudioHealth(body: unknown): body is Health {
  if (typeof body !== "object" || body === null) return false;
  const health = body as Partial<Health>;
  return health.status === "ok" && health.app === "framestudio" && typeof health.api_version === "number";
}

/** Permissions the UI may use; everything else is refused. */
export const ALLOWED_PERMISSIONS = new Set(["notifications", "clipboard-sanitized-write", "fullscreen"]);
