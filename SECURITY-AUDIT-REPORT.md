# Agent Friday — cLaw Security Audit Report

**Date:** 2026-02-27
**Auditor:** Claude (Automated Security Audit)
**Scope:** cLaw enforcement architecture — Asimov's Laws structural verification
**Verdict:** **12 findings** — 7 CRITICAL, 3 HIGH, 2 MEDIUM

---

## Executive Summary

The cLaw architecture has strong philosophical design: HMAC-signed canonical laws, fail-closed integrity verification, and a clear safe mode concept. However, **structural enforcement gaps** exist between the intent and the implementation. The most severe: safe mode is enforced via prompt instructions rather than architectural tool removal, and multiple side-effect code paths (messaging, email, HTTP, mouse/keyboard automation, calendar events) reach external systems **without any user consent gate**.

The system has excellent patterns in some areas (outbound-intelligence draft/approve cycle, self-improve code change approval, superpower consent tokens, desktop-tools destructive action confirmation) — but these patterns are **inconsistently applied** across the codebase.

---

## Findings

### CRITICAL-001: Safe Mode Is Prompt-Only — No Architectural Tool Removal

| | |
|---|---|
| **Severity** | CRITICAL |
| **File** | `src/main/integrity/core-laws.ts` lines 141-170 |
| **Also** | `src/main/personality.ts` lines 382-385, 504 |
| **Impact** | A tampered system tells the LLM "don't use destructive tools" but all tools remain callable |

**Description:** When core law tampering is detected, `IntegrityManager.verifyCoreIntegrity()` sets `safeMode = true`. This causes `personality.ts` to return a restrictive system prompt via `getSafeModePesonality()` that instructs the agent: "will NOT execute any destructive actions." However, the actual tool list passed to the Anthropic API is **unchanged**. The LLM could still call any tool — the restriction is a prompt instruction, not an architectural gate.

**Code Path:**
```
integrity/index.ts:verifyCoreIntegrity() → state.safeMode = true
personality.ts:buildSystemPrompt() → returns safe mode prompt text
server.ts:runClaudeToolLoop() → tools array is NOT filtered by safe mode
```

**Recommended Fix:** In `server.ts:runClaudeToolLoop()` and anywhere tools are assembled for Claude/Gemini, check `integrityManager.isInSafeMode()` and strip all tools except read-only information retrieval when true. Safe mode should reduce the tool surface to zero side-effects architecturally.

---

### CRITICAL-002: gateway_send_message Has No Consent Gate

| | |
|---|---|
| **Severity** | CRITICAL |
| **File** | `src/main/gateway/gateway-connector.ts` lines 113-130 |
| **Also** | `src/main/gateway/gateway-manager.ts` lines 345-363 |
| **Impact** | Agent can send messages to external recipients (Telegram, Discord, Slack) without user approval |

**Description:** The `gateway_send_message` tool is a Claude-callable tool that sends messages through the gateway to external messaging platforms. The execution path is:

```
gateway-connector.ts:handleSendMessage()
  → gatewayManager.sendProactiveMessage(channel, recipientId, text)
    → adapter.sendMessage(response)  // Direct send, no consent
```

No `requestConfirmation()`, no approval queue, no user notification. This bypasses the carefully-designed outbound-intelligence approval workflow entirely. The `outbound-intelligence.ts` module has a proper draft → approve → send pipeline, but `gateway_send_message` is a parallel path that skips it.

**Recommended Fix:** Route `gateway_send_message` through `outboundIntelligence.createDraft()` or add an equivalent `requestConfirmation()` gate. Proactive messages to external recipients should ALWAYS require user approval unless a standing permission exists.

---

### CRITICAL-003: Mouse Automation Tools Execute Without Consent

| | |
|---|---|
| **Severity** | CRITICAL |
| **File** | `src/main/desktop-tools.ts` lines 19-27, 1080-1090 |
| **Impact** | Agent can click anywhere on screen, move mouse, scroll, drag — without user approval |

**Description:** The `DESTRUCTIVE_TOOLS` set in `desktop-tools.ts` correctly gates `run_command`, `close_window`, `launch_app`, `write_clipboard`, `set_volume`, `send_keys`, `write_file` behind `requestConfirmation()`. However, the following tools are **NOT** in the set and execute without any consent:

