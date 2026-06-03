# Skills System

Agent Friday's skill system enables self-improvement through learnable workflows, versioned optimization, and automatic quality recovery.

---

## Overview

The skill system has three layers:

1. **Learnable Skills** — YAML files defining reusable workflows
2. **SkillOpt Engine** — Versioned skill optimization with training epochs and regression gates
3. **Auto-Research Loop** — Automatic quality recovery when skill performance drops

---

## Learnable Skills

Skills are YAML files stored in `~/.friday/skills/`. Each skill defines a reusable workflow that Friday can invoke when it recognizes a trigger pattern.

### Skill File Structure

```yaml
name: meeting-prep
description: Prepare a briefing for an upcoming meeting
trigger_patterns:
  - "prepare for my meeting with"
  - "meeting prep"
  - "brief me on"
tool_chain:
  - search_wiki
  - query_trust_graph
  - search_web
  - browse_web
prompt_template: |
  Research {person} and prepare a meeting briefing:
  1. What we know from the wiki and trust graph
  2. Recent news and activity
  3. Suggested talking points
  4. Potential areas of collaboration
success_criteria:
  - Includes background from local sources
  - Includes recent external context
  - Provides actionable talking points
```

### Managing Skills

Skills are managed through the `learn_skill` tool, available in chat:

| Action | Description |
|--------|-------------|
| `create` | Create a new skill YAML file |
| `modify` | Update an existing skill |
| `delete` | Remove a skill |
| `list` | List all registered skills |
| `read` | Read a skill's content |

Skills can also be managed via the filesystem at `~/.friday/skills/`.

---

## SkillOpt Engine

The SkillOpt engine (`skillopt_engine.py`) tracks skill performance over time and evolves skills through an optimization loop inspired by Microsoft's SkillOpt research.

### Architecture

```
SkillOptEngine
├── SkillVersion        Versioned snapshot with metrics
├── TrainingEpoch       Batch evaluation + improvement cycle
├── ValidationGate      Regression prevention (within 5% of best)
└── AutoResearchLoop    Proposes edits when scores drop
```

### Composite Scoring

Every skill execution is scored on a weighted composite of five dimensions:

| Dimension | Default Weight | Description |
|-----------|---------------|-------------|
| `accuracy` | 0.40 | Correctness of the output |
| `user_satisfaction` | 0.25 | User feedback / acceptance |
| `latency` | 0.15 | Response time (normalized: <= target = 1.0, exponential decay beyond) |
| `cost` | 0.10 | Token cost (normalized against target) |
| `completeness` | 0.10 | Coverage of the requested task |

Weights are configurable per skill via `~/.friday/skillopt/<skill>/config.json`.

### Default Targets

| Parameter | Default | Description |
|-----------|---------|-------------|
| `latency_target_ms` | 5000 | Latency at or below this scores 1.0 |
| `cost_target_usd` | 0.05 | Cost at or below this scores 1.0 |

### Version Lifecycle

```
register_skill() → record_execution() → maybe_train() → promote_best()
```

1. **Register** — A skill is registered with initial content and scoring weights
2. **Execute** — Every execution is logged with metrics to `metrics.jsonl`
3. **Score** — Composite score is computed and version summary is updated
4. **Research** — If the rolling mean drops, auto-research proposes improvements
5. **Train** — A candidate version is evaluated against the current champion
6. **Validate** — The validation gate checks for regressions
7. **Promote** — If the candidate passes, it becomes the new champion

---

## Validation Gate

The validation gate prevents regressions. A candidate version must satisfy two conditions to be promoted:

1. **Within tolerance** — Score >= 95% of the all-time best score
2. **Beats baseline** — Score > current champion's score

If improvement is < 0.5%, the candidate is accepted as a marginal pass (within noise).

---

## Auto-Research Loop

Inspired by Andrej Karpathy's work on self-improving AI systems.

### Trigger Conditions

The auto-research loop fires when the 10-execution rolling mean drops more than 10% below the all-time best score.

### Research Process

1. **Detect** — Rolling mean vs best score comparison
2. **Analyze** — Examine recent executions for patterns:
   - Error rate spikes
   - Latency exceeding 2x target
   - Quality drift with no obvious cause
3. **Hypothesize** — Generate explanations for the drop
4. **Propose** — Create specific edit proposals:
   - `replace` — Replace the entire skill content
   - `patch` — Find-and-replace within the content
   - `append` — Add new sections
5. **Test** — Hand candidates to a training epoch
6. **Validate** — The validation gate decides

### LLM-Backed Research

When an LLM researcher callable is wired up, the loop performs deep analysis using the model. Without it, a heuristic fallback analyzes error and latency patterns.

---

## Storage Layout

```
~/.friday/skillopt/
└── <skill_name>/
    ├── versions/
    │   ├── v001.md           # First version (auto-promoted as baseline)
    │   ├── v001.json         # Version metadata sidecar
    │   ├── v002.md           # Candidate version
    │   └── v002.json
    ├── metrics.jsonl          # Append-only execution log
    ├── best_skill.md          # Current champion artifact
    ├── config.json            # Weights, thresholds, targets
    └── research_log.jsonl     # Auto-research findings
```

### Execution Record Fields

Each entry in `metrics.jsonl`:

| Field | Type | Description |
|-------|------|-------------|
| `skill_name` | string | Skill identifier |
| `version_id` | string | Version that was executed (e.g., `v003`) |
| `execution_id` | string | Unique execution ID |
| `timestamp` | string | ISO 8601 timestamp |
| `inputs` | object | Input parameters |
| `outputs` | object | Output data |
| `metrics` | object | Raw metric values |
| `composite_score` | float | Weighted composite (0.0 - 1.0) |
| `duration_ms` | float | Execution time in milliseconds |
| `cost_usd` | float | Estimated cost |
| `user_feedback` | object | Optional user feedback |
| `error` | string | Error message if execution failed |

---

## CLI

The SkillOpt engine includes a CLI for inspection:

```bash
# Fleet status
python skillopt_engine.py status

# Show a specific skill
python skillopt_engine.py show meeting-prep

# List versions
python skillopt_engine.py versions meeting-prep

# Export full fleet state as JSON
python skillopt_engine.py export

# Register a skill from a file
python skillopt_engine.py register meeting-prep skills/meeting-prep.md
```

---

## API Integration

The SkillOpt engine exposes public helpers used by `server.py`:

| Function | Description |
|----------|-------------|
| `get_engine()` | Lazy singleton — returns the shared SkillOptEngine |
| `export_fleet_state()` | JSON snapshot for the Observatory UI |
| `record_skill_run(...)` | Convenience hook for recording executions |
| `maybe_autoresearch(skill_name)` | Trigger auto-research check (call periodically) |
