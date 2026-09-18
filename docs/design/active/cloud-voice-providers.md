# Cloud Voice Providers — ElevenLabs and Inworld as Tier-3 siblings

> **Status:** active
> **Last verified:** 2026-09-08
> **Implementation:** partial, 2026-09-09 - TTS only. `services/cloud_voice.py` (provider layer, gate, metering), `services/voice_indicator.py` (§4.4), `routes/cloud_voice_routes.py` (selection, disclosure, synthesis). STT, duplex and the §7.2 per-hour metering axis are NOT implemented. See §10.0 for the decisions taken.
> **Supersedes / superseded by:** partially supersedes [`elevenlabs-voice.md`](elevenlabs-voice.md) §6 decision (B); extends [`voice-system-spec.md`](voice-system-spec.md) §2, §7, §8
> **Written:** 2026-09-08
> **Method:** STORM — multi-perspective interrogation, simulated exchange, cited synthesis

## Implementation notes

- Nothing here is built. This is a design document.
- The provider research in §2 was gathered 2026-09-08 against vendor documentation and third-party benchmarks. Every figure carries a source. Figures marked *vendor-claimed* have not been independently reproduced and must not be treated as measured.
- This document **reopens a settled decision**. See §0. That reopening is the maintainer's instruction, not an inference drawn here, but the terms on which it can be reopened are constrained by the prior record and by §1's non-negotiables.

---

**One-line thesis:** *Cloud voice providers join Friday as named, user-selected Tier-3 siblings alongside Gemini Live — never as a default, never as a silent substitute, and never as a dependency the offline path can acquire.*

---

## 0. What this document does, and the decision it reopens

### 0.1 The collision, stated plainly

[`elevenlabs-voice.md`](elevenlabs-voice.md) is an **active decision record**. On 2026-08-19 it settled three questions, and one of them was settled *against* the change this document specifies:

> **(B) Friday's voice — decided: no change.** She stays on Gemini Flash Live native audio. Barge-in and model-native prosody are preserved. §2's cascade regression and §3's ~12-day burn of a $99 plan both pointed here; this is now closed, not open.

That document further states the conditions under which (B) could be reopened:

> **A bigger plan does not reopen (B).** §3's cost argument is now redundant to §2's architectural one: even on Scale or Business, cascading a duplex model into STT→LLM→TTS still loses barge-in and model-native prosody. Budget was the second reason, never the load-bearing one. Only the cloned-voice question (§5) reopens (B).

**This is the most important finding in this work, and it is not a documentation problem — it is a design problem.** The request that produced this document ("add ElevenLabs and Inworld.ai as cloud voice providers") is, read literally, the change that record closed.

### 0.2 How this document resolves it

It does not overturn (B). It reframes what is being added, on these grounds:

1. **(B) was a question about the *default*.** The prior record rejected *replacing* Gemini Live native audio with a TTS vendor in the conversational path. This document does not replace it. Gemini Live remains the Tier-3 default; ElevenLabs and Inworld are **additional, user-selected Tier-3 providers**. Under the "user chooses, never the product" constraint (§1.0), adding a choice is categorically different from moving a default.
2. **The §2 cascade objection survives intact and is preserved here as a first-class warning**, not deleted. A user who selects a TTS-only cloud provider for the conversational path *is* accepting STT→LLM→TTS in place of a duplex model, and the UI must say so at the point of selection (§4.3). The prior record's argument becomes a **disclosure requirement** rather than a prohibition.
3. **The §3 cost objection survives and gets teeth.** It is no longer prose in a design doc; it becomes live per-interaction metering and a budget ceiling (§7), wired to the existing `services/cost_meter.py`.
4. **The cloned-voice carve-out is the door the prior record itself left open.** If a recognisably owned Friday voice is a product goal, `elevenlabs-voice.md` §5/§6 says that is the argument that reopens (B) — argued explicitly. §10 Q1 puts that question back to the maintainer rather than assuming the answer.

**Net:** (B)'s conclusion — *Gemini Live remains Friday's default speaking voice* — stands. What changes is that it is no longer the *only* cloud option, and the reasons it won are now enforced as runtime disclosure and metering rather than as a closed door.

### 0.3 Document structure decision, and why

**Chosen: one new integration spec (this file) covering both cloud providers, plus three surgical edits to existing documents.** Rejected alternatives are recorded below because the reasoning matters more than the outcome.

What `git log --follow` actually shows (the folder names are misleading; all three files share a 2026-09-06 mtime from commit `f7df1d1`, a tree-wide docs restructure, not a content edit):

| File | Folder says | Last *content* commit | Reality |
|---|---|---|---|
| `design/active/voice-system-spec.md` | active | `2e7ff4f` **2026-07-06** | Live tier/selection/fallback design. Header self-describes as `partially-implemented`, "Spot-checked 2026-09-06, not fully reconciled." |
| `design/active/elevenlabs-voice.md` | active | `0a0a7ea` **2026-08-21** | Decision record. Newest substantive content of the three. |
| `design/historical/voice-system-overhaul-spec.md` | historical | `14dca0d` **2026-08-31** | Filed historical, but **edited more recently than the "active" spec**. Its own header names itself the incident record and points forward to the active spec. |

So the `historical/` label is correct in *role* but actively misleading in *recency* — it was touched 2026-08-31, nearly two months after the active spec's last real edit. Anyone trusting mtimes or folder names picks the wrong file. That reconciliation is recorded here in §0.3 and in the edits below.

- **Rejected: edit `voice-system-spec.md` alone.** It is the right home for the tier matrix and fallback changes, but it is a 50 KB document already self-described as unreconciled with its own implementation. Adding ~15 KB of provider design to a document whose acceptance criteria are "unconfirmed until checked" compounds an existing debt, and it would bury the §0.1 collision inside a file that never mentions ElevenLabs.
- **Rejected: edit `elevenlabs-voice.md` alone.** It is a *decision record*. Editing a decision record to reverse its decision destroys the artifact — the value of that document is that it preserves why (B) was rejected. Records get superseded by name, not rewritten. The repo already has the convention for this: a `Supersedes / superseded by:` header field.
- **Rejected: fold Inworld into `elevenlabs-voice.md`.** The provider-neutral machinery (selection surface, provider indicator, fallback, metering) is the substance; scoping it under one vendor's filename guarantees the next provider repeats the work.

**Companion edits required (specified in §11, not applied blindly):**
1. `voice-system-spec.md` §2 — tier matrix gains a Tier-3 provider column; §7.2 gains cloud-provider fallback rows.
2. `elevenlabs-voice.md` header — `Supersedes / superseded by:` gains a forward pointer to this file; §6 decision (B) gains a dated amendment note. **The decision text itself is not edited.**
3. `voice-system-overhaul-spec.md` — no content change; its `historical` status is correct. Its misleading recency is noted here instead.

---

## 1. The interrogation (STORM)

Four perspectives were run against the design. They are not four ways of agreeing. Where they conflict, the conflict is recorded and adjudicated rather than smoothed.

### 1.0 The three constraints, which no perspective may override

Stated up front because two of the four perspectives below argue against them and lose:

- **C1 — Both paths, always.** Local-private and cloud-frontier voice both remain available. The *user* chooses. Cloud providers are additions to the local path, never replacements. No default silently routes to cloud.
- **C2 — Transparency.** The user always knows which provider served a given interaction. No silent fallback from local to cloud. If the local path fails, that **surfaces and offers**; it does not substitute. Any provider indicator reflects what *actually served* the request, not what was requested.
- **C3 — Offline capability is the point.** Friday understands voice input and emits text to a synthesizer fully offline. That capability is why Gemma 4 E2B was chosen as the base model. Cloud providers are for quality and variety when the user opts in; **the offline path must never become load-bearing on a network call.**

These are architectural commitments, not preferences. They are settled input to this design.

### 1.1 P1 — The realtime voice engineer

*Has shipped duplex voice. Knows where latency actually bites.*

