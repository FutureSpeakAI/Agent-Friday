# Skill: job_scanner

## Identity

Autonomous job discovery skill for Agent Friday. Scans LinkedIn (and other
configured sources) on a recurring schedule, scores every posting against
Stephen's career profile, deduplicates against the tracker, and pushes
priority notifications when high-match opportunities surface.

## When this skill fires

- Scheduled every **4 hours during active hours** (07:00–22:00 local).
- On demand, when Stephen asks "scan jobs", "what's new on LinkedIn",
  "any priority jobs today", or similar.
- After a profile / preferences update — re-rescore the existing tracker
  with the new weights.

## What this skill does

1. **Keyword rotation.** Pull the active query set from `config.yaml`
   and rotate through subsets so we don't hammer the same searches every
   run.
2. **Search LinkedIn.** Construct LinkedIn search URLs for each keyword
   set, parameterized by recency, location, remote flag, and seniority.
3. **Score each posting.**
   - `title_match` (weight × 3)
   - `salary_match` (× 2)
   - `remote_match` (× 2)
   - `skills_overlap` (× 2)
   - `seniority_match` (× 1.5)
   - `company_signal` (× 1)
4. **Deduplicate.** Hash `(source, external_id)` or
   `(company, title, location)` as a fallback. Skip if seen.
5. **Persist.** Insert new listings into the `JobTracker`.
6. **Notify.** Priority jobs (score ≥ 0.80) fire a `priority_job_alert`
   immediately. Everything else rolls into the next `daily_digest`.

## Inputs

- `keyword_set` (optional, default = `auto-rotate`)
- `since` (optional, default = last run timestamp)
- `min_score_to_notify` (optional, default = 0.80)

## Outputs

- Returns a `ScanResult` dict:
  ```json
  {
    "scanned": 47,
    "deduped": 22,
    "new_listings": 25,
    "priority": 4,
    "notifications_sent": 4,
    "duration_ms": 8421
  }
  ```
- Persists to: `~/.friday/job_tracker.json`

## Quality bar

- No duplicates ever surface to Stephen.
- Priority filter catches at most ~5 jobs per day on average — if it's
  firing more, recalibrate weights.
- Scanner survives transient LinkedIn errors (timeout, 429) by backing
  off exponentially and writing partial results.

## Failure modes to guard against

- LinkedIn DOM changes — extractor falls back to URL-only stub.
- Bad salary parsing — leave fields null, never fabricate.
- Score weight misconfig — clamp to [0, 1.0] and log warning.

## How this skill improves itself

Every execution is logged to SkillOpt as:

- `accuracy` — fraction of priority alerts Stephen accepted (didn't snooze).
- `user_satisfaction` — explicit feedback from the chat ("good find",
  "irrelevant", etc.).
- `completeness` — did we capture salary, location, and skills, or are
  there nulls?
- `latency_ms` — total scan time, including network.

When the 10-run rolling mean drops 10% below the all-time best, the
autoresearch loop proposes weight tweaks or new keyword sets.

## Success criteria

A run is successful if it:

1. Returns within 30 seconds per keyword set,
2. Inserts at least one new listing on a typical day,
3. Maintains the **priority precision ≥ 75%** quality bar (3 out of 4
   priority alerts should be ones Stephen would actually apply to).
