# Agent Friday — The Action & Creation Layer: Technical Specification

**Spec stage:** Opus 4.8 — codebase-grounded design (every claim verified in-tree, 2026-07-08).
**Build stage:** Fable 5 — one build session per phase, **after the July 9 demo freeze lifts**.
**Status:** Draft for Stephen's review. **SPEC ONLY — no implementation in this session, no app code touched, nothing committed.** Sections marked ⚠️ need a decision before their phase starts. Open questions in §9.

> **Relationship to `docs/V6_WHOLENESS_SPEC.md`.** This is the *actuator-and-creative-engine detail* under V6's umbrella — the concrete "she can act and create" layer that V6 §1.1 frames as durable-agency actuation plus (deferred-as-product but present-as-infrastructure) the creative engines. Where V6 defines the general machinery — the durable **Goal** entity (V6 P5), the general **approval queue** `services/approvals.py` (V6 P5), per-app **actuation permission tiers** and screen-trust (V6 P6), the **self-heal Doctor** (V6 P7) — this spec **reuses and references** it rather than duplicating. The one place the two overlap is the human-gate: this spec ships a *thin per-action consent gate* (a subset of V6 P5's approval queue) so the Google pillar can land before all of V6 P5; §3.3 states that overlap explicitly so P5 generalizes it rather than colliding with it.

---

## 1. Executive Summary

Friday already has the *engines* and the *safety backbone*. What she lacks is the **connective tissue between the two** — the wiring that lets her invoke her own creative and outward capabilities **mid-conversation and mid-voice-call**, every invocation signed, receipted, transparent, and (for the one pillar that touches the network) gated and consented.

Four pillars, all facets of one thing — **Friday's ability to act and create**:

1. **Conversational creative generation.** The six creative chat tools (`generate_image`, `generate_video`, `generate_music`, `compose_timeline`, `create_presentation`, `create_website`) **already exist and are registered** in `services/agent.py` (verified — §4.1). The real gap is narrower and honest: **voice exposes only four tools** (`query_calendar`, `search_news`, `open_url`, `navigate_workspace`), so Friday cannot create anything by voice; and no creative invocation yet leaves a work-log receipt or a transparency orb. This pillar closes the voice gap and wraps every creation in the shared Action Envelope (§3).
2. **Multimodal daily creations.** The daily loop **already** reaches image/music/short-production engines (verified — `services/creations.py`; the "writing + code-art only" premise is out of date). The real gaps: media modes are budget-gated and easy to miss; the surfacing message is a **hardcoded template, not a personality-generated comment**; and the proactive-chat consumer may not be wired in the shipped UI. This pillar makes media first-class and makes Friday genuinely *come alive* around what she made.
3. **Google agentic actions by voice.** Friday has **her own** Google connector — the encrypted multi-account OAuth path in `services/google_accounts.py` — and it is **broken only because no token was ever minted** (no `google_token.json`, empty `~/.gmail-mcp/accounts/*`). This pillar **repairs her own auth and makes re-auth near-one-click**, and — per the honesty constraint — **never transplants any other app's or orchestrator's OAuth tokens** (that is an OAuth-boundary violation and a security anti-pattern). Then it wires read+write email/calendar/contacts actions into the **announce→act→confirm** voice choreography the voice session just built. This is the only pillar with real outbound network, so it routes through the fail-closed egress gate with **explicit per-action consent**.
4. **OfficeCLI integration.** A local, no-cloud, no-MS-Office document engine. **Findings correct a premise: OfficeCLI is .NET/C#, not Go** — but it is still a single self-contained binary, Apache-2.0, with a Windows release binary and a **built-in stdio MCP server** that Friday's stdio `mcp_client` can consume directly. This pillar exposes Word/Excel/PowerPoint create+edit as agent tools, fully local and sovereign.

**Cross-cutting (all four):** every action is cLaws-gated and permission-tiered (observe/act as a *grant*, never a standing capability); every creation and action is **signed (Proof of Integrity)** and **logged as a receipt**; every subagent task is **transparent/explorable** (the click-shows-the-real-process work); all reuse **announce→act→confirm**; all honor **fail-closed egress** (the three local pillars stay offline; only the Google pillar opens a gated, consented path).

---

## 2. Grounded Substrate — What Already Exists (verified in-tree, 2026-07-08)

The contract is "reuse, don't rebuild." Every path below was verified against the working tree.

