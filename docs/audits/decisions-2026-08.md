# Architecture decisions — August 2026

**Status:** accepted by Stephen, 2026-08-13.
**Purpose:** these are the durable answers to the ten decision questions raised in
[`inference-discovery.md`](./inference-discovery.md). Downstream work inherits them from this
file rather than from chat history. Where a decision defers work to a later phase, that is
stated explicitly — do not implement a deferred decision early.

**Phase map used throughout:**

| Phase | Scope |
|---|---|
| **A** | Truth-flow: make the system able to report its own failures honestly. Items A1–A8. |
| **B** | Routing and hardware-adaptive dispatch. |
| **C** | Residency scheduling and routed local image generation. |

---

## D1 — Health must be able to fail

**Decision: yes.**

Replace the hardcoded `"status": "ok"` with a **one-token inference probe** against the
currently resident model, cached on a short TTL. Key presence is reported **separately as
configuration status, not as health** — a present key is a config fact, not evidence that
inference works.

The existing zero-call-site probe function (`routing/ollama_manager.py:196` `health_check`)
gets its call site here.

*Rationale:* the audit verified that `/api/health` returns `"status": "ok"` unconditionally
and that provider health for Anthropic and Gemini reduces to a non-empty key string, so a
revoked key reports healthy. Every operator decision built on that signal was built on a
signal that could not report failure.

**Lands in:** Phase A, item **A2**.

## D2 — The egress bypass is a gap; close it

**Decision: gap, close it.**

All `services/worker_adapters/ollama_adapter.py` traffic passes through the egress gate.
If the bypass existed for speed, **make the gate cheap for local traffic rather than
optional.** The gate is not a performance knob.

**Lands in:** Phase A, item **A3**.

## D3 — Context windows come from the catalog

**Decision: yes.**

Truncation layers read `context_window` from the model catalog, falling back to the current
hardcoded constants **only where the catalog lacks a value**.

*Rationale:* real per-model window data is already fetched and displayed, and no context
layer reads it; a small local model receives the same 200,000-token threshold as Claude Opus.

**Lands in:** Phase A, item **A4**.

## D4 — A first-class hardware profile

**Decision: yes, minimal version — but not in Phase A.**

A first-class hardware profile covering **VRAM, RAM, bandwidth class, CPU threads, and OS**,
which dispatch consults for **chat, image, and embedding** selection.

> **That design lands in Phase B. Do not build it in Phase A.**

**Lands in:** Phase B.

## D5 — Configurable embeddings, gated on re-index

**Decision: configurable eventually, gated on a re-index path.**

Collections are **keyed by model and dimension**; embedding spaces **never mix**.

The immediate Phase A piece is only the **write-time assertion**: a write whose vector
dimension mismatches the collection's recorded dimension must raise, naming the model and
both dimensions. Full configurability waits on the re-index path.

*Rationale:* the audit measured `qwen3-embedding:0.6b` at 1024 dimensions against Friday's
hardcoded 384, and verified that a mismatch would fail **silently** inside broad exception
handling — presenting as permanent memory loss with no user-visible error.

**Lands in:** Phase A item **A5** (assertion only); full configurability later.

## D6 — `preferred_model` gets wired, not deleted

**Decision: wire into dispatch during Phase B routing work. Do not delete it.**

**No action in Phase A.**

## D7 — Relocate the runtime stack

**Decision: yes, relocate.**

The runtime stack moves under the repo's own convention, with a **config-overridable cache
path**.

**Lands in:** Phase A, item **A8**.

## D8 — Image generation stays standalone for now

**Decision: routed capability is the target once a residency scheduler exists.**

Until then image generation stays **standalone**, with the documented
**evict → generate → reload** sequence.

**No action in Phase A.**

## D9 — Invert the test default

**Decision: invert it.**

Real provider bodies run **by default** against local or mocked backends. Only tests that
**spend money or require network** stay behind an explicit opt-in marker.

*Rationale:* the audit found roughly 50 of 57 API test files stub the real provider call
bodies via an autouse fixture, so a defect in real HTTP request construction ships green.

**Lands in:** Phase A, item **A1** — done first, so every later item lands under live tests.

## D10 — Fix the OAuth pin now; defer the tray crash

**Decision: split.**

- **Fix this phase:** the Google OAuth redirect pinned to a literal `localhost:3000` while
  the server will silently bind 3001+ if 3000 is occupied. **Item A6.**
- **Do not fix now:** the `friday_tray.py` POSIX crash (unguarded `creationflags` and
  `os.startfile`, verified to have no `sys.platform` guard anywhere in the file). Recorded
  below under the portability milestone.

---

## Portability milestone (recorded, not scheduled)

Deferred by D10. Carried here so it is not lost:

| Item | Evidence | Impact |
|---|---|---|
| `friday_tray.py` crashes on import under POSIX — `CREATE_NO_WINDOW` passed to `subprocess.Popen` at `friday_tray.py:86` and `os.startfile` at `:131,133`, with no `sys.platform`/`os.name` guard in the file | VERIFIED | Blocks macOS/Linux at the first step |
| Credential storage falls back to **plaintext** off-Windows — `services/credential_store.py:151` `protect()` has only vault-key → Windows DPAPI → plaintext; no Keychain, no Secret Service | VERIFIED | Blocks a trustworthy non-Windows release |
| `nvidia-smi` multi-GPU parsing splits by comma without iterating lines; no CUDA device-index selection | VERIFIED | Server / multi-GPU form factor |
| Windows-only launch and ops layer; the env bootstrap parses `.bat` `set` lines with no `.sh` equivalent | VERIFIED | No POSIX launch story |

---

## Out of scope for Phase A

Restated so adjacency does not become scope creep. **Phase B and C are out of scope even
where adjacent:** the hardware profile (D4), wiring `preferred_model` (D6), routed image
generation (D8), and full embedding configurability (D5 beyond the write-time assertion).
