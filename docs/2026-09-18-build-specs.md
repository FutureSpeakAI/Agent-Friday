# Friday: three specs for review

2026-09-18. Written to be argued with, not executed. Every claim about the
current system was checked against the running code today.

---

# SPEC 1 — Multiple chat windows and an undockable chat panel

> "We need to support multiple chat windows open in the Friday desktop at the
> same time, not just a switcher. We should also give the chat panel an undock
> button to make it a free floating window... so I can juggle work between
> cloud and local models simultaneously."

## The good news first

This is mostly a frontend job. The backend already does the hard part, and I
checked rather than assumed:

- `services/conversations.py` gives every conversation its own directory, its
  own message store, its own cost totals, and **its own seat**:
  `create(title, seat=...)`, `patch(cid, seat=...)`. There are already 329
  conversation files on disk.
- `routes/chat.py` reads that binding per turn (`_conv_seat`) and the router's
  `_chosen_seat` honours it above the global default.
- Writes are atomic (`_atomic_write`), so two windows appending to two
  different conversations do not race.

So "two windows, two models" is already true at the data layer. What does not
exist is a UI that can show two at once.

## What to build

**1. Undock.** A button on the chat panel header opens
`window.open('/?conversation=<cid>&chrome=chat', ...)` — a real browser window
carrying one conversation, with the holographic shell and workspace rail
suppressed. Friday is served over HTTP into Chrome, so this needs no new
process model.

**2. New chat opens where you ask it to.** The existing "new chat" keeps its
behaviour in the main window. Add a modifier or a split button — *New chat in
a new window* — which creates the conversation with an explicit seat and
undocks it in one action. That is the gesture he actually described: start a
second model without ending the first.

**3. A seat picker per window, not per app.** The model selector in an
undocked window writes `conversations.patch(cid, seat=...)`, never the global
`capability_routing`. Today the model button changes the global seat, which is
why two windows would fight. This is the one backend-adjacent change and it is
small.

**4. Shared orbs, attributed.** The holographic process view stays in the main
window and shows orbs from every window, each labelled with its conversation.
One agent, one machine, one activity view. (The orb label now carries the full
model name, fixed today.)

## The constraint to state plainly in the UI

**Two cloud windows: fine. One cloud plus one local: fine. Two local models at
once: not on this hardware.** Bonsai at 64K holds about 11 of 12.3 GB. A
second local seat has nowhere to live, and the arbiter will start evicting.

So the seat picker in a second window should show local models as unavailable
*with the reason* when a local seat is already resident, rather than letting
him pick one and discovering the thrash. That is the same honesty rule applied
to a new surface.

## Acceptance

- Two windows, two different cloud models, both streaming, neither reply
  appearing in the wrong window.
- One window on Bonsai, one on Sonnet, simultaneously.
- Closing the undocked window does not end the conversation; reopening it from
  the switcher restores it.
- Picking a second local model is refused with a reason naming the resident
  seat.
- Orbs from both windows appear in the main view, correctly attributed.

## Estimate

Frontend: the window plumbing, the chrome-less mode, the per-window seat
picker, the orb attribution. Backend: one change, making the model button
write the conversation seat rather than the global one. This is days, not
weeks, and the risk is concentrated in the seat-picker change.

---

# SPEC 2 — What Friday should do out of the box

The five gaps from this morning's audit, now with designs.

## 2.1 Search that works without a key hunt

**Today:** `services/web_search.py` has Firecrawl, then Brave, then a
DuckDuckGo scrape. Neither of the first two has a key, so every search falls to
the scrape, which returns HTTP 202 anti-bot walls. Your interview prep failed
on this today.

**Build:** first-run detection. When a search is attempted and every keyed
provider is unconfigured, Friday says so *once*, names the variable
(`BRAVE_SEARCH_API_KEY`), links the signup, and offers to retry. It does not
silently degrade to a scraper and then report "no results", which is what it
does now and is indistinguishable from the internet being empty.

**Acceptance:** with no key, a search returns a refusal naming the missing
configuration. With a key, it returns results and the health model reports
which backend served.

## 2.2 Credentials that announce their own expiry

**Today:** both Google accounts have needed reauthorization since 2026-09-01.
Friday answered calendar questions for nine days in between. The live-state
fix from 09-09 stops it *lying* about this; nothing yet makes it *volunteer*.

