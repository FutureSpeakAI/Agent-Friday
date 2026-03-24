# Gateway & Trust Flow

## Quick Reference

| Property | Value |
|----------|-------|
| **Status** | Active |
| **Type** | Security / Messaging |
| **Complexity** | High (12 components, 5 trust tiers, Ed25519 attestation) |
| **Last Analyzed** | 2026-03-24 |

## Overview

Every inbound message from an external channel (Telegram, Discord, Slack) passes through a multi-layer security pipeline before reaching Claude. The Gateway Manager orchestrates channel adapters, the Trust Engine resolves sender identity to one of five trust tiers, and each tier maps to a static capability policy that gates tool access, memory permissions, iteration limits, and rate limiting. Unknown senders are shunted into a pairing flow; the Integrity system provides HMAC-signed attestation for cross-agent P2P governance.

## Flow Boundaries

| Boundary | Location |
|----------|----------|
| **Start** | Inbound message arrives at a ChannelAdapter (e.g. Telegram long-poll) |
| **End** | Response sent back through the adapter + audit log + optional memory extraction |

## Component Reference

| Component | File | Purpose |
|-----------|------|---------|
| Gateway Types | `src/main/gateway/types.ts` | Shared interfaces: GatewayMessage, TrustTier, TrustPolicy, AuditEntry, PairedIdentity |
| Trust Engine | `src/main/gateway/trust-engine.ts` | Resolves sender identity to trust tier, manages pairings, rate limiting, tool filtering |
| Gateway Manager | `src/main/gateway/gateway-manager.ts` | Singleton orchestrator: adapter lifecycle, inbound pipeline, Claude tool loop, session + memory |
| Gateway Connector | `src/main/gateway/gateway-connector.ts` | Registers as a standard connector so Claude can send proactive messages |
| Persona Adapter | `src/main/gateway/persona-adapter.ts` | Builds channel-specific system prompts with injection defense and trust-tier awareness |
| Session Store | `src/main/gateway/session-store.ts` | Per-sender ephemeral conversation buffer (last 10 messages, 4h expiry) |
| Audit Log | `src/main/gateway/audit-log.ts` | Append-only JSONL audit trail, monthly rotation |
| Telegram Adapter | `src/main/gateway/adapters/telegram.ts` | Telegram Bot API long-polling adapter, zero external deps |
| Consent Gate | `src/main/consent-gate.ts` | Centralized consent for all side-effect actions; auto-deny in safe mode |
| Integrity Manager | `src/main/integrity/index.ts` | HMAC-signed manifest for laws, identity, and memory; safe mode on tampering |
| Core Laws | `src/main/integrity/core-laws.ts` | Hardcoded Asimov's cLaws (Three Laws); canonical source for integrity verification |
| cLaw Attestation | `src/main/claw-attestation.ts` | Ed25519-signed attestation for cross-agent P2P governance |
| Agent Trust | `src/main/agent-trust.ts` | Tracks user's trust in the agent (frustration detection, recovery mode) |
| Integration Handlers | `src/main/ipc/integration-handlers.ts` | IPC handlers for gateway enable/disable, pairing approve/revoke |
| Integrity Handlers | `src/main/ipc/integrity-handlers.ts` | IPC handlers for integrity state, verification, safe mode reset |

## Detailed Flow

### 1. Adapter receives inbound message (`gateway/adapters/telegram.ts:110-161`)

The Telegram adapter runs a long-polling loop calling `getUpdates` with a 30s timeout. For each update containing a text message from a non-bot sender, it constructs a `GatewayMessage` with `trustTier: 'public'` (default) and calls `this.onMessage(gatewayMsg)`. In group chats, messages are only processed if the bot is @mentioned.

### 2. Gateway Manager handles inbound (`gateway/gateway-manager.ts:119-240`)

`handleInbound(msg)` is the core 12-step pipeline:

1. **Trust resolution** (`trust-engine.ts:171-193`): `resolveTrust(channel, senderId)` checks owner IDs first, then paired identities, defaulting to `'public'`. On any error, fails CLOSED to `'public'`.

