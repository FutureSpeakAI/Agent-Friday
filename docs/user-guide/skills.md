# Skills

A skill is a reusable procedure that Agent Friday™ can follow when a request
matches it. Skills can be created by Friday, saved from a successful
[workflow](workflows.md), or imported. A skill never grants new permissions: it
describes how to do something, and every tool it uses keeps its normal checks
and approvals.

The skill system has three parts:

1. **Skills**, portable `SKILL.md` folders (or legacy YAML files) that define
   reusable procedures.
2. **The SkillOpt engine**, which versions skills, scores their runs and
   promotes a better version only if it passes a regression gate.
3. **The auto-research loop**, which proposes edits when a skill's results
   drop.

## Where skills come from

- **Saved from a workflow.** After a successful workflow run whose deliverable
  was verified, **Keep as reusable procedure** saves the workflow's steps as a
  skill named `workflow-<workflow name>`. It is refused if the run was not
  verified, or if the workflow changed after that run. Saving it again for the
  same revision changes nothing.
- **Created in chat.** Ask Friday to remember a procedure. Friday uses the
  `learn_skill` tool, which writes a YAML skill file.
- **Imported.** From a folder, a `.zip`, a legacy `.yaml` file or an
  OpenClaw-style package.
- **Bundled.** A few skills ship with Friday.

Your skills live in `%USERPROFILE%\.friday\skills\`. A skill is active as soon
as it is saved, without a restart.

## The SKILL.md format

A skill is a folder containing a `SKILL.md` file: YAML frontmatter, then a
Markdown body that is the procedure.

```markdown
---
name: meeting-prep
description: Prepare a briefing for an upcoming meeting
version: 1
triggers:
  - "prepare for my meeting with"
  - "meeting prep"
  - "brief me on"
tool_chain:
  - search_wiki
  - query_trust_graph
  - search_web
success_criteria:
  - Includes background from local sources
  - Includes recent external context
  - Provides actionable talking points
license: MIT
source: friday
---

# Meeting Prep

