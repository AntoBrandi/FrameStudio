import { createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { CreatePage } from "@/features/generate/CreatePage";
import { HomePage } from "@/pages/HomePage";
import { PlaceholderPage } from "@/pages/PlaceholderPage";
import { SettingsPage } from "@/pages/SettingsPage";
import { AppShell } from "./AppShell";

interface ViewSearch {
  view?: string;
}

const validateView = (search: Record<string, unknown>): ViewSearch =>
  typeof search.view === "string" && search.view ? { view: search.view } : {};

const rootRoute = createRootRoute({ component: AppShell });

const homeRoute = createRoute({ getParentRoute: () => rootRoute, path: "/", component: HomePage });

const imageRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/image",
  validateSearch: validateView,
  component: function ImageRoute() {
    const { view } = imageRoute.useSearch();
    return <CreatePage mode="image" view={view} />;
  },
});

const videoRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/video",
  validateSearch: validateView,
  component: function VideoRoute() {
    const { view } = videoRoute.useSearch();
    return <CreatePage mode="video" view={view} />;
  },
});

const placeholder = (path: "/studio" | "/assets" | "/elements" | "/canvas") =>
  createRoute({
    getParentRoute: () => rootRoute,
    path,
    component: () => <PlaceholderPage page={path.slice(1) as "studio" | "assets" | "elements" | "canvas"} />,
  });

const settingsRoute = createRoute({ getParentRoute: () => rootRoute, path: "/settings", component: SettingsPage });

export const routeTree = rootRoute.addChildren([
  homeRoute,
  imageRoute,
  videoRoute,
  placeholder("/studio"),
  placeholder("/assets"),
  placeholder("/elements"),
  placeholder("/canvas"),
  settingsRoute,
]);

export function makeRouter(history?: Parameters<typeof createRouter>[0]["history"]) {
  return createRouter({ routeTree, history, defaultPreload: "intent", scrollRestoration: true });
}

export const router = makeRouter();

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
