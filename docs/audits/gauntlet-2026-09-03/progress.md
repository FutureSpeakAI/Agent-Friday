# Gauntlet Audit — 2026-09-03

Live page. Overnight autonomous run, dispatched by Stephen via a relayed
gauntlet-loop prompt (drafted earlier at `docs/gauntlet-loop-prompt`,
worktree `.claude/worktrees/gauntlet-prompt`, commit 7d77304). Executed
unattended per his standing authority; judgment calls made along the way
are logged here as "JUDGMENT CALL" so he can see and override any of them.

Worktree: `.claude/worktrees/gauntlet-audit-2026-09-03`, branch
`gauntlet-audit-2026-09-03`, based on `integration/release-2026-09-03`
at f000f07. Never pushed. Main checkout at
`C:/Users/swebs/Projects/friday-desktop` was left untouched — it has
unrelated in-progress uncommitted work (knowledge-graph/credential-store
changes) that does not belong to this task.

## Honest limits, up front

Before anything else, what this run did NOT do, so nothing below reads
stronger than it is:

- **1 of 12 seams closed.** Only "MCP and connector registration" hit the
  audit's own 2-consecutive-clean-sweep bar. The other 11 are still open —
  several got close and then a fresh sweep found one more real thing,
  which is why they reset. Per-seam status is in `coverage.md`.
- **The claim corpus stopped growing at 00:51, then got one gap closed.**
  `claims.jsonl` sat flat at 90 entries from commit `16450bd` until this
  morning — extraction stopped after round 2. A claim that was never
  extracted was never judged, so "swept" below still means "swept against
  the claims we pulled," not "swept against everything the corpus
  contains." Stephen named `src/agent_friday/routes/*.py` docstrings
  specifically as unwalked; that one gap is now closed (C91-C133, all 61
  route files read at the docstring level, 15 of the strongest claims
  independently verified, no new findings surfaced). Everything else
  `coverage.md`'s claim-corpus table already flagged as partial or not-
  done — `ui_parts/app.html`'s disclosure strings, deeper per-file
  service-module docstrings, `THREAT_MODEL.md`'s untraced claims — is
  still exactly that; treat that table as the honest boundary of what
  this run actually checked, not this summary's tone.
- **Startup wiring stayed parked.** Static analysis only — see JUDGMENT
  CALL 1. Nothing about boot sequencing was dynamically verified.
- **Five early fixes (F1, F2, F8, F11, F12) shipped without a documented
  revert step**, before the red→green→red-on-revert discipline was
  tightened partway through the night. All five have now been run through
  that check retroactively (see "Retroactive revert verification," just
  below the fixed ledger) — genuinely confirmed now, but they weren't
  when they first landed, and one of them (F11) turned up a real bug in
  its own probe in the process. Worth knowing before trusting the "22
  fixes, full proof" framing at face value.

## What you need to decide — ranked, one sentence each

25 items are queued because they're judgment calls, not because they're
unimportant. Ranked by how much rides on the answer. **One set of five is
handled separately, as its own five-minute document, not folded into this
list:** [decisions-five-dead-settings.md](decisions-five-dead-settings.md)
turns the long-known "five settings persist and redraw but drive nothing"
gap into five direct per-setting questions with options and a
recommendation each — go there first if you want the quickest wins before
tackling the rest of this list.

1. **Q19 (SEVERE).** `local_only` and `local_preferred` don't actually keep
   ordinary chat local by default — the most common thing you do with
   Friday. Decide: tighten the router to match the mode's own promise (chat
   gets slower/lower-quality on a local seat), or rewrite what the modes
   claim to guarantee. This is the one the whole "privacy-first" premise
   rests on — read it first.
2. **F29 (child safety, high priority).** `minor_mode`'s own settings
   description promises adult content is hidden in the gallery when it's
   on; nothing hides it. Decide: build the hide, or correct the promise —
   either way this shouldn't sit queued long.
3. **F10's router half** (the copy was already fixed; the behavior wasn't).
   Decide: should `local_only` fail closed and refuse when no local model
   is reachable, or fall back to cloud with a visible notice? Same family
   of question as Q19, on a different code path.
4. **F18.** Vault-forced local-only answers are watered-down on at least
   one entry point outside `/api/chat`, not leaked — but "vault access
   always gets the full local answer" isn't true everywhere. Decide if
   that's worth closing now or living with.
5. **F30.** When a background task's local seat fails and it falls back to
   another provider, the fallback doesn't always re-apply the right
   content gating for where it actually landed. Decide if this needs a
   structural fix or is an acceptable, documented gap.
6. **F21.** The two voice websockets don't obviously enforce the same
   "refuse if auth isn't configured" posture your HTTP routes do. Decide
   if voice needs the identical fail-closed rule or its own reasoning.
7. **Q16.** The unattended daily "short-production" creation skips its own
   pipeline's human-review checkpoints before the expensive spend and
   before publishing. Decide: keep it fully autonomous, or make it pause
   and wait for you like every other caller of that pipeline does.
8. **Q6 / Q7 / Q11 / Q13 (money visibility, four related findings).**
   Image/video/music generation, several opt-in provider catalogs, and
   Gemini voice calls aren't metered at all — including the ones your
   daily-creation budget gate is supposed to be checking against. Decide
   if real-dollar visibility into these needs to happen now or can wait.
9. **F25.** Scheduled jobs (news, digests, KG reindex, etc.) get zero
   retries by default — one bad minute silently skips a whole day's run.
   Decide if that's the right default or too brittle.
10. **Q21.** Nothing in the product would tell you a background job has
    been running too long — the exact blind spot that let F31 (below) run
    for hours before anyone noticed. Decide if a job-duration watchdog is
    worth building now.
11. **Q18.** Friday has a content-based "this looks like heavy work, I
    should ask" heuristic that's fully built and never called; the thing
    that actually asks is a different, load-time-only heuristic. Decide
    whether to wire the two together or drop the unused one.
12. **F9.** A connector blocked by the security scanner shows up as a
    generic error, not "this was blocked for a security reason" — so the
    natural next click (Restart) used to defeat the block (now fixed
    separately). Decide if the status message should say why.
13. **F3.** The context-log "Retention Period" setting doesn't delete
    anything automatically. Decide: build the automatic sweep, or change
    the setting's copy to say it's manual.
14. **Q9 / Q10 (knowledge graph hygiene, two related gaps).** Asking Friday
    to forget someone doesn't scrub her graph's text descriptions of them,
    just entity titles; and nothing ever prunes or corrects old graph
    entries. Decide if either is worth building now.
15. **Q23.** A side effect of tonight's own F31 fix: in `gated_cloud` mode
    with a large backlog, some chunks from multi-chunk files can get
    silently, permanently skipped. Decide if it's worth the restructuring
    to fix now (doesn't affect the default `local_only` mode).
16. **Q17.** The "Go Off Record" toggle says it disables logging "for this
    session," but nothing ever turns logging back on automatically.
    Decide: wire a real reset point, or reword the toggle.
17. **Q20.** A documented settings key (`task_overrides.voice`) does
    nothing — voice turns are never classified in a way that would let it
    fire. Low stakes; decide if it's worth wiring up or removing from docs.
18. **Q5 / Q8 (installer, two low-severity items).** The installer's
    embedder claim is inaccurate on its own recommended no-GPU path, and a
    computed disk-space warning never actually blocks a download. Both
    minor; decide if they're worth a doc/code touch.
19. **F22 / F40 (federation, two related gaps).** A settings-sync push
    could in principle broadcast stale in-memory config instead of what's
    saved; separately, the Federation panel's per-peer "block" control has
    no backend route and enforces nothing at all — clicking it silently
    does nothing. Decide if either needs building/hardening now.
20. **F24.** The five onboarding persona/distribution presets mostly don't
    do what their descriptions claim. Decide: build them out, or simplify
    the copy to match what they actually do.
21. **Q22.** The MCP connector allowlist remembers approval by server name,
    not by the exact command it approved — editing an approved server's
    command inherits the old approval. Decide if that's the UX trade-off
    you want.

(Q1/Q4 and a handful of smaller HOLDS/observations are in `findings.jsonl`
and `coverage.md` but didn't make this list — genuinely lower-stakes than
the 21 above.)

## What got fixed without asking

23 real defects landed with full red→green→red-on-revert proof and a green
full suite after every batch (five of them — F1, F2, F8, F11, F12 —
originally without the revert step; see the honest-limits note above and
the retroactive-verification section below the fixed ledger). The two most
consequential: **F31**, a live cost incident that spent real money
overnight (detail immediately below), and **F32**, a credential leak where
every MCP connector received Friday's live decrypted secrets. Five more
close local-only/privacy enforcement gaps the same class as Q19 above but
narrow enough to fix outright (F33, F34, F35, F36, F37), plus F38 (a false
onboarding claim in README.md) and F39 (a second browser tab silently
killing a voice call with no notification) — see the FIXED LEDGER for all
23. Nothing else tonight rose to "wake him up for this."

## ⚠⚠ READ THIS FIRST — live production is still spending money right now (F31)

Stephen's overnight report: the LIVE running Friday (not this worktree —
separately installed, v5.11.0) has been making continuous claude-sonnet-5
calls roughly every 8-14s since ~19:00 on 2026-09-03, $27.41 by 23:45,
~$10/hour. His hypothesis was that `/api/health` (or similar GET status
polling) fires a real inference probe and bills the ledger per glance.

**That specific hypothesis is refuted, cleanly, with direct evidence** —
`services/provider_health.py` never touches `cost_meter` at all, and the
live `server_stderr.log`'s last POST request of any kind was at 22:30:12
last night, hours before the spend (and the investigation) stopped. No GET
polling — health, processes, tasks, seat-gate/statuses — bills anything,
ever. That part of his mental model was wrong, but he was right that
something real and expensive was running unattended, and he was right to
ask for it to be found.