2. **Rate limit check** (`trust-engine.ts:251-276`): Sliding 1-minute window per sender. Hard cap of 10,000 concurrent sender entries to prevent DoS. Rate limits per tier: local=999, owner-dm=30, approved-dm=10, group=5, public=3.

3. **Audit inbound** (`audit-log.ts:53-68`): Fire-and-forget JSONL append with text truncated to 500 chars.

4. **Public tier handling** (`gateway-manager.ts:139-141`): Unknown senders get the pairing flow -- a unique 8-character code (40 bits of entropy, characters `ABCDEFGHJKLMNPQRSTUVWXYZ23456789`) is generated and sent back. Code expires in 15 minutes.

5. **Build system prompt** (`persona-adapter.ts:53-70`): Layers four blocks: core personality (from `buildSystemPrompt()`), injection defense rules, channel-specific overlay (Telegram/Discord/Slack format constraints), and trust-tier awareness block.

6. **Gather and filter tools** (`gateway-manager.ts:278-339`): Collects MCP tools + connector tools, then runs `trustEngine.filterTools()` against the policy's allow/block patterns. For `'local'` tier, all tools pass. For `'public'`, zero tools. For `'group'`, whitelist-only (allow patterns carve exceptions from default-deny). For other tiers, explicit block overrides allow.

7. **Build conversation** (`gateway-manager.ts:156-175`): Wraps inbound text with `[GATEWAY MESSAGE]` metadata tags (channel, sender, trust tier, timestamp), prepends session history (up to 10 messages from SessionStore).

8. **Run Claude tool loop** (`gateway-manager.ts:178-185`): Calls `runClaudeToolLoop()` with filtered tools and `maxIterations` capped by trust policy (local=25, owner-dm=15, approved-dm=8, group=5, public=0).

9. **Send response** (`gateway-manager.ts:188-195`): Routes through the channel adapter's `sendMessage()`.

10. **Audit outbound** (`audit-log.ts:74-89`): Logs response with tool call count and processing duration.

11. **Memory extraction** (`gateway-manager.ts:204-214`): Only if `policy.memoryWrite` is true (local and owner-dm tiers only).

12. **Unified inbox capture** (`gateway-manager.ts:217-222`): Ingests the message for DLP, triage, and context stream.

### 3. Trust tier policy enforcement

The five trust tiers and their capabilities:

| Tier | Max Iterations | Tool Access | Memory R/W | Desktop | Scheduler | Rate/min |
|------|---------------|-------------|------------|---------|-----------|----------|
| `local` | 25 | All (`*`) | R+W | Yes | Yes | 999 |
| `owner-dm` | 15 | All except `ui_automation_*`, `system_management_*`, `run_powershell`, `execute_powershell` | R+W | No | Yes | 30 |
| `approved-dm` | 8 | `firecrawl_*`, `web_search`, `scrape_url`, `calendar_get_*`, `draft_communication`, `gateway_send_message` | R only | No | No | 10 |
| `group` | 5 | `firecrawl_*`, `web_search`, `scrape_url` (whitelist-only) | None | No | No | 5 |
| `public` | 0 | None | None | No | No | 3 |

### 4. Pairing flow for unknown senders (`trust-engine.ts:284-310`, `gateway-manager.ts:245-272`)

When trust resolves to `'public'`:
1. Trust engine generates an 8-character cryptographic code using `crypto.randomInt()` over a 32-character alphabet.
2. Code is sent back to the sender with instructions to enter it in the desktop app.
3. Desktop UI shows pending pairings via `gateway:get-pending-pairings` IPC.
4. User approves via `gateway:approve-pairing` IPC, assigning a trust tier (default `'approved-dm'`).
5. Identity is saved to `{userData}/gateway/identities.json` (vault-encrypted).

### 5. Consent gate for proactive messages (`consent-gate.ts:45-82`)

When Claude uses the `gateway_send_message` tool to send a proactive message, `requireConsent()` is called:
- Auto-deny if integrity system is in safe mode.
- Auto-deny if no renderer window is available.
- Sends a confirmation request to the renderer via `desktop:confirm-request` IPC.
- 30-second timeout with auto-deny.