**Opening position.** The interesting number is not model latency, it is time-to-first-audio measured from the user's machine, and the two differ by more than people expect. ElevenLabs publishes ~75 ms for `eleven_flash_v2_5` — explicitly footnoted "excluding application & network latency" [elevenlabs.io/docs/overview/models]. Coval's independent benchmark, mirrored at openbenchmarks.com and synced 2026-09-07, measures **185 ms median / 230 ms p95 TTFA** for that same model under production-realistic conditions [openbenchmarks.com/text-to-speech-benchmark-by-coval]. **2.5× the vendor figure.** For `eleven_v3_conversational`: 280 ms claimed, 339 ms median / 433 ms p95 measured. Inworld publishes 20 ms p90 TTFB for `inworld-tts-2-flash` [docs.inworld.ai/tts/tts-models] and 25 ms p99 in its own marketing [inworld.ai/resources/best-tts-api-2026] — two of its own pages disagree — and **I found no independent latency benchmark for Inworld at all.** Treat every Inworld latency number as unverified vendor claim.

**The point that actually matters.** None of this is the real cost. `elevenlabs-voice.md` §2 already got this right and it is still right: today Tier 3 is one duplex hop, `mic → gemini-2.5-flash-native-audio-latest → speakers`. A TTS vendor turns that into `mic → STT → LLM → TTS → speakers`. Three network-bound hops where there is one. You lose barge-in and model-native prosody — *properties*, not latency. A p95 of 230 ms on the TTS leg is excellent and completely beside the point if the pipeline around it is 1.5 s and cannot be interrupted.

**Concession.** Inworld's Realtime API is a genuine duplex product — single WebSocket, STT→LLM→TTS server-side, semantic VAD with barge-in, drop-in OpenAI Realtime API compatibility [inworld.ai/realtime-api]. That is architecturally the same *shape* as Gemini Live, not a vocoder bolted on. It claims <1 s end-to-end. If cloud voice is happening, that is the honest comparison to Gemini Live — not the TTS endpoint.

**Demand.** Two distinct integration modes, named differently in the UI and never conflated: **duplex** (Gemini Live, Inworld Realtime API) and **synthesis-only** (ElevenLabs TTS, Inworld TTS endpoint). Selecting a synthesis-only provider for the conversational path must disclose the cascade at the point of selection.

### 1.2 P2 — The offline-path guardian

*Cares about exactly one thing: that the local path never regresses.*

**Opening position.** Every one of these has been a "just an option" before. The failure mode is not a decision to make cloud the default; it is **accretion**. A shared codepath gets a `provider` parameter. A helper starts calling `_api_key()` at import. A health check reaches the network. Six months later the offline path boots but the mic button is grey because a provider-catalog refresh timed out.

**The specific hazard here.** `services/elevenlabs_tools.py` already documents the discipline — "The HTTP client is imported LAZILY inside the call sites, so importing this module never requires network libs or a key — it stays import-safe." Good. That property is currently maintained by *one module's comment*. Once there are three cloud providers plus a registry plus a selection surface plus a cost meter, it needs to be a **test**, not a comment.

**On Inworld specifically.** It looked briefly like the offline story might improve — Inworld open-sourced `github.com/inworld-ai/tts` under MIT. It is **training and modeling code only**: no Inworld checkpoints, five commits, last release v0.5.0 September 2025, and it points at third-party `xcodec2` weights and Llama-3.2-1B-Instruct as base [github.com/inworld-ai/tts]. `huggingface.co/inworld-ai` returns empty. Their own comparison page's "Can I run TTS on-premise?" FAQ lists Deepgram, Google, Cartesia and ElevenLabs — **and does not list Inworld** [inworld.ai/resources/best-tts-api-2026]. **Treat Inworld as cloud-only.** It contributes nothing to C3.

**Demand.** A boot-time and CI assertion that with the network black-holed and every cloud key absent, Friday completes a full voice round trip on Tier 1. That test is the load-bearing artifact of this document. Everything else is UI.

**Where P2 overreached.** P2 argued for gating cloud providers behind a settings flag defaulted off *and* an additional confirmation on every session. Rejected: C1 says the user chooses, and a choice re-litigated every session is not a choice, it is nagging. One selection, persisted, with a permanent indicator (§4.4) satisfies C2 without punishing the decision.

### 1.3 P3 — The cost line

*These are metered APIs and the user is spending real money per sentence.*

**Opening position.** `elevenlabs-voice.md` §3 did the arithmetic and it holds: at ~825 characters per minute of speech, a Friday that talks 2 h/day burns ~99,000 chars/day. The current rate is $0.05/1K characters for Flash-class and $0.10/1K for v3/Multilingual, **flat across every subscription tier** — tiers change allotment and concurrency, not unit price [elevenlabs.io/pricing/api]. So 2 h/day ≈ **$4.95/day ≈ $150/month** on the cheap model. Inworld is materially cheaper: $25/1M chars for `inworld-tts-2` and $15/1M for Flash at On-Demand, falling to $12.50/$7 at Growth [inworld.ai/pricing] — for the same 99,000 chars/day, **$2.48/day on TTS-2, $1.49/day on Flash**, roughly a third to a half of ElevenLabs.

**Per-reply, the unit that belongs in a UI.** A ~200-character reply: ElevenLabs Flash **$0.010**, ElevenLabs v3 **$0.020**; Inworld TTS-2 **$0.0050** on-demand down to **$0.0010** at enterprise floor, Flash **$0.0030**. STT meters differently — ElevenLabs Scribe v2 $0.22/hr batch and $0.39/hr realtime; Inworld STT-1 $0.15/hr on-demand, $0.10/hr paid.

**The thing I actually want.** Not a monthly dashboard. **The cost of the interaction that just happened, next to the provider indicator, in the moment.** A number after the fact is an autopsy. The repo already has the machinery: `services/cost_meter.py` has `record()`, `meter()`, `get_budget()`/`set_budget()`, `_check_budget_alerts()`, and — critically — a PRICING convention already built for exactly this, reusing the `"in"`-per-1K slot as *USD per 1K characters*, with ElevenLabs rows verified against the vendor page on 2026-09-04. Inworld needs four rows in the same shape. **No new metering system. Extend the one that exists.**

**And the honesty rule already in that file.** `UNPRICED_MODELS` records rates that could not be confirmed against the provider's own page as *unpriced* rather than guessed, storing `cost_usd` as NULL — "an honest gap a UI can render as 'not priced,' not a number that looks like a fact." Inworld's rates were read from Inworld's own pricing page, so they qualify. But note Artificial Analysis lists Inworld at $20.8/1M and $10.4/1M — **which does not match Inworld's own list rates** [artificialanalysis.ai/text-to-speech/leaderboard]. Discrepancy unresolved; §10 Q4.

**Where P3 lost.** P3 wanted a hard monthly cap that disables cloud voice on breach. Rejected as specified: a hard cutoff mid-conversation is a silent behaviour change, which is a C2 violation dressed as thrift. Amended: on breach, cloud voice **stops being offered for new sessions** and the indicator says why; an in-flight session finishes.

### 1.4 P4 — The maintainer

*Will carry two cloud providers plus a local one and does not want the configuration surface to become unusable.*

**Opening position.** The provider count is not the problem; the **product of dimensions** is. Naively this is: 3 local/cloud paths × 2 modalities (STT, TTS) × per-provider voice × per-provider model tier × quality-vs-latency × fallback preference. That is a settings page nobody can reason about, and it is how you get the "five dead settings" already recorded in `docs/decisions/2026-09-04-five-dead-settings.md`.

**The good news, and it is genuinely good.** The repo's existing shape already absorbs most of this. `services/provider_registry.py` has `ROLE_VOICE`, and `local-voice-lite` and `nvidia-nemo` are already registered as providers with `"capabilities": ["asr", "tts"]`, a `models` list, `model_meta` labels, and an `auth` block. **Cloud voice providers are the same shape with `auth: {"type": "api_key"}`.** No new configuration concept is required — which is the strongest argument that this design is not over-reaching.

