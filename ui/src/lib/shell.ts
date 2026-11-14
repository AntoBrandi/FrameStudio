/** What the desktop shell adds when the UI runs inside it (see desktop/src/preload.ts). */
export interface ShellBridge {
  kind: "electron";
  platform: string;
  /** True when the backend runs on this machine, so its paths are local. */
  canReveal: boolean;
  revealInFolder: (path: string) => Promise<boolean>;
}

export function shellBridge(): ShellBridge | undefined {
  return (globalThis as { frameStudioShell?: ShellBridge }).frameStudioShell;
}
