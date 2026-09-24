# Local Voice — Repairing Tier 1/2 and adding a native-audio second mode

> **Status:** active
> **Last verified:** 2026-09-08
> **Implementation:** none for the native-audio path; the repair targets `services/local_voice.py`, `services/nemo_voice.py`, `routes/voice.py`, `services/provider_registry.py`, `paths.py`
> **Supersedes / superseded by:** extends [`voice-system-spec.md`](voice-system-spec.md) §2, §7, §8, §10; sibling to [`cloud-voice-providers.md`](cloud-voice-providers.md) (shares its constraints C1–C3 and its vocabulary)
> **Written:** 2026-09-08
> **Method:** STORM — multi-perspective interrogation, simulated exchange, cited synthesis

## Implementation notes

- **Nothing here is built.** This is a design document. No code was written, no test was run, and no model was loaded while writing it.
- **A model training run held the GPU throughout authoring** (`train/train.py` under Friday-Models, ~12 GB VRAM, one CPU core saturated). Every finding below comes from reading files. No figure in §5 was measured on this machine; the ones that are measurements are other people's, cited as such. §5.6 makes the contention rule permanent rather than incidental.
- **Feasibility and diagnosis were done before this document was written.** Those conclusions are carried here as restated, tagged **INHERITED** (§1.2), and are *not* independently re-verified. Anything tagged **VERIFIED** was read from this tree today.
- This document does **not** reopen whether native audio replaces speech-to-text. That is settled (§0.3) and specified as settled. The perspectives in §2 argue about *how the two modes coexist and when each is selected*; none of them gets to relitigate that both exist.

**Evidence registers**, extending the provenance discipline `cloud-voice-providers.md` §5.3 applies to vendor figures rather than replacing it: **VERIFIED** (read from this tree or a primary source today) · **INHERITED** (established by earlier feasibility work, restated, not re-verified here). Only these two are used; a claim carrying neither is an argument, not a fact.

---

**One-line thesis:** *Friday's local voice does not work, and the reason it cannot be diagnosed is that it has no log — so instrumentation is step zero, repair is step one, and native audio arrives afterwards as a **second, user-chosen listening mode** whose value is understanding, never accuracy.*

---

## 0. What this document does

### 0.1 Scope

Two bodies of work, in this order:

1. **The repair (§4).** Tier-1 CPU voice has never worked on this machine, Tier-2 GPU voice degrades onto that broken Tier-1 path, and neither emits a diagnosable log. This is not a new feature; it is the precondition for every other voice claim in the repo being true.
2. **The native-audio path (§5).** Gemma 4 native audio-in, served by `llama-server`, as a **second listening mode alongside** speech-to-text — not a replacement for it.

### 0.2 Relationship to the sibling documents

[`cloud-voice-providers.md`](cloud-voice-providers.md) was written the same day and covers Tier 3. This document covers Tiers 1 and 2 and the new local mode. They must not contradict each other, so this document **adopts its vocabulary rather than inventing a parallel set**:

| Term | Defined in | Used here |
|---|---|---|
| **C1 / C2 / C3** (the three constraints) | `cloud-voice-providers.md` §1.0 | Quoted verbatim in §0.4, unmodified |
| **Surfaces and offers** (vs. substitutes) | ibid. §6.3 | The governing rule for *both* new selection points here (§4.5, §5.5) |
| **The indicator** — persistent, names what *actually served* | ibid. §4.4 | Extended to carry listening mode, not duplicated |
| **Disclosure at the point of selection** | ibid. §4.3 | The pattern reused for native-audio mode selection (§5.5) |
| **`duplex`** as a registry capability keyword — described there as "the one new capability keyword this design introduces" | ibid. §3.1 | **Extended, not adopted:** a second keyword `audio-in` is added here (§5.4), by the same mechanism. That sentence in the cloud spec should be read as scoped to that document |
| **Provider is selected for the voice role as a unit**, with one named exception — cloud TTS over local STT ("Local listening, cloud voice") | ibid. §3.3 | Upheld **including its exception**. Native audio is a *mode*, not a second provider (§5.4), and it composes with that exception (§5.8) |
| **`voice_offline_no_cloud`** CI gate | ibid. §6.4 | **Reused, not duplicated.** §7.2 adds cases to it |
| **Reason code / user message / action** triple | `voice-system-spec.md` §8 | New codes follow the existing naming (§6) |
| **`PREFERRED(t) → ACTIVE(t')`** and the one-notice rule | ibid. §7.1, §7.3 | Extended to mode transitions (§6.2) |

Where the two documents touch the same surface — tier selection, provider disclosure, failure taxonomy — this one is the local-side extension and the cloud document remains authoritative for anything Tier-3.

### 0.3 The resolved design decision, stated so an override can be informed

**Native audio-in is a second mode alongside speech-to-text, not a replacement for it.**

The reasoning, in one paragraph, because a maintainer who overrides this should be overriding an argument rather than a preference: Gemma 4 native audio is **worse at transcription than the pipeline Friday already has** — 13.15% word error rate against faster-whisper's 11.48%, degrading to roughly 41% against 16% on noisy audio (**INHERITED**). Dictation is an accuracy task. Replacing an 11.48% path with a 13.15% path makes dictation worse for every user, in exchange for a capability dictation does not use. What native audio actually buys is **audio-to-reasoning in a single pass** — prosody, tone, hesitation, emphasis, speaker intent: everything that is destroyed the moment speech becomes a string. That is a different job, and it deserves a different mode rather than a silent substitution inside the same one.

**The owner may override this.** If they do, the thing being traded away is dictation accuracy across the board, and the thing bought is that every voice turn carries prosody. §11 Q1 puts the question rather than assuming the answer.

### 0.4 The non-negotiable constraints (verbatim from `cloud-voice-providers.md` §1.0)

- **C1 — Both paths, always.** Local-private and cloud-frontier voice both remain available. The *user* chooses. Cloud providers are additions to the local path, never replacements. No default silently routes to cloud.
- **C2 — Transparency.** The user always knows which provider served a given interaction. No silent fallback from local to cloud. If the local path fails, that **surfaces and offers**; it does not substitute. Any provider indicator reflects what *actually served* the request, not what was requested.
- **C3 — Offline capability is the point.** Friday understands voice input and emits text to a synthesizer fully offline. That capability is why Gemma 4 E2B was chosen as the base model. Cloud providers are for quality and variety when the user opts in; **the offline path must never become load-bearing on a network call.**

Two consequences specific to this document:

- C2's "surfaces and offers, never substitutes" applies **within the local path too**, not only at the local/cloud boundary. Tier 2 → Tier 1 is already announced (`gpu_degraded`); §5.5 extends the same rule to listening-mode changes.
- C3 names Gemma 4 E2B by reason. §5 is the first work that actually cashes that reason in — until now E2B is Friday's brain, not her ears.

---

## 1. What is established, and how firmly

### 1.1 Read from this tree today — **VERIFIED**

