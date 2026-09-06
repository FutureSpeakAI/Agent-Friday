# ElevenLabs as Friday's voice — decided architecture

**Document:** `docs/design/elevenlabs-voice.md`
**Status:** **Architecture decided (2026-08-19, Stephen). Not built.** §§1–3, 5
remain the evaluation that produced the decision; §§4, 4.1–4.5 and 6 record
what was settled. Nothing in this document is implemented.
**Written:** 2026-08-19, alongside the `speak_text` / `list_voices` tools
(`services/elevenlabs_tools.py`), which *are* built and are a different thing.
**Amended:** 2026-08-19 — Friday's voice settled on Gemini Flash Live; subagent
voices settled as an opt-in ElevenLabs feature over a default relay.
**Companion docs:** `docs/VOICE_SYSTEM_SPEC.md`, `docs/VOICE_SYSTEM_OVERHAUL_SPEC.md`,
`docs/contracts/roles-and-model-identity.md`, `docs/SEATS_AND_TRANSPARENCY_SPEC.md`.

**One-line thesis:** *ElevenLabs is the right tool for audio Friday **produces**
and the wrong tool for audio Friday **speaks** — because Tier 3 is already a
native-audio duplex model, and replacing it with a text-to-speech vendor is an
architectural downgrade that also costs more than the plan can carry.*

**The decision that followed:** *Friday stays on Gemini Flash Live native audio.
Subagents may speak in ElevenLabs voices, but that is an **opt-in feature over a
default of Friday relaying** — one voice, one relationship, everything filtered
through her. The cost objection in §3 was entirely about **sustained**
conversation; subagents speak in **bursts**, which is the regime where
ElevenLabs is both cheap and worth it (§4.1).*

---

## 0. The distinction this document exists to protect

Three different things get called "Friday's voice," and conflating them is how
this decision goes wrong:

| | What it is | Today | ElevenLabs fit |
|---|---|---|---|
| **A. Produced audio** | Narration, voiceover, story audio — an *artifact* saved to disk | Nothing. This was a hole. | **Strong.** Already shipped as `speak_text`. |
| **B. Friday's speaking voice** | The voice that answers you in conversation | Tier 3 Gemini Live native audio; Tier 1 piper offline | **Rejected — decided.** Stays Gemini Flash Live. §2, §3. |
| **C. Per-role subagent voices** | Distinct voices per seat so you can hear who is talking | Not implemented | **Accepted as opt-in — decided.** Default is relay. §4. |

(A) is done. (B) is settled: **no change** — Friday keeps the duplex model, and
§§2–3 are the reasons, now recorded as closed rather than open. (C) is settled
in principle and unbuilt in practice; §4 is its design.

The load-bearing asymmetry: **the relay path is the one that must be solid; the
distinct-voice path is the feature.** If relay is correct and subagent voices
never ship, Friday is whole. If subagent voices ship on a shaky relay, every
failure is audible in the room. Build in that order.

---

## 1. What the account can actually do — **unverified, and why**

I could not query the account. The key supplied for this work
(`1a0c…7d96f`) is an **API key ID, not an API key**. ElevenLabs rejects it:

```
HTTP 400 api_key_id_used_as_api_key
"API key ID used as API key — only valid API keys can be used.
 API keys start with 'sk_' and are shown when the key is created or rotated."
```

Verified against both `/v1/user/subscription` and `/v1/voices`. So every
account-specific question below — plan tier, remaining credits, whether voice
cloning is enabled — is answered **conditionally by published tier rules, not
by reading this account.** Resolve it with one command once a real `sk_` key
exists:

```
curl -H "xi-api-key: sk_..." https://api.elevenlabs.io/v1/user/subscription
```

That returns `tier`, `character_count`, `character_limit`, and
`can_use_instant_voice_cloning` / `can_use_professional_voice_cloning` —
which settles §5 factually instead of by inference.

---

## 2. Latency — the number that kills (B)

| Model | Model latency | Notes |
|---|---|---|
| Flash v2.5 | ~75 ms | Speed model, 32 languages. ElevenLabs now recommends Flash over Turbo in all cases. |
| Turbo v2.5 | ~250–300 ms | Effectively superseded by Flash. Do not start new work on it. |
| Multilingual v2 | Not a latency model | Highest quality; the right default for narration. |

