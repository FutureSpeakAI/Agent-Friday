# API Reference

All endpoints are served by the Flask application in `server.py` at `http://localhost:3000` by default. Endpoints under `/api/` require authentication when `FRIDAY_PASSWORD` is set; loopback (same-machine) requests are auto-authenticated by default. Set `FRIDAY_TRUST_LOOPBACK=0` to require login for loopback requests too. The `/ws/live` voice WebSocket can additionally require a shared token via `FRIDAY_WS_TOKEN` (passed as `?token=…`) regardless of loopback trust. See [Configuration → Authentication](CONFIGURATION.md#authentication) for details.

---

## Authentication

### `GET /login`
Renders the login page. Loopback requests are auto-authenticated and redirected to `/`.

### `POST /login`
Authenticates with username and password.

**Request (form):**
| Field | Type | Description |
|-------|------|-------------|
| `username` | string | Username (default: `admin`) |
| `password` | string | Password |

### `GET /logout`
Clears the session and redirects to `/login`.

---

## Chat

### `POST /api/chat`
Main chat endpoint. Sends a message through the full intelligence pipeline (context pruning, compression, model routing, vault gating, PII scrubbing, tool-use agent loop).

**Request:**
```json
{
  "message": "What's on my calendar today?",
  "workspace": "garden:project-name",
  "workspaceContext": null,
  "includeVision": false,
  "voice_mode": false,
  "cite_sources": false,
  "image": "<base64, optional>"
}
```

Chat history is kept server-side (there is no `history` field), and model selection is controlled by settings and the model router rather than a per-request `model` field.

**Response:**
```json
{
  "response": "Here are today's events...",
  "user_msg": { ... },
  "friday_msg": { ... },
  "sources": [],
  "tool_trace": [ ... ],
  "actions": [ { "type": "navigate", "workspace": "..." } ],
  "cite_sources": false,
  "session_id": "..."
}
```

### `GET /api/chat/history`
Returns the current session's chat history.

### `POST /api/chat/send`
Alternative chat send endpoint with extended options.

### `POST /api/chat/pin/<msg_id>`
Pin or unpin a specific message.

### `GET /api/chat/search`
Search chat history by keyword.

**Query params:** `q` (search string)

### `POST /api/chat/clear`
Clear the current chat session.

---

## Wiki

### `GET /api/wiki/<section>/<filename>`
Read a wiki page by section and filename.

**Response:**
```json
{
  "content": "# Page Title\n...",
  "path": "professional/job-search.md"
}
```

### `GET /api/wiki/structure`
Returns the full wiki directory tree.

### `POST /api/wiki/update`
Propose a wiki update (queued for approval).

**Request:**
```json
{
  "path": "professional/job-search.md",
  "content": "# Updated content...",
  "reason": "Added new job listing"
}
```

### `GET /api/wiki/pending`
List pending wiki update proposals.

### `POST /api/wiki/pending/<pid>/approve`
Approve a pending wiki update.

### `POST /api/wiki/pending/<pid>/reject`
Reject a pending wiki update.

### `PUT /api/wiki/edit`
Direct wiki edit (bypasses approval queue).

**Request:**
```json
{
  "path": "identity/about.md",
  "content": "# Updated content..."
}
```

### `DELETE /api/wiki/file`
Delete a wiki file.

**Request:**
```json
{ "path": "notes/old-draft.md" }
```

### `POST /api/wiki/search`
Search the wiki by keyword.

**Request:**
```json
{ "query": "job search", "limit": 5 }
```

### `POST /api/wiki/correct`
Submit a factual correction to a wiki page.

### `POST /api/wiki/setup-research`
Trigger an auto-research task to build/enrich a wiki section.

---

## Knowledge Graph

The two-tier knowledge graph over the wiki, SOUL.md, and memory
(`docs/KNOWLEDGE_SYSTEM_SPEC.md`). Tier A (structural) is always available
and LLM-free; Tier B (semantic) requires a reindex and defaults to
local-only models.

### `GET /api/knowledge-graph/summary`
Counts per artifact, community list, last index time, effective settings,
and whether the graph is dirty (wiki changed since last build).

### `GET /api/knowledge-graph/graph`
Nodes + edges + communities + layout for the 3D explorer. Positions
(`x`,`y`,`z`) are precomputed server-side — the client never runs a
simulation. Query params: `community` (filter to one community), `limit`
(node cap, default 2000; highest-degree nodes win, `truncated: true` when
applied).

### `GET /api/knowledge-graph/node/<id>`
One node with its relationships, neighbor nodes, provenance, and any
community reports covering it. Ids look like `page:research/graphrag` or
`ent_<hash>`.

### `GET /api/knowledge-graph/neighbors/<id>?depth=1..3`
Ego-graph for expand-on-click.

### `POST /api/knowledge-graph/query`
`{"question": "...", "mode"?: "structural"|"local"|"global"|"drift"}` —
auto-routed retrieval. Structural answers (candidates, BFS paths,
`should_read` shortlist) are always included; Tier B modes add an LLM
answer with provenance when the semantic index exists.

### `POST /api/knowledge-graph/reindex`
`{"tier": "A"|"B", "mode"?: "delta"|"full", "sync"?: bool}` — Tier A is
synchronous (milliseconds); Tier B runs in a background thread (returns
`{"status": "started"}`) unless `sync` is set. `409` if a Tier B pass is
already running.

### `GET /api/knowledge-graph/reindex/status`
`{"running": bool, "last": {...}}` for the Tier B background pass.

### `GET /api/knowledge-graph/search?q=`
Fast title/description substring search for the explorer's search box.

### `GET /api/knowledge-graph/events`
Server-sent events: `node_ignited` (a newly learned fact lit up),
`reindexed`, `progress`.

---

## Context & Compression

### `POST /api/context/search`
Search the full context log (append-only JSONL).

**Request:**
```json
{ "query": "vault", "limit": 50 }
```

### `GET /api/context/stats`
Returns context logging statistics (event counts, date range, file sizes).

### `GET /api/compression-stats`
Returns Headroom compression statistics.

**Response:**
```json
{
  "calls": 42,
  "tokens_saved": 128000,
  "tokens_before": 200000,
  "tokens_after": 72000,
  "compression_ratio": 0.64,
  "last_ratio": 0.71,
  "enabled": true,
  "available": true
}
```

### `DELETE /api/context/range`
Delete context log entries within a date range.

### `POST /api/context/pause`
Pause context logging.

### `POST /api/context/resume`
Resume context logging.

### `GET /api/context/export`
Export context logs as a downloadable archive.

---

## Model & Routing

### `GET /api/model-stats`
Returns model routing statistics (local vs cloud requests, token counts, cost, savings).

### `GET /api/ollama/status`
Check Ollama availability and connection status.

**Response:**
```json
{
  "available": true,
  "url": "http://localhost:11434",
  "models": [ ... ],
  "model_count": 3,
  "hardware": { "gpu": "NVIDIA RTX 4090", "vram_gb": 24.0, "ram_gb": 64.0 }
}
```

### `GET /api/ollama/models`
List installed Ollama models with sizes and recommendations.

### `POST /api/ollama/pull`
Pull (download) a new Ollama model.

**Request:**
```json
{ "model": "qwen3:14b" }
```

---

## Providers & Model Catalog

The provider layer (v5.1) is model-agnostic: OpenRouter and OpenAI-compatible providers can be registered, keyed, tested, and browsed at runtime. These endpoints back the Settings provider step, the Add Provider form, and the model picker.

### `GET /api/providers`
List all registered providers, enriched with availability, live health stats (latency/error-rate), catalog size, spend today, and origin.

### `POST /api/providers`
Register a new provider from a descriptor.

### `PATCH /api/providers/<name>`
Partial descriptor update (enable/disable, base URL, budget, …). Persists as a user override.

### `DELETE /api/providers/<name>`
Remove a user-added provider. A customized built-in provider reverts to its shipped default instead.

### `POST /api/providers/<name>/key` · `DELETE /api/providers/<name>/key`
Store (POST) or remove (DELETE) a provider's API key. Keys are encrypted at rest via the credential store and hot-reloaded into the running process.

### `POST /api/providers/<name>/test`
Test Connection: a deep, adapter-aware probe returning latency and the number of models the endpoint reports.

### `POST /api/providers/<name>/reload-key`
Re-read the stored key from the credential store (or env) and reinitialize the provider client without re-submitting the key.

### `POST /api/providers/<name>/models/refresh`
Force model discovery for one provider immediately (the ⟳ Refresh list button).

### `GET /api/providers/health`
Per-provider reachability/auth status. Shallow by default (offline-safe); pass `?deep=1` for a light endpoint probe.

### `GET /api/providers/templates`
Provider templates for the Add Provider form.

### `POST /api/providers/validate`
Dry-run descriptor validation for the Add Provider form — no write.

### `GET /api/models`
The available-model catalog grouped by UI role (orchestrator / subagent / creative / voice). Single source of truth for every model selector in the UI. Role lists are curated — only descriptor-declared static models plus live Ollama models; the discovery long tail (e.g. OpenRouter's 300+) appears only in the flat `models` list and via search. Each entry carries availability plus a boolean `curated` flag. Also returns `voice_engines` and the currently `selected` models, including `creative_video_model` (from `capability_routing.creative_video` — video has no flat `*_model` key).

### `GET /api/models/search`
Search models across all registered providers (static + discovery caches). Powers the Model Browser. Result rows include `modalities` (list) and `local` (bool); static rows resolve label and modalities from provider `model_meta`.

**Query params:** `q` (substring on id/label), `provider`, `free=1`, `tools=1`, `local=1` (on-device providers only), `max_price_in` (USD per 1M tokens), `min_context`, `modality` (exact member of the modalities list — one of `vision`/`image`/`video`), `sort` (`price` | `price_desc` | `context`; applied before the result limit, with unpriced entries sorted last on price sorts). Ollama-backed providers list their live installed models when the daemon is running.

---

## Settings & Setup

### `GET /api/settings`
Returns current settings.

### `POST /api/settings`
Update settings. Accepts a JSON object with any settings keys.

### `GET /api/setup/status`
Check whether first-run setup has been completed.

### `GET /api/setup/skip`
### `POST /api/setup/skip`
Skip the setup wizard.

### `POST /api/setup/complete`
Complete the setup wizard with initial configuration.

---

## Skills

### `GET /api/skills`
List all skills (learned, imported, and bundled) from the skill registry.

### `POST /api/skills/import`
Import a portable skill. Accepts either a multipart `file` upload (a `.zip`) or a JSON body pointing at a folder, zip, or legacy `.yaml`.

**Request (JSON):**
```json
{ "path": "C:\\path\\to\\skill-folder", "name": "meeting-prep" }
```

### `GET /api/skills/<name>/export`
Download a skill as a portable `.zip`.

### `GET /api/skillopt/state`
Returns the SkillOpt fleet state (JSON snapshot used by the Observatory UI).

---

## Personality & Trust

### `GET /api/personality`
Returns personality state (maturity, traits, evolution stage).

### `POST /api/personality/set`
Update personality parameters.

### `GET /api/trust`
Returns the full trust graph.

### `POST /api/trust/edit`
Edit an existing trust graph entry.

### `POST /api/trust/add-person`
Add a new person to the trust graph.

### `GET /api/epistemic`
Returns epistemic calibration scores.

---

## Health & System

### `GET /api/health`
System health check (uptime, version, active models, vault encryption state, and governance/ring info).

**Response:**
```json
{
  "status": "ok",
  "version": "5.2.0",
  "mood": "...",
  "memory_entries": 128,
  "vault_count": 42,
  "uptime_seconds": 3600,
  "server_start": "2026-07-04T08:00:00",
  "creations_today": 2,
  "models": [ { "name": "...", "active": true } ],
  "agent_name": "AGENT FRIDAY",
  "orchestrator_model": "...",
  "subagent_model": "...",
  "creative_model": "...",
  "voice_model": "...",
  "vault": { "encryption_enabled": true, "warning": "" },
  "governance": { "enabled": true, "policy": "cLaws", "ring_permissions": { ... }, "tool_counts_by_ring": { ... } }
}
```

### `GET /api/system`
Extended system information.

### `GET /api/memory/stats`
Memory system statistics.

---

## Evolution & Briefings

### `GET /api/evolution`
### `POST /api/evolution`
Get or update the personality evolution state.

### `GET /api/briefings`
List available daily briefings.

### `GET /api/briefing/<filename>`
### `GET /briefing/<filename>`
Read a specific briefing file.

---

## Career Operations

### `GET /api/career-ops/tracker`
Job application tracker data.

### `GET /api/career-ops/pipeline`
Career pipeline status.

### `GET /api/career-ops/reports`
List career operation reports.

### `GET /api/career-ops/report/<filename>`
Read a specific career report.

### `GET /api/jobs`
List tracked job opportunities.

### `POST /api/jobs/apply`
Placeholder — returns a stub response (`{ "status": "placeholder", "message": "Would apply to: ..." }`); no application workflow is executed yet.

---

## Finance & Health (Vault-Protected)

These endpoints serve TIER_2/TIER_3 vault-protected data. Content is gated by the vault access control system.

### `GET /api/finance/portfolio`
Portfolio overview (TIER_3).

### `GET /api/finance/perks`
Financial perks and benefits (TIER_3).

### `GET /api/finance/contacts`
Financial contacts (TIER_3).

### `GET /api/finance/quickref`
Financial quick reference (TIER_3).

### `GET /api/health/medications`
Medication list (TIER_3).

### `GET /api/health/appointments`
Upcoming appointments (TIER_3).

### `GET /api/health/insurance`
Insurance information (TIER_3).

### `GET /api/health/vehicles`
Vehicle records (TIER_2).

---

## Creative Tools

### `POST /api/create/image`
Generate an image via Gemini.

**Request:**
```json
{ "prompt": "A cyberpunk cityscape at sunset" }
```

### `POST /api/create/music`
Generate music via Gemini.

### `GET /api/create/music/available`
Report whether cloud music generation (Lyria batch API) is available. Returns `{ "available": bool, "reason": "..." }`; the UI uses this to badge the music button.

### `POST /api/create/code-art`
Generate code art via Claude.

### `POST /api/create/poem`
Generate poetry via Claude.

### `POST /api/create/video`
Generate video content via Gemini.

### `POST /api/create/presentation`
Generate a self-contained HTML slide deck (LLM outline rendered into a fixed template).

### `POST /api/create/website`
Generate a self-contained hash-routed website (LLM spec rendered into a fixed template).

### `POST /api/create/timeline`
Assemble clips + music into an exported production (timeline/video assembly). Accepts either a full `{ "timeline": { ... } }` contract or the simpler `{ "clips": [...], "music": ..., "transition": ..., "title": ..., "exports": ... }` shorthand. Alias of `POST /api/creations/compose`.

---

## Voice

### `GET /api/voice/session-info`
Engine selection for the mic button. Returns `{ "status": "ok", "engine": "...", "ws_url": "...", ... }`; clients connect to the returned `ws_url` — `/ws/voice-local` (local, default) or `/ws/live` (cloud opt-in).

### `WS /ws/voice-local`
Local Tier-1/Tier-2 voice WebSocket (faster-whisper/NeMo + Piper). Mirrors the `/ws/live` audio plumbing and event contract, so clients can switch engines by URL alone.

### `GET /api/voice/fallback-status`
Report which voice capabilities are available given the current network state.

### `GET /api/voice/setup/status`
Voice setup readiness for the first-run wizard. Returns `{ "ready": bool, "engine": "...", "steps": [ { "id": "deps"|"models"|"mic"|"key", "status": "...", "detail": "..." } ] }`.

### `POST /api/voice/setup/test`
Run a voice setup test.

### `GET /api/voice/start-my-day`
Assemble the sequential morning voice briefing (calendar → email → news → tasks).

### `POST /api/voice/tts`
Text-to-speech via Gemini.

**Request:**
```json
{
  "text": "Good morning, boss.",
  "voice": "Kore"
}
```

### `GET /api/audio/<filename>`
Serve a cached audio file.

---

## Vibe Code (Coding Terminal)

### `POST /api/vibe-code/launch`
Launch a coding terminal session.

**Request:**
```json
{ "task": "Build a React dashboard", "cwd": "C:\\Projects\\app" }
```

### `GET /api/vibe-code/status`
Get status of all vibe code terminals.

### `POST /api/vibe-code/stop`
Stop a running vibe code terminal.

### `POST /api/vibe-code/clear`
Clear completed terminals.

### `GET /api/vibe-code/presets`
List available vibe code presets.

---

## Content Pipeline (v2)

Social publishing routes (Blueprint `content_pipeline_bp`, `routes/content_pipeline.py`) — see `docs/CONTENT_PIPELINE_SPEC.md` §11 for the full design. All responses are JSON envelopes (`{ "ok": ... }` or `{ "status": ... }`); errors are reported in-body with HTTP 200 (creations-route convention). The legacy ideation-kanban routes (`/api/content/pipeline`, `/api/content/idea`, `/api/content/draft`, `/api/content/templates`, `/api/content/from-template`, `/api/content/item`, `/api/content/drafts` in `routes/workflows.py`) remain untouched and back the Ideas tab.

### Posts

### `GET /api/content/posts`
List posts. Query filters: `status`, `platform`, `from`, `to`, `source`.

### `POST /api/content/posts`
Create a draft `ContentPost`.

**Request:**
```json
{
  "title": "Launch day",
  "body": "canonical markdown body",
  "assets": [ { "filename": "friday-image-abc.png", "content_hash": "sha256:...", "kind": "image", "alt_text": "..." } ],
  "platforms": ["linkedin", "bluesky"],
  "schedule": { "publish_at": null, "timezone": "America/Chicago", "optimal_time": true },
  "source": { "kind": "creation", "ref": "friday-image-abc.png" }
}
```

### `GET /api/content/posts/<id>`
### `PATCH /api/content/posts/<id>`
### `DELETE /api/content/posts/<id>`
Read / edit / delete a post. Delete marks the post `CANCELLED` and optionally issues best-effort platform takedowns for already-published targets.

### `POST /api/content/posts/<id>/compose`
Run the composition engine, producing one `PlatformTarget` per platform.

**Request:**
```json
{ "platforms": ["linkedin"], "regenerate": false }
```

Skips `CONFIRMED` targets; `regenerate: true` rebuilds `PENDING`/`HELD` ones.

### `POST /api/content/posts/<id>/schedule`
Set/replace the post's `ScheduleConfig`. Resolves `optimal_time` (seed tables → learned best-times histogram) and returns same-platform conflict warnings.

### `POST /api/content/posts/<id>/publish-now`
Immediate dispatch. Still fully gated: moderation harm scan (H1–H4) and egress-gate sensitivity classification run before any adapter is called.

### `POST /api/content/posts/<id>/cancel`
Cancel the whole post, or one target with `{ "target_id": "tgt_..." }`.

### `POST /api/content/posts/<id>/release`
Release a `HELD` target after human review.

**Request:**
```json
{ "target_id": "tgt_...", "ack": true }
```

### Composition & repurposing

### `POST /api/content/preview`
Stateless per-platform adaptation for the Compose tab's live preview. Returns `{ ok, adapted_body, segments, hashtags, char_used, char_limit, asset_plan, warnings }` — the preview is the exact payload that would ship.

### `POST /api/content/repurpose`
One source → a spread of platform-native drafts (blog → thread + LinkedIn + captions + federation listing, video → Short/reel/TikTok cuts, etc.).

**Request:**
```json
{ "source": { "kind": "creation", "ref": "friday-video-xyz.mp4" }, "spread": "default" }
```

### Queue & calendar

### `GET /api/content/queue`
Upcoming targets (joined to posts, ordered by `publish_at`) + pinned `HELD` section + recent history from `publish_log.jsonl`.

### `GET /api/content/calendar`
`?from=&to=` → scheduled-post pills plus suggested optimal slots (labeled "learned" vs "general best practice").

### `GET /api/content/best-times`
Seed + learned best-time tables (`?platform=` to filter).

### Analytics

### `GET /api/content/analytics/summary`
Dashboard rollup (`?days=30`): impressions, engagement rate, best post, ψ earned from content.

### `GET /api/content/analytics/post/<id>`
Per-post drilldown: snapshot time-series and per-platform metric table. Metrics a platform cannot report are absent (rendered "—"), never zero.

### `POST /api/content/analytics/refresh/<target_id>`
Manual metrics poll for one target (respects the adapter's local rate budget).

### `GET /api/content/insights`
Current insight cards from the weekly `content_insights` job (attribute lift, learned best times, variant verdicts — each with honest sample sizes).

### Platform accounts

### `GET /api/content/platforms`
Adapter registry + per-platform `status()` envelope (`{ connected, account, scopes, expires_at, tier, last_error }`). Never contains token material.

### `POST /api/content/platforms/<name>/connect`
Begin auth. Returns a `connect_url` (OAuth platforms), a token-paste descriptor (Bluesky app password, Mastodon token), or a manual-mode descriptor (Substack/Medium handoff).

### `GET /api/content/platforms/<name>/callback`
OAuth loopback redirect target (`http://localhost:3000/...`, PKCE where supported, `state` bound + single-use). Tokens are encrypted into the credential store; never written to disk in plaintext.

### `DELETE /api/content/platforms/<name>`
Disconnect: platform-side token revoke where the API supports it + local credential purge. `?purge_analytics=1` also removes that platform's snapshots and targets.

### `POST /api/content/platforms/<name>/test`
Connection test (private/self post where the platform allows it).

### Voice cards & export

### `GET /api/content/voice-cards/<platform>`
### `POST /api/content/voice-cards/<platform>`
Read / edit the per-platform voice card (`~/.friday/content/voice_cards/<platform>.md`) folded into every composition prompt alongside SOUL.md.

### `GET /api/content/export`
Full local data export: posts, targets, engagement snapshots, best-times tables, and the publish log as one JSON bundle.

> Chat/agent tool equivalents (`content_create_post`, `content_schedule_post`, `content_post_status`, `content_repurpose`) are Ring 2 governance-gated wrappers over the same service calls, so voice and chat drive the pipeline with no additional privilege surface.

---

## Notifications

### `GET /api/notifications`
List notifications.

### `POST /api/notifications/read`
Mark notifications as read.

### `POST /api/notifications/dismiss`
Dismiss notifications.

### `POST /api/notifications/push`
Push a new notification.

### `GET /api/notifications/chat-injections`
Get pending chat injection notifications.

### `POST /api/notifications/chat-injections/ack`
Acknowledge a chat injection.

---

## Tasks & Processes

### `GET /api/tasks`
List active tasks.

### `GET /api/tasks/<task_id>`
Get a specific task.

### `DELETE /api/tasks/<task_id>`
Delete a task.

### `POST /api/agent/steer`
Steer an active agent task.

### `GET /api/processes`
List active background processes.

---

## Contacts & Outreach

### `GET /api/contacts`
List contacts.

### `GET /api/contacts/<name>`
Get a specific contact.

### `POST /api/contacts/research`
Research a contact for meeting prep.

### `GET /api/outreach/suggestions`
Get outreach suggestions.

### `POST /api/outreach/draft`
Draft an outreach message.

### `POST /api/outreach/log`
Log an outreach interaction.

### `GET /api/outreach/pipeline`
View the outreach pipeline.

---

## Content & Drafting

### `POST /api/draft`
Create a content draft.

### `POST /api/draft/deploy`
Deploy a draft to its target.

### `GET /api/content/drafts`
List saved drafts.

### `GET /api/content/drafts/<filename>`
Read a specific draft.

### `GET /api/content/pipeline`
Content pipeline status.

### `POST /api/content/idea`
Submit a content idea.

### `POST /api/content/draft`
Create a new content draft.

---

## Flow Engine

### `POST /api/flow`
Execute a multi-step flow.

### `GET /api/flow/queue`
Get the flow execution queue.

### `POST /api/calendar/enrich`
Enrich a calendar event with context.

### `POST /api/flow/draft/confirm`
Confirm a flow-generated draft.

---

## Routines & Todos

### `GET /api/routines`
List configured routines.

### `POST /api/routines/<routine_id>/run`
Manually trigger a routine.

### `GET /api/todos`
List todos.

### `POST /api/todos`
Create a todo.

### `POST /api/todos/<todo_id>/approve`
Approve a proposed todo.

### `POST /api/todos/<todo_id>/reject`
Reject a proposed todo.

### `POST /api/todos/<todo_id>/complete`
Mark a todo complete.

### `DELETE /api/todos/<todo_id>`
Delete a todo.

---

## FutureSpeak Business

### `GET /api/futurespeak/pipeline`
FutureSpeak business pipeline.

### `GET /api/futurespeak/revenue`
Revenue tracking.

### `GET /api/futurespeak/legal`
Legal status.

### `GET /api/futurespeak/assets`
Business assets.

---

## Analysis

### `POST /api/analyze`
Run an analysis task (document, data, comparison).

---

## Creations

### `GET /api/creations`
List files in the creations directory (`~/Desktop/friday-creations/`).

### `GET /api/creations/<filename>`
Serve a specific creation file.

### `POST /api/creations/generate`
Unified generation endpoint for the Studio Generate panel — generates an image, or a video when `{ "kind": "video" }` is supplied. Always returns HTTP 200; success or failure is reported in the body's `status` field (`ok`|`blocked`|`unavailable`|`error`). Alias of `POST /api/create/image`.

### `POST /api/creations/compose`
Assemble clips + music into an exported production. Alias of `POST /api/create/timeline` (see Creative Tools).

### `GET /api/creations/daily`
List all daily creations (date, title, type, mood), newest first.

### `GET /api/creations/daily/latest`
The most recent daily creation (full record).

### `GET /api/creations/daily/<date>`
A specific daily creation by `YYYY-MM-DD`.

### `POST /api/creations/daily/run`
Generate today's creation on demand. Pass `?force=1` to regenerate if it already exists.

---

## Calendar & Countdowns

### `GET /api/calendar`
Get calendar events.

### `GET /api/countdowns`
Get active countdowns.

---

## Email

### `POST /api/email/draft`
Placeholder — returns a stub response (`{ "status": "placeholder", ... }`); no draft is created. Real email drafting is exposed via `POST /api/messages/draft` and the outreach draft endpoints.

---

## Control

### `GET /api/control/permission`
### `POST /api/control/permission`
Get or set computer control permissions (Ring 3).

### `POST /api/control/kill`
Kill a running process.

---

## Static / PWA

### `GET /`
Serves the main UI (`index.html`).

### `GET /friday-live` · `GET /friday-live/`
Serves the live holographic UI.

### `GET /friday-live/manifest.json`
PWA manifest.

### `GET /friday-live/sw.js`
Service worker.

### `GET /static/<filename>`
Static file serving.

### `GET /favicon.ico`
Favicon.
