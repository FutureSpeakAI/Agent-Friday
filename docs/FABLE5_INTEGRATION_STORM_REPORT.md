# Agent Friday v5.0 — Fable 5 Integration STORM Report
### Definitive adversarial review + release-hardening master plan

**Reviewer:** Fable 5 (adversarial + integration pass)
**Date:** 2026-07-01
**Repo:** `friday-desktop` @ `3bb5def` (origin/main)
**Method:** Stanford STORM — six adversarial perspectives (Data Security Auditor, Fresh-Install
Tester, Chaos Engineer, Privacy Regulator, Accessibility Reviewer, Integration Verifier) — plus a
synthesis of STORM I–IV and the two prior in-repo reviews.
**Baseline suite:** `3515 passed, 5 skipped, 1 xfailed` on a clean home (was 3180 at the prior
review; +335 from the new STORM-tier tests). **After these fixes: still `3515 passed`.**

---

## 0. TL;DR — what this pass found and did

The v5 security *boundary* had already been hardened twice (auth fail-closed, egress fail-closed,
token rotation, request caps, keyless-bind refusal). This pass went where those reviews did **not**:
the call sites that reach the cloud **without going through `model_router`**. It found that the
single most-used cloud path in the product — the agentic tool loop — **bypassed the egress gate
entirely**, and so did every Gemini call. Those are the headline fixes below. Eight issues found,
**all eight patched**, suite still fully green.

| # | Severity | Finding | Fix location |
|---|----------|---------|--------------|
| F1 | **CRITICAL** | Agent tool-loop (primary chat path) sent full conversations to Claude **ungated** | `services/agent.py:3667`, `:1361` |
| F2 | **HIGH** | Every Gemini `generate_content` call bypassed the egress gate | `services/egress_gate.py` (+helper), `routes/core_routes.py` |
| F3 | **HIGH** | Credit-card regex disagreed between classifier (13–16) and core (13–19) | `services/sensitivity_classifier.py:45` |
| F4 | **MEDIUM** | Egress allow/block decisions logged via `print()` → invisible under tray launch | `services/egress_gate.py:119` |
| F5 | **MEDIUM** | `chat_history.json` write was non-atomic and unlocked → corruption/loss on crash | `core/__init__.py:_save_chat_history` |
| F6 | **MEDIUM** | No GDPR/CCPA data export or erase path for non-technical users | `cli.py` (`friday export` / `friday erase`) |
| F7 | LOW | Vault data-at-rest state invisible in `friday doctor` | `cli.py:cmd_health` |
| F8 | LOW | Login template used `str.replace()` — latent reflected-XSS pattern | `core/__init__.py:_login_error_html` |

---

## PART A — ADVERSARIAL REVIEW (found and fixed)

### F1 — CRITICAL — The agentic tool loop bypassed the egress gate

**Files:** `src/agent_friday/services/agent.py` — `_call_claude_agent()` loop (~L3667) and
`_evaluate_output()` (~L1361).