| Area | Exists today | Key files (paths + symbols) | The gap this spec fills |
|---|---|---|---|
| **Creative engines** | Image/video (Imagen/Nano-Banana/Veo/Omni), music (Lyria 3, demo-mode), FFmpeg timeline, showcase decks/sites | `services/creative_engine.py` (`generate_image` L559, `generate_video` L699, `generate` dispatcher L1081, friendly→API maps L64-102, `check_content_safety` L244), `services/music_engine.py` (`generate_music` L210, `cloud_music_available` L189), `services/timeline_engine.py` (`compose` L369), `services/showcase_engine.py` (`generate_presentation` L128, `generate_website` L306) | Voice can't reach them; no work-log receipt / transparency orb per invocation |
| **Creative chat tools** | All six registered + dispatched | `services/agent.py` — schemas L1910-2044, handlers `_tool_generate_image` L2047 … `_tool_create_website` L2141, registered in `CLAUDE_TOOL_HANDLERS` L2234-2239; central dispatch `_execute_tool` L3714; unified loop `_oai_agentic_loop` L4478 | Not in the voice tool set (§4.1); no receipt/transparency wrapper |
| **Voice tool surface** | Curated 4-tool live set + choreography | `services/voice_engine.py` (`_VOICE_LIVE_TOOLS`, `_build_voice_live_tools` L273, `_voice_tool_run` L301), `routes/voice.py` (`VOICE_TOOL_CHOREOGRAPHY` L362, injected L900/L1211; ask-first policy L1234-1244) | Only `query_calendar`/`search_news`/`open_url`/`navigate_workspace`; no create/compose/send |
| **Daily creation loop** | Free-choice across media incl. `image`/`music-clip`/`short-production` | `services/creations.py` (`generate_daily_creation` L463, `DAILY_MODES` L290-303, `_MEDIA_DAILY_MODES` L283, `_generate_media_daily` L425, `_choose_daily_mode` L343); registered builtin `scheduler.py` L677 (08:00 Central) | Media budget-gated/skippable; surfacing message hardcoded, not persona-generated; proactive-chat consumer possibly unwired |
| **Creation storage + gallery** | Date-keyed JSON + materialized gallery files; polling UI | `core/__init__.py` (`DAILY_CREATIONS_DIR = ~/.friday/creations` L585, `CREATIONS_DIR = ~/Desktop/friday-creations` L584), `routes/creations.py` (`/api/creations*`, `/api/create/*`, `/api/creations/daily/*`), `index.html` (`loadCreations` L12906) | — (reuse as-is) |
| **Proactive surfacing** | Notification queue + proactive-chat injection | `notifications_engine.py` (`push(...)` L92, `proactive_chat=True`, `pending_chat_injections` L295, `ack_chat_injection` L314, `upsert_status` L150), `routes/notifications.py` (`/api/notifications/chat-injections` L105) | Consumer poller not confirmed in `index.html`; surfacing copy is templated |
| **Friday's own Google (built-in)** | Encrypted multi-account OAuth; read fetchers; connect endpoints | `services/google_accounts.py` (scopes L44-52 = Gmail-read + Calendar-RW + Drive-read, `build_auth_flow` L585, `upsert_account` L165, `merged_gmail` L407, `merged_calendar` L478, `credentials_for` L300), `services/calendar_engine.py` (`_google_client_config` L131, `_google_credentials` L198, token path L87), `routes/google_accounts.py` (`/api/google/accounts/connect` L84, callback L121), `routes/google.py` (legacy `/api/google/auth`) | **No token minted** (broken); **no one-click UI**; no send/compose scope; no Contacts |
| **Google chat tools** | Rewired to built-in path; graceful when unconnected | `services/agent.py` (`_GOOGLE_NOT_CONNECTED_NOTE` L603, `_tool_query_calendar` L612, `_tool_search_email` L648, `_tool_draft_email`) | Read-only; no send/create-event/contacts; not in voice set |
| **Native MCP client (stdio)** | Working; registers `mcp_<server>_<tool>` Ring-2 | `mcp_client.py` (`MCPManager` L365, `load_config` L373, `start_all` L407, `call` L447; **stdio JSON-RPC only**), `services/agent.py` (`_mcp_register_server_tools` L4043, `TOOL_RINGS[full]=2` L4068, `_mcp_boot` L4097, config `~/.friday/mcp_servers.json`) | No Office tools registered; SSE transport absent (not needed for OfficeCLI) |
| **Egress gate (fail-closed)** | `seal_outbound` + `gate_text`; 4-layer classifier; startup self-test; offline overlay | `services/egress_gate.py` (`seal_outbound` L421, `gate_text` L404, `gate_operational` L522, `startup_self_test` L467), `services/sensitivity_classifier.py` (`classify` L322, tiers L32-36), `services/model_router.py` (`_seal_or_block` L88, called L156/L661), `core/__init__.py` (offline overlay `_apply_offline_routing_overlay` L1732) | — (reuse; extend adversarial suite for Google pillar) |
| **cLaws + signing + receipts** | HMAC-signed cLaws; Ed25519 identity; C2PA provenance; work-log | `governance/proof_of_integrity.py` (`CLAWS_TEXT` L40, `IntegrityEngine` L113, `sign_payload` L160, `get_public_key_hex` L153), `services/provenance.py` (`write` L317, C2PA manifest L185, ledger `~/.friday/provenance/ledger.jsonl`), `services/work_log.py` (`log_start` L97, `log_finish` L148, `~/.friday/work_log.db`) | Creative gen signs C2PA already; **no work-log receipt** on creative/Google/office actions; no unified "Action Envelope" |
| **Transparency (click→real process)** | Monitoring orbs survive purge; detail renders result | `core/__init__.py` (`process_register` L1081 w/ `category="monitoring"`+`model=`, `process_update(result=)` L1109, `process_log` L1139), `routes/tasks.py` (`/api/tasks/<id>` L89 status-map L124, `/api/processes` L171 monitoring-survives-900s L186), `tests/api/test_process_transparency.py` | New actions must adopt the `monitoring` + `model` + `intent:` + `result` contract |
| **Announce→act→confirm** | Injected into both live + local voice prompts | `routes/voice.py` (`VOICE_TOOL_CHOREOGRAPHY` L362, ask-first L1234), tool results egress-gated L1621/L1720 | Only the 4 read/navigate tools obey it; create/send tools don't exist |

---

## 3. Cross-Cutting Contract — the backbone every new action reuses

Every new tool this spec adds — creative, Google, or Office — MUST route through the same six-part backbone. This is not new machinery; it is the composition of existing primitives into one **Action Envelope** helper (`services/action_envelope.py`, introduced in Phase 1, §7) that wraps a tool handler.

