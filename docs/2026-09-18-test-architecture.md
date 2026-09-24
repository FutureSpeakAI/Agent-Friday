# Friday: capability audit and test architecture

Written 2026-09-18, from the running system rather than from the docs.
Every number below was measured on your machine today.

---

## Part 1 — What Friday can do now

75 tools, 17 workspaces, 5 declared capabilities. Grouped by what they actually
reach:

**The outside world.** `search_web`, `browse_web`, `search_news`. Currently
degraded — DuckDuckGo returns anti-bot walls, Brave is built and unkeyed.

**Google, deeply.** Calendar read *and* write (5 tools), Gmail read, Drive
search, Docs/Sheets read, Tasks full CRUD (5 tools), Contacts search. This is
the most complete integration in the system and the one most likely to be
taken for granted. Both accounts currently need reauthorization.

**The machine itself.** `run_command`, `open_path`, `open_url`, plus real
OS control: `move_mouse`, `click`, `type_text`, `press_key`, `scroll`,
`screenshot`, `write_clipboard`. Friday can drive the desktop, not just
describe it.

**Its own memory.** A personal wiki (read/search/propose/correct), a knowledge
graph over it (`knowledge_query`, `knowledge_related`, `knowledge_communities`),
and a trust graph of people.

**Its own body.** `switch_model` changes the seat, `navigate` drives the UI,
`revert_workspace` and `list_workspace_history` undo what it did to your
workspaces. Friday can modify its own interface and roll the change back.

**Creative production.** Image, video and music generation, timeline assembly
to a finished export, real HTML decks and websites, a 24-candidate image
bake-off with self-scoring, and a Series Bible for persistent creative memory.

**Media perception.** `inspect_image` looks at a file with a vision model,
`inspect_audio` listens and transcribes. Friday can check its own output.

**Voice.** ElevenLabs synthesis plus the local Piper/Kokoro/Whisper stack.

**Subprocesses.** `spawn_interactive_session`, `send_to_session`,
`read_session_output` — a persistent CLI it can converse with.

**Work management.** Stored multi-step workflows with live status, background
tasks with chained follow-ups.

**Self-examination.** `epistemic_score`, `personality_show`,
`personality_check_sycophancy`, `learn_skill`.

### The honest edge of that list

Three things it reports as capabilities that do not currently work:
web search (no key on a real provider), Google (both accounts need
reauthorization), and `pii_ner` (declared absent by design, Layers 1a+1b only).

And one structural gap found today: **no model can direct work to another
model.** `spawn_task` delegates but takes no tier or model argument;
`switch_model` changes your seat but returns nothing. The food chain exists in
the architecture and nothing can address a rung of it.

---

## Part 2 — What should be true out of the box, and isn't

Table stakes for a system with this surface area:

1. **Search that works without a key hunt.** Brave is built, metered, and
   health-modelled. It needs a key and a first-run prompt, not a code change.

2. **A connection that announces its own death.** Google went nine days
   needing reauthorization while Friday answered calendar questions. The
   repair exists now; the pattern should be general — any credential that
   expires should surface at the top of the next relevant turn.

3. **One model resident, chosen honestly.** Today an idle Ollama runner cost
   Bonsai a factor of nine in speed, and the arbiter could not see it because
   it did not spawn it. A scheduler that only knows half the processes on the
   card is not a scheduler.

4. **Latency that does not scale with vocabulary.** Before today, every turn
   re-read ~39,000 tokens. That is now ~24,000 and falling. The ceiling should
   be a budget, not an accident.

5. **A visible seam between "I did" and "I will".** Today's guard fixes were
   both about this. It should be structural, not a regex.

---

## Part 3 — Backend test architecture

522 test files exist: 306 unit, 102 api, 95 gauntlet, 6 security. Nine test
directories are **empty** — `honesty/`, `conformance/`, `golden/`, `persona/`,
`probes/`, `screenshots/`, `audit_screenshots/`, `app/`, `.asimovs-mind/`.
Somebody planned all of this and built none of it. That is the map of the gap.

The governing principle, from your own notes: *a passing test is only evidence
if it could have failed.* Every suite below is designed so the failing case is
reachable.

### Tier 1 — Seam tests (highest value)

Every bug found today lived at a seam, not in a function:

| Seam | The failure it produced |
|---|---|
| catalogue ↔ installed | a model listed but absent routed local, then fell to cloud silently |
| arbiter ↔ foreign seats | Ollama double-booked the GPU; a hand-started seat read as "not installed" |
| router ↔ dispatch | seat change wrote a setting nothing read |
| stream ↔ encoding | correct UTF-8 became Latin-1 mojibake, streamed path only |
| budget ↔ relevance | tools trimmed 75→8 by size, dropping the one the turn needed |
| settings ↔ runtime | a 200KB context cap written for a 1M-token cloud seat |

**Test shape:** assert the two sides agree, with a fixture that makes them
disagree. Not "does `installed()` work" but "when the catalogue says yes and
the daemon says no, which wins, and does the user find out?"

### Tier 2 — Honesty tests (the empty `honesty/` directory)

The through-line of everything found today. Friday's failures are rarely
incapacity; they are narration that outruns what happened.

- Every completed-action claim in a reply has a matching receipt.
- Every substitution (model, tool, provider, context) surfaces to the user,
  not only to the log.
- A degraded dependency produces a refusal naming the dependency, never a
  confident answer.
