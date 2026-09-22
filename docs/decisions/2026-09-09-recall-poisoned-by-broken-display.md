# Recall can be poisoned by a broken display

> **Status:** implemented
> **Written:** 2026-09-09
> **Implementation:** `services/live_state.py` (the rule and the probe registry); `services/model_router.py` `_build_memory_context_block` (the hook); `conversation_memory.py` `supersede()` / `unsupersede()` / `find_ids()` (the remedy)
> **Tests:** `tests/api/test_live_state_not_from_memory.py`, `tests/api/test_memory_supersede.py`
> **Related:** `docs/decisions/2026-09-04-five-dead-settings.md`; `docs/history/2026-09-09-superseded-google-connectivity.json` (what was superseded, and how to reverse it)

## The mechanism

This entry is about a failure mode, not about the Google connector. The connector
was the vehicle. The mechanism will recur anywhere a system stores what a user
says as durable truth.

It ran in five steps:

1. **A display was wrong.** The connectors page read `status` from the Google
   account store and rendered nothing — it showed the *presence* of an account
   record as a working connection. Both accounts had been `needs_reauth` since
   2026-09-01. The page said connected.

2. **The user read the display and believed it.** Reasonably. It is the one
   place in the product whose entire job is to report whether accounts work.

3. **The user told the assistant what he saw.** On 2026-09-09 Stephen said:
   *"I went to the settings workspace and I checked there and it says that my
   Google accounts are connected."* In good faith, reporting an observation.

4. **The assistant stored it as a user-authored fact.** This is the highest-trust
   class of input the system has. Nothing marked it as derived from a display,
   because from the memory layer's point of view it was simply something the
   user said.

5. **The assistant retrieved it and answered with it, citing the user.** Asked
   "are my Google accounts connected?", it returned *"Yeah, they are connected
   now. I checked the settings workspace and it confirms..."* with a
   `[conversation:2026-09-09]` citation. No tool was called.

**The system laundered its own error through the person it was lying to, and
returned it citing him as the authority.** The false claim entered as a display
bug and came back wearing user provenance, which is the strongest credential the
architecture recognises.

## Why nothing else caught it

Each defence we have is aimed at a different failure, and this one slips past
all of them by construction:

| Defence | Why it does not fire |
|---|---|
| Claim verification | Nothing was fabricated. The model accurately reported a real memory of a real thing the user really said. |
| Tool receipts / execution proof | No tool was called, so there is no missing receipt to notice. |
| Fixing the display | The memory was written before the fix and outlives it. Correcting a UI does not retract what it caused someone to say. |
| Provenance tracking | Provenance was intact and *correct*. The sentence really did come from the user. Provenance records who said it, not whether they were repeating something we told them. |

The detection that actually worked was noticing a **citation marker on an answer
that should have required a tool call**. That is the signal worth institutionalising:
when a question is about live state and the answer carries a `[conversation:...]`
or `[memory:...]` citation, the answer is wrong by construction regardless of
how plausible it reads.

## The rule adopted

> **Live state is never answerable from memory.**

Two question classes, needing different machinery:

- **Recalled-fact questions** — what was decided, said, preferred, named. Memory
  is the right and only source. These do not decay on their own.
- **Live-state questions** — what is true of the world at this instant: whether
  an account is connected, a service reachable, a model loaded, how much disk is
  free. Recall is *structurally incapable* of answering these. Not unreliable —
  incapable. A memory reports what was true when it was written; the entire
  content of the question is what is true now. A recalled answer here is a
  category error phrased as a fact, delivered with a real memory's confidence
  and a real citation.

`services/live_state.py` holds the rule, the probe registry, and the two-part
prompt block. Both parts matter. Supplying the live answer is insufficient on
its own: the model then holds a live reading *and* a remembered one, and the
remembered one carries the user's voice. The block therefore also withdraws
recall's licence to answer, naming the specific trap — *"including one where the
user themselves said it was working"*.

Adding a new status readout means appending a `Probe`. Classification, the
caveat, and the authoritative answer all follow from registering it, so the
default path for the next status question is the correct one. A probe whose
`answer` reads a cached summary written by an earlier turn has reintroduced this
bug.

## Remediation: supersede, never delete

Poisoned entries are **superseded**, not deleted. A stored turn records
something a person actually said; erasing it destroys history and conceals that
a correction happened. Superseding keeps the row byte-for-byte and removes only
its authority as evidence:

- `search()` — the retrieval path that feeds model context — excludes it.
- `get_session()` / `recent()` — the history paths — still return it, flagged
  with a reason.
- `unsupersede()` restores it. The operation is reversible.

The true history here is worth preserving precisely because it is the evidence
for this entry: a page told Stephen something false on 2026-09-09, and he
repeated it in good faith. Deleting that sentence would erase the only trace of
how the error travelled.

Supersession filtering happens in Python, not in a Chroma `where` clause:
entries written before the flag existed carry no such key, and metadata
predicates over missing keys are not portable across versions. A filter that
silently dropped every pre-existing memory would be far worse than the bug it
was added to fix.

## For the next maintainer

Before adding any status readout, ask which question it answers — *does a record
exist*, or *does the thing work*. That distinction is the single pattern behind
every failure found on 2026-09-09: `has_accounts()` reporting a non-empty index
as a working calendar; a readiness check using `find_spec` to prove a package
name existed and calling a broken import ready; the connectors page rendering a
record as a connection.

And assume that anything a display asserts can end up in memory as a user's own
words. A wrong readout is not contained by the screen it appears on.
