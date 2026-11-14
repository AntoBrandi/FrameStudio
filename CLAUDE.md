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
- **Keys stay in the backend.** API keys live in `framestudio.vault` (OS keyring, or the encrypted
  `secrets.enc`); never in the database, a log line, an API response or a URL. Outbound HTTP only
  through `services.outbound.client(provider)` (`framestudio/net.py`), which enforces each
  provider's host allowlist and on/off switch; `tests/test_net.py` fails if anything else dials out.
  Hosted spending goes through `services.usage` (check or reserve against the monthly cap first).
- Tests and E2E backends run with `FRAMESTUDIO_SECRETS=file` (never the real keyring);
  E2E also sets `FRAMESTUDIO_FAKE_PROVIDERS=1` (key checks answered locally; `fs-test-ok…` passes).
- UI strings go through i18n (`ui/src/i18n/en.json`).

## Commands

`bun run dev` · `bun run dev:web` · `bun run start` · `bun run lint` · `bun run typecheck` ·
`bun run test` · `bun run test:e2e` · `bun run api:gen`. Backend only: `cd backend && uv run pytest`.
Playwright's `--project` flag is variadic: put file arguments before it. `e2e/electron-library.spec.ts`
is serial (tests build on each other): run the whole file, not one test with `-g`.
Test media: `backend/tests/fixtures/media` (regenerate with `make_corpus.py`).

## Layout

- `backend/framestudio/`: `app.py` (factory), `security.py`, `settings.py`, `events.py`,
  `jobs/` (queue, fake jobs), `catalog/` (ModelSpec, one YAML per model in `catalog/models/`,
  param validation, JSON Schema, cost estimates), `generation.py` (generations → jobs → assets;
  `GenerateJob` on the GPU lane for local providers, `GenerateRemoteJob` on the network lane for
  hosted ones), `providers/` (protocols, error taxonomy, retries; mock and mock-cloud), `llm/`
  (vendor-neutral LLM interface, scripted `FakeLLM`, `MeteredLLM` for caps and usage; adapters
  `anthropic_api.py`, `openai_api.py`, `gemini_api.py`; `registry.py` builds them from the
  connections that are on and resolves the model per task; `pricing.py`),
  `connections/` (provider registry with hosts and key checks, connection service), `vault.py`
  (keys), `net.py` (outbound policy), `usage.py` (ledger, monthly caps), `media/` (content-addressed store, ffmpeg),
  `library/` (ingest and media detection, asset queries, projects and folders, FTS5 search),
  `db/` (models, migrations), `api/`.
- `backend/tests/contracts/`: suites every generation and LLM provider must pass. A new catalog
  model needs a provider harness there; its catalog fixtures are generated and checked.
- Provider failures are `ProviderError` (kind + code). Never retry a hosted `submit` (it may be
  paid); status and download go through `with_retries`.
- LLM adapters keep each provider's raw reply in `Message.native` and replay it unchanged
  (Claude thinking blocks, OpenAI encrypted reasoning, Gemini thought signatures): never rebuild
  an assistant turn from `parts` when `native` holds that provider's copy. Adapters must be given
  a transport (`services.outbound.transport(provider)`).
- LLM features: `llm/prompt_help.py` (Enhance), `library/describe.py` (Describe job, name
  suggestions). They get their model from `services.llm_registry.for_task(...)`; the offline demo
  answers them through `llm/fake.py: demo`. Describing sends pictures to a provider: keep it opt-in.
- Hosted generation (M2.7, M2.8): connections also bring generation providers
  (`llm/registry.py` `GENERATORS`): Google → `providers/gemini_image.py` (one call, job kind
  `generate.hosted`), BytePlus → `providers/seedance.py` (polled task, `generate.remote`). Their
  test stand-ins are `tests/contracts/gemini_image_wire.py` and `byteplus_wire.py` (prompts with
  "blocked"/"slow"/"inactive" steer them). Host allowlists accept `*.domain` wildcards.
