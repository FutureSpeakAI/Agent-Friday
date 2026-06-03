# API Reference

All endpoints are served by the Flask application in `server.py` at `http://localhost:3000` by default. Endpoints under `/api/` require authentication when `FRIDAY_PASSWORD` is set; loopback requests are auto-authenticated.

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
  "history": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ],
  "workspace": "garden:project-name",
  "model": "claude-sonnet-4-6"
}
```

**Response:**
```json
{
  "reply": "Here are today's events...",
  "tool_results": [...],
  "model_used": "claude-sonnet-4-6",
  "provider": "cloud",
  "routing": { "task_type": "tool_use", "reason": "..." }
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
System health check (uptime, API connectivity, memory, Ollama status, vault stats).

**Response:**
```json
{
  "status": "healthy",
  "uptime_seconds": 3600,
  "anthropic_connected": true,
  "gemini_connected": true,
  "ollama_available": false,
  "vault_stats": { "allowed": 42, "denied": 3 },
  "memory_mb": 256
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
Initiate a job application workflow.

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

### `POST /api/create/code-art`
Generate code art via Claude.

### `POST /api/create/poem`
Generate poetry via Claude.

### `POST /api/create/video`
Generate video content via Gemini.

---

## Voice

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

---

## Calendar & Countdowns

### `GET /api/calendar`
Get calendar events.

### `GET /api/countdowns`
Get active countdowns.

---

## Co-Parenting

### `POST /api/coparent/draft`
Draft a co-parenting communication (TIER_3 — vault-gated).

---

## Email

### `POST /api/email/draft`
Create an email draft.

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
