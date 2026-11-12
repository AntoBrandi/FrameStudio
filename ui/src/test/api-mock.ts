/** Tiny request router for component tests: ``"GET /api/settings": body``. */
type Handler = (request: Request) => unknown;

const SETTINGS = {
  theme: "dark",
  language: "en",
  remote_access: false,
  notify_on_job_finish: true,
  mock_latency_seconds: 2,
  mock_failure_rate: 0,
};

export const DEFAULT_ROUTES: Record<string, unknown> = {
  "GET /api/health": { status: "ok", app: "framestudio", version: "0.1.0", api_version: 1 },
  "GET /api/settings": SETTINGS,
  "GET /api/jobs": [],
  "GET /api/generations": [],
  "GET /api/system/info": {
    app: "framestudio",
    version: "0.1.0",
    api_version: 1,
    python: "3.13.0",
    os: "linux",
    platform: "Linux",
    data_dir: "/tmp/fs",
    database: "/tmp/fs/framestudio.db",
    schema_revision: "0003_generations_assets",
    schema_head: "0003_generations_assets",
    bind_host: "127.0.0.1",
    port: 8790,
    remote_access: false,
    restart_required: false,
    dev_mode: true,
    tools: { ffmpeg: true, ffprobe: true },
    started_at: "2026-11-12T10:00:00Z",
  },
};

let routes: Record<string, unknown> = { ...DEFAULT_ROUTES };
export const requests: { method: string; path: string; body: unknown }[] = [];

export function mockApi(overrides: Record<string, unknown>) {
  routes = { ...routes, ...overrides };
}

export function resetApi() {
  routes = { ...DEFAULT_ROUTES };
  requests.length = 0;
}

export async function routeRequest(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const key = `${request.method} ${url.pathname}`;
  const text = request.method === "GET" ? "" : await request.clone().text();
  requests.push({ method: request.method, path: url.pathname, body: text ? JSON.parse(text) : undefined });
  if (!(key in routes)) return new Response(JSON.stringify({ detail: `no mock for ${key}` }), { status: 404 });
  const entry = routes[key];
  const value = typeof entry === "function" ? await (entry as Handler)(request) : entry;
  if (value instanceof Response) return value;
  return new Response(JSON.stringify(value), {
    status: request.method === "POST" && key.endsWith("/api/generations") ? 201 : 200,
    headers: { "content-type": "application/json" },
  });
}
