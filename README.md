# FrameStudio

A local-first studio for AI images and video: projects, a media library, elements referenced by
`@handle`, cinema controls and an AI Director. Open models run on your machine through local
engines; hosted models (OpenAI, Claude, Gemini, Nano Banana, Seedance) run with your own keys.
Nothing leaves the machine unless you connect a provider or start a download.

**Status:** milestones M0 (skeleton) and M1 (library) of the
[build plan](https://claude.ai/artifact/1KprF7PcfuMVMvEaSicamN) are done. Generation runs on a mock
engine; real models arrive with M2 (hosted) and M3 (local). What works today:

- **Library:** import by button, drag and drop or paste; files are recognised by content (images,
  video, audio), deduplicated, and get WebP thumbnails, video posters, hover previews and audio
  waveforms. Damaged and unsupported files are refused with a reason.
- **Projects and folders:** projects with a cover, a brief and their own folders; coloured global
  folders; move assets in bulk or by dragging them onto a folder; trash with restore.
- **Studio** (M5.6): the Studio home has a large composer (Generate there starts a project named
  after the prompt, or adds to the one you pick) above your projects. A project opens as a
  workspace: its pages (assets, brief, settings, elements), tools (Storyboard, Canvas, Director),
  folders and trash on the left; the asset grid with takes in progress and a floating composer
  (Image and Video in one, with the cinema chips) in the middle; the Director's panel on the right.
  Export downloads the project as a zip: a folder named by its slug, a subfolder per folder, and
  the brief.
- **Gallery:** masonry or square grid, card size, split by date, filters (type, model, dates, liked,
  downloaded), range selection; virtualized, so 10,000 assets scroll smoothly.
- **Search:** full text over prompts, file names, captions and notes (accent-insensitive, works for
  Chinese, Japanese and Korean), with tokens such as `type:video`, `is:liked`, `after:2026-11-01`.
- **Viewer:** zoom and pan, frame stepping, filmstrip, notes, like, download, copy, move, reveal in
  folder (desktop), deep links.

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
- **Hosted providers** (OpenAI, Anthropic, Google, BytePlus) are connected in Settings ›
  Connections with your own keys. A key is checked with the provider before it is saved, then
  kept by the backend only: in the OS keyring, or in `secrets.enc` (encrypted, tied to this
  machine) where there is none. `FRAMESTUDIO_SECRETS=file` forces the file. Each provider can
  reach only its own hosts, only while its connection is on, and a monthly cap per provider stops
  hosted requests once the estimated spend reaches it.
- **Language models** (Claude, OpenAI, Gemini) help with prompts: Enhance rewrites a rough
  prompt and shows the diff, with one-click undo. Describe image adds captions and tags so the
  library can be searched by what pictures show; it sends a downscaled copy of each picture to the
  model, so it is off until you turn it on per project (or for the library in Settings).
- **Hosted image and video**: Nano Banana (Google) for stills and edits with reference images,
  Seedance (BytePlus) for clips with sound from text, a start frame, start and end frames, or
  reference images. Paid tasks show their estimated cost before Generate and count against the
  monthly cap. fal.ai adds a second route: search its catalog in Settings › Connections and add a
  model; its settings and price are read from fal. Auto picks the best available model for what
  you give it, and the viewer hands results on (Recreate, Use as reference, Turn to video).
- **Elements** (M4, under way): characters, locations and props with a handle (`@maya-ortiz`),
  a description, custom properties and pictures or clips from the library (the first is the
  primary), global or per project, in folders, with Draft and Ready status. Export them as CSV or
  as a zip with their media, and import that into another library or project. In the prompt box,
  typing `@` suggests elements with their pictures and puts them in as chips; pasted prompts get
  chips too, and handles that name no element are flagged. Each model gets elements in the form it
  can use: their pictures as reference images (Nano Banana, Seedance), one input picture
  (image-to-image models), or their names and descriptions in words; Preview in the composer shows
  exactly what will be sent. One picker adds references from recent work, uploads, generations,
  liked pictures or elements (or a new element made on the spot), counting against the model's
  real limit.
  From one photo, an element can generate its own character sheet, location set, prop turnaround or
  turntable clip; the results are added to it as they finish.
- **Cinema controls** (M5, under way): a shot description (film genre, era and pace; camera body,
  lens, focal length and aperture; ordered camera moves, also typed as `#dolly-in`; palette;
  lighting) is compiled into each model family's own prompt: plain sentences for Nano Banana,
  camera movement first for Seedance, tags and a negative prompt for diffusion models. Preview
  shows the result by section, and Settings shows which controls each model actually follows,
  measured with fixed-seed A/B pairs (hosted models are measured with your keys, within a budget).
  Film and Camera chips above the prompt open the pickers: carousels for genre, era and pace,
  dials for camera body, lens, focal length and aperture, all usable from the keyboard.
  Camera moves (about thirty, from dolly in to bullet time) go into the prompt as ordered chips,
  from the Moves picker or by typing `#`, and are worded per model (Seedance gets its own camera
  verbs).
  Palettes: presets, saved palettes, or a new one made from any picture (its main colours, worded
  for the prompt); a palette can also grade a picture or clip, as a copy, at a chosen strength.
  Lighting: presets (silhouette, practicals, window, …) or your own rig of up to four lights placed
  on a 3D sphere around a lit head (direction, height, colour, brightness, softness, plus ambient
  fill). The rig is turned into lighting words for the prompt and can be saved as a preset.
- **Test doubles ship with the product** (D8): the mock engine, a hosted "Mock cloud" model,
  fake local and remote jobs, a scripted fake LLM, and offline key checks
  (`FRAMESTUDIO_FAKE_PROVIDERS=1`, where keys starting `fs-test-ok` pass).

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
