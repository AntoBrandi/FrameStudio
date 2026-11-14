import { describe, expect, it } from "vitest";
import {
  defaultDataDir,
  isAppNavigation,
  isExternalLink,
  isFrameStudioHealth,
  isLocalBackend,
  isRevealable,
  resolveShellConfig,
} from "./config";

describe("data directory (mirrors platformdirs in the backend)", () => {
  it("follows XDG on Linux", () => {
    expect(defaultDataDir("linux", {}, "/home/u")).toBe("/home/u/.local/share/FrameStudio");
    expect(defaultDataDir("linux", { XDG_DATA_HOME: "/data" }, "/home/u")).toBe("/data/FrameStudio");
    expect(defaultDataDir("linux", { XDG_DATA_HOME: "  " }, "/home/u")).toBe("/home/u/.local/share/FrameStudio");
  });

  it("uses Application Support on macOS", () => {
    expect(defaultDataDir("darwin", {}, "/Users/u")).toBe("/Users/u/Library/Application Support/FrameStudio");
  });

  it("uses LOCALAPPDATA on Windows", () => {
    expect(defaultDataDir("win32", { LOCALAPPDATA: "C:\\Users\\u\\AppData\\Local" }, "C:\\Users\\u")).toBe(
      "C:\\Users\\u\\AppData\\Local\\FrameStudio",
    );
  });
});

describe("shell config", () => {
  const base = { platform: "linux" as const, home: "/home/u", repoRoot: "/repo" };

  it("spawns a backend by default", () => {
    const config = resolveShellConfig({}, base);
    expect(config.mode).toBe("spawn");
    expect(config.port).toBe(8790);
    expect(config.dataDir).toBe("/home/u/.local/share/FrameStudio");
    expect(config.hidden).toBe(false);
  });

  it("attaches to a running backend", () => {
    const config = resolveShellConfig(
      {
        FRAMESTUDIO_BACKEND_URL: "http://127.0.0.1:9000/ignored/path",
        FRAMESTUDIO_UI_URL: "http://localhost:5173",
        FRAMESTUDIO_DATA_DIR: "/tmp/fs",
        FRAMESTUDIO_TOKEN: " abc ",
        FRAMESTUDIO_SHELL_HIDDEN: "1",
      },
      base,
    );
    expect(config).toMatchObject({
      mode: "attach",
      backendUrl: "http://127.0.0.1:9000",
      uiUrl: "http://localhost:5173",
      dataDir: "/tmp/fs",
      token: "abc",
      hidden: true,
    });
  });

  it("rejects non-http backend URLs and bad ports", () => {
    expect(() => resolveShellConfig({ FRAMESTUDIO_BACKEND_URL: "file:///etc" }, base)).toThrow();
    expect(resolveShellConfig({ FRAMESTUDIO_PORT: "99999" }, base).port).toBe(8790);
  });
});

describe("navigation policy", () => {
  it("keeps the window on the app origin", () => {
    expect(isAppNavigation("http://127.0.0.1:8790/image?view=1", "http://127.0.0.1:8790")).toBe(true);
    expect(isAppNavigation("http://127.0.0.1:9999/", "http://127.0.0.1:8790")).toBe(false);
    expect(isAppNavigation("https://example.com", "http://127.0.0.1:8790")).toBe(false);
    expect(isAppNavigation("not a url", "http://127.0.0.1:8790")).toBe(false);
  });

  it("opens only web links externally", () => {
    expect(isExternalLink("https://example.com")).toBe(true);
    expect(isExternalLink("file:///etc/passwd")).toBe(false);
    expect(isExternalLink("javascript:alert(1)")).toBe(false);
  });

  it("recognises the backend's health answer", () => {
    expect(isFrameStudioHealth({ status: "ok", app: "framestudio", version: "0.1.0", api_version: 1 })).toBe(true);
    expect(isFrameStudioHealth({ status: "ok", app: "other", api_version: 1 })).toBe(false);
    expect(isFrameStudioHealth(null)).toBe(false);
  });
});

describe("reveal in folder", () => {
  const base = { platform: "linux" as const, home: "/home/u", repoRoot: "/repo" };

  it("only reveals files inside the media folder", () => {
    expect(isRevealable("/data/media/ab/cd/x.png", "/data", "linux")).toBe(true);
    expect(isRevealable("/data/media/../auth_token", "/data", "linux")).toBe(false);
    expect(isRevealable("/data/auth_token", "/data", "linux")).toBe(false);
    expect(isRevealable("/data/media", "/data", "linux")).toBe(false);
    expect(isRevealable("relative/x.png", "/data", "linux")).toBe(false);
    expect(isRevealable(42, "/data", "linux")).toBe(false);
    expect(isRevealable("C:\\data\\media\\x.png", "C:\\data", "win32")).toBe(true);
  });

  it("knows when the backend is on this machine", () => {
    expect(isLocalBackend(resolveShellConfig({}, base))).toBe(true);
    expect(isLocalBackend(resolveShellConfig({ FRAMESTUDIO_BACKEND_URL: "http://127.0.0.1:9" }, base))).toBe(true);
    expect(isLocalBackend(resolveShellConfig({ FRAMESTUDIO_BACKEND_URL: "http://gpu-box:8790" }, base))).toBe(false);
  });
});
