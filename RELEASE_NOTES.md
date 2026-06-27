# Agent Friday v4.5.0 — Creator Economy, Federation & Security Hardening

Agent Friday is a sovereign AI assistant that runs entirely on your machine. v4.5.0 is a major release with three headline features: a full creator economy pipeline, a federated identity and marketplace layer, and a comprehensive security hardening pass. Every capability runs locally by default; cloud APIs are optional.

---

## What's New

### Creator Economy — Layer 1: Production Engine

The Studio workspace is now a real production system, not just a chat interface.

- **Music generation** via Google Lyria 3 — compose original tracks with prompt-driven style, tempo, and mood controls; graceful demo mode when the API key is absent
- **Video generation** via Google Veo — text-to-video and image-to-video with configurable duration and aspect ratio
- **Image generation** via Google Imagen (Nano Banana Pro / Pro 2) — consistent character and scene rendering across a production
- **FFmpeg timeline composition** — stitch music, video, and image sequences into a finished timeline export; Windows `drawtext`/GIF filter quirks handled
- **Scene DNA** — per-production creative memory that keeps visual style, character appearances, and tone consistent across generations
- **Series Bible** — define cast, world rules, and recurring motifs; cast looks propagate automatically into every new generation
- **QA gates** — configurable quality thresholds block low-confidence outputs before they enter the timeline
- **Full provenance chain** — every generated asset carries a C2PA-aligned content credential, signed with the user's Ed25519 key, before it leaves the pipeline

### Creator Economy — Layer 2: Ownership & Provenance

- **C2PA-aligned content credentials** — assets carry a signed provenance manifest (who created it, when, with which model, under what license)
- **Ed25519 identity signing** — each Friday instance has a persistent keypair; credentials are cryptographically bound to the creator's identity
- **Ownership registry** — local registry tracks all signed assets; supports transfer and co-creator attribution
- **License enforcement** — per-asset license terms (CC0, CC-BY, proprietary, etc.) are embedded in credentials and checked at distribution time

### Creator Economy — Layer 3: Federation Protocol

- **Encrypted P2P transport** — peer connections use X25519 key exchange + ChaCha20-Poly1305 AEAD; no plaintext content on the wire
- **Peer registry** — discover and connect to other Friday instances on the local network or via manual invite
- **Marketplace** — two-layer commons/commerce model: free sharing in the commons tier, optional paid listings in the commerce tier
- **Positron/Negatron economy** — net-charge accounting (ψ/η/Q) with a genesis bonus for early creators; charge accrues from community engagement, not from the platform
- **Asimov-governed defederation** — community content policy packs define moderation rules (H1–H4 harm floors, family mode); peers who violate policy are defederated by consensus, not by central authority
- **Moderation** — H1–H4 graduated harm classification; family mode overlays conservative defaults without replacing the base policy

### Security Hardening

Several security issues identified in a pre-release external review have been fixed.

- **Fail-closed egress gate** — `seal_outbound()` is called before every cloud API request; a 4-layer classifier (sensitivity → PII → context → policy) blocks or scrubs data that shouldn't leave the device; the gate fails closed (blocks on error) rather than open
- **FRIDAY_PASSWORD triple-coupling fix** — the password previously controlled vault decryption, API authentication, AND network binding simultaneously, making it impossible to use a strong vault passphrase without also locking out local API callers; these three concerns are now decoupled
- **Static file serving security hole fixed** — path traversal in the static file handler was closed; requests are now canonicalized and validated against the intended root before serving
- **Flask API session token authentication** — the `/api/*` surface now requires a session token for non-read endpoints; tokens are issued at startup and stored in the OS keychain
- **TLS requirement for non-loopback binding** — if the server binds to a non-loopback address, TLS is now required; plain HTTP is rejected with a clear error pointing to `friday tls-init`
- **Honest threat model** — `THREAT_MODEL.md` documents what the security model actually protects (local malware, network eavesdropping on LAN) and what it does not (physical access, OS-level compromise, supply chain)
- **HMAC keychain** — governance and session keys are stored in the OS keychain (Windows Credential Manager / macOS Keychain) rather than on disk

### Package & CLI Restructure

- **`src/agent_friday/` package layout** — the codebase is now a proper Python package installable with `pip install -e .`
- **`friday` CLI** — `friday start` launches the server, `friday doctor` checks dependencies and key availability, `friday version` prints build info, `friday vault-setup` initializes the encrypted vault, `friday tls-init` generates a self-signed TLS cert for LAN binding
- **Professional README** — includes architecture overview, quick-start guide, and links to YouTube demo videos
- **Community health files** — `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, GitHub issue templates (bug report, feature request)

### UI Improvements

- **Studio as social media timeline** — the Studio workspace now renders a scrollable card timeline (similar to a social feed) instead of a flat list; each card is expandable to show full metadata, provenance, and playback controls
- **"Talk to Friday" buttons** — Studio and News workspaces now have a microphone button on each card/article; tapping it starts a voice session pre-seeded with that item's context (e.g. "discuss this article")
- **Voice mode contextual discussion** — when a voice session is triggered from a card, Friday's opening turn references the specific content rather than starting cold

### Infrastructure

- **Internal scheduler** — replaces the previous `while True: sleep(86400)` daily loop with a proper job scheduler (`services/scheduler.py`); supports interval, daily, and weekly triggers; jobs are declared in `schedules.json` and visible in Settings → Scheduled Tasks
- **Cost metering** — every cloud API call is metered (input + output tokens × per-direction price) and stored in a local SQLite `costs.db`; the Cost & Usage view in Settings shows spend by provider and date range
- **Auto-compaction** — long-running agent conversations are automatically compacted (summarize-the-middle) to keep context within model limits without losing important earlier context
- **PreToolUse / PostToolUse lifecycle hooks** — `services/tool_hooks.py` fires registered hooks before and after every tool execution; used internally for cost metering and audit logging, and exposed for user-defined extensions

---

## Breaking Changes

- **FRIDAY_PASSWORD semantics changed** — if you were relying on the password to gate API access, you now need to set a separate session token. Run `friday vault-setup` to migrate.
- **Static asset paths** — if you were loading custom assets from paths outside the `static/` directory via a URL trick, those paths no longer work after the path traversal fix.

---

## Bug Fixes

- Demo-mode guard in `_generate_agent` — keyless `/api/chat/send` no longer returns HTTP 500
- Port-in-use probe no longer uses `SO_REUSEADDR` on Windows (was masking already-bound ports)
- CI test failures from egress gate imports resolved
- Pre-release audit blockers: duplicate `/api/security/*` routes, countdown `days_until` returning `"undefined"`, inline Babel parse error silently blanking the UI

---

## Upgrade Notes

```bash
# Pull latest
git pull origin main

# Re-install dependencies (new packages: pynacl, cryptography updates)
pip install -r requirements.txt

# Re-run vault setup if you use FRIDAY_PASSWORD
friday vault-setup

# Restart the server
friday start
```

---

## Known Limitations

- **Lyria 3 music generation**: the Google AI Python SDK 1.72 does not expose a batch `generate_music` endpoint; multi-track sessions fall back to demo mode (mock audio) until the SDK exposes the API
- **NeMo Tier-2 voice (GPU)**: requires `torch` with CUDA; the default venv ships CPU-only `torch`. Install `torch` with the appropriate CUDA index URL separately if you have an RTX GPU
- **Headroom compression**: no Windows wheel available for `headroom-ai` ≥ 0.21; compression falls back gracefully to a no-op ("0% saved")

---

*Agent Friday is built by FutureSpeakAI. Contributions welcome — see CONTRIBUTING.md.*
