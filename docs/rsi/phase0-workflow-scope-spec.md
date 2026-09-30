# RSI Spec — Phase 0: Workflow-Chain Scope Hardening

> Status: specified, not implemented. The red-first tests it names are kept out of
> the suite until the scope exists, so that the documented checks stay green.


**Executor:** Bonsai2 (local, 27B). This spec does the thinking; you do the
mechanical work. Follow it exactly. Do not redesign. Do not expand scope.

**Phase source:** `phase-plan.md`, Phase 0.
**Ground truth:** `tests/unit/test_workflow_chain_hardening.py` — pre-written,
currently FAILING. Your job is done when it passes and nothing else breaks.

---

## Non-negotiables (from phase-plan.md)

1. Run ALL checks through the project venv: `venv\Scripts\python.exe -m pytest ...`
   An import error naming a common package means WRONG INTERPRETER, not a real failure.
2. Repo-relative paths only in any file you write.
3. Do NOT touch: `src/agent_friday/privacy/`, `src/agent_friday/governance/`,
   `services/egress_gate.py`, `services/sensitivity_classifier.py`,
   `services/credential_store.py`, `services/vault_passphrase.py`.
4. Empty/default behavior for existing callers must not change.
5. If a change is already present (target re-selection), record a `no-op`
   verdict and STOP. Do not re-apply.

---

## The defect

Chain steps run unattended with unlimited authority:

- `services/subagents.py` defines five built-in scopes but no `workflow-step`
  scope, and `scope_check()` returns ALLOW for unknown task ids (fails open).
- Three dispatch sites spawn step tasks with no `scope=` argument:
  1. `services/agent.py` — `run_workflow_chain` (~line 3430)
  2. `services/agent.py` — `_retry_chain_step` (~line 3480)
  3. `services/agent.py` — `_advance_task_chain` (~line 3540) ← the path every
     mid-chain step actually travels; missed by ROADMAP.md
- `services/scheduler.py` (~line 593) spawns scheduled chain steps unscoped.

`_spawn_task` ALREADY supports `scope=` and fails closed when given an unknown
scope name. The plumbing exists. Callers just don't use it.

## The fix — exactly four changes

### Change 1 — `services/subagents.py`: add the scope

Add a `workflow-step` entry to `BUILTIN_SCOPES`, modeled on the existing
`research` scope's structure:

- `max_ring = 2`
- `denied_tools` must include: `spawn_task`, `install_package`, plus every
  ring-3 computer-control tool already denied by the `research` scope.
- Must NOT deny: `read_file`, `write_file`, `run_command`, `search_web`,
  `browse_web`.

### Change 2 — `services/subagents.py`: fail closed

In `scope_check()`, the unknown-task-id branch currently returns an allow
verdict. Change it to return a deny verdict with reason
`"unknown task id: fail closed"`. Do not change the known-task-id paths.

### Change 3 — `services/agent.py`: scope the three dispatch sites

In `run_workflow_chain`, `_retry_chain_step`, and `_advance_task_chain`,
add `scope="workflow-step"` to the `_spawn_task(...)` call. No other edits.

### Change 4 — `services/scheduler.py`: scope the scheduler dispatch

Same one-keyword addition at the chain-step spawn (~line 593).

## What must not change

- Direct user-initiated `spawn_task` calls (agent chat path) stay unscoped —
  the user's own authority is not the target.
- The five existing scopes and their behavior.
- Any public function signature.

## Verification (run in this order)

```
venv\Scripts\python.exe -m pytest tests/unit/test_workflow_chain_hardening.py -q
venv\Scripts\python.exe -m pytest tests/unit tests/api -q
venv\Scripts\python.exe scripts/check_imports.py
ruff check --select E9,F63,F7,F82 .
venv\Scripts\python.exe scripts/check_gated_prompt_callers.py
venv\Scripts\python.exe scripts/check_settings_readers.py
venv\Scripts\python.exe scripts/check_stale_model_names.py
```

All green = done. Write a short plain-language summary of what changed to
`docs/rsi/phase0-verdict.md` (repo-relative paths only) with verdict
`shipped`, `no-op`, or `failed`.
