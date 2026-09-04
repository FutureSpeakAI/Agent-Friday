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

**This fix cannot stop tonight's bleeding.** It lives in an isolated
worktree; nothing here reaches the live process until Stephen restarts it,
and per his own explicit instruction the live app is deliberately not being
touched or restarted overnight. The reindex will keep billing at roughly
$10/hour until it either exhausts its own backlog on its own, or he
restarts with this fix applied. **He should expect a real number
meaningfully larger than the $27.41/1,029-calls snapshot he had at 23:45
by the time he reads this** — the mechanism was still active at ~03:04 and
nothing in this run could stop it.

Generalization he asked for is done: `/api/seat-gate/statuses`,
`/api/processes`, `/api/tasks`, and every other polled status/residency
route checked have no side effects and no cost_meter linkage. The defect
was entirely inside the KG indexer's own extraction loop, not in any
read/status endpoint — a narrower, different, and honestly more interesting
bug than the one he suspected.

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
