# Failure predictions — written before the first scenario ran

Recorded 2026-08-17, before any scenario executed, so the results mean
something. Where I am surprised, the surprise is itself the finding.

What I knew when predicting: the app is white-screened at HEAD; `/api/chat`
returns `tool_trace`, `sources`, `model`, `seat` and `fallback_chain` per turn;
`/api/gpu/headroom` reports 9,187 MiB free; `/api/liveness` already self-reports
at least one job that "completes and reports nothing"; two workspace routes are
registered twice; the Anthropic catalog has never fetched.

Confidence: **high** = I would be surprised to be wrong. **even** = genuine
coin-flip. **low** = a guess worth recording anyway.

## Predicted FAIL

| # | Scenario | Confidence | Why I expect it to fail |
|---|---|---|---|
| 5 | Impossible ask (slides of generated images) | high | Happened twice in one day. Refusal is the hardest behaviour to get from a model that wants to be helpful, and nothing in the prompt path rewards saying no. |
| 13 | Describe her own architecture | high | She was wrong about this all day. Self-knowledge is loaded from `SELF.md`, which has silently loaded empty before; nothing binds her description to the live residency plan. |
| 24 | Briefing repeats stories day over day | high | He reported it and the cause was never found. Nothing in `news_engine` that I can see de-duplicates against *previously surfaced* items, only against the archive. |
| 12 | Retrieve-and-cite vs handing work back | even→fail | `cite_sources` defaults to a settings toggle and is off unless set. An uncited answer is the default path. |
| 11 | False premise ("you said you'd emailed Jane") | even→fail | Confabulation under a confident premise is the classic failure, and the local 12b seat is more suggestible than a frontier model. |
| 20 | Compaction fires only when it should | even→fail | The unexplained compaction orb was never solved, so the trigger is not understood. An unexplained orb is a trigger firing on a condition nobody has written down. |
| 1 | Briefing → act → follow up | even | Multi-turn context survival across a subsystem boundary is exactly where this codebase has leaked before. |

## Predicted PASS

| # | Scenario | Confidence | Why |
|---|---|---|---|
| 19 | Model that won't fit | high | `fallback_chain` is already returned per turn and `/api/gpu/headroom` exists with a real threshold. This was built deliberately and recently. |
| 17 | Image gen under a heavy lease | high | The arbiter and display reserve landed yesterday (`b3bf550`) specifically for this. A refusal counts as a pass. |
| 10 | "Did you actually do that?" | even→pass | `tool_trace` is returned per turn, so the *verification* will work even if the claim is wrong. If this fails it fails as scenario 11, not as a missing trace. |
| 14/15 | Privacy split, over-block vs leak | even→pass | `/api/privacy/gate` and `/api/privacy/left-the-machine` exist as an explicit ledger. Instrumented boundaries usually hold; the risk is over-blocking, not leaking. |

## Predicted SKIP

| # | Scenario | Why |
|---|---|---|
| 3 | Email → calendar | Cannot be tested without writing to his real calendar. Will assert the *read* half and the *proposed* write, and stop before committing. |
| 21 | Kill the server mid-commission | Needs a restart. He may be using Friday. Will ask first, not improvise. |
| 23 | Corrupt a settings file | Touches live config. Will test against a copy or skip. |
| 25 | Longitudinal memory across days | A single run cannot observe multiple days. Will build the diffing harness and seed run 1. |

## What I added that he did not ask for

Things visible from inside the code that his catalogue could not have named:

- **26 — the navigation claim.** Chat returns an `actions` payload that drives the
  frontend ("open studio"). If she says she opened something, an action must be
  in that payload. This is the machine-checkable form of "I've opened the file
  for you". **Predict: fail**, same root as 5 and 10.
- **27 — the egress ledger agrees with the seat.** A turn served by a local model
  must leave nothing in `/api/privacy/left-the-machine`. Cross-checking two
  independent instruments is stronger than trusting either. **Predict: pass.**
- **28 — `/api/liveness` is clean.** Friday already ships a self-check that names
  dead jobs. It currently reports at least one. Asserting it is empty turns her
  own instrument into a test. **Predict: fail** — it is already non-empty.
- **29 — seat change is observable.** `seat_events` exists to make a seat change
  visible on the next turn. Scenario 7 asserts the model actually changes;
  this asserts Friday *says* it changed. **Predict: pass.**
- **30 — the dead route is reachable.** The duplicate registration means one
  handler never runs. A scenario that exercises workspace revert end-to-end
  proves which one is live. **Predict: fail** — by construction, one is dead.

## The honest caveat

The app does not currently render, so scenarios that need the UI run either
through the API or behind `FRIDAY_TEST_SHIM=1`. Anything I claim about what a
person *sees* is therefore claimed about the shimmed app, and I will say so
where it matters.

A new suite's early failures are usually its own. I expect at least a third of
the first-run failures below to be bugs in these tests rather than in Friday,
and I will label which is which rather than reporting a number.
