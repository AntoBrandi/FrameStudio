# FrameStudio

A local-first studio for AI images and video: projects, a media library, elements referenced by
`@handle`, cinema controls and an AI Director. Open models run on your machine through local
engines; hosted models (OpenAI, Claude, Gemini, Nano Banana, Seedance) run with your own keys.
Nothing leaves the machine unless you connect a provider or start a download.

**Status:** milestone M0 (skeleton) of the [build plan](https://claude.ai/artifact/1KprF7PcfuMVMvEaSicamN).
The app runs end to end with a mock engine: type a prompt and a placeholder image or clip lands in
the feed. Real models arrive with M2 (hosted) and M3 (local).

## Quick start

You need [Bun](https://bun.sh), [uv](https://docs.astral.sh/uv/) and, for video, `ffmpeg` on `PATH`.
No GPU and no API keys are needed for anything in M0.

```sh
bun run setup      # JS + Python dependencies, the Electron binary, a first build
bun run dev        # backend + UI with hot reload + the desktop window
bun run dev:web    # the same without the window; open the printed link in a browser
bun run start      # the built app, as an installed copy runs (per-user data directory)
```

`bun run dev` keeps its data in `.data/`, apart from the per-user data directory
(`~/.local/share/FrameStudio`, `~/Library/Application Support/FrameStudio`,
`%LOCALAPPDATA%\FrameStudio`). Set `FRAMESTUDIO_DATA_DIR` to use another folder.

The backend also runs on its own, for example on a machine with a bigger GPU:

```sh
cd backend
uv run framestudio serve --host 0.0.0.0   # binding beyond loopback turns remote access on
uv run framestudio token --url            # sign-in link for a browser
```

## How it fits together

```
ui/        React 19 + Vite + Tailwind 4, TanStack Router and Query, typed API client
desktop/   Electron shell: a window on the UI the backend serves, nothing more
backend/   FastAPI on uv: SQLite + Alembic, job queue, events, media store, engines
e2e/       Playwright: the Electron app (spawn and attach modes) and a Chrome smoke run
scripts/   dev, start and setup commands (plain Node, any OS)
```

- **The backend serves the UI** over HTTP (plan decision D9). Electron only opens a window on it,
  so the same UI works in a browser and the backend can run on another machine.
- **Every API request is authenticated** (D10): a per-install token, sent as
  `Authorization: Bearer …` by scripts or as an HttpOnly `SameSite=Strict` cookie by the UI.
  `Host` and `Origin` are checked on every request, so other websites and DNS rebinding cannot
  reach the API. The server binds to 127.0.0.1 unless remote access is turned on.
- **Jobs** run in a persistent queue with a serial GPU lane and a concurrent network lane. Remote
  (paid) tasks store the provider's task ID before the first poll and resume after a restart
  instead of being submitted twice; local jobs that were cut off become "interrupted".
- **Events** stream to the UI over Server-Sent Events, with replay after reconnects.
- **Media** is content-addressed (`media/ab/cd/<sha256>.<ext>`) and served with range requests.
- **Test doubles ship with the product** (D8): the mock engine, fake local and remote jobs.

## Development

```sh
bun run lint        # ruff + eslint + prettier
bun run typecheck   # tsc for ui/ and desktop/
bun run test        # pytest (backend) + vitest (ui, desktop)
bun run test:e2e    # Playwright; builds ui/ and desktop/ when missing
bun run api:gen     # after changing the API: regenerate ui/src/api/{openapi.json,schema.d.ts}
```

Schema changes ship with an Alembic migration in `backend/framestudio/db/migrations/versions/`;
a test fails when the models and migrations disagree, and another when the committed OpenAPI
snapshot is stale.

**Linux and Chromium's sandbox.** Ubuntu 24.04 and later restrict unprivileged user namespaces, and
an Electron installed by a package manager has no root-owned `chrome-sandbox` helper, so Electron
refuses to start. The dev, start and test commands detect this and run Electron with
`--no-sandbox`, printing the command that enables the sandbox instead
(`sudo chown root:root …/chrome-sandbox && sudo chmod 4755 …/chrome-sandbox`). Packaged builds
(milestone M10) install the helper or an AppArmor profile.

## License

AGPL-3.0-only. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
