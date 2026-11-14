import { useQueryClient } from "@tanstack/react-query";
import { Link, Outlet } from "@tanstack/react-router";
import { Command as CommandIcon, Moon, PanelLeft, PanelRight, Settings, Sun } from "lucide-react";
import { type ReactNode, useEffect } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { $api, type JobOut } from "@/api/client";
import { connectEvents } from "@/api/events";
import { Button } from "@/components/ui/button";
import { Kbd, Tooltip } from "@/components/ui/misc";
import { useAuthStore } from "@/features/auth/store";
import { JobTray } from "@/features/jobs/JobTray";
import { LibrarySidebar } from "@/features/library/Sidebar";
import { BackendStatus } from "@/features/status/BackendStatus";
import { isMac } from "@/lib/utils";
import { CommandPalette } from "./CommandPalette";
import { NAV } from "./nav";
import { useShellStore } from "./shell-store";
import { useTheme } from "./theme";

const INSPECTOR_SLOT = "inspector-slot";

/** Render children into the right inspector (when it is open). */
export function InspectorPortal({ children }: { children: ReactNode }) {
  const open = useShellStore((s) => s.inspectorOpen);
  const target = typeof document !== "undefined" ? document.getElementById(INSPECTOR_SLOT) : null;
  if (!open || !target) return null;
  return createPortal(children, target);
}

function useEventsBridge() {
  const queryClient = useQueryClient();
  const needsToken = useAuthStore((s) => s.needsToken);
  const setNeedsToken = useAuthStore((s) => s.setNeedsToken);
  const { t } = useTranslation();
  const { data: settings } = $api.useQuery("get", "/api/settings");
  const notify = settings?.notify_on_job_finish ?? true;

  useEffect(() => {
    if (needsToken) return;
    return connectEvents(queryClient, {
      onAuthLost: () => setNeedsToken(true),
      onJobFinished: (job: JobOut) => {
        const ok = job.state === "succeeded";
        const message = t(ok ? "jobs.finished" : "jobs.failed", { title: job.title });
        // On the Image and Video pages, and in a project's workspace, the feed
        // or the in-progress strip already shows how a generation ended.
        const feedVisible =
          job.kind.startsWith("generate") && /^\/(image|video|studio\/[^/]+)(\/|$)/.test(window.location.pathname);
        if (!feedVisible && job.state !== "cancelled") {
          if (ok) toast.success(message);
          else toast.error(message, { description: job.error_message ?? undefined });
        }
        if (notify && document.hidden && typeof Notification !== "undefined" && Notification.permission === "granted") {
          new Notification("FrameStudio", { body: message, tag: job.id });
        }
      },
    });
  }, [needsToken, notify, queryClient, setNeedsToken, t]);
}

function useShortcuts() {
  const setPaletteOpen = useShellStore((s) => s.setPaletteOpen);
  const toggleSidebar = useShellStore((s) => s.toggleSidebar);
  const toggleInspector = useShellStore((s) => s.toggleInspector);
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const mod = isMac() ? event.metaKey : event.ctrlKey;
      if (!mod || event.altKey) return;
      const key = event.key.toLowerCase();
      if (key === "k") {
        event.preventDefault();
        setPaletteOpen(!useShellStore.getState().paletteOpen);
      } else if (key === "b") {
        event.preventDefault();
        toggleSidebar();
      } else if (key === ".") {
        event.preventDefault();
        toggleInspector();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setPaletteOpen, toggleInspector, toggleSidebar]);
}