### 3.1 The Action Envelope (thin wrapper, built once, reused everywhere)

```
result = with_action_envelope(
    kind="creation" | "google" | "office",
    intent=<one-line human string>,      # "Compose email to Libby about pickup"
    tier="observe" | "act",              # act = state-changing / outward
    provider=<provider or "local">,
    consent=<ConsentDecision or None>,   # required when tier=="act" and outward
    run=<callable that performs the work>,
)
```

It performs, in order:
1. **cLaws / content floor** — for creative, the existing `check_content_safety` / `check_music_safety` (harm floor). For all, the signed-cLaws manifest is already injected into the system prompt (`_settings_system_prefix`); no per-call boolean gate is invented.
2. **Permission tier / consent** — `observe` (read/announce/local-artifact) proceeds; `act` (state-changing or outward) requires a fresh **per-action grant** (§3.3), never a standing capability.
3. **Transparency orb** — `core.process_register(pid, category="monitoring", model=<model>, icon=<kind icon>)`, then `process_log(pid, f"intent: {intent}")`, updated on completion with the real `result=`. Guarantees the click-through panel shows model + intent + result and survives the 900s monitoring purge window (`routes/tasks.py` L186).
4. **Egress** — for any cloud LLM/generation body, `egress_gate.gate_text(...)` / `model_router._seal_or_block(...)` (fail-closed). Local providers bypass. Google *service* API calls are gated by OAuth scope, not the content gate (§3.2).
5. **Signing** — artifacts → `provenance.write(path, tool_chain=..., sources=..., license=...)` (C2PA + Ed25519, already wired for image/video/timeline; extend to voice-invoked and office artifacts). The Ed25519 identity is `IntegrityEngine.get_public_key_hex()`.
6. **Receipt** — `work_log.log_start(task)` before, `work_log.log_finish(task, result)` after (`~/.friday/work_log.db`), carrying `goal_ancestry` when the action descends from a V6 goal.

### 3.2 Egress posture per pillar (fail-closed)

- **Creative (Pillar 1/2):** prompts to Gemini already pass `egress_gate.gate_text(prompt, "gemini", ...)` (`creative_engine.py` L596). No user artifact is uploaded; only the prompt leaves. Music/timeline/showcase are local composition. Local classification never sends content to the cloud to classify itself.
- **Daily creations (Pillar 2):** same as creative; the *surfacing comment* is generated locally-or-routed like any chat turn through `_get_friday_system_prompt` and `_seal_or_block`.
- **Google (Pillar 3):** two sub-paths. (a) Any *free-text* that becomes a Gemini/LLM body (e.g. "draft this email") passes the content egress gate. (b) The *authorized service call* (Gmail send, Calendar insert, People read) leaves via Google's SDK on the user's own OAuth token — gated by **scope consent + per-action user grant**, not the content classifier (the payload is a user-authorized action, not free-text egress). Both are logged to `egress-log.jsonl` / `work_log`.
- **Office (Pillar 4):** fully local. Caveat (§6): OfficeCLI runs as a subprocess, so *its own* network calls (update check, remote image fetch) bypass Friday's Python egress gate; mitigated by env flags + local-file-only inputs (§6.3).

### 3.3 Permission tiers & the thin consent gate (overlap with V6 P5 stated)

