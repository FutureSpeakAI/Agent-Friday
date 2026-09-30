# Voice-first: every limit, kept or removed

> **Status:** DECIDED and built, except where marked.
> **Decided:** 2026-09-29, on the owner's instruction: *"No restrictions unless
> the user explicitly sets them… every voice-specific restriction becomes a user
> setting, OFF by default, unless it protects something the constitution
> requires. The approval gates and cLaws still apply to voice exactly as they do
> to chat. That isn't a voice restriction."*

## The rule applied

A limit counts as **voice-specific** only if a typed request would not meet it.
By that test, most of what the Voice tab lists was never a voice restriction at
all: it is either the owner's own setting, already, or a rule that applies
identically in chat.

`services/voice_engine.voice_restrictions()` is the live list — the Voice tab
renders it and it reflects the current settings, so it cannot drift from what
is enforced. `tests/unit/test_voice_parity.py` fails if a future entry is
neither the owner's setting nor an exempt kind.

---

## Kept, and not a restriction (no setting, by design)

| Limit | Kind | Why it stays |
|---|---|---|
| Approval cards for outward actions | governance | Sending, buying, posting or changing anything beyond this PC waits for the owner's OK — **in voice exactly as in chat**. The owner's own carve-out. |
| Never-send list | privacy | Withheld from every cloud model on every surface. Not voice-specific. |

Neither is negotiable by a voice setting, because neither is about voice.

---

## Kept, now the owner's to change

| Limit | Setting | Default | Why this default |
|---|---|---|---|
| Ceiling on a direct voice tool | `voice_tool_hard_limit_s` | `20` (`0` = none) | **It refuses nothing.** Past the ceiling the work continues in the background and reports back; the ceiling only decides whether it holds the line meanwhile. Left on so a conversation never goes silent. Widen or remove it freely. |
| Spoken approvals in a room must name Friday | `voice_room_approvals_require_name` | `true` | **The one place this inverts the owner's general rule.** See below. |

---

## The room-mode tradeoff — the owner's call

In `voice_room_mode: "room"`, a spoken "yes" to an approval card counts only if
the words name Friday: *"Friday, send it"*.

This is left **on** by default, which is the opposite of the owner's stated
default, for one reason: it is an **identity gap, not a restriction**.

- In chat, an approval arrives on a signed-in session. Friday knows who
  approved.
- In a room with several people, Friday does not tell voices apart
  (`services/voice_engine.py:_voice_room_mode`). "Yes" from anyone present
  would count as the owner's approval.
- So removing it does not grant voice a capability it lacks. It changes *whose
  consent counts* — which is the approval gate's own requirement, and the owner
  said the approval gates stay.

**Turned off, anyone within earshot can approve anything on a card** — a
payment, an email, a post. That is a real and reasonable choice for a household
where everyone present is trusted, and it is one switch:
`voice_room_approvals_require_name: false`.

It disappears entirely when Household Identity lands and voices can be told
apart. Until then the name is a weak signal, not proof — someone can say
"Friday, send it" too. The setting buys deliberateness, not security.

**In one-person mode (`"one"`, the default) none of this applies**: an
unqualified "yes" approves, as it always has.

---

## Removed

Nothing had to be removed. The audit found no voice-only limit that subtracted
a capability — `delegate_to_friday` already handed any spoken request to the
full agent with the whole tool registry.

What it found instead was **reach that existed in chat and not in voice**,
which is the same complaint from the other side. Four tools were added to the
voice surface:

| Tool | What it unlocks spoken |
|---|---|
| `run_workflow` | "Run my morning routine." A workflow's steps run wherever it says, **including on the local model**, so this is how a spoken request reaches private work without any of it passing through the cloud voice model. |
| `workflow_status` | "What routines do I have?" / "How is it going?" With no name it lists them, which is what a spoken request needs before it can name one. |
| `navigate_to` | "Open the Harbor Legal email." Deep links to an email, file, wiki page, Settings tab, calendar day, contact or post — not just a whole workspace. |
| `check_situation` | "What are you working on?" / "Is the GPU busy?" / "What did today cost?" |

All four are ring-1 (no card): three are reads, and `navigate_to` acts only on
the owner's own screen. Anything outward *inside* a workflow still raises its
own card, one at a time, exactly as in chat.

And one limit that was invisible until those tools existed: **a private
workflow result could not come back at all.** The egress gate's only move on
TIER_3 material is to withhold it, so "summarise my own mail" ran on a local
seat and returned "content withheld" — the promise kept, the answer lost. A
withheld task or workflow result is now offered through the handoff path
instead: scrubbed to placeholders, refused outright by the never-send floor if
it touches that, receipted, and read by the owner before any of it leaves.

This is not a relaxation of the gate. The gate blocks or it does not; this
adds the third option the gate has no way to express — *scrub it and ask*.
Pinned by `tests/unit/test_voice_workflow_result_handoff.py`, including that
the model never receives the raw text and that a floor refusal still stands.

---

## Not a restriction we could act on yet

`docs/design/active/owner-rules-and-anomaly-detection.md` §7.4 says cards
raised by a rule or an anomaly pause **cannot** be approved by voice in room
mode at all — stricter than the naming rule above.

**Owner rules are specified and not built**, so there is nothing to remove or
soften today. When they are built they should use
`voice_room_approvals_require_name` rather than a second, separate rule, so the
owner has one switch and not two. Recorded here so that decision is not made
again from scratch.

---

## What did not change

- **Local-only mode** (`model_routing.mode == "local_only"`) still stops a
  cloud voice session and its hand-overs. That is the owner's setting doing
  exactly what it says.
- **The private-context handoff** is unchanged in policy and stronger in
  practice: it now signs a receipt naming exactly what left, and chooses a
  local seat (or the sidekick) out loud. See
  [voice-tool-contract.md](../reference/voice-tool-contract.md) §5.
- **Recorded unrestricted cloud consent does not disable the handoff's scrub.**
  `seal_outbound` skips its scrub under that consent; the handoff scrubs
  directly through `core._scrub_pii` and always will. Honouring "unrestricted"
  there would silently turn "ask my local model" into "send my mail to Google",
  which is not what either setting says. Pinned by
  `test_unrestricted_cloud_mode_does_not_disable_the_handoff_scrub`.
