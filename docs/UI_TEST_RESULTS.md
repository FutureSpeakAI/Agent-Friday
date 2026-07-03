# UI Test Sweep — v5.0 (2026-07-03)

Exhaustive user-facing test pass over every workspace, panel, form, dropdown,
and API surface of Friday Desktop v5.0, driven through a real Chrome session
against `localhost:3000`. Six issues found, six fixed, all fixes verified
live. Full regression suite run after the changes.

## Issues found and fixed

### 1. CRITICAL — Settings → Economy tab crashed the entire app
`SettingsTabEconomy` rendered `<EconomyPanel/>`, a component that does not
exist (`ReferenceError: EconomyPanel is not defined`). One click on the
Economy tab unmounted the whole React tree: dock, windows, chat — gone.
- Fix: render the real `WalletPanel` (ψ balance, η outstanding, Q score,
  genesis grant, transfers, leaderboard, transaction history) so the tab
  matches its spec (wallet + budgets via `OrchestratorBudgetPanel`).
- File: `ui_parts/app.html` (SettingsTabEconomy).
- A JSX-wide audit (`scripts/jsx_undefined_check.py`) confirmed this was the
  only undefined component tag in the UI (83 distinct tags checked).

### 2. CRITICAL — the `Safe` error boundary could not catch render errors
`Safe` was a **function component** wrapping `children` in `try/catch`.
React render errors are not catchable that way (children is an already-built
element tree; the throw happens later inside the renderer), so *any*
workspace exception nuked the entire desktop — exactly how issue #1
escalated from "broken tab" to "blank app".
- Fix: `Safe` is now a class error boundary
  (`getDerivedStateFromError` / `componentDidCatch`). A crashing workspace
  shows an in-window error card with the message and a Retry button; the
  rest of the desktop stays alive. Verified live: issue #3 fired while #2's
  fix was in place and was cleanly contained to the Settings window.
- File: `ui_parts/app.html` (Safe).

### 3. HIGH — leaving Settings → Connectors crashed the Settings window
`GoogleAccountsPanel` used `useEffect(refresh,[])` where `refresh` is a
concise arrow returning the fetch Promise. React treats the effect's return
value as the cleanup function, so unmounting the panel called a Promise →
`TypeError: c is not a function` (surfaced when switching Connectors → About).
- Fix: `useEffect(()=>{refresh()},[])`. Audited the codebase for the same
  pattern; the one other bare-identifier effect (`useEffect(load,[])` in the
  settings hook) returns undefined and is safe.
- File: `ui_parts/app.html` (GoogleAccountsPanel).

### 4. MEDIUM — About tab: wrong version, three permanently-dead health cells
The header hard-coded `v4.5.0` on a v5.0 build, and Mood / Memory Entries /
Vault Entries always rendered "—" because `/api/health` never served the
`mood`, `memory_entries`, `vault_count` keys the panel reads.
- Fix (backend): `/api/health` now serves (fail-soft, em-dash on failure):
  - `version` — pyproject.toml first (editable-install egg-info can be
    stale; it reported 4.5.0), then package metadata, then a constant;
  - `mood` — from the emotional-arc engine;
  - `memory_entries` — count of `~/.friday/memory/**/*.json`;
  - `vault_count` — count of vault entries (same definition as
    `/api/vault/status`).
- Fix (frontend): About renders `v{h.version}` dynamically.
- Files: `src/agent_friday/routes/core_routes.py`, `ui_parts/app.html`.
- Verified: About now shows v5.0.0 · Mood "negative" · 26 memory entries ·
  3 vault entries.

### 5. MEDIUM — Settings → Voice: TTS voice list wrong for local engines
The "TTS Voice" dropdown always listed the 8 Gemini cloud voices, even with
the Local CPU engine selected — picking one was a silent no-op because the
local engines speak through Piper (`local_voice_tts_voice`).
- Fix: the row is engine-aware. Cloud/auto → "TTS Voice (Gemini)" saving
  `tts_voice`; local/local-gpu/auto → "Local TTS Voice (Piper)" saving
  `local_voice_tts_voice` (Amy / Lessac, the voices the engine can fetch).
- File: `ui_parts/app.html` (SettingsTabVoice).

### 6. LOW — stale voice thinking-traces polluting chat history
Two `friday` messages (12:32 today) contained raw model reasoning
("**Crafting a Direct Greeting** …"). The leak itself was already fixed in
code (the Live reader skips `part.thought` parts — every turn after the
13:02 server start is clean); these were residue rows rendering as normal
Friday messages.
- Fix: surgically removed the two polluted entries from
  `~/.friday/chat_history.json` (backup kept at
  `chat_history.json.bak-uitest`). No code change needed.