function TopNav() {
  const { t } = useTranslation();
  const { resolved, toggle } = useTheme();
  const toggleSidebar = useShellStore((s) => s.toggleSidebar);
  const toggleInspector = useShellStore((s) => s.toggleInspector);
  const setPaletteOpen = useShellStore((s) => s.setPaletteOpen);
  const mod = isMac() ? "⌘" : "Ctrl";
  return (
    <header className="flex h-12 items-center gap-2 border-b border-line bg-flat px-2">
      <Tooltip label={`${t("shell.toggleSidebar")} (${mod}+B)`}>
        <Button variant="ghost" size="icon" aria-label={t("shell.toggleSidebar")} onClick={toggleSidebar}>
          <PanelLeft />
        </Button>
      </Tooltip>
      <Link
        to="/"
        className="mr-2 flex items-center gap-2 rounded-md px-1 font-display text-xl font-bold uppercase tracking-wide"
      >
        <img src="/favicon.svg" alt="" className="size-6" />
        <span>
          Frame<span className="text-tungsten">Studio</span>
        </span>
      </Link>
      <nav aria-label={t("nav.primary")} className="flex min-w-0 flex-1 items-center gap-0.5 overflow-x-auto">
        {NAV.filter((item) => item.key !== "settings").map((item) => (
          <Link
            key={item.to}
            to={item.to}
            activeOptions={{ exact: item.to === "/" }}
            className="flex items-center gap-1.5 whitespace-nowrap rounded-md px-2.5 py-1.5 text-sm text-muted hover:bg-raised hover:text-ink data-[status=active]:bg-raised data-[status=active]:text-ink [&_svg]:size-4"
          >
            <item.icon aria-hidden />
            {t(`nav.${item.key}`)}
          </Link>
        ))}
      </nav>
      <Button
        variant="outline"
        size="sm"
        onClick={() => setPaletteOpen(true)}
        className="hidden text-muted sm:inline-flex"
      >
        <CommandIcon aria-hidden />
        <span className="sr-only">{t("shell.openPalette")}</span>
        <Kbd>{mod}+K</Kbd>
      </Button>
      <JobTray />
      <BackendStatus compact />
      <Tooltip label={resolved === "dark" ? t("shell.themeLight") : t("shell.themeDark")}>
        <Button
          variant="ghost"
          size="icon"
          onClick={toggle}
          aria-label={resolved === "dark" ? t("shell.themeLight") : t("shell.themeDark")}
          data-testid="theme-toggle"
        >
          {resolved === "dark" ? <Sun /> : <Moon />}
        </Button>
      </Tooltip>
      <Tooltip label={t("nav.settings")}>
        <Link
          to="/settings"
          aria-label={t("nav.settings")}
          className="flex size-8 items-center justify-center rounded-lg text-muted hover:bg-raised hover:text-ink data-[status=active]:text-tungsten"
        >
          <Settings className="size-4" />
        </Link>
      </Tooltip>
      <Tooltip label={`${t("shell.toggleInspector")} (${mod}+.)`}>
        <Button variant="ghost" size="icon" aria-label={t("shell.toggleInspector")} onClick={toggleInspector}>
          <PanelRight />
        </Button>
      </Tooltip>
    </header>
  );
}

function Inspector() {
  const { t } = useTranslation();
  return (
    <aside
      aria-label={t("shell.inspector")}
      className="flex w-80 flex-col border-l border-line bg-flat"
      data-testid="inspector"
    >
      <h2 className="border-b border-line px-4 py-3 font-display text-lg font-semibold uppercase tracking-wide">
        {t("shell.inspector")}
      </h2>
      <div id={INSPECTOR_SLOT} className="peer flex flex-col gap-3 overflow-auto p-4 empty:hidden" />
      <div className="hidden p-4 text-sm text-muted peer-empty:block">
        <p className="font-medium text-ink">{t("shell.nothingSelected")}</p>
        <p>{t("shell.nothingSelectedHint")}</p>
      </div>
    </aside>
  );
}

export function AppShell() {
  const { t } = useTranslation();
  const sidebarOpen = useShellStore((s) => s.sidebarOpen);
  const inspectorOpen = useShellStore((s) => s.inspectorOpen);
  useEventsBridge();
  useShortcuts();

  return (
    <div className="grid h-full grid-rows-[auto_minmax(0,1fr)]">
      <a
        href="#main"
        className="sr-only z-50 rounded-md bg-tungsten px-3 py-2 text-tungsten-ink focus:not-sr-only focus:fixed focus:left-2 focus:top-2"
      >
        {t("app.skipToContent")}
      </a>
      <TopNav />
      <div className="flex min-h-0">
        {sidebarOpen ? <LibrarySidebar /> : null}
        <main id="main" tabIndex={-1} className="relative min-w-0 flex-1 overflow-auto focus:outline-none">
          <Outlet />
        </main>
        {inspectorOpen ? <Inspector /> : null}
      </div>
      <CommandPalette />
    </div>
  );
}
