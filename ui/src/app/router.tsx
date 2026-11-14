import { createRootRoute, createRoute, createRouter, useNavigate } from "@tanstack/react-router";
import { ElementsPage, type ElementsSearch } from "@/features/elements/ElementsPage";
import { CreatePage } from "@/features/generate/CreatePage";
import { HomePage } from "@/pages/HomePage";
import { AssetsPage, type LibrarySearch, TrashPage } from "@/pages/LibraryPages";
import { PlaceholderPage } from "@/pages/PlaceholderPage";
import { StudioHome } from "@/features/studio/StudioHome";
import { type ProjectSearch, ProjectWorkspace, WORKSPACE_TABS, type WorkspaceTab } from "@/features/studio/Workspace";
import { SettingsPage } from "@/pages/SettingsPage";
import { AppShell } from "./AppShell";

interface ViewSearch {
  view?: string;
}

const text = (value: unknown): string | undefined => (typeof value === "string" && value ? value : undefined);
const truthy = (value: unknown): boolean | undefined => (value === true || value === "true" ? true : undefined);

/** Drop undefined keys so URLs stay clean. */
function clean<T extends object>(value: T): T {
  return Object.fromEntries(Object.entries(value).filter(([, v]) => v !== undefined)) as T;
}

const validateView = (search: Record<string, unknown>): ViewSearch => clean({ view: text(search.view) });
const validateLibrary = (search: Record<string, unknown>): LibrarySearch =>
  clean({ folder: text(search.folder), view: text(search.view), q: text(search.q), liked: truthy(search.liked) });
const TABS = new Set<string>(WORKSPACE_TABS);
const validateProject = (search: Record<string, unknown>): ProjectSearch =>
  clean({
    tab: TABS.has(search.tab as string) && search.tab !== "assets" ? (search.tab as WorkspaceTab) : undefined,
    folder: text(search.folder),
    view: text(search.view),
    q: text(search.q),
    director: truthy(search.director),
  });

const CATEGORY = new Set(["character", "location", "prop", "auto"]);
const validateElements = (search: Record<string, unknown>): ElementsSearch =>
  clean({
    scope: text(search.scope),
    category: CATEGORY.has(search.category as string) ? (search.category as ElementsSearch["category"]) : undefined,
    status: search.status === "draft" || search.status === "ready" ? search.status : undefined,
    folder: text(search.folder),
    q: text(search.q),
  });

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

const assetsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/assets",
  validateSearch: validateLibrary,
  component: function AssetsRoute() {
    const search = assetsRoute.useSearch();
    const navigate = useNavigate();
    return (
      <AssetsPage
        search={search}
        setSearch={(patch) => void navigate({ to: "/assets", search: (prev) => clean({ ...prev, ...patch }) })}
      />
    );
  },
});

const trashRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/trash",
  validateSearch: validateLibrary,
  component: function TrashRoute() {
    const search = trashRoute.useSearch();
    const navigate = useNavigate();
    return (
      <TrashPage
        search={search}
        setSearch={(patch) => void navigate({ to: "/trash", search: (prev) => clean({ ...prev, ...patch }) })}
      />
    );
  },
});

const studioRoute = createRoute({ getParentRoute: () => rootRoute, path: "/studio", component: StudioHome });

const projectRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/studio/$projectId",
  validateSearch: validateProject,
  component: function ProjectRoute() {
    const { projectId } = projectRoute.useParams();
    const search = projectRoute.useSearch();
    const navigate = useNavigate();
    return (
      <ProjectWorkspace
        key={projectId}
        projectId={projectId}
        search={search}
        setSearch={(patch) =>
          void navigate({
            to: "/studio/$projectId",
            params: { projectId },
            search: (prev) => clean({ ...prev, ...patch }),
          })
        }
      />
    );
  },
});

const elementsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/elements",
  validateSearch: validateElements,
  component: function ElementsRoute() {
    const search = elementsRoute.useSearch();
    const navigate = useNavigate();
    return (
      <ElementsPage
        search={search}
        setSearch={(patch) => void navigate({ to: "/elements", search: (prev) => clean({ ...prev, ...patch }) })}
      />
    );
  },
});

const canvasRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/canvas",
  component: () => <PlaceholderPage page="canvas" />,
});

const settingsRoute = createRoute({ getParentRoute: () => rootRoute, path: "/settings", component: SettingsPage });

export const routeTree = rootRoute.addChildren([
  homeRoute,
  imageRoute,
  videoRoute,
  studioRoute,
  projectRoute,
  assetsRoute,
  trashRoute,
  elementsRoute,
  canvasRoute,
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
