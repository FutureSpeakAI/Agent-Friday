# Agent Friday v5.0 — Adversarial STORM Code Review

**Reviewer:** Fable 5 (adversarial pass)
**Date:** 2026-07-01
**Scope:** v5.0 "Super Agent" transformation — learning loop, memory dreaming, user
modeling, SOUL.md, channel bridges, egress gate, auth, and the systems where they
integrate.
**Method:** Six adversarial perspectives — Hostile User, Security Auditor,
Reliability Engineer, New User, Performance Critic, Integration Tester.

---

## Result summary

| | |
|---|---|
| Full test suite | **3180 passed, 3 skipped, 1 xfailed** (169s) |
| New security regression tests | 4 added (`tests/unit/test_fable5_security.py`), all pass |
| UI rebuild | `index.html` regenerated (878,772 bytes) OK |
| Server boot | Binds `127.0.0.1:3000`, health path served, clean shutdown |
| New findings this pass | **2 CRITICAL/HIGH security + 1 hygiene**, all patched |
| Prior-pass findings (commit `23f3d35`) | 16 across v5 subsystems, verified & retained |

The v5 subsystems had already received a strong hardening pass (committed as
`23f3d35`). This adversarial review went a layer deeper into the **security
boundary itself** and found two fail-**open** holes in the parts of the system
that are supposed to be fail-closed. Both are now patched and covered by tests.

---

## NEW findings (this review)

### FINDING 1 — CRITICAL — Auth fail-OPEN: keyless server is wide open to remote clients
**File:** `src/agent_friday/core/__init__.py` — `check_auth()` (~L1749) and
`login_required()` (~L324)

**Problem.** Both the global `@app.before_request` guard and the `login_required`
decorator contained:

```python
if not _HTTP_AUTH_KEY:
    return None            # check_auth: allow the request
    return f(*args, **kwargs)   # login_required: allow the call
```

`_HTTP_AUTH_KEY` is derived from `FRIDAY_REMOTE_KEY` or, as fallback,
`FRIDAY_PASSWORD`. When **neither** is set (a very common default state — the boot
log even warns the vault passphrase is unset), the key is the empty string and the
guard **allowed every request through with no authentication at all.** Loopback is
auto-trusted by design, so on a laptop this is invisible — but Friday is explicitly
designed to be exposed over a **Cloudflare Tunnel** (per project docs). The instant
the tunnel is up with no key configured, *the entire API — chat, vault-adjacent
routes, channel control, everything — is reachable by anyone on the internet who
has the hostname.* This is the single most dangerous issue found.

**Patch — fail CLOSED.** A non-loopback request that reaches the guard with no key
configured is now **denied** (`403`, JSON for `/api/*`, a plain-text notice
otherwise) instead of allowed. Loopback trust is unchanged, so the local UX does
not regress; the login page and static assets remain reachable so an operator who
later sets a key isn't locked out mid-transition.

**Test:** `test_remote_request_denied_without_key`,
`test_loopback_still_trusted_without_key`.

---

### FINDING 2 — HIGH — Egress gate fail-OPEN on gate error (leaks the payload it exists to protect)
**File:** `src/agent_friday/services/model_router.py` — `_call_claude()` (~L122)
and `_call_openai()` inner `_send()` (~L452)

**Problem.** The egress gate is documented as "the last line of defense" that
"cannot be bypassed without modifying this module," with a hard **fail-closed**
guarantee. But both cloud call sites wrapped it like this:

```python
try:
    kwargs = _seal(kwargs, "anthropic")
except Exception as _eg_err:
    print(f"  [EGRESS] gate error (payload forwarded as-is): {_eg_err}")
resp = client.messages.create(**kwargs)   # ← sends the UN-sealed payload
```

If `seal_outbound` raised for *any* reason — an import failure, a classifier
exception, an OOM in the embedding model — the code printed a warning and then
**sent the original, un-sealed `kwargs` to the cloud anyway.** That is a complete
bypass of the security boundary triggered by the very failure mode the boundary is
supposed to defend against. "Forwarded as-is" was doing exactly the wrong thing.

**Patch — fail CLOSED.** A gate exception now logs at ERROR level and **raises**,
blocking the cloud send entirely. Nothing leaves the device when the gate can't
verify it. Both provider paths (Anthropic + OpenAI-compatible) are fixed
identically.

**Test:** `test_call_claude_fails_closed_when_gate_raises` (asserts the network
`create()` is *never reached* when the gate errors) and
`test_call_claude_sends_when_gate_ok` (sanity: a healthy gate still allows the
send).

---

### FINDING 3 — LOW — Version string stale
**File:** `pyproject.toml`

`version = "4.5.0"` on a v5.0 release. Bumped to `5.0.0` to match the release
commit and the product name used throughout the UI/docs.

---

## Verification of PRIOR-pass findings (commit `23f3d35`, retained)

This review confirmed the 16 fixes committed just before it are correct and that
the two NEW security fixes compose cleanly with them. Highlights re-verified from
the adversarial angles:

- **Channel egress fail-closed (`channels/manager.py::gate_reply`).** The previous
  backstop misread the `Tier` constant (`PUBLIC == 1`, no tier 0) and fell through
  to `return text` on a double classifier failure — leaking ungated text to an
  external chat. Now releases only on a positive `PUBLIC` verdict; withholds on any
  other tier or a second failure. **Also** — raw exceptions are no longer echoed to
  external channels (they could embed vault paths / PII); a fixed content-free
  notice is sent and the detail is logged locally. *(Security Auditor / Hostile User)*

