# Five settings that persist and redraw but drive nothing — five decisions

> **Status:** active
> **Last verified:** 2026-09-09 (addendum: three more found, tally now 8)
> **Implementation:** none yet — `scene_name` removed, with proof test `tests/gauntlet/test_dead_scene_name_setting_removed.py`; the other four are ruled but unbuilt. Until each is built, the Settings pane does not show its control (`auto_open_chat`, `compact_mode`, `startup_workspace` removed from Settings; `tests/unit/test_settings_structure.py`), so no switch claims an effect it does not have. The ruling to build them stands.
> **Supersedes / superseded by:** —
> **Written:** 2026-09-04

## Implementation notes

Ruled by the maintainer; not yet built. Four get built for real: streaming responses, auto-open chat, compact mode (a genuine small-window mode, not a CSS tweak) and startup workspace. The fifth, `scene_name`, was investigate-and-remove-if-dead — confirmed fully dead (the real holographic-background system runs on different state) and removed. The four builds are substantial standalone feature work, each warranting its own implementation pass; this record exists so the ruling is not mistaken for a still-open question. The options and "my read" lines below are the original request as put to the maintainer.

---

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

---

## Addendum 2026-09-09 — three more, found while building cloud voice

This document is becoming the honest running record of this defect class rather
than a snapshot of one night, so new instances are appended here as they are
found instead of accumulating in commit messages.

**The three.** `elevenlabs_api_key`, `elevenlabs_model` and
`elevenlabs_voice_id` were read by `services/elevenlabs_tools.py:_settings()`
and were **not declared in `DEFAULT_SETTINGS`**. Because `_load_settings_raw()`
whitelists against that dict —

```python
merged.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
```

— each one wrote to `settings.json` successfully, reported success, and was
discarded on the very next read. A user who set an ElevenLabs voice in settings
got their choice back exactly once, from the in-memory dict, and never again.

**Why this variant is worse than the original five.** The five were controls
that drove nothing. These three *did* have a consumer — `elevenlabs_tools.py`
genuinely reads them — so the failure was not "nothing happens" but "it works,
then silently stops working." That is harder to notice and harder to
attribute: the user concludes the feature is flaky rather than that the setting
is dead. The env-var and `core.ELEVENLABS_API_KEY` paths kept working
throughout, which masked the key case entirely for anyone who had set the env
var.

**Disposition: (A) build, not (C) leave-and-note.** All three are now declared
in `DEFAULT_SETTINGS` and read at real enforcement points in
`services/cloud_voice.py` (`selected_model()`, `selected_voice()`,
`_api_key()`). Four further cloud-voice keys (`inworld_api_key`,
`inworld_model`, `inworld_voice_id`, `inworld_plan_tier`) were declared the same
way in the same change, so the new surface does not repeat the pattern it was
built alongside.

**Pinned.** `tests/gauntlet/test_cloud_voice_egress_and_cost.py::TestSettingsAreLive`
asserts both halves for all seven keys: that each is declared in
`DEFAULT_SETTINGS`, and that changing it changes behaviour at the enforcement
point. Verified by mutation — undeclaring `inworld_plan_tier` turns the test
red. This is the "test that fails without it" bar rather than a test that
merely passes today.

**Running tally: 5 + 3 = 8.**

**The generalisable check, for whoever finds number nine.** Every one of these
eight was findable by the same two-line audit, which is cheap enough to run on
any change that adds a setting:

1. Grep for the key across `src/` and both HTML files. If the only hits are the
   save path and the redraw, it is dead in the original sense.
2. Grep for the key in `DEFAULT_SETTINGS`. If it is absent but read anywhere,
   it is dead in this new sense — it will work once and then revert.

A setting ships only with its enforcement and a test that fails without it.