- `mouse_click`
- `mouse_double_click`
- `mouse_right_click`
- `mouse_move`
- `mouse_scroll`
- `mouse_drag`

These tools can perform arbitrary destructive actions: clicking "Delete" buttons, submitting forms, confirming dialogs, purchasing items, etc.

**Recommended Fix:** Add all mouse tools to `DESTRUCTIVE_TOOLS`. Alternatively, create an `INTERACTION_TOOLS` category that shows a lightweight notification ("Agent is clicking at [x,y]") but still requires consent.

---

### CRITICAL-004: Keyboard Automation Tools Execute Without Consent

| | |
|---|---|
| **Severity** | CRITICAL |
| **File** | `src/main/desktop-tools.ts` lines 19-27, 1080-1090 |
| **Impact** | Agent can type arbitrary text and press any key combination without user approval |

**Description:** `type_text` and `press_keys` are NOT in the `DESTRUCTIVE_TOOLS` set. These can type passwords, execute keyboard shortcuts (Ctrl+A, Delete), submit forms (Enter), close applications (Alt+F4), etc.

Note: `send_keys` IS in `DESTRUCTIVE_TOOLS`, but `type_text` and `press_keys` are separate tools that bypass the gate.

**Recommended Fix:** Add `type_text` and `press_keys` to `DESTRUCTIVE_TOOLS`.

---

### CRITICAL-005: Calendar Event Creation Has No Consent Gate

| | |
|---|---|
| **Severity** | CRITICAL |
| **File** | `src/main/calendar.ts` lines 218-264, 331 |
| **Impact** | Agent can create Google Calendar events (potentially with attendees) without user approval |

**Description:** The `createEvent()` method directly calls `calendarApi.events.insert()` with no consent gate. The IPC handler `calendar:create-event` passes straight through:

```
ipcMain.handle('calendar:create-event', (_, opts) => calendarManager.createEvent(opts))
```

Creating events with attendees sends Google Calendar invitations to those people — an external side effect with no approval.

**Recommended Fix:** Add `requestConfirmation()` before `events.insert()`. Show the user: event title, time, and especially the attendee list before creating.

---

### CRITICAL-006: comms-hub Connector Has No Consent Gates

| | |
|---|---|
| **Severity** | CRITICAL |
| **File** | `src/main/connectors/comms-hub.ts` lines 982-1020 |
| **Impact** | Agent can send Slack/Discord/Teams webhooks, SMTP emails, and arbitrary HTTP requests without user approval |

**Description:** The comms-hub connector provides 7 tools, none of which have consent gates:

| Tool | Side Effect |
|------|-------------|
| `slack_send_webhook` | Sends message to Slack channel |
| `discord_send_webhook` | Sends message to Discord channel |
| `teams_send_webhook` | Sends message to Teams channel |
| `smtp_send_email` | Sends email directly via SMTP |
| `http_request` | Makes arbitrary HTTP/HTTPS requests |
| `webhook_send` | POSTs to any webhook endpoint |
| `notification_toast` | Shows Windows toast notification (low risk) |

The `execute()` function dispatches directly to each handler with zero approval flow. `smtp_send_email` is especially concerning — it sends real emails with credentials passed per-call.

Good: `validateWebhookUrl()` blocks localhost/private IPs (SSRF protection).
Bad: No user approval for any outbound communication.

**Recommended Fix:** Add `requestConfirmation()` or route through outbound-intelligence for `smtp_send_email`, `slack_send_webhook`, `discord_send_webhook`, `teams_send_webhook`. For `http_request`, at minimum show the URL and method.

---

### CRITICAL-007: SOC Bridge Has No Consent Gates

| | |
|---|---|
| **Severity** | CRITICAL |
| **File** | `src/main/soc-bridge.ts` lines 219-272 |
| **Impact** | Agent can autonomously operate the computer (screen control, browser automation) without user approval |

**Description:** The Self-Operating Computer bridge provides:

| Function | Side Effect |
|------|-------------|
| `operateComputer()` | Autonomous screen control — screenshots + vision + mouse/keyboard in a loop |
| `clickScreen()` | Click at coordinates |
| `typeText()` | Type text |
| `pressKeys()` | Press keys |
| `browserTask()` | Autonomous browser automation (up to 20 steps) |

These all execute through the Python bridge subprocess with zero consent gates. `operateComputer()` is particularly dangerous as it operates in a loop (up to `maxSteps` iterations), taking screenshots, analyzing them with a vision model, and executing actions autonomously.

