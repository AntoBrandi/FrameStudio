/**
 * FrameStudio desktop shell. It opens a window on the UI that the backend
 * serves and adds nothing but OS integration (decision D9): no app logic lives
 * here or in IPC, so the same UI works in any browser.
 */
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { app, BrowserWindow, Menu, session, shell } from "electron";
import { attachBackend, type BackendHandle, readToken, spawnBackend, waitForHealth } from "./backend";
import {
  ALLOWED_PERMISSIONS,
  APP_NAME,
  COOKIE_NAME,
  isAppNavigation,
  isExternalLink,
  resolveShellConfig,
} from "./config";

const here = path.dirname(fileURLToPath(import.meta.url));
const config = resolveShellConfig(process.env, {
  platform: process.platform,
  home: os.homedir(),
  repoRoot: process.env.FRAMESTUDIO_REPO_ROOT ?? path.resolve(here, "..", ".."),
});

app.setName(APP_NAME);
if (process.env.FRAMESTUDIO_DATA_DIR) {
  // Development and test instances keep their browser profile (and their
  // single-instance lock) next to their own data, apart from the installed app.
  app.setPath("userData", path.join(config.dataDir, "shell"));
}

let backend: BackendHandle | undefined;
let mainWindow: BrowserWindow | undefined;
let appOrigin = "";

function page(title: string, body: string): string {
  const html = `<!doctype html><html><head><meta charset="utf-8"><title>${title}</title>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0e1012;color:#e8eaed;
font:14px/1.6 system-ui,sans-serif}main{max-width:44rem;padding:2rem}h1{font-weight:600;font-size:20px}
pre{white-space:pre-wrap;background:#16191d;border:1px solid #2c3138;border-radius:8px;padding:12px;
max-height:50vh;overflow:auto;font-size:12px}.dot{display:inline-block;width:8px;height:8px;border-radius:50%;
background:#f2a65a;margin-right:8px;animation:p 1s infinite alternate}@keyframes p{to{opacity:.3}}</style>
</head><body><main>${body}</main></body></html>`;
  return `data:text/html;charset=utf-8,${encodeURIComponent(html)}`;
}

const escape = (text: string) =>
  text.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c] ?? c);

function createWindow(): BrowserWindow {
  const window = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 960,
    minHeight: 600,
    show: false,
    backgroundColor: "#0e1012",
    title: APP_NAME,
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(here, "preload.cjs"),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      webviewTag: false,
      spellcheck: false,
      backgroundThrottling: !config.hidden,
    },
  });
  if (!config.hidden) window.once("ready-to-show", () => window.show());

  window.webContents.setWindowOpenHandler(({ url }) => {
    if (!isAppNavigation(url, appOrigin) && isExternalLink(url)) void shell.openExternal(url);
    return { action: "deny" };
  });
  window.webContents.on("will-navigate", (event, url) => {
    if (url.startsWith("data:") || isAppNavigation(url, appOrigin)) return;
    event.preventDefault();
    if (isExternalLink(url)) void shell.openExternal(url);
  });
  return window;
}

function hardenSession(): void {
  session.defaultSession.setPermissionRequestHandler((_contents, permission, callback) =>
    callback(ALLOWED_PERMISSIONS.has(permission)),
  );
  session.defaultSession.setPermissionCheckHandler((_contents, permission) => ALLOWED_PERMISSIONS.has(permission));
}

async function start(window: BrowserWindow): Promise<void> {
  await window.loadURL(
    page(APP_NAME, `<h1><span class="dot"></span>Starting ${APP_NAME}…</h1><p>Waiting for the backend.</p>`),
  );
  try {
    backend = config.mode === "attach" ? attachBackend(config.backendUrl!) : await spawnBackend(config);
    const child = backend.child;
    await waitForHealth(backend.url, 120_000, () => !child || child.exitCode === null);
    const uiUrl = config.uiUrl ?? backend.url;
    appOrigin = new URL(uiUrl).origin;
    const token = config.token ?? readToken(config.dataDir);
    if (token) {
      // HttpOnly session cookie: the same credential the browser sign-in sets.
      await session.defaultSession.cookies.set({
        url: uiUrl,
        name: COOKIE_NAME,
        value: token,
        httpOnly: true,
        sameSite: "strict",
        secure: uiUrl.startsWith("https:"),
      });
    }
    await window.loadURL(uiUrl);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const tail = backend?.logTail() ?? "";
    await window.loadURL(
      page(
        `${APP_NAME}: backend problem`,
        `<h1>FrameStudio could not reach its backend</h1><p>${escape(message)}</p>` +
          (tail ? `<pre>${escape(tail)}</pre>` : "") +
          `<p>Restart the app to try again.</p>`,
      ),
    );
  }
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (!mainWindow) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  });

  app.on("web-contents-created", (_event, contents) => {
    contents.on("will-attach-webview", (event) => event.preventDefault());
  });

  void app.whenReady().then(() => {
    if (process.platform !== "darwin") Menu.setApplicationMenu(null);
    hardenSession();
    mainWindow = createWindow();
    void start(mainWindow);
  });

  app.on("window-all-closed", () => app.quit());

  let stopping = false;
  app.on("before-quit", (event) => {
    if (stopping || !backend?.child) return;
    stopping = true;
    event.preventDefault();
    void backend.stop().finally(() => app.quit());
  });
}
