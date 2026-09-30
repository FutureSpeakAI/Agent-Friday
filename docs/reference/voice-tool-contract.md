# Making a typed tool voice-callable

> **Status:** ACTIVE — the contract for adding a tool to the live voice session.
> **Audience:** anyone adding a capability that voice should reach.
> **Companion:** [voice-capability.md](voice-capability.md) says what voice can do,
> in the user's words. This says how to add to it.

Voice already reaches every chat capability through `delegate_to_friday`, which
hands a spoken request to the full agent with the whole tool registry. So the
question is never "can voice do this" — it is **"is this worth a direct tool?"**

Give a capability its own voice tool when the spoken phrasing is the whole
request and a background hand-over would have to guess: *"run my morning
routine"*, *"what's on my calendar"*, *"how reliable is that source"*. Leave it
to `delegate_to_friday` when the work is open-ended, long, or needs several
tools — that is not a lesser path, it is the right one.

---

## 1. The five steps

Everything lives in `services/voice_engine.py` unless noted.

1. **Declare it** in `_VOICE_LIVE_TOOLS` — a plain 4-tuple, so nothing imports
   `google.genai` at module load:

   ```python
   ("tool_name",
    "What it does, when to use it, and how to behave while it runs.",
    {"arg": ("string", "What this argument is.")},
    ["arg"])                      # required args; [] if none
   ```

   An argument's type is `string`, `integer`, `number`, `boolean`, or `array`
   (a list of strings).

2. **Route it** in `_voice_tool_run`, through `_governed`:

   ```python
   if name == "tool_name":
       return _governed("tool_name", _tool_tool_name, args)
   ```

   `_governed` calls `agent._execute_tool`, which is the one path to a handler.
   Going around it skips the governance check, the provenance ledger, the audit
   and the PII hooks — a spoken request must pass the same checkpoint a typed
   one does.

3. **Allow it** in `governance/action_gate.py`. A tool absent from the ring-1
   list raises an approval card on every call, which in a conversation means
   the user approving a card to hear their own calendar. Put it in ring 1 only
   if it reaches no one outside this machine, and say why in a comment beside
   it. If it *does* reach someone, leave it out: the card is correct.

4. **Set the conversation** if the tool starts background work. Anything that
   spawns a task or a chain reads `agent._CURRENT_CONVERSATION` to know where
   to report. The chat path sets it; the voice path must too, or the work runs
   correctly and reports where nobody in the call is looking:

   ```python
   _tok = _ag._CURRENT_CONVERSATION.set(
       session.get("conversation_id") if isinstance(session, dict) else None)
   try:
       return _governed(name, _fn, args)
   finally:
       _ag._CURRENT_CONVERSATION.reset(_tok)
   ```

5. **Test it.** `tests/unit/test_voice_parity.py` holds the shape tests; add
   yours beside them. At minimum: the tool is declared, its required args are
   right, and — if it spawns work — the conversation is carried.

---

## 2. The description is the behaviour contract

The model never reads this file. The description string **is** how it behaves,
so it carries the manners as well as the meaning. Four rules, each from
something that went wrong:

- **Say what to do while it runs.** A tool that returns before its work is
  finished must say so: *"say one short sentence that it has started, keep the
  conversation going, and do NOT guess what it produced."* Without that the
  model invents a result and says it out loud.
- **Name the fallback.** If a result can come back empty or unconnected, say
  what to tell the user instead of letting the model conclude it cannot help:
  *"if it says connected:false, that integration needs a one-time connection —
  offer to help; do NOT say you can't access their email."*
- **Say it aloud, not on screen.** Voice output is spoken. Ask for a sentence,
  not a table: *"read it back as a sentence, not a table."*
- **Keep it short and concrete.** These descriptions are sent on every session
  and are read by a model under latency pressure.

---

## 3. Spoken confirmation

Anything outward — sending, buying, posting, changing something beyond this PC
— raises an approval card, in voice exactly as in chat. That is not a voice
restriction and must not be worked around.

What voice adds is the ability to **decide the card by speaking**, and the rule
is that the user's own words have to carry the decision:

