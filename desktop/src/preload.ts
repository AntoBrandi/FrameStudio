/**
 * Tells the UI it runs inside the desktop shell. Shell-only extras (reveal in
 * folder, auto-update) will be added here; app logic never is.
 */
import { contextBridge } from "electron";

contextBridge.exposeInMainWorld("frameStudioShell", {
  kind: "electron",
  platform: process.platform,
});