| # | Finding | Evidence |
|---|---|---|
| V1 | Tier-1 has **two** asset roots, not one. `LOCAL_VOICE_DIR = friday_home() / "local_voice"` with `WHISPER_DIR`/`PIPER_DIR` beneath it is the default; but `WhisperASR._download_root()` and `PiperTTS._voice_path()` **prefer `voice_assets_dir()`** when OS mode is set. A configurable override therefore already exists and is undocumented | `local_voice.py:43, 71-73, 343-358, 413-430`; `paths.py` `voice_assets_dir()` |
| V2 | **`~/.friday/local_voice/` does not exist on this machine.** With OS mode off — which it is here — no other root is consulted, so `models_ready()` returns False and `/api/voice/session-info` refuses the local engine | `local_voice.py:698-708`; directory listing |
| V3 | The provisioned assets sit at `~/.friday/runtime/{whisper-models,piper-voices,venv-voice}` — 1.51 GB, 0.06 GB, 1.25 GB | provisioning audit; directory listing |
| V4 | **No line in `src/` reads `~/.friday/runtime/piper-voices` or `~/.friday/runtime/whisper-models`.** `runtime/` is referenced only by the model/LLM subsystems (`core/__init__.py:711-722`, `paths.py:127-162`, `model_store.py`, `residency_arbiter.py`, …) — never by voice | recursive `Select-String` over `src/` |
| V5 | The repo's own provisioning audit says so plainly: "**Nothing was wired into Friday.** This mission provisioned and proved an environment; it did not touch routing, `capability_routing`, or any repo source." | provisioning audit |
| V6 | **Even repointing the paths would not work.** The provisioned Piper voice is `en_US-lessac-medium`; the default is `DEFAULT_PIPER_VOICE = "en_US-amy-medium"`. The provisioned ASR model is `faster-whisper-large-v3-turbo` in **Hugging Face hub layout** (`models--…/blobs/snapshots/refs`); the default is `DEFAULT_WHISPER_MODEL = "small"` at a **flat `download_root`** | `local_voice.py`; directory listing |
| V7 | `local_voice.py` and `nemo_voice.py` contain **no logging import, no logger, no handler.** Their entire diagnostic output is two bare `print()` calls | `local_voice.py:751`, `:764` |
| V8 | Those prints land in `~/.friday/server_stderr.log` — **135,976 KB, unrotated** — while every other subsystem writes to `friday.log` with `RotatingFileHandler(maxBytes=10MB, backupCount=3)` | `core/__init__.py:857-877`; directory listing |
| V9 | The one voice-specific file logger (`voice_debug.log`, `routes/voice.py:1658-1670`) is opt-in behind `FRIDAY_VOICE_DEBUG`, unrotated, and is called **only from the `/ws/live` cloud handler**. `/ws/voice-local` never calls it | `routes/voice.py:1658-1670` |
| V10 | Tier 2 degrades to Tier 1 at three distinct points: `resolve_tier` (pre-flight), `ensure_ready:725-728` (pre-import gate), `ensure_ready:749-767` (post-load-failure retry) | `local_voice.py` |
| V11 | `~/.friday/models/nemo/` exists but contains **no `.nemo` or `.ckpt` files**, so `nemo_models_ready()` is False and Tier 2 degrades on every attempt | directory listing; `nemo_voice.py` |
| V12 | `session-info` already requires three things, not two — ASR, TTS, **and** a resident local brain seat: "refusing the local engine rather than starting a session that cannot answer" | `routes/voice.py:916-921` |
| V16 | The Tier-1 default role bindings are `"asr": {"provider": "local-voice-lite", "model": "whisper-small"}` and `"tts": {"provider": "local-voice-lite", "model": "piper-en_US-amy-medium"}` — at **`core/__init__.py:1992-1993`**. `cloud-voice-providers.md` §8.1 cites `:1655` for this; **that citation is wrong** (line 1655 is an unrelated comment) and should be corrected when its companion edits are applied | `core/__init__.py:1992-1993` |
| V17 | The `nemo_voice.py` contention probe is **deliberately non-gating**: "Never raises and **never blocks the tier** … The point is to make the trade visible, not to make it for the user." | `nemo_voice.py:226-231` |
| V13 | Friday already runs `llama-server` on `127.0.0.1:8090+` and already extracts GGUFs out of Ollama blobs for it | `services/local_call.py:64-106`, `services/gguf_extract.py`, `services/agent.py:357` |
| V14 | Ollama is a separate, live dependency reached at `http://localhost:11434` | `core/__init__.py:1874, 2490-2495, 2967`; `routing/ollama_manager.py` |
| V15 | Tier-1 wheels ride `[all]`; the GPU group is deliberately excluded and opt-in | `pyproject.toml:44-121` |

### 1.2 Carried from earlier feasibility work — **INHERITED** (restated, not re-verified here)

| # | Finding |
|---|---|
| I1 | Local audio-in is buildable now via **`llama-server`**, confirmed working on Windows/CUDA by a third party |
| I2 | It is **not** buildable via **Ollama**, which crashes on audio every few requests — and Ollama is what Friday runs today (V14) |
| I3 | The GGUF multimodal-tower conversion bug was real and was fixed in **llama.cpp PR #24118, 2026-06-04**. Any GGUF pulled **before 2026-06-05** must be re-pulled |
| I4 | Gemma 4 native audio exists on **E2B, E4B and 12B only**; **16 kHz mono**; **hard 30-second per-request ceiling** |
| I5 | Gemma 4 audio is **worse at transcription**: **13.15% WER vs faster-whisper's 11.48%**, degrading to roughly **41% vs 16%** on noisy audio |
| I6 | Its actual advantage is **audio-to-reasoning in a single pass** — prosody, tone, speaker intent |

**These are load-bearing and unverified in this document.** I3 in particular becomes a runtime check in §5.7 rather than a footnote, precisely because it is inherited: a stale-GGUF failure that is silent is indistinguishable from "native audio doesn't work," and that is the exact class of mystery §4.1 exists to end.

### 1.3 The historical record this document is downstream of

The 2026-08-21 voice-mode audit reached a verdict that has not been actioned:

> **"Voice mode was not broken. Friday was running the wrong voice architecture."**

and, on the local cascade specifically, that it

> "**hangs indefinitely** on this machine tonight: it transcribes you, emits `status: thinking`, and then never speaks, never errors, and never times out."

with the standing warning: "If the local engine is ever selected again, it will hang again." Its open item 3 states the requirement this document adopts as R2: "`local_ok` should mean 'can complete a turn,' not 'deps are installed'." And its open item 2 is the sentence the whole repair answers to: **"Silence is the one failure mode audio cannot express."**

The `2026-08-25` triage session added the general principle that §4.1 applies:

> Every subsystem that makes a runtime selection — a path taken, a fallback used, a layer skipped, content withheld, a model substituted — emits a receipt of what it actually did, at the moment it does it, **into a channel its consumer already reads.**

with the corollary that decides where the log goes: "a receipt written where nobody reads it is not a receipt."

---

## 2. The interrogation (STORM)

Four perspectives, run against the design. They are not four ways of agreeing. Where they conflict the conflict is recorded and adjudicated.

### 2.1 P1 — The realtime voice engineer

*Has shipped duplex voice. Knows where latency actually bites.*

**Opening position.** Everyone budgets model inference and nobody budgets the parts that actually dominate. On a CPU Tier-1 turn the wall clock is `VAD close → ASR → brain → TTS first chunk → audio out`, and the two that hurt are the ones nobody instruments: **endpointing delay** (how long after you stop talking before VAD decides you stopped) and **first-token-to-first-audio** in the synthesizer. A 200 ms ASR improvement is invisible next to a 700 ms VAD hangover. Friday cannot currently measure either, because there is no log (V7).

**On the 30-second ceiling (I4), which is the number in this design that will bite.** A hard per-request cap on a *conversational* input path is not a limit, it is a turn-taking policy in disguise. Thirty seconds is generous for a command and short for someone thinking aloud, and the failure lands at the worst possible moment — the user has just finished the long explanation that was the whole point. Whatever else happens, the ceiling **must not be discovered at the end of a turn.**

**The thing I actually care about in native audio.** It removes a serialization point. Today the brain cannot start until ASR has finished producing a string; the pipeline is strictly staged. A model that takes audio directly can begin reasoning on the audio. That is a *structural* latency argument, not a benchmark one, and it is the only latency claim in this document I would defend without measurements.

**Where P1 concedes.** It is also a *worse* structural argument than it looks, because of I4: at a 30 s ceiling you are batching a whole utterance and sending it, which is not streaming. Native audio as specified here is **utterance-granular, not streaming**. Anyone expecting duplex responsiveness from it will be disappointed, and the UI must not imply otherwise.

**Demands.**
1. Instrumentation carries **five timestamps per turn** — VAD open, VAD close, ASR/audio-in complete, first brain token, first audio byte out — or the repair has not happened (§4.1).
2. The 30 s ceiling is **surfaced continuously during capture**, never as a terminal error (§5.6).
3. Native audio is labelled **utterance-granular** wherever it is offered. No "realtime" wording (§5.5).

### 2.2 P2 — The offline-path guardian

*Cares about exactly one thing: that the offline path never regresses.*

**Opening position.** I will start by conceding the uncomfortable thing: **there is no offline path to protect.** V2 and V5 say it plainly. The `[all]` install ships the wheels (V15), the settings page offers the choice, `/api/voice/setup/status` has historically reported optimistically, and the directory the code reads has never existed. Friday's local-first premise is, on this surface, currently a claim in a document.

That reframes my job. I am not guarding a working path from a new feature; I am insisting that the new feature does not get built on top of a hole and thereby make the hole permanent.

**The specific hazard.** Native audio needs `llama-server` (I1). Friday already runs `llama-server` (V13) — for the *brain*. If the audio path binds to the brain's server instance, then local voice acquires a dependency on model residency, and every residency eviction becomes a voice outage with a confusing error. Worse, it makes the audio path's health a function of something the user changed for an unrelated reason.

**On Ollama (I2).** Friday runs Ollama today (V14). Native audio cannot use it. So this design necessarily introduces a **second local inference runtime for the voice role**, and that is a real cost that should be stated rather than absorbed. It happens to be a cost Friday has already partly paid (V13), which is the only reason I am not objecting outright.

**Where P2 overreached.** P2 argued that native audio should not be built at all until Tier 1 has been working, in the field, for a full release cycle — a hard sequencing gate. **Rejected.** The ordering is already correct (§8: repair first, and instrumentation before that). Adding a *time* gate on top converts a sound engineering order into an indefinite block, and the design work does not consume the maintainer's GPU. Amended to: **native audio may not ship *selectable* until `test_voice_local_turn_completes` (§7.1) is green in CI**, which is a condition rather than a calendar. (It may never ship enabled-by-default at all — §5.5 settles that separately and unconditionally.)

