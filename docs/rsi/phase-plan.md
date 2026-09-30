# RSI Next-Phase Spec — Observability, Truthful Status, and Local-Seat Optimization

> Status: plan. Phase 0 is specified in `phase0-workflow-scope-spec.md` and not yet
> implemented; later phases are unstarted.

> Written by the frontier spec seat (Fable 5.1 Max via Agent Friday) for **Bonsai2 as executor**.
> Each phase is sized as ONE RSI implement run. Do not attempt multiple phases in a single run.
> This spec absorbs and supersedes `next-targets.md`.

---

## Non-negotiable rules (learned the hard way — violating any of these fails the run)

1. **Red-first tests.** Every defect gets a failing test BEFORE the fix. Run it failing, then passing. No test, no fix.
2. **Venv only.** All checks run through the project venv interpreter (`.venv/Scripts/python.exe`). An ImportError naming a common package (flask, requests) means WRONG INTERPRETER before it means real failure.
3. **Artifacts over labels.** A step's verdict comes from what it wrote (files, commits, test output), never from its own status string.
4. **No absolute paths in the tree.** The repo is public. Specs, docs, and code use `~`-relative or repo-relative paths. The pre-commit scanner blocks violations; do not bypass with `--no-verify`.
5. **Sensitive subsystems are off-limits unattended.** No changes under `privacy/`, `governance/`, `egress_gate.py`, `sensitivity_classifier.py`, `credential_store.py`, `vault_passphrase.py`, or auth/session handling in an unattended RSI run. Flag for human-reviewed work instead.
6. **Empty-input behavior is preserved.** Callers that rely on default behavior (daily briefing, triage) must not break. State the "must not change" surface in every phase.
7. **Commit your wins.** A verified fix goes on a branch (`rsi/<topic>`) the same run it passes. No pushes, no tags.

Required checks for every phase (all green before a phase is "shipped"):

```
pytest tests/unit tests/api -q
python scripts/check_imports.py
ruff check --select E9,F63,F7,F82 .
python scripts/check_gated_prompt_callers.py
python scripts/check_settings_readers.py
python scripts/check_stale_model_names.py
```

---

## Phase 0 — Harden the unattended paths the loop itself runs on

**Why first:** the ROADMAP verified open defects in `run_workflow_chain` and the scheduler — the exact code path RSI runs travel. The loop protects itself before it builds anything new.

- Audit `run_workflow_chain` and scheduler entry points for: unhandled step exceptions, missing per-step timeouts, and state that survives a crashed step.
- Write failing tests reproducing each verified defect, then fix minimally.
- Must not change: workflow YAML schema, existing step semantics for currently-green workflows.

**Ground truth:** new tests in `tests/unit/test_workflow_chain_hardening.py`, red before / green after.

## Phase 1 — Truthful status (artifact-based verdicts + honest cancellation)

**Why:** status labels contradicted the filesystem three times in one week ("interrupted" while writing files; "completed" with zero diff).

- Each workflow step gets a computed verdict: `shipped` (artifacts changed + checks green), `no-op` (completed with empty diff), `failed` (checks red or exception). Verdict derives from `git status`/diff + test results, NOT from step self-report.
- Add cancellation for running workflows, or — if genuinely impossible in the current process model — an explicit `cancellation_unsupported` reason surfaced to the caller instead of silence.
- Must not change: existing `workflow_status` response fields (add, don't remove).

**Ground truth:** a test that runs a deliberately no-op step and asserts verdict == `no-op`; a test asserting cancel either stops a run or returns the explicit unsupported reason.

## Phase 2 — Notification pipeline with reasoning transparency

**Why:** the user cannot be pinged between turns; task-completion notifications carry no "why". Sequenced AFTER Phase 1 because notifications built on lying status ship the lie.

- Push channel: workflow/task completion events surface as UI toasts (and optionally OS notifications).
- Payload carries the reasoning thread: step verdicts (from Phase 1), artifacts written, and a deep-link.
- Deep-links open the process log in a holographic UI window or a Chrome tab (same spin-out pattern as the chat window).
- Must not change: existing task-tray behavior for tasks that predate the pipeline.

**Ground truth:** unit tests for event emission + payload shape; a manual verification note listing the clickthrough path.

## Phase 3 — ROADMAP cheap wins

- Cloud-voice wiring, startup receipts, single authoritative core-tool list (per ROADMAP items already verified as real gaps).
- Each is small; batch only if one run's diff stays reviewable.

**Ground truth:** per-item tests where testable; startup receipt visible in logs on boot.

## Phase 4 — Bonsai2 serving optimization (benchmark first, all reversible)

**Current config (verified):** llama.cpp at :8090, PTQ1_0 quant (1.75 bpw, 5.9 GB), 32K of a 262K trained context window.

1. **Baseline benchmark BEFORE any change:** tokens/sec, time-to-first-token, and a fixed code-task eval set. Record in `docs/rsi/bonsai2-bench.md` (repo-relative paths only).
2. **Context raise:** `n_ctx` 32K → 64K (then 128K if VRAM headroom confirmed). Re-benchmark.
3. **KV-cache quantization:** enable q8_0 KV if supported; re-benchmark.
4. **Quant swap evaluation:** acquire a Q4_K_M-class quant (~15 GB); compare on the same eval set. Ship only if code-task quality improves without unacceptable latency.
5. **Low-temperature implement runs:** RSI implement stages call the local seat at temperature ≤ 0.2.
- Every change is a launch-flag or model-file swap — document the exact rollback for each.
- Must not change: the serving endpoint/port contract other components rely on.

**Ground truth:** before/after benchmark tables committed; rollback instructions verified by actually rolling back once.

## Phase 5 — The loop cleans its own room

- RSI runs archive their working files (patch scripts, scratch output) to `~/.friday/cleanup-archive/` instead of leaving them in the repo root.
- Verified wins are committed to an `rsi/<topic>` branch automatically at the end of a green run (branch only; never push).
- Specs written by the frontier stage use repo-relative paths so they can live in `docs/rsi/` without tripping the scanner.

**Ground truth:** a completed run leaves `git status` clean except the intended branch commit; scanner passes on a committed spec.

---

## Sequencing

Phases run in order; a phase ships only when its ground truth and the full check suite are green. If a phase's target turns out already-fixed (see: the Gmail re-selection incident), the verdict is `no-op` and the loop advances rather than claiming credit.