Two tiers, matching V6 §6 invariant 11's observe/act framing:
- **observe** — read, announce, or produce a *local* artifact. Default-allowed. (Reading calendar, generating an image to the local gallery.)
- **act** — anything **outward** (leaves the machine on the user's behalf: send email, create a shared calendar event) or **irreversible**. Requires a **fresh per-action grant**.

The grant mechanism reuses the voice **ASK-first** pattern already in place: the `confirmed=true` argument on a tool is the mechanical grant token (`routes/voice.py` L1234-1244) — the tool refuses to act without it, and the model is instructed to obtain a spoken/typed "yes" first. Each granted `act` writes a **consent receipt** into `work_log` (who/what/when/scope).

> **⚠️ Overlap with V6 P5.** V6 P5 introduces a *general* approval queue (`services/approvals.py`) that blocks outward/irreversible steps until the owner approves, with expiry. This spec's Google pillar (Phase 4) ships a **thin subset**: a synchronous per-action `confirmed=true` grant + consent receipt, sufficient for interactive voice/chat. It is **forward-compatible**: when V6 P5 lands, the Office/Google `act` tools swap their inline `confirmed` check for `approvals.require(...)` with **no change to the tool contract**. Phase 4 must not build a *competing* durable approval store — the inline grant is deliberately ephemeral. (See Q3.)

### 3.4 Voice choreography (reused verbatim)

Every voice-invoked action obeys `VOICE_TOOL_CHOREOGRAPHY` (`routes/voice.py` L362): **ANNOUNCE** one short sentence → end sentence → **ACT** (silent) → **CONFIRM** one short sentence. For `act`-tier tools the full shape is **ASK → (user yes) → ANNOUNCE → ACT(confirmed=true) → CONFIRM**. Server→client `{"type":"status"|"action"|"cite"}` WS events and `tts_pause`/`tts_resume` bracketing are reused from `_voice_tool_run` (`voice_engine.py` L301).

---

## 4. Pillar Designs

### 4.1 Pillar 1 — Conversational creative generation

**Honest state.** Chat already has all six tools; the model can already generate an image mid-chat and be told "it's in the Studio gallery" (`_creative_result_summary`, `agent.py` L2156). Two real gaps:

1. **Voice can't create.** The live voice tool set is the curated four (`voice_engine._VOICE_LIVE_TOOLS`). Adding creative tools means declaring them in `_build_voice_live_tools` (L273) and dispatching them in `_voice_tool_run` (L301), each obeying the choreography (§3.4) and — because generation takes seconds to minutes — using the existing `tts_pause`/status pattern plus the process orb so the call doesn't feel dead while Veo/Lyria run.
2. **No receipt/transparency wrapper.** Creative tools sign C2PA provenance but don't write a work-log receipt or a `monitoring` orb with the real result. Wrap all six (chat + voice) in the Action Envelope (§3.1).

**Design.**
- **Voice creative tools (subset first).** Expose `generate_image`, `generate_music`, and `create_presentation` to voice in Phase 1 (the demo-legible, fastest-feedback three); `generate_video`/`compose_timeline` are slow (Veo LRO up to 600s) — expose them but have Friday **announce the wait and surface progress via the orb**, not block the call. Each voice tool result is a SHORT spoken summary ("Done — a square image of X is up in your Studio") plus a `{"type":"cite","label":"Opened","sources":[...]}` event carrying the `framed_url`.
- **Workspace surfacing (reuse).** No new channel: generation already writes to `CREATIONS_DIR`, fires the orb, and pushes a `kind="creation"` notification with `target={"workspace":"studio","creation":filename}` (`creations.py` `_notify_creation` L623). Voice invocations reuse this so the artifact lands in the Studio workspace identically to a Studio-bar creation.
- **Consent tier.** Creation is **observe-tier** (local artifact, no outbound beyond the already-gated prompt) → no per-action grant required. The only gate is the existing content-safety floor.
- **Model transparency.** The orb records the *creative* model actually used (`resolve_image_model`/`resolve_video_model`/music map), so the click-through shows "gemini-3-pro-image" etc.

**Non-goals.** Do not rebuild the engines; do not add a websocket; do not change the gallery.

### 4.2 Pillar 2 — Multimodal daily creations that come alive

**Honest state.** `generate_daily_creation` (`creations.py` L463) already free-chooses across `code-art`/`image`/`music-clip`/`short-production` (`DAILY_MODES` L290) weighted by recent work, ambient mood, active project, and remaining budget (`_choose_daily_mode` L343; budget ceiling `daily_creation_budget_usd` default $0.50). The "writing + code-art only" premise is outdated.

**Real gaps → design.**
1. **Media reliability & legibility.** Media modes are `_EXPENSIVE_DAILY_MODES` and skipped when budget/keys are short, silently falling back to text. Design: make the *choice* legible (record why a mode was chosen/skipped in the daily record) and add a settings-surfaced "media budget" so a media day is a deliberate, visible affordance rather than a silent coin-flip. Ensure `image` at minimum is reachable within the default budget.
2. **She doesn't actually talk about it.** The surfacing `chat_message` is a **hardcoded f-string template** (`creations.py` L577-580), so today the "I made something" line does not pass through the personality engine. Design: generate the surfacing comment with a **short LLM call through `_get_friday_system_prompt(workspace="creation")`** — an in-character, specific reaction to *this* creation ("I tried something jagged this morning — a 30-second Lyria clip that sounds like a subway at 6am. Curious what it does to you."). This is the "come alive" beat Stephen wants; it reuses the same persona funnel as every other Friday utterance.
3. **Proactive consumer may be unwired.** Agent recon found the `/api/notifications/chat-injections` engine + route exist and are tested, but the poller in `index.html` targets `/api/notifications`, not `chat-injections`. Design: verify and, if missing, wire the proactive-chat consumer so `proactive_chat=True` injections actually surface in the chat panel with the "proactive" badge; the daily creation is the flagship producer.
4. **A "talk about it together" follow-up.** After surfacing, if the user engages ("show me"), Friday opens the creation (reuse `open_url`/navigate) and discusses it — a light, optional second beat, not a new subsystem.

**Envelope.** The daily job runs under the Action Envelope with a `monitoring` orb (already partly true via `_creation_orb_start` L611) and a work-log receipt, so "what did Friday make and why" is explorable after the fact.

**Non-goals.** No new scheduler; no change to storage schema beyond adding a `choice_reason` field to the daily record.

### 4.3 Pillar 3 — Google agentic actions by voice

**Honest state (the constraint, encoded).** Friday's **own** connector is `services/google_accounts.py` — encrypted, multi-account, already write-scoped for Calendar. It is broken for exactly one reason: **no token was ever minted** (`~/.friday/google_token.json` missing; `~/.friday/google_accounts/` absent; `~/.gmail-mcp/accounts/*` empty). The design **repairs her own auth** and **must not** read, copy, or "transplant" any other application's or orchestrator's OAuth tokens — that crosses an OAuth trust boundary and is a security anti-pattern. Re-auth is a fresh consent run under Friday's own OAuth client.

This pillar splits into **3a (repair auth + near-one-click re-auth)** and **3b (read+write actions in the voice choreography)** — see phases §7.

**3a — Repair & one-click re-auth.**
- **Standardize on the built-in encrypted path** (`google_accounts.py`), retiring the redundant `gmail`/`calendar` MCP servers from `~/.friday/mcp_servers.json` (both currently unauthenticated; the calendar one is already `enabled:false`). One connector, encrypted at rest, write-capable — not two competing half-wired ones. (See Q7.)
- **One Desktop OAuth client, one scope set.** Today there's a Web client (`~/.friday/credentials.json`) and a Desktop client (`~/.gmail-mcp/oauth-keys.json`, BOM'd). Pick the **Desktop** client (loopback redirect, no host ambiguity), rewrite it without BOM, and unify scopes to the **superset the actions need** (see below) so a single token satisfies every path — resolving the current read-only-vs-read-write split (`calendar_engine` legacy read-only vs `google_accounts` read-write).
- **Near-one-click UI (the biggest UX gap).** There is **no rendered connect/reconnect control** anywhere — only the CLI `scripts/friday_google_connect.py` and raw JSON endpoints. Design: a **"Connectors" card in Settings** (and a proactive nudge when a Google chat/voice tool returns `connected:false`) with a single **"Connect Google"** button → opens `/api/google/accounts/connect`'s `auth_url` → callback `upsert_account` (encrypted) → live status. Reconnect / add-account / remove reuse the existing `routes/google_accounts.py` endpoints. Target: two clicks (button → Google consent → done).
- **Scope set (superset for all actions):** `gmail.readonly` **+ `gmail.send`** (or `gmail.modify` if drafts must be saved server-side — Q4), `calendar` (RW, already granted), `contacts.readonly` (net-new), `userinfo.email`. Requesting the superset once avoids re-consent when 3b lands.