- `local_context.spoken_decision(owner_words, room_mode)` returns
  `"approve"`, `"deny"` or `None` from what the user actually said. "No" beats
  "yes" in the same breath.
- The model **reports** a decision; it does not make one.
  `decide_by_voice(approval_id, owner_words, room_mode, claimed)` accepts it
  only when the user's words agree with what the model claims they said. A
  model that mishears an approval cannot manufacture one.
- In **room mode** the words must name Friday — *"Friday, send it"* — because
  voices are not told apart. The owner can turn that off
  (`voice_room_approvals_require_name`), and turning it off means anyone within
  earshot can approve. Default on, and the only voice limit that is.

If your tool raises a card, reuse this path. Do not add a second way to say yes.

---

## 4. Reading an approval card back

A card the user cannot see is a card they cannot judge, so voice reads it back
before asking. The pattern, as the payload card does it
(`services/local_context.py`):

1. **One sentence that something is waiting, and why.** Not the card's full
   text — the shape of the decision.
2. **The exact thing being decided, when it is short enough to say.** The
   payload card reads out the text that would leave the machine, because
   approving a summary you have not heard is not approval.
3. **The three ways out**, in the user's words: send it, don't send it, or
   change it. `revise_share_request` exists so "change it" is real.
4. **In room mode, keep the rationale out of it.** Do not read back anything
   that quotes the user's history in front of other people; say that something
   needs them on screen. (Owner rules and anomaly pauses will state this as a
   rule of their own — `docs/design/active/owner-rules-and-anomaly-detection.md`
   §7.4 — which is specified and **not yet built**. Follow it anyway; it is the
   right shape for any card.)

A declined card sends nothing and says only that. It never re-asks on its own.

---

## 5. Private data: hand it to the local model

**The rule.** When a spoken request needs the user's own data — mail, vault,
wiki, files, contacts, finances, health — the raw data is read by a **local**
model on this machine, and only its scrubbed summary reaches the cloud voice
model. Never pass raw private data to a cloud voice session, and never build a
path that could.

Use `ask_local_for_context`; do not reimplement it. What it already does:

- picks a local seat — one that is already serving, else the sidekick, else a
  cold one it summons — and **says which out loud before the wait**;
- refuses, in words, when there is no local model at all. It does not fall
  through to the cloud;
- scrubs the local answer through core's PII gate (`core._scrub_pii`) into
  placeholders, and turns people into relationships (`[his sister]`). It fails
  **closed**: a scrub that cannot run shares nothing;
- applies the egress floor (`judgment_gate.never_send_hits`,
  `hard_identifier_hits`), which refuses the **whole** answer rather than
  trimming a phrase out of it;
- shows the owner the exact text on a card, and
- signs a receipt into the decision BOM naming exactly what left, where it
  went, which local model produced it, and the categories of what was held
  back. The receipt is signed **before** the text leaves; if it cannot be
  signed, the share does not happen.

If you are adding a tool that touches private data, the tool should return
context **through** this path rather than reading the data itself and handing
it back into the call. `tests/unit/test_voice_private_handoff_seam.py` asserts
at the point text is handed to the live call, not on the scrub's return value —
copy that habit.

**Background work gets this for free.** A task or workflow result that the
egress gate withholds is no longer a dead end: `routes/voice.py`'s
`_injection_or_card` hands it to `local_context.offer()`, so it comes back as
a scrubbed card instead of "content withheld". If your tool does its private
work in a background task — which is the right shape for anything slow — you
do not need to do anything to get this; it applies to the result on its way
into the call. What you *should* do is run that work on a local seat, so the
raw data never reaches a cloud model in the first place.

---

## 6. What you do not have to do

- **Add a restriction.** Voice-only limits are the owner's, off by default,
  unless a rule the constitution requires is at stake
  (`voice_engine.voice_restrictions`). If you think your tool needs a
  voice-specific limit, it probably needs an approval card instead.
- **Add a time limit.** A direct voice tool is already bounded by
  `voice_tool_hard_limit_s` (default 20s, `0` for none) and hands off to the
  background past it. Nothing is refused.
- **Handle the mic, the barge-in, or the transcript.** That is the session's
  job, not the tool's.
