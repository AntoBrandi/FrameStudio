/**
 * Tells the UI it runs inside the desktop shell and offers the few things only
 * the shell can do (reveal a file in the system file manager). App logic never
 * lives here.
 */
import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("frameStudioShell", {
  kind: "electron",
  platform: process.platform,
  // The backend's files are on this machine (not a remote backend).
  canReveal: process.argv.includes("--framestudio-local-backend"),
  revealInFolder: (target: string): Promise<boolean> => ipcRenderer.invoke("framestudio:reveal", target),
});
