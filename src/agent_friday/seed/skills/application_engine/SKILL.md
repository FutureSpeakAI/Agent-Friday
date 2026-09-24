# Skill: application_engine

## Identity

Job application preparation for Agent Friday. Given a `JobListing` (and
optional intel pack), the engine prepares everything the owner needs to
apply:

```
  intel ──→ resume tailor ──→ cover letter ──→ field plan ──→ tracker log
```

It never submits. There is no submitter in the engine and no result status
that claims a submission. Submitting is irreversible and public-facing, so the
owner submits on the employer's site.

Lives at `agent_friday/seed/skills/application_engine/engine.py`. Invoked by
the chat ("prepare an application for this") or by the Career workspace.

## When this skill fires

- The user says "prepare an application for that one" / "tailor for X".

## What this skill does

1. **Intel pass.** Pull company brief from cache or generate one. Stash
   3 distinctive talking points.
2. **Resume tailor.** Pick a resume variant via A/B logic, swap in
   role-specific bullets, ensure must-include items are present.
3. **Cover letter.** Draft against the listing's stated priorities, cap
   at `cover_letter_max_words`. Run brand-voice check before saving.
4. **Field plan.** Detect ATS platform (Greenhouse, Lever, Workable,
   SmartRecruiters, other) and emit a field-by-field plan. Answers to legal
   or demographic questions are left blank for the owner.
5. **Outcome.**
   - Below salary floor → `blocked`, and the user is notified.
   - Otherwise → `prepared`; `review_suggested` is true above the
     confirmation threshold.
6. **Tracker log.** Persist an `ApplicationRecord` (stage `triaged`, never
   `applied`) with the resume + cover letter variants used and which quality
   gates passed.

## Inputs

- `job_id` — the `JobListing` to prepare for
- `force_confirm` (optional) — mark the result `review_suggested`
- `resume_variant` (optional) — override A/B selection

## Outputs

```json
{
  "application_id": "app_...",
  "job_id": "job_...",
  "status": "prepared" | "blocked",
  "submitted": false,
  "next_step": "Friday did not submit this application ...",
  "ats": "greenhouse" | "lever" | ...,
  "resume_variant": "AI_VP_v3",
  "cover_letter": "...",
  "cover_letter_word_count": 412,
  "quality_gates_passed": ["salary_floor", "must_include", "brand_voice"],
  "quality_gates_failed": [],
  "duration_ms": 11234
}
```

## Quality gates

| Gate              | Pass condition                                    |
|-------------------|---------------------------------------------------|
| `salary_floor`    | `salary_max >= 150000` (configurable)             |
| `salary_ceiling`  | If `salary_max >= 300000`, suggest a review      |
| `must_include`    | Every must-include item appears in resume         |
| `cover_voice`     | Cover letter passes brand-voice check             |
| `cover_length`    | Word count ≤ `cover_letter_max_words` (450 default)|
| `dedup_apply`     | No prior application for the same `job_id`         |
| `ats_supported`   | Detected ATS is in the supported list             |

A failed `salary_floor` is a hard block; everything else surfaces a
warning on the prepared result.

## A/B testing

The engine maintains a small bandit over resume variants. On every
application, it picks a variant with epsilon-greedy (default ε = 0.10)
and records the variant in the `ApplicationRecord`. When responses come
back (positive or negative), `record_response()` updates the variant's
score so future picks improve.

## ATS handling

We support the common four major ATSes with platform-specific field maps:

| Platform        | Detection signal                       | Notes                              |
|-----------------|----------------------------------------|------------------------------------|
| Greenhouse      | `boards.greenhouse.io` in URL          | Mostly text fields + dropdowns     |
| Lever           | `jobs.lever.co` in URL                 | Has its own EEO block              |
| Workable        | `apply.workable.com` in URL            | Often gates by domain whitelist    |
| SmartRecruiters | `jobs.smartrecruiters.com` in URL      | Heavy on cover letter parsing      |

Anything else is `unknown` — we emit the field plan and ask the user to
finish manually.

## Quality bar

- **Zero misfires** on the salary floor gate.
- **No application prepared twice** for the same job_id.
- Cover letter brand-voice score ≥ 0.75.

## How this skill improves itself

Each application is recorded to SkillOpt. When response data lands
(interview, rejection, ghost) the engine attributes outcomes back to
the application and folds them into:

- `accuracy` — was the application a real fit? (response_kind score)
- `user_satisfaction` — explicit user feedback
- `completeness` — did we fill every required field?
- `cost_usd` — token spend on tailoring

If the rolling outcome score drops, autoresearch proposes:
- Adjusting must-include lists,
- Trying a new resume variant,
- Cover letter style shifts (terser / more storytelling),
- A different brand-voice profile per role family.

## Success criteria

A run is successful if it:

1. Surfaces a complete artifact bundle (resume + cover + field plan),
2. Passes all configured quality gates,
3. Hands off with all information ready for the user to submit.
