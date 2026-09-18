# Decisions

Durable architectural and behavioural decisions. Downstream work inherits from
these files rather than from chat history.

Records live in [`docs/decisions/`](decisions/); this file is the index.

| Date | Decision | Status |
|---|---|---|
| 2026-09-09 | [Recall can be poisoned by a broken display](decisions/2026-09-09-recall-poisoned-by-broken-display.md) — live state is never answerable from memory | implemented |
| 2026-09-04 | [Five dead settings](decisions/2026-09-04-five-dead-settings.md) — a control ships only with its enforcement and a test that fails if the enforcement is removed | implemented |
| 2026-08-24 | [Pending classifier calls](decisions/2026-08-24-pending-classifier-calls.md) | implemented |
| 2026-08-13 | [Architecture decisions — August 2026](decisions/2026-08-architecture-decisions.md) — truth-flow, routing, residency | implemented |

## The through-line

Two rules have earned their place across several of these:

**Presence is not function.** A check that asks whether a record exists and
reports that as whether the thing works will read as correct for as long as the
record survives the thing. It has now produced a calendar reporting `connected`
with zero usable accounts, a readiness probe calling a broken import ready
because the package *name* resolved, and a settings page rendering an account
record as a working connection.

**Live state is never answerable from memory.** Recall reports what was true when
it was written. Whether something works *right now* is a different question
wearing the same words.