Research the person and prepare a meeting briefing:
1. What we know from the wiki and trust graph
2. Recent news and activity
3. Suggested talking points
4. Potential areas of collaboration
```

| Frontmatter field | Type | Description |
|---|---|---|
| `name` | string | Skill identifier. Defaults to the folder name. |
| `description` | string | One-line summary. |
| `version` | integer | Skill version. |
| `triggers` | string list | Phrases that activate the skill. `trigger_patterns` is accepted as an alias. |
| `tool_chain` | string list | Tools the procedure relies on. |
| `success_criteria` | string list | Conditions for a successful run. |
| `license` | string | License of the skill. Defaults to MIT. |
| `source` | string | Where it came from: `friday`, `imported`, `openclaw`, `bundled` or `workflow`. |

### How skills are used

On each turn, Friday checks your message against every skill's triggers (a
case-insensitive match on the phrase). For up to three matching skills, Friday
sees the name, description, suggested tools and success criteria, and is told to
load the complete procedure with the `read_skill` tool before using it. The
full procedure is never cut short, because a partial procedure could drop its
checks or limits.

When a skill is saved over, the previous procedure is kept in a `_history`
folder inside the skill's folder.

### Legacy YAML skills

Single-file skills in `%USERPROFILE%\.friday\skills\*.yaml` still work and are
found alongside the folder format. The `learn_skill` tool takes these actions:

| Action | Description |
|---|---|
| `create` | Create a skill YAML file. |
| `modify` | Replace an existing skill's content. |
| `delete` | Remove a skill. |
| `list` | List the YAML skills. |
| `read` | Read a skill's content. |

### Importing and exporting

Skills are portable. These routes require you to be signed in:

- `GET /api/skills` lists all skills (learned, imported, bundled).
- `POST /api/skills/import` imports a skill from a `.zip` upload, or from JSON
  that points at a folder, a zip or a legacy `.yaml` file.
- `GET /api/skills/<name>/export` downloads a skill as a `.zip`.
- `POST /api/skills/reload` rescans the skills folders without a restart.

### Closed-loop learning

Friday records the trajectory of skill runs and feeds the results to the
SkillOpt engine. The nightly auto-research job is disabled in this release. The
check can still be run on demand with `maybe_autoresearch()`.

## The SkillOpt engine

The SkillOpt engine (`skillopt_engine.py`) tracks each skill's performance
over time and evolves skills through an optimization loop inspired by
Microsoft's SkillOpt (github.com/microsoft/SkillOpt). Everything it stores is
on your PC.

```
SkillOptEngine
├── SkillVersion        Versioned snapshot with metrics
├── TrainingEpoch       Batch evaluation and improvement cycle
├── ValidationGate      Regression prevention (within 5% of best)
└── AutoResearchLoop    Proposes edits when scores drop
```

### Composite scoring

Every skill run is scored on a weighted composite of five dimensions:

| Dimension | Default weight | Description |
|---|---|---|
| `accuracy` | 0.40 | Correctness of the output |
| `user_satisfaction` | 0.25 | Your feedback and acceptance |
| `latency` | 0.15 | Response time (at or below the target scores 1.0, with exponential decay beyond) |
| `cost` | 0.10 | Token cost against the target |
| `completeness` | 0.10 | Coverage of the requested task |

Weights and targets are configurable per skill in
`%USERPROFILE%\.friday\skillopt\<skill>\config.json`. The defaults are a
latency target of 5000 ms and a cost target of $0.05.

### Version lifecycle

```
register_skill() -> record_execution() -> maybe_train() -> promote_best()
```

1. **Register.** A skill is registered with its content and scoring weights.
2. **Execute.** Every run is logged with metrics to `metrics.jsonl`.
3. **Score.** The composite score is computed and the version summary updated.
4. **Research.** If the rolling mean drops, auto-research proposes improvements.
5. **Train.** A candidate version is evaluated against the current champion.
6. **Validate.** The validation gate checks for regressions.
7. **Promote.** If the candidate passes, it becomes the new champion.

### Validation gate

A candidate must satisfy two conditions to be promoted:

1. **Within tolerance.** Its score is at least 95% of the all-time best score.
2. **Beats the baseline.** Its score is above the current champion's score.

If the improvement is under 0.5%, the candidate is accepted as a marginal pass
(within noise).

## The auto-research loop

The loop fires when the 10-run rolling mean drops more than 10% below the
all-time best score.

1. **Detect.** Compare the rolling mean with the best score.
2. **Analyze.** Look for error-rate spikes, latency above twice the target, and
   quality drift with no obvious cause.
3. **Hypothesize.** Generate explanations for the drop.
4. **Propose.** Create edit proposals: `replace` (the whole content), `patch`
   (find and replace) or `append` (new sections).
5. **Test.** Hand candidates to a training epoch.
6. **Validate.** The validation gate decides.

When a language model is wired in, the loop analyses with it. Without one, a
heuristic fallback analyses error and latency patterns.

## Storage layout

```
%USERPROFILE%\.friday\skillopt\
└── <skill_name>\
    ├── versions\
    │   ├── v001.md           First version (auto-promoted as baseline)
    │   ├── v001.json         Version metadata
    │   ├── v002.md           Candidate version
    │   └── v002.json
    ├── metrics.jsonl          Append-only run log
    ├── best_skill.md          Current champion
    ├── config.json            Weights, thresholds, targets
    └── research_log.jsonl     Auto-research findings
```

Each line of `metrics.jsonl` records the skill name, version, run id, time,
inputs, outputs, raw metrics, composite score (0.0 to 1.0), duration, estimated
cost, optional user feedback and any error.

## Command line

Run these as modules from Friday's Python environment:

```
python -m agent_friday.skillopt_engine status
python -m agent_friday.skillopt_engine show meeting-prep
python -m agent_friday.skillopt_engine versions meeting-prep
python -m agent_friday.skillopt_engine export
python -m agent_friday.skillopt_engine register meeting-prep skills/meeting-prep.md
```

`status` shows fleet status, `show` one skill, `versions` its versions, `export`
the full fleet state as JSON, and `register` adds a skill from a file.