**The one genuinely new axis, and it must be resisted.** Nothing today lets STT come from one provider and TTS from another. Split routing is the single change that makes the surface combinatorial. **Do not ship it.** A provider is selected for the voice role as a unit. If a provider does only TTS, it is only selectable where the other half is already satisfied locally (§3.3) — a constraint the registry can express through `capabilities` and enforce, rather than a matrix the user must navigate.

**Secrets.** There is an existing mechanism and it must be reused, not reinvented: `core.ELEVENLABS_API_KEY` resolved from env then `settings.json` (`elevenlabs_tools.py:_api_key()`), mirroring `GEMINI_API_KEY` and `ANTHROPIC_API_KEY` at `core/__init__.py:981-1016`. Inworld gets `INWORLD_API_KEY` in the identical pattern. **No new secrets mechanism. No keys in this document or in source.** Note the prior record's warning is still live: an ElevenLabs API key *ID* is not an API key; real ones start with `sk_`, and `elevenlabs-voice.md` §1 records a whole section that could not be verified because of exactly that confusion.

**Demand.** Adding provider three must be a registry entry, four PRICING rows, and a capability declaration — not a code change in the fallback state machine. If the machine has to learn each provider's name, the design failed.

### 1.5 The exchange, where it was load-bearing

**P1 × P2, on the duplex option.** P1 wanted Inworld's Realtime API treated as a peer of Gemini Live. P2 objected that a full-duplex cloud path is *more* entangling than a TTS endpoint — it owns turn-taking, so the local path's VAD and barge-in logic risk becoming "the fallback implementation" and rotting. **Resolution:** Inworld Realtime API is in scope as a selectable duplex provider, but the local VAD/barge-in path stays the reference implementation and is exercised by the §6.4 offline CI gate every run. Cloud duplex may not own any code that Tier 1 depends on.

**P3 × P1, on which model to default within a provider.** P3 wanted Flash-class everywhere on cost. P1 pointed out `eleven_v3_conversational` measures 339 ms vs Flash's 185 ms but scores materially better WER (4.2% vs 6.6%) [openbenchmarks.com] — and that for a *chosen* cloud voice, the user chose it for quality, so defaulting them to the cheap model quietly delivers the thing they didn't ask for. **Resolution:** per-provider default is the low-latency model, the quality model is one visible control away, and the cost delta is shown *on that control* (§5.3) rather than discovered at month end.

**P4 × P3, on where cost appears.** P4 resisted a live per-interaction cost readout as chart-junk in a voice UI. P3 held that a metered API with no in-the-moment number is how you get a surprise bill. **Resolution:** the indicator carries provider name always; cost is a rolling session total, not a per-utterance flash, and the per-interaction figure is available on hover/expand. One persistent element, two levels of detail.

**All four, on the collision in §0.** No perspective argued for silently reversing (B). P1 and P3 independently reconstructed the *same* objections the 2026-08-19 record already made — which is corroboration that the record was right, and the reason §0.2 preserves both objections as live mechanisms rather than deleting them.

---

## 2. Provider capability synthesis

All figures gathered 2026-09-08. **Vendor-claimed** figures are marked; they exclude network and have not been independently reproduced.

### 2.1 Capability matrix

| | **ElevenLabs** | **Inworld** |
|---|---|---|
| **TTS** | Yes. `eleven_v3` (flagship), `eleven_v3_conversational` (realtime), `eleven_multilingual_v2`, `eleven_flash_v2_5` (low-latency) [docs/overview/models] | Yes. `inworld-tts-2`, `inworld-tts-2-flash`. TTS-1/1-Max **discontinued 2026-06-15** [docs.inworld.ai/tts/tts-models] |
| **STT** | Yes. `scribe_v2` (batch), `scribe_v2_realtime` (streaming) [docs] | Yes. `inworld-stt-1`, single family [inworld.ai/speech-to-text] |
| **Duplex / realtime agent** | Yes — Speech Engine (WS, you supply LLM) and ElevenAgents (hosted) [docs/overview/capabilities/speech-engine] | Yes — Realtime API, single WS, **drop-in OpenAI Realtime API compatible**, semantic VAD, barge-in [inworld.ai/realtime-api] |
| **TTS streaming** | HTTP chunked + WebSocket `/v1/text-to-speech/{voice_id}/stream-input`. **WS endpoint does not support `eleven_v3`** [docs/websockets] | HTTP stream `/tts/v1/voice:stream` + WebSocket; docs state WS is lowest-latency [docs.inworld.ai/tts/best-practices/latency] |
| **STT streaming** | Yes, WS, `scribe_v2_realtime`; browser-safe via single-use token [docs/speech-to-text/realtime] | Yes, `wss://api.inworld.ai/stt/v1/transcribe:streamBidirectional` [inworld.ai/speech-to-text] |
| **Languages** | TTS 29–70 by model; STT 90+ | TTS 200+; **STT 30+, realtime streaming only en/es/fr/de/it/pt** |
| **Regional endpoints** | Enterprise data residency: EU/India/Singapore [docs/data-residency] | `api.eu.inworld.ai`, `api.in.inworld.ai` [docs] |
| **Self-host / offline** | "Private deployments" referenced; **not verified** | **No open weights.** MIT repo is training code only. Terms §3 has a conditional "where permitted" customer-hosted clause. **Treat as cloud-only.** |

**The asymmetry that matters for §3:** both do TTS *and* STT, so neither is TTS-only. But Inworld's STT covers 30+ languages against its TTS's 200+, with realtime streaming in only six — so a user selecting Inworld for the voice role in, say, Polish gets TTS but not realtime STT. The registry must express this per-modality, per-language, or it will promise something it cannot deliver.

### 2.2 Latency — claimed vs measured

| Model | Vendor claim | Independently measured |
|---|---|---|
| `eleven_flash_v2_5` | ~75 ms model-side [elevenlabs docs] | **185 ms median / 230 ms p95 TTFA**, WER 6.6% [openbenchmarks.com, Coval, synced 2026-09-07] |
| `eleven_v3_conversational` | ~280 ms [elevenlabs docs] | **339 ms median / 433 ms p95**, WER 4.2% [same] |
| `scribe_v2_realtime` | ~150 ms [elevenlabs docs] | **None found.** |
| `inworld-tts-2-flash` | 20 ms p90 [docs] / 25 ms p99 [marketing] — *Inworld's own pages disagree* | **None found.** |
| `inworld-tts-2` | 100 ms p90 [docs] | **None found.** |
| `inworld-stt-1` | <100 ms [inworld.ai] | **None found** for latency. Accuracy: **2.4% WER, tied lowest** across 30+ models, Coval production benchmark [via inworld.ai/speech-to-text/accuracy, retrieved 2026-07-27] |
| Inworld Realtime API e2e | <1 s (STT 200 + LLM 400 + TTS <100) [inworld.ai/realtime-api] | **None found.** |

**Caveat on the one independent source.** Coval sells voice-agent eval infrastructure — independent of both vendors, but not disinterested. Methodology is Apache-2.0 at github.com/coval-ai/benchmarks; figures are rolling 7-day and drift daily. Quality/naturalness and price are explicitly not measured. Other 2026 "benchmark" pages surfaced in search were vendor-run or content marketing and were not relied on.

**Consequence for the spec:** ElevenLabs' measured TTFA is ~2.5× its published figure. Inworld's numbers have no independent check *at all* and its own two pages disagree by 5 ms and a percentile. Any latency claim shown in Friday's UI must be labelled by source (§5.3), and Inworld's must be labelled *vendor-claimed, unverified*.

### 2.3 Pricing

