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
whose tool loop called:

```python
resp = client.messages.create(**kwargs)   # L3667 — NO gate
```

directly, once per iteration, with the full growing conversation (`convo`) and tool results.
The only outbound protection on that path was `_pii_redact` / `_scrub_pii` — **regex-only**
(SSN, CC, phone, email, street). The gate's Layers 2–4 (Presidio NER, embedding similarity for
*contextual* PII like "my son lives with me on weekends", and the financial/medical/legal/vault
keyword tiers) **never ran on the main path.** A user typing *"my A1C came back at 9.2, what
should I change about my medication?"* — no SSN, no card, no email — sailed straight to Anthropic
cloud. The documented guarantee ("sensitive data stays on your device… unless the content is
classified as public") depends on that classifier, and the classifier wasn't in the loop.

`_evaluate_output()` (task grader) had the same hole: it shipped `goal + output` — whatever the
task touched, including file/vault reads — to Claude ungated.

**Patch — route both through the same fail-closed wrapper as `model_router` (R3's single
enforcement point):**

```python
kwargs = _seal_or_block(kwargs, "anthropic")   # every loop iteration
resp   = client.messages.create(**kwargs)
```

`_seal_or_block` raises (blocking the send) if the gate errors or its startup self-test failed,
and returns the sealed payload otherwise. `_gate_messages` preserves `tool_use`/`tool_result`
block structure (only `type == "text"` parts are rewritten), so the Anthropic tool protocol is
not corrupted. This is the highest-impact fix in the review: it closes the boundary for chat,
channels, scheduled tasks, and orchestrator workers in one place.

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
Wrapping the internal-prompt sites is low-risk follow-up; they were left for a focused pass to
avoid destabilizing the creative pipeline in a security review I can't live-integration-test
against Gemini.

### F3 — HIGH — Credit-card regex inconsistency between security layers

**File:** `services/sensitivity_classifier.py:45`.

The classifier used `\b(?:\d[ -]?){13,16}\b` while `core/__init__.py:678` (`_CC_RE`) used
`{13,19}`. A 17–19-digit card (some prepaid / UnionPay lengths) classified **PUBLIC** by the
egress gate's classifier while core's PII redactor treated it as a card — the two layers
disagreeing on what a card *is*, a live exfiltration seam depending on call order. **Patched** to
`{13,19}`; verified `sc._CC_RE.pattern == core._CC_RE.pattern` at runtime.

### F4 — MEDIUM — Egress decisions logged via `print()` (invisible under tray launch)

**File:** `services/egress_gate.py:119`. The most security-sensitive log line in the codebase —
every cloud allow/block verdict — went to `print()`. Under `pythonw.exe` / a detached tray launch
(no console) those writes are discarded, an invisible audit gap. **Patched** to
`logging.getLogger("friday.egress")` (INFO for allow, WARNING for block, so a withheld leak is
visible even at a raised level). The JSONL audit sink was already correct and is unchanged.
*(This was P2/HIGH in `FRIDAY_CODEBASE_STORM_REPORT.md` and had not been applied.)*

### F5 — MEDIUM — Chat history write was neither atomic nor locked

**File:** `core/__init__.py:_save_chat_history`. Settings use a fsync+temp+rename atomic write;
chat history used a bare `write_text()` with **no lock**. Flask is `threaded=True`, so two
concurrent chats clobber each other's appends, and a crash / full disk mid-write leaves a
half-written `chat_history.json` — on next boot `_load_chat_history()` silently returns `[]` and
the user's history is **gone**. **Patched** to a locked, atomic write (temp + fsync + `replace`)
under a new `_CHAT_HISTORY_LOCK`, matching the settings durability guarantee. Directly answers the
Chaos Engineer ("crash mid-operation") and Data Security ("what happens to user data on crash").

### F6 — MEDIUM — No GDPR/CCPA data-rights mechanism for non-technical users

Friday stores everything under `~/.friday` and phones home to **nothing** (verified — see Privacy
Regulator), so the raw material for data rights exists but a regular person can't act on it.
**Patched** by adding two CLI commands:

- `friday export` — bundles all of `~/.friday` (minus audio cache / logs) into a timestamped,
  portable `friday-data-export-<ts>.zip` (right of access / portability).
- `friday erase [--yes]` — permanently deletes `~/.friday` behind a typed `ERASE` confirmation,
  showing the blast radius first (right to erasure). Because Friday is device-local with no
  server copy, this is a *complete* erasure.

Additive, server-independent, no new dependencies (stdlib `zipfile`/`shutil`).

### F7 — LOW — Vault data-at-rest state invisible in `friday doctor`

**File:** `cli.py:cmd_health`. The health check reported *credential* encryption but not vault
**data-at-rest** state — the one thing a privacy-conscious user most needs to see, and previously
only visible in a boot banner that's gone under a tray launch. **Patched** with a loud line:
green `encrypted (AES-256-GCM, passphrase armed)` when a passphrase is set, bold-yellow
`PLAINTEXT — set FRIDAY_VAULT_PASSPHRASE` otherwise.

### F8 — LOW — Login template `str.replace()` XSS-prone pattern

**File:** `core/__init__.py`. `LOGIN_HTML.replace('{{ error }}', error)` bypasses Jinja
auto-escaping. No live XSS today (both banners are fixed strings), but the day someone echoes the
attempted username it becomes reflected XSS. **Patched** with `_login_error_html()` — an allowlist
of the two known banners; anything else is `markupsafe.escape`-d to inert text.

### What was checked and found SOUND (no change needed)

- **Auth fail-closed** (`check_auth`, `login_required`, `login`): a non-loopback request with no
  `FRIDAY_REMOTE_KEY` is denied 403; loopback trust intact. Keyless non-loopback **bind** refused
  at startup. Login throttle SQLite-persisted, `hmac.compare_digest`. Token rotation (24 h + grace)
  correct.
- **Vault crypto** (`vault_crypto.py`): AES-256-GCM + Argon2id (256 MiB/4 passes) + MAGIC-as-AAD
  (no version downgrade). `roundtrip_ok` proves recoverability before migration removes plaintext.
- **Egress `_seal_or_block`** (R3): genuinely fail-closed — gate raises or self-test-fail both
  block the send.
- **Channel funnel**: inbound allowlist closed-by-default; reply gated (`gate_reply`) with a
  correct fail-closed backstop; raw exceptions never echoed to external channels.
- **Settings**: corrupt `settings.json` → falls back to `DEFAULT_SETTINGS`; atomic write. **Chaos
  "corrupt settings" scenario already handled.**
- **Onboarding**: voice-first, zero-cloud, atomic state writes, null-safe, key-store-failure honest.
- **Credential store**: tiered vault→DPAPI→plaintext with a plaintext **warning** and locked file
  perms (chmod 0600 / icacls).
- **SQL**: dynamic `execute()` in `marketplace.py`/`cost_meter.py` builds fragments from hard-coded
  allowlists; values always parameterized. No injection.

---

## PART B — INTEGRATION REPORT

### B1. Release Readiness Scorecard (post-fix)

| Area | Score | Notes |
|------|:----:|-------|
| Install experience | 7/10 | `pip install -e .` clean, cross-platform; extras well-segmented. Heavy `[all]` (torch-free) OK. `friday doctor` is genuinely useful. −3: no bundled Gemma auto-pull; `start.bat` still the documented key path. |
| First-run onboarding | 7/10 | Voice-first state machine, works with zero keys, atomic + null-safe. −3: no vault-passphrase step (see Blocker B2-#3); wizard depends on browser reaching `/`. |
| Core chat functionality | 9/10 | Tool loop, compaction, governance, cost metering — mature. **+ now egress-gated (F1).** |
| Voice mode | 7/10 | Tier-1 local (faster-whisper + Piper, CPU) is the default and wired; Gemini Live opt-in. −3: NeMo GPU silently falls back to CPU; Live model-ID drift risk. |
| Creative tools | 6/10 | Music/image/video + provenance + QA gates present. −4: Gemini creative calls not yet gated (F2/H1); image bytes uninspectable. |
| News / briefings | 8/10 | RSS-based (no CAPTCHA scraping), scheduled, archived. |
| Security posture | 8/10 | Was 6 — **F1/F2/F3 raise it materially.** Real layered defense, now enforced on the main path. −2: image egress + internal-prompt Gemini sites remain (H1); vault plaintext-by-default (B2-#3). |
| Documentation | 7/10 | Deep architecture/threat/onboarding docs. −3: quick-start still points at `start.bat`; no plain-language data-rights doc yet (CL