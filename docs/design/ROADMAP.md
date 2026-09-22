# The plan of plans — what ships, what folds, what dies

> **Status:** the roadmap. Supersedes the `docs/design/active/` backlog as the
> ordering authority; the individual specs remain as detail where they survive.
> **Written:** 2026-09-19
> **Method:** STORM — four parallel surveys, each verifying a doc's claims
> against the tree rather than reading its status header.
> **Covers:** 27 docs in `docs/design/active/`, plus
> `docs/design/connector-ecosystem.md`.

Commission (the maintainer, verbatim): *"Go through the unbuilt specs and
create a master spec. You decide what's needed and what's not; what's noise
from past vibes and what's actually going to be in production, and in what
order… We want to prioritize investigation before implementation."*

A note on method, since it was asked for: there is no "Socratic forge" in this
repo. The only match is `epistemic_engine.py`'s *socratic ratio*, a scoring
dimension for whether Friday asks questions rather than a way of writing
specs. What this document uses is STORM, which is the house method and is
named in eight of the docs surveyed, run as four parallel investigations with
one instruction — **verify, do not repeat**.

---

**Evidence registers:**
- **VERIFIED** — a file, symbol or line was read during the survey (2026-09-19).
- **INFERRED** — a conclusion from verified facts.
- **UNKNOWN** — not determined; the check that would settle it is named.

---

## 0. The finding that matters most

**Status headers in this repo are unreliable in both directions** (**VERIFIED**).
`agent-editor-and-coordination-spec.md` says "Implementation: none" and its
AE-0 is 832 lines of shipped `services/arbiter.py`. `voice-system-clean-sheet.md`
says the same and its Phases 0–3 landed within 48 hours of being written.
`context-assembly.md` and `tool-index.md` have said "none" since 2026-09-18,
when both were substantially implemented under other names. Meanwhile
`v6-wholeness-spec.md` claims three phases shipped; one is (**VERIFIED**: P1),
two are partial and thinner than described, six are not built.

So the backlog is not 27 unbuilt specs. It is roughly a third already shipped
under different names, a third genuinely dead, and a third real. Nothing in
this document should be trusted on a header either — every claim below names
what was checked.

**The second-order finding:** the survey found four live defects that nothing
was failing loudly enough to surface, including two open auth holes. An audit
is cheaper than an outage, and this is the argument for investigating before
implementing that the commission asked for.

---

## 1. Fixed during this survey

Not scheduled — done, because finding an open door and writing it on a list is
not a response to finding an open door.

| | Defect | Evidence |
|---|---|---|
| 1 | `routes/workflows.py` had **27 routes and zero auth decorators**, including `POST /api/workflows/chains`, which creates a workflow chain — the machinery the nightly self-improvement loop runs through. 16 mutating routes now carry `@login_required`; reads stay open, matching the split in `routes/goals.py` and `routes/scheduler.py`. Loopback is always trusted (`core/__init__.py:494`), so the desktop app is unaffected. | **VERIFIED** |
| 2 | `GET /api/approvals` and `GET /api/approvals/<id>` were ungated while `/decide` was gated — the *contents* of every pending action were readable by a non-loopback caller, and the approval queue is a map of what the machine is about to do. Both now gated. | **VERIFIED** |
| 3 | The tool catalogue built earlier today reached `/api/chat/send` but **not `/api/chat`**, the streaming path the UI actually uses — it assembles its own tool payload. The measured 15,930→3,311 token cut was not applying to the surface the person types into. Now wired. | **VERIFIED** |