## Also changed
- `window.fridayDebugScene()` — read-only scene-state hook added to
  `ui_parts/styles_and_scene.html` (structures / transition / camera / mood).
  Used to diagnose the scene-transition investigation below; kept as a
  testing aid.
- `scripts/route_diff.py`, `scripts/route_diff2.py`,
  `scripts/jsx_undefined_check.py` — audit tooling used for this sweep
  (UI-vs-server route diff; undefined JSX tag scan).
- `index.html` rebuilt from `ui_parts/` (JSX precompiled).

## Verified working (no action needed)
- **Dock / workspaces:** all 19 open and render — Home, News, Messages,
  Calendar, Family, Health, Finance, Career, Contacts, Code, Sites, Draft,
  Content, Wiki, Trust, Studio, Marketplace, System, Settings.
- **News:** Front Page / Feed (28k stories) / Read Later / My Notes /
  Briefings (15) / Weekly / Editorial / Trust (96 sources); Manage Sources,
  Customize, Media Diet panels; headline → new tab; ⧉ Background →
  background tab; **Discuss with Friday → live voice session end-to-end**
  (source card, spoken reply, transcript, clean stop).
- **Chat:** send / response; empty send ignored gracefully; 1000+ char
  message renders wrapped and answered; history persists across refresh;
  New Chat clears via `/api/chat/clear`.
- **Model selectors:** top-bar panel (routing mode, orchestrator, subagent,
  creative, voice, local models) and Settings → Models both persist
  server-side (`orchestrator_model` + `capability_routing` stay in sync) —
  the recently-fixed save path holds.
- **Scene selector:** 13 scenes + "Reset to auto (evolution)", persisted via
  `/api/evolution`; structure morphs verified (see note below).
- **Settings:** General (name/personality persist after refresh — tested
  live), Privacy (vault card accurate: UNLOCKED / AES-256-GCM / 3 entries;
  egress Audit⇄Enforce toggle; erase guarded by typed confirmation),
  Federation (identity, capability chips, peer discovery, defederation
  form), Economy (post-fix), Orchestrator (Ollama green · 6 models; spawned
  a real worker → COMPLETED via OLLAMA adapter; cancel wired to
  `/api/orchestrator/cancel/<id>`), Connectors (Google accounts panel, MCP
  list, platform connector toggles), About (post-fix).
- **Voice:** Gemini Live end-to-end (speaks, transcribes both directions,
  barge-in config, clean stop); engine selector correctly greys out Local
  GPU (NeMo not installed); Local CPU stack reports ready
  (faster-whisper + piper + silero); audio device pickers live next to the
  chat mic button.
- **Bell:** task list with live status, QA verdict chips, View Results modal
  with full activity log; notifications with per-item dismiss + Clear All.
- **Process orbs:** none stale (`fridayGetOrbs() == []`); orb API present.
- **Error handling:** unknown workspace deep-links are ignored gracefully
  (no phantom window, no crash); API-failure paths render friendly text
  (empty states with guidance, not stack traces); workspace crashes now
  contained per-window (issue #2).
- **API:** all 12 endpoints from the test spec return 200 — with one
  correction: the front page lives at `/api/news/front-page/latest`
  (`/api/news/front-page` alone is not a route and never was). Route audit:
  all 258 `/api/*` paths referenced by the shipped UI match registered
  Flask routes.

## Notes / observations (not bugs)
- `/api/repos/scan` takes ~25 s for 58 repos; the Code workspace shows a
  scanning state the whole time. Works, just slow by nature.
- Scene structure transitions advance on `requestAnimationFrame`; when the
  browser window is fully hidden/occluded the browser throttles rAF and a
  mid-transition centerpiece can look empty until frames flow again. Not
  reproducible with the window visible. (Diagnosed with
  `fridayDebugScene()`; transition math is delta-based and self-heals.)
- Task cards can show "✓ COMPLETE" alongside a "FAIL" chip — lifecycle
  status + QA-gate verdict respectively. Reads oddly but is by design.
- `ui_parts/liquid_ui_panel.html` and `skills_observatory.html` are not part
  of the built index.html; the `/api/liquid/*` endpoints they reference do
  not exist server-side. Dead code to either ship or remove in a future pass.
- `routes/jobs.py` imports top-level `data`/`skills` packages, which resolve
  only when the repo root is on `sys.path` (true for `python server.py`, not
  for arbitrary embedders). Worth hardening when the frozen build is next
  touched.

## Test suite
Full offline suite (`pytest -q`) run after all changes:
**3672 passed, 3 skipped, 0 failed — exit code 0.**
