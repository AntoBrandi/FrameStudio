# FrameStudio: notes for coding agents

Work follows the build plan: https://claude.ai/artifact/1KprF7PcfuMVMvEaSicamN. Steps have IDs
(M0.1 … M10.x), each with Build / Done when / Tests / Depends on. A step is finished when its
"Done when" line can be shown live and its listed tests exist and pass. v1 path:
M0 → M1 → M2 → M4 → M5 → M6; M3 (setup and local models) runs in parallel and never blocks it.

## Hard rules

- **No app logic in Electron** (`desktop/`). The backend serves the UI; the shell only opens a
  window, signs it in and adds OS extras. Anything a browser user needs belongs in `ui/` + `backend/`.
- **Local-first network policy.** No outbound call unless the user enabled that provider or started
  that download. No CDN assets (fonts are bundled; Swagger UI is disabled for this reason).
- **Every `/api` route is behind the access guard** (`backend/framestudio/security.py`). Only
  `GET /api/health` and `POST /api/auth/session` are public. Do not add exceptions casually.
- **Hardware support is data, not code paths** (plan D2). Never size defaults to one machine.
- **Schema change = Alembic migration + test.** `tests/test_migrations.py` fails on model drift.
- **API change = `bun run api:gen`** and commit `ui/src/api/openapi.json` + `schema.d.ts`.
- **Paid remote work is never submitted twice**: store the provider task ID before polling; see
  `backend/framestudio/jobs/queue.py`.
- UI strings go through i18n (`ui/src/i18n/en.json`).

## Commands

`bun run dev` · `bun run dev:web` · `bun run start` · `bun run lint` · `bun run typecheck` ·
`bun run test` · `bun run test:e2e` · `bun run api:gen`. Backend only: `cd backend && uv run pytest`.
Playwright's `--project` flag is variadic: put file arguments before it.

## Layout

- `backend/framestudio/`: `app.py` (factory), `security.py`, `settings.py`, `events.py`,
  `jobs/` (queue, fake jobs), `generation.py` (generations → jobs → assets), `providers/`
  (mock engine), `media/` (content-addressed store, ffmpeg), `db/` (models, migrations), `api/`.
- `ui/src/`: `app/` (shell, router, palette, theme), `features/` (auth, jobs, generate, status),
  `pages/`, `api/` (generated types, client, SSE bridge), `components/ui/` (shadcn-style).
- `desktop/src/`: `config.ts` (pure, tested), `backend.ts` (spawn/attach), `main.ts`, `preload.ts`.