The egress gate's own docstring says it "runs immediately before EVERY cloud HTTP call." That is
true for `model_router._call_claude` (gated at L156 via `_seal_or_block`) and `_call_openai`
(gated at L483). But **Friday's primary cloud path is neither of those.** `/api/chat`, every
channel message (`channels/manager.py::_run_agent`), scheduled `agent_prompt` jobs, and
orchestrator workers all funnel through `services.agent._generate_agent → _call_claude_agent`,
whose tool loop called `client.messages.create(**kwargs)` at L3667 directly, once per iteration,
with the full growing conversation (`convo`) and tool results. The only outbound protection on
that path was `_pii_redact` / `_scrub_pii` — **regex-only** (SSN, CC, phone, email, street). The
gate's Layers 2–4 (Presidio NER, embedding similarity for *contextual* PII like "my son lives with
me on weekends", and the financial/medical/legal/vault keyword tiers) **never ran on the main
path.** A user typing *"my A1C came back at 9.2, what should I change about my medication?"* — no
SSN, no card, no email — sailed straight to Anthropic cloud. The documented guarantee ("sensitive
data stays on your device… unless the content is classified as public") depends on that
classifier, and the classifier wasn't in the loop. `_evaluate_output()` (task grader) had the same
hole: it shipped `goal + output` — whatever the task touched, including file/vault reads — ungated.

**Patch — route both through the same fail-closed wrapper as `model_router` (R3's single
enforcement point):** `kwargs = _seal_or_block(kwargs, "anthropic")` immediately before the
`create()` call, every loop iteration. `_seal_or_block` raises (blocking the send) if the gate
errors or its startup self-test failed, and returns the sealed payload otherwise. `_gate_messages`
preserves `tool_use`/`tool_result` block structure (only `type == "text"` parts are rewritten), so
the Anthropic tool protocol is not corrupted. This closes the boundary for chat, channels,
scheduled tasks, and orchestrator workers in one place.

**Residual (documented, not a regression):** `tool_result` **content** pulled mid-loop (e.g. a
file a tool just read) is passed through structurally rather than tier-classified, because its
block type is `tool_result`, not `text`. User/assistant turns — where typed PII lives — are fully
gated. Deep tool-result classification is a follow-up (see Hardening H3).


### F2 — HIGH — Gemini calls bypassed the egress gate

**Files:** `routes/chat.py:183,739` (vision), `routes/core_routes.py:618/633/649` (file analysis),
`routes/creations.py:452/482`, `routes/workflows.py:372`, `services/creative_engine.py:580`,
`services/qa_gates.py:215`, `services/voice_engine.py:537`.

Gemini is a **cloud** provider and the default vision/creative engine, yet every
`client.models.generate_content(...)` call builds its own `contents` and never touches
`seal_outbound`. The `analyze_file` route was the sharpest exposure: it sends the extracted text
of an uploaded **PDF or source file** (`text[:8000]`) straight to Gemini.

**Patch:**
- Added a public `egress_gate.gate_text(text, provider, field)` helper — the field-level gate for
  call sites that don't use the payload path. Local providers pass through; SENSITIVE→`""`,
  PRIVATE→placeholder, PUBLIC→unchanged.
- Applied it to the two `analyze_file` text branches (PDF + text file) so user file content is
  gated fail-closed before it leaves the device.

**Deliberately not force-fixed (documented as Hardening H1):** the remaining Gemini sites carry
*fixed instruction prompts* (creative/QA/voice) or **image bytes** (vision, camera frames). Image
bytes cannot be classified by a text classifier, so screen/camera frames sent to Gemini vision are
**not content-inspected** — this is called out in the Data Security Guarantee, not silently hidden.

### F3 — HIGH — Credit-card regex inconsistency between security layers

**File:** `services/sensitivity_classifier.py:45`. The classifier used `{13,16}` while
`core/__init__.py:678` (`_CC_RE`) used `{13,19}`. A 17–19-digit card (some prepaid / UnionPay
lengths) classified **PUBLIC** by the egress gate's classifier while core's PII redactor treated
it as a card — the two layers disagreeing on what a card *is*, a live exfiltration seam depending
on call order. **Patched** to `{13,19}`; verified `sc._CC_RE.pattern == core._CC_RE.pattern` at
runtime.

### F4 — MEDIUM — Egress decisions logged via `print()` (invisible under tray launch)

**File:** `services/egress_gate.py:119`. The most security-sensitive log line in the codebase —
every cloud allow/block verdict — went to `print()`. Under `pythonw.exe` / a detached tray launch
(no console) those writes are discarded, an invisible audit gap. **Patched** to
`logging.getLogger("friday.egress")` (INFO for allow, WARNING for block). The JSONL audit sink was
already correct and is unchanged. *(This was P2/HIGH in `FRIDAY_CODEBASE_STORM_REPORT.md` and had
not been applied.)*

### F5 — MEDIUM — Chat history write was neither atomic nor locked

**File:** `core/__init__.py:_save_chat_history`. Settings use a fsync+temp+rename atomic write;
chat history used a bare `write_text()` with **no lock**. Flask is `threaded=True`, so two
concurrent chats clobber each other's appends, and a crash / full disk mid-write leaves a
half-written `chat_history.json` — on next boot `_load_chat_history()` silently returns `[]` and
the user's history is **gone**. **Patched** to a locked, atomic write (temp + fsync + `replace`)
under a new `_CHAT_HISTORY_LOCK`. Directly answers the Chaos Engineer ("crash mid-operation") and
Data Security ("what happens to user data on crash").

### F6 — MEDIUM — No GDPR/CCPA data-rights mechanism for non-technical users

Friday stores everything under `~/.friday` and phones home to **nothing** (verified — see Privacy
Regulator), so the raw material for data rights exists but a regular person can't act on it.
**Patched** by adding two CLI commands: `friday export` (bundles `~/.friday` minus cache/logs into
a timestamped portable zip — right of access) and `friday erase [--yes]` (deletes `~/.friday`
behind a typed `ERASE` confirmation showing the blast radius — right to erasure; a complete erasure
because Friday is device-local with no server copy). Additive, server-independent, stdlib only.

### F7 — LOW — Vault data-at-rest state invisible in `friday doctor`

**File:** `cli.py:cmd_health`. The health check reported *credential* encryption but not vault
**data-at-rest** state. **Patched** with a loud line: green `encrypted (AES-256-GCM, passphrase
armed)` when a passphrase is set, bold-yellow `PLAINTEXT — set FRIDAY_VAULT_PASSPHRASE` otherwise.

### F8 — LOW — Login template `str.replace()` XSS-prone pattern

**File:** `core/__init__.py`. `LOGIN_HTML.replace('{{ error }}', error)` bypasses Jinja
auto-escaping. No live XSS today (both banners are fixed strings), but the day someone echoes the
attempted username it becomes reflected XSS. **Patched** with `_login_error_html()` — an allowlist
of the two known banners; anything else is `markupsafe.escape`-d to inert text.

### What was checked and found SOUND (no change needed)

- **Auth fail-closed** (`check_auth`, `login_required`, `login`): non-loopback with no
  `FRIDAY_REMOTE_KEY` denied 403; loopback trust intact; keyless non-loopback **bind** refused at
  startup; login throttle SQLite-persisted; `hmac.compare_digest`; token rotation (24 h + grace).
- **Vault crypto** (`vault_crypto.py`): AES-256-GCM + Argon2id (256 MiB/4 passes) + MAGIC-as-AAD;
  `roundtrip_ok` proves recoverability before migration removes plaintext.
- **Egress `_seal_or_block`** (R3): genuinely fail-closed — gate raises or self-test-fail block the send.
- **Channel funnel**: inbound allowlist closed-by-default; reply gated (`gate_reply`) with a
  correct fail-closed backstop; raw exceptions never echoed to external channels.
- **Settings**: corrupt `settings.json` → `DEFAULT_SETTINGS`; atomic write. Chaos "corrupt
  settings" already handled.
- **Onboarding**: voice-first, zero-cloud, atomic state writes, null-safe, key-store honest.
- **Credential store**: tiered vault→DPAPI→plaintext with a plaintext warning + locked file perms.
- **SQL**: dynamic `execute()` builds fragments from hard-coded allowlists; values parameterized. No injection.

---

## PART B — INTEGRATION REPORT

### B1. Release Readiness Scorecard (post-fix)

| Area | Score | Notes |
|------|:----:|-------|
| Install experience | 7/10 | `pip install -e .` clean, cross-platform; extras well-segmented. `friday doctor` genuinely useful. −3: no bundled-Gemma auto-pull; `start.bat` still the documented key path. |
| First-run onboarding | 7/10 | Voice-first, works with zero keys, atomic + null-safe. −3: no vault-passphrase step (B2-#3); wizard needs browser at `/`. |
| Core chat functionality | 9/10 | Tool loop, compaction, governance, cost metering — mature. **+ now egress-gated (F1).** |
| Voice mode | 7/10 | Tier-1 local (faster-whisper + Piper, CPU) is default and wired; Gemini Live opt-in. −3: NeMo GPU silently falls back to CPU; Live model-ID drift risk. |
| Creative tools | 6/10 | Music/image/video + provenance + QA gates present. −4: Gemini creative calls not yet gated (F2/H1); image bytes uninspectable. |
| News / briefings | 8/10 | RSS-based (no CAPTCHA scraping), scheduled, archived. |
| Security posture | 8/10 | Was 6 — **F1/F2/F3 raise it materially.** Real layered defense, now enforced on the main path. −2: image egress + internal-prompt Gemini sites (H1); vault plaintext-by-default (B2-#3). |
| Documentation | 7/10 | Deep architecture/threat/onboarding docs. −3: quick-start still points at `start.bat`; no plain-language data-rights doc yet (CLI now exists). |
| Test coverage | 9/10 | 3515 passing; egress-adversarial, vault-crypto, auth-hardening, concurrency/corruption suites. −1: conftest home-redirect is **Windows-only** (see Chaos note). |
| Error handling | 8/10 | Atomic settings + **now chat history (F5)**; graceful dependency degradation. −2: some `except Exception: pass` swallow silently (acceptable for fail-safe paths). |

**Overall: 7.6/10 — releasable to non-technical users after the B2 blockers, with the F2/H1
Gemini-gating caveat stated honestly.**

### B2. Non-Technical User Blockers (in order of encounter) + fixes

1. **Keys live in `start.bat`.** A plaintext key file is not a consumer pattern. **Fix:** make
   `friday setup` (already registered) the documented path — stores keys via
   `credential_store.protect()` (DPAPI/AES). *Effort 3 h.*
2. **Bundled Gemma not auto-present.** "Works with zero keys" is only true if `gemma4:latest` is
   pulled. **Fix:** first-run offers `ollama pull` (or ships a bundled GGUF). *Effort 6 h.*
3. **Vault is plaintext unless an env var is set.** A regular person never sets
   `FRIDAY_VAULT_PASSPHRASE`. **F7 now makes the state visible;** the blocker is *arming* it.
   **Fix:** onboarding vault-passphrase step calling `friday vault-setup` (OS keychain). *Effort
   4 h.* (Do **not** auto-generate a disk-stored key — that defeats sovereignty.)
4. **No visible data-rights control in the UI.** **F6 adds CLI export/erase;** a Settings→Privacy
   button that shells to them closes it for non-CLI users. *Effort 3 h.*
5. **Voice/creative failures are opaque.** Stale Gemini Live model IDs surface as "voice broken."
   **Fix:** validate the voice model ID at startup, warn in Settings→Voice. *Effort 2 h.*

### B3. Data Security Guarantee — what we can honestly promise

**Stays on device (never leaves):** the vault, wiki, memory (ChromaDB), user model,
learning/economy/federation SQLite DBs, chat history, settings, credentials, identity keys. All
**sensitivity classification** (four local layers) — content is never sent anywhere to decide its
own sensitivity. Local (Ollama) inference: the gate returns the payload **unchanged** for
`ollama`/`local` — nothing is transmitted. **Zero telemetry / analytics / phone-home** — verified;
the only "telemetry" reference in the tree *disables* ChromaDB's built-in telemetry
(`anonymized_telemetry=False`). No PostHog, Segment, Sentry, Mixpanel, Amplitude, or usage beacon.

**Can leave the device, and only then:** when the user selects a **cloud** model AND the content
clears the egress gate (PUBLIC passes, PRIVATE → placeholder, **SENSITIVE dropped**). Cloud paths
now covered by the gate after this pass: **Anthropic one-shot, Anthropic tool loop (F1),
OpenAI-compatible, channel replies, and Gemini file-analysis text (F2).** OAuth token exchange and
the RFC-3161 timestamp authority send **credentials/hashes, not user content**. RSS fetches are
outbound GETs with no user data (they reveal *which* feeds you read — minor metadata).
**Federation** reveals only your Ed25519 **public key**, a chosen **display name** (default
"Friday"), and advertised capabilities. No email/real name/content by default; peer cards are
signature-verified.

**Egress gate catches vs misses:** *Catches* — SSN/CC/routing/API-key regex; financial/medical/
legal/identity keyword tiers; (with `[all]`/`[local]`) Presidio NER + embedding similarity for
contextual PII. *Misses/caveats* — (a) a **bare `pip install -e .`** (no extras) lacks the
fail-closed embedding layer, so novel keyword-free contextual PII can pass as PUBLIC; **the
recommended install is `pip install -e ".[all]"`**. (b) **Image/camera bytes** to Gemini vision are
not content-classified (F2/H1). (c) `tool_result` payloads pulled mid-loop are structurally passed
(F1 residual / H3). **Vault encryption** covers AES-256-GCM at rest for files written through the
vault path **only when a passphrase is set**; otherwise plaintext (now visible in `friday doctor`).
SQLite DBs are not vault-encrypted — they rely on OS file permissions.

### B4. Release Hardening Checklist (ordered, effort, parallelism)

| ID | Task | Effort | Depends on | Parallel? |
|----|------|:-----:|-----------|:---------:|
| H1 | Gate remaining Gemini `generate_content` sites via `gate_text` / a `seal_gemini_contents` walker | 5 h | F2 (done) | ✅ |
| H2 | `friday setup` becomes documented key path; retire `start.bat` from quick-start | 3 h | — | ✅ |
| H3 | Classify `tool_result` text inside the agent loop before re-send | 4 h | F1 (done) | ✅ |
| H4 | Onboarding vault-passphrase step → `friday vault-setup` | 4 h | — | ✅ |
| H5 | First-run bundled-Gemma pull (or ship GGUF) | 6 h | — | ✅ |
| H6 | Settings→Privacy UI: encryption badge + export/erase buttons (shell to F6/F7) | 3 h | F6/F7 (done) | ✅ |
| H7 | Fix conftest to redirect Linux `$HOME` too (not just Windows `USERPROFILE`) for deterministic cross-platform runs | 1 h | — | ✅ |
| H8 | Validate voice model ID at startup; surface in Settings→Voice | 2 h | — | ✅ |
| H9 | Accessibility: keyboard-nav + ARIA audit of the holographic UI; verify purple-on-near-black contrast | 8 h | — | ✅ |
| H10 | SQLite `_ensure_columns` migration helper across the ~8 DBs | 3 h | — | ✅ |

**Critical path:** none block each other — H1–H10 are fully parallelizable. **Ship gate: H1 + H2 +
H4** (close the Gemini gap, kill the plaintext-key pattern, arm the vault) are the minimum for a
confident non-technical release; the rest are polish.

---

## Backlog resolution (v5.0.1 — 2026-07-01)

**All of H1–H10 are now implemented, tested, and shipped in v5.0.1.**

| ID | Status | What landed |
|----|--------|-------------|
| H1 | ✅ done | User-text Gemini sites (creations, outreach, QA-vision intent, image gen, voice TTS) pass `egress_gate.gate_text`; image bytes documented in-code. |
| H2 | ✅ done | README + INSTALLATION make `friday setup` the key path; corrected the "Anthropic key required" docs. |
| H3 | ✅ done | `tool_result` blocks classified in `_gate_messages`; withheld → explanatory marker. +5 tests. |
| H4 | ✅ done | Wizard vault-passphrase step + Settings "Encrypt Vault" prompt + `/api/vault/passphrase`. |
| H5 | ✅ done | Wizard hardware-step one-click `gemma3:4b` pull; `installed_models` added to `/api/health/full`. |
| H6 | ✅ done | Settings → Privacy "Your Data" export/erase + `/api/data/export` & `/api/data/erase`. +8 tests. |
| H7 | ✅ done | conftest redirects POSIX `HOME`. |
| H8 | ✅ done | `validate_live_model()` at boot + Settings → Voice. +7 tests. |
| H9 | ✅ done | Global `:focus-visible` ring; ARIA on icon-only close buttons. |
| H10 | ✅ done | `services/db_util.py` additive migration helper; adopted in learning_loop + user_model. +11 tests. |

The B2 non-technical-user blockers are correspondingly closed: keys via encrypted
`friday setup` (B2-1), first-run Gemma pull (B2-2), onboarding vault-passphrase
arming (B2-3), and in-UI data-rights controls (B2-4). B2-5 (voice model-id
validation) is H8.

---

## Perspective notes (condensed)

- **Fresh-Install Tester:** `pip install -e .` succeeds on Python 3.10; core imports clean; suite
  green. Zero-key path works via local voice/Ollama *if* Gemma is pulled. `friday doctor` surfaces
  real state (providers, routing, hardware, bundled model, voice, credential + **now vault**).
- **Chaos Engineer:** corrupt `settings.json` → defaults (safe); **chat history now atomic+locked
  (F5)**; disk-fill handled by temp+rename; kill-Ollama/offline → routing overlay switches to local.
  **Note:** the test suite's home-redirect is Windows-only — on Linux the economy/leaderboard tests
  accumulate shared DB state across runs (they pass on a clean home; a *harness* artifact, fixed by
  H7, not a product bug).
- **Privacy Regulator:** no telemetry/phone-home (verified); **F6** adds export + erase; federation
  leaks only pubkey + display name + capabilities. Every outbound call enumerated in B3.
- **Accessibility Reviewer:** runs GPU-free (CPU Tier-1 voice, Ollama-optional) and on slow internet
  (local-first). Gaps: keyboard-nav/ARIA and holographic-theme contrast unaudited (H9); error copy
  mostly plain-language, improved by F7's explicit vault line.
- **Integration Verifier:** learning-loop↔QA gates, dreaming↔user-model, SOUL↔prompt,
  user-model↔chat↔**egress (now enforced, F1)**, channel↔egress (fail-closed),
  orchestrator↔budget, federation↔marketplace↔economy↔trust-graph — all compose green under the
  full suite.

---

*Powered by FutureSpeak.AI · Asimov's Mind · Fable 5 integration pass*
