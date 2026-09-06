# Five settings that persist and redraw but drive nothing — five decisions

**ANSWERED by Stephen, not yet built.** Four get built for real: streaming
responses, auto-open chat, compact mode (a genuine small-window mode, not
a CSS tweak), and startup workspace. The fifth, `scene_name`, was
investigate-and-remove-if-dead — investigated and confirmed fully dead
(the real holographic-background system runs on entirely different state;
removing this setting does not touch it) and removed, with a proof test
(`tests/gauntlet/test_dead_scene_name_setting_removed.py`). This ruling
predates and is separate from the 2026-09-04 delegation of the main
findings queue (see `progress.md`'s "DELEGATION RESOLUTION" section) —
the four real builds are substantial, standalone feature work (real
token streaming, a real compact UI mode, etc.), each large enough to
warrant its own implementation pass rather than being folded into that
queue-clearing session. Not attempted here; recorded so the ruling isn't
lost and isn't mistaken for still being an open question.

These were found 2026-09-03 (KNOWN_ISSUES.md) and re-confirmed tonight:
each toggle/picker saves correctly, reads back correctly, and is the
*only* code anywhere in `src/` or either HTML file that ever looks at its
own key. Nothing else consults them. They are not broken in the sense of
losing data — they are broken in the sense of being props on a stage set.

Each one is a real product decision, not a bug with one right answer,
which is why none of them got fixed unilaterally tonight. Answer each
with a single letter; five minutes, not a project.

---

### 1. `stream_responses` — "Streaming Responses" ("Stream tokens as they arrive.")

**What's actually true today, which the toggle doesn't reveal:** chat
responses never stream at all, on or off. `routes/chat.py` imports
`stream_with_context` but never calls it — there is no streaming
implementation anywhere in the codebase for this toggle to gate. Turning
it off doesn't stop something; turning it on doesn't start something.

- **(A) Build it.** Real token streaming is a genuine feature people
  expect from a chat product; wire the toggle to an actual
  Server-Sent-Events/chunked response.
- **(B) Remove it.** If streaming isn't planned soon, a toggle for a
  feature that doesn't exist is worse than no toggle.
- **(C) Leave it, note it.** Keep the toggle as a placeholder for planned
  work, but that's a real user-facing lie until (A) happens — recommend
  against this one specifically.

**My read:** (A) or (B), not (C) — this is the one of the five where the
gap is a missing *feature*, not just an unwired setting.

---

### 2. `auto_open_chat` — "Auto-open Chat on Launch" (Settings → Interface)

No description text beyond the label. Saves and redraws; nothing checks
it at startup to decide whether the chat panel opens.

- **(A) Build it.** Straightforward: read the flag once at first paint,
  open the chat panel if true.
- **(B) Remove it.** If "always open" or "always closed" is fine as the
  permanent behavior, drop the toggle.

**My read:** cheapest of the five to build if you want it — (A) is a
small, contained change with no design ambiguity.

---

### 3. `compact_mode` — "Compact Mode" ("Reduce padding and element sizes.")

Same shape: toggle exists, promise is purely visual (CSS density), never
applied.

- **(A) Build it.** Needs a CSS class toggle threaded through the
  relevant components — more surface area than #2 since "compact" has to
  actually mean something everywhere it would show.
- **(B) Remove it.**

**My read:** lower priority than the others — cosmetic, and the "define
compact everywhere" scope is the reason it's likely still unbuilt. Fine
to leave queued longer than the rest if you're not planning a density
pass soon.

---

### 4. `scene_name` — Scene picker (10 options: nebula/matrix/void/aurora/
cosmos/midnight/prism/circuit/ocean/ember) ("Holographic background scene.")

The dropdown saves a choice among 10 named scenes; nothing renders a
different scene based on it. Checked just now: none of the 10 names
(nebula, matrix, void, aurora, cosmos, midnight, prism, circuit, ocean,
ember) appear anywhere else in `index.html` — not in the Three.js scene
layer, not anywhere. This isn't "unwired," it's fully fictional: the
picker offers ten choices where zero renderable scenes exist behind any
of them.

- **(A) Build it.** Real work — this means designing and implementing up
  to 10 distinct holographic scene variants from scratch, not wiring an
  existing thing. Biggest lift of the five by far.
- **(B) Remove it.** If a scene picker isn't a near-term priority, this is
  the clearest "just cut it" of the five — there's nothing partially built
  to preserve.

**My read:** (B), unless a multi-scene visual pass is already on your
roadmap — building 10 real scenes to match this dropdown is a project of
its own, not a settings fix.

---

### 5. `startup_workspace` — Startup Workspace picker (list of your configured
workspaces, or "None")

Saves a chosen workspace id; nothing opens that workspace on launch.

- **(A) Build it.** Same shape as #2 — read once at startup, navigate to
  the chosen workspace.
- **(B) Remove it.**

**My read:** same cheap-to-build category as #2 — no real design
ambiguity, just needs the one missing wire.

---

## If you want the fast path

If you don't want to think about all five individually right now: **(A)
for #2 and #5** (cheap, unambiguous, no scope question), **(B) for #1 and
#4** (don't leave a feature-shaped lie live; don't build 10 scenes to
match a dropdown unless that's already planned), **defer #3** (the one
real "how much are we actually building" question, worth more than five
minutes). That combination is fixable in one small, low-risk batch
whenever you're ready to greenlight it — say the word and it can be this
run's next fix, proven the same way as everything else tonight.