Item 3 is the rule in `one-tool-registry.md` ("one registry, surfaces are
filters over it") breaking the day after it was written about, in code written
the same evening. Two builders, two answers.

---

## 2. What is actually true, by area

### 2.1 Voice — six docs, one system, five historical

`voice-system-clean-sheet.md` (2026-09-16) **is** the spec; Phases 0–3 shipped
within 48 hours (**VERIFIED**: `voice_manifest.py`, `voice_workers.py`,
`voice_session.py`, `voice_receipt.py`, routes at `routes/voice.py:1318-1967`).

One live defect: **a user who selects "Local listening, cloud voice
(ElevenLabs)" gets the disclosure, the setting saves, and then hears Piper**
(**VERIFIED**: `voice_manifest.read_selection` reads the mode but builds the
mouth from `local_voice_tts_engine`; `voice_workers.py`, `voice_session.py`
and `routes/voice.py` contain zero references to `cloud_voice`;
`static/cloud_voice_playback.js` exists with no referrer). That is the same
lying-surface family as everything else fixed today, and it is one wiring task.

`voice-mode-diagnosis-and-repair.md` is fully built and accurate — the only
doc in the set whose status table was true. It is history now.

### 2.2 Agency and autonomy — seven docs, one spine, one gate under four names

`autonomy-execution-spec.md` is the spine. A1–A3 shipped (**VERIFIED**:
`persona_eval.py`, `dissent_gate.py`, `goals.py` with signed receipts,
`approvals.py` with the policy table). `task-visibility.md` shipped whole
(**VERIFIED**: `task_journal.py`, `observer_access.py`, `routes/tasks.py`) and
belongs in `implemented/`. `arbiter.py` shipped as AE-0 of the agent-editor
spec, 832 lines, against a doc claiming nothing was built.

The same human gate is specified four times under four names — action-creation
§3.3, autonomy A3, agent-editor §4.2, crew Q3. One gate exists in code
(`approvals.py`) and **its only callers are `goals.py`** (**VERIFIED**).

**Three open defects the nightly loop should not ship without** (**VERIFIED**):
`run_workflow_chain` and `_retry_chain_step` spawn tasks with no `scope=`
(`agent.py:3446, 3655`); the scheduler does the same (`scheduler.py:593`);
`subagents.py` has no `workflow-step` scope to give them. `scope` is supported
and fails closed — it is simply not passed.

### 2.3 Context and tools — the newest work, and its own gap

`context-assembly.md`'s invariants are right and most of the mechanism shipped
today under other names. Two "core tool" lists exist and disagree
(`tool_catalogue.ALWAYS_RESIDENT` vs `tool_selector.CORE_TOOLS`) (**VERIFIED**).

`model-soup.md` and `frontier-on-12gb-deepseek-derived.md` are not fantasies —
they were measured. They are obsolete: every number in them is Gemma
E2B/12B arithmetic, and the seat became a 27B at 32,768 on 2026-09-18, one day
after model-soup was written (**VERIFIED**: `residency_arbiter.py:977-995`).

`findings-graph.md` was never built and its own kill criterion has elapsed.

### 2.4 Infrastructure — mostly finished artifacts filed as designs

`google-housekeeper.md`'s live value is one real defect (calendar writes are
not account-explicit) plus hygiene. Its central premise — that weekly Google
re-auth explains the daily reconnects — is **wrong and now provably so**: the
cause was a local key mismatch, named in `keystore.py:11-16` after today's
work (**VERIFIED**).

`hostname-onboarding.md` proposes installing a root CA and running Caddy as
SYSTEM so that one person's `localhost:3000` shows a padlock. Its own risk
section argues against it.

`self-patching-installer.md` is 1,212 lines specifying a patcher for a user
base that is one person running from a git checkout, whose author measured the
conflict rate at zero.

---

## 3. The order

Investigation-first, as commissioned: each item names what to check before
building, and several resolve to "nothing to build."

**Now — small, live, and cheap**

1. **Wire the cloud mouth.** `cloud_voice.synthesize()` into the local session
   and load `cloud_voice_playback.js`. Ends a setting that lies.
2. **Scope the unattended paths.** Pass `scope=` from `run_workflow_chain`,
   `_retry_chain_step` and `scheduler.py:593`; add a `workflow-step` scope.
   Directly protects the loop running tonight.
