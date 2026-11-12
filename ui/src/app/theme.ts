import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect } from "react";
import { $api, type AppSettings } from "@/api/client";
import { SETTINGS_KEY } from "@/api/events";

export type ThemeSetting = AppSettings["theme"];
const STORAGE_KEY = "framestudio.theme";

export function resolveTheme(setting: ThemeSetting, prefersLight: boolean): "dark" | "light" {
  if (setting === "system") return prefersLight ? "light" : "dark";
  return setting;
}

function prefersLight(): boolean {
  return typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: light)").matches;
}

export function applyTheme(setting: ThemeSetting): void {
  document.documentElement.dataset.theme = resolveTheme(setting, prefersLight());
  try {
    localStorage.setItem(STORAGE_KEY, setting);
  } catch {
    // Storage can be unavailable (private windows); the backend setting still applies.
  }
}

function cachedTheme(): ThemeSetting {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    if (value === "dark" || value === "light" || value === "system") return value;
  } catch {
    // ignore
  }
  return "dark";
}

/** The theme from backend settings, applied to <html data-theme>. */
export function useTheme() {
  const queryClient = useQueryClient();
  const { data } = $api.useQuery("get", "/api/settings");
  const theme: ThemeSetting = data?.theme ?? cachedTheme();
  const patch = $api.useMutation("patch", "/api/settings");

  useEffect(() => {
    applyTheme(theme);
    if (theme !== "system" || typeof matchMedia !== "function") return;
    const media = matchMedia("(prefers-color-scheme: light)");
    const onChange = () => applyTheme("system");
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, [theme]);

  const setTheme = useCallback(
    (next: ThemeSetting) => {
      applyTheme(next);
      queryClient.setQueryData<AppSettings>(SETTINGS_KEY, (old) => (old ? { ...old, theme: next } : old));
      patch.mutate({ body: { theme: next } });
    },
    [patch, queryClient],
  );

  const resolved = resolveTheme(theme, prefersLight());
  const toggle = useCallback(() => setTheme(resolved === "dark" ? "light" : "dark"), [resolved, setTheme]);
  return { theme, resolved, setTheme, toggle };
}