**Demands.**
1. The audio-in server is a **distinct `llama-server` instance and port** from the brain seat, or if it is shared, sharing is an explicit configured decision with its eviction behaviour specified (§5.4, §11 Q3).
2. The Tier-1 default binding (`core/__init__.py:1992-1993`, V16) is not touched **as a side effect of this work**. Changing which voice Friday ships with is a legitimate product decision (§4.2 R1.5, §11 Q4); a loader that ends up pointing somewhere new because of a path repair is drift. The test is intent, not the diff.
3. `voice_offline_no_cloud` (cloud spec §6.4) is **extended, not forked**, and it must now actually pass — today it would fail at step 3 (V2).

### 2.3 P3 — The maintainer

*Will carry three local tiers plus two cloud providers plus Gemini Live and does not want the configuration surface to become unusable.*

**Opening position.** Count the axes honestly before adding one. Today: `voice_engine ∈ {local, local-gpu, gemini, auto}`, plus the cloud spec's provider selection within Tier 3, plus per-provider model tier. This document proposes adding a **listening mode**. That is a *fourth* axis, and the repo has already written down what happens when axes accumulate without anyone owning them: `docs/decisions/2026-09-04-five-dead-settings.md`.

**The `auto` problem, which is not new and must be fixed here rather than inherited.** The 2026-08-21 voice-mode audit recorded that "**`\"auto\"` does not mean auto**" — that `auto` and `local` were the same branch, "a value that reads as 'pick whichever works' and behaves as 'always local, regardless.'" The current code (V10, `local_voice.py:599-621`) has since made `auto` mean "GPU if ready else CPU" — which is better, and is *still* not what the word promises, because it never considers whether the tier it picks can complete a turn (V12 checks that at a different layer). Adding a mode axis on top of a selector that already misrepresents itself compounds the debt. **Fix the selector or stop offering the word.**

**Where the design gets this right.** Listening mode is a **mode on the voice role, not a provider**. It does not multiply with provider selection because it is only offered where the engine declares it (§5.4), which is data in `provider_registry.py`, not a branch in the selector. That is the same test the cloud spec set for itself — "adding provider three must be a registry entry … not a code change in the fallback state machine" — and this design passes it: adding the audio-in capability is a `capabilities` entry plus a readiness probe.

**The thing I will not accept.** A "use both and pick the better answer" configuration. It doubles local compute on a machine that is already sharing a GPU with a training run, it doubles the failure surface, and it produces a system whose behaviour cannot be reasoned about from settings. Refuse it in the document so nobody proposes it in a PR (§5.3).

**Demands.**
1. **One new axis, capability-gated, defaulting off.** No sub-options on it in v1.
2. `auto` is fixed or removed **in the same change** as the repair (R4), not later.
3. Every new setting has a corresponding readiness probe and a reason string when unavailable — the `nvidia-nemo` pattern, greyed with a reason, never a control that silently does nothing.

### 2.4 P4 — The audio-native advocate

*Focused on what native audio uniquely enables, since that capability is the entire reason to build this.*

**Opening position.** The WER comparison (I5) is being read as a verdict and it is a category error. Word error rate measures the fidelity of a transcript. Nobody in this design wants a transcript from Gemma 4. The comparison that matters has no metric attached and I will state it as a set of concrete losses instead:

- "Fine." said flatly, and "Fine!" said brightly, produce the same string. The pipeline emits `Fine.` and the brain guesses.
- Hesitation — the two-second pause before "…yes" — is deleted by every ASR on the market, because it is not a word.
- Sarcasm, which is prosody negating semantics, survives transcription as its own opposite.
- Emphasis: "*I* didn't say that" vs "I didn't say *that*." Same string, different claim.
- Who is speaking, whether there are two people, whether one of them is a television.

Every one of those is information present in the microphone signal and absent from the string, and Friday's entire product argument is that she understands you rather than parses you.

**The strongest form of the argument, and where it interacts with C3.** C3 says Gemma 4 E2B was chosen *because* offline capability is the point. Until now that has meant "she can think offline." Native audio is the first thing that makes it mean "she can *listen* offline" — and the two-mode design is what lets both be true at once, because dictation keeps the accurate path.

**Where P4 lost, and it matters.** P4 argued for making native audio the default listening mode on the grounds that conversation is the primary use and dictation the secondary one. **Rejected on the evidence, not on caution:** I5's noisy-audio figure (~41% vs ~16%) is not a quality difference, it is a functioning-versus-not difference, and a default is what runs for the user who never opens settings — including the user in a kitchen with a fan on. Amended to: native audio is **opt-in**, and the *offer* is contextual (§5.5) rather than buried, so that the users who want it find it.

**Demand.** The mode selector must be described in terms of what it does — *understanding how you said it* — and not in terms of the technology. A setting that says "Gemma 4 native audio" tells the user nothing about why they would want it, and a user who cannot tell why will not choose it, which makes the entire build pointless.

### 2.5 The exchange, where it was load-bearing

**P1 × P4, on the 30-second ceiling.** P4 wanted long-form listening — the whole point being to hear how someone explains something, which takes longer than thirty seconds. P1 held that I4 is a hard model limit and that chunking a continuous utterance into 30 s windows and concatenating the responses produces a model that has heard the words but not the shape — you lose exactly the cross-utterance prosody that motivated the feature, while paying full cost for it. **Resolution:** no automatic chunking. The ceiling is a **capture budget shown during capture** (§5.6). On reaching it, capture closes cleanly at the boundary and the turn is served; it does not truncate silently and it does not stitch. Long-form listening is out of scope and named as such (§9), with §11 Q2 asking whether it is a product goal worth a different design.

**P2 × P3, on the second runtime.** P2 wanted `llama-server` for audio isolated from the brain's instance; P3 pointed out that a second resident model is a second VRAM tenant on a machine where the training run already demonstrates what contention costs, and that `nemo_voice.py:225-255` already contains a contention probe written for exactly this reason — noting there that voice models slowed local replies "roughly 10x". **Resolution:** isolation is the default *shape* (separate instance, separate port), but residency is arbitrated by the existing mechanism rather than a new one, and the audio-in seat is **evictable with an announced consequence** (§5.4). Its eviction produces `local_audio_seat_evicted` (§6.1), not a hang. §11 Q3 puts the sharing question to the maintainer because it is a resource-allocation call, not a design one.

**P3 × P4, on where the mode lives in the UI.** P4 wanted the mode surfaced in the voice UI itself, one tap from the mic, on the grounds that it is a per-conversation choice — you dictate a note, then you talk through a problem. P3 objected that a control adjacent to the mic which changes model behaviour mid-session is precisely how you get a user who cannot explain why Friday behaved differently on Tuesday. **Resolution:** the mode is **set in settings, indicated in the voice UI, and switchable from the indicator** — one place to configure, one place to see, and the switch is on the thing that already tells you the truth (cloud spec §4.4) rather than a second control that could disagree with it.

**All four, on ordering.** No perspective argued against instrumentation-first. P1 wanted it for latency, P2 for regression detection, P3 because a setting without a readiness signal is a dead setting, P4 because native-audio quality claims are unfalsifiable without per-turn records. **That unanimity is the strongest result in this interrogation** and is why §4.1 is R0 rather than an item in a list.

---

## 3. The repair and the new path, in one picture

```
                        ┌─────────────────────────── R0: it emits receipts ──┐
                        │                                                     │
  mic ──▶ VAD ──▶ ┌─────┴──────────────┐                                      │
                  │  LISTENING MODE    │                                      │
                  ├────────────────────┤                                      │
                  │ transcribe (dflt)  │──▶ faster-whisper ──▶ text ──┐        │
                  │ understand (opt-in)│──▶ llama-server audio-in ────┤        │
                  └────────────────────┘        (Gemma 4, 16 kHz,     │        │
                           ▲                     mono, ≤30 s)         │        │
                           │                                          ▼        │
                    capability-gated                            local brain    │
                    per engine tier                                   │        │
                                                                      ▼        │
                                            Piper / NeMo TTS ──▶ speakers ─────┘
```

The mode axis sits **inside** a tier, before the brain. It does not create a tier, it does not create a provider, and it does not touch TTS at all.

---

## 4. Phase R — the repair

R0 precedes everything because without it the rest cannot be confirmed. **The R-numbers are identifiers, not a build order** — they are grouped here by subject so that Tier-2 work sits next to Tier-2 work. The build order is §8, where R4 lands before R3.

### 4.1 R0 — Instrumentation is step zero

**Problem.** Local voice has no log (V7). Its two `print()` calls land in a 136 MB unrotated `server_stderr.log` (V8) whose contents the repo elsewhere describes as lost — "stderr from `traceback.print_exc()` is simply lost in production" (`routes/chat.py:87-88`), and the tray DEVNULLs stdio (`routes/voice.py:2272`). The one voice-specific logger is cloud-only and opt-in (V9). Consequence: **a local voice failure cannot be diagnosed after the fact.** The 2026-08-21 hang — "never speaks, never errors, and never times out" — is exactly this shape, and the reason its cause is still described in that audit as an observation rather than a diagnosis.

**Specification.**

**R0.1 — A logger, in the repo's own convention.** `logging.getLogger("friday.local_voice")` in `local_voice.py` and `friday.nemo_voice` in `nemo_voice.py`, propagating to the existing root handler so records land in `~/.friday/friday.log` with its existing rotation (10 MB × 3) and format (`%(asctime)s %(levelname)-8s %(name)s — %(message)s`, `%Y-%m-%dT%H:%M:%S`). **No new log file and no new rotation policy.** The receipts principle decides this: the consumer already reads `friday.log`; a fourth log is a receipt nobody reads. The two `print()` calls at `:751` and `:764` become `log.warning` and `log.error` respectively.

**R0.2 — A turn receipt.** One structured record per voice turn, at turn end, at INFO. Fields:

| Field | Why |
|---|---|
| `turn_id`, `session_id` | correlation with the WS frames the client saw |
| `preferred_tier`, `active_tier` | the `PREFERRED(t) → ACTIVE(t')` pair (`voice-system-spec.md` §7.1) |
| `listening_mode` | `transcribe` \| `understand` (§5) |
| `downgrade_reason` | populated exactly when `preferred != active`; the §8 reason code, not prose |
| `t_vad_open`, `t_vad_close`, `t_input_complete`, `t_first_brain_token`, `t_first_audio_out` | P1's five timestamps |
| `asr_model`, `tts_voice`, `brain_seat` | what actually ran, resolved, not configured |
| `outcome` | `served` \| `timeout` \| `error:<code>` \| `aborted` |
| `audio_ms_in`, `chars_out` | volume, and the input to any future metering |

Deliberately **not** included: transcript text, audio, or any user content. The receipt is a record of *routing*, and a diagnostic log that accumulates conversation content is a privacy liability in a product whose premise is local-first.

**R0.3 — A readiness receipt.** Every `models_ready()` / `gpu_tier_ready()` / `_local_brain_ready()` evaluation that returns False logs **which** condition failed and **which path it checked**. The specific defect this catches is V2/V4: today a user sees "local voice unavailable" and the log does not say `~/.friday/local_voice/whisper does not exist`, which is the single sentence that would have ended this whole investigation months ago.

**R0.4 — Silence is an outcome.** A turn that produces no audio and no error within its budget emits `outcome: timeout` with code `local_voice_turn_timeout` (§6.1). This is the direct answer to the voice-mode audit's "Silence is the one failure mode audio cannot express." The watchdog is specified in R2; R0 is the requirement that it be *recorded*.

**Acceptance.** Kill Friday mid-turn, then reconstruct from `friday.log` alone: which tier was preferred, which served, why it changed, where the time went, and whether the turn completed. If any of those five requires reading source code, R0 is not done.

### 4.2 R1 — Reconcile the model paths

**Problem.** V1–V6. The code reads `~/.friday/local_voice/{whisper,piper}`, which does not exist; the assets are at `~/.friday/runtime/{whisper-models,piper-voices}`, which nothing in `src/` reads; and the assets that are there are **the wrong ones anyway** — `en_US-lessac-medium` against a default of `amy-medium`, and an HF-hub-layout `large-v3-turbo` against a flat-`download_root` default of `small`.

That last point is the one that makes this not a one-line fix, and it is why the obvious repair is wrong.

**Options considered.**

| Option | Verdict |
|---|---|
| **A. Repoint the code at `runtime/`** | **Rejected.** It fixes the directory and not the contents (V6). The user gets a different voice than the default declares and an ASR model the loader cannot address in that layout. It also inverts a real separation: `runtime/` is the *provisioning* area (`provisioning-report.md` §5), owned by an out-of-tree mission; `local_voice/` is Friday's own managed asset directory. Making Friday's code depend on an externally-provisioned tree is how you get a voice path that works on one machine. |
| **B. Move the assets into `local_voice/`** | **Rejected as the primary fix.** Same contents problem, plus it makes a one-machine manual step load-bearing. |
| **C. Let the existing downloader do its job into `local_voice/`, and make the failure legible** | **Chosen.** `local_voice.py:355` and `:420` already describe downloading into `~/.friday/local_voice/{whisper,piper}`. The defect is not that the download path is wrong; it is that **nothing ever ran it and nothing said so.** |
| **D. Make the paths configurable** | **Already true, and undocumented** (V1): `voice_assets_dir()` is preferred under OS mode at `local_voice.py:343-358` and `:413-430`. So the work is not to *add* configurability but to **document the precedence and make the resolved root appear in the receipt** (R0.2's `asr_model` / `tts_voice` fields become path-qualified). An override nobody knows about is a debugging trap of exactly the kind R0 exists to close. |

**Specification.**

- **R1.1** — `LOCAL_VOICE_DIR` stays `friday_home() / "local_voice"` as the **default** root, and the existing `voice_assets_dir()` override (V1) stays. What changes is that the precedence is *written down once* — resolved through `paths.py` alongside `runtime_dir()`, documented in `paths.py:172`'s docstring rather than implied by it, and **reported**: every readiness result and every turn receipt names the root that was actually consulted. Today a user with OS mode set and a user without it get different behaviour from the same settings and identical (absent) diagnostics.
- **R1.2** — A **provisioning check on first local-voice use**, not on boot (C3: nothing on the local path may block on a probe). It reports, per asset, one of: `present` / `absent` / `present-but-unusable`. The third state is V6's case and it currently has no representation at all.
- **R1.3** — Missing assets produce `local_voice_models_missing` (an **existing** code, `voice-system-spec.md` §8) with the concrete path in the `detail` field, and the existing in-UI download flow as the `action`. Offline with assets missing keeps the existing `models_missing_offline`.
- **R1.4** — **New code `local_voice_models_misplaced`** for the V6 state: assets found somewhere Friday knows about (`runtime/`) but not in a form the loader can use. Its `action` is an explicit, user-confirmed **import** — copy or link the asset into `local_voice/`, converting layout where the loader requires it. Import is **offered, never automatic** (C2's rule, applied to the filesystem): silently adopting a 1.5 GB model a different process provisioned is a substitution the user did not ask for, and if it is the wrong model the resulting quality change would be unattributable.
- **R1.5** — If `en_US-lessac-medium` is to be the shipped default rather than `amy-medium`, that is a **settings and registry change** (`provider_registry.py` `local-voice-lite` models list, `model_meta`, and the Tier-1 default binding at `core/__init__.py:1992-1993`, V16), decided deliberately and separately from this repair. It is **not** something the loader infers from what happens to be on disk. Note that `cloud-voice-providers.md` §8.1 states the binding "stays" as `piper-en_US-amy-medium`; that document was describing what its own scope does not touch, not foreclosing a product decision. §11 Q4.

**Acceptance.** On a machine with no assets, first local-voice use produces a specific, actionable message naming the exact missing path; on this machine, it produces `local_voice_models_misplaced` naming both paths and offering import.

### 4.3 R2 — Make Tier 1 able to complete a turn

**Problem.** The 2026-08-21 voice-mode audit: "**The check vouches for two thirds of the pipeline**" — `local_ok = eng.available()` covers ASR and TTS but never the brain, "a gate reasoning about the *form* of the thing (are the packages installed?) rather than its *meaning* (can this pipeline answer a question?)". Partially fixed since: `routes/voice.py:916-921` now requires a resident local brain seat (V12). The remainder is the hang: transcribes, emits `status: thinking`, never speaks, never errors, never times out.

**Specification.**

- **R2.1 — `local_ok` means "can complete a turn."** Readiness is the conjunction of ASR loadable, TTS loadable, **and** a brain seat resident and reachable. A failure of the third conjunct specifically emits **`local_voice_brain_absent`** (§6.1) rather than the generic unavailability message — the whole point of R2.1 is that the three conditions stop being one undifferentiated boolean. V12 is the right shape; the change is that the same conjunction governs `/api/voice/setup/status` and the settings UI's availability display, so the three surfaces cannot disagree. The voice-mode audit records that `setup/status` "inherits the same optimism and reports `\"ready\": true`" — that is the specific inconsistency being closed.
- **R2.2 — A per-stage turn watchdog.** Each stage (ASR/audio-in, brain, TTS-first-chunk) has a budget. Exceeding it terminates the turn with `local_voice_turn_timeout`, names the stage in `detail`, emits the R0.2 receipt with `outcome: timeout`, and **speaks or displays a failure** rather than going quiet. Budgets are configuration with documented defaults, not constants buried in the module — because on a machine sharing a GPU with a training run the honest budget is different, and a user should be able to say so rather than experience it as a bug.
- **R2.3 — A local self-test.** One command / one settings button that runs the §7.1 round trip and reports each stage's outcome and timing. The repo already contains the artifact of someone doing this by hand: `~/.friday/voice-selftest.wav`. Make it a supported path rather than a leftover.

### 4.4 R3 — Tier 2 must not degrade onto a broken Tier 1

**Problem.** Tier 2's own defect was fixed, but its three degradation points (V10) all land on Tier 1, which does not work (V2), and `~/.friday/models/nemo/` has no checkpoints (V11) so degradation is the *normal* path, not the exceptional one. The user's experience of choosing GPU voice is therefore: a fallback they were told about, onto a tier that silently hangs.

**Specification.**

- **R3.1** — R1 and R2 are **prerequisites** for R3, not parallel work. Tier 2's fallback target must be known-good before its fallback can be called correct.
- **R3.2** — Each of the three degradation points (`resolve_tier`, `ensure_ready:725-728`, `ensure_ready:749-767`) emits the R0.2 receipt **and** the one-notice-per-transition frame required by `voice-system-spec.md` §7.3 — `{type:'error-nonfatal'}`, not a transient `{type:'status'}` line. Today only `resolve_tier` records `last_downgrade`, and only when the user explicitly chose GPU; the `auto` path sets it empty (V10, `local_voice.py:599-621`), which means an `auto` user who is silently on CPU has no receipt at all.
- **R3.3** — **Degradation checks its target.** A degrade to a Tier 1 that fails R2.1 readiness is not a degrade, it is a failure: emit `local_voice_load_failed` (existing code) with both tiers named in `detail`, rather than swapping to a tier that will hang. This is C2's "surfaces and offers, never substitutes" applied inside the local path.
- **R3.4** — The existing `nemo_voice.py:225-255` contention probe is retained **with its non-gating contract intact** (V17: "never blocks the tier … the point is to make the trade visible, not to make it for the user"). Its threshold is documented rather than implicit, and its verdict is written into the R0.2 receipt so that "voice was slow that evening" becomes attributable. **It is not converted into a gate** — §5.6 explains why the native-audio path needs a *separate*, explicitly gating check and why reusing this one would have been a quiet reversal of a deliberate decision.

### 4.5 R4 — `auto` means auto, or `auto` goes away

**Problem.** P3's objection, and the voice-mode audit's finding: "a value that reads as 'pick whichever works' and behaves as 'always local, regardless.'" Current behaviour (V10) is "GPU if `gpu_tier_ready()` else CPU" — better, and still not what the word promises, because neither branch consults R2.1 turn-readiness.

**The head-on conflict, stated rather than glossed.** `voice-system-spec.md` §7.2's `auto` row reads:

> `auto` | Tier2 (if ready) → Tier1 → **(cloud only if key present AND not local-only)** → text-only

So the authoritative spec **already permits `auto` to reach Tier 3**. The voice-mode audit's open item 1 leaves it genuinely open — "Either make it **probe cloud** when local cannot complete a turn, or remove the option." And `cloud-voice-providers.md` §6.3 is unambiguous that a *local failure* must surface and offer rather than promote to cloud. Those three cannot all be satisfied by the current row. **This document does not get to resolve that by writing a test.** It is put to the maintainer as §11 Q5, with the three candidates below, and whichever is chosen requires a corresponding edit to §7.2 (§10, row 11.2).

| | Behaviour | Consequence |
|---|---|---|
| **R4.1 — local-only `auto`** *(recommended)* | Best *local* tier that can complete a turn: Tier 2 if GPU-ready **and** turn-ready, else Tier 1 if turn-ready, else text-only with the classified error. Never crosses to cloud. Label states the scope: *Automatic (local only)* | Consistent with C1/C2 and with the cloud spec's §6.3 by construction. **Requires deleting the parenthetical from §7.2's `auto` row.** |
| **R4.2 — remove `auto`** | Three honest values: `local`, `local-gpu`, `gemini` (plus provider selection within Tier 3) | Smallest surface; P3's preference. Also requires a §7.2 edit — removing the row. |
| **R4.3 — `auto` may offer cloud** | Keeps §7.2's row, but the cloud step becomes an **offer** the user accepts, never an automatic hop | Preserves the existing spec's intent while satisfying §6.3. Costs an interruption at the worst moment, which is precisely the trade §6.3 already made deliberately. |

`test_resolve_engine_auto_never_cloud` (§7.1) is written for R4.1/R4.2 and **must not be added under R4.3** — it would encode one candidate as settled. **Inheriting the current behaviour unchanged is the one option that is not acceptable**, because whatever `auto` ends up meaning, it does not currently mean it. The voice-mode audit's open item 1: "The present behaviour is a setting that lies to the person who chose it."

---

## 5. Phase N — native audio as a second listening mode

### 5.1 What is being added, in one sentence

A second way for a local tier to consume microphone audio: instead of transcribing it to text and handing the text to the brain, hand the **audio itself** to a Gemma 4 model that can hear it — for the turns where *how* something was said is part of what was said.

### 5.2 Why it is a mode and not a tier

A tier in `voice-system-spec.md` §2 is a *stack*: ASR + TTS + hardware + install extra + WS route. Native audio changes exactly one element of that stack — the ASR stage — and leaves TTS, hardware class, install extra and WS route identical. Introducing Tier 4 for it would duplicate the entire matrix to vary one cell, and would break the cloud spec's tier vocabulary that this document is committed to sharing (§0.2). It is therefore an **axis inside a tier**, which is also what makes P3's combinatorics objection survivable: the mode multiplies with *nothing*, because it is offered only where an engine declares it (§5.4).

### 5.3 The two modes, named and bounded

| | **`transcribe`** (default) | **`understand`** (opt-in) |
|---|---|---|
| Path | mic → faster-whisper (or NeMo ASR on Tier 2) → text → brain | mic → Gemma 4 audio-in via `llama-server` → brain response |
| Optimises for | **accuracy of words** | **fidelity of meaning** |
| WER | 11.48% clean / ~16% noisy (**INHERITED**, I5) | 13.15% clean / ~41% noisy (**INHERITED**, I5) |
| Preserves prosody, tone, hesitation, emphasis | **No** — deleted at the string boundary | **Yes** — the reason it exists (I6) |
| Input constraints | none new | **16 kHz mono, ≤30 s per request** (**INHERITED**, I4) |
| Model support | any Whisper/NeMo model already supported | **Gemma 4 E2B / E4B / 12B only** (**INHERITED**, I4) |
| Granularity | streaming-capable | **utterance-granular** (P1's demand, §2.1) |
| Good for | dictation, notes, commands, anything you will read back | conversation, explaining a problem, anything where tone carries the claim |
| Runtime | in-process wheels | `llama-server` (**not Ollama** — I2) |

**Explicitly refused: a "both" mode.** Running transcription and native audio on the same utterance and choosing between the results is rejected (P3, §2.3): it doubles local compute on a contended machine, doubles the failure surface, and produces behaviour that cannot be predicted from settings. It is refused here so that it is not proposed later as an obvious improvement.

### 5.4 Registration and runtime

**Capability keyword.** `audio-in`, a sibling to the cloud spec's `duplex` (§3.1 there), added to `provider_registry.py` and declared by any provider whose model can consume audio directly. This is the mechanism by which the mode is capability-gated rather than branch-gated — P3's test that "provider three is a registry entry, not a code change."

`local-voice-lite` and `nvidia-nemo` keep `capabilities: ["asr", "tts"]` unchanged (V-registry). Native audio is declared by the **brain-side** provider entry that serves audio-in, because that is where the capability physically lives.

**Runtime — `llama-server`, not Ollama.** I1/I2 make this non-optional, and V13 makes it cheap: Friday already runs `llama-server` on `127.0.0.1:8090+`, already extracts GGUFs from Ollama blobs via `gguf_extract.py`, and already dispatches to it through `local_call.py:64-106`. The audio path reuses that machinery.

**Seat isolation (P2 × P3 resolution, §2.5).** The audio-in model runs as a **distinct seat on its own port**, arbitrated by the existing residency mechanism (`residency_arbiter.py`, `local_seats.py`) rather than a new one. It is evictable. Eviction while `understand` is the selected mode produces `local_audio_seat_evicted` (§6.1) and an offer to fall back to `transcribe` — **offered, not applied** (C2). Whether the audio seat may instead share the brain's instance is a resource decision, not a design one: §11 Q3.

**Registry shape** — the existing entry shape, unchanged, plus one capability:

```python
{
    "name": "local-audio-in",
    "label": "Local Audio Understanding (Gemma 4)",
    "type": "local-llama-server",
    "base_url": "",                        # loopback seat, port assigned by the arbiter
    "auth": {"type": "none"},
    "models": ["gemma4-e2b", "gemma4-e4b", "gemma4-12b"],
    "capabilities": ["audio-in"],
    "roles": [ROLE_VOICE],
    "cost_per_1k": {},
    "model_meta": { ... },                 # label / short / roles / modalities: ["audio", "text"]
    "enabled": True,
}
```

No new configuration concept, no new secrets mechanism, no network dependency. C3 is satisfied structurally rather than by assertion: there is nothing in this entry that can reach outside the machine.

### 5.5 Selection and disclosure

**Where it lives (P3 × P4 resolution).** Configured in Settings → Audio & Voice, alongside the existing `voice_engine` control. Indicated in the voice UI on the existing indicator (cloud spec §4.4), which already carries "what actually served." Switchable from that indicator. **One place to configure, one place to see, and the switch is on the element that tells the truth.**

**Default.** `transcribe`, always, on a fresh install and after any upgrade. Native audio is never pre-selected. (P4 lost this argument on the noisy-WER evidence, §2.4.)

**Availability.** The `understand` option is visible and greyed where unavailable, with the reason — the `nvidia-nemo` pattern, and P3's demand 3. Reasons: no supported model resident; `llama-server` not available; GGUF provenance check failed (§5.7). Never a control that exists and does nothing (`docs/decisions/2026-09-04-five-dead-settings.md`).

**Disclosure at the point of selection** — the cloud spec's §4.3 pattern, reused with local content. Selecting `understand` shows, **before** confirmation, four things:

1. **What it is for.** In P4's terms, not the technology's: *Friday hears how you say things — tone, hesitation, emphasis — instead of only what you said.* The word "Gemma" does not appear in the primary sentence.
2. **What it costs you.** Plainly: *transcription accuracy drops, noticeably in noisy rooms.* This is I5 stated to the person making the trade rather than buried in a design document — the same move the cloud spec makes with its cascade warning.
3. **Its limits.** Utterance-granular, not streaming. **30 seconds per turn.** 16 kHz mono.
4. **Where it runs.** On this machine. Nothing leaves. This is the one disclosure that is good news, and it is the reason the mode exists at all under C3.

**Never inferred.** Friday does not switch modes based on content, noise level, utterance length, or a confidence score. Every one of those would be a silent substitution, and C2 forbids them for the same reason it forbids silent cloud promotion: the user cannot attribute behaviour they were not told about.

### 5.6 The 30-second ceiling, and GPU contention

**The ceiling (P1 × P4 resolution, §2.5).**

- The remaining capture budget is **visible during capture**, not discovered at the end. P1's rule: a limit that lands after the user has finished their point is a limit that has already failed.
- At the boundary, capture **closes cleanly and the turn is served**. It does not truncate silently, and it does not stitch multiple 30 s windows into one turn — stitching would preserve the words and destroy the cross-utterance shape that was the entire motivation.
- Speech that exceeds the budget produces `local_audio_input_too_long` (§6.1) with the action *switch this turn to transcription* — **offered, taken by the user, not applied automatically**.
- Long-form listening is **out of scope** (§9) and is §11 Q2.

**GPU contention.** Native audio needs the GPU. On this machine the GPU is periodically held by training runs (~12 GB), and the repo already knows what that costs: `nemo_voice.py:225-255` records that voice models slowed local replies "roughly 10x." Therefore:

- `understand` availability is gated by a **VRAM admission check at seat load**, which is *not* the Tier-2 contention probe. The probe deliberately never blocks (V17, R3.4) because Tier-2 degrades gracefully to a CPU tier that still works; native audio has no such graceful degradation — its only fallback is a different listening mode, which C2 forbids taking without the user. So the two checks differ by design and the difference is stated here rather than discovered later: the probe **reports**, the admission check **refuses and offers**. They share the same VRAM measurement (`nemo_voice.gpu_status()`), not the same policy.
- Insufficient free VRAM produces `local_audio_no_vram` (§6.1) with the reason and the current holder where known, and an offer to use `transcribe` for the session.
- **A voice mode may never evict a training job or a user-pinned seat.** It yields; it does not compete.

### 5.7 GGUF provenance — the check that stops a silent mystery

I3 establishes that the multimodal-tower conversion bug was fixed in llama.cpp PR #24118 on **2026-06-04**, and that **any GGUF pulled before 2026-06-05 must be re-pulled**. A stale GGUF does not announce itself; it produces bad or absent audio understanding that is indistinguishable from "this feature does not work."

**Specification.** Before `understand` is offered as available, the candidate GGUF is checked for provenance: conversion date / source revision recorded at extraction time by `gguf_extract.py`, or file mtime as the fallback signal where no better record exists.

- Provenance **on or after 2026-06-05** → available.
- Provenance **before 2026-06-05, or unknown** → `local_audio_gguf_stale` (§6.1). The mode is unavailable with the reason stated and the action *re-pull this model*. **Unknown is treated as stale**, deliberately: this is an inherited fact (§1.2) whose failure mode is silent, and the cost of a false positive is one re-download while the cost of a false negative is exactly the class of unattributable bug R0 exists to eliminate.
- The date and the PR number belong in the error `detail`, not only in this document, so a future maintainer meets the reason rather than the symptom.

### 5.8 Composition with "Local listening, cloud voice"

`cloud-voice-providers.md` §3.3 permits exactly one split: **cloud TTS with STT staying local**, offered as "Local listening, cloud voice" and named there as the *recommended* cloud configuration, on the grounds that only synthesis text leaves the machine.

`understand` mode composes with it cleanly and this is worth stating rather than leaving to be discovered:

- The listening half is local in both modes, so the split's whole justification — raw audio never leaves — is **strengthened**, not weakened, by native audio. Nothing about `understand` changes what crosses the boundary.
- What *does* change is the egress-gate surface. `cloud-voice-providers.md` §8.3 makes gating synthesis input a blocking prerequisite for cloud TTS. That is unaffected by mode: the gate reasons about the outgoing text, and both modes produce outgoing text from the brain.
- One genuine asymmetry: in `understand` mode there is **no transcript**. Any diagnostic, receipt or gate that assumed an ASR string exists for the *input* side must tolerate its absence. This is called out because it is the kind of assumption that is invisible until it is violated.

The reverse split (cloud STT with local TTS) remains refused by §3.3 and is not reopened here.

---

## 6. Failure taxonomy additions

Following `voice-system-spec.md` §8's existing `{code, user_message, action}` shape and its naming convention. **New codes only** — existing codes (`local_voice_deps_missing`, `local_voice_models_missing`, `models_missing_offline`, `local_voice_load_failed`, `gpu_degraded`, `gpu_install_failed`, `vad_downgraded`, `mic_no_audio`, `mic_permission_denied`, …) are reused unchanged.

### 6.1 New codes

| Code | Root cause | Discriminator | User message | Action |
|---|---|---|---|---|
| `local_voice_models_misplaced` | Assets exist under `runtime/` but not in a form/location the loader uses (V6) | `LOCAL_VOICE_DIR` absent **and** a known asset found elsewhere | "Your voice models are on this machine but in the wrong place for Friday to use." | Offer import (R1.4) |
| `local_voice_turn_timeout` | A turn stage exceeded its budget; the silent-hang class | Watchdog fired; stage named in `detail` | "Voice stopped responding partway through. Nothing was sent anywhere." | Retry; open self-test (R2.3) |
| `local_voice_brain_absent` | ASR/TTS ready, no resident local brain seat (V12) | `_local_brain_ready()` False with `models_ready()` True | "Local voice can hear you, but no local model is loaded to answer." | Load a local model |
| `local_audio_unsupported_model` | Selected model is not E2B/E4B/12B (I4) | Model id not in the audio-capable set | "This model can't listen directly. Pick one that can, or use transcription." | Switch model or mode |
| `local_audio_gguf_stale` | GGUF predates 2026-06-05 or provenance unknown (I3, §5.7) | Provenance check | "This model file was built before the audio fix and won't hear correctly." | Re-pull the model |
| `local_audio_runtime_unsupported` | Audio requested against an Ollama-backed seat (I2) | Serving runtime is Ollama | "Audio understanding needs Friday's own model server, not Ollama." | Start/point at `llama-server` |
| `local_audio_input_too_long` | Utterance exceeded the 30 s ceiling (I4, §5.6) | Capture budget exhausted | "That was longer than audio understanding can take in one turn." | Serve as transcription this turn |
| `local_audio_no_vram` | Insufficient free VRAM; contention (§5.6) | Contention probe below threshold | "Not enough free GPU right now — something else is using it." | Use transcription for now |
| `local_audio_seat_evicted` | Audio seat evicted by the residency arbiter (§5.4) | Arbiter eviction event | "Friday's listening model was unloaded to make room." | Reload, or switch to transcription |

Every one of these is **surfaced and offered**. None of them silently changes mode.

### 6.2 Mode transitions use the existing transition rule

`voice-system-spec.md` §7.3 requires that every `PREFERRED(t) → ACTIVE(t')` with `t' != t` emit exactly one persistent notice with reason code, human message and remediation, as `{type:'error-nonfatal'}` rather than a transient `{type:'status'}` line. **A listening-mode change is such a transition** and uses the same frame and the same rule. No parallel mechanism, no new frame type.

---

## 7. Verification

### 7.1 New tests, in the existing naming convention

Following `voice-system-spec.md` §10.2's function-name convention and its harness shape — "synthetic WAV in → STT → canned response → TTS → byte-level output assertion."

| Test | Asserts | CI |
|---|---|---|
| `test_voice_local_turn_completes` | The full Tier-1 round trip completes with real wheels and fixture assets in a temp `friday_home()`. **This is the load-bearing test of Phase R** — it is what "Tier 1 works" means. | Must pass |
| `test_voice_local_models_misplaced` | Assets present under a `runtime/`-shaped path and absent from `local_voice/` produce `local_voice_models_misplaced` and **no automatic import** | Must pass |
| `test_voice_local_turn_timeout` | A stalled brain stage produces `local_voice_turn_timeout` with the stage named, within budget, and a receipt with `outcome: timeout` — never silence | Must pass |
| `test_voice_receipt_emitted` | Every turn emits one R0.2 receipt with all required fields, and the receipt contains **no transcript text or audio** | Must pass |
| `test_voice_degrade_checks_target` | Tier 2 → Tier 1 with Tier 1 not turn-ready produces `local_voice_load_failed`, not a swap (R3.3) | Must pass |
| `test_voice_readiness_discriminates` | Each of the three R2.1 conjuncts failing alone produces its own code — `local_voice_models_missing`, `local_voice_deps_missing`, `local_voice_brain_absent` — never one generic unavailability | Must pass |
| `test_resolve_engine_auto_never_cloud` | `auto` never resolves to a cloud engine under any settings — the local sibling of the existing `test_resolve_engine_local_only_never_cloud` (D-AC3) | **Conditional.** Add only if §11 Q5 resolves to R4.1 or R4.2. Under R4.3 the test instead asserts that the cloud step is an *offer*, never an automatic hop |
| `test_audio_mode_capability_gated` | `understand` is unavailable, with a reason, when no provider declares `audio-in` | Must pass |
| `test_audio_mode_gguf_provenance` | Pre-2026-06-05 **and unknown** provenance both yield `local_audio_gguf_stale` | Must pass |
| `test_audio_mode_never_auto_switches` | No content, noise, length or confidence condition changes the mode without user action | Must pass |
| `test_audio_mode_input_ceiling` | Over-30 s capture yields `local_audio_input_too_long` and an **offer**, never an automatic re-route or a stitched turn | Must pass |
| `test_audio_mode_roundtrip` | Real audio-in through a real `llama-server` seat | **Manual / GPU-gated.** Mirrors Tier 2's convention: "Real inference is manual." Must not run while a training job holds VRAM (§5.6) |

### 7.2 The offline gate is extended, not duplicated

`voice_offline_no_cloud` (cloud spec §6.4) already asserts that with the network black-holed and all cloud keys absent, Friday boots, the mic is live, a full Tier-1 round trip completes, no cloud module is imported, the indicator reads Tier 1, and time-to-first-audio has no added timeout stalls.

Two cases are added to **that** test rather than a new one:

- **Mode-invariance:** the same assertions hold with `listening_mode = understand` on a machine where the audio seat is resident.
- **Provenance offline:** the §5.7 check performs **no network call**. A provenance check that phones home to resolve a model revision would be a C3 violation with a plausible excuse, and it is the most likely accidental form of one in this design.

**Note plainly:** `voice_offline_no_cloud` would **fail today** at its step 3 (V2). It is currently an aspiration. Phase R is what makes it a test.

---

## 8. Recommended implementation order

Sequenced by dependency, not by appeal.

| # | Work | Why here | Gate to proceed |
|---|---|---|---|
| **0** | **R0 — instrumentation** (§4.1) | Nothing after this is confirmable without it. Unanimous across all four perspectives (§2.5) | A killed mid-turn session is reconstructable from `friday.log` alone |
| **1** | **R1 — path reconciliation** (§4.2) | The single reason Tier 1 has never run (V2) | `local_voice_models_misplaced` fires correctly on this machine; assets install to `local_voice/` on a clean one |
| **2** | **R2 — turn completion + watchdog** (§4.3) | Fixes the hang; makes `local_ok` mean what it says | `test_voice_local_turn_completes` green |
| **3** | **R4 — `auto` semantics** (§4.5) | Small, and it must not be inherited into a system with more axes | `test_resolve_engine_auto_never_cloud` green |
| **4** | **R3 — Tier-2 degradation** (§4.4) | Only correct once its target is known-good | `test_voice_degrade_checks_target` green |
| **5** | **`voice_offline_no_cloud` passes** (§7.2) | The line between "local-first is a claim" and "local-first is a property" | Green in CI |
| **6** | **N1 — `llama-server` audio seat + provenance check** (§5.4, §5.7) | First native-audio work; needs GPU only for manual verification | `test_audio_mode_gguf_provenance` green; manual round trip once the GPU is free |
| **7** | **N2 — mode axis, registry capability, selection + disclosure** (§5.3–5.5) | The user-visible feature | Capability-gating and never-auto-switch tests green |
| **8** | **N3 — capture budget UI, contention gating** (§5.6) | Polish that P1 and P3 both made a correctness condition | `test_audio_mode_input_ceiling` green |

**Steps 0–5 are the repair and should be treated as one release.** Shipping native audio before step 5 would mean adding a second listening mode to a path that cannot complete a turn in the first mode, which is how a feature inherits a reputation it did not earn.

Steps 6–8 are GPU-touching only at manual verification; the design, registry, provenance and taxonomy work is all readable/writable while a training run holds the card.

---

## 9. What this document does not specify

- Any implementation. No code is written, nothing is committed.
- **Tier 3** — `cloud-voice-providers.md` owns it. This document does not modify cloud provider selection, the cascade disclosure, or metering.
- **Cost metering for local voice.** Local is $0.00 and the cloud spec's §7.3 already says the comparison against $0.00 is what does the persuading. The R0.2 receipt records volume so that future metering is possible without re-instrumenting.
- **Long-form listening** beyond 30 s — refused as a stitched-turn design (§5.6), open as a product question (§11 Q2).
- **Native audio *out*.** This is audio-in only. Local TTS is unchanged: Piper on Tier 1, NeMo on Tier 2.
- **Split-modality routing** — upheld as refused by `cloud-voice-providers.md` §3.3. A mode is not a second provider.
- **Speaker diarisation / identification.** Native audio may make it *possible*; nothing here specifies it, and it carries biometric implications that need their own decision record.
- **Which Piper voice ships by default** — surfaced as §11 Q4, not decided here.

---

## 10. Effect on the three unapplied edits to `voice-system-spec.md`

`cloud-voice-providers.md` §11 identified three structural edits to `voice-system-spec.md` that were specified but not applied. This document changes their urgency:

| Edit (cloud spec §11) | Was | Now | Why |
|---|---|---|---|
| **11.1 — §2 tier matrix gains a Tier-3 provider column** | Needed for the cloud work | **More urgent, and now insufficient as scoped.** | The same matrix's Tier-1/Tier-2 **"Model download"** row is *wrong on this machine*: it says "→ `~/.friday/local_voice/`" (correct per code, V1) while the assets are at `runtime/` and nothing reads them (V4). A reader trusting that row concludes local voice is provisioned. The edit should be widened: add the Tier-3 provider column **and** an ASR-mode row (`transcribe` / `understand`) with the download row corrected against R1. Doing 11.1 without that leaves the most-read table in the voice docs accurate about cloud and misleading about local. |
| **11.2 — §7.2 fallback rows + §7.3 no-auto-promotion clause** | Needed | **Substantially more urgent, and its scope must grow by one row.** | Two reasons. (a) §7.2's `local-gpu` row promises "Tier2 → Tier1 (**banner**) → text-only"; on this machine that path degrades onto a tier that hangs (V2 + §4.4), so the row documents a fallback that does not work — R3.3's "degradation checks its target" belongs in the same edit. (b) **The `auto` row is in direct conflict with the same edit's own §7.3 clause**: it permits "(cloud only if key present AND not local-only)" while `cloud-voice-providers.md` §6.3 forbids promoting to cloud on local failure without consent. Whichever of §4.5's R4.1/R4.2/R4.3 the maintainer picks (§11 Q5), that row changes. Applying 11.2 without touching it ships a table that contradicts the clause being added two paragraphs below it. |
| **11.3 — §10.2 CI matrix gains `voice_offline_no_cloud`** | Needed | **Most urgent of the three, and its meaning has changed.** | It was proposed as a guard against future cloud accretion. Given V2 it is also the **only** test that would have caught the current state, and it fails today (§7.2). Adding it converts an unnoticed absence into a red build — which is the point. It should be added **with** the §7.1 local tests, so the failure is legible rather than mysterious. |

**Net: all three become more urgent, and 11.1 needs widening.** None of them is contradicted by this document. Additionally, the `elevenlabs-voice.md` amendment (cloud spec §11.4/11.5) is unaffected — this document touches no Tier-3 decision.

One further note, offered rather than specified: the voice-mode audit recorded a live contradiction between the ElevenLabs decision (Gemini Live decided) and a local default in `routes/voice.py` — "Both are load-bearing and they cannot both be right." The cloud spec resolved the *documentation* side (Gemini Live is the Tier-3 default, Tier 1 is the product default, no conflict). **Both of item 8's citations have since rotted**: `routes/voice.py:497` is now `_voice_orb_start()`'s docstring, and `elevenlabs-voice.md` §6 has no numbered subsections, so "§6.2" resolves to nothing. The code side should be re-located and confirmed during R4 — and this is a small worked example of why R0.2's receipts matter more than line-number citations, which decay every refactor.

---

## 11. Open questions — the owner's calls, not engineering's

These are the ones a spec cannot settle. Each names the consequence of each answer, so that answering is a decision rather than a preference.

**Q1 — Does the two-mode design hold, or should native audio replace transcription?**
Specified as two modes (§0.3), and the reasoning is stated so an override is informed. What replacement buys: every turn carries prosody, one path to maintain, a simpler settings page. What it costs: dictation degrades from 11.48% to 13.15% WER, and from ~16% to ~41% in noisy conditions (I5) — a change from "usable in a kitchen" to "not." **If the answer is replacement, this document needs a different §5 and the WER figures need independent verification first** (they are INHERITED, §1.2).

**Q2 — Is long-form listening a product goal?**
The 30 s ceiling (I4) makes "explain this whole thing to Friday out loud" impossible in `understand` mode as specified. Stitching is refused on quality grounds (§5.6). If long-form matters, it needs its own design — probably a two-pass shape where transcription carries the words and native audio samples the delivery — and that is a materially bigger build than what is specified here.

**Q3 — May the audio-in seat share the brain's `llama-server` instance, or must it be isolated?**
Isolation is safer (P2) and costs a second resident model on a machine already contended by training runs (P3). Sharing halves the memory and couples voice availability to brain residency, so an unrelated model change becomes a voice outage. This is a resource-allocation call about *this* machine's real budget, which the maintainer knows and the document does not.

**Q4 — Which Piper voice is Friday's default: `amy-medium` (declared) or `lessac-medium` (provisioned)?**
R1.5 refuses to let the loader infer this from what happens to be on disk. It is a change to the registry and the Tier-1 default binding (`core/__init__.py:1655`), which means it is a decision about what Friday sounds like — and that is a product question wearing a filename.

**Q5 — What does `auto` mean, and may it reach cloud?**
This is not a tidy-up; it is a live three-way conflict between `voice-system-spec.md` §7.2 (which permits `auto` → cloud), `cloud-voice-providers.md` §6.3 (which forbids promoting to cloud on local failure without consent), and the voice-mode audit's open item 1 (which leaves it open). §4.5 lays out R4.1 (local-only), R4.2 (remove it), R4.3 (offer, never hop). Each requires a different edit to §7.2 and a different §7.1 test. **Answering this unblocks step 3 of §8**; leaving it open means shipping a setting that means something different from what the spec says it means.

**Q6 — Should `understand` mode be *offered* contextually, and if so on what trigger?**
P4 lost the default (§2.4) but the concern survives: a mode nobody discovers is a mode nobody uses, and the build is then wasted. A contextual offer is possible — but every trigger anyone would propose (long utterance, detected emotion, ambiguity) is a form of content inspection driving a UI change, which sits uncomfortably beside C2's spirit even though it violates no letter of it. Specified conservatively as settings-only (§5.5) pending an answer.

**Q7 — Do the inherited figures need independent verification before they carry design weight?**
I1–I6 are the load-bearing facts of §5 and none was verified for this document (§1.2). I3 (the 2026-06-05 GGUF cutoff) is already converted into a runtime check that fails safe (§5.7). I5 (the WER figures) currently justifies the entire two-mode design — and if Q1 is ever revisited, it should be revisited against a measurement rather than a restatement.

---

## 12. Sources

**Repository — read 2026-09-08, `main` @ `3c58538`**

- `docs/design/active/voice-system-spec.md` — §2 tier matrix, §7.1–7.4 preference/fallback, §7.3 transition-notice rule, §8 error taxonomy, §9 egress, §10 verification gate and CI matrix, §3 AC-ID convention (D-AC3)
- `docs/design/active/cloud-voice-providers.md` — §1.0 constraints C1–C3, §3.1 capability keywords, §3.3 split-modality refusal, §4.3 selection-time disclosure, §4.4 the indicator, §6.3 surfaces-and-offers, §6.4 `voice_offline_no_cloud`, §11 the three unapplied edits
- `docs/design/active/elevenlabs-voice.md` — decisions (A)(B)(C); §4.6 egress gap (referenced, not modified)
- The voice-mode audit (2026-08-21) — "running the wrong voice architecture"; the indefinite local-cascade hang; `auto` semantics; `local_ok` vouches for two thirds of the pipeline
- The provisioning audit (2026-08-13) — the `runtime/whisper-models`, `runtime/piper-voices`, `runtime/venv-voice` paths, disk, and "**Nothing was wired into Friday.**"
- The 2026-08-25 voice triage — the receipts contract and the consumer rule
- `docs/user-guide/local-voice-gpu-tier.md` — Tier-2 interface contract, `MIN_VRAM_GB`, degradation promise, manual-test procedure
- `docs/decisions/2026-09-04-five-dead-settings.md` — the failure mode a capability-gated control avoids
- `docs/README.md` — status-header convention; "**Code beats documentation**"
- `src/agent_friday/services/local_voice.py:43` (`voice_assets_dir` import), `:71-73` (`LOCAL_VOICE_DIR`), `:343-358` (`WhisperASR._download_root`, download target), `:413-430` (`PiperTTS._voice_path`, download target), `:550` (shared tier interface), `:599-621` (`resolve_tier`), `:698-708` (`models_ready`), `:725-728` and `:749-767` (two of the three degradation points; the third is in `resolve_tier`), `:741-746` (GPU load order), `:751`, `:764` (the two `print()` calls)
- `src/agent_friday/services/nemo_voice.py:57-73` (model ids, `NEMO_DIR`, `MIN_VRAM_GB`), `:225-255` (contention probe), `:226-231` (its non-gating contract)
- `src/agent_friday/services/provider_registry.py:48` (`ROLE_VOICE`), `:530` (`local-voice-lite`), `:557` (`nvidia-nemo`)
- `src/agent_friday/routes/voice.py:905-944` (`session-info` tier resolution), `:916-921` (brain-seat requirement), `:1214` (`/ws/voice-local`), `:1640` (`/ws/live`), `:1658-1670` (`voice_debug.log`)
- `src/agent_friday/routes/chat.py:87-88` (stderr "simply lost in production"); `routes/voice.py:2272` (the tray DEVNULLs stdio)
- `src/agent_friday/core/__init__.py:857-881` (logging convention and rotation), **`:1992-1993`** (Tier-1 default ASR/TTS bindings — note `cloud-voice-providers.md` §8.1 cites `:1655` for this and is wrong, V16), `:1874`, `:2490-2495`, `:2967` (Ollama)
- `src/agent_friday/services/local_call.py:64-106`, `services/gguf_extract.py`, `services/agent.py:357`, `services/residency_arbiter.py`, `services/local_seats.py` (`llama-server` machinery)
- `src/agent_friday/paths.py:127-172` (`friday_home`, `runtime_dir`, `voice_assets_dir`)
- `pyproject.toml:44-121` (voice extras; Tier-1 rides `[all]`, GPU group excluded)
- Filesystem, `%USERPROFILE%\.friday` — `local_voice/` absent; `runtime/piper-voices/en_US-lessac-medium.onnx`; `runtime/whisper-models/models--mobiuslabsgmbh--faster-whisper-large-v3-turbo/`; `models/nemo/` without checkpoints; `server_stderr.log` 135,976 KB

**Inherited, not verified here** (§1.2): `llama-server` audio-in feasibility; Ollama audio instability; llama.cpp PR #24118 (2026-06-04) and the 2026-06-05 GGUF cutoff; Gemma 4 audio availability on E2B/E4B/12B, 16 kHz mono, 30 s ceiling; WER 13.15% vs 11.48% clean and ~41% vs ~16% noisy.