| | ElevenLabs | Inworld |
|---|---|---|
| **TTS unit** | per character | per character |
| **TTS rate** | $0.05/1K (Flash, v3-conversational) · $0.10/1K (v3, Multilingual v2). **Flat across all tiers** [elevenlabs.io/pricing/api] | $25/1M TTS-2, $15/1M Flash (On-Demand) → $12.50/$7 (Growth $1,500/mo) → sub-$5 enterprise [inworld.ai/pricing] |
| **STT unit / rate** | per hour. Scribe v2 $0.22/hr; realtime $0.39/hr | per hour. $0.15/hr On-Demand, $0.10/hr all paid tiers |
| **~200-char reply** | **$0.010** Flash · **$0.020** v3 | **$0.0050** TTS-2 on-demand · **$0.0030** Flash |
| **Free tier** | Yes — but **no commercial license**, attribution required | Yes — On-Demand, ~70 min TTS or 400 min STT, **commercial license included** |
| **Tiers change** | allotment + concurrency only | unit price *and* allotment |

**Friday-shaped worked example** (2 h/day speech ≈ 99,000 chars/day, per `elevenlabs-voice.md` §3's 825 chars/min):

| Provider / model | $/day | $/month |
|---|---|---|
| ElevenLabs Flash v2.5 | $4.95 | ~$149 |
| ElevenLabs v3 | $9.90 | ~$297 |
| Inworld TTS-2 (on-demand) | $2.48 | ~$74 |
| Inworld TTS-2 Flash (on-demand) | $1.49 | ~$45 |
| Inworld TTS-2 Flash (Growth) | $0.69 | ~$21 + $1,500 plan |
| **Gemini Live (today's Tier 3)** | on an existing meter | already paid |
| **Tier 1 Piper (today's default)** | **$0.00** | **$0.00** |

This table is the honest form of P3's argument and belongs in the settings UI in some reduced form, not only in this document. Note it also vindicates `elevenlabs-voice.md` §3 while showing Inworld shifts the numbers by ~3× — enough to change a decision, not enough to make sustained cloud speech free.

### 2.4 Licensing on generated audio — the section with real shipping risk

**ElevenLabs:**
- Free plan has **no commercial license** and requires attribution ("elevenlabs.io" or "11.ai" in the title) [help-center/legal].
- All paid plans include a commercial license, provided Beta Services are not used and you hold rights in the input. Audio generated *during* a paid subscription stays usable commercially **indefinitely**, including after cancellation.
- **Beta Services content "cannot be used for any commercial purpose or in any production environment."** No ElevenLabs page labels which current models are GA vs beta. **This is the single largest unresolved risk** — §10 Q2.
- **Voice clones cannot be exported or downloaded.** They live in the ElevenLabs account and are reachable only via API with your key [docs/voice-cloning]. A shipped custom Friday voice would be a **permanent hard dependency on ElevenLabs** — directly relevant to §10 Q1 and to C3.
- PVC may only be of your own voice; even with consent you cannot clone another person's. Creator tier or above.
- **Output ownership could not be verified** from a first-party source; the ToS page did not parse. Do not rely on third-party summaries.

**Inworld:**
- Terms §4: "**we assign to you all of our right, title, and interest in Outputs**" [inworld.ai/terms]. Cleaner than anything verifiable from ElevenLabs.
- Commercial license on **every** tier including free.
- **But Terms §13: "Upon termination, your license ends, and you must delete all Services, Models and Outputs."** That is in direct tension with §4's assignment. For shipped audio assets this is unresolved and material — §10 Q3.
- AUP: **mandatory AI disclosure** ("clearly and prominently disclose to users they are interacting with AI"); no training on Output; no impersonation without consent. The disclosure requirement happens to align with C2.
- Non-uniqueness disclaimer: another customer with similar input may generate the same output.

### 2.5 Privacy posture

Relevant because Friday's whole premise is local-first.

| | ElevenLabs | Inworld |
|---|---|---|
| **Default retention** | **Retained.** History on by default; deletion leaves debug/moderation logs, backups up to 30 days [docs/zero-retention-mode] | Retained; usage metadata kept for billing |
| **Zero retention** | ZRM, **Enterprise only, API-only**. Excludes Music, voice cloning, Dubbing, Studio | ZDR, workspace-level, **Growth-tier add-on**, Enterprise included. **Excludes voice-cloning audio samples and voice-design scripts** |
| **Training on your data** | **On by default**; opt-out toggle available to anyone, non-retroactive. Enterprise: no training by default | **No training on non-public Materials** by default [terms §4]. STT audio "never used for training" |
| **Biometric** | Generation traceable to the responsible user | Privacy notice: may derive "a digital model of speech characteristics," **retained up to 3 years** |
| **Compliance** | SOC 2, GDPR + DPA, BAA for qualifying healthcare | SOC 2 Type II, GDPR, HIPAA; DPA/SLA and EU/India residency **Enterprise-only** |

**Consequence:** on the default self-serve tiers both providers retain data, and ElevenLabs trains on it unless the user opts out. This is precisely the property that makes C1 load-bearing rather than decorative, and §4.3 must disclose it at selection time.

---

## 3. Where each provider fits

### 3.1 Both providers are TTS *and* STT — but not symmetrically

Neither is TTS-only, which was the assumption worth checking. Registration must therefore be per-modality:

| Provider | `capabilities` | Notes constraining registration |
|---|---|---|
| `elevenlabs` | `["asr", "tts"]` | ASR via `scribe_v2_realtime` (streaming) / `scribe_v2` (batch). TTS WS excludes `eleven_v3`. |
| `inworld` | `["asr", "tts"]` | ASR realtime limited to 6 languages vs TTS's 200+. Must be expressed per-language or the registry over-promises. |
| `inworld-realtime` | `["duplex"]` | **New capability.** Distinct entry, not a mode flag on `inworld` — a duplex session is a different lifecycle, and P4's "provider three is a registry entry" test only holds if capability differences are data. |

`duplex` is the one new capability keyword this design introduces. Gemini Live retroactively holds it too; that is a correctness improvement to the existing registry, not scope creep.

### 3.2 Tier placement

Cloud voice providers are **Tier-3 siblings**, not a new tier. Tier 3 stops meaning "Gemini Live" and starts meaning "cloud voice, provider-selected," with Gemini Live as its default provider. This is the smallest change that satisfies C1: Tiers 1 and 2 are untouched, and the fallback ladder in `voice-system-spec.md` §7.2 keeps its shape.

### 3.3 The split-modality rule (P4's constraint, enforced)

A provider is selected for the **voice role as a unit**. Friday does not offer "STT from A, TTS from B."

The one permitted asymmetry, because it *strengthens* C3 rather than weakening it: a cloud provider may serve **TTS only, with STT staying local**. That configuration is strictly better on all three constraints — audio never leaves the machine, only synthesis text does — and it maps onto the pipeline C3 describes ("understand voice input and emit text to a synthesizer fully offline"), where the synthesizer is the only swapped part.

It is therefore offered explicitly as **"Local listening, cloud voice"**, and it is the **recommended cloud configuration**. The reverse — cloud STT with local TTS — is *not* offered: it ships the user's raw microphone audio off-machine for the least benefit, which is the worst trade available.

---

## 4. Provider selection — how the choice is exposed

### 4.1 Selection is explicit, persisted, and never inferred

One control, in voice settings, listing every registered provider that declares `asr`/`tts`/`duplex` for `ROLE_VOICE`. Local providers appear in the same list as cloud ones — not a separate "cloud" section — because C1 says these are peers among which the user chooses, and separating them typographically implies a hierarchy the design does not have.

Default on a fresh install is unchanged: **Tier 1 local.** No cloud provider is ever pre-selected, and the absence of a key is not a prompt to add one.

### 4.2 A provider without a key is visible but unselectable

Shown, greyed, with the reason ("needs an API key") and a link to where keys are configured. This follows the existing `nvidia-nemo` pattern — registered and enabled so the UI surfaces it as a discoverable upgrade, availability gated by a readiness check, never gating Tier 1. It also avoids the failure the repo already wrote down in `2026-09-04-five-dead-settings.md`: a control that exists and does nothing.

### 4.3 Disclosure at the point of selection

Selecting a cloud provider shows, **before** confirmation, four things — this is where P1's cascade objection and P3's cost objection are enforced as UI rather than prose:

1. **Mode.** "Duplex" or "Synthesis-only." For synthesis-only, plainly: *this replaces one speech-to-speech hop with speech-to-text, model, text-to-speech — barge-in and model-native prosody are lost.* That sentence is `elevenlabs-voice.md` §2's argument, surfaced to the person making the trade instead of buried in a design doc.
2. **What leaves the machine.** For duplex/cloud-STT: raw microphone audio. For TTS-only: synthesis text. Named exactly.
3. **Retention and training.** Per §2.5, per provider, per tier where known. ElevenLabs' train-by-default and its opt-out toggle are stated, not linked.
4. **Cost.** Per-reply figure and the $/month at the user's own recent speech volume, computed from their actual `cost_meter` history rather than a generic table.

### 4.4 The indicator — C2's enforcement point

A persistent element in the voice UI naming **the provider that actually served the most recent interaction.**

Non-negotiable properties:
- It is written **after** the response is served, from the served path, never from configuration or intent. If Gemini Live was requested and Tier 1 served, it says Tier 1. C2's final clause is precisely this.
- It renders during degraded states, not only healthy ones.
- It carries the session's rolling cost for metered providers (P3 × P4 resolution: provider name always, cost as session total, per-interaction on expand).
- It is not dismissible while a cloud provider is active.

---

## 5. Per-provider configuration

### 5.1 Shape — reuse, do not invent

Each provider is one `provider_registry.py` entry in the existing shape (`local-voice-lite` is the template), differing only in `auth`:

```python
{
    "name": "elevenlabs-voice",
    "label": "ElevenLabs (cloud)",
    "type": "cloud-voice",
    "base_url": "https://api.elevenlabs.io/v1",
    "auth": {"type": "api_key"},          # vs {"type": "none"} for local
    "models": ["eleven_flash_v2_5", "eleven_v3_conversational",
               "scribe_v2_realtime"],
    "capabilities": ["asr", "tts"],
    "roles": [ROLE_VOICE],
    "model_meta": { ... },                 # label / short / roles / modalities
    "enabled": True,
}
```

No new configuration concept. That is the design's main claim to being maintainable, and P4's test for whether it succeeded.

### 5.2 Voice selection

Voice list is fetched from the provider and cached; it is **not** hardcoded, because both vendors' catalogues change. Cache is stale-tolerant and its refresh is **never** on a path Tier 1 can block on (§6.4). Selected `voice_id` is stored per provider, so switching providers and back does not lose the choice.

ElevenLabs `voice_id` is already a per-request parameter in `elevenlabs_tools.py` and there is no per-voice subscription charge — stock voices unlimited, custom/cloned capped by tier.

### 5.3 Model tier — latency vs quality, priced at the control

Per provider, two named options rather than a model dropdown:

| | ElevenLabs | Inworld |
|---|---|---|
| **Responsive** (default) | `eleven_flash_v2_5` — 185 ms median measured, WER 6.6%, $0.010/reply | `inworld-tts-2-flash` — 20 ms p90 *vendor-claimed, unverified*, $0.0030/reply |
| **Best quality** | `eleven_v3_conversational` — 339 ms median measured, WER 4.2%, $0.020/reply | `inworld-tts-2` — 100 ms p90 *vendor-claimed, unverified*, $0.0050/reply |

Every latency figure shown carries its provenance label. Measured figures cite the benchmark and its sync date; vendor claims say **vendor-claimed, unverified** — and for Inworld, that is every figure. This is the §2.2 finding made operational: Friday should not repeat a vendor's number as if it were a measurement, and the 2.5× gap on ElevenLabs is the concrete reason why.

Cost delta appears on the control itself (P3 × P1 resolution), so choosing quality is a visible purchase.

### 5.4 Secrets — the existing mechanism, unchanged

`INWORLD_API_KEY` added to `core/__init__.py` alongside `GEMINI_API_KEY`, `ELEVENLABS_API_KEY`, `ANTHROPIC_API_KEY` (`:981-1016`), resolved by the pattern in `elevenlabs_tools.py:_api_key()` — module constant, then env, then `settings.json`. Lazy import at call sites preserved so the module stays import-safe without network libs or a key.

**No keys in this document, in source, or in settings committed to the repo.** Retain and surface the existing ElevenLabs guard: an API key *ID* is not an API key; real keys begin `sk_`. `elevenlabs-voice.md` §1 is a whole section that could not be verified because of that exact confusion — the error message in `elevenlabs_tools.py:_NO_KEY` already says so and should be mirrored for Inworld if it has an analogous trap (**unverified** — §10 Q5).

---

## 6. Failure and fallback

### 6.1 The rule

**A cloud provider failing never silently promotes another provider.** `voice-system-spec.md` §7.3 already states the principle and this design does not weaken it:

> Every transition `PREFERRED(t) → ACTIVE(t')` with `t' != t` emits exactly one persistent, dismissible notice carrying: reason code, human message, remediation action. Transient `{type:'status'}` lines are **not** sufficient for a downgrade — a downgrade uses `{type:'error-nonfatal'}`.

Extended for cloud providers: the notice names **both** the requested and the serving provider, and the §4.4 indicator updates to the serving one in the same commit.

### 6.2 Cloud fails → local

Permitted and announced. Adding to `voice-system-spec.md` §7.2:

| Preference | Order attempted | Terminal |
|---|---|---|
| `cloud:<provider>` | provider → **(announce)** Tier 2 if ready → **(announce)** Tier 1 → text-only | text-only + classified error |

The user gets a working voice and is told it changed. This direction is safe: it moves *toward* the local path.

### 6.3 Local fails → cloud: **surfaces and offers, never substitutes**

This is C2's hard case and the one place this design refuses an obvious convenience.

If Tier 1 fails and a cloud provider is configured and reachable, Friday **does not use it**. It surfaces the failure with its remediation (per `voice-system-spec.md` §8's error taxonomy) and *offers* the cloud path as an explicit action the user takes. Declining leaves Friday in text-only.

This is deliberately worse ergonomics than auto-promotion. The reason: the local path failing is exactly the moment the user most needs to know their audio is about to leave the machine. Auto-promotion here would be the single most defensible-sounding C1 and C2 violation available, which is why it is named and refused rather than left to a future implementer's judgement.

The existing local-only override is unchanged and takes precedence over everything above: Airplane/Sovereign mode forces `model_routing.mode=local_only`, pins `voice_engine=local`, makes every cloud branch unreachable, and `_resolve_voice_engine` must return `engine != gemini` (D-AC3). Cloud voice providers join that unreachable set. **No new bypass.**

### 6.4 The offline gate — C3's enforcement, and the load-bearing artifact

P2's demand, adopted as the acceptance criterion this whole document answers to. Added to `voice-system-spec.md` §10's CI matrix:

**Test: `voice_offline_no_cloud`.** With the network black-holed and `GEMINI_API_KEY`, `ELEVENLABS_API_KEY` and `INWORLD_API_KEY` all absent from env and `settings.json`:

1. Friday boots.
2. The mic button is live, not greyed.
3. A full voice round trip completes on Tier 1 — audio in, transcription, model, synthesized audio out.
4. No cloud module is imported at boot; no socket is opened.
5. The §4.4 indicator reads Tier 1.
6. Wall-clock to first audio is within the Tier-1 budget with **no added timeout stalls** — the specific regression this catches is a cloud health check or voice-catalogue refresh (§5.2) blocking a local path on a DNS timeout.

Step 6 is the one that will actually fail one day. Steps 1–5 fail loudly; step 6 fails as "voice got slow," which is how C3 erodes without anyone deciding to erode it.

### 6.5 Metered-provider failures are their own class

Quota exhaustion, budget breach and auth failure are distinct from network failure and get distinct reason codes in the §8 taxonomy — a user out of credits needs a different action than a user with a dead connection. On budget breach (P3 × §1.3 resolution): cloud voice stops being **offered for new sessions**, the indicator says why, an in-flight session finishes.

---

## 7. Cost visibility

### 7.1 Extend `cost_meter.py`; build nothing new

The machinery exists: `record()`, `meter()`, `PRICING`, `UNPRICED_MODELS`, `get_budget()`/`set_budget()`, `_check_budget_alerts()`, `summary()`, `timeseries()`.

Critically, the per-character convention is **already established and documented in that file** — the `"in"`-per-1K slot reused as *USD per 1K characters*, call site passing `len(text)` as `input_tokens` and `0` as `output_tokens`, with the ElevenLabs rows verified against `elevenlabs.io/pricing/api` on 2026-09-04. Those rows (`eleven_flash_v2_5`, `eleven_multilingual_v2`, et al.) remain correct as of 2026-09-08.

Inworld needs four rows in the identical shape, converted from $/1M to $/1K characters:

```python
"inworld-tts-2":        {"in": 0.025, "out": 0.0},   # $25/1M chars, On-Demand
"inworld-tts-2-flash":  {"in": 0.015, "out": 0.0},   # $15/1M chars, On-Demand
```

**Two honesty constraints inherited from that file's own invariant:**

- Rates confirmed only against a provider's own current pricing page. Inworld's were. **Artificial Analysis lists Inworld at $20.8/1M and $10.4/1M, which does not match** — unresolved, §10 Q4. Where a rate cannot be confirmed it goes in `UNPRICED_MODELS` and stores `cost_usd` as NULL, rendering as "not priced," not as a number that looks like a fact.
- Inworld's price is **tier-dependent** (On-Demand → Growth → Enterprise), unlike ElevenLabs' flat rate. A single PRICING row is therefore wrong for any user not on On-Demand, and will silently over-report. Either the row is annotated as the on-demand ceiling, or plan tier becomes a configured input. §10 Q4.

### 7.2 STT meters on a different axis

Both providers bill STT per **hour of audio**, not per character. `record()` already accepts `duration_ms`, so the plumbing exists, but the PRICING table's per-1K-unit convention does not express $/hour. Either a `kind="stt"` branch in `cost_for()` or separate hourly rows are needed. This is the one place the existing table's shape genuinely does not stretch, and it should be resolved deliberately rather than by overloading the character slot a second time.

### 7.3 What the user sees

Three levels, matching the P3 × P4 resolution:

1. **In the moment** — session rolling cost on the §4.4 indicator, cloud providers only. Local reads $0.00, which is the comparison that does the persuading.
2. **On expand** — per-interaction cost, provider, model, characters or audio-seconds.
3. **Over time** — existing `summary()`/`timeseries()` surfaces, with voice attributable as its own kind.

Budgets reuse `get_budget()`/`set_budget()` and `_check_budget_alerts()`. No separate voice budget system.

---

## 8. Interaction with the existing local voice implementation

### 8.1 What does not change

Tiers 1 and 2 are untouched. `services/local_voice.py`, `services/nemo_voice.py`, the `/ws/voice-local` route, `faster-whisper` + Piper, the `.[voice-local-lite]` extra, model download paths under `~/.friday/local_voice/`, and `provider_health` readiness — none of it is modified by this design. The Tier-1 default binding stays `{"provider": "local-voice-lite", "model": "piper-en_US-amy-medium"}` (`core/__init__.py:1655`).

If this document's implementation touches any of the above beyond registry registration, that is a signal the design drifted.

### 8.2 What changes, minimally

- `provider_registry.py` — three new entries (§5.1), plus `duplex` as a capability keyword, plus Gemini Live gaining it retroactively.
- `core/__init__.py` — `INWORLD_API_KEY` in the existing block.
- `cost_meter.py` — Inworld PRICING rows; an STT hourly path (§7.2).
- `voice-system-spec.md` §2 / §7.2 / §10 — tier matrix column, fallback rows, offline CI gate.
- Voice settings UI — provider selector (§4.1), disclosure (§4.3), indicator (§4.4).

### 8.3 The egress gate hole, still open and now more urgent

`elevenlabs-voice.md` §4.6 records a real defect in shipped code:

> `creative_engine.py:682` routes prompts through `egress_gate.gate_text()` before any cloud call. **`speak_text` as built does not.** Flagged gap: before subagent speech ships, synthesis input must pass the gate with `"elevenlabs"` as the provider, or a subagent could speak a vault-sensitive string straight to a vendor. This is a real hole in the tool delivered on 2026-08-19, not a hypothetical.

**Correction, 2026-09-09.** The quoted text above is **stale**. `speak_text` *does* gate: `services/elevenlabs_tools.py:162` calls `egress_gate._gate_text(text, "elevenlabs", "speak_text.text")` before the network call and before its own `FRIDAY_TESTING` short-circuit, with a proof test at `tests/unit/test_elevenlabs_egress.py`. The hole named here was closed; this section had not caught up. The *requirement* below stands unchanged and is now met for the conversational path too - `cloud_voice.gate_synthesis_input()` gates every cloud synthesis call, refuses partial redactions rather than speaking the remainder, and fails closed when the gate itself is unreachable.

That gap was scoped to subagent speech. **This design widens its blast radius**: routing Friday's *conversational* output through a cloud TTS provider means every spoken sentence is synthesis input. Ungated, that is Friday's entire conversation crossing to a vendor without passing the check every other cloud call passes.

**Therefore: egress gating of synthesis input is a blocking prerequisite for cloud TTS in the conversational path, not a follow-up.** It was already item 3 of the prior record's non-negotiable build order. It stays there, and its priority rises.

`voice-system-spec.md` §9.3's honest limitation extends unchanged: for duplex cloud providers, raw audio is ungated in the same way Tier 3 raw audio already is. This must be surfaced (§4.3 item 2), not solved — the gate reasons about text.

---

## 9. What this document does not specify

- Any implementation. No code is written.
- Subagent voices — `elevenlabs-voice.md` §4 owns that and its decision (opt-in over default relay) is untouched.
- Produced audio (narration/voiceover) — decision (A), shipped, out of scope.
- Split-modality routing — refused in §3.3, not deferred.
- Cloud STT with local TTS — refused in §3.3.
- Voice cloning as an implementation. §10 Q1 asks whether it is a product goal; until answered, it is not designed.

---

## 10.0 Decisions taken 2026-09-09, and what shipped

Implementation status changed from `none` to **partial (TTS only)** on this date. What follows is what was decided and what the code now does. Each remains overridable by a later decision record, but the code implements it as stated.

**Q1 - voice cloning: NO.** There is no cloned Friday voice on ElevenLabs and no cloning flow was built. ElevenLabs clones cannot be exported or downloaded and are reachable only through their API with your key, which would make Friday's *identity* a permanent vendor dependency - in direct conflict with C3 and with the standing offline-voice commitment. **Cloud voices are selectable alternates; they are never Friday's identity.** `cloud_voice.PROVIDERS[*]["cloning"]` is `False` for both providers and the disclosure surface says so in plain words.

**Q2 - ElevenLabs model tiers: ship only what is documented as GA.** Confirmed 2026-09-09 that the premise holds - `elevenlabs.io/docs/overview/models` carries no GA/Beta column, and the Terms of Service reference a separate Beta Services Addendum without enumerating members. Since Beta Services "cannot be used for any commercial purpose or in any production environment," a model whose status cannot be determined is **excluded**. Admission requires *positive* first-party commercial-commitment evidence, never documentation tone: (a) a published per-character API price, (b) a dedicated per-plan concurrency column, (c) designation as the replacement for a formally deprecated model.

| Model | Shipped | Evidence |
|---|---|---|
| `eleven_flash_v2_5` | **yes** | (a) + (b) "Flash" concurrency column + (c) named replacement for deprecated `eleven_turbo_v2_5`; documented as powering the Agents Platform |
| `eleven_multilingual_v2` | **yes** | (a) + (b) "Multilingual v2" concurrency column |
| `eleven_v3`, `eleven_v3_conversational` | **no** | No concurrency column, no deprecation-replacement role, no GA marker. §5.3 named `eleven_v3_conversational` as the "Best quality" option; it is withheld until ElevenLabs confirms in writing |
| `eleven_turbo_v2_5`, `eleven_turbo_v2`, `scribe_v1` | no | formally deprecated |
| `eleven_multilingual_v3` | no | **not a real model id** - priced in `cost_meter.PRICING` but absent from ElevenLabs' model table. A live defect, recorded here |
| `scribe_v2`, `scribe_v2_realtime` | no | STT, refused by §3.3 |

Consequence: **ElevenLabs ships with one quality tier, not the two §5.3 assumed.** Q2 still needs an answer from ElevenLabs directly before that changes.

**Q3 - Inworld's contradictory terms: treat Inworld audio as NON-DURABLE.** §4 assigns Outputs to the user; §13 requires deleting all Outputs on termination; both cannot hold for shipped audio. Inworld audio is therefore fine for interactive playback and is **never** written into archives, saved artifacts, or anything shipped. `cloud_voice.audio_is_durable("inworld")` returns `False`, the synthesis response carries `X-Friday-Voice-Durable: 0`, and `legal_review_required()` returns the unresolved question as prose. **This is flagged for legal review before commercial release, not resolved in code** - and deliberately so; a code path is not an answer to a contract question.

**Q4 - Inworld pricing: plan tier is a configured input.** The PRICING rows are the **On-Demand ceiling**. `inworld_plan_tier` (`on_demand` | `growth` | `enterprise`) is a real setting read by `cloud_voice.meter_model_id()`: any tier but on-demand meters under `<model>:<tier>`, which is in `UNPRICED_MODELS` and stores SQL NULL, rendering as "not priced" rather than over-reporting. Artificial Analysis' disagreeing figures are not used - `cost_meter.py`'s own invariant says the vendor's current pricing page wins and third-party aggregators are not acceptable sources.

**Q5 (of `local-voice-repair-and-native-audio.md` §4.5) - `auto` means LOCAL ONLY.** Labelled "Automatic (local only)". It never reaches cloud. This follows directly from the no-silent-fallback rule: a mode that can silently send a user's voice to a third party is the exact failure the transparency commitment exists to prevent. The contradicting parenthetical in `voice-system-spec.md` §7.2 has been deleted; no test asserts `auto` may reach cloud.

**Still open and NOT decided here:** Q5 (Inworld key-id trap - unverified, so no Inworld key-format claim is made anywhere in the code), Q6 (no independent Inworld latency benchmark - every Inworld figure is rendered with a "vendor-claimed, unverified" provenance label), Q7 (ElevenLabs private deployment), Q8 (Inworld TTS-2 GA vs research preview - carried as data in `ga_evidence`, surfaced by `legal_review_required()`), Q9 (which account tier is actually held).

**What shipped:** TTS only. STT stays local - §3.3 refuses cloud-STT-with-local-TTS, and "Local listening, cloud voice" is the recommended and only cloud configuration. §7.2's STT-per-hour metering axis is therefore **not implemented and not needed**; it stays open for whenever cloud STT is reconsidered. Duplex is not implemented.

---
## 10.0b Decisions taken 2026-09-09 (second pass)

**Q8 - Inworld's GA status: THE SAME STANDARD APPLIES, and Inworld ships nothing.**

Q2's rule is "where GA cannot be determined, exclude." Applying it to ElevenLabs but not to Inworld would make it a preference rather than a standard - one standard for both vendors, or neither means anything. Inworld's status cannot be determined: its product docs present TTS-2 as production while its **own** comparison material and Artificial Analysis label it "Research Preview." A vendor contradicting itself is worse evidence than silence, because there is a first-party statement on each side.

Inworld is therefore **registered, visible, and unselectable**, with the real reason shown - not a misleading "needs a key," which would send a user with a working key to fix the wrong thing. `cloud_voice.ga_established("inworld")` is `False` and gates three points: `resolve_provider()` (so a persisted selection from another build cannot win), `available_providers()` (the selection surface), and `synthesize()` (before any network work).

**The integration stays.** Enabling Inworld later is a configuration change - flip `GA_ESTABLISHED["inworld"]` - not a rebuild. The HTTP client, egress gating, PRICING rows, plan-tier handling, disclosure, and non-durability enforcement are all built and tested. `TestTheIntegrationSurvives` fails if someone deletes them as dead code.

**What would change the answer**, any one of, in writing from Inworld: (1) a first-party, dated statement that TTS-2 is generally available and not a research preview; (2) removal of the "Research Preview" label from Inworld's own comparison material; (3) an explicit statement that research-preview status does not restrict commercial or production use. **These resolve Q8 only.** Q3 (the §4/§13 Outputs contradiction) is a separate blocker: resolving Q8 would let Inworld be *selected* and would **not** make its audio durable. A test pins that the two cannot be conflated.

Net position: **one honest provider ships.** ElevenLabs, two models. That is preferred over two providers where one carries an unresolved commercial-use question papered over because the feature was wanted.

**`local` terminates - it never falls through to cloud.** The more important half of the `auto` fix. Previously, a user who selected the mode named "local" with Tier-1 deps missing and a Gemini key present had their microphone audio and spoken replies streamed to Gemini Live, silently; the only guard was `model_routing.mode == 'local_only'`, so protection required saying "local" twice in two places and the word the user actually chose bought them nothing. A user who picks "local" and receives a cloud provider has been lied to by the word itself. `local`, the default, and any unrecognised value now terminate at text-only with a message naming the problem, the remediation, and the fact that the cloud path exists and is theirs to choose. **This is a deliberate behaviour change for existing users**; the prior behaviour was the bug. An explicit `gemini` selection is still honoured - this removes a silent hop, not the user's ability to choose.

**Q3 tightened - non-durable now means not cached, not merely not archived.** "Friday will not archive it" and "it is not cached anywhere" are different promises and only the second is what non-durable should mean. Non-durable responses carry `Cache-Control: no-store, no-cache, must-revalidate, max-age=0` plus `Pragma`/`Expires`, targeted rather than blanket so the restriction means something. The client half is `static/cloud_voice_playback.js` (ephemeral object URLs revoked on end/error, no Cache API / IndexedDB / download, an `assertMayPersist()` that throws). **It is not yet wired**: both `/api/voice/tts` call sites live in `ui_parts/app.html` and `index.html`, which were under concurrent edit. Wiring is one script tag and one call swap.

**`eleven_multilingual_v3` price row removed.** It was priced in `cost_meter.PRICING` but is not a real ElevenLabs model id (verified against their model table 2026-09-09). A rate for a model that cannot be called is a small lie of the same family as an unverified rate.

**Dead-settings tally now 8.** `docs/decisions/2026-09-04-five-dead-settings.md` has a dated addendum recording `elevenlabs_api_key`, `elevenlabs_model` and `elevenlabs_voice_id`, plus the generalisable two-line audit for finding number nine.

---

## 10. Open questions the spec could not resolve

**Q1 — Is a recognisably owned Friday voice a product goal?** `elevenlabs-voice.md` §5/§6 names this as the *only* argument that reopens (B) on its merits. It is unanswered here because it is a product decision, not a technical one. It carries a hard consequence: ElevenLabs voice clones **cannot be exported or downloaded** and are reachable only via API with your key [docs/voice-cloning]. A cloned Friday voice is therefore a permanent vendor dependency for Friday's *identity* — which sits in direct tension with C3. If the answer is yes, that tension needs its own decision record.

**Q2 — Which ElevenLabs models are GA and which are Beta Services?** No first-party page labels this. Beta Services content "cannot be used for any commercial purpose or in any production environment." If any model this spec names is a Beta Service, shipping it is a licensing violation. **Blocking for any commercial release.** Resolve directly with ElevenLabs; do not infer from documentation tone.

**Q3 — Does Inworld Terms §13 apply to already-generated audio?** §4 assigns Outputs to you; §13 says on termination "you must delete all Services, Models and Outputs." For a product shipping generated audio these cannot both hold. Get written clarification before shipping any baked Inworld audio.

**Q4 — Which Inworld price applies, and why does Artificial Analysis disagree?** Inworld's rate is tier-dependent; AA lists $20.8/1M and $10.4/1M against Inworld's own $25/1M and $15/1M on-demand. A single PRICING row will misreport for anyone not on On-Demand. Decide: annotate the row as the on-demand ceiling, or make plan tier a configured input.

**Q5 — Does Inworld have an ElevenLabs-style key-ID trap?** ElevenLabs' key-vs-key-ID confusion cost `elevenlabs-voice.md` an entire unverifiable section. Inworld's docs show `Authorization: Basic $INWORLD_API_KEY`. Whether a similar ID/secret distinction exists is **unverified** and should be checked before writing the error message in §5.4.

**Q6 — Is there any independent latency benchmark for Inworld?** None found. Every Inworld latency figure in §2.2 is vendor-claimed, and Inworld's own two pages disagree (20 ms p90 vs 25 ms p99). Until an independent measurement exists, Inworld's numbers cannot be compared like-for-like with ElevenLabs' measured 185 ms. §5.3's provenance labels are a mitigation, not a fix.

**Q7 — What does an ElevenLabs "private deployment" actually offer?** Referenced from the Speech Engine docs but not fetched. If it permits genuinely local execution it changes this document's relationship to C3 substantially — it would be the only path by which a cloud-quality voice stops being a network dependency. Worth reading before Q1 is answered.

**Q8 (ANSWERED 2026-09-09 — see §10.0b: cannot be determined, so Inworld ships nothing) — Is Inworld TTS-2 GA or research preview?** Inworld's docs present it as production; Inworld's own comparison article and Artificial Analysis both label it "Research Preview." Unresolved, and it bears on whether it can be a selectable provider in a shipped build.

**Q9 — Which account tier is actually held?** `elevenlabs-voice.md` §1 could not read the ElevenLabs account (key ID, not key). Every plan-dependent figure in §2.3 and §7.1 is published-rate inference, not this account. One call settles it: `curl -H "xi-api-key: sk_..." https://api.elevenlabs.io/v1/user/subscription`. The Inworld equivalent is unverified (Q5). Until both are read, the cost tables are estimates.

---

## 11. Companion edits required

Specified, not applied — these touch two live documents and one is a decision record.

**11.1 `voice-system-spec.md` §2 Tier Matrix.** The Tier-3 column head changes from "Tier 3 — Gemini Live" to "Tier 3 — Cloud Voice (provider-selected)". Rows gain: **ASR** "Native (Gemini Live, Inworld Realtime) or provider ASR (Scribe v2, Inworld STT-1)"; **TTS** "Native or provider TTS"; **Egress** unchanged, with a pointer to §8.3 above; and a new **Provider** row pointing here.

**11.2 `voice-system-spec.md` §7.2.** Add the `cloud:<provider>` row from §6.2. Add the §6.3 local-fails-does-not-auto-promote rule as an explicit clause under §7.3, since it is the one case where the existing text's "announce the transition" is insufficient — the transition must not happen at all without consent.

**11.3 `voice-system-spec.md` §10.2 CI matrix.** Add `voice_offline_no_cloud` (§6.4).

**11.4 `elevenlabs-voice.md` header.** `Supersedes / superseded by:` changes from `—` to: `partially superseded by cloud-voice-providers.md (2026-09-08) — decision (B) amended: Gemini Live remains the default, cloud providers added as user-selected siblings. (A) and (C) unchanged.`

**11.5 `elevenlabs-voice.md` §6.** Append a dated amendment note beneath decision (B). **Do not edit the decision text.** Suggested:

> **Amendment 2026-09-08.** (B)'s conclusion stands: Gemini Live native audio remains Friday's *default* speaking voice, and §2's cascade argument is unrefuted. What changed is scope — `cloud-voice-providers.md` adds ElevenLabs and Inworld as *user-selected* Tier-3 siblings alongside it, never as a default. §2's architectural objection is preserved there as a selection-time disclosure (§4.3) and §3's cost objection as live metering (§7). The cloned-voice question in §5 remains the only thing that reopens (B) *as a default*, and is now tracked as Q1.

**11.6 `voice-system-overhaul-spec.md`.** No content change. Its `historical` status is correct. Its misleading recency — last content edit 2026-08-31, later than the "active" spec's 2026-07-06 — is recorded in §0.3 here rather than by relabelling a file whose role is accurate.

---

## 12. Sources

All retrieved 2026-09-08 unless noted.

**ElevenLabs**
- Models, latency claims, concurrency: https://elevenlabs.io/docs/overview/models
- API pricing (rates, tiers, metering units): https://elevenlabs.io/pricing/api
- TTS WebSocket: https://elevenlabs.io/docs/eleven-api/guides/how-to/websockets
- TTS HTTP streaming: https://elevenlabs.io/docs/eleven-api/guides/how-to/text-to-speech/streaming
- Realtime STT: https://elevenlabs.io/docs/eleven-api/guides/how-to/speech-to-text/realtime
- Speech Engine / ElevenAgents: https://elevenlabs.io/docs/overview/capabilities/speech-engine
- Zero Retention Mode: https://elevenlabs.io/docs/eleven-api/resources/zero-retention-mode
- Data residency: https://elevenlabs.io/docs/overview/administration/data-residency
- Commercial use / attribution / Beta Services: https://elevenlabs.io/docs/help-center/legal/can-i-publish-the-content-i-generate-on-the-platform
- Training opt-out: https://elevenlabs.io/docs/help-center/legal/is-my-data-used-to-improve-eleven-labs-ai-models
- Voice cloning (tiers, non-exportability): https://elevenlabs.io/docs/eleven-creative/voices/voice-cloning

**Inworld**
- Pricing, tiers, free tier, credit mechanics: https://inworld.ai/pricing
- TTS models, latency claims, deprecations: https://docs.inworld.ai/tts/tts-models
- TTS latency best practices, regional endpoints: https://docs.inworld.ai/tts/best-practices/latency
- STT capabilities, streaming protocol: https://inworld.ai/speech-to-text
- STT accuracy (Coval, retrieved by Inworld 2026-07-27): https://inworld.ai/speech-to-text/accuracy
- Realtime API (duplex, OpenAI compatibility, transports): https://inworld.ai/realtime-api
- Terms — Output assignment §4, customer-hosted §3, termination §13: https://inworld.ai/terms
- AUP — AI disclosure, training restrictions: https://inworld.ai/aup
- Privacy — biometric retention: https://inworld.ai/privacy
- Zero Data Retention coverage/exclusions: https://docs.inworld.ai/portal/zero-data-retention
- Security/compliance: https://inworld.ai/security
- Open-source TTS repo (training code only, MIT): https://github.com/inworld-ai/tts
- Voice cloning: https://docs.inworld.ai/tts/best-practices/voice-cloning
- Self-described comparison (research-preview labels, on-prem FAQ): https://inworld.ai/resources/best-tts-api-2026

**Independent / third-party**
- Coval TTS benchmark, mirrored, synced 2026-09-07 — measured TTFA and WER: https://openbenchmarks.com/text-to-speech-benchmark-by-coval/elevenlabs-vs-cartesia
- Coval benchmark methodology (Apache-2.0): https://github.com/coval-ai/benchmarks
- Artificial Analysis TTS leaderboard — quality Elo, conflicting Inworld price: https://artificialanalysis.ai/text-to-speech/leaderboard/provider-voice

**Repository (read 2026-09-08, `main` @ `f7df1d1`)**
- `docs/design/active/voice-system-spec.md` — §2 tier matrix, §7 fallback, §9 egress, §10 CI
- `docs/design/active/elevenlabs-voice.md` — §§1–6, decision record
- `docs/design/historical/voice-system-overhaul-spec.md` — §12.5 latency, §12.7 egress
- `src/agent_friday/services/provider_registry.py` — `ROLE_VOICE`, provider entry shape
- `src/agent_friday/services/cost_meter.py` — `PRICING`, `UNPRICED_MODELS`, `record()`, budgets
- `src/agent_friday/services/elevenlabs_tools.py` — `_api_key()`, `_NO_KEY`, lazy-import discipline
- `src/agent_friday/core/__init__.py:981-1016, 1655` — key constants, Tier-1 default binding