- fal.ai (M2.10): `providers/fal.py` (queue), `catalog/fal_import.py` (entries built from fal's
  OpenAPI schemas and prices, written to `<data>/catalog/`, loaded with the built-in catalog;
  `provider_inputs` maps FrameStudio inputs to the endpoint's fields). Offline demo models live in
  `connections/fake.py` (`FAL_DEMO`); the test queue is `tests/contracts/fal_wire.py`.
- Auto (M2.9): `router.py` picks by each catalog entry's `auto_rank` among models that fit and can
  run; viewer hand-offs fill the composer through `features/generate/handoffs.ts` and its store.
- Elements (M4.1): `library/elements.py` (handles unique per scope: `scope_key` is the project id
  or "global", so a project may reuse a global handle; media are library assets in order, the first
  is primary, trashed ones drop out; editing the description or media bumps `version`; export as
  CSV, media named by sha256, or a zip with `elements.json` and the files; CSV import finds media by
  content, zip import ingests them), `api/elements_api.py`. UI: `features/elements/` (`ElementsPage`,
  `ElementEditor`; its `slugify` mirrors the backend's).
- Prompt tokens (M4.3): a prompt is a token list (`text`, `element` @handle, `move` #name,
  `skill` /name); `prompt` text is its rendering. `framestudio/prompts.py` is the only parser
  (`POST /api/prompts/parse`, also used when a generation arrives as text); `@` is always a token
  (unknown handles warn), `#`/`/` only for known moves and skills. Generations store
  `prompt_tokens` and record `element_uses` ("Used in"). UI: `features/generate/tokens.ts`, the
  `TokenNode` chips and `@` menu in `PromptEditor.tsx`; the composer store keeps drafts as tokens.
  Enter priorities there: @ menu (CRITICAL) > pick guard (HIGH) > send (NORMAL).
- Reference resolver (M4.4): `framestudio/references.py` turns tokens + attached pictures into
  what a model gets, by strategy from its catalog limits: `multi_reference` (attached first, then
  each element's primary, then more angles, ≤3 each, numbered "image N" in the prompt),
  `single_image` (`max_references: 1`; fal image-to-image endpoints map one `image_url` to the
  `reference` input), `description` (no pictures, or start/end frames). Element descriptions follow
  the prompt; over-limit pictures and descriptions are dropped with warnings. Stored per generation
  in `generations.resolved` (the job sends that prompt and those pictures; `reference_ids` stays the
  hand-attached ones). `POST /api/generations/preview` returns it; the composer's Preview shows it.
  Golden requests: `tests/golden/references/` (rewrite with `FRAMESTUDIO_UPDATE_GOLDEN=1`).
- Reference picker (M4.5): `features/generate/ReferencePicker.tsx` is the one picture picker
  (composer references, start/end frames, an element's pictures): tabs Recent, Uploads,
  Generations, Liked (asset `provenance`/`liked` filters), plus Elements and Presets when given
  `elements`. The composer hosts it; picked elements become chips in the prompt. Its counter is
  attached + one per element with pictures against the model's limit (elements are unlimited when
  the model takes no pictures). E2E tests sharing a backend use unique names per run.
- Element generators (M4.6): `library/element_generators.py` (recipes character_sheet,
  location_set, prop_turnaround, turntable; one generation per view, prompt = text + element token +
  text; Nano Banana / Seedance first, else the router). Views are pinned to the element's pictures
  at start (`references` + `element_pictures=False`), so early results do not feed later views.
  Generations with `attach_element_id` add their result to the element in `_store_output`
  (atomic version bump first). `/api/elements/recipes`, `/api/elements/{id}/generate[/estimate]`;
  UI `features/elements/GenerateViews.tsx`. Stand-in gotcha: a prompt containing "slow" stays
  queued forever in `FakeBytePlus` (likewise "blocked"/"inactive" fail).
- Cinema controls (M5): `framestudio/cinema/` — `vocab.yaml` (every picker choice with its
  sentence `phrase` and diffusion `tags`; move ids are `#move` prompt tokens), `ShotSpec` in
  `__init__.py` (film, camera, ordered moves, palette, lighting; stored as `generations.shot`),
  `compiler.py` (profiles per model family: gemini, seedance, diffusion, generic, chosen by the
  catalog's `prompt_profile` or provider; section order, drop order at the length limit, negatives
  inline/param/drop, native params). The resolver (M4.4) calls the compiler, so Preview shows the
  prompt by section. `adherence.py` measures which controls each model follows (fixed-seed A/B,
  picture metrics or a vision-model judge asked twice with the pictures swapped); built-in results
  in `adherence.yaml`, runs saved to `<data>/adherence.json`; ignored controls are flagged.
  Compiler goldens: `tests/golden/compiler/` (`FRAMESTUDIO_UPDATE_GOLDEN=1`).
- Shot pickers (M5.2): UI `features/cinema/` — `ShotChips` (chips above the prompt) opens
  `FilmPicker` (genre, era, pace carousels: radio groups) and `CameraPicker` (body, lens, focal,
  aperture drums: listboxes); they write the composer store's `shots[mode]` (persisted).
  Preview artwork: `cinema/previews.yaml` + `framestudio previews` (talks to a running backend
  through `net.backend_client`; `--dry-run` prices it, a run needs `--budget`); installed files in
  `framestudio/cinema/previews/`, listed in `/api/cinema/vocab` and served from
  `/api/cinema/previews/...`; until then `PreviewArt` draws placeholders.
- Camera moves (M5.3): #move tokens in the prompt are the shot's moves, in prompt order (`shot.moves`
  is for the API and the Director). Vocab entries can carry per-profile wording (`profiles:
{seedance: …}`: BytePlus's camera verbs); the seedance profile notes more than two moves.
  UI: `#` typeahead (`MovesPlugin` in `PromptEditor.tsx`, video only), `MovesPicker` and the Moves
  chip; token edits in `features/cinema/moves.ts`. `cinema/motion.py` measures a clip's zoom
  divergence (dolly in > 0, static ≈ 0) for smoke checks. Do not run `npx prettier --write src`
  from `ui/`: it reformats the generated `openapi.json` (the ignore file lives at the root).
- Palettes (M5.4): `cinema/colour.py` (sRGB↔CIELAB), `cinema/palette.py` (seeded k-means extraction
  dark to light with picture shares, words from named colours, `.cube` LUT pulling colours toward
  the palette, `grade()` via ffmpeg `lut3d`), `cinema/presets.py` (saved presets in the `presets`
  table, kinds palette and lighting; `GradeJob` on the new `cpu` lane writes a graded copy).
  Routes under `/api/cinema/` (`palettes/extract`, `presets`, `grade/{asset_id}`). UI:
  `PalettePicker`, `GradeDialog` (viewer's Colour grade). i18n: `palette.*` is the command
  palette; colour palettes use `colourPalette.*` (never overwrite an existing i18n section).
- Lighting (M5.5): `Rig` / `Light` in `cinema/__init__.py` (1–4 lights; azimuth 0 = from the camera
  side, 90 = screen right, 180 = behind; elevation −90…90; colour, intensity, softness; ambient).
  Every lighting preset in `vocab.yaml` has a `rig`; `cinema/lighting.py` `describe()` turns a rig
  into words (key/fill/rim roles, direction, contrast), and the compiler uses them when
  `shot.lighting.rig` is set (the preset stays as where the edit began; `label` names a saved rig).
  Saved rigs are `presets` of kind lighting; `POST /api/cinema/lighting/describe`. UI:
  `LightingPicker` (presets, saved rigs, sliders, quick directions, live words) with
  `LightSphere.tsx` (three.js, lazy-loaded; drag markers on the guide sphere), falling back to an
  SVG diagram without WebGL (jsdom). The picker is a modal: the chips behind it are hidden from the
  accessibility tree while it is open.
- Studio (M5.6): `features/studio/` — `StudioHome` (`/studio`: hero `Composer` with a "Save to"
  target; `prepare` creates a project named by `projectNameFrom` before the generation is sent,
  then the page opens it; `MyProjects` grid), `Workspace.tsx` (`/studio/$projectId`, search
  `tab` = assets | brief | settings | elements | trash | storyboard | canvas, `director` for the
  right panel; assets get `InProgress` (the project's running or recently failed takes) and the
  floating composer, hidden while a selection shows the bulk bar), `Brief.tsx`. The project's
  sidebar is `ProjectNav` in `features/library/Sidebar.tsx`. The composer's `onMode` switches
  Image/Video in place (store `studioMode`); in a workspace the viewer's hand-offs stay there.
  Cached generation lists are matched by mode and project (`listsGeneration`/`insertGenerations`
  in `api/events.ts`). Export: `GET /api/projects/{id}/export[?folder_id=]` (zip written to the
  media tmp dir, stored uncompressed, deleted after sending; `<slug>/<folder>/<name>`).
- Keyframe then shot (M5.7): `cinema/keyframes.py` — a planned `Shot` (table `shots`; not
  `generations.shot`, which is a ShotSpec) keeps the prompt, spec, video and keyframe models.
  Keyframes are image generations with `shot_id` and `shot_role="keyframe"` at the clip's aspect
  (`keyframe_params_for`); `animate` makes the clip (`shot_role="clip"`) from the approved keyframe
  (and end frame) with the same tokens and spec; `extend` starts a new shot from a clip's last frame
  (`frame_asset`; frame pictures never set `source_generation_id`, since a generation's result is its
  newest asset). `cinema/continuity.py` scores a clip's first and last frames against its pictures
  (block SSIM, `SAME` = 0.75), cached in `shots.checks` when the shot is read. Routes under
  `/api/shots`, plus `POST /api/assets/{id}/frame`. The mock engine animates start and end frames
  (push-in, fade), so offline Auto routes start frames to it. UI: `features/shots/` (`KeyframeFirst`
  in the video composer, `ShotsTray`/`ShotCard` on the Video page and in a workspace); the viewer's
  "Extend from last frame".
- LLM contract fixtures: `backend/tests/contracts/fixtures/llm/<provider>/<case>.json`, synthetic
  until recorded (`uv run python -m tests.contracts.llm_wire` rewrites synthetic ones). Live runs:
  `LIVE_API=1 FRAMESTUDIO_LIVE_<PROVIDER>_KEY=… uv run pytest tests/contracts` (paid);
  add `FRAMESTUDIO_RECORD=1` to save the answers as fixtures.
- Migrations run with SQLite foreign keys off (batch rebuilds would otherwise cascade); FTS tables
  are outside the ORM (`migrate.UNMANAGED_PREFIXES`).
- Events: the SSE endpoint subscribes before streaming; the UI refetches on (re)connect with
  `refresh()` (cancel in-flight, then invalidate). Use `refresh`/`invalidateLibrary`, not a bare
  `invalidateQueries`, after changes: an invalidation joins a first fetch already in flight.
  Likewise, an event applied with `setQueryData` while a fetch is in flight must restart that fetch
  (see the `generation.updated` handler), or the older read overwrites it.
- E2E runs against built artifacts; `e2e/global-setup.ts` rebuilds `ui`/`desktop` when sources
  are newer than the build (`FRAMESTUDIO_E2E_REBUILD=1` forces it).
- Composer (`ui/src/features/generate/`): controls come from `/api/models` parameter specs;
  `params.ts` mirrors `validate_params` in `backend/framestudio/catalog` (rules, defaults, value
  checks): change both together. Estimates and problems come from `POST /api/generations/estimate`.
  The prompt box is Lexical (`PromptEditor.tsx`); jsdom cannot type into it, so unit tests set the
  draft through the store (or drive the editor through `__lexicalEditor`) and E2E types for real.
- `ui/src/`: `app/` (shell, router, palette, theme), `features/` (auth, jobs, generate, status,
  `library/`: pure layout and selection, Gallery, AssetCard, AssetViewer, LibraryView, Sidebar),
  `pages/`, `api/` (generated types, client, SSE bridge), `components/ui/` (shadcn-style).
- `desktop/src/`: `config.ts` (pure, tested), `backend.ts` (spawn/attach), `main.ts`, `preload.ts`.