**75 ms is model latency, not end-to-end.** Add TLS + round trip from this
machine to ElevenLabs and realistic time-to-first-audio is ~150–300 ms on a
good connection. Streaming *is* supported (chunked HTTP and a WebSocket
endpoint), so playback can start before synthesis finishes — that part is fine.

The problem is not ElevenLabs' latency in isolation. **It is that adopting it
for (B) forces a cascade.** Today Tier 3 is
`gemini-2.5-flash-native-audio-latest` (`voice_engine.py:632`) — speech in,
speech out, one model. Routing Friday's voice through ElevenLabs means:

```
mic → STT (whisper) → LLM → ElevenLabs TTS → speakers
```

Three network-bound hops where there is currently one, and the loss of what
native audio gives for free: prosody carried from the model's own
understanding, barge-in / interruption handling, and no text bottleneck in the
middle. **You would be trading a duplex speech model for a nicer-sounding
vocoder bolted to the end of a pipeline.** That is a regression even if every
individual number looks good.

---

## 3. Cost — the number that kills (B) twice

Published rates: Multilingual v2 = **1 credit/character**; Flash / Turbo v2.5 =
**0.5 credits/character**. Plans: Free $0, Starter $6, Creator $22 (121k
credits), Pro $99 (600k), Scale $299, Business $990.

Assumptions for the arithmetic below: ~5.5 characters per spoken word
including spaces, ~150 words/minute → **~825 characters per minute of speech.**

**On Flash v2.5 (0.5 cr/char), one credit buys two characters:**

| Plan | Credits | Characters (Flash) | Minutes of speech | Hours |
|---|---|---|---|---|
| Creator $22 | 121,000 | 242,000 | ~293 | **~4.9 h/mo** |
| Pro $99 | 600,000 | 1,200,000 | ~1,455 | **~24 h/mo** |

Now price the actual use case. If Friday is a daily driver and speaks even
**2 hours a day**:

- 120 min × 825 chars = ~99,000 chars/day = ~49,500 credits/day on Flash.
- Creator (121k) is gone in **~2.4 days.**
- Pro (600k) is gone in **~12 days.**

**Conversational Friday exhausts the $99 plan in under two weeks.** Meanwhile
piper on Tier 1 is free, local, and already the bound `tts` role
(`core/__init__.py:1655` — `{"provider": "local-voice-lite", "model":
"piper-en_US-amy-medium"}`), and Gemini Live is billed on a different meter you
are already paying.

By contrast, the (A) use case is trivially affordable: a 2,000-word narrated
story is ~11,000 characters ≈ 5,500 Flash credits — **~4.5% of one Creator
month.** This asymmetry is the whole argument.

---

## 4. Subagent voices — DECIDED: an opt-in feature over a default relay

**Default:** a subagent does not speak. Friday relays it — one voice, one
relationship, everything filtered through her.
**Opt-in:** the user may allow subagents to speak in their own ElevenLabs
voices.

This inverts the framing in the pre-decision draft of this section, which
treated per-role voices as a transparency nice-to-have. The decision keeps that
scepticism where it belongs — in the *default* — while allowing the feature for
users who want it. Relay is not a fallback for when voices fail; **relay is the
product, and voices are an option layered on top.**

Design consequence, stated once and applying everywhere below: **the relay path
must be correct before the voice path is built.** Every mechanism in §§4.2–4.3
must work in relay mode first. A subagent voice is then a substitution at the
final step — who utters the words — not a different pipeline.

### 4.1 The burst case — why cost stops being the objection

§3's arithmetic killed ElevenLabs for **sustained** speech. Subagents are not
sustained; they speak in bursts. Same published rates, same
~5.5 chars/word assumption, Flash v2.5 at 0.5 credits/char:

A burst = a couple of sentences ≈ **300 characters**.

| Regime | Chars/day | Credits/day (Flash) | Against Creator (121k/mo) |
|---|---|---|---|
| Friday conversational, 2 h/day (§3) | ~99,000 | **~49,500** | gone in ~2.4 days |
| Subagents bursting, 10×/day | 3,000 | **~1,500** | ~45,000/mo → **~37% of plan** |

**~33× less.** That is the whole difference, and it is why the same vendor is
wrong for (B) and right for (C).

Budget ceilings within Creator, so the feature has a known edge:

| Model | Chars/mo | Bursts/mo @300 | Bursts/day |
|---|---|---|---|
| Flash v2.5 (0.5 cr/char) | 242,000 | ~806 | **~27/day** |
| Multilingual v2 (1 cr/char) | 121,000 | ~403 | **~13/day** |

So even on the quality model, ~13 spoken interjections a day fits inside $22.
This is also an argument for the §4.3 overflow rule on grounds other than
politeness: a system that wants the floor 50 times a day has both a taste
problem and a billing problem, and the same rule catches both.

> **Unverified.** These are published rates, not this account's. §1 still
> stands: the key supplied is an API key **ID**, not an `sk_` key, so plan
> tier and remaining credits could not be read. Substitute the real plan before
> treating any ceiling above as a budget.

### 4.2 Streaming context — answering "why did you do that" from the trace

The requirement: a voice must answer from the **live reasoning trace**, not from
a summary composed after the fact.

**The current substrate does not support this.** `chain_run_status` exposes
`log_tail` (last 3 lines) and `result_tail` (last 400 chars) from the `TASKS`
registry. Those are post-hoc tails — precisely the after-the-fact summary this
requirement rules out. A live subscription is new work, not a wiring change.

**Mode 1 — subagent speaks for itself.** The easy case: the subagent still holds
its own reasoning stream in-process at the moment it speaks, so "why" is
answered from context it actually has. The rule that matters is negative: it
must answer *from* that context, never **re-derive** an answer. Context can be
trimmed underneath it (`fit_tools_to_seat` already trims tool payloads to fit a
seat), so when the relevant span is gone the correct output is "I no longer have
that in context" — not a reconstruction. **A reconstructed rationale delivered
in a confident voice is the worst failure mode this feature can produce**, and
audio strips away the cues that would let a reader catch it.

**Mode 2 — Friday relays.** The harder case, and the one that must be solid.
Friday needs a **live subscription** to the subagent's stream, not a read of its
tail after completion. Shape: each subagent publishes reasoning deltas to a
per-task ring buffer readable at any point, including mid-run; Friday answers
"why" by reading that buffer. When the buffer does not contain the answer, the
correct behaviour is to **ask the subagent, or say she will find out** — never
to infer. This is the same discipline as `_execute_tool`'s unknown-tool message:
a dead end gets reported, because dead ends are where invented results come
from.

Both modes, one invariant: **what is spoken is a view onto the trace, never a
regeneration of it.**

### 4.3 Floor control — who holds the mic

Text tolerates six things talking at once. Audio does not. One audio floor, one
holder, Friday holding it by default.

1. **Requesting.** A subagent never speaks spontaneously. It raises a
   `speak_request` carrying role, urgency class, estimated length, and a
   one-line text summary — *the relay fallback*. **Nothing is synthesised at
   request time.** Synthesis costs money and would create audio that may never
   be played.
2. **Granting.** Friday grants only at an utterance boundary — never
   mid-sentence, never while the user is speaking.
3. **Yield and reclaim.** Friday yields explicitly ("Research has something")
   and reclaims on completion, budget exhaustion, or user barge-in. The floor
   always returns to her; it is never *left* with a subagent, so silence is
   always Friday's silence and never an ambiguous dead air.
4. **Utterance budget.** A granted turn is bounded — proposal ~200 chars / ~15 s.
   Overrun truncates gracefully and hands back ("there's more, on screen").
   Anything habitually longer belongs in relay or on screen, not in the room.
5. **Interruption classes — exactly two.** The **user** (absolute; cancels the
   queue outright, preserving barge-in), and a **halt-class event** (safety, or
   confirmation of an irreversible action). Nothing else: not errors, not
   completions, not progress. No subagent may interrupt Friday or another
   subagent.
6. **Two at once.** Priority, then FIFO — but the rule that matters is
   **overflow: past a queue depth of ~2, the queue does not lengthen, it
   collapses to relay.** Friday speaks one sentence covering everything pending.
   Queue depth is itself the signal that individual voices have stopped being
   informative.