**Build:** a credential health sweep on a schedule, and a rule that the first
turn touching an expired connector leads with the expiry rather than the
answer. Surfaced in the UI as a persistent badge, not a toast that scrolls
away.

**Acceptance:** expire a token in a fixture; the next calendar question opens
with the reauthorization prompt and does not answer from stale data.

## 2.3 The arbiter sees the whole card

**Today:** partially fixed. It now respects a declared engine per model. It
still cannot see seats it did not spawn — an idle Ollama runner cost Bonsai a
9× slowdown today and the arbiter was unaware.

**Build:** enumerate every process holding VRAM, not just `self.procs`. Adopt
or refuse to co-reside, and make eviction a decision rather than an accident.
This is also item 2 of Spec 3 and should be built once.

**Acceptance:** start a foreign llama-server; the arbiter reports it, accounts
for its VRAM, and does not double-book.

## 2.4 A latency budget, declared

**Today:** standing prompt overhead was ~39,000 tokens this morning and is
~24,000 now after tool subsetting and the context bound. Nothing asserts it
stays there.

**Build:** a budget in settings, measured per turn, surfaced when exceeded, and
asserted in CI. The number is a product decision; the enforcement is not.

**Acceptance:** a test fails when overhead regresses past the declared ceiling.

## 2.5 A structural line between "I did" and "I will"

**Today:** the guard is a regex over the reply text. I fixed two faults in it
today: it flagged "I'll" as a completion claim, and its verb stems could never
match irregular past tenses, so "I've sent the email" had never been caught.
Both were one-character-class problems in a pattern doing semantic work.

**Build:** move the check off the prose and onto the receipts. Every tool call
produces a receipt; a reply asserting an action is checked against receipts for
*that* action, not against a word list. The regex stays as a cheap
pre-filter, not as the authority.

**Acceptance:** a reply claiming a completed action with no matching receipt is
flagged regardless of phrasing; a reply stating intent is never flagged.

---

# SPEC 3 — What comes next (spec and hold)

Not to be built until the foundation is green.

## 3.1 A vocabulary for the food chain

**The gap:** no model can direct work to another. `spawn_task` delegates but
has no model or tier argument; `switch_model` changes the user's seat, returns
nothing, and takes effect next message.

**Design:** add `tier` to `spawn_task` — `small_local`, `large_local`,
`cloud_frontier` — plus a synchronous `ask_tier(tier, task)` that returns the
answer to the caller rather than to the task tray. `cloud_frontier` routes
through the existing consent record and cost ledger, so escalation surfaces to
the user rather than spending quietly.

**Why first:** everything else on this list is easier afterwards, and the
four-tier architecture is inert without it.

## 3.2 Arbiter sees foreign seats

Same as 2.3. Listed twice because it blocks both.

## 3.3 Give the reflex tier its real job

Ternary-Bonsai 4B is downloaded and runs on CPU. Its first assignment — tool
selection — went to embeddings instead, at 200× the speed and better accuracy.
Its actual fit is the short-prompt judgement calls: sensitivity classification,
local-versus-cloud escalation, which tier a task belongs to. Those are
occasional, so CPU latency of a few seconds is affordable.

Depends on 3.1 existing, since "which tier" is meaningless until tiers can be
addressed.

## 3.4 Instrument the prompt cache

The single largest performance fact found today: a cached prompt costs 0.4–5
seconds, an uncached one 35–50. That is an 82× difference and nothing measures
it. Track hit rate per turn, surface it, and treat a drop as a performance
regression rather than as weather.

## 3.5 Ship the honesty suite

`tests/honesty/` is an empty directory somebody already named. Friday's
distinguishing claim is that it tells you the truth about itself. That property
is currently defended by code review and by you noticing. It should be defended
by tests before any of the above lands on top of it.

---

# Sequencing

Foundation, in order: 2.3/3.2 (arbiter), 2.5 (receipts over regex), 3.5
(honesty suite), 2.4 (latency budget). Then Spec 1 (multi-window), which is the
one you can feel. Then 3.1 (tiers), then 3.3 and 3.4.

Spec 1 could jump the queue if juggling models matters more to you this week
than correctness does. That is a product call, not an engineering one.
