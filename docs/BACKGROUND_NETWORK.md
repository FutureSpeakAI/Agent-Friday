# Background Network Activity

Agent Friday starts several background threads that make outbound network
connections. This document lists every one of them, what they connect to,
why, and how to disable each if you prefer a stricter network posture.

Friday is a sovereignty-focused tool — every outbound call is opt-in at the
design level. None of these threads send conversation content, vault data, or
PII anywhere; they are limited to connectivity probes, RSS feeds, and provider
health checks.

---

## 1. Network Monitor (`_network_monitor_loop`)

**What it does:** Pings a reliable external host (currently `8.8.8.8` / Google
DNS) every 30 seconds to determine whether Friday is online or offline.

**Why:** When offline, Friday automatically switches the model router to
local-only inference (Ollama) so you keep getting responses even without
internet. The probe result also drives the offline badge in the UI.

**Data sent:** A single ICMP echo (ping) or TCP handshake — no payload, no
identity, no headers.

**Disable:** Set `offline_auto_local: false` in Settings → Privacy, or set
`FRIDAY_TESTING=1`. The probe still runs but the offline overlay is not applied.

---

## 2. News Archiver (`_news_archiver_loop`)

**What it does:** Fetches RSS feeds from news sources in your news_priorities
list at the same cadence the briefing runs (typically every hour or on the
briefing schedule).

**Data sent:** Standard HTTP GET requests to the RSS endpoints. No auth
headers, no user identifiers, no content from your conversations.

**Disable:** Clear your news_priorities list in Settings, or toggle off
"Include Sources" in the briefing panel.

---

## 3. Connector Health Monitor (`connector_health_monitor_loop`)

**What it does:** Polls the health of connected services (Google OAuth token
validity, MCP server reachability) roughly every 5 minutes. Fires a
notification if a connector you rely on goes down.

**Data sent:** Lightweight presence/status checks. For Google: a token
validity check (no email or calendar content). For MCP: a TCP connection test.

**Disable:** Disconnect the connector in Settings → MCP Connectors / Account &
Security.

---

## 4. MCP Server Boot (`_mcp_boot`)

**What it does:** Launches any stdio MCP servers you have configured in
`~/.friday/mcp_servers.json` and registers their tools. MCP servers are local
processes, not remote services — but some MCP servers (e.g. a Slack MCP) may
themselves make outbound network calls as part of their tool implementations.

**Data sent:** Depends entirely on which MCP servers you enable. The core
Friday server only communicates with them over local stdio.

**Disable:** Remove entries from `~/.friday/mcp_servers.json` or toggle them
off in Settings → MCP Connectors.

---

## 5. Predictive Prewarm (`_predictive_prewarm_loop`, `_prewarm_predicted_boot`)

**What it does:** At boot and on a recurring timer, estimates which workspaces
you are likely to use next (based on time-of-day usage patterns) and pre-loads
their data. This is entirely local — it reads from your wiki and stored
briefings, never from the network.

**Data sent:** None.

---

## 6. Internal Scheduler (`start_scheduler`)

**What it does:** Runs the Friday job scheduler — a 60-second tick loop that
fires registered background jobs (daily briefing generation, self-improvement
report, repo-sync, etc.) at their configured times.

**Network activity:** Depends on which jobs are scheduled. Briefing generation
makes LLM API calls (Anthropic / Gemini) and fetches news RSS. Repo-sync runs
`git pull` on repositories you specify. All scheduled jobs are listed in
`~/.friday/schedules.json` and can be removed there.

**Disable individual jobs:** Edit `~/.friday/schedules.json` or use Settings →
Scheduled Tasks.

---

## 7. Provider Key Bootstrap (`bootstrap_provider_env`)

**What it does:** Reads encrypted provider API keys from the credential store
(`~/.friday/credentials/`) and sets them in the process environment so the
Anthropic / Gemini SDK clients can find them.

**Network activity:** None — this is a local decryption step.

---

## Summarized traffic table

| Thread | Destination | Frequency | Disable |
|--------|------------|-----------|---------|
| Network monitor | 8.8.8.8 (ping) | Every 30s | `offline_auto_local: false` |
| News archiver | RSS feed URLs | Hourly (briefing cadence) | Clear `news_priorities` |
| Connector health | Google OAuth · MCP servers | Every 5min | Disconnect connector |
| MCP boot | Local stdio (+ whatever MCP servers call) | Once at startup | Remove from mcp_servers.json |
| Scheduler jobs | Anthropic / Gemini APIs · git remotes | Per schedule | Delete job from schedules.json |
| Predictive prewarm | None (local only) | Boot + periodic | n/a |
| Key bootstrap | None (local only) | Once at startup | n/a |

---

## What Friday does NOT do

- Send conversation content to any third party (other than the LLM provider
  you have explicitly configured and paid for).
- Send vault data (financial, health, legal records) outside your machine.
  Vault content is local-model-only by policy; the egress gate rejects any
  attempt to send it to a cloud model.
- Collect telemetry, usage statistics, or crash reports.
- Phone home to FutureSpeak.AI.
- Auto-update itself.