- **Channel adapter double-checked locking (`get_adapter`).** Two concurrent Flask
  threads could each build an adapter and each spawn a poll/gateway thread for the
  same bot, double-dispatching every inbound message. Now guarded by `_LOCK`.
  *(Reliability)*

- **Learning-loop deadlock + race (`learning_loop.py`).** `_LOCK` promoted to
  `RLock` (promote → score_skill re-entry would deadlock a plain Lock); the whole
  read-count/score/promote sequence is serialized so two concurrent epochs can't
  both blow past `max_active_skills`; a `UNIQUE` index on `skills.pattern` +
  `INSERT OR IGNORE` closes the check-then-insert dup race; anti-flood requires
  ≥`min_distinct` distinct prompts and caps new skills/epoch. *(Reliability)*

- **Memory-dreaming honesty (`memory_dreaming.py`).** Docstring/behavior now match:
  it *counts* noise, it does not delete/tag turns (consolidation is
  non-destructive). Pull window bounded (`_PULL_WINDOW`) and reports `capped`
  rather than silently under-consolidating. Fact source is day-scoped
  (`dream:<day>`) so re-running a day can't inflate confidence. *(Reliability / Integration)*

- **User-model lost-update fixes (`user_model.py`).** `_nudge_trait` and
  `_bump_counter` do Python-side read-modify-write; both are now under `_LOCK` so
  concurrent observers (chat + channel poll) don't clobber each other while the
  SQL-relative `evidence+1` still counts both (value/evidence drift). `note_fact`
  only reinforces confidence on a genuinely new `source`. *(Reliability / Integration)*

- **SOUL.md concurrency + parser (`soul.py`).** `load_soul` snapshots the cached
  value into a local before validating (a concurrent `_invalidate` could null it
  between check and read, breaking the `-> str` never-raises contract);
  `render_personality` no longer eats real content following a self-contained
  italic editor note. *(Reliability / Hostile User)*

- **Onboarding null-safety + key-store honesty (`onboarding.py`).** `advance()`
  normalizes `None` inputs (a client POSTing `{"answer": null}` would hit
  `None.strip()`); when a key fails to store it now surfaces a warning instead of
  advancing as if the key were saved. *(Hostile User / New User)*

- **Chat personalization actually used (`routes/chat.py`).** `/api/chat` fed
  `observe_message()` but never injected the learned USER MODEL / HEURISTICS blocks
  into its prompt; now it does, and — verified here — the blocks flow through the
  same `_scrub_pii` scrub on the cloud-bound path and, ultimately, through
  `seal_outbound` at the provider call, so learned facts are gated before egress.
  *(Integration Tester)*

---

## Adversarial perspectives — what each surfaced

1. **Hostile User** — null/empty onboarding inputs (fixed prior pass); oversized
   channel messages are already truncated (`text[:4096]` / `[:2000]`); malformed
   Telegram/Discord payloads handled by defensive `.get()` chains. No new crash
   found.
2. **Security Auditor** — **two new fail-open holes (Findings 1 & 2).** SQL
   injection: reviewed every dynamic `execute()` — `marketplace.py` and
   `cost_meter.py` build column/`SET` fragments only from **hard-coded allow-lists**,
   values are always parameterized. No injection. Path traversal: dream/DB paths
   are derived from `FRIDAY_HOME`, not request input. Auth model otherwise sound
   (HMAC `compare_digest`, SQLite-persisted login throttle survives restarts).
3. **Reliability Engineer** — the prior pass closed the major thread-safety and
   swallowed-exception issues; this pass confirmed the two `except Exception:
   print(); continue-anyway` patterns in the egress path were the remaining
   silent-but-dangerous failures (Finding 2).
4. **New User** — `pip install -e .` deps are correct and cross-platform;
   `friday doctor` alias exists; server boots clean from a fresh state. The keyless
   Cloudflare exposure (Finding 1) was the one first-run footgun.
5. **Performance Critic** — settings use a TTL'd LRU cache; classifier embedder /
   Presidio are lazy-loaded under locks; ChromaDB cold-start is deferred. Dreaming's
   pull window is now bounded. No unbounded growth introduced.
6. **Integration Tester** — verified the v5 systems compose: learning loop ↔ QA
   promotion gates, dreaming ↔ user_model (day-scoped, no double-count), user_model
   ↔ chat prompt ↔ egress gate, channel bridge ↔ egress gate (fail-closed). All
   green under the full suite.

---

## Recommendations (not blocking)

- **R1.** Refuse to *start* the server for non-loopback binding when no
  `FRIDAY_REMOTE_KEY` is set (belt-and-suspenders to Finding 1) — or print a loud
  startup banner. Today the only signal is the vault-plaintext warning.
- **R2.** Add a startup self-test that calls `seal_outbound` once on a known
  SENSITIVE string and refuses cloud routing if the gate is non-functional, so
  Finding 2's failure mode is caught at boot, not at first leak.
- **R3.** Centralize the two provider call sites' gate wrapper into a single
  `_seal_or_block(payload, provider)` helper so the fail-closed contract can't drift
  between Anthropic and OpenAI paths again.
- **R4.** Consider moving `presidio` (egress Layer 2 NER) into `[all]` — the
  default recommended install currently ships without it, weakening the gate.

---

*Powered by FutureSpeak.AI · Asimov's Mind*
