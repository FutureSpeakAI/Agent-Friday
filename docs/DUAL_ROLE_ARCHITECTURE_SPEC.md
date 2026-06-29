# Dual-Role Orchestration Engine — Architecture Spec
**Agent Friday v5 · FutureSpeak.AI**

## Overview

Friday operates as both **employer** (orchestrating local sub-agents) and **employee**
(accepting federated compute work from peers). This document specifies the architecture
for that dual-role system.

---

## Phase 1 — Local Sub-Agent Orchestration (Friday as Employer)

### WorkerTask
```
task_id         UUID
task_type       CODE | RESEARCH | CREATIVE | ANALYSIS | BROWSER | CUSTOM
prompt          str
context         dict  {user_intent, workspace_goal, project_objective, task_spec}
budget_mψ       int   max milliPositrons this worker may spend
budget_tokens   int   max tokens
deadline_seconds int
adapter_type    OLLAMA | CLAUDE_CODE | PYTHON_SCRIPT | BROWSER | HTTP_API | GEMINI | OPENROUTER
parent_task_id  optional UUID
priority        1–5 (5 = highest)
```

### WorkerResult
```
task_id         UUID
status          COMPLETED | FAILED | TIMEOUT | BUDGET_EXCEEDED | CANCELLED
output          str or dict
artifacts       list[Path]  files produced
tokens_used     int
cost_mψ         int
duration_seconds float
quality_score   float 0–1  (QA evaluation)
error           optional str
```

### Orchestrator
High-level API: `delegate(prompt, task_type, budget, context)` → WorkerResult

### Worker Adapters
- **OllamaAdapter** — local Ollama inference
- **PythonScriptAdapter** — subprocess, captures stdout + files
- **HttpApiAdapter** — POST to any HTTP endpoint

---

## Phase 2 — Federated Computation (Friday as Employee)

### CapabilityCard (advertised to network)
```
agent_pubkey        Ed25519 public key
capabilities        list[{type, description, price_mψ, avg_duration_seconds}]
compute_specs       {cpu_cores, ram_gb, gpu_model, gpu_vram_gb}
availability        {online: bool, busy_until: datetime|null}
min_trust_score     float  (won't accept work below this)
```

### ComputeProvider
- `advertise_capabilities()` → CapabilityCard
- `accept_job(job_request)` → bool  (cLaws gate + capacity check)
- `execute_job(job)` → JobResult
- `reject_job(job_request, reason)` → rejection

### ComputeClient
- `find_providers(capability_type)` → list[CapabilityCard]
- `request_job(provider_pubkey, task_spec, offered_price_mψ)` → JobRequest
- `await_result(job_id)` → JobResult
- `rate_provider(job_id, quality_score)` → trust update

### Federation Routes (added to /api/federation/)
```
GET  /api/federation/capabilities              — this Friday's CapabilityCard
POST /api/federation/compute/request           — receive a job request
POST /api/federation/compute/result            — deliver a job result
GET  /api/federation/compute/status/:job_id    — check job status
```

---

## Phase 3 — Work Log & Audit Trail

SQLite `work_log.db` — every orchestrated action:
```
work_id, task_id, worker_type, prompt_hash, started_at, completed_at,
tokens_in, tokens_out, cost_mψ, quality_score, status, error,
artifacts_produced, goal_ancestry_json
```

---

## Phase 4 — UI Surfaces

1. **Orchestrator panel** (Settings) — active workers, budget dashboard, kill button
2. **Capability card** (Federation settings) — advertise/toggle capabilities, pricing, incoming jobs
3. **Work log** (dock) — scrollable timeline, expandable entries, filters

---

## Budget Enforcement

- Per-workspace monthly caps + per-task caps stored in `budgets.db`
- `reserve_budget(workspace, amount)` atomic; returns False if cap exceeded
- `enforce_hard_stop(worker_id)` kills overbudget workers
- Warning notifications at 80% monthly spend

---

## Goal Ancestry

Every workspace carries `{mission, goal, objective, current_task}`.
When Friday delegates, the ancestry propagates to the worker's context.
Workers receive only the context relevant to their trust tier (egress gate applies).