**3b — Read+write actions in the voice choreography.** Net-new tools (chat **and** voice), each wrapped in the Action Envelope with the correct tier:

| Tool | Tier | Backing | Notes |
|---|---|---|---|
| `read_email` / `search_email` | observe | `calendar_engine._collect_messages` / `merged_gmail` | exists in chat; add to voice |
| `read_calendar` / `query_calendar` | observe | `merged_calendar` / `_fetch_calendar_today` | read tool already in voice |
| `pull_contacts` / `find_contact` | observe | **net-new** People API fetcher (mirror `merged_gmail`) | needs `contacts.readonly`; greenfield |
| `compose_email` (draft) | observe | `_tool_draft_email` (exists) | draft only — no send; safe to auto |
| `send_email` | **act** | **net-new** Gmail `users.messages.send` | outbound → ASK→confirmed→receipt |
| `create_calendar_event` | **act** | **net-new** Calendar `events.insert` | outbound → gated |
| `update_calendar_event` | **act** | **net-new** Calendar `events.patch` | outbound → gated |

- **"Messages" disambiguation (required by the brief).** In this pillar, **"messages" = Gmail email**. **SMS/RCS is explicitly out of scope** and flagged as a *separate, much harder integration* (no first-party local API on Windows; requires a paid gateway like Twilio, or Android-bridge tooling, each with its own auth, cost, and egress surface). If Stephen wants SMS, it is its own future spec, not a scope creep here. (See Q6.)
- **Voice choreography for `act` tools.** `send_email` etc. follow **ASK → yes → ANNOUNCE ("Sending it now.") → ACT(confirmed=true) → CONFIRM ("Sent — it's in your Sent folder.")**. Friday reads back recipient + subject + one-line gist *before* asking for the yes, so consent is informed. Draft is prepared observe-tier; only the send crosses to act-tier.
- **Egress.** The email *body text* passes the content egress gate before any LLM assist; the authorized `send` leaves on the user's own token. Both logged.

**Non-goals.** No SMS. No non-Google mail. No standing send capability (every send is a fresh grant).

### 4.4 Pillar 4 — OfficeCLI integration (local, sovereign)

**Findings (verified against a fresh clone of `iOfficeAI/OfficeCLI`, v1.0.132).**
- **License: Apache-2.0** (root `LICENSE`; `NOTICE` requires attribution retention under §4). Bundled deps: `DocumentFormat.OpenXml` 3.4.1 (MIT), `System.CommandLine` (MIT), .NET runtime (MIT). **Permissive — consume, wrap, or vendor are all allowed** with attribution.
- **Language premise corrected: it is .NET 10 / C#, not Go** (`src/officecli/officecli.csproj`, `net10.0`, `PublishSingleFile` + `SelfContained` + `PublishTrimmed`). It is still a **single self-contained native binary** with the runtime embedded — nothing to install at runtime, consistent with the sovereignty goal.
- **Windows binary exists.** GitHub Releases ship `officecli-win-x64.exe` and `officecli-win-arm64.exe` (plus mac/linux). Install via `install.ps1` (`irm .../install.ps1 | iex`), npm `@officecli/officecli`, or manual download; bare `officecli` self-installs.
- **Agent interface — four surfaces:** (1) direct **CLI** with deterministic `--json`; (2) **resident mode** over named pipes (warm document, `open`/`set`/`get`/`save`/`close`); (3) **batch mode** (JSON command array via stdin/`--commands`/`--input`); (4) a **built-in stdio MCP server** — bare `officecli mcp` starts a "Minimal MCP server over stdio" (`src/officecli/McpServer.cs`: JSON-RPC 2.0 with `initialize`/`tools/list`/`tools/call`). `officecli mcp <target>` merely *registers* it into a client config as `{command:"officecli", args:["mcp"]}`. Path addressing is `/slide[1]/shape[2]` (1-based, element-local-name), with L1/L2/L3 layering, `validate`/`view issues` self-heal, and `view html`/`screenshot` headless rendering.