### 6. Integrity subsystem (parallel protection layer)

The integrity system runs orthogonally to the gateway trust flow:

1. **Core Laws verification** (`integrity/index.ts:140-200`): On startup, HMAC-SHA256 of canonical laws text (empty-string form) is compared against the signed manifest. Mismatch triggers auto-recovery (re-sign); failure enters safe mode.

2. **Identity verification** (`integrity/index.ts:209-223`): Agent config is HMAC-signed after legitimate changes; external tampering is detected.

3. **Memory watchdog** (`integrity/memory-watchdog.ts:90-117`): Diff computation against signed snapshots detects added/removed/modified memories. Changes are surfaced to the agent via system prompt injection.

4. **Meta-signature** (`integrity/index.ts:55-73`): HMAC over the entire manifest body prevents individual field replacement attacks.

5. **cLaw attestation** (`claw-attestation.ts:86-108`): For P2P agent network, each outbound message includes an Ed25519-signed attestation proving the agent operates under valid Fundamental Laws. Verification checks: laws hash match, signature validity, 5-minute freshness window.

## IPC Channels Used

| Channel | Direction | Purpose |
|---------|-----------|---------|
| `gateway:get-status` | Renderer -> Main | Get gateway status (enabled, channels, paired identities count) |
| `gateway:set-enabled` | Renderer -> Main | Enable/disable gateway; creates Telegram adapter if token configured |
| `gateway:get-pending-pairings` | Renderer -> Main | List pending pairing requests for UI display |
| `gateway:get-paired-identities` | Renderer -> Main | List all paired contacts |
| `gateway:approve-pairing` | Renderer -> Main | Approve a pairing code with optional trust tier |
| `gateway:revoke-pairing` | Renderer -> Main | Revoke a paired identity by ID |
| `gateway:get-active-sessions` | Renderer -> Main | Count of active conversation sessions |
| `integrity:get-state` | Renderer -> Main | Full integrity state (laws, identity, memories, safe mode) |
| `integrity:is-safe-mode` | Renderer -> Main | Boolean safe mode check |
| `integrity:verify` | Renderer -> Main | Run full verification and return summary |
| `integrity:reset` | Renderer -> Main | Re-sign everything and exit safe mode |
| `integrity:acknowledge-memory-changes` | Renderer -> Main | Mark memory changes as discussed |
| `desktop:confirm-request` | Main -> Renderer | Push consent confirmation to UI |

## State Changes

| State | Trigger | Effect |
|-------|---------|--------|
| Sender trust tier resolved | Every inbound message | Determines tool access, memory permissions, iteration cap |
| Rate limit exceeded | Too many messages from one sender | Silent drop (no response) |
| Pairing code generated | First message from unknown sender | Code stored in pendingPairings map (15min TTL) |
| Identity paired | User approves code in desktop UI | Identity saved to vault-encrypted JSON; future messages get assigned tier |
| Safe mode entered | Core law HMAC mismatch or manifest meta-signature invalid | All consent-gated actions auto-denied; reduced capabilities |
| Memory changes detected | Memory files differ from signed snapshot | Change report injected into system prompt for agent awareness |
| Session expired | No activity for 4 hours | Session pruned from SessionStore |

## Error Scenarios

| Scenario | Behavior |
|----------|----------|
| Trust resolution throws | Fails CLOSED to `'public'` tier (most restrictive) |
| Channel adapter crashes during poll | Poll loop catches error, backs off 5s, retries |
| Claude tool loop throws | Error response sent to sender: "Sorry, I ran into an issue..." |
| Adapter fails to send response | Error is caught silently (adapter might be down) |
| Memory extraction fails | Warning logged, does not block response delivery |
| Rate limit map exceeds 10k entries | New senders rejected (DoS protection) |
| Audit log write fails | Warning logged, does not block message processing |
| Vault locked during identity save | Plaintext fallback for identity persistence |
| Passphrase-derived HMAC key unavailable | Integrity checks limited; safe mode may trigger |
| Manifest meta-signature invalid | Safe mode entered; user prompted to reset via UI |