3. **Startup receipts** (`startup-receipts-input.md`). A reporter registry
   beside `BLUEPRINT_REPORT`, with today's `credential_sweep.inventory()` and
   `connector_health.summarise()` as the first two reporters. The artifact and
   route already exist; the work is a dict of callables.
4. **Reconcile the two core-tool lists** into one, and re-measure the seat
   arithmetic on the 27B at 32,768. Every table in the context docs is
   12B-era.

**Next — real, larger, still justified**

5. **Connector phases 5–6** (`connector-ecosystem.md`): credential namespace,
   then OAuth consolidation. Two OAuth state registries and three PKCE
   implementations exist today.
6. **A goals surface.** `goals.py` is 1,255 lines running on a scheduler tick
   and `index.html` has zero references to it (**VERIFIED**). The backend is
   built and headless.
7. **Account-explicit calendar writes** (HK-2). A live wrong-account defect.
8. **The test suite.** Over an hour to run, which is why several of today's
   defects survived. Not a feature; it is the thing that makes the rest
   cheaper.

**Investigate before committing**

9. **Deep research** — built but unreachable: no agent tool, no UI
   (**VERIFIED**). Decide whether to surface it or delete it. **UNKNOWN**
   whether it is wanted.
10. **Native audio-in** — `native_audio.py` exists with no caller. A decision
    layer with no pipeline. **UNKNOWN** whether it is still wanted.
11. **Nightly loop verification discipline** — `grow-button.md` §5–6
    (red-first, no-op detector, a check that could have failed) is the right
    chapter for the loop being built now. Fold it in; do not build the button.

**Dead — delete or archive**

`hostname-onboarding.md` (delete). `findings-graph.md` (delete; kill criterion
elapsed). `friday-crew-spec.md` desks/demonstrations/Hyper-V (delete; keep two
small defects). `self-patching-installer.md` §6–17 (keep §15 Phase 0 as a
note). `v6-wholeness-spec.md` P3, P6, P8, P9 — traversal galaxies for a
skeptical stranger who does not exist, screen actuation the spec itself says
needs a cloud VLM, whole-self export with no second machine, and multi-user on
a one-user machine. `elevenlabs-voice.md` §4 subagent floor control. NOOA, the
second Windows account, the broker. `agent-editor` AE-2–AE-7 — projects and
org charts, which that doc's own kill criterion calls "theatre on one 12 GB
machine".

Archive as finished: `voice-mode-diagnosis-and-repair.md`,
`vault-cloud-fallback-decision.md` (one word from the maintainer closes it),
`google-oauth-verification-checklist.md` (a runbook, not a design),
`task-visibility.md` → `implemented/`.

---

## 4. What this cost to find

Four parallel surveys, read-only, no server touched, while the maintainer's
self-improvement loop kept the GPU. They found two auth holes, one setting
that lies to the user, one gap in code written four hours earlier, and roughly
nine documents' worth of work that should not be done at all.

The recurring shape of every defect found today — in credentials, in connector
health, in workflow status, and here — is the same: **the system knew and did
not say, or said it somewhere nobody was looking.** That is worth more as a
review question than any of the individual fixes.

---

## 5. Open questions

1. **Deep research and native audio-in**: surface or delete? Both are built
   and unreachable. **UNKNOWN.**
2. **`vault_cloud_fallback`**: flip to `warn`, or close the record at
   `redact`? One word. **UNKNOWN.**
3. **Gmail send** (HK-5→8): there is deliberately no standing send capability.
   Does that change? **UNKNOWN**, and the answer decides ~200 lines of spec.
4. **The bundled OAuth client**: `BUNDLED_CLIENT_ID` is empty and nothing is
   submitted to Google. Does Friday ever have users who are not you? Most of
   §2.4 depends on the answer. **UNKNOWN.**