**Recommendation: wrap-as-stdio-MCP (consume its own MCP server).**
- **Why.** Friday's `mcp_client.py` is stdio-JSON-RPC-capable and already registers MCP tools as `mcp_<server>_<tool>` Ring-2 (`agent.py` L4043). OfficeCLI ships exactly that server. So integration is **one entry in `~/.friday/mcp_servers.json`** — `{"command": "<pinned officecli.exe>", "args": ["mcp"], "env": {"OFFICECLI_SKIP_UPDATE":"1","OFFICECLI_NO_AUTO_INSTALL":"1"}, "enabled": true}` — and every Word/Excel/PowerPoint operation auto-registers as a Ring-2 tool with **zero wrapping code**. This is the maintainer's supported agent interface, so it tracks upstream.
- **Alternative considered — consume-the-binary via subprocess `--json`.** Viable and gives a *curated, smaller* tool surface (fewer tokens), but requires writing and maintaining a Python shim for each op. **Use only if** the full MCP tool list proves too large/noisy for the model; then expose a hand-picked subset (`create`, `set`, `get`, `view`, `dump`) as native chat/voice tools that shell out. Keep this as a fallback, not the default.
- **Alternative rejected — vendor the source.** Apache-2.0 permits it, but it's a heavy .NET tree with no benefit over pinning the release binary; rebuilding requires the .NET 10 SDK. **Pin the Windows release binary under `~/.friday/bin/officecli.exe`** instead (fetched by a repair action, §7 Phase 5, dovetailing V6 P7's Doctor).