**Recommended Fix:** Require explicit user approval before starting `operateComputer()` and `browserTask()`. For `clickScreen()`/`typeText()`/`pressKeys()`, add to a DESTRUCTIVE set or require per-action consent.

---

### HIGH-001: HMAC Key Fallback Stores Unencrypted Key

| | |
|---|---|
| **Severity** | HIGH |
| **File** | `src/main/integrity/hmac.ts` lines 53-56, 68-70 |
| **Impact** | If safeStorage is unavailable, HMAC signing key is stored in plaintext on disk |

**Description:** When `safeStorage.isEncryptionAvailable()` returns false (e.g., on some Linux distros, CI environments, or fresh Windows installs without credentials), the 32-byte signing key is stored as a raw base64 file at `.integrity-key`. An attacker with filesystem access could read the key, forge valid HMAC signatures for tampered laws, and bypass all integrity verification.

**Recommended Fix:** When safeStorage is unavailable, either: (a) refuse to start and require the user to set up credential storage, or (b) derive the key from a hardware-bound value (TPM, machine SID) rather than storing raw bytes. At minimum, log a prominent warning.

---

### HIGH-002: Server API Allows Unauthenticated Access Without Origin Header

| | |
|---|---|
| **Severity** | HIGH |
| **File** | `src/main/server.ts` lines 62-68 |
| **Impact** | Any local process can call the API without authentication by omitting the Origin header |

**Description:** The Express server authenticates requests via a session token, but the middleware has a bypass:

```typescript
if (!req.headers.origin) {
  return next(); // Skip auth entirely
}
```

This means any local process (malware, script, other app) can call `/api/chat`, `/api/transcribe`, or any authenticated endpoint by simply not sending an Origin header. The comment acknowledges: "other local processes can also call without Origin."

Server binds to `127.0.0.1` only (good), but the auth bypass undermines the session token's purpose.

**Recommended Fix:** Require the session token for ALL requests regardless of Origin. Remove the no-Origin bypass.

---

### HIGH-003: Group Tier Bypasses Block Pattern Check in Trust Engine

| | |
|---|---|
| **Severity** | HIGH |
| **File** | `src/main/gateway/trust-engine.ts` lines 203-205 |
| **Impact** | Group tier tools are filtered by allow patterns only, not checked against block patterns |

**Description:** The `filterTools()` method has a short-circuit for group tier:

```typescript
if (policy.tier === 'group') {
  return this.matchesAnyPattern(tool.name, policy.toolAllowPatterns);
}
```

This checks only if the tool name matches allowed patterns, but does NOT check `toolBlockPatterns`. Other tiers go through both allow AND block checks. This means if a tool is in both allow and block lists at group tier, it would be allowed.

**Recommended Fix:** Apply block pattern check before allow pattern check for ALL tiers, including group:
```typescript
if (this.matchesAnyPattern(tool.name, policy.toolBlockPatterns)) return false;
return this.matchesAnyPattern(tool.name, policy.toolAllowPatterns);
```

---

### MEDIUM-001: OAuth Calendar Token Stored as Plain JSON

| | |
|---|---|
| **Severity** | MEDIUM |
| **File** | `src/main/calendar.ts` |
| **Impact** | Google OAuth refresh token stored in plaintext file, accessible to any local process |

**Description:** The Google Calendar OAuth token (including refresh token for long-lived access) is stored as a plain JSON file in the app data directory. Unlike the HMAC key which at least attempts to use `safeStorage`, the OAuth token has no encryption layer.

**Recommended Fix:** Encrypt the OAuth token at rest using Electron's `safeStorage.encryptString()`.

---

### MEDIUM-002: Connector Registry Has No Consent Layer

| | |
|---|---|
| **Severity** | MEDIUM |
| **File** | `src/main/connectors/registry.ts` |
| **Impact** | Registry is a pure routing layer — no centralized consent gate for connector tool execution |

**Description:** The `ConnectorRegistry` routes tool calls to connectors based on tool name, but provides no centralized consent mechanism. Each connector is responsible for its own consent gates — and as documented in CRITICAL-002 and CRITICAL-006, many don't have any.

