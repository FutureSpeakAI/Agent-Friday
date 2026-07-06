# Changelog — Agent Friday / Asimov's Mind

All notable changes to this project are documented here.  
Format: [Semantic Versioning](https://semver.org) · Date: YYYY-MM-DD

> **Note:** Pre-1.0 releases have been archived. Current version: **5.3.0**

---

## [5.3.0] - 2026-07-06

### Content Pipeline — Friday becomes a publishing platform

The full social-media management system specified in
`docs/CONTENT_PIPELINE_SPEC.md`: create → compose → schedule → publish →
monitor → learn, across eleven platforms plus the Friday Federation.

- **Data model & store** (`services/content_pipeline.py`): `ContentPost` /
  `PlatformTarget` entities, SQLite store (WAL), the full §3.2 status machine
  (DRAFT→SCHEDULED→PUBLISHING→PUBLISHED/PARTIAL/HELD/FAILED, sticky-safe
  holds, re-arm that never double-posts), append-only publish log.
- **Composition engine** (`services/content_composer.py`): one canonical body
  → platform-native versions (thread splitting, grapheme-aware limits,
  platform hashtag norms, format conversion matrix), voice injection via
  SOUL.md + user-editable per-platform voice cards, all LLM calls
  engine-direct and egress-gated.
- **Publication engine** (`services/publisher.py`): a 1-minute scheduler
  builtin claims due targets and runs the gate chain — H1–H4 moderation scan,
  sensitivity classification with **hold-for-review** semantics (never silent
  redaction; explicit human release honored, hard harm floor never
  releasable), adapter prepare/publish, retry ladders with Retry-After,
  verify-before-retry double-post protection, per-platform rate budgets,
  native-schedule delegation (YouTube `publishAt`, Mastodon `scheduled_at`),
  recurrence templates, signed provenance publication entries, ψ earn.
- **Eleven platform adapters** (`services/platforms/`): LinkedIn, X/Twitter,
  Instagram, YouTube, TikTok, Bluesky (byte-offset facets), Mastodon
  (instance-aware), Reddit (per-subreddit rules surfaced at compose),
  Substack + Medium (honest assisted handoff — no fake automation), and the
  Friday Federation (marketplace listing + encrypted CONTENT_OFFER + ψ) —
  each behind one contract with capabilities declaration, encrypted
  credential storage, local rate budgeting, and a shared contract test
  battery.
- **Analytics collector** (`services/analytics_collector.py`): decaying-poll
  engagement collection normalized into one metrics shape (absent ≠ zero),
  local-only storage, weekly insights (attribute lift with Wilson bounds,
  learned best-times that outrank seed tables at n≥5), engagement→ψ minting
  (idempotent, daily-capped), strict untrusted-input discipline — platform
  text never reaches an LLM prompt.
- **ContentWS v2** (Content workspace): Compose (platform chips, live
  per-platform previews, hashtag rows, variants, alt-text nags) · Calendar
  (drag-reschedule, optimal-slot suggestions) · Queue (held-with-evidence,
  release/edit, history, pause-all) · Analytics (unified dashboard, insight
  cards) · Accounts (one-click OAuth or token paste — verified live at
  connect, plain-language scopes with the "cannot" list, rate budgets,
  disconnect = revoke + purge) · Ideas (the v1 kanban, graduated).
- **API**: 23 new `/api/content/*` routes (documented in `docs/API.md`);
  OAuth loopback callbacks on localhost only; full local data export.
- **Chat/voice tools**: `content_create_post`, `content_schedule_post`,
  `content_post_status`, `content_repurpose` — "Friday, post this to
  LinkedIn and Bluesky tomorrow morning" works end to end.
- **Scheduler**: new `once` trigger (one-shot schedules, auto-disable).
- **Provenance**: `add_publication()` — publication events signed into each
  asset's manifest history; the local ledger remains the source of truth.
- **Review hardening**: a 10-finding adversarial review pass (all confirmed
  real, all fixed with regression tests) — egress gates now cover title
  fallbacks and hashtag/tag side-channels, HELD release actually publishes,
  recurrence idempotency is pagination-immune, native-schedule declines
  defer instead of publishing early, YouTube uploads verify-before-retry,
  analytics observations are once-ever per post.

### Voice System Overhaul — systemwide bug hunt + out-of-the-box hardening

Root-caused and fixed the "voice is broken again" report across all three
tiers. Spec: `docs/VOICE_SYSTEM_SPEC.md` (new, STORM-derived); findings
verified against the live Gemini API by real `bidiGenerateContent` connects.

**Fixed — Tier 3 (Gemini Live):**
- `/friday-live`, its manifest, and service worker 404'd after the v5 `src/`
  restructure (`send_from_directory('.')` resolved against the package dir) —
  the Tier-3 PWA client was completely unreachable. Anchored like `/static`.
- The model story was BACKWARDS: live connect probes show
  `gemini-3.1-flash-live-preview` and the 12-2025 preview still work, while
  the previously "verified" fallback `gemini-2.5-flash-preview-native-audio`
  does not exist (1008). New verified chain: `native-audio-latest` →
  `preview-09-2025` → `3.1-flash-live-preview`; explicit
  `_RETIRED_LIVE_MODELS` denylist checked BEFORE the marker heuristic, so
  `validate_live_model` reports `retired` and the auto-correct (now firing on
  `retired` + `unknown`, persisting only the delta — never the offline
  routing overlay) actually corrects the IDs it was built for.
- `DEFAULT_SETTINGS.voice_model` (which always wins over `LIVE_MODEL`) updated
  to the `-latest` alias everywhere (core, setup wizard, registry, UI consts);
  the new IDs are selectable in Settings and priced in the cost meter.
- `LiveConnectConfig` construction moved inside the per-attempt try (a
  pydantic ValidationError on older SDKs was silent); the top-level runner
  crash now sends an error frame instead of a debug-only log line.
- `friday_live.html`: capped exponential reconnect backoff, fatal-error stop
  (no more 1.5 s reconnect storms into a bad key), sticky actionable error
  banner, WS auth token support, and caught `getUserMedia` rejections.

**Fixed — egress gate (was killing cloud voice + chat):**
- The keyword layer's bare substring matching classified Friday's own system
  prompt ("Sovereign Vault") and everyday turns ("doctor appointment",
  "courtesy" via 'court') as TIER-3, emptying `system` and message content on
  EVERY sealed cloud call — the Anthropic API then 400s and the voice user
  hears "Sorry, I hit an error." Now: word-boundary matching, strong/weak
  keyword split (2+ distinct weak hits escalate), product-architecture terms
  excluded, span-level paragraph redaction instead of whole-field drops, a
  trusted-constant registry (the shipped system prompt survives sealing),
  never-empty message substitution, and a false-positive leg in the startup
  self-test. Leakage posture unchanged: flagged content still never leaves.
- Closed the Gemini Live gate BYPASS: tool results (email snippets, wiki
  excerpts), typed turns, and voice-context openers now pass the egress gate
  before reaching Google.

**Fixed — Tier 1 (local CPU):**
- Silero VAD judged each ~85 ms mic chunk by only its first 32 ms, discarding
  utterance onsets and endpointing mid-sentence — now scores every 512-sample
  window (max-pool) and keeps a ~250 ms pre-roll, so first syllables reach
  Whisper.
- `/api/voice/setup/test` 500'd on every call (`b64encode` on a BytesIO) —
  the first-run TTS test could never pass on any tier; it now returns real
  audio (and the wizard actually PLAYS it) or an actionable 503.
- Piper voice download: streamed with a 30 s timeout (was `urlretrieve` with
  none, hanging all voice sessions forever on a stalled connection while
  holding the engine lock); model-load failures now surface the real cause.

**Fixed — Tier 2 (local GPU):**
- `gpu_status()` now falls through to nvidia-smi when torch is CPU-only, so a
  physical RTX GPU is detected and the "install a torch-CUDA wheel" hint can
  actually surface; an explicit `local-gpu` preference that degrades to CPU
  is announced with the reason instead of silent; `voice-local-gpu` dep-group
  status requires real CUDA, not mere torch importability.

**New — in-UI install + diagnostics (out-of-the-box requirement):**
- `services/voice_installer.py` + `POST /api/voice/setup/install[/status|/cancel]`:
  allowlisted background installs (Tier-1 deps, Tier-1 model download, Tier-2
  torch-CUDA + NeMo) with streamed logs, disk preflight, and cancellation — no
  180 s cap, no pip incantations.
- Voice Setup Wizard: per-step Install/Download buttons, live install log, a
  GPU-tier step, honest step statuses (derived from real health fields — a
  fresh machine no longer shows green checks), and stale-model results gate
  readiness.
- Mic/speaker Test buttons (live level meter, AirPods zero-PCM detection)
  wired into the audio-device popup — the implementations existed but nothing
  rendered them. Voice Tools toggle and device selections now persist
  (`voice_tools`, `audio_input_device_id`, `audio_output_device_id` were
  missing from `DEFAULT_SETTINGS`, so every save silently reverted on reload).
- Per-tier smoke gate `tests/smoke/test_voice_tiers.py`: Tier-1 real TTS→STT
  loopback, Tier-2 detection contract + runnable-or-actionable-skip, Tier-3
  chain sanity + opt-in live connect probe (`FRIDAY_SMOKE_CLOUD=1`).

**Fixed — restructure fallout:**
- Root `server.py` shim works from any cwd; `friday start`/`setup` inject
  `PYTHONPATH` (they crashed instantly in non-pip-installed checkouts) and
  report the child's exit code; `friday update` uses repo-root paths again
  (it always claimed "not a git repository"); `pystray`/`pillow`/`pyttsx3`
  added to manifests; stale v4.5.0 flat-layout `agent_friday.egg-info`
  removed; the index.html 404 message names the real build command.

### Content Pipeline — self-knowledge & docs

- **Friday knows her publishing stack.** `SELF.md` gains a Content Pipeline
  capability section (compose → schedule → publish → measure → learn, the
  sovereignty invariants — hold-for-review, no engagement automation, honest
  Substack/Medium handoff — plus demo talking points), and `VOICE_DEMO.md`
  gains a "How I publish" section so voice demos describe publishing with
  the same lucidity as creation.
- **API reference.** `docs/API.md` documents the `/api/content/*` v2 routes
  (posts, compose, schedule, publish-now, cancel/release, preview,
  repurpose, queue, calendar, best-times, analytics, insights, platform
  connect/disconnect, voice cards, export) per
  `docs/CONTENT_PIPELINE_SPEC.md` §11.
- **Manual test procedures.** Per-platform first-connect + first-publish
  checklists (spec §15) appended to `tests/MANUAL_TEST_PROCEDURES.md` —
  real platform OAuth can't be CI'd.

### Model Selector

- **The top-bar pill is just the model name.** It now shows the active
  orchestrator's short label plus a caret — no cloud/home emoji, no
  "+ Local" suffix. Fresh installs still read "Sonnet 5".
- **Clicking it opens a compact 320px panel instead of a wall of models.**
  *Quick Switch* pins the current model first and offers up to 4 available,
  provider-diverse alternatives — one click switches the orchestrator and
  closes the panel. *By Role* collapsible rows (one open at a time, each a
  short capped list: 5 options for Orchestrator/Subagent, 4 per Creative
  sublist, the available Voice engines) cover Orchestrator, Subagent,
  Creative — split into an Image model (flat `creative_model`) and a Video
  model (`capability_routing.creative_video` `{model, provider}`) — and
  Voice, which selects the engine and shows the Gemini Live model sublist
  only while the gemini engine is active. A *Routing Mode* row toggles
  Cloud Only / Smart / Local Pref. / Local Only with compact stats, a
  *Local Models* section appears only when Ollama is actually running
  (top 4 with size badges, current default pinned into view, "▸ N more
  installed" expands the full list in-panel), and a *Browse All Models*
  footer button deep-links Settings straight to the Providers tab (via
  `window.__fridaySettingsTab` + the `friday:settings-tab` event).
- **No dead rows.** The panel never shows more than ~15 model entries, and
  unavailable models simply don't appear — nothing is grayed out.
- **`GET /api/models` role lists are now curated.** Only descriptor-declared
  statics plus live Ollama models make the role lists; the discovery long
  tail (OpenRouter's 300+) stays in the flat `models` list. Every entry
  carries a new boolean `curated` field, and `selected` gained
  `creative_video_model` (read from `capability_routing.creative_video` —
  video has no flat `*_model` key).
- **`GET /api/models/search` learned modality, local, and sort.** New
  `modality` param (exact member of the modalities list — one of
  vision/image/video), `local=1` (on-device providers only), and `sort`
  (`price` | `price_desc` | `context`), all applied BEFORE the limit
  truncation; price sorts put unpriced entries last, since unknown ≠ free.
  Result rows gained `modalities` (list) and `local` (bool), static rows
  resolve their label + modalities from provider `model_meta`, and
  Ollama-backed providers list their live installed models. Negative wire
  prices (OpenRouter's "pricing varies" sentinel) now read as unpriced —
  never as the cheapest model in a sort or a negative spend figure.
- **Settings → Providers Model Browser is a real browser.** It
  auto-populates on open (no empty state) and adds a search box, a provider
  filter dropdown, capability filters (Tool calling / Vision / Image gen /
  Video gen / Free / Local), a sort dropdown (available first / cheapest /
  highest price / largest context), a result count line, per-row modality
  icons, pricing as "$X in / $Y out per 1M", and assign buttons —
  Orchestrator, Subagent, plus Image/Video buttons on models with those
  modalities.
- **Off-menu assignments keep their names.** A model assigned from the
  Model Browser that sits outside the curated role list now renders in the
  AI Models tab selects with its catalog label + "(via Model Browser)"
  instead of "(no longer offered)".

---

## [5.2.1] — 2026-07-04 — "Found It (wiki restore + voice fixes)"

### Fixed

- **The personal wiki is back.** Since the 2026-06-27 security refactor moved
  `WIKI_DIR` to `~/.friday/wiki`, the one-shot migration from the legacy
  `~/wiki` was guarded by `not WIKI_DIR.exists()` — always False on long-lived
  installs (auto-briefings had created the directory years of commits earlier)
  — so it silently no-oped and the user's real wiki (46 files: personal,
  people, journalism, identity, professional, research, ai-personality, meta)
  was orphaned while the UI showed only briefings. The migration is now a
  per-file, never-overwrite, idempotent merge that renames the legacy dir only
  after a fully successful copy. (The encrypted `vault/wiki-*` dirs are stale
  April snapshots from the predecessor app's vault — kept as a backup, never a
  serving path.)
- **"Open settings" opened the System workspace.** The spoken-navigation alias
  table mapped settings/preferences/config to `system`, and neither `settings`
  nor `marketplace` existed as navigation targets — while the voice tool's
  hard-coded workspace list predated both, so Gemini couldn't even name them.
  Settings and Marketplace are now first-class targets, "…menu" phrasing
  resolves, the tool's workspace list is derived from the resolver's alias
  table (single source of truth), and VOICE_DEMO.md stops teaching that
  settings lives inside System.
- **Voice turn-desync ("two parallel conversations").** Two renewal-seam bugs:
  the liveness watchdog could false-fire in the middle of a long user
  monologue (models that don't stream mid-turn transcription look "quiet"),
  forcing needless session renewals; and mic audio buffered during any
  renewal seam was burst-fed into the fresh session, making Gemini respond to
  utterances from half a minute earlier. The watchdog now fires only after
  speech has ENDED with no reply, and every renewed/resumed leg drains stale
  buffered audio before listening.

---

## [5.2.0] — 2026-07-04 — "Always Listening (voice continuity + creation tools)"

Voice mode you can trust through an hours-long conversation — and interrupt
mid-sentence — plus real slide-deck and website generation from chat or voice.

### Fixed

- **You can interrupt Friday again (speaker-mode barge-in).** Speaker mode
  runs Gemini Live with `ActivityHandling.NO_INTERRUPTION` (the echo-safety
  fix), which — per the Live API reference — means the model NEVER stops on
  its own; and the old client-side interrupt detector had been removed, so no
  layer implemented barge-in and Friday talked straight through the user. The
  bridge now detects deliberate talk-over itself (`LiveBargeDetector`: seeds a
  speaker-bleed RMS baseline from the quietest quartile of a per-response
  grace window — capped, so a user who talks through the grace can't raise
  the bar against themselves — then fires on ≥200 ms of speech above
  max(550, 3× the bleed)). The detection window tracks CLIENT PLAYBACK
  (`{type:'speaking'}` transitions from the browser, with a bytes÷48000
  estimate fallback for the PWA) because Gemini streams faster than
  real-time and users interrupt during playback, long after streaming ends.
  On fire: the rest of the turn's audio is swallowed server-side, the client
  is flushed via `{type:'interrupted'}`, and the in-flight generation is
  cancelled with the documented `client_content` interrupt. Escape is a
  deterministic manual barge hotkey; headphones mode keeps native
  `START_OF_ACTIVITY_INTERRUPTS`. Verified end-to-end against a live
  session: `interrupted` fired 0.19 s after talk-over began, zero straggler
  audio. An adversarial multi-agent review then hardened the seams: explicit
  client barges are trusted unconditionally (the Escape flush could close
  the play window ahead of its own barge frame), barge/tool/playback state
  resets at every session-leg start, the liveness watchdog stands down while
  a voice tool call runs, zombie WS handlers are generation-fenced away from
  the resume cache, stopping voice mid-reconnect can no longer leak a hot
  mic, and the phone PWA now actually silences scheduled audio on interrupt.

- **Hours-long Gemini Live voice sessions.** The "voice randomly goes silent
  while the mic still shows live" dropout is fixed on both ends. Server
  (`/ws/live`): a per-leg liveness watchdog force-renews the session when the
  user is audibly speaking but Gemini has gone quiet (the hung-receive case that
  used to freeze a call forever); GoAway now drains the in-flight sentence
  before renewing; renewal connects ride a retry ladder (handle → handle →
  fresh) instead of falling back to an amnesiac session; conversation state is
  hoisted above the model-fallback loop so a mid-call fallback no longer
  re-greets or drops the transcript; and the newest resumption handle is cached
  across WebSocket connections so a reconnecting browser resumes the SAME
  conversation. Client: voice mode now auto-reconnects with capped backoff when
  the socket dies unexpectedly, and a heartbeat-based stall watchdog force-cycles
  half-open sockets; a deliberate stop sends `{type:'bye'}` so the next session
  starts fresh.
- **Friday knows itself again.** `SELF.md` / `VOICE_DEMO.md` silently stopped
  loading after the `src/` restructure (they stayed at the repo root while core
  resolved them against the package root) — every system prompt shipped with
  ZERO self-knowledge. `_res_file()` now falls back to the repo root, and both
  docs were rewritten to match the real dock (Sites, Content, Trust,
  Marketplace, the full Studio suite) and the real creation tools.

### Added

- **`create_presentation` + `create_website`** — chat/voice agent tools, POST
  `/api/create/presentation` and `/api/create/website`, and
  `services/showcase_engine.py`. The routed text model writes a strict JSON
  spec; a deterministic template renders a polished, self-contained HTML
  artifact into the Studio gallery (deck: keyboard nav, speaker notes,
  print-to-PDF; site: multi-page hash routing, responsive, deploys anywhere).
  The LLM never writes HTML — same output quality every run, offline-safe.

### Docs

- Repo cleanup for release: internal working artifacts (storm reports, review
  logs, UI test results, competitive analyses, stale release-note files, the
  release plan) removed from the tree; design specs consolidated under
  `docs/`; `CHANGELOG.md` + GitHub Releases are now the single history.

---

## [5.1.1] — 2026-07-04 — "Gemini July-2026 model lineup"

Registry/catalog refresh against the live Gemini API surface (verified
against ai.google.dev model cards, pricing, and deprecation tables,
2026-07-04).

### Added

- **Gemini 3.5 Flash** (`gemini-3.5-flash`, stable), **Gemini 3.1 Pro**
  (`gemini-3.1-pro-preview`) and **Gemini 3.1 Flash-Lite**
  (`gemini-3.1-flash-lite`) in the google-gemini provider, catalog meta,
  and cost tables. Text roles stay `[]` until a google text dispatch
  exists in `routing/model_router.py` — same "never offer what can't
  dispatch" deal as Gemini 2.5 Pro. (Gemini 3.5 Pro is not yet in the
  public API as of 2026-07.)
- **Gemini Omni Flash** (`gemini-omni-flash` → `gemini-omni-flash-preview`):
  any-to-any conversational video generation/editing (I/O 2026), wired
  end-to-end — creative role in the registry, offline-fallback picker
  entry, pricing ($1.50/1M in; $17.50/1M video out ≈ $0.10/s of 720p),
  and a new Interactions-API dispatch branch in
  `creative_engine.generate_video()` (Omni renders synchronously — it is
  not a Veo-style long-running operation).
- Nano Banana Lite aliases → `gemini-3.1-flash-lite-image`.

### Fixed

- **Every Veo alias pointed at a dead endpoint**:
  `veo-3.0-*-generate-preview` shut down 2025-11-12, and the `veo-3.0` /
  `veo-2.0` GA models retired 2026-06-30. All aliases now resolve to the
  live `veo-3.1-*-generate-preview` family, with new `veo-3.1`,
  `veo-3.1-fast`, and `veo-3.1-lite` ids exposed.
- **Nano Banana Pro resolved to `gemini-3-pro-image-preview`** — shut
  down 2026-06-25 — now the stable `gemini-3-pro-image`. Nano Banana 2
  now maps to the actual NB2 model (`gemini-3.1-flash-image`) instead of
  the 2.5-era original, which stays reachable as plain `nano-banana`
  (sunsets 2026-10-02).
- New Gemini 3.x text ids added to `_FORBIDDEN_CREATIVE` so a text model
  can never be picked as a creative target.

---

## [5.1.0] — 2026-07-04 — "Model-Agnostic (provider layer P0–P2)"

The first three phases of `docs/MODEL_AGNOSTIC_PROVIDER_SPEC.md`: Friday routes
by REGISTRY, not by model-name guessing, speaks to any number of
OpenAI-compatible providers concurrently, and ships OpenRouter first-class.

### Added

- **Provider descriptor schema v2** (`routing/provider_descriptors.py`):
  adapter/type aliasing, auth env-var chains with aliases (`HF_TOKEN` /
  `HUGGINGFACE_API_KEY`), per-provider network/discovery/pricing/budget/feature
  blocks, and real validation with actionable errors — a bad
  `~/.friday/providers/*.json` is skipped, logged, and surfaced in
  `/api/health/full` + Settings instead of vanishing. YAML descriptors accepted.
- **Ten new built-in providers**: OpenRouter (enabled, first-class), Hugging
  Face Inference Providers router, Groq, Together, Fireworks, Mistral,
  DeepSeek, xAI, Perplexity, Cohere — one key press each in Settings.
- **OpenRouter first-class** (GAP-1): live model discovery from
  `/api/v1/models` (pricing, context, modalities, tool support, `:free`
  detection) cached with TTL + stale-while-revalidate; usage accounting
  (`usage.cost` is the authoritative billed figure in the ledger); server-side
  `models[]` fallback support; 429 `Retry-After` etiquette.
- **Model resolver** (`resolve_model`, GAP-4 fix): registry-first model→provider
  attribution. `meta-llama/llama-4-maverick:free` resolves to OpenRouter (the
  `:` no longer misroutes aggregator ids to Ollama), `gemma3:4b` to the local
  daemon, `claude-x:latest` (installed) to Ollama even with a cloud-looking
  name, `provider::model` is always explicit.
- **Multi-provider dispatch** (GAP-3 fix): `_call_openai(provider=…)` reads the
  endpoint, credentials, and headers from THAT provider's descriptor — Groq
  subagent + OpenRouter orchestrator + local Ollama vault in one session. The
  single-slot `model_routing.openai_*` settings keep working (legacy path).
- **Provider health measurement plane**: per-provider rolling p50/p95 latency,
  error rate, last success/failure, and a 5-failure circuit breaker with 60s
  cooldown — recorded from every adapter call, surfaced in
  `GET /api/providers(/health)`, and consulted by the generation ladders
  (a 'down' provider is tried last, never first).
- **Provider management API**: `POST /api/providers/validate` (dry-run),
  `PATCH /api/providers/<name>` (enable/disable/edit),
  `POST /api/providers/<name>/test` (latency + models_seen + optional 1-token
  ping), `POST /api/providers/<name>/models/refresh`, and
  `GET /api/models/search` across every provider's statics + discovery cache.
- **Settings → Providers tab**: health-dot provider list (measured, not
  assumed), encrypted key management, Test Connection, model-list refresh,
  per-provider spend today + budget cap display, Add Provider from templates
  (classification radio gated to private hosts), and a cross-provider Model
  Browser with free/tools/price metadata.
- **Pricing service** (`services/pricing.py`): discovery-cache → descriptor →
  dataset → v1-blended lookup; unknown price is `None`, never treated as $0.

### Security

- **Egress classification is registry-driven** (GAP-9 fix): the gate's local
  bypass now requires `classification: "local"` + a local-capable adapter + a
  loopback/RFC1918/`.local` base_url **re-verified at call time**. A descriptor
  *typed* `ollama` pointing at a remote URL is classified cloud and sealed; a
  genuine LAN vLLM/LM Studio finally gets the legit local bypass. The old
  `{"ollama", "local"}` set survives only as the fallback for non-registry
  family names.
- Descriptors are data, never secrets: raw `api_key` fields are rejected with a
  400 pointing at the encrypted key endpoint, and `extra_headers` cannot set
  `Authorization`.

---

## [5.0.1] — 2026-07-01 — "Super Agent (hardening)"

The post-release hardening pass — the full H1–H10 backlog from
`docs/FABLE5_INTEGRATION_STORM_REPORT.md`, closing the egress gaps the boundary
reviews missed and the fresh-user onboarding gaps the install audit found.

### Security

- **The agent tool loop and the user-text Gemini paths now pass the egress gate.**
  `tool_result` blocks pulled mid-loop are classified before re-send (a withheld
  result becomes an explanatory marker, never silent empty output); the creations,
  outreach-draft, QA-vision-intent, image-gen, and voice-TTS Gemini calls gate
  their user-authored text. Sensitive TTS routes to on-device voice instead of the
  cloud. Image/camera bytes remain the documented can't-text-classify caveat.

### Added

- **Onboarding vault passphrase (H4).** The first-run wizard offers an optional
  passphrase that arms AES-256-GCM before launch; Settings → Privacy shows an
  "Encrypt Vault" prompt whenever the vault isn't armed. New `/api/vault/passphrase`.
- **First-run Gemma pull (H5).** The wizard's hardware step offers a one-click
  `gemma3:4b` download when Ollama is running but the model isn't present, so the
  zero-key local path is real on first run.
- **Data rights UI (H6).** Settings → Privacy → "Your Data": export everything as
  a ZIP (`/api/data/export`) and a typed-ERASE-guarded wipe (`/api/data/erase`),
  mirroring `friday export` / `friday erase`.
- **SQLite migration helper (H10).** `services/db_util.py` adds forward-only
  additive column migration so upgrading users' DBs gain new columns instead of
  silently keeping the old schema.

### Changed

- **`friday setup` is the documented key path (H2).** README + INSTALLATION stop
  steering users at plaintext `start.bat` env vars and correct the docs that
  wrongly listed an Anthropic key as *required* — `gemma3:4b` is the zero-key default.
- **Voice model id is validated at startup and in Settings → Voice (H8).** A
  stale/renamed Gemini Live id (the opaque "voice broken" that looks like an auth
  error) is now caught up front.

### Fixed

- **Accessibility (H9):** a global keyboard `:focus-visible` ring (the holographic
  UI had none) and ARIA labels on icon-only close buttons.
- **Cross-platform tests (H7):** the hermetic-home conftest now redirects POSIX
  `HOME` too, not just Windows `USERPROFILE`.

---

## [5.0.0] — 2026-07-01 — "Super Agent"

The developer-tool → sovereign-consumer-product transformation. Adds a local,
closed-loop learning system, overnight memory consolidation, user modeling, an
editable personality file, a bundled zero-key local model, voice-first
onboarding, and messaging-channel bridges — every one of them local-first and
routed through the existing cLaws governance + egress gate.

### Added

- **Learning Loop Engine** (`services/learning_loop.py`). Observes task outcomes,
  mines successful (task-type, tool-strategy) patterns into text *heuristics*,
  scores them with a Wilson lower bound blended with satisfaction, and promotes
  the best into the system prompt. Local-only, SQLite-backed (`learning.db`),
  bounded by `max_active_skills`. **Skills are advisory text, never executable
  code** — no new tool surface. Weekly `learning_epoch` scheduler job. API under
  `/api/learning/*`.
- **Memory Dreaming** (`services/memory_dreaming.py`). Nightly (03:00) local
  consolidation: reviews the day's ChromaDB conversation turns, extracts topics
  and durable facts (preferences/decisions/bio), feeds high-confidence facts to
  the user model, tags noise, and writes `~/.friday/dreams/<day>.md`. Never
  touches cloud. API under `/api/memory/dream*`.
- **User Modeling** (`services/user_model.py`). Tracks communication style
  (formality/verbosity), per-domain expertise, and workflow patterns from each
  turn; injects a compact **TIER_1** `== USER MODEL ==` block into every system
  prompt. SQLite-backed with a GDPR-style `forget()`. API under `/api/user-model/*`.
- **SOUL.md personality config** (`services/soul.py`). Friday's personality now
  lives in a user-editable `~/.friday/SOUL.md` (seeded from the shipped default,
  versioned in `soul_history/`). `core._load_agent_personality()` reads it first.
  API under `/api/soul*`.
- **Bundled Gemma / no-API-key mode.** Default local model is now **`gemma3:4b`**
  (Google's open Gemma 3 4B-IT, ~8 GB RAM). `install.{sh,ps1,bat}` auto-install
  Ollama and pull the model (best-effort, skippable via `FRIDAY_SKIP_MODEL=1`).
  Chat works fully offline with zero cloud keys; creative/voice degrade
  gracefully. `friday doctor` / `friday health` now report Ollama + Gemma + a
  "no-key mode ready" status.
- **Voice-First Onboarding** (`services/onboarding.py`). First-run state machine
  — greet → name → voice test → optional keys → Ed25519 identity → SOUL.md —
  spoken via the local voice engine (no cloud key required). API under
  `/api/onboarding/*`.
- **Channel bridges** (`services/channels/`). Discord (`discord.py`, graceful
  no-op when absent) and Telegram (stdlib, zero-dep) bots. Every inbound message
  runs the shared agent loop; every reply passes the **egress gate** before send.
  Disabled by default, allowlist-gated, bot tokens in the credential store. API
  under `/api/channels/*`.

### Fixed

- **Blueprint auto-discovery registered zero routes in two shipping paths** —
  the entire API 404'd. (1) The repo-root `server.py` shim `exec()`s the package
  server, so `__file__`-relative discovery globbed a nonexistent `<repo>/routes`.
  (2) The packaged **AgentFriday.exe** never bundled `routes/*` at all: the spec's
  `collect_submodules('agent_friday')` silently returned `[]` because `src` wasn't
  on `sys.path` at spec-eval time, and the dynamically-imported route modules were
  invisible to PyInstaller's static analysis. Fixed by enumerating routes via
  `pkgutil` with an explicit `ROUTE_MODULES` fallback for the frozen build
  (drift-guarded by `tests/api/test_blueprint_discovery.py`) and adding `src` to
  the spec's path so the route modules are bundled. Verified: `python server.py`
  and the frozen `.exe` both serve 200 on every endpoint.
- **The entire UI silently failed to mount** — an unclosed
  `<div style={{display:'none'}}>` in `FamilyWS` left the component's outer JSX
  element open, so the single inline Babel script died at parse time: blank
  screen, bare holo scene, empty console. Also repaired ~1,390 double-encoded
  UTF-8 (mojibake) runs and a stray BOM in `ui_parts/app.html` (dock emoji,
  comment rules, license-picker hints).
- **JSX is now precompiled at build time.** `build_ui.py` compiles the app
  bundle with `@babel/standalone` under node when available (in-browser Babel
  remains as fallback). Cold-load `DOMContentLoaded` dropped from ~17.5 s to
  ~0.35 s, and index.html no longer needs the Babel CDN at runtime.
- **UI libraries are vendored — the shell and holo scene now load offline.**
  React 18.3.1, ReactDOM, marked 9.1.6, highlight.js 11.9.0 (+theme CSS), and
  Three.js r128 with its six post-processing files moved from unpkg/jsdelivr/
  cdnjs into `static/vendor/`, each verified against the SRI hash the old CDN
  tag pinned. Remaining external fetches are Google Fonts and the optional
  MediaPipe camera libs, both of which degrade gracefully.
- **`/api/vault/status` was never implemented** — the Settings → Privacy
  "Sovereign Vault" card 404'd forever (silently). New endpoint reports live
  encryption state (AES-256-GCM vs None), entry/encrypted counts, and a
  `locked` flag (encrypted blobs on disk with no derivable key).
- **`/static/*` and the dock icon set never served.** `send_from_directory`
  with a relative path resolves against Flask's `root_path` (inside the
  package since the src/ move), so `/static/favicon.ico` 404'd — and
  `/assets/*` had no route at all, so the dock's designed SVG icon set
  (`assets/icons/*.svg`) had never once rendered; the emoji fallback always
  showed. Both routes now anchor to the process cwd like `serve_ui` does, and
  the two missing icons (`marketplace.svg`, `settings.svg`) were drawn in the
  set's neon line-art style. The dock now ships its intended icons.
- **`friday doctor` misdiagnosed keys and crashed on legacy consoles** — it
  now reads API keys from `start.bat`-style launch scripts (same precedence as
  the server's env bootstrap) and degrades ✓/✗ glyphs instead of dying with
  `UnicodeEncodeError` on cp1252 consoles.

### Notes

- All v5 subsystems are **local-only** and pass through cLaws governance and the
  egress gate. Nothing new introduces a default cloud dependency.
- 3,162 tests pass (64 new). See `docs/SUPER_AGENT_BUILD_SPEC.md` for the full
  design and `docs/RELEASE_NOTES_v5.0.md` for the release summary. (The suite
  grew to 3,629 once the v5 test files were committed — see [5.0.1].)

---

## [Unreleased]

### Removed

- **Removed the personal Co-Parent/OFW workspace and `ofw_monitor` skill from the
  public release.** The co-parenting platform monitor, its custody-calendar
  tracking, and the related draft mode were personal to the original author and
  are not part of the open-source distribution.

---

## [4.5.0] — 2026-06-06

The public-release hardening pass. Prunes the surface area down to the core
general-purpose workspaces, makes the powerful-but-risky subsystems opt-in, and
strips the founder's personal content out of source so a fresh user starts clean.

### Removed

- **Stub workspaces.** `FinanceWS` and `HealthWS` (vault-gated placeholders with
  no real integrations) are removed, UI + routes (`/api/finance/*`,
  `/api/health/*`). They can return later as Seeds/plugins.
- **Personal Co-Parent workspace, removed entirely.** The dedicated workspace
  component, its API routes, the platform message loader + notification monitor,
  the related calendar keywords, the message-classification lane, and its draft
  mode are all gone. (Sensitive personal data was always gitignored and never
  shipped.)
- **Redundant dock entries.** `FamilyWS`, `TrustWS` (trust is now a tab in
  News + Contacts), and `StudioWS` (functions live in Dev Studio and the Sites
  workspace) are no longer separate dock entries.
- **Content workspace pipeline.** `ContentWS` and its kanban endpoints
  (`/api/content/pipeline|idea|draft`) are removed; writing is consolidated into
  the Draft workspace (reachable via News → Share to Draft) and the chat pipeline.
  The draft library serving routes (`/api/content/drafts*`) stay.
- **FutureSpeak business pipeline.** The personal-CRM endpoints
  (`/api/futurespeak/{pipeline,revenue,legal,assets}`) and their UI tabs are
  removed. The workspace remains as a general-purpose **Sites** portfolio/deploy
  manager (projects + scan + scaffold).

### Changed

- **Dock pruned to 10 core icons:** Home, News, Messages, Calendar, Career, Code,
  Wiki, Contacts, Sites, System. (Settings remains the gear-button slide-out.)
- **Computer Control is now opt-in.** New setting `computer_control_enabled`
  defaults to **false**. The feature is surfaced under Settings as **Experimental**
  with a clear warning; the Ring-3 runtime grant and the kill switch are unchanged,
  and the grant endpoint now refuses unless the feature is enabled.
- **SkillOpt nightly job disabled.** The 3:30 AM auto-research job is commented
  out for general release (marginal value while the skill library is small); the
  infrastructure stays for when there are 50+ skills.
- **Voice debug logging gated.** Per-chunk voice logs are off by default — client
  logs behind `window.FRIDAY_VOICE_DEBUG`, server `_vlog` behind the
  `FRIDAY_VOICE_DEBUG` env var.
- **De-personalized for new users.** Hardcoded author-specific content (name,
  family, bio, local news feeds, personal keyword lanes, and seeded personal
  portfolio sites) has been replaced with generic, settings-driven defaults across
  the news editor, draft, and message subsystems.

### Security

- **Vault encryption-at-rest, wired into the running app.** The `vault_crypto.py`
  primitives (AES-256-GCM + Argon2id, already present and tested) are now actually
  used by `server.py`. A vault key is derived once from `FRIDAY_PASSWORD` at startup
  (`_get_vault_key`); sensitive files (finance, health, and
  `vault/{legal,finances,family}`) are transparently encrypted on write
  and decrypted on read (`_vault_write_text` / `_vault_read_text`); and any existing
  plaintext is encrypted in place on first boot (`_migrate_vault_plaintext`, verifies
  a decrypt round-trip before replacing each file). With no `FRIDAY_PASSWORD` set the
  vault stays plaintext (logged at startup) — behaviour is unchanged for keyless
  local-dev. New tests: `tests/test_vault_at_rest.py`. This closes the gap documented
  in `docs/SITE_VS_REPO_DISCREPANCIES.md` (vault was previously plaintext at rest).

---

## [4.4.0] — 2026-06-06

The trust-and-portability release. Hardens authentication, adds a third
(OpenAI-compatible) provider with a full agentic tool loop, gates every tool
call behind a sandbox policy, ships a portable SKILL.md registry, and closes the
loop on skill learning so real chat usage feeds the optimizer.

### Added

- **OpenAI-compatible provider** — A third cloud provider alongside Anthropic and
  Ollama. Opt-in via `model_routing.cloud_provider = "openai"` plus
  `openai_base_url` (defaults to OpenRouter), `openai_model`, and `openai_api_key`
  (or env `OPENAI_API_KEY` / `OPENROUTER_API_KEY`). Unlocks OpenRouter's hundreds
  of models and any `/v1` endpoint. Ships a **full agentic tool loop** at parity
  with the Anthropic path. Vault / sensitive requests still never route here —
  they stay local or on Anthropic.
- **Portable skill registry** (`skill_registry.py`) — A portable "SKILL.md
  folder" format: YAML frontmatter plus a markdown body, agentskills.io-compatible.
  Import/export across folder, zip, legacy-YAML, and OpenClaw formats. New HTTP
  routes `/api/skills`, `/api/skills/import`, `/api/skills/<name>/export`, and
  `/api/skillopt/state`. Matched skills are injected into the system prompt each
  turn, so newly learned skills take effect without a restart.
- **Closed-loop learning** (`skill_capture.py`) — Captures turn trajectories to
  CognitiveMemory and JSONL, feeds real chat usage into the SkillOpt optimizer,
  and runs a nightly `skillopt-nightly` auto-research job. Connects the
  previously-dormant SkillOpt machinery to live usage.

### Security

- **Auth hardening** — The session secret is now a persisted random value
  (`~/.friday/secret_key`, mode `0600`) instead of a hardcoded default. Credential
  checks are constant-time (`hmac.compare_digest`). A per-IP login throttle caps
  attempts at 8 per 5 minutes. New env toggles: `FRIDAY_TRUST_LOOPBACK` (default
  on; set `0` to require login even on localhost), `FRIDAY_WS_TOKEN` (optional
  token gating the `/ws/live` voice WebSocket), and `FRIDAY_COOKIE_SECURE` (Secure
  cookie for HTTPS / tunnel). Session cookies are now `SameSite=Lax` and
  `HttpOnly`.
- **Tool-execution sandbox** — Every agent tool call passes through a policy gate
  controlled by `FRIDAY_SANDBOX_MODE` (`off` / `confine` [default] / `strict`)
  and `FRIDAY_SANDBOX_ROOT`. `confine` keeps `write_file` inside a root (default
  `HOME`) and runs `run_command` against a destructive-command blocklist;
  `strict` additionally allowlists commands.

### Fixed

- **Command injection in the vibe-code launcher** — Closed a command-injection
  hole in the vibe-code terminal launcher.

---

## [v4.3] — 2026-05-28

The self-evolving interface release. Adds Liquid UI and the Seeds & Gardens
workspace architecture.

### Liquid UI

- **`liquid_ui.py`** — Friday's self-evolving interface engine.
  - `LiquidUIRequest` captures intent — explicit ("I wish I could…") or
    behavioral (workspace ping-pong, repeated filters, error loops,
    dwell-time collapse).
  - `FeatureSpecGenerator` produces structured specs with complexity
    tier classification: trivial (<1m, auto), simple (1–5m), medium
    (5–30m), complex (30–120m), epic (2h+).
  - `LiquidUIBuilder` writes React + backend artifacts to
    `~/.friday/liquid_ui/features/<id>/`, snapshots state, emits a
    hot-reload token. Source tree stays pristine.
  - `SuggestEngine` runs four behavioral detectors and surfaces
    proactive `Suggestion` objects with confidence scores.
  - `SnapshotManager` — HMAC-irrelevant but path-stable rollback. Every
    change snapshots touched files; Ctrl+Z eligibility = within 30s.
    60-day retention; Settings exposes the full chain.
  - Every Liquid UI feature is also a SkillOpt skill — usage events
    update accuracy / satisfaction / completeness.
- **`ui_parts/liquid_ui_panel.html`** — React management panel with
  build queue, feature cards, proactive suggestions, snapshot history,
  ✨ Wish modal.

### Workspace architecture

- README documents the **Seeds & Gardens** model and the new stock
  workspace layout:
  - Personal: Messages (unified inbox + outbound drafts), Family, Health
  - Professional: Career, Finances, Business, News
  - Creative: Studio (was "Content"; "Draft" rolls into Messages)
  - Infrastructure: Wiki, Trust, Code, Skills Observatory
  - Dashboard home with KPI cards, today's agenda, activity feed, alerts
  - ➕ Add Garden gallery: Smart Home, Travel, Education, Legal,
    Fitness, Entertainment, Real Estate, Pets …
- Design principles: pick 4–5 workspaces at setup; reorder by frequency;
  auto-minimize after 30 days unused; every menu has ✨ Suggest +
  right-click "Improve this workspace"; complete rollback via Liquid UI
  snapshots.

---

## [v4.2] — 2026-05-28

Self-improving skills release. Adds a SkillOpt-inspired engine, two
production skills, and a holographic Observatory UI.

### Skills system

- **`skillopt_engine.py`** — Versioned skills with composite scoring,
  validation gate (5% regression tolerance), and a Karpathy-style
  AutoResearch loop that proposes patches when rolling scores drop ≥ 10%
  below the all-time best. JSONL execution log per skill; `best_skill.md`
  artifact per champion. CLI: `python skillopt_engine.py status`.
- **`skills/job_scanner/`** — Autonomous LinkedIn discovery every 4h
  during active hours. Round-robin keyword rotation, score-weighted
  notifications (title × 3, salary × 2, remote × 2, skills × 2,
  seniority × 1.5, company × 1), dedup against `JobTracker`, daily cap
  of 6 priority alerts.
- **`skills/application_engine/`** — Full-cycle: intel → resume tailor →
  cover letter → ATS form plan → submission → tracker log. Epsilon-greedy
  resume A/B bandit. Quality gates: salary floor ($150K), confirmation
  above $300K, dedup-apply, brand-voice ≥ 0.75, cover-letter word count
  bounds. Greenhouse / Lever / Workable / SmartRecruiters field maps.
- **`data/job_tracker_schema.py`** — `JobListing`, `ApplicationRecord`,
  `JobTracker` dataclasses with atomic JSON writes, pipeline status
  tracking (discovered → triaged → applied → screening → interview →
  offer → closed/rejected/withdrawn), and 30-day response-rate analytics.
- **`notifications.py`** — Friday-Chat-ready templates: priority job
  alerts (🔴), daily digests (🟡), weekly reports (📊), interview
  detection (📞), skill improvement announcements (🧠), skill regression
  notes.

### UI

- **Skills Observatory** (`ui_parts/skills_observatory.html`) — React +
  Recharts workspace. Skill cards with sparkline trends, version history
  with inline diff, execution scatter plot with reference lines, active
  experiments panel, research log, champion-vs-challenger comparison.
  Holographic dark theme (`#0a0e1a` base, cyan `#00d4ff`, blue `#3b82f6`,
  magenta `#ff0080` accents, glass cards).

### Setup & onboarding

- **Existing-user detection** — Setup wizard and `friday` CLI now skip
  re-setup when any of these are present: `.setup_complete` marker,
  API keys in config or environment, or a generated `start.bat`. Use
  `setup_wizard.py --force` to redo setup from scratch.
- **Branded onboarding banner** — New users see the FutureSpeak.AI boxed
  ASCII art banner on first run.

### Cleanup & hygiene

- Removed one-shot scripts (`merge_gemini.py`, `patch_career.py`,
  `write_scene.py`), base64 chunk fragments (`chunks/`, `combine.b64`,
  `p0.b64`, `temp_b64.txt`), legacy PowerShell decoders, and stale
  install logs.
- Untracked `.asimovs-mind/vault/bridge-token` and `port` — these are
  per-machine secrets and should never have been in git history.
- Strengthened `.gitignore`: now covers `.env*`, `.claude/`, `*.pyc`,
  `settings.json`, `credentials.json`, skill-state JSONs, all editor
  backup variants.

---

## [v4.1] — 2026-05-26

Major feature release. Built in a single focused session. Everything below was designed, implemented, and shipped today.

### Governance & Security

- **Governance gate with privilege rings** — Every tool call passes through `_evaluate_policy()` before execution. Four rings (0=read-only, 1=local-write, 2=network, 3=OS-control) with distinct permission requirements.
- **Decision BOM audit chain** — HMAC-SHA256 signed decision records appended to `~/.friday/vault/decision-bom.jsonl`. Tamper-evident; covers every allow/deny decision with timestamp, tool, ring, policy, reason, and signature.
- **Computer control with kill switch** — Ring 3 (`move_mouse`, `click`, `type_text`, `press_key`, `screenshot`, `scroll`) enabled by user toggle. Rate-limited to 20 actions/second. Blinking red indicator in top bar. Kill switch button always visible in UI for instant suspension.
- **Blocked operations list** — Hard-coded deny list for destructive shell commands regardless of ring level: `rm`, `del`, `rmdir /s`, `format`, `shutdown`, `reg delete`, `taskkill`, and others.

### Voice Mode

- **Live WebSocket audio** — `/ws/live` endpoint connects to Gemini 3.1 Flash Live Preview for real-time bidirectional audio. Mic button in UI opens the WebSocket session.
- **Chat transcript persistence** — Voice conversations are transcribed and saved to chat history alongside text conversations, with `[voice]` provenance tag.
- **Context-log persistence** — Voice turns logged to `~/.friday/vault/context-log/` like text turns.
- **Adaptive voice/text mode** — UI auto-detects when a voice session is active and switches TTS response format (1–3 sentences, no markdown) for the Claude system prompt.
- **Audio device selector** — Settings panel shows available audio input/output devices, lets user switch without restart.
- **Fixed audio extraction path** — Resolved `chunk.data` vs `part.inline_data.data` extraction bug that caused silent audio responses.
- **Fixed Gemini Live API version** — Corrected `http_options` to use `v1alpha` (was using wrong version causing 404s).

### Chat UI

- **Rich markdown rendering** — Chat responses render full GitHub-flavored markdown: headers, bold, italic, inline code, fenced code blocks with syntax highlighting, bulleted and numbered lists, tables, blockquotes.
- **Code block copy button** — Each fenced code block has a copy-to-clipboard button in the top-right corner.
- **Message pinning** — Pin any chat message; pinned messages are excluded from the 30-day retention purge.
- **Chat history search** — Search bar filters chat history by message content.
- **Source citations** — Chat responses from tool-augmented turns show a "sources" section with links.

### Model Selector

- **Model selector UI** — Top bar shows model pills (orchestrator + subagent + creative). Click any pill to change model without restarting.
- **All Claude 4.x models** — Claude Opus 4.7, Sonnet 4.6, Haiku 4.5 available.
- **Gemini models** — Gemini 2.5 Flash, 2.0 Flash, 1.5 Pro, Lyria, Veo 2.0.

### Tool Expansion (12 → 30 tools)

**New tools added:**
- `query_calendar` — Check upcoming calendar events
- `get_career_pipeline` — Read job search status from wiki
- `get_briefing` — Fetch most recent daily briefing
- `learn_skill` — Create/modify/delete/list skill YAML workflows in `~/.friday/skills/`
- `search_email` — Search Gmail via connector
- `draft_email` — Draft email via connector
- `open_url` — Launch URL in Chrome
- `install_package` — pip/npm package installer
- `move_mouse` — Ring 3: move cursor
- `click` — Ring 3: mouse click
- `type_text` — Ring 3: keyboard injection
- `press_key` — Ring 3: key/chord press
- `screenshot` — Ring 3: screen capture (base64 PNG)
- `scroll` — Ring 3: mouse wheel
- `correct_wiki` — Global find-replace across entire wiki + vault JSONs
- `propose_wiki_update` — Queue wiki edit for user approval
- `describe_screenshot` — Gemini vision describes a screenshot
- `analyze_file` — Gemini multimodal file analysis

### Quick Draft with Background Tasks

- **`spawn_task` tool** — Agent can delegate deep work to a background thread with full tool access. Task runs in a Claude agent context; results appear in Task Tray.
- **Task Tray** — Bell-icon dropdown in top bar shows all active/completed tasks with live status, elapsed time, spinner, and collapsible log lines.
- **Cancel tasks** — Stop button kills a running background task.
- **Tool trace** — Each task stores a trace of every tool call it made, visible in the task detail panel.

### Holographic Scene

- **Scene persistence** — Preferred scene index stored in `~/.friday/personality.json`. Survives server restarts.
- **`POST /api/evolution`** — Set `{ preferred_scene_index: N }` to pin a scene; `null` to return to auto-rotation.
- **Terminal flash fixes** — Eliminated flash/flicker on scene transitions by fixing animation interpolation timing.
- **13 named structures** — Genesis Lattice, Sacred Sphere, Shannon Network, Geodesic Cathedral, Lovelace Astrolabe, Von Neumann Tesseract, Dirac Probability, Mandelbrot Set, Turing Möbius, Ocean of Light, Fibonacci Nerve, Transcendence, Giga Earth (Rez).

### Setup Wizard

- **CLI setup wizard** (`setup_wizard.py`) — Interactive rich terminal UI for first-run configuration. Covers agent name, orchestrator, creative engine, API keys, voice, scene selection, and writes `start.bat`.
- **Web setup wizard** — Glassmorphism overlay shown on first visit if `~/.friday/.setup_complete` is missing. Now includes API key entry step and scene picker (was previously just name/model/voice).
- **API key hot-reload** — Keys entered in the web wizard are live-loaded into the running process without restart.
- **`/api/setup/status`** — Returns `{ initialized: bool }` based on presence of `~/.friday/.setup_complete`.
- **`/api/setup/complete`** — Accepts all wizard choices including `anthropic_api_key`, `gemini_api_key`, `preferred_scene_index`.

### Privacy Shield

- **PII auto-redaction** — SSN, credit cards, phone numbers, email addresses, street addresses scrubbed before reaching Claude.
- **Smart tagging mode** — PII tagged as `[PII:type:hash]` with in-memory rehydration table; model never sees raw values, user sees restored responses.
- **Custom watchlist** — `~/.friday/privacy_shield.json` for project codenames, client names, and other sensitive tokens.
- **User email bypass** — Addresses in `user_email` and `owner_identities` settings pass through clean.

### Smart Context Loader

- **Keyword-routed wiki loading** — Message analysis routes relevant wiki sections into context automatically:
  - Career/job/resume → `~/wiki/professional/`
  - Family/kids/custody → `~/wiki/family/` + `~/wiki/legal/`
  - Named people → trust graph lookup → person's wiki file
  - Finance/budget → `~/wiki/finance/`
  - Health/medication → `~/wiki/health/`
- **Project context files** — Drop `.friday-context.md` or `AGENTS.md` in any project directory; automatically injected when messaging from that directory (Hermes-inspired).
- **200KB context cap** — Total context trimmed to prevent token overruns.

### Other Improvements

- **Append-only context logging** — Daily JSONL files in `~/.friday/vault/context-log/`, configurable retention.
- **Off-record mode** — Toggle to suspend chat logging without disabling tool-call logging.
- **Trajectory compression** — When chat history exceeds 2MB, old turns are summarized via a Claude call.
- **Wiki proposal workflow** — All agent-initiated wiki edits queue for user approval. Bell icon shows pending count.
- **Wiki global search** — Full-text search across all `.md` and `.txt` files in `~/wiki/`.
- **Epistemic scoring** — `/api/epistemic` endpoint scores independence across calibration, sourcing, uncertainty acknowledgment, bias resistance, and correction rate.
- **Personality traits** — `/api/personality` endpoint exposes maturity, curiosity, skepticism, humor, loyalty, directness, empathy, contrarianism.
- **Vibe Code terminals** — `/api/vibe-code/` endpoints spawn Claude tasks in new CMD windows with configurable workflow presets.
- **Camera mode** — Live video PIP with frame capture and auto-describe via Gemini vision.

---

## [v4.0] — 2026-04-14

### Added
- Initial Flask server with Anthropic Claude integration
- Personal wiki read/write with `read_wiki`, `search_wiki`, `propose_wiki_update`
- Three.js holographic scene (6 initial structures)
- Chat with persistent history (30-day retention, 500-message cap)
- PII scrubbing (basic SSN + CC patterns)
- Background task runner (first implementation)
- Trust graph integration
- Career ops tracker (parses `application-log.md`)
- Gemini creative endpoints: image, music, code art, poem, video
- TTS with 5 Gemini voice personas
- Settings panel with model selection, temperature, response length
- Daily briefing generation and serving
- Finance, health, vehicle workspace endpoints (template data)
- Countdowns endpoint
- Wiki pending approval workflow (first implementation)
- Mobile responsive layout

---

*Older history is available in git log.*