- A capability that exists but is unconfigured reports *unconfigured*, never
  *absent*. (Friday told you it had no Firecrawl; the service was in the tree.)
- Recall never answers a live-state question. Probe registry coverage.

**Test shape:** run a turn with a dependency deliberately broken, assert the
reply contains a refusal and the log contains the reason. The failing case is
a confident answer.

### Tier 3 — Degradation matrix

For each of the 75 tools: kill its dependency, assert the failure is visible,
named, and does not escalate to cloud without saying so. Parameterised, one
test per tool, cheap to write once the harness exists.

### Tier 4 — Budget and latency regression

Today's numbers become tomorrow's assertions:

- Standing prompt overhead stays under a declared ceiling.
- Smart context stays within the seat-derived budget.
- Tool subsetting keeps the intent-relevant tool in the kept set (the
  `draft_email` case).
- Prompt-cache hit rate across a simulated multi-turn session.

The failing case is a regression in tokens, which is a regression in seconds.

### Tier 5 — Adversarial and safety

`security/` has 6 files against 75 tools with filesystem, shell and OS control.
Prompt injection through fetched web content, through wiki files, through
calendar event titles. Path traversal on `read_file`/`write_file`. Command
construction on `run_command`.

---

## Part 4 — Frontend tests with machine-vision verification

`screenshots/` and `audit_screenshots/` are empty directories. This is the
part that doesn't exist at all, and it's the part where Friday is unusual:
**it already owns every tool needed to test itself.**

`screenshot` captures. `inspect_image` looks with a vision model.
`click`/`type_text`/`press_key`/`scroll` drive. `navigate` moves the UI.

### The loop

```
navigate(workspace)            →  put the UI in a known state
screenshot()                   →  capture what the user would see
inspect_image(shot, question)  →  ask a vision model a FALSIFIABLE question
assert on the answer           →  fail loudly, keep the frame as evidence
```

The discipline that makes it real, and the trap it avoids: **the question
asked of the vision model must be answerable "no".** "Does this look right?"
is a vacuous test — it will say yes. "Does the model selector read
`bonsai2:27b`, and is there any error banner visible?" can fail, and names
what failed.

### Suites

**A. Workspace conformance.** All 17 workspaces: navigate, screenshot, assert
the workspace rendered, no error banner, no mojibake, no empty-state where
data should exist. Golden frames stored per workspace; diff on change.

**B. The honesty surfaces.** These are the screens that lie when they break,
and today proved each one can:
- Settings → Models: does the seat shown match `describe_dispatch`?
- Model Soup card: does the posture shown match `settings.json` on disk?
- Connectors: does Google's status match the token store?
- Voice settings: does "effective" match what a session would actually get?

The assertion compares **the pixels to the API**, not the pixels to a
snapshot. A screen that agrees with a stale cache is the bug.

**C. The round trip.** Type a real prompt into the chat, wait, screenshot,
and verify with vision that: the reply rendered, the model badge names the
seat that actually served it, no mojibake, and any tool the reply claims to
have used appears in the receipt strip. This single test would have caught
three of today's bugs.

**D. Creative output verification.** Friday generates an image, a deck, a
website, a video. `inspect_image` looks at the result and answers whether it
matches the brief. This is your own visual-verification rule turned into a
gate: pipeline-green is not visually right.

**E. Liquid UI / workspace mutation.** Friday changes a workspace, screenshot,
assert the change is visible; `revert_workspace`, screenshot, assert it is
gone. Undo is only real if you can see it undone.

### Harness notes

- Run against a **dedicated Friday instance on another port** with its own
  `FRIDAY_HOME`. Never the instance you are using — I killed your seat twice
  today by benchmarking against the live one.
- Frames land in `tests/screenshots/<suite>/<case>-<timestamp>.png` and are
  kept on failure, discarded on pass.
- Every vision assertion records the question asked and the answer given, so a
  flaky verifier is diagnosable rather than mysterious.

---

## Part 5 — What we can make Friday do next

Ordered by leverage, not by appeal.

1. **Give the food chain a vocabulary.** A `tier` argument on `spawn_task`
   plus a synchronous variant that returns to the caller. This is the one that
   turns four models into a system. Everything else on this list is easier
   afterwards.

2. **Teach the arbiter to see the whole card.** Including seats it did not
   spawn. Without it, the soup starves itself every time it switches.

3. **Make the reflex tier real.** The 4B is downloaded and runs on CPU. Its
   job is not tool selection — that went to embeddings at 200× the speed. Its
   job is the judgement calls with short prompts: is this sensitive, is this
   worth the cloud, which tier should this go to.

4. **Close the prompt-cache loop properly.** 86.7% of the system prompt is
   already stable. Instrument the hit rate, alert when it drops, treat cache
   misses as a performance bug rather than weather.

5. **Ship the honesty suite.** Friday's distinguishing claim is that it tells
   you the truth about itself. Right now that property is defended by code
   review and by you noticing. It should be defended by tests, in the
   directory somebody already named `honesty/`.

6. **Then the capability work** — vision round trips, the creative pipeline,
   voice — on a system that can be trusted to report its own state.

---

## Appendix — what I did not verify

- The 17 workspaces are read from the `navigate` tool's own description; I did
  not open each one.
- Tool counts and descriptions are from the live registry.
- The empty test directories are as found on disk; I did not check git history
  for whether they once held anything.
- Latency figures are from the Bonsai seat at 64K on the RTX 4070 and do not
  transfer to any other seat.