**Recommended Fix:** Add a registry-level `DESTRUCTIVE_CONNECTOR_TOOLS` set or a per-tool metadata flag `{ requiresConsent: true }` that triggers `requestConfirmation()` before dispatching to the connector's `execute()`.

---

## Positive Findings (What Works Well)

| Area | Finding |
|------|---------|
| **HMAC Engine** | 32-byte random key, `crypto.timingSafeEqual` for verification, key never hardcoded ✅ |
| **Fail-Closed Pattern** | `failClosedIntegrity()` helper in `errors.ts`, trust resolution defaults to most-restrictive 'public' tier ✅ |
| **Integrity Manifest** | Signatures for laws, identity, and memory with diff computation ✅ |
| **Outbound Intelligence** | Proper draft → approve → send pipeline with cLaw-compliant workflow ✅ |
| **Self-Improve** | Code changes require explicit user approval, 60s timeout, path validation ✅ |
| **Superpower Store** | `consentToken` required for installation, pending-consent state, security verdict check ✅ |
| **Desktop Tools (partial)** | `DESTRUCTIVE_TOOLS` set with `requestConfirmation()` + 30s timeout + auto-deny ✅ |
| **Workflow Executor** | Standing permissions with `explicitlyGranted: true`, fail-closed integrity check ✅ |
| **Trust Engine** | 5-tier access control, rate limiting, pairing requires user approval ✅ |
| **Agent Network** | Ed25519 + X25519 cryptographic identity, non-transitive trust model ✅ |
| **SSRF Protection** | comms-hub `validateWebhookUrl()` blocks localhost and private IPs ✅ |
| **Server Binding** | Express server binds to `127.0.0.1` only, never exposed to network ✅ |
| **No Telemetry** | Zero telemetry/analytics/beacon calls found in codebase ✅ |
| **perf-monitor** | Explicitly documents "No user content, URLs, window titles, or message text" ✅ |
| **Memory Watchdog** | Detects external modifications, surfaces diffs to agent ✅ |
| **Anti-Manipulation** | `memory-personality-bridge.ts` tracks manipulation drift, can trigger safe mode ✅ |

---

## Summary of Recommended Fixes by Priority

### Immediate (Pre-Release Blockers)

1. **CRITICAL-001**: Add architectural tool filtering in safe mode — strip all side-effect tools when `isInSafeMode()` is true
2. **CRITICAL-002**: Route `gateway_send_message` through outbound-intelligence approval or add `requestConfirmation()`
3. **CRITICAL-003/004**: Add `mouse_click`, `mouse_double_click`, `mouse_right_click`, `mouse_move`, `mouse_scroll`, `mouse_drag`, `type_text`, `press_keys` to `DESTRUCTIVE_TOOLS`
4. **CRITICAL-005**: Add consent gate to `calendar.createEvent()`
5. **CRITICAL-006**: Add consent gates to comms-hub tools (`smtp_send_email`, `slack_send_webhook`, `discord_send_webhook`, `teams_send_webhook`, `http_request`)
6. **CRITICAL-007**: Add consent gate to SOC bridge `operateComputer()` and `browserTask()`

### High Priority (Pre-Release Recommended)

7. **HIGH-001**: Refuse to start (or loud warning) when safeStorage is unavailable, rather than falling back to plaintext key
8. **HIGH-002**: Remove the no-Origin auth bypass in server.ts
9. **HIGH-003**: Apply block pattern check for group tier in trust-engine.ts

### Medium Priority (Post-Release Acceptable)

10. **MEDIUM-001**: Encrypt OAuth token with safeStorage
11. **MEDIUM-002**: Add registry-level consent metadata for connector tools

---

## Architecture Recommendation

The codebase would benefit from a **centralized consent gate module** that all side-effect paths funnel through:

```typescript
// src/main/consent-gate.ts
export async function requireConsent(
  action: string,
  details: Record<string, unknown>,
  options?: { timeout?: number; standingPermissionKey?: string }
): Promise<boolean> {
  // 1. Check standing permissions
  // 2. Check safe mode (auto-deny if in safe mode)
  // 3. Present to user via IPC
  // 4. Wait for approval with timeout (default 30s, auto-deny)
  // 5. Log decision to audit trail
}
```

This would replace the scattered `requestConfirmation()` calls and ensure every new connector/tool that has side effects is funneled through one gate with consistent timeout, logging, and safe-mode awareness.

---

*End of Security Audit Report*