**The real mechanism**: a knowledge-graph Tier B reindex (manually
triggered 6 times between 19:39 and 22:30 last night via
`POST /api/knowledge-graph/reindex`) has been running, continuously,
unattended, ever since — confirmed still `"running": true` / `"last": null`
at investigation time (~03:04). Its entity-extraction step
(`knowledge_graph/indexer.py`) has **no cap on cloud-eligible LLM calls per
pass**. When its intended cheap/free provider (an OpenRouter free-tier
Gemma model) started failing, the model router's own cross-provider circuit
breaker — correct behavior for chat, so a user is never left without an
answer — rerouted every subsequent chunk straight to paid
`claude-sonnet-5`, silently, for as long as the pass had chunks left. For a
large corpus that's thousands of chunks at full frontier pricing, which is
exactly the sustained ~$10/hour. Full trace, with every piece of evidence
that ruled the health-check theory out and pinned this one down instead,
is finding **F31** in `findings.jsonl`.

**Fixed in this worktree, proven red-green-red-on-revert**: added
`MAX_CLOUD_EXTRACT_CALLS = 200` to `indexer.py`, mirroring the file's own
existing `MAX_REPORT_COMMUNITIES` cap-per-pass idiom — once a pass has made
that many cloud-eligible attempts, remaining chunks are left for the next
delta pass instead of continuing to spend. Two new tests in
`tests/unit/test_kg_indexer.py::TestCloudExtractionCap` prove the cap fires
and prove it does not touch the default local_only path. Full
`test_kg_indexer.py` suite (18 tests) and the full unit+API suite both
green after.

**Update — the live app was in fact restarted, and this is resolved, not
an open contradiction.** This section originally said the fix "cannot stop
tonight's bleeding" because this worktree is isolated from the live
process and nothing here would ever touch it unattended. That claim is
still true — this session never touched the live app. But an independent
check found the live process actually restarted at 03:35:47, with
`settings.json` rewritten roughly 30 seconds before that. Left alone, that
would be an unexplained anomaly sitting in a security audit's own ledger —
exactly the kind of loose thread that wastes a morning. It isn't one:
Stephen ordered that restart himself, through a different session, once
F31 was diagnosed, specifically to stop the spend. So the bleeding did NOT
continue indefinitely as this section originally implied — it stopped at
03:35:47, roughly 3.5 hours after the 23:45 snapshot of $27.41/1,029
calls, and however much accrued in that window is the real, bounded final
number, not something still running as he reads this. This session's own
fix (`MAX_CLOUD_EXTRACT_CALLS`) was never installed to the live app either
way — it lives in this worktree only — so the restart, not this fix, is
what actually stopped it.

Generalization he asked for is done: `/api/seat-gate/statuses`,
`/api/processes`, `/api/tasks`, and every other polled status/residency
route checked have no side effects and no cost_meter linkage. The defect
was entirely inside the KG indexer's own extraction loop, not in any
read/status endpoint — a narrower, different, and honestly more interesting
bug than the one he suspected.

**Also worth surfacing here, found while chasing this generalization
tonight — a real security gap, not a cost one:** every stdio MCP connector
was inheriting Friday's full decrypted secrets environment (cloud-provider
API keys, vault key, etc.) with no filtering at all, despite a purpose-built
mechanism in `extension_security.py` that was simply never wired in.
Fixed, proven, committed — see Fix #9 (F32) in the FIXED LEDGER below. Not
related to tonight's cost incident, but a genuinely more serious class of
finding, and it landed the same night, so it belongs in this same
top-of-file summary rather than only buried in the ledger.

