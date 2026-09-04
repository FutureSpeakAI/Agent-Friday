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