**Design.**
- **Exposed capability:** create + edit `.docx`/`.xlsx`/`.pptx` as agent tools (chat first; a curated create/edit subset to voice later). Tools inherit the Action Envelope: `office`-kind orb (`monitoring`, model = the LLM that drove it), work-log receipt, and — because the *output is an artifact* — `provenance.write` on the produced file so an OfficeCLI-authored doc carries the same C2PA content credential as an image.
- **Tier:** editing a *local* file is **observe-tier** (no outbound). If a future flow sends the doc somewhere (email attachment via Pillar 3), that send is the `act`-tier gate, not the edit.
- **Sovereignty hardening (§6.3):** run with `OFFICECLI_SKIP_UPDATE=1` + `OFFICECLI_NO_AUTO_INSTALL=1`; pass **local file paths only**, never remote URLs (OfficeCLI's own `SsrfGuard` already refuses private/loopback fetches, but local-only inputs keep it fully offline); the binary is pinned (no auto-update phone-home).

**Non-goals.** No cloud Office; no server/watch-SSE mode consumed by Friday (stdio MCP only); no foreign-format plugins in v1.

---

## 5. OfficeCLI Findings — summary card (for the report)

| Question | Finding |
|---|---|
| **License** | **Apache-2.0** (+ NOTICE attribution). Bundles MIT deps (OpenXML SDK, System.CommandLine, .NET runtime). Consume/wrap/vendor all permitted. |
| **Language** | **.NET 10 / C#** — *not Go* (premise corrected). Still a single self-contained trimmed native binary, runtime embedded. |
| **Windows binary** | **Yes** — `officecli-win-x64.exe` / `officecli-win-arm64.exe` on GitHub Releases; `install.ps1` one-liner; npm; self-install. |
| **Agent interface** | CLI `--json` · resident (named pipes) · batch (JSON) · **built-in stdio MCP server** (`officecli mcp`, JSON-RPC 2.0). |
| **Recommendation** | **Wrap-as-stdio-MCP** — one `mcp_servers.json` entry → auto Ring-2 tools, zero shim. Fallback: subprocess `--json` curated subset. Reject: vendor source. |
| **Sovereignty caveat** | Subprocess network bypasses Friday's Python egress gate; mitigate with `OFFICECLI_SKIP_UPDATE`/`NO_AUTO_INSTALL` + local-file-only inputs; its `SsrfGuard` blocks private fetches. |

---

## 6. Sovereignty, Safety & Egress Invariants (must not be broken)

Extends V6 §6; nothing here weakens those.

1. **Local pillars stay offline.** Creative generation's only outbound is the already-gated Gemini *prompt*; music/timeline/showcase/Office are local composition. No user artifact is uploaded.
2. **The Google pillar is the only outbound action path, and it is doubly gated.** Free-text → content egress gate (fail-closed); the authorized service call → OAuth scope consent **plus** a fresh per-action `act` grant. No standing send/write capability.
3. **Friday repairs only her own auth.** Re-auth mints a token under Friday's own OAuth client via a real consent run. **No other app's/orchestrator's OAuth tokens are ever read, copied, or transplanted** — that boundary is inviolate.
4. **Every action is signed and receipted.** Artifacts → `provenance.write` (C2PA + Ed25519); actions → `work_log` receipt; both anchored to the one `IntegrityEngine` identity. `act`-tier grants leave a consent receipt.
5. **Every subagent task is transparent.** `process_register(category="monitoring", model=…)` + `intent:` log + completion `result=` → explorable at `/api/tasks/<id>` past the ephemeral purge.
6. **cLaws stay signed and non-negotiable.** The content-safety floor (`check_content_safety`/`check_music_safety`) and the HMAC-signed cLaws prompt are unchanged; new tools add gates, never remove the floor.
7. **Voice actions obey announce→act→confirm; `act` obeys ask-first.** Reused verbatim from `routes/voice.py`; kill/stop paths (V6 P6) remain available.
8. **OfficeCLI is sovereign-by-configuration.** Pinned binary, update/telemetry disabled, local-file-only inputs; the subprocess-egress gap is disclosed (invariant, not hidden — mirrors V6's local-VLM honesty).
9. **Consent is forward-compatible with V6 P5.** The thin inline grant is a subset of the coming approval queue; it must not fork into a competing durable store.

---

## 7. Phased Build Plan (one Fable session per phase)

Each phase is independently shippable, has its own acceptance gate + tests, and honors §6. Dependency order below; **Phase 1 goes first** because it is lowest-risk (no new outbound), most demo-legible, and it builds the shared Action Envelope every later phase reuses.

```
P1 Creative-in-chat+voice ──┬─► P2 Multimodal daily creations
   (+ Action Envelope)      │
                            ├─► P3 Repair Google auth ──► P4 Google actions by voice
                            │      (one-click re-auth)      (read+write, act-gated)
                            └─► P5 OfficeCLI integration        (float; indep. of P3/P4)
```

| Phase | Pillar | One-line deliverable | Hard deps |
|---|---|---|---|
| **P1** | 1 | Six creative tools wrapped in the **Action Envelope**; the demo-fast three (`generate_image`/`generate_music`/`create_presentation`) exposed to **voice** with choreography + orb | Action Envelope (built here) |
| **P2** | 2 | Media daily creations made first-class + legible; **persona-generated** surfacing comment; proactive-chat consumer wired | P1 (envelope, engines-in-voice) |
| **P3** | 3a | **Repair Friday's own Google auth**; one connector, one Desktop client, superset scopes; **one-click Connect/Reconnect UI** | — (auth only; no new actions) |
| **P4** | 3b | Read+write email/calendar/contacts as chat+voice tools in **announce→act→confirm**, `act`-gated + receipted; SMS flagged out | P3; thin consent gate (§3.3) |
| **P5** | 4 | OfficeCLI as a pinned stdio-MCP server → Word/Excel/PPT tools; Office artifacts signed; sovereign-by-config; fetch via a repair action | P1 (envelope); float |

**Sequencing notes.** P1→P2 share the creative engines and the envelope. P3 must precede P4 (no actions without repaired auth). P5 depends only on P1's envelope and can float — pull it forward if an Office demo is wanted sooner, or slot it alongside V6 P7's Doctor (the binary-fetch repair action is a natural Doctor check).

---

## 8. Acceptance Criteria & Test Plan (per phase)

Framework reuse: `pytest` (`tests/unit` no-Flask, `tests/api` Flask client, `tests/security`), `FRIDAY_TESTING=1`, `test_home`/`friday_dir` fixtures; `@playwright/test` for UI. Every phase's read paths must pass **offline**.

### Phase 1 — Creative in chat + voice
**Acceptance.**
- A voice turn "make me a square image of a fox in fog" produces a real file in `CREATIONS_DIR`, a `monitoring` orb showing the creative model + `intent:` + result, a work-log receipt, and a C2PA provenance sidecar — and Friday **announces before, stays silent during, confirms after** (choreography honored).
- The same three fast tools work identically from chat; slow tools (`generate_video`) announce the wait and surface progress via the orb without blocking the voice session.
- Creation is observe-tier: no per-action grant is demanded; the content-safety floor still blocks a seeded H1 prompt.

**Tests.** `tests/unit/test_action_envelope.py` (tier routing, orb/receipt/provenance calls fire, fail-closed on egress error), `tests/unit/test_voice_creative_tools.py` (`_build_voice_live_tools` includes the three; `_voice_tool_run` dispatches + emits `cite`), `tests/api/test_creative_receipts.py` (work-log entry per creation), a Playwright smoke asserting the Studio gallery gains the file and the orb detail renders `result`.

### Phase 2 — Multimodal daily creations
**Acceptance.**
- A forced daily run (`POST /api/creations/daily/run?force=1`) can select and complete a **media** mode within the configured budget; the daily record carries `choice_reason`.
- The surfacing message is **LLM-generated in-character** (varies run to run; passes through `_get_friday_system_prompt`), not the old template; a `proactive_chat=True` injection appears in the chat panel with the proactive badge.
- The whole run is receipted + orb-explorable; the timeline renders offline.

**Tests.** `tests/unit/test_daily_media_choice.py` (media reachable under budget; `choice_reason` recorded; budget hard-stop falls back with a logged reason), `tests/unit/test_daily_surface_persona.py` (comment routed through the persona funnel, not the literal template string), `tests/api/test_chat_injection_consumer.py` (injection produced → `/api/notifications/chat-injections` serves it → ack clears it), Playwright asserting the proactive badge renders.

### Phase 3 — Repair Google auth + one-click re-auth
**Acceptance.**
- From a clean state (no token), clicking **Connect Google** in Settings completes consent and lands an **encrypted** account in `~/.friday/google_accounts/`; `GET /api/google/status` / `/api/google/accounts` report connected with the superset scopes; **no plaintext token** is written.
- Read tools (`query_calendar`, `search_email`) flip from `connected:false` to real data with no code change.
- **Security:** the flow uses only Friday's own OAuth client; a test asserts no code path reads tokens from any non-Friday location (no `~/.gmail-mcp/accounts` transplant, no external-app token dir).

**Tests.** `tests/api/test_google_connect_ui.py` (button→auth_url→callback→encrypted upsert; reconnect idempotent), `tests/security/test_no_token_transplant.py` (assert the connect path never opens another app's token store; only mints via `build_auth_flow`), `tests/unit/test_scope_superset.py` (one token satisfies both read-only and read-write validators), offline: connect UI degrades gracefully when offline.

### Phase 4 — Google actions by voice (the outbound pillar)
**Acceptance.**
- Voice "email Libby that I'll be 10 minutes late" → Friday **reads back** recipient+subject+gist, **asks**, and only on "yes" calls `send_email(confirmed=true)`; a **consent receipt + work-log receipt** are written; without the yes, nothing is sent.
- `create_calendar_event` inserts a real event only after the same ask-first grant; `pull_contacts` (observe) returns People-API data.
- SMS is **not** offered; asking for a text yields a clear "that's a separate integration I don't have."
- **Adversarial egress test (required):** with the egress gate forced into a failed self-test state, every Google `act` tool is **blocked** (fail-closed) — no send, no insert; and a TIER_3 secret embedded in an email body is dropped/redacted by `gate_text` before any LLM assist, while the authorized send still requires the explicit grant.

**Tests.** `tests/unit/test_google_action_tools.py` (send/insert/patch build correct API calls on a mocked service; observe vs act tiers), `tests/security/test_google_egress_adversarial.py` (**the required adversarial suite**: gate-down → all `act` blocked; TIER_3 body → redacted; unconsented send → refused; consented send → single receipt), `tests/unit/test_send_requires_confirmed.py` (`confirmed=true` mandatory; missing grant → refusal + no side effect), `tests/api/test_google_action_routes.py`, voice-choreography assertion that `act` tools follow ASK→confirm.

### Phase 5 — OfficeCLI
**Acceptance.**
- With the pinned binary present, `mcp_servers.json` gains the `officecli` entry and Friday auto-registers `mcp_officecli_*` Ring-2 tools; a chat turn "make a 3-slide deck about Q3 and bold the title" produces a real `.pptx` via the MCP tools, with an `office` orb (model + intent + result), a work-log receipt, and a provenance sidecar on the file.
- The binary runs with update/auto-install disabled; a test confirms no remote URL is passed and the tool operates on a local temp file only.
- A repair action fetches the correct Windows binary when missing (or reports a plain next step if it can't).

**Tests.** `tests/api/test_officecli_mcp_registration.py` (entry → tools registered as Ring-2; disabled entry → none), `tests/unit/test_office_action_envelope.py` (office artifact signed + receipted), `tests/security/test_officecli_offline.py` (SKIP_UPDATE/NO_AUTO_INSTALL set; local-file-only; no outbound), `tests/unit/test_officecli_repair_action.py` (fetch-or-clear-next-step against a mock target). A live end-to-end doc-creation smoke gated behind an env flag (needs the real binary).

### Cross-phase regression
- After P4 (the behavior/outbound phase), extend `tests/security/test_egress_gate_adversarial.py` with the Google action paths (mirrors V6 §5's requirement).
- Determinism/idempotency: connect (P3) idempotent; daily `choice_reason` (P2) deterministic under a seeded mode; OfficeCLI registration (P5) idempotent.

---

## 9. Open Questions for Stephen

**Q1 — Voice creative scope (shapes P1).** Expose all six creative tools to voice, or just the demo-fast three (`generate_image`/`generate_music`/`create_presentation`) in P1 and add the slow LRO ones (`generate_video`/`compose_timeline`) later? *Recommend the three first* — video's up-to-600s Veo poll is awkward to narrate in a live call.

**Q2 — "Come alive" cost (shapes P2).** The persona-generated surfacing comment adds one small LLM call per daily creation (and per proactive surfacing). Fine as a daily cost, or cap it (e.g. skip the LLM comment on the cheapest text days)? *Recommend: always generate the comment — it's the whole point of the pillar — but route it local-first when offline.*

**Q3 — ⚠️ Consent model vs V6 P5 (shapes P4).** Ship the thin inline `confirmed=true` grant now and swap to V6 P5's `approvals.require(...)` when P5 lands, or wait for P5 and build P4 on the real queue? *Recommend inline-now, swap-later* — it unblocks the voice actions before the larger goal machinery, and §3.3 keeps the tool contract stable across the swap. Confirm the swap is acceptable.

**Q4 — Gmail scope (shapes P3/P4).** `gmail.send` (send-only, minimal) or `gmail.modify` (also lets Friday save server-side drafts and manage labels, broader blast radius)? *Recommend `gmail.send`* unless you want her drafting into your real Drafts folder.

**Q5 — ⚠️ Retire the Google MCP servers? (shapes P3).** Standardize on the built-in encrypted connector and remove the `gmail`/`calendar` entries from `mcp_servers.json` (avoids a second, unauthenticated path)? *Recommend yes.* Separately: the config currently holds a **live GitHub PAT in plaintext** at `~/.friday/mcp_servers.json` — worth rotating/encrypting regardless of this decision.

**Q6 — SMS (shapes P3/P4 scope boundary).** Confirm SMS/RCS is **out of scope** for this layer (it needs a paid gateway or an Android bridge, each its own auth/cost/egress spec). "Messages" = Gmail here. *Recommend: out of scope; revisit as a standalone spec if you want it.*

**Q7 — OfficeCLI surface & placement (shapes P5).** Full MCP tool list (broad, tracks upstream, more tokens) or a curated subprocess subset (leaner, more maintenance)? And keep P5 floating, or bind its binary-fetch repair action into V6 P7's Doctor? *Recommend full MCP list first (measure token cost), Doctor-integrate the fetch.*

**Q8 — Voice for Office (shapes P5).** Do you want Word/Excel/PowerPoint editing by *voice* in v1, or chat-only first? *Recommend chat-only first* — document editing is verbose to drive by voice; add a curated voice subset once the chat path is proven.

---

*This spec touches no app code and reuses the existing engines, egress gate, signing, receipts, transparency, and voice choreography verbatim. Its center of gravity is wiring and honesty, not new subsystems. It is the actuator-and-creative detail under `docs/V6_WHOLENESS_SPEC.md`; where they overlap (the human-gate), §3.3 defines the subset relationship so V6 P5 generalizes it rather than colliding.*

*End of specification.*