**One more, and this is the most consequential thing found all night —
NOT fixed, needs your judgment (Q19):** `local_only` mode's own stated
guarantee ("ALL turns go to the local seat — the cloud orchestrator is not
consulted") and `local_preferred`'s ("local seat first; cloud only as
fallback") are both false for ordinary interactive chat — not an edge case,
the single most common thing you do with Friday. The router's tool-use
branch only prefers local for background/scheduled work; every
interactive message defaults straight to cloud regardless of which of
these two modes you've picked, unless you've also explicitly bound a local
model as your reasoning seat. Empirically verified against the real router
with factory-default settings: `local_only`, `local_preferred`, and `smart`
all route an ordinary chat message to cloud claude-sonnet-5 identically.
**Why this is queued rather than fixed:** the code carries an explicit,
dated, Stephen-attributed comment from 2026-08-16 keeping interactive chat
on cloud for speed — this may be a documented decision this promise was
never reconciled against, not a plain oversight, and reversing it changes
the speed/quality of every single chat message for anyone who picked
local_only or local_preferred specifically for the privacy guarantee.
Even the copy-only half of this (correcting `_MODE_MEANING`'s wording) hits
an existing, off-limits test (`tests/unit/test_seat_transparency.py:72`)
that hardcodes the current absolute claim as correct — so unlike F10, there
is no clean, test-safe copy fix to land unilaterally here either. Full
detail, both options, and their consequences are in findings.jsonl Q19 —
please read that one closely; it is the one item tonight that touches what
the product's core privacy promise actually does, for the case nearly
everyone hits nearly every time.

## JUDGMENT CALL 1 — second-instance boot (startup seam)

Per the operational note: if I can't prove all four inertness conditions
(off-GPU, off-real-Ollama, no MCP servers, no real provider key), park the
seam rather than boot anything. I have not yet completed that proof (see
coverage.md). Until it's done, the startup-wiring seam is probed **statically
only** (call-graph / reachability analysis by reading code, no process
started). If the four conditions get proven later tonight, dynamic boot
work against a scratch second instance may follow — this file will say so
explicitly before it happens.

## MANDATE CHANGE (2026-09-03, mid-run)

Stephen: "don't just want a findings ledger, I want a fixed things ledger."
Relayed instruction widened the mandate: fix anything where the correct
behavior is unambiguous (dead settings keys, functions nothing calls that
obviously should be called, a check that special-cases some cases and
misses others, a default pointing at something that doesn't exist), keeping
full red→green→red-on-revert probe discipline. Queue instead of fix:
anything that changes what the product does rather than whether it does
what it says, user-facing copy, cases where the claim should move rather
than the code, and the five already-known persist-and-redraw settings.
Run full unit+API suites after each batch, not just at the end. Never
install to the live Friday overnight — fix, prove, commit, leave staged.

This file now tracks two ledgers: **fixed** (with red/green/red evidence)
and **queued for Stephen** (with evidence and why it's not mine to decide).

## Status

- [x] Worktree created, scaffold written
- [x] Round-1 claim corpus extracted (6 background agents; see findings.jsonl)
- [ ] Further seam-by-seam finder/falsifier rounds
- [x] Fix #1 landed (KG nightly reindex) — see FIXED LEDGER
- [x] Fix #2 landed (provider_health google/comfyui/higgsfield) — see FIXED LEDGER
- [ ] Fix candidate #3 (screenshot vision path vs local-only) — needs verification first
- [ ] Two consecutive clean sweeps per seam

## FIXED LEDGER

### Fix #1 — nightly knowledge-graph reindex was never scheduled (F1, UNREACHED)
**File:** [src/agent_friday/services/scheduler.py](../../../src/agent_friday/services/scheduler.py)
**Finding:** the nightly KG reindex job (03:30, after memory-dreaming at 03:00)
was registered inside `notifications._register_default_daily_jobs()`, a
function orphaned by the scheduler migration (server.py's own comment says
`start_scheduler()` "replaces" it) and never called from anywhere. Every
sibling job (news, digests, self-improvement, session-summary...) was ported
to the new `_register_default_builtin_tasks()` registrar; this one was left
behind. Settings still shows the toggle, `DEFAULT_SETTINGS` still says
`nightly_reindex: True`; nothing ran.
**Fix:** ported the registration into `_register_default_builtin_tasks()`,
calling the same `_run_knowledge_reindex_job`, same 03:30 schedule.
**Probe:** [tests/gauntlet/test_kg_nightly_reindex_registered.py](../../../tests/gauntlet/test_kg_nightly_reindex_registered.py)
**Evidence:**
- RED before fix: `KeyError: 'knowledge_graph_reindex'` / assertion failure — job absent from `BUILTIN_TASKS`.
- GREEN after fix: all 3 assertions pass.
- RED again after reverting via `git stash` (no-op check) — same failure, same reason.
- Fix reapplied from the stash; stash entry dropped.

### Fix #2 — provider_health deep-check silently skips 3 of 16 providers (F2, BROKEN)
**Files:** [src/agent_friday/services/provider_health.py](../../../src/agent_friday/services/provider_health.py), [src/agent_friday/services/local_image.py](../../../src/agent_friday/services/local_image.py)
**Finding:** `inference_probe()`'s `probe_types` covered `ollama`/`anthropic`/
`openai-compatible` (13/16 providers, 11 of them only because they share the
openai-compatible type). `google` fell through `deep=1` straight to the
shallow "key present" response — no real generation was ever attempted.
`local-comfyui` and `higgsfield` both declare `auth:{"type":"none"}`, so
`_has_key()` returned `True` unconditionally with no reachability check at
any depth — even though `provider_registry.is_provider_available()` already
has a correct MCP-liveness check for higgsfield that `provider_health`
never called. This is the modern recurrence of the historical "two
providers special-cased, six misreport" defect class.
**Fix:**
- `local_image.py`: added `is_reachable()` (GET `127.0.0.1:8188/system_stats`, mirrors the existing `interrupt_comfy()` pattern).
- `provider_health.py`: added `comfyui` branch (calls `is_reachable()`), `higgsfield` branch (calls `provider_registry.is_provider_available()`), and a `google` branch in `inference_probe()` mirroring the existing `anthropic` branch (real `generate_content` call via `core.get_genai_client()`); added `"google"` to `inference_health()`'s `probe_types`.
**Probe:** [tests/gauntlet/test_provider_health_google_comfyui_higgsfield.py](../../../tests/gauntlet/test_provider_health_google_comfyui_higgsfield.py)
**Evidence:**
- RED before fix: all 3 cases report `status: "ok"` for a broken/unreachable provider (comfyui case additionally `AttributeError` — `is_reachable` didn't exist yet).
- GREEN after fix: all 3 pass.
- RED again after reverting via `git stash` (no-op check) — same 3 failures, same reasons.
- Fix reapplied from the stash; stash entry dropped.

**Batch verification after fix #1 + #2:** `pytest tests/gauntlet/` — 7/7 green
(6 fix-pinning + 1 queued-defect-pinning, which is correctly RED — see F3
below). Full `pytest tests/unit tests/api --tb=no -q`: **exit code 0**, no
failures visible in the dot output, ran well past tests/README.md's claimed
~45s (see Handoff item 4 — read the exit code, don't trust the summary line
or the doc's timing claim). This batch's 2 landed fixes are clean against
the whole suite.

### Fix #3 — MCP restart() bypassed the security-disable gate (F8, BROKEN)
**File:** [src/agent_friday/mcp_client.py](../../../src/agent_friday/mcp_client.py)
**Finding:** `extension_security.gate_mcp_config()` blocks a connector at boot
whose launch command trips the destructive/download-and-execute scanner,
which becomes `sp.status = "disabled"`. `MCPManager.start_all()` and
`MCPManager.authorize()` both correctly check for that status and refuse.
`MCPManager.restart()` did not — it unconditionally called `sp.stop()` then
`sp.start()`. Since `POST /api/mcp/restart` is the single most natural thing
an operator does after seeing a blocked connector's (mislabeled — see F9,
queued) status, a security-blocked server could be started for real just by
clicking Restart.
**Fix:** added the same `if sp.status == "disabled": return False` guard
`start_all()` already has, at the top of `restart()`.
**Probe:** [tests/gauntlet/test_mcp_restart_respects_disabled.py](../../../tests/gauntlet/test_mcp_restart_respects_disabled.py)
**Evidence:**
- RED before fix: `restart()` called `sp.start()` on a disabled server.
- GREEN after fix: both tests pass (including a no-op-shaped sanity check that restart still works for a *non*-disabled server — the guard is specific, not a blanket refusal).
- RED again after reverting via `git stash` (no-op check) — same failure, same reason.
- Fix reapplied from the stash; stash entry dropped.
- `pytest tests/gauntlet/` after this fix: 9/9 pass except the one deliberately-red F3 probe (queued finding, correct state).
- Full `pytest tests/unit tests/api --tb=no -q` run in background: **exit code 0**, no regressions from fixes #1-#3 together.

### Fixes #6-#7 — F19 (voice brain-seat resolve() typo) and #20 (F16 was incomplete: /ws/live had no local-only gate at all)
See findings.jsonl F19/F20 for full evidence. Both proven red->green->red-on-revert. Batch full-suite: `<testsuite tests="6697" errors="0" failures="0" skipped="8" time="577.057">` — clean, confirms this batch alongside every prior fix.

### Fix #4 — egress_gate had no handling for the OpenAI-shape tool_calls wire field (F12, hardening)
**File:** [src/agent_friday/services/egress_gate.py](../../../src/agent_friday/services/egress_gate.py)
**Finding:** `_gate_messages()` gates the Anthropic `tool_use` block shape
(a real historical leak, already fixed — see `_gate_tool_use`'s docstring:
"a local seat that falls back to cloud carries its own tool calls with
it"), but had zero handling for the OpenAI-compatible wire shape's
equivalent: an assistant message's `tool_calls[].function.arguments` (a
JSON-encoded string carrying the same kind of real user data). A dedicated
verifier traced every retry-with-provider-switch path in the codebase at
the object-identity level (worker adapters, compute_client, scheduler
retries, orchestrator, `_generate_agent`'s cloud/local ladder, chat.py's
inline fallback + redispatch closures) and confirmed **none of them
currently reuse a caller-supplied messages list across a provider switch**
— each rebuilds a fresh plain `{role, content}` copy. So this specific gap
could not be triggered by any code that exists today in this worktree.
**Fixed anyway, as hardening**, per the verifier's own recommendation: one
accidental future refactor (e.g. `_call_ollama`/`_call_openai` "optimized"
to reuse the passed-in list directly) would make it live, and the fix
costs nothing — it mirrors the existing, already-battle-tested
`_gate_tool_use` pattern rather than inventing new policy.
**Fix:** added `_gate_tool_calls()`, gating every string value inside each
`tool_calls[].function.arguments` JSON payload (parsed, gated via the
existing `_gate_arg_values`, then re-serialized — OpenAI's wire format
requires `arguments` to remain a JSON string) while preserving `id`/`type`/
`function.name` for replay pairing; falls back to opaque-text gating for a
malformed non-JSON arguments string, mirroring `_gate_tool_result`'s
existing fallback. Wired into `_gate_messages()` alongside the existing
`content` handling.
**Probe:** [tests/gauntlet/test_egress_gate_tool_calls_openai_shape.py](../../../tests/gauntlet/test_egress_gate_tool_calls_openai_shape.py)
**Evidence:**
- RED before fix: a planted sensitive value (SSN pattern) in a tool-call argument survived `_gate_messages()` byte-for-byte, both the JSON and non-JSON-fallback cases.
- GREEN after fix: all 3 tests pass, including a no-op-shaped structural check (call id, function name, and non-sensitive argument keys/values all preserved — the fix redacts content, not shape).
- RED again after reverting via `git stash` (no-op check) — same 2 failures, same reasons.
- Fix reapplied from the stash; stash entry dropped.
- `pytest tests/gauntlet/` after this fix: 11/12 pass, the one deliberate F3 failure (queued finding) unchanged.
- Full `pytest tests/unit tests/api --tb=no -q` running now — this one touches a security-critical shared function (`_gate_messages`) used by every cloud call site including the existing adversarial egress tests, so the full-suite result matters more than usual for this fix specifically.

## ⚠ HIGHEST-PRIORITY QUEUE ITEM — please read first, decision needed

**F10 — the local-only privacy promise is false by default, and this is
worse than a leak: a user made a decision based on it.** (Framing per
Stephen's 00:33 check-in — a false privacy promise in a product whose
entire premise is privacy deserves more care than a routing bug.)

**The question, precisely:** when the user has chosen local-only and no
local model can currently be reached (Ollama isn't running, or nothing
installed fits the task), should Friday —

- **(A) fail closed** — refuse the turn and tell the user why, matching the
  "LOCAL ONLY MEANS LOCAL ONLY" pattern `routes/chat.py:941-987` *already*
  uses for the sibling case (a live Ollama call that fails mid-request)?
  **Consequence:** Friday sometimes can't answer at all in local-only mode.
  Honest, but a real capability loss whenever Ollama is down or between
  model installs.
- **(B) fall back to cloud, but say so** — keep working, but tell the user
  in the moment that this specific turn left the device (a visible notice,
  not a silent default)? **Consequence:** always answers, but "local-only"
  stops meaning "never leaves this device" and starts meaning "prefers this
  device" — which is what `local_preferred` already promises, so this
  would make the two modes nearly redundant unless local-only's notice is
  loud enough to matter.

Both are legitimate; this is Stephen's call, not mine, because it trades
off capability against the specific guarantee the mode's name makes.

**Why it's broken today:** `routing/model_router.py`'s `_route_basic()`
silently routes to cloud when Ollama isn't running or no local model fits —
on 2 of its 3 relevant branches the `fallback_to_cloud` check is either
dead code (both branches return the identical `provider: "cloud"`) or
entirely absent. `setup_wizard.py` also never sets `fallback_to_cloud=False`
when a user picks local_only — it only writes `mode`. Closing this needs
coordinated changes across those 2 files plus confirming callers handle
whatever new failure shape option (A) would introduce without crashing —
genuinely not a one-line fix, so the router/wizard behavior stays queued.

**What isn't a product decision, and IS fixed now:** regardless of which
way (A)/(B) goes, the copy was simply false either way, so it no longer
makes an absolute claim. `onboarding_copy.py`'s `ROUTING_CHOICES` local_only
description now reads: *"This computer for everything. If she can't reach a
local model, she currently falls back to the cloud rather than refuse — a
stricter, fails-closed mode is being considered."* — accurate to today's
real behavior, and it sets up whichever way the decision above goes without
needing a second copy change. Probe:
[tests/gauntlet/test_onboarding_copy_no_false_absolutes.py](../../../tests/gauntlet/test_onboarding_copy_no_false_absolutes.py)
(demonstrates the fix by failing against the literal old string).

Full evidence in findings.jsonl F10. Please look at this first and answer
(A) or (B) — the router-behavior fix follows once you do.

### Fix #5 — onboarding copy corrections: local-only's false absolute (F10 copy half) + vault passphrase overstatement (F11)
**File:** [src/agent_friday/services/onboarding_copy.py](../../../src/agent_friday/services/onboarding_copy.py)
**Finding:** `VAULT_LOCATION` said the passphrase is stored "not in any
file you could open." `services/vault_passphrase.py`'s own docstring says
it writes BOTH the keychain and a DPAPI-wrapped file on disk as a durable
backup — a real file that exists and can be opened; its bytes are just
DPAPI ciphertext, unreadable as plaintext without the same Windows account.
**Fix:** corrected to describe both storage homes accurately (credential
manager + an encrypted backup file), keeping the actual security property
(neither is readable as plain text) intact.
**Probe:** [tests/gauntlet/test_onboarding_copy_no_false_absolutes.py](../../../tests/gauntlet/test_onboarding_copy_no_false_absolutes.py) — `test_vault_location_no_longer_claims_no_file_exists` (the same file's other test, `test_local_only_no_longer_claims_an_absolute_never`, covers F10's copy half above).
**Note:** unlike F10, this one had no attached product decision — the old
copy was simply inaccurate, so it's fixed outright, not queued.

### Fix #8 — KG Tier B extraction had no cap on cloud-eligible LLM calls per pass (F31, BROKEN — live incident)
**File:** [src/agent_friday/services/knowledge_graph/indexer.py](../../../src/agent_friday/services/knowledge_graph/indexer.py)
**Finding:** see the top-of-file section above and F31 in findings.jsonl for
the full live-incident investigation. Summary: `reindex_tier_b()`'s
per-chunk extraction loop had no ceiling on cloud-eligible LLM calls. When
the routed cheap/free provider (OpenRouter free-tier Gemma) went unhealthy,
`model_router`'s own cross-provider circuit breaker — correct for chat —
silently rerouted every remaining chunk to paid `claude-sonnet-5` for as
long as the pass had chunks left. A live pass triggered last night ran 7+
hours unattended at ~$10/hour. The file already had this exact idiom for
its later phase (`MAX_REPORT_COMMUNITIES = 24 # cap LLM cost per index
pass`) — extraction, the much higher-volume earlier phase, had no
equivalent.
**Fix:** added `MAX_CLOUD_EXTRACT_CALLS = 200`; a per-pass counter of
cloud-eligible (`pinned=False`) extraction attempts (counts attempts, not
just successes — a failing call can still have made, and paid for, an HTTP
round trip). Once the cap is hit, remaining chunks are left "stale" for the
next delta pass and reported via a new `skipped_cloud_cap` field on the
`info` dict — a deferral, not a failure, so `extract_failures` stays
accurate.
**Probe:** `tests/unit/test_kg_indexer.py::TestCloudExtractionCap` (2 new
tests: the cap firing on a synthetic 5-chunk gated_cloud corpus with the
cap set to 2, and a no-op-shaped check that `local_only` mode — the
default, `pinned=True` for every chunk — is completely unaffected by the
cap).
**Evidence:**
- RED before fix: `AttributeError: ... has no attribute
  'MAX_CLOUD_EXTRACT_CALLS'` — clean test failure inside the test body, not
  a collection error (confirmed both new tests fail this way).
- GREEN after fix: both new tests pass; full `test_kg_indexer.py` (18
  tests) green.
- RED again after reverting via `git stash` (no-op check) — same failure,
  same reason.
- Fix reapplied from the stash; stash entry dropped.
- Full unit+API suite run after this fix — see batch verification note
  below.
**Cannot help tonight's live spend** — worktree-isolated, live app not
touched or restarted per Stephen's explicit instruction. See the top-of-
file section for what he should expect to see when he wakes up.

### Fix #9 — every stdio MCP server inherited Friday's full decrypted-secrets environment (F32, BROKEN — real security gap)
**File:** [src/agent_friday/mcp_client.py](../../../src/agent_friday/mcp_client.py)
**Finding:** a fresh-context sweep of the MCP seam found `services/
extension_security.py`'s `sanitize_env_for_mcp()`/`ENV_BLOCKLIST` (19 named
secrets, "Env vars MCP servers must NEVER see") had zero callers anywhere in
the codebase, despite a live API surface
(`/api/security/env-blocklist`/`/trust-levels`) that reads as if the control
is active. `MCPServerProcess._spawn()` did `full_env = os.environ.copy()` —
the entire parent environment, unfiltered — and `credential_store.
bootstrap_provider_env()` decrypts every stored provider key into
`os.environ` at boot, before any MCP server spawns. Net effect: any stdio
connector (a random `npx`/`pip`/`uvx` community package, "sandboxed" by
this codebase's own default trust model) could read Friday's live
`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`GEMINI_API_KEY`/`FRIDAY_VAULT_KEY`/etc.
straight out of its own environment — no exploit, just normal process
inheritance a purpose-built security module was never wired into.
**Fix:** `MCPServerProcess` gained a `trust_level` parameter (default
`"sandboxed"` — opt IN to secrets, not opt out of leaking them); `_spawn()`
filters the inherited environment through `extension_security.
sanitize_env_for_mcp()` before layering the connector's own (separately
encrypted) env on top, so a connector's own configured vars are unaffected.
`MCPManager.load_config()` resolves each server's trust level via the
already-existing (previously uncalled) `extension_security.
get_trust_level()`.
**Probe:** [tests/gauntlet/test_mcp_env_leak_to_subprocess.py](../../../tests/gauntlet/test_mcp_env_leak_to_subprocess.py)
**Evidence:**
- RED before fix: all 5 tests fail cleanly (a blocklisted key survives into
  the subprocess env; the not-yet-existing `trust_level` kwarg/attribute
  raises `TypeError`/`AttributeError` inside the test body).
- GREEN after fix: all 5 pass, including no-op-shaped checks that a
  `"trusted"` server still gets the full environment and that a connector's
  own configured env vars still pass through.
- RED again after reverting via `git stash` (no-op check) — same 5
  failures, same reasons.
- Fix reapplied from the stash; stash entry dropped.
- Ran together with the two existing MCP gauntlet probes (F8, F28): 9/9
  green, no interaction between the three MCP fixes.
- Full unit+API suite run after this fix — see result below.

### Fix #10 — /ws/live's local-only gate never rechecked across a call's renewal legs (F33, BROKEN)
**File:** [src/agent_friday/routes/voice.py](../../../src/agent_friday/routes/voice.py)
**Finding:** F20 made local-only refuse a NEW /ws/live connection. But the
handler's own comments say a single Gemini Live connection is capped
(~10 min) and the reconnect loop is what makes an hours-long call possible
by redialing Gemini for each new leg — that loop never rechecked
`model_routing.mode` before redialing. Turning local-only on mid-call had
no effect on a call already in progress.
**Fix:** added the same local-only recheck at the top of the renewal
loop, before each leg's first connect attempt — refuses the next renewal
and ends the call gracefully instead of redialing Gemini. Deliberately
scoped to "refuse the next leg," not "cut audio mid-sentence on the
current one" (an abruptness question left alone as a UX judgment call
outside this fix).
**Probe:** [tests/gauntlet/test_ws_live_respects_local_only.py](../../../tests/gauntlet/test_ws_live_respects_local_only.py) (new test alongside the existing F20 pin)
**Evidence:** RED before fix (source-position pin: no `local_only` mention
inside the reconnect loop at all) → GREEN after (2/2) → RED again on
`git stash` revert → fix reapplied, stash dropped. Ran with the full
gauntlet suite: no interaction with other voice fixes.

### Fix #11 — KG indexer's "pinned" (local_only) flag was computed and then discarded (F34, BROKEN)
**File:** [src/agent_friday/services/knowledge_graph/indexer.py](../../../src/agent_friday/services/knowledge_graph/indexer.py)
**Finding:** `_resolve_model()` correctly computes `pinned=True` for
local_only (and TIER_2/3 chunks), but `_llm()` discarded it and always
called the general router (`_generate_text`), which can select a cloud
model via `capability_routing.reasoning` regardless of the `model=` hint.
Index.html's own KG-settings copy ("Local only = nothing ever leaves this
machine") was false for anyone who'd bound a cloud reasoning seat — an
ordinary, UI-encouraged action.
**Fix:** `_llm()` now branches on `pinned`: when True, calls
`_call_ollama` directly (bypassing the router entirely), the same pattern
F16 already established for voice.
**Probe:** [tests/unit/test_kg_indexer.py::TestLlmEnforcesThePin](../../../tests/unit/test_kg_indexer.py)
**Evidence:** RED before fix → GREEN after (2/2, including a no-op-shaped
check that unpinned/gated_cloud chunks still use the router as before) →
RED again on `git stash` revert → fix reapplied, stash dropped. Full
`test_kg_indexer.py` (20 tests) and full `tests/gauntlet/` green together
with F33.

**Batch verification, Fixes #10+#11 together:** first full run surfaced one
real regression, correctly caught rather than missed: `tests/api/
test_kg_reindex_route.py::test_reindex_tier_b_sync` asserts a full,
default-mode (`local_only`) reindex produces entities, mocking
`_generate_text` to return a canned extraction — exactly the pinned path
Fix #11 now routes through `_call_ollama` instead. The test's own intent
(does a local_only reindex actually extract entities) is still correct and
still worth asserting; only its mock target was now incomplete. Extended it
to also stub `_call_ollama` with the same tuple shape the api conftest's
own `stub_llm` fixture already uses elsewhere — a mechanical update to keep
testing the same real behavior after Fix #11's legitimate internal change,
not a weakening of the test. Both tests in that file pass after. Full
`pytest tests/unit tests/api` re-run after this correction — result below.

### Fix #12 — _generate_text never checked the router's refuse/vault_access verdicts (F35, BROKEN)
**File:** [src/agent_friday/services/model_router.py](../../../src/agent_friday/services/model_router.py)
**Finding:** `_generate_agent` (services/agent.py) has an explicit guard:
refuse early on `route.get('refuse')`, and never let a `vault_access=True`
local route fall back to cloud. Its sibling `_generate_text` — used by
briefings, the weekly digest/editorial, calendar/message drafting, wiki
identity bootstrap, and KG description-summarization — had neither check
at all. A vault-forced local route whose local leg failed fell straight
through to a real cloud call, defeating `vault_cloud_fallback`'s deny/warn
contract.
**Fix:** added the same two guards from `_generate_agent`, verbatim in
spirit: an early return on `refuse`, and a `vault_access`-gated cloud/
openai fallback for the local branch, plus threading `vault_access` into
`_mode_filtered_attempts` (which already accepted it, unused here).
**Probe:** [tests/gauntlet/test_generate_text_honors_vault_refusal.py](../../../tests/gauntlet/test_generate_text_honors_vault_refusal.py)
**Evidence:** RED before fix (2/3 fail cleanly; the ordinary-fallback
sanity check passes both before and after) → GREEN after (3/3) → RED again
on `git stash` revert → fix reapplied, stash dropped. Ran with
`tests/gauntlet/` (only the 2 pre-existing by-design reds) and the two
related pre-existing test files (`test_fallback_honours_mode.py`,
`test_model_router.py`) — no regressions.
**Batch verification, Fix #12:** full unit+API suite running now — result
to follow below once complete.

### Fix #13 — MCP tool-call sanitize/audit mechanism was never wired in (F36, BROKEN — same shape as F32)
**File:** [src/agent_friday/mcp_client.py](../../../src/agent_friday/mcp_client.py)
**Finding:** deliberately re-swept the MCP seam looking for F32's exact
pattern ("a real security mechanism exists, TRUST_LEVELS declares intent,
but nothing calls it") elsewhere, and found it again: `validate_tool_input`,
`validate_tool_output`, and `audit_tool_call` (extension_security.py) had
zero callers on either transport's real `call_tool()`. A sandboxed/
untrusted MCP server's output reached the agent's context with invisible/
control Unicode intact — the exact steganographic injection vector
`sanitize_unicode` exists to close — and `GET /api/security/mcp-audit`
returned an empty log forever, no matter how many tool calls happened.
**Fix:** both `MCPServerProcess.call_tool()` and `MCPServerHTTP.call_tool()`
now sanitize arguments and output and record an audit entry, trust-level-
aware via `self.trust_level` (F32 already added this to the stdio class;
extended it to `MCPServerHTTP` and to `MCPManager.load_config()`'s HTTP
branch, which never resolved one for remote servers before).
**Probe:** [tests/gauntlet/test_mcp_tool_call_sanitize_and_audit.py](../../../tests/gauntlet/test_mcp_tool_call_sanitize_and_audit.py)
**Evidence:** RED before fix (3/4 fail cleanly; the "trusted server output
unmangled" sanity check passes both before and after) → GREEN after (4/4)
→ RED again on `git stash` revert → fix reapplied, stash dropped. Full
`tests/gauntlet/` green (only the 2 pre-existing by-design reds) — no
interaction with F8/F28/F32.
**Batch verification, Fix #13:** full unit+API suite running now — result
to follow below once complete.

### Fix #14 — the always-on KG "related pages" context bypassed tier gating entirely (F37, BROKEN — real privacy gap)
**File:** [src/agent_friday/services/knowledge_graph/integration.py](../../../src/agent_friday/services/knowledge_graph/integration.py)
**Finding:** `model_router.py`'s system-prompt assembly deliberately tier-
gates its main content (a documented, hardened path after a real prior
incident). But `knowledge_context_block()` — folded into *every* system
prompt by `context_injection.build_injected_context()`, for whatever
provider is handling the turn — never checked sensitivity at all. Its
candidates carry a real plaintext excerpt of the page (the decrypted body,
whenever the vault is unlocked — the normal state), and a page in a user-
designated encrypted wiki section is TIER_3, but nothing downstream of
`structural_query.py` ever read that. Any TIER_3 page's real content could
reach any provider's system prompt, protected only by the generic PII-
pattern egress scan, not the deliberate tier-based redaction the rest of
the prompt gets.
**Fix:** `knowledge_context_block()` now excludes any candidate whose
`section` is in `wiki_engine._wiki_encrypted_sections()` — the exact same
check `wiki_graph._page_sensitivity()` already uses to mark a page TIER_3
— filtered before the shown-items slice so a safe candidate isn't
displaced.
**Probe:** [tests/gauntlet/test_kg_context_block_excludes_encrypted_sections.py](../../../tests/gauntlet/test_kg_context_block_excludes_encrypted_sections.py)
**Evidence:** RED before fix (an encrypted-section summary appears in the
block) → GREEN after (3/3, including two no-op-shaped sanity checks) →
RED again on `git stash` revert → fix reapplied, stash dropped. Full
`tests/gauntlet/` green (only the 2 pre-existing by-design reds).
**Batch verification, Fixes #13+#14 together:** full unit+API suite
running now — result to follow below once complete.

### Fix #15 — README.md's "greets you by voice" onboarding claim is false (F38)
**File:** [README.md](../../../README.md)
**Finding:** README said Friday "greets you by voice and walks you through
setup" on first run. Both onboarding surfaces are silent: the browser
`SetupWizard` is pure click-through React with no audio call anywhere
(`WIZARD_VOICES` there is a persona picker for *later*, not a greeting
played now), and the CLI `setup_wizard.py` drives the same steps via
`rich` text prompts only. The only "greeting" logic anywhere in the
codebase lives inside an already-open, user-initiated Gemini Live voice
call — not wired to first launch at all.
**Fix:** corrected the sentence to describe today's real onboarding (a
silent wizard, browser or `friday setup`) — a pure documentation
correction, no product decision involved; voice remains available
afterward as an opt-in mode, unaffected.
**Probe:** [tests/gauntlet/test_readme_onboarding_is_not_voice.py](../../../tests/gauntlet/test_readme_onboarding_is_not_voice.py)
**Evidence:** RED before fix → GREEN after (2/2, including a grounding
check that `setup_wizard.py` genuinely has no audio-playback call) → RED
again on `git stash` revert → fix reapplied, stash dropped. Full
`tests/gauntlet/` green (only the 2 pre-existing by-design reds).

### MCP and connector registration — formally CLOSED
2 consecutive clean sweeps achieved (rounds 5 and 6 tonight, after F32 and
F36 both landed) — this seam meets the audit's own exit bar. F9 and Q22
remain open/queued for you; closing the seam means fresh sweeps stopped
finding anything new twice in a row, not that every queued item on it is
resolved. See coverage.md for the full round-by-round history.

### Fix #16 — a second browser tab silently killed the first tab's voice call (F39)
**File:** [src/agent_friday/routes/voice.py](../../../src/agent_friday/routes/voice.py)
**Finding:** every other reason `/ws/live`'s reconnect loop ends a call the
browser didn't ask for (local-only turned on mid-call, F33; the handle-
retry giveup; a hard connect failure) sends a status/error frame before
`done.set()` — `_safe_send()` itself no-ops once `done` is set, so this
ordering is load-bearing. The "zombie fence" branch (fires when a second
`/ws/live` connection — e.g. a second browser tab — supersedes this one,
per the single global connection-generation slot) broke that pattern: it
set `done` with no notification at all. A second tab silently ended the
first tab's live call with zero signal to the user.
**Fix:** added the same `_safe_send(...)` call the sibling local-only
branch already has, before `done.set()`.
**Probe:** [tests/gauntlet/test_voice_zombie_fence_notifies.py](../../../tests/gauntlet/test_voice_zombie_fence_notifies.py)
**Evidence:** RED before fix → GREEN after (2/2, including a no-op-shaped
check that the sibling branch this fix was modeled on still notifies
correctly) → RED again on `git stash` revert → fix reapplied, stash
dropped. Full `tests/gauntlet/` green (only the 2 pre-existing by-design
reds).
**Batch verification, Fix #16:** full unit+API suite running now — result
to follow below once complete.

### Fix #17 — a scheduled task's traceback was silently discarded under the packaged app (F41)
**File:** [src/agent_friday/services/scheduler.py](../../../src/agent_friday/services/scheduler.py)
**Finding:** `dispatch()`'s exception handler — the single place every
builtin/agent_prompt scheduled task's failure lands — called
`traceback.print_exc()`, writing to stderr. The packaged app launches via
`pythonw` (confirmed in `packaging/windows/lib/Shortcuts.ps1`/`Heal.ps1`),
which has no stderr console at all, so under the real shipped runtime
that traceback went nowhere — not to `friday.log` (logging-only), not to
any console. The one-line failure summary still reached the user; the
traceback needed to diagnose *where* a task broke did not, for as long as
this handler has existed.
**Fix:** swapped `traceback.print_exc()` for `_log.exception(...)` —
this module's own logger, already used correctly elsewhere in the same
file — and removed the now-unused `import traceback`.
**Probe:** [tests/gauntlet/test_scheduler_task_failure_uses_logger.py](../../../tests/gauntlet/test_scheduler_task_failure_uses_logger.py)
**Evidence:** RED before fix → GREEN after (2/2, including a no-op-shaped
sanity check that other existing `_log` usages elsewhere are untouched) →
RED again on `git stash` revert → fix reapplied, stash dropped. Full
`tests/gauntlet/` green (only the 2 pre-existing by-design reds).
**Batch verification, Fix #17:** full unit+API suite running now — result
to follow below once complete.

## Retroactive revert verification — F1, F2, F8, F11, F12

These five landed before the red→green→red-on-revert discipline was
tightened partway through the night, so their original evidence was
"probe exists and is green," not the full three-step proof every later
fix got. Run properly just now, after Stephen flagged the gap:

- **F1 + F2** (`scheduler.py`, `provider_health.py`, `local_image.py`,
  commit `f35c0b5`): reverted all three files to their pre-fix content,
  ran `test_kg_nightly_reindex_registered.py` +
  `test_provider_health_google_comfyui_higgsfield.py` — 6/6 fail cleanly.
  Restored; 6/6 pass again.
- **F8** (`mcp_client.py`, commit `907ba0e`): `mcp_client.py` has been
  touched three more times since (F28, F32, F36), so a whole-file revert
  would have clobbered their fixes too — instead, extracted F8's own diff
  hunk and reverse-applied *only that hunk*. `test_mcp_restart_respects_
  disabled.py` fails cleanly (1/2) while all of F28's/F32's/F36's own
  probes stay green (12/12) — proof this fix is independently real, not
  just correlated with the others. Restored; all 13 green again.
- **F11** (`onboarding_copy.py`, commit `16450bd`): reverted to pre-fix
  content and found a real bug in the probe itself, not just a missing
  step — see the next item.
- **F12** (`egress_gate.py`, commit `c9a5a6f`): reverted to pre-fix
  content, ran `test_egress_gate_tool_calls_openai_shape.py` — 2/3 fail
  cleanly (the third is a no-op-shaped structural check, correctly
  unaffected either way). Restored; 3/3 pass again.

**F11's probe was silently broken since it was written, and this is the
kind of thing running the actual revert check catches that skipping it
doesn't.** `VAULT_LOCATION` is a triple-quoted multi-line string, and the
real pre-fix text wrapped exactly between "you" and "could" — the probe's
old-claim substring (`"not in any file you could open"`, written as one
line) never matched the real string's `"...you\ncould..."` on either side
of the fix, so the assertion passed vacuously whether the fix was present
or not. The one place this should have been caught — a helper documenting
that the old-claim string was discriminating — checked a hand-typed,
single-line reconstruction of the old text instead of the real one, and
wasn't even prefixed `test_` so pytest never ran it at all. Fixed now:
the probe's substring is `"not in any file"` (stays on one physical
source line, can't be silently defeated by a rewrap), and the
discriminating-fixture check is a real test that runs against the actual
multi-line text. Re-verified: 2/3 fail cleanly against the reverted file
(now catching both F10's and F11's old claims correctly), 3/3 pass
restored. The underlying `onboarding_copy.py` fix itself was always
correct — read directly, it does what F10/F11 claim — only the probe that
was supposed to prove it had the bug.

All five fixes are now retroactively confirmed with the same evidence
standard as the other 17. Full `tests/gauntlet/` suite re-run after all
five checks: only the 2 pre-existing by-design reds, no regressions.

## HANDOFF ITEM RESPONSES (2026-09-04, Stephen's 00:33 check-in)

**1. GPU context during pytest (rule crossed).** Root-caused via static
reading, not by re-running nvidia-smi (per the standing "don't run it at
all" rule — the investigation itself had to stay GPU-safe). `services/
sensitivity_classifier.py:240`, `pipeline/context_pruner.py:152`, and
`services/prewarm.py:65` all call `SentenceTransformer(name)` with no
`device=` argument. sentence-transformers' documented default (stable
across the pinned `>=2.2` range) auto-selects CUDA via
`torch.cuda.is_available()` when a GPU is present — that's almost certainly
what nvidia-smi caught at 00:31 (the classifier's Layer 3 lazy-loading
during test collection/execution), not an actual conversational-model load.
**Remediation:** `CUDA_VISIBLE_DEVICES=""` set for every pytest invocation
from this point forward — the NVIDIA driver reports zero devices with this
set, so `torch.cuda.is_available()` returns False and no context can be
created, full stop. This is a launch-time environment variable, not a code
change, so it doesn't touch `tests/conftest.py` (off-limits — existing test
file). Not independently re-verified via nvidia-smi (deliberately, to avoid
touching the tool again) — the confidence here is the well-documented,
deterministic CUDA_VISIBLE_DEVICES contract, not a live re-check. **Open
question for Stephen, not decided unilaterally:** should `tests/conftest.py`
itself set this for every contributor's run? That's a real test-suite
correction I'm not positioned to make (off-limits file), noted here so it
doesn't get lost.

**2. claims.jsonl bookkeeping (fixed).** Was empty; coverage.md said every
corpus source UNSTARTED despite progress.md describing a completed
round-1 extraction pass — a real bookkeeping failure, not a wrong-file
mixup. Backfilled `claims.jsonl` with C1-C90, transcribed from the round-1
and round-2 agents' actual reports (quotes + locations + linked finding or
an honest `noted_not_independently_chased` status where a claim was read
but not individually traced to a verdict). coverage.md's corpus table
corrected to match.

**3. Suite verification rests on exit code alone — RESOLVED.** Re-ran
without the `tail -5` pipe — the summary line was STILL missing from the
captured output even reading the full file directly (progress dots to
100%, then just `[exited with code 0]`, no "N passed" line at all). So the
pipe wasn't the cause; something about this background-capture path drops
pytest's final terminal-writer output specifically (pytest.ini's own
`addopts` has nothing that would suppress it — confirmed, no custom
reporter, no `-p no:terminal`). Rather than keep guessing at the terminal
capture, switched to `--junit-xml=<path>`, which pytest writes to disk
directly regardless of what happens to the captured stdout — immune to
whatever swallows the terminal summary. **Result:**
`<testsuite tests="6697" errors="0" failures="0" skipped="8" time="601.669">`
— 6697 test items genuinely executed (6689 passed + 8 skipped), zero
failures, zero errors, in 601.67s (~10 minutes, matching the aborted Fable
session's independent observation that this suite exceeds 10 minutes in
this environment). Deterministic proof the suite actually ran rather than
being vacuously collected-and-skipped. This XML run happened after Fix #5,
so it confirms the full current state (Fixes #1-#5 together) is clean.

**Side observation, not chased further:** 6697 is far more than
tests/README.md's and pytest.ini's own claimed "~1,870"/"~1850". Most
likely explanation is counting methodology (JUnit counts every
parametrized case separately; the doc figures may count test functions
pre-expansion) rather than the doc being wrong outright — flagged as a
corpus item worth someone reconciling, not asserted as a confirmed defect.

**4. F10 sharpened.** See the rewritten queue item above: posed as an
explicit (A) fail-closed / (B) fall-back-with-notice choice with
consequences for each, and the copy corrected now regardless of which way
that goes (Fix #5, just below the queue item).

## QUEUED FOR STEPHEN

### Q26 — the root cause behind every cost-metering gap tonight (Q6, Q7, Q11, Q13), and an actionable priority order
Dispatched per your explicit request to find the systemic reason rather
than keep finding individual instances. Confirmed: text-chat metering
works because it's a side effect of the cloud/local fallback ladder that
already had to exist; image/video/music/voice generation each have
exactly one provider, so no router — and thus no metering chokepoint —
was ever built for them. A true unified fix like egress's single
`gate_text` primitive isn't realistic, because cost needs the RESPONSE
in a different shape per modality, not just the request text the way
sensitivity does. But the remaining gap splits cleanly:
- **Mechanical, just needs a verified rate (not a design call):** Gemini
  image generation's `usage_metadata` is already present on every
  response and read by nothing; ElevenLabs' character count is already
  in scope right after the existing egress gate; the 5 extra provider
  catalogs (Q11b) are already fully wired through the same chokepoint
  every other provider uses — their $0 is a missing pricing table, not a
  code gap. None of these were implemented tonight anyway: fabricating a
  plausible-looking dollar rate for a real financial ledger without
  verifying it against each provider's actual current published pricing
  would be a new defect, not a fix.
- **Needs real per-provider design work:** Veo video and Lyria music are
  long-running "operations" with no simple usage object — billing has to
  be computed from requested clip length, not read off a response.
  Gemini Live bills per session-second across a persistent websocket,
  needing a connection-lifetime timer.
- **The durable fix**, once the mechanical items land and have set a
  precedent: a required-usage-record interface every future non-chat
  provider integration must satisfy, with a hard failure if it's skipped
  — mirroring how egress already hard-fails a skipped seal.

Full detail and the recommended order to tackle these in is in
findings.jsonl Q26.

### Q24 — manual "Run Now" can race a schedule into running twice concurrently
`dispatch()`'s only concurrency guard is skipped specifically for manual
triggers (`if sid in _RUNNING and not manual`) — click Run Now while a
schedule is already running (or click it twice) and the same task
executes on two threads at once, racing writes to the same record/file.
Worse: the scheduler's own nightly KG reindex bypasses even the *manual*
route's lock entirely, so a user clicking "Reindex now" in the UI while
the 03:30 schedule is mid-run gets two fully concurrent rebuilds of the
same on-disk graph. Not fixed because the `and not manual` exemption may
be a deliberate escape hatch (forcing a re-run past a stuck lock from a
crashed prior run) rather than an oversight — removing it outright could
break that legitimate case. Decide: should Run Now refuse outright while
a run is in progress, queue behind it, or keep the override with a real
lock instead of none? Evidence in findings.jsonl Q24.

### Q25 — a whole Settings→Scheduler UI section is dead, polling but rendering nothing
Fully implemented (list/toggle/run-now/delete/history, 5s polling) but
none of its handlers or fetched data are referenced in any JSX — the real,
working scheduler UI lives in a separate Workflows-tab component with
different handler names. Low severity; decide whether to delete the dead
section or build it out as a second real surface. Evidence in
findings.jsonl Q25.

### F40 — the Federation panel's per-peer ask/allow/block trust control does nothing
The dropdown reads as a real lever (color-coded amber/green/red, defaults
to "ask") — marking a peer "block" should stop its federation traffic.
It can't: the UI's save call POSTs to a route that doesn't exist
(swallowed by an empty error handler, so the click looks like it worked),
and even the underlying DB column is never read anywhere that would gate
behavior — every peer is processed identically regardless of its pref.
Compounds the already-queued F22 (a peer's settings-sync push can
overwrite your real settings.json): the one control meant to stop a given
peer stops nothing. Not fixed because the right semantics for "ask"
specifically (presumably: pause for interactive approval, a flow that
doesn't exist yet) is a real federation-protocol design decision, not
mechanical wiring — building only "allow"/"block" would just move the
false promise onto "ask." Evidence in findings.jsonl F40.

### Q23 — F31's own cost cap can silently, permanently drop chunks from multi-chunk files (gated_cloud mode only)
A side effect of tonight's own F31 fix, not present before the cap
existed: the manifest tracks completeness per FILE, not per chunk, so if
one chunk from a multi-chunk source succeeds while another from the same
file is skipped by `MAX_CLOUD_EXTRACT_CALLS`, the whole file gets marked
"up to date" and the skipped chunk's entities never make it into the graph
— silently, until that file is edited again. Only affects `gated_cloud`
mode with a cap-sized backlog; `local_only` (the default) is unaffected.
The clean fix requires restructuring how `reindex_tier_b` batches its
per-file manifest writes — a real, non-trivial change to a function this
audit has already touched twice tonight — so it's queued rather than
rushed this late in the run: a data-completeness gap, not a money or
security one. Evidence in findings.jsonl Q23.

### Q22 — the MCP allowlist is keyed by server name, not by its approved command (minor)
Editing an already-approved server's command to something new (still only
warn-tier — block-tier findings are never bypassed) inherits the old
approval with no re-review, because `is_allowlisted()` only checks the
name. Two reasonable designs exist (name-keyed vs. command-fingerprint-
keyed) and which one you want is a UX/friction trade-off, not a bug with
one answer. Evidence in findings.jsonl Q22.

### Q21 — no signal anywhere tells you a background job has been running unusually long
The direct answer to "how would we have noticed F31 sooner": nothing would
have. The KG reindex status endpoint has no `started_at`, the reindex
never registers as a tracked task/orb so it never gets an `elapsed` field
anywhere, and `scheduler.py`'s builtin-kind tasks (unlike `agent_prompt`-
kind ones, which have an explicit 1800s timeout) have no wall-clock cap at
all. A real, valuable defensive improvement, but choosing a timeout value
and touching the central scheduler dispatcher is a design decision with
real blast radius — queued rather than guessed at unattended this late in
the run. Evidence in findings.jsonl Q21.

### Q19 — SEVERE: local_only/local_preferred's own promise is false for ordinary interactive chat
See the top-of-file section above — this is the single most important
queued item tonight. Full detail in findings.jsonl Q19.

### Q20 — task_overrides.voice is permanently inert (found alongside Q19)
`docs/CONFIGURATION.md` documents a `task_overrides.voice` config key, and
`routing/model_router.py` has three `TaskType.VOICE`-gated branches meant
to honor it — but `classify_task()` can never return `TaskType.VOICE`, and
nothing else injects it, so all three are dead code and the documented
config key silently does nothing. Lower severity than Q19 (a no-op setting,
not a privacy-promise violation) but queued rather than touched alongside
Q19 in the same sensitive routing function tonight — making it reachable
means deciding how voice turns should be classified, a real design choice,
not a one-line wiring fix. Evidence in findings.jsonl Q20.

### Q17 — "Go Off Record" claims "for this session" but nothing ever resets it
`off_record` is correctly read everywhere it's checked (not the F3/F6/F14/
F15/F24 unread-setting class) — the defect is that no session-boundary,
new-chat, or app-restart ever turns it back off, contradicting the toggle's
own "Disable logging for this session" copy. Inverse-severity sibling of
F3 (F3 promised automatic deletion that never happens; this promises
automatic resumption that never happens). Fix is a real choice between
wiring a real reset boundary or rewording the toggle — queued. Evidence in
findings.jsonl Q17.

### Q18 — `looks_heavy()`, the content-based "should I ask before this?" heuristic, has zero callers
`services/workflow_plan.py`'s stated premise ("If Friday thinks work might
be heavy, she asks") is backed by a real heuristic that nothing ever calls
— the live "ask" trigger is a completely different, load-time-only
heuristic (`pause_forecast.py`) with no awareness of what the request
actually asks for. Matches F1's orphaned-function shape, but combining the
two heuristics is a real design choice (changes how often the pause-warning
interrupts the user), not a one-line wiring fix — queued. Evidence in
findings.jsonl Q18.

### Q16 — the daily "short-production" creation bypasses its own pipeline's human-review checkpoints (found generalizing F31)
While generalizing F31's defect shape across the rest of the codebase (see
Round 6 below), a sweep found something adjacent but distinct, worth your
judgment even though it isn't the same bug. `creative_pipeline.py`'s
full-production template marks 3 of 6 stages `checkpoint: True`, with
comments naming exactly why: *"human reviews the look before any spend"*,
*"cost gate — video is the expensive call"*, *"final review before the work
is published"*. `services/creations.py:456` — the unattended, once-a-day
`short-production` mode of the daily-creation builtin task (08:00 Central) —
runs that same template with `until_checkpoint=False`, auto-advancing
through all three. It's the only caller of this template that runs
unattended: an overnight job can generate a real Veo video (the single most
expensive call in the whole creative pipeline) and publish it to
`~/Desktop/friday-creations`, with no human ever having looked at it first.

**The question, precisely, mirrors F10's shape:** (A) make this one caller
stop at its checkpoints like every other caller — consequence: the "she
made something overnight" flagship daily-creation feature (docs/
ACTION_CREATION_SPEC.md Pillar 2) stops being fully autonomous for this one
mode; a busy/offline user's run just waits for approval instead of
finishing. Or (B) keep today's behavior — consequence: an autonomous
overnight job keeps being able to spend on the most expensive stage in the
pipeline and publish without review, budget ceiling ($0.50/day) aside.
**Not fixed** because it trades off the feature's "come alive
autonomously" premise against a real spend/oversight gap — your call, not
mine. Evidence: `creative_pipeline.py:185,205,224` (the checkpoint comments),
`creations.py:451-456` (`until_checkpoint=False`), `scheduler.py` (08:00
builtin registration per docs/ACTION_CREATION_SPEC.md's own table). Full
detail in findings.jsonl Q16.

### Q1 — context_retention_days is decorative (F3, BROKEN)
Settings → Privacy → Context Logging → Retention Period persists and reads
back (passes the settings-readers-check proxy) but nothing ever deletes a
log file by age — only a fully manual `DELETE /api/context/range` (user
picks a date range and types "DELETE") ever removes anything. **Not fixed**
because writing an automatic, unattended deletion sweep for the user's own
log data is a real blast-radius decision (a bug deletes more than intended)
that should not ship overnight without Stephen looking at it. Options: wire
a scheduled sweep using the existing `_context_log_files()`/delete-range
logic, or change the UI copy to stop implying automatic enforcement.
Evidence: `core/__init__.py:1495` (DEFAULT_SETTINGS), `routes/context.py:113`
(read-back), `routes/context.py:150-164` (the only deletion path, manual),
scheduler.py's full builtin roster (no retention job).

### Q2 — RESOLVED, was not a live defect (F4 → H9)
Independently re-verified: the vision-path fix already landed 2026-08-23
(commit 4607bd9, confirmed ancestor of HEAD). `routes/chat.py` reads
`model_routing.mode` before any network call, prefers the local vision seat
in local/local-preferred mode, and blocks the cloud call entirely (not a
fallback) when local-only and local vision fails. KNOWN_ISSUES.md's "still
open" text for this item is stale by 18 seconds of git history (the doc
commit landed 18s after the code fix, same day) — not a live vulnerability.
See H9 in findings.jsonl.

### Q3 — KNOWN_ISSUES.md's screenshot/local-only entry is stale documentation (new, trivial)
`KNOWN_ISSUES.md:871-905` still lists the local-only screenshot leak as open
(§5), citing pre-fix line numbers and pre-fix behavior, even though the fix
landed the same day (see Q2/H9). This is a one-line-of-reasoning doc move
(cut the entry to §2 "Fixed in this release," cite commit 4607bd9), but per
Stephen's instruction ("a case where the claim should move rather than the
code" queues), it is queued rather than edited unilaterally — low-risk, easy
for him to wave through.

### Q4 (observation, not yet a confirmed live gap) — `local_preferred` mode silently falls back to Gemini on local-vision failure
The F4 verifier noted: in `local_preferred` routing mode, if local vision
fails, `routes/chat.py` falls through to Gemini with no explicit user-facing
notice beyond a `vision_cloud` ledger event. This matches the mode's
documented semantics ("prefer local," not "require local"), so it is
probably not a THREAT_MODEL contradiction — but worth checking against
`local_preferred`'s own UI help text before closing. Not yet checked; low
priority, noted for a future sweep.

## Observed anomaly, not a confirmed finding (2026-09-04)

One `-k "voice"` test run printed `[MEMORY] sentence-transformers embedder
unavailable, using ChromaDB default: [WinError 10038] An operation was
attempted on something that is not a socket`, followed by a live download
of ChromaDB's default ONNX embedder (~79MB) from the internet — inside
what tests/README.md calls an "offline suite" that "needs... no network."
Checked whether this was caused by the CUDA_VISIBLE_DEVICES="" remediation
from earlier tonight: it is NOT — `SentenceTransformer('all-MiniLM-L6-v2')`
loads cleanly in isolation both with and without that env var set. The
WinError 10038 (a Windows socket-handle error, often a multiprocessing/
resource-tracker artifact) more plausibly comes from running many
sequential pytest invocations back-to-back in one long session tonight
(this session's own test methodology) than from a defect in the shipped
suite under normal single-invocation use. Not chased further — recorded
honestly as an observed anomaly with uncertain cause and NOT logged as a
confirmed finding, since I could not reproduce or attribute it cleanly.
Worth a look if it recurs.

**Second observed anomaly (2026-09-04, ~03:20), checked and ruled out as
unrelated to Fix #8:** a full `pytest tests/` run (7190 items, after Fix #8)
reported 4 failures. 2 are expected/by-design (F3's and F30's deliberately-
red queued-finding probes under `tests/gauntlet/` — this was the first run
of the whole `tests/` tree rather than just `tests/unit tests/api`, so
these appeared for the first time in a full-tree run tonight, not a new
regression). The other 2 —
`test_model_plan.py::test_the_indexer_falls_back_to_a_tool_capable_model`
and `test_nemo_voice.py::test_nemo_models_ready_false_when_uncached` — are
unrelated to knowledge_graph/indexer.py by subject matter, and both PASS
cleanly in isolation regardless of whether Fix #8 is present or reverted
(checked both ways via git stash). Conclusion: pre-existing test-order/
shared-state flakiness somewhere in this ~7200-test suite, not caused by
Fix #8. Not chased further — same category as the WinError 10038 anomaly
above, flagged honestly rather than silently ignored.

**Third observed anomaly (2026-09-04, ~07:03), understood immediately, not
a flake:** the first full-suite run after Fix #17 (F41) reported 1
failure, `test_notifications_engine.py::TestListNotifications::test_
same_priority_newer_first`. That test is a pre-existing, deliberate
`@pytest.mark.xfail(strict=True, reason="_now_iso() strips microseconds
... unstable ... known limitation, not a test error")` — it pushes two
notifications 10ms apart and asserts the newer one sorts first, which is
genuinely a coin flip against the real 1-second timestamp resolution
depending on whether a wall-clock second boundary falls between the two
pushes. `strict=True` means an unexpected PASS on a lucky roll counts as
a suite failure, not just the expected fail. Re-ran the full suite once
more with no code changes: clean, 6701/0/0/8 — confirms this was that
known, already-documented, self-describing timing coin-flip landing on
the "unexpectedly passed" side once, not a regression from Fix #17
(scheduler.py and notifications_engine.py are unrelated modules).

## Scope addition (2026-09-04): visual-notes.md, capture-only

Stephen: a visual/aesthetics pass on the liquid UI workspaces is a
different loop with a different bar (no red-then-green proof for taste),
and shouldn't be merged into this one. This audit's bar stays exactly as
written — no visual critic added, no judging appearance. But when a finder
or critic incidentally notices something about a workspace's look or
usability (confusing, empty when it shouldn't be, two surfaces disagree
visually, a control is easy to miss, a layout obscures what matters,
something looks unfinished), it now gets a line or two in
[visual-notes.md](visual-notes.md) — location only, no investigation, no
fix, no verdict. UI copy that's false and any index.html/app.html
divergence stay in findings.jsonl as before (those have verdicts). Folding
"note anything workspace-visual you happen to see, don't chase it" into
every future finder/critic dispatch prompt from here on.

**Also raised: Workspace Studio's discoverability.** Stephen's framing was
that it's shipped and real (full undo) but reachable only by asking Friday,
with no UI surface at all — possibly UNREACHED-adjacent in this audit's
terms. Checked the code rather than accepting the premise: it's REFUTED.
`index.html`'s `FWin` component (the real, live floating-window wrapper
every open workspace renders through) has a 💬 button in every workspace's
title bar, wired to the real `/api/workspace/<id>/chat` etc. routes and the
real backend — a genuine, if easy-to-miss, discoverable UI entry point (see
findings.jsonl H12). The premise didn't hold, so this isn't an UNREACHED
finding; the real gap is a visual/discoverability one (small unlabeled
icon, no onboarding), which is exactly what visual-notes.md is now for —
logged there, not investigated further.

## Handoff from the session that wrote this prompt (2026-09-04, ~00:15)

The Fable 5.1 session that authored `docs/gauntlet-loop-prompt` briefly began
executing it before standing down on discovering this session already
running (verified: branch `docs/gauntlet-loop-prompt`, directory renamed to
`docs/audits/gauntlet-2026-09-03-fable-aborted/`, its findings/claims files
are genuinely empty, its worktree removed — no collision, nothing to merge).
It passed forward corrections, adopted here:

1. **Exit criteria corrected** (coverage.md updated): "every claim holds
   blind" is unsatisfiable for a seam with one queued (not landed) BROKEN
   finding. Corrected bar: two clean fresh-context sweeps AND every claim
   carries a verdict with an artifact (HOLDS+observation, or BROKEN/
   UNREACHED+a red probe pinning it, fixed or queued). Retroactively applied
   to F3 (queued) by adding tests/gauntlet/test_context_retention_days_is_decorative.py,
   which stays red by design until Stephen decides Q1.
2. **GET is not automatically safe.** `provider_health.inference_probe`/
   `check_all(deep=...)` can load a model or spend a real cloud call from a
   plain-looking status/health/residency route. Rule from here forward: read
   the handler before GETting any health/status/probe/residency route on the
   live localhost:3000 instance; treat any route calling inference, a
   provider, or the network as mutating. (I have not GETed anything on the
   live instance this session — all work so far has been static analysis of
   the worktree — so nothing to remediate, just adopting the rule forward.)
3. **Startup-seam parking reconfirmed independently.** Their evidence
   (`_residency_boot` on any non-testing boot, docstring says it boots the
   GPU; `_mcp_boot()` unconditional; 9 call sites hardcode the Ollama default
   so a scratch setting doesn't redirect them all; no flag disables any of
   it) matches this session's own static read from earlier tonight. Startup
   seam stays parked for dynamic boot.
4. **Suite timing:** a full run can exceed 10 minutes, especially with
   another session active in the main tree. Confirmed here: this round's
   `pytest tests/unit tests/api --tb=no -q` ran a long time and, with `-q`,
   produced no visible summary line in the captured output — only progress
   dots to 100% and `[exited with code 0]`. Read the exit code, not the
   summary line. (Result: exit 0, no failures — this batch's 2 fixes are
   clean against the full suite.)
5. **Concurrency cap:** 4-6 concurrent finder agents max on this machine,
   not 13. Round 1 used 6, within bound; keeping to that going forward.
6. **~/.friday read policy for every subagent, adopted verbatim:** may read
   `settings.json`, `startup-report.json`, `schedules.json`, `*.log`; never
   `credentials.json`, vault files, `*.key`, `chat_history*`, `conversations/`,
   or audio cache; describe structure only, never quote personal content.
   Folding this into every future finder/agent prompt.
7. **tests/README.md's "~45 seconds" / "~1,870 tests" are corpus claims to
   judge, not planning facts** — already treated that way (see F5-adjacent
   candidate finding from round 1's README/tests agent about the "240
   routes" claim not being what the test actually asserts); reconfirmed here
   given the suite actually took far longer than 45s in this environment.
8. **Verified independently, not just taken on trust:** `tests/conftest.py:55-61`
   resolves `_ROOT`/`_SRC` relative to `conftest.py`'s own file location
   (this worktree's `tests/`), so the worktree's `src/` — not the main
   checkout's — is what gets prepended to `sys.path`. Confirmed empirically:
   earlier tracebacks this session showed `agent_friday.services.scheduler`
   imported from `...worktrees\gauntlet-audit-2026-09-03\src\...`. Fix
   verification this session was testing the right code.
9. **State-directory naming: NOT renamed.** The aborted session suggested
   dating the directory for the night the pass runs. This session's
   directory (`gauntlet-2026-09-03/`) is not the aborted one, was never a
   collision (confirmed above), and already has real committed history under
   that name — renaming now would only create churn. Judgment call: kept
   as-is.

## Round log

(newest first)

### Round 6 — live production cost-leak investigation (2026-09-04, ~03:00-03:20)
Dispatched by Stephen's own urgent message reporting real, ongoing overnight
spend on the live app. Investigated and resolved — see the "READ THIS FIRST"
section at the top of this file and finding F31. Summary: his named
hypothesis (`/api/health` billing per poll) is refuted with direct evidence;
the real mechanism is an unattended, still-running KG Tier B reindex whose
extraction loop has no cap on cloud-eligible LLM calls, discovered via live
read-only GETs to `/api/knowledge-graph/reindex/status` and `/api/processes`
(both confirmed side-effect-free by reading their handlers first — no route
that touches inference or cost_meter was ever hit) plus static tracing
through `model_router.py` and `knowledge_graph/indexer.py`. Fixed in this
worktree with full red-green-red-on-revert proof (Fix #8); cannot stop
tonight's live spend, which continues per Stephen's own explicit
instruction not to touch the live process overnight.

### Round 0 — setup (2026-09-03)
Worktree + scaffold created. No findings yet. Launching claim-extraction
agents and first-wave finders next.

### Round 1 — first wave launched (2026-09-03, in progress)
6 background agents launched, all fresh-context, all instructed not to
mutate the live app or this worktree:
1. Claim extraction: THREAT_MODEL.md, KNOWN_ISSUES.md, docs/FILE_GRANTS.md
2. Claim extraction: README.md, RELEASE_NOTES.md, docs/CONFIGURATION.md,
   docs/API.md, tests/README.md
3. UI claim extraction + direct probe of the `settings-readers-check`
   pre-commit hook (it printed "OK" on a commit that touched none of the
   files it claims to check — flagged for verification, not yet a finding)
4. Finder: knowledge-graph ingest → retrieval seam (checking for
   recurrence of the "indexer calls a nonexistent method, swallowed" class)
5. Finder: settings write → read seam, privacy/egress toggles specifically
   (checking for recurrence of the "Saved but nothing reads it" class)
6. Finder: provider-health / router-to-seat seam (checking for recurrence
   of the "special-cased two providers, six misreport" class)

None have reported back yet. Meanwhile doing static-only startup-wiring
analysis myself (reading server.py's daemon block / _residency_boot) to
either clear or park the dynamic second-instance boot question — see
JUDGMENT CALL 1 above. Not booting anything yet.