**This is the audio analogue of the on-screen orb clutter, and it is worse.**
On screen, six orbs are clutter you can skim past or ignore; attention
reallocates in milliseconds and the cost is visual noise. In audio, six voices
are **serial** — they occupy wall-clock time that cannot be skimmed, cannot be
parallel-processed, and cannot be ignored while doing something else. Screen
clutter is a nuisance; audio clutter is an interruption of the room, and it
spends the one resource the user cannot get back. **Failing here is more
intrusive than failing on screen, so these defaults must be more conservative
than the on-screen defaults are.** When in doubt: relay, or say nothing.

### 4.4 Latency asymmetry — lean into it, don't hide it

Friday duplex is ~200 ms. A cascaded subagent is STT-final + LLM first token +
TTS TTFB ≈ **650 ms – 1.1 s**, before any of its own thinking time.

**Lean into it.** Three reasons:

1. The delay is *true information* — a specialist is working. It carries the
   same signal a person's pause carries, and users already read it correctly.
2. Hiding it means pre-rolling synthesis before the floor is granted: paying
   for audio that may never play, and pre-committing to a line before the
   reasoning that justifies it has finished.
3. Spoken filler ("let me think…") is Friday's voice saying something she did
   not decide to say. That is fabricated presence, and it is the audio version
   of a progress bar that is not measuring anything.

Implementation shape: **a short earcon on floor handover** — it marks the
transition, sets the expectation that a different speaker is coming, and covers
the gap honestly instead of pretending it isn't there. The asymmetry becomes a
cue that the system is deep rather than slow.

### 4.5 Binding a voice to a role

13 roles (`docs/contracts/roles-and-model-identity.md` §1). A `voice_id` is the
same shape of binding the role already carries for provider+model, and belongs
**alongside it, not in a parallel table**.

Resolve through `residency_policy.resolve_role()` before lookup. Contract §2 is
explicit that failing to resolve both alias tables renders duplicates — a voice
map keyed on unresolved aliases would hand one role two voices, which in audio
is not a cosmetic bug but a false identity claim.

**The honest limitation:** contract §3 says a role is not a model and not a
count of resident processes. So a voice identifies a **role**, not a
**process**. Two concurrent `researcher` seats share a voice and are
indistinguishable by ear. Recommendation: **accept this.** Roles are the unit of
meaning, and per-process voices would require allocating voices dynamically —
precisely the unbounded proliferation §4.3 exists to prevent.

Cost note: `voice_id` is a per-request parameter (already exposed by
`speak_text`), and there is no per-voice subscription charge — you pay per
character regardless of how many voices are in play. Stock voices are
unlimited; custom/cloned voices are capped per tier (§5).

### 4.6 A speaking agent is an egress surface of a different kind

Two distinct exposures, and the existing gate covers neither.

**Text sent for synthesis is ordinary network egress — and is currently
ungated.** `creative_engine.py:682` routes prompts through
`egress_gate.gate_text()` before any cloud call. **`speak_text` as built does
not.** Flagged gap: before subagent speech ships, synthesis input must pass the
gate with `"elevenlabs"` as the provider, or a subagent could speak a
vault-sensitive string straight to a vendor. This is a real hole in the tool
delivered on 2026-08-19, not a hypothetical.

**Audio into a room is a disclosure no gate can model.** Once synthesised,
speech is audible to everyone present — people who never joined the session and
consented to nothing. The egress gate reasons about bytes leaving the machine;
it has no concept of a room with other people in it, and no version of it will.
The mitigations are therefore product decisions, not engineering ones: the
opt-in default, a visible indicator whenever audio is live, and a standing rule
that vault-classified content is never spoken aloud regardless of who asked.

---

## 5. Voice cloning — available, but tier-gated (and unverified here)

| | Minimum tier | Audio needed |
|---|---|---|
| Instant Voice Cloning (IVC) | Starter ($6) | 1–3 min clean mono, ≥22 kHz |
| Professional Voice Cloning (PVC) | Creator ($22) | ≥30 min; ~3 h for production grade |

So cloning is almost certainly available — Starter and up covers IVC, Creator
and up covers PVC. **But whether *this* account is on a paid tier at all is
exactly what §1 could not check.** If the account is Free, cloning is
unavailable and several numbers above are moot.

Relevant to Friday specifically: a *custom* Friday voice is the one genuinely
compelling reason to consider ElevenLabs for (B). Neither piper nor Gemini Live
can give Friday a voice that is distinctly *hers* and consistent across
machines. If Friday having a recognisable, owned voice is a product goal rather
than a nicety, that is the argument that could override §2 and §3 — and it
should be argued on those terms, explicitly, not smuggled in as a quality
upgrade.

---

## 6. Decisions, and the order to build them in

**Settled (2026-08-19, Stephen):**

1. **(A) Produced audio — shipped.** `speak_text` / `list_voices` are
   registered and follow the existing tool pattern. Narration was a real hole;
   it is closed. Cost at this usage level is negligible.
2. **(B) Friday's voice — decided: no change.** She stays on Gemini Flash Live
   native audio. Barge-in and model-native prosody are preserved. §2's cascade
   regression and §3's ~12-day burn of a $99 plan both pointed here; this is
   now closed, not open.
3. **(C) Subagent voices — decided: opt-in over a default relay.** Default is
   Friday relaying: one voice, one relationship. Users may opt in to subagents
   speaking for themselves. §4.1 is why cost is no longer the objection.

**Still open, and deliberately so:** a *bespoke cloned Friday voice* (§5) is the
one thing that could reopen (B). If Friday having a recognisably owned voice
becomes a product requirement, the trade is paying latency and money for
identity — argued on those terms, explicitly, not smuggled in as a quality
upgrade.

### Build order — relay first, and not negotiable

The dependency is real, not stylistic. Every mechanism below is required for
relay; only the last is required for voices.

1. **Live trace subscription (§4.2).** The per-task ring buffer replacing
   `log_tail`/`result_tail`. Without it, "why did you do that" is answered from
   a post-hoc summary, which is the failure this feature exists to avoid.
   Needed by relay *and* voices.
2. **Floor control (§4.3).** Request/grant/yield/reclaim, utterance budget,
   two interruption classes, and the overflow-collapses-to-relay rule. Needed
   by relay, because relay also has to decide when Friday speaks unprompted.
3. **Egress gating for synthesis input (§4.6).** Route `speak_text` through
   `egress_gate.gate_text()` with `"elevenlabs"` as provider. **This is a hole
   in the shipped tool today**, independent of any of the above.
4. **Role→voice binding (§4.5).** Alias-resolved, stored alongside the existing
   provider+model binding. This is the only step that is *only* about voices,
   and it is the smallest.

If 1–3 land and 4 never does, Friday is whole and nothing was wasted. If 4
lands first, every defect in 1–3 becomes audible in the room.

### Before any of this is built, get the real numbers

- Rotate the leaked key (it was pasted in plaintext; see the session report)
  and issue a real `sk_` key.
- Run the `/v1/user/subscription` call in §1 to get **actual** tier, credits
  remaining, and cloning entitlement.
- Then re-read **§4.1** with the real plan substituted — that is where the live
  budget question now sits. The burst ceilings (~27/day on Flash, ~13/day on
  Multilingual v2 within Creator) are what set the floor-control overflow
  threshold in §4.3, so a different plan moves a design constant, not just a
  number in a table.
- **A bigger plan does not reopen (B).** §3's cost argument is now redundant to
  §2's architectural one: even on Scale or Business, cascading a duplex model
  into STT→LLM→TTS still loses barge-in and model-native prosody. Budget was
  the second reason, never the load-bearing one. Only the cloned-voice question
  (§5) reopens (B).

---

## 7. Sources

- [ElevenLabs models](https://elevenlabs.io/docs/overview/models) · [Meet Flash](https://elevenlabs.io/blog/meet-flash) · [Turbo v2.5](https://elevenlabs.io/blog/introducing-turbo-v25)
- [Pricing](https://elevenlabs.io/pricing) · [Plans & overages breakdown](https://flexprice.io/blog/elevenlabs-pricing-breakdown) · [Pricing 2026 guide](https://bigvu.tv/blog/elevenlabs-pricing-2026-plans-credits-commercial-rights-api-costs/)
- [Voice cloning help centre](https://help.elevenlabs.io/hc/en-us/sections/23821115950481-Voice-Cloning) · [Cloning capability review](https://www.coval.ai/blog/elevenlabs-review-2026-voice-cloning-and-synthesis-capabilities-explained/)
- [Hosted MCP server](https://elevenlabs.io/docs/eleven-agents/operate/hosted-mcp) · [MCP in Claude announcement](https://elevenlabs.io/blog/elevenlabs-mcp-in-claude)
