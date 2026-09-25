# Voice, from a clean sheet — how Friday's voice subsystem should have been built

> **Status:** active
> **Last verified:** 2026-09-16
> **Implementation:** none yet — this is the target architecture; §12 is the build order for the implementing session
> **Supersedes / superseded by:** supersedes the *design* in [`voice-system-spec.md`](voice-system-spec.md), [`local-voice-repair-and-native-audio.md`](local-voice-repair-and-native-audio.md) §4–§5 and [`voice-mode-diagnosis-and-repair.md`](voice-mode-diagnosis-and-repair.md); keeps [`cloud-voice-providers.md`](cloud-voice-providers.md) §10.0 decisions and [`elevenlabs-voice.md`](elevenlabs-voice.md) decision (B) as settled; the three constraints C1–C3 are inherited verbatim (§1.3)
> **Written:** 2026-09-16
> **Method:** clean-sheet design against measured facts; every number carries a provenance tag

## Implementation notes

- **This is not a bug list.** It is what the voice subsystem looks like if it were designed today, knowing everything measured this month. Where the current tree already has the right piece, §11 says so and the builder keeps it. Where it has the wrong shape, §11 says what replaces it.
- **Provenance tags.** **MEASURED-2026-09-16** — measured today on the owner's machine while writing this. **RECEIPT-2026-09-10** — from `friday.log` turn receipts. **MEASURED-2026-09-12** — the seat VRAM ladder in `residency_arbiter.py`. **TREE** — read from the code today. **VENDOR** — a third party's claim, not reproduced here. A number with no tag is an engineering target, not a fact.
- **Nothing in this document is built.** No code changed, no dependency was installed, and no model was downloaded while writing it. The two timing probes in §2.4 ran read-only against packages and assets already on disk, on an idle GPU (the FridayWeaver seat was not running: no `llama-server.exe`, nothing listening on 8090–8130, 1,280 MiB in use — **MEASURED-2026-09-16**).

---

**One-line thesis:** *Voice is a proven pipeline, not a configured one: every stage proves it can do its job before it is described, every byte of GPU it takes is a lease that ends with a process, and the model is handed exactly the tool list the user is shown — so the three ways voice lied this week become impossible to express rather than merely fixed.*

---

## 0. What this document decides

Ten decisions, so the rest of the document can be read as their consequences.

| # | Decision | Where argued |
|---|---|---|
| **D1** | **Readiness is proof, not presence.** A stage is "ready" only when a probe has actually transcribed, thought, or spoken within a TTL, and the proof (latency, device, model) is what the UI, the API and the model's own self-description all read from. One object, the **Voice Manifest**. | §3.1, §6 |
| **D2** | **GPU memory for voice is leased and process-bound.** Every GPU-resident voice engine runs in a child *voice worker* under a lease from `services/arbiter.py`; release is the worker exiting. Admission uses the conservative `nvidia-smi` figure against the 2,560 MiB display reserve. One GPU job at a time. | §3.2, §5 |
| **D3** | **The tool list is a contract, computed before the session and shown to the user.** The floor tools are reserved before any budget is spent; a seat that cannot hold system + tools + loop reserve + reply refuses the session with the arithmetic, and the prompt's tool section is generated from the same list handed to the API. | §3.3 |
| **D4** | **The brain is the FridayWeaver seat, unchanged.** No new local LLM is shipped (no Qwen3, no second GGUF). The local voice model is the same agentic pipeline text chat uses, on the seat already on port 8095 at 131,072 context. | §4.3, §4.6 |
| **D5** | **Streaming end to end, with a prefix-stable prompt.** Tokens stream from the seat into a clause chunker into a streaming synthesizer; the static prompt prefix is byte-identical between turns so llama-server's prefix cache makes per-turn prefill small. This, not a faster model, is where the latency goes. | §4.2, §4.4 |
| **D6** | **Ear and mouth default to what the seat leaves free.** ASR: faster-whisper on CUDA when a lease is granted (0.2 s per utterance, **MEASURED-2026-09-16**), CPU int8 at beam 1 otherwise (2.2 s). TTS: Kokoro-82M on CUDA when a lease is granted; on the CPU, Piper is the floor and Kokoro (torch or ONNX) is offered only where it measures fast enough. NeMo is demoted to experimental. | §2.4, §4.3 |
| **D7** | **Cloud voice is realigned as *local brain, cloud mouth*.** Gemini Live stays, as the duplex option the user chooses. It gains one tool, `ask_friday`, that dispatches the question to the local agent pipeline with the full tool surface and the knowledge graph; the answer is egress-gated before it is relayed. Cloud voice can then reach the user's context honestly, through the local model. | §4.5 |
| **D8** | **A custom tune is not required to ship this, and the trigger for one is written down.** If, after §12 Phase 3, the measured spoken-register metrics miss their bars, the next FridayWeaver training run gets a voice-register SFT pass — on the *same* line, not a new model. | §4.6 |
| **D9** | **voicebox: borrow the patterns, do not adopt it, do not vendor it.** Per-model unload becomes D2's process-bound lease; the serial queue becomes the GPU job queue; LuxTTS is a *candidate* engine behind the engine interface, gated by measurement. | §10 |
| **D10** | **One WebSocket contract, one settings surface, one indicator.** `/ws/voice` for local, `/ws/live` for cloud, identical frames; a Voice Stack card with three rows (Ear / Mind / Mouth) that shows selected → effective → proven; an in-session HUD with three lamps and the reach and egress lines. | §7, §8 |

---

## 1. Requirements

### 1.1 Stated requirements

- **R1 — Fast local voice with a real model in the loop.** Not a bare STT→TTS pipe.
- **R2 — Tool calling is non-negotiable.** Local voice always involves a model in the loop that can call tools and do research in the knowledge graph. A speech-to-speech model with a reduced tool set does not satisfy this.
- **R3 — Gemini Flash Live stays supported.** Cloud voice is made honest, not removed.
- **R4 — Do not bundle Qwen3.** Use the seat that exists (FridayWeaver, `:8095`).
- **R5 — A custom tune or a local/cloud realignment is acceptable if justified.**
- **R6 — UI support is part of the spec.** Settings surfaces, state indicators, what the user sees while each thing happens, what they are told when something degrades.

### 1.2 Derived requirements

- **R7 — The three failure classes of the week are structurally impossible** (§3): a component describing itself inaccurately; resources taken and never returned; capability silently reduced.
- **R8 — Disk is a binding constraint.** C: has **15.1 GB free of 931 GB** (**MEASURED-2026-09-16**, `System.IO.DriveInfo`), and the residency layer refuses new loads below a 10 GiB system-volume floor (`DISK_FLOOR_MIB`, **TREE**). The whole design must fit in **≤ 3 GB of new downloads**, and §2.5 budgets it at well under 1 GB.
- **R9 — Nothing on the local path may block on the network** (C3, inherited).
- **R10 — Every runtime selection leaves a receipt in `friday.log`** (the 2026-08-25 receipts principle; `voice_receipt.py` already does this and is kept).

### 1.3 Inherited constraints, unchanged

From `cloud-voice-providers.md` §1.0, verbatim: **C1** both paths always, the user chooses, no default routes to cloud; **C2** transparency, no silent fallback, indicators reflect what *served*; **C3** the offline path is never load-bearing on a network call. Settled 2026-09-09 and kept: `auto` means local-only; `local` terminates and never falls through to cloud; local failure *surfaces and offers* cloud, never substitutes.

### 1.4 Non-goals

- Wake word, multi-language local voice, speaker identification (biometric; needs its own decision record).
- Long-form (> 30 s) native-audio listening. Native audio-in stays the opt-in `understand` mode of the prior spec and is not on this document's critical path (§4.7).
- Replacing Gemini Live's duplex path with a cascade. Decision (B) of `elevenlabs-voice.md` stands.
- Cloud STT. Refused by `cloud-voice-providers.md` §3.3; not reopened.

---

## 2. The machine and the numbers

### 2.1 Hardware

| | Value | Tag |
|---|---|---|
| GPU | RTX 4070, 12,282 MiB; **9,722 MiB usable** after the 2,560 MiB Windows display reserve (`MIN_DISPLAY_RESERVE_MIB["windows"]`) | TREE, MEASURED-2026-09-12 |
| CPU | Intel i7-10700F, 8 cores / 16 threads | MEASURED-2026-09-16 |
| RAM | 32,620 MB | MEASURED-2026-09-16 |
| Disk | C: 15.1 GB free of 931 GB; 10 GiB residency floor | MEASURED-2026-09-16, TREE |
| CUDA in venv | torch 2.13.0+cu126, cuDNN 9.10, `torch.cuda.is_available()` True; **CTranslate2 4.8.1 sees the GPU** (`int8_float16`, `float16` supported); ONNX Runtime 1.26 is **CPU-only** (no CUDA provider) | MEASURED-2026-09-16 |
| Voice packages present | faster-whisper 1.2.1, piper-tts 1.4.2, kokoro 0.7.16 (+ misaki, espeakng-loader), silero-vad 6.2.1, nemo-toolkit 3.0.0, onnxruntime 1.26.0. **Not** present: kokoro-onnx, webrtcvad | MEASURED-2026-09-16 |
| Voice assets present | `~/.friday/local_voice/whisper/models--Systran--faster-whisper-small` (~460 MB), `~/.friday/local_voice/piper/en_US-amy-medium.onnx` (63 MB), `~/.friday/runtime/kokoro-onnx/model_q8f16.onnx` (86 MB, no voices file), `~/.friday/models/nemo/` **empty** | MEASURED-2026-09-16 |

### 2.2 The brain seat

| | Value | Tag |
|---|---|---|
| Seat | `gemma4:e2b-fridayweaver-1.0` — Gemma 4 E2B Q8_0 (4.95 GB, served over `\\wsl.localhost`) + FridayWeaver-1.0 LoRA at serve time, **mmproj present** (vision + audio tower) | TREE (`~/.friday/runtime/models/models.json`) |
| Context | 131,072, `-b 512 -ub 512`; `MAX_SEAT_NUM_CTX = 131072` | TREE |
| VRAM ladder | no seat 1,791 MiB · 32k 5,398 · 64k 5,622 · 128k 6,082 MiB total in use → the seat itself is ~4,291 MiB at 128k, leaving **~5.9 GB** under the usable line | MEASURED-2026-09-12 |
| Tool surface | 75 registry tools; at 32k the budget trimmed to 40–49 and dropped `knowledge_query` on 2026-09-10; at 128k nothing is trimmed | TREE (`tool_budget.py`) |
| Prompt size | assembled text-chat system prompt ~13,550 tokens; 75 tool declarations 12,438 tokens (measured via `/tokenize`) — so a voice turn's prefill is **~26k tokens** before the transcript | TREE (`tool_budget.py` comments, measured 2026-09-09) |
| Status right now | **not running** (see Implementation notes). The brief calls it "the seat on 8095"; `models.json` records its last manual port as 8713. The Arbiter cannot relaunch it because its spawn path does not pass `--lora` yet | MEASURED-2026-09-16, TREE |

### 2.3 What the last real local sessions cost — the receipts

From `friday.log`, **RECEIPT-2026-09-10**, times in ms since VAD close (`t_input_complete` = transcript ready; `t_first_brain_token` = brain returned, non-streaming; `t_first_audio_out` = first PCM frame sent):

| Session / tier | ASR | Brain | TTS to first audio | Total to first audio |
|---|---|---|---|---|
| CPU tier (faster-whisper small int8 on CPU; Kokoro on CUDA; brain `gemma4:e2b-friday-v1`), 10 turns | **4,561–5,190** | 3,400–15,300 | 2,500–13,300 (warm turns 2,500–4,800) | **10,692–24,642** |
| GPU tier (Nemotron streaming ASR on CUDA; FastPitch+HiFi-GAN on CUDA), 3 turns | **147–199** | ~5,400 | 300–500 | **6,116–8,662** |

Read: on the CPU tier the ear alone costs five seconds, the brain three to fifteen, and the mouth two and a half even when warm. On the GPU tier the ear is essentially free and the brain is the whole bill. Nothing here is a model-quality problem.

Cold loads (**voice-mode-diagnosis-and-repair.md**, measured 2026-09-10): Kokoro torch 39–56 s. NeMo ASR held ~4 GB for five hours after one session and starved the display to 448 MiB.

### 2.4 Today's probes — what is achievable with what is already on disk

Probe: a 6 s Piper-synthesized utterance ("What is on my calendar tomorrow morning, and did Priya reply…"), faster-whisper `small` from the on-disk cache, beam 1, three runs each; then Kokoro (torch) on CPU, two sentences, first-chunk and total. Script: `scratchpad/probe_voice.py`; run on the idle GPU. **MEASURED-2026-09-16.**

| Stage | Engine / device | Load | Per run (warm) | Notes |
|---|---|---|---|---|
| mouth | Piper `en_US-amy-medium`, CPU | — | 1.27 s for 5.9 s of audio → **4.7× realtime** | first synthesis in the process; includes espeak warm-up |
| ear | faster-whisper `small`, **CPU int8**, beam 1 | 2.7 s | **2.21–2.29 s** for 5.9 s audio | correct transcript. The 4.6–5.2 s in §2.3 is the same model with the default beam 5 and the VAD filter; **voice must run beam 1** |
| ear | faster-whisper `small`, **CUDA int8_float16** | 1.6 s | 0.60 s first, then **0.21–0.22 s** | correct transcript; torch reports 0 MiB allocated afterwards (CTranslate2 owns its own memory — the worker model in §3.2 is how it is returned) |
| ear | faster-whisper `small`, **CUDA float16** | 0.9 s | **0.18–0.20 s** | best; ~10× the CPU figure |
| mouth | Kokoro-82M (torch) `af_heart`, **CPU** | 3.1 s | **1.44 s to first chunk** for 2.25 s of audio → **1.6× realtime** | usable only behind the clause pipeline; the 39–56 s cold load of 2026-09-10 was the CUDA path (torch CUDA init), not this |
| mouth | Kokoro-82M (torch), CPU, sentence 2 | — | **FAILED** `TypeError: unsupported operand type(s) for +: 'NoneType' and 'str'` inside the pipeline | a robustness hazard in the g2p path: the mouth must catch a per-clause failure and speak that clause with Piper (§4.3) |
| mouth | Kokoro via `kokoro-onnx`, CPU int8 | — | **not measured** (not installed; voices file absent) | Phase 2 measures it before it can become a default |

What the probes decide: the ear goes on the GPU whenever a lease is free (a 10× win for ~1 GB), the CPU ear runs at beam 1, and Kokoro's CPU path is too slow to be the default mouth — the mouth also prefers the GPU, with Piper as the always-present floor (§4.3). What they do not decide: end-to-end first-audio, which depends on the seat (§4.4) and is the Phase 2 acceptance measurement in §12.

### 2.5 Budgets derived from the above

**VRAM plan when everything is granted** (targets, against 9,722 MiB usable):

| Tenant | MiB | Basis |
|---|---|---|
| FridayWeaver seat @128k | ~4,300 | MEASURED-2026-09-12 |
| faster-whisper small, CUDA float16 | ~600–900 | **VENDOR** class figure for a 244M-parameter model in fp16 with CTranslate2 buffers; the worker reports the real resident set on first load (§3.2 rule 2) |
| Kokoro torch, CUDA | ~400–600 | RECEIPT-2026-09-10 session ran it beside the seat without a display incident; exact figure is a Phase 1 measurement |
| Headroom | ≥ 3,900 | |

The seat is never displaced by voice (§5.4). If the ASR or TTS lease is refused, that stage runs on CPU and says so.

**Disk plan (new downloads, all optional):**

| Item | Size | Needed for |
|---|---|---|
| Kokoro-82M voices file for the ONNX path (`voices-v1.0.bin`) | ~27 MB | the CPU-mouth candidate in §4.3, measured in Phase 2 |
| `kokoro-onnx` wheel | < 5 MB | same |
| Nothing else | — | whisper small, Piper Amy, Kokoro torch are present |

Total ≤ 40 MB. NeMo checkpoints (1.5 GB) are **not** downloaded; LuxTTS (§10) is not installed until its gate passes.

---

## 3. The three failure classes, made structurally impossible

Each of this week's failures is a *shape*, and each shape gets a mechanism that cannot express it, not a check that catches it.

### 3.1 "A component describing itself inaccurately" → the Voice Manifest (D1)

**What happened.** Friday told the owner it was running "faster-whisper and Kokoro on CPU" while `voice_engine` was `gemini` and Kokoro's CPU opt-in was off — a pipeline that cannot exist. Readiness said "ready" because a package imported. The three surfaces — settings panel, `/api/voice/session-info`, and the model's own words — each reconstructed the truth from different inputs, and they disagreed.

**The mechanism.** One object, `services/voice_manifest.py`, is the *only* source of facts about the voice stack. It has three stages — `ear`, `mind`, `mouth` — and for each:

```
{
  "selected":  {"engine": "kokoro", "device_policy": "gpu_if_leased"},   # what the user asked for
  "effective": {"engine": "kokoro-onnx", "device": "cpu", "model": "kokoro-v1.0 int8", "voice": "af_heart"},
  "proof":     {"state": "proven", "at": "2026-09-16T09:12:04", "ttl_s": 900,
                "latency_ms": 310, "sample": "1.8 s of audio from a 9-word line"},
  "reason":    "",            # non-empty whenever effective != selected, or state != proven
  "action":    null           # {"label": "...", "kind": "install|download|settings|retry"} when the user can fix it
}
```

Rules that make the lie inexpressible:

1. **`proof.state` has three values — `unproven`, `proving`, `proven` — and `refused` with a code.** "Proven" is set only by `prove()` actually running the stage: the ear transcribes a bundled 2 s WAV and must return the expected words; the mind sends a one-line request to the seat with the *real* tool list and must receive a completion with `timings`; the mouth synthesizes a fixed 9-word line and must return non-empty PCM of plausible duration. Importability, file existence and `torch.cuda.is_available()` are inputs to `effective`, never to `proof`.
2. **Proofs expire.** `ttl_s` defaults to 900 s idle and is re-run on `voice/arm` (mic click) if expired. A proof older than its TTL renders as `unproven (last proven 14 min ago)`, never as ready.
3. **The model's self-description is generated from the manifest.** The voice system prompt's first paragraph — today a constant string claiming "ON-DEVICE speech-to-text and text-to-speech" — becomes `manifest.describe_for_model()`, e.g. *"Your ears are faster-whisper small on the GPU, your reasoning is FridayWeaver running locally, your voice is Kokoro on the CPU. All three run on this machine; nothing leaves it."* — or, on the cloud path, *"You are Gemini Live; the microphone audio is sent to Google. Questions about the user's own notes, memory or knowledge graph are answered by their local model through the `ask_friday` tool."* The model cannot claim a pipeline the manifest does not hold, because it has no other source.
4. **`/api/voice/session-info`, the Settings card, the HUD, and the session-start `manifest` frame are all `manifest.snapshot()`.** No surface computes its own version.
5. **A test pins it:** `test_manifest_cannot_be_proven_without_running` — monkeypatch every engine's `run()` to raise; assert no stage reaches `proven`, and `describe_for_model()` says so in words.

### 3.2 "Resources taken and never given back" → leased, process-bound residency (D2)

**What happened.** NeMo ASR loaded ~4 GB of CUDA memory in the server process and never released it; torch does not reliably return device memory to the driver, and no admission check honoured the display reserve. The display fell to 448 MiB.

**The mechanism.**

1. **Voice engines that use the GPU run in a child process, `friday-voice-worker`**, one per engine, speaking a tiny length-prefixed JSON/PCM protocol over stdio (§5.2). Releasing the memory is `worker.terminate()`; the driver reclaims everything a dead process held. This is voicebox's per-model unload done the only way that is actually guaranteed on Windows/CUDA.
2. **Every worker holds a lease** from `services/arbiter.py` (`acquire("gpu_vram", mib, holder="voice:ear", evictable=True, ttl_s=...)`). Lease expiry or eviction kills the worker. The lease's `amount` is the engine's declared working set, and the worker reports its *measured* resident set after load so the declaration is corrected on the next run.
3. **Admission is conservative and reserve-aware.** Before a GPU lease, `vram_headroom(reserve_mib=resolve_display_reserve(profile)["mib"])` must be `ok` using the `nvidia-smi` free figure — the fix F2 already made in `nemo_voice.gpu_status()` becomes the *only* admission path. A refusal is a sentence: *"GPU voice not loaded: 2,100 MiB free against a 2,560 MiB display reserve. Running the ear on the CPU instead."*
4. **Idle unload.** A worker with no job for `voice_idle_unload_s` (default 600) exits and releases its lease. The Settings card shows the countdown. Arming the mic re-proves and re-loads; the cold-load cost is shown as a determinate "loading…" state (§8.3), never paid silently on the first utterance.
5. **One GPU job at a time.** A single `GpuQueue` serialises ear and mouth jobs (a job is one transcription or one clause synthesis). It is a queue, not a lock, so a barge-in can cancel queued synthesis jobs.
6. **Voice never evicts the brain seat or a foreign hold.** The seat is pinned; a training run's `declare_foreign_hold` shuts the GPU to voice (the arbiter already refuses `gpu_vram` under a `gpu_exclusive` hold). Voice yields to CPU and says so.
7. **Tests:** `test_voice_worker_exit_releases_lease` (lease state becomes `released` when the worker's pipe closes); `test_gpu_admission_refuses_on_conservative_figure` (torch=11.5 GB / nvidia-smi=2.0 GB refuses — the F2 test, kept); manual GPU-gated: after a session and idle timeout, `nvidia-smi` shows no `python.exe` voice worker and free memory returns to within 100 MiB of the pre-session reading.

### 3.3 "Capability silently reduced" → the capability contract (D3)

**What happened.** The tool budget, fitting 75 tools into a 32k window with a 17.6k prompt, ranked well and then dropped whatever did not fit — including `knowledge_query`, the model's own route into the knowledge graph. The model was still told it had it.

**The mechanism.**

1. **The contract is computed before the session starts, not per turn.** `voice_manifest.mind.contract` = `{tools: [names], floor_present: true, knowledge_graph: true, memory: true, window: 131072, prompt_tokens: 26,410, loop_reserve: 6144, reply_cap: 300, fits: true}`. It uses `tool_budget.fit_tools_to_seat()` with the *real* voice system prompt and asks the seat's `/tokenize` for the true count (`measure_request()` exists).
2. **The floor is reserved first** (`_FLOOR_TOOLS` — `knowledge_query`, `search_wiki`, `read_wiki`, `search_web` — already implemented in `tool_budget.py`, kept). If the floor cannot fit, `fits` is false and the session **refuses** with the arithmetic in the reason: *"This seat holds 32,768 tokens; the voice prompt needs 26,410 and the reply reserve 6,144. There is no room for Friday's tools. Raise the seat's context or shorten the prompt."* Refusing beats a mute assistant.
3. **The prompt's tool section is rendered from `contract.tools`** and nothing else. `_voice_tool_surface_note()` (TREE) already does this for the cloud path; the local path gets the same generator so the model is never told about a tool the API was not handed.
4. **The contract is sent to the client as a `contract` frame at session start and shown on the HUD** ("75 tools · knowledge graph ✓ · memory ✓"). The existing `context_reach` frame is folded into it.
5. **Turn-time trimming is forbidden on the voice path.** If a turn's transcript grows past the window, the turn *compacts the transcript* (`compaction.maybe_compact`, exists) — never the tools. `test_voice_contract_never_trims_floor` asserts the API tool list on every round of a 30-turn synthetic session contains the floor.

---

## 4. Architecture

### 4.1 Topology

```
 browser ── /ws/voice (local) ─────────────┐        ┌── /ws/live (cloud, Gemini Live) ── Google
   │  mic PCM16@16k ↑ │ ↓ PCM16@24k        │        │      ▲ ask_friday tool → local agent pipeline
   │  same frames on both routes            ▼        ▼      │   (egress-gated text back)
   │                                 VoiceSession (state machine, one per socket)
   │                                   │           │            │
   │                              ┌────┴───┐  ┌────┴────┐  ┌────┴────┐
   │  Voice Manifest ◄─ proofs ── │  EAR   │  │  MIND   │  │  MOUTH  │ ── receipts ─► friday.log
   │  (one object, §3.1)          │ VAD +  │  │ agent   │  │ clause  │
   │                              │ ASR    │  │ pipeline│  │ chunker │
   │                              └───┬────┘  │ on the  │  └───┬─────┘
   │                                  │       │ seat    │      │
   │                     ┌────────────┴───┐   │ :8095   │  ┌───┴────────────┐
   │                     │ voice worker   │   └────┬────┘  │ voice worker   │
   │                     │ (child proc,   │        │       │ (child proc)   │
   │                     │  GPU lease)    │        │       │  or in-proc CPU│
   │                     └────────────────┘        │       └────────────────┘
   │                          ▲                    │                ▲
   │                          └──── services/arbiter.py leases ─────┘
   │                                   (gpu_vram; display reserve; foreign holds)
```

Three stages, one session object, one manifest, one lease broker. The cloud route shares the session object and the frames; it differs only in which engines fill the three stages (§4.5).

### 4.2 The turn, streamed end to end (D5)

Today's turn is strictly staged: the whole utterance is transcribed after VAD closes, the whole reply is generated before a byte is spoken, and the whole sentence is synthesized before it is sent. Every stage waits for the previous one to finish completely. The redesign overlaps them.

```
 user speaks ─────────────────┐
   VAD open        VAD close  │
   │   chunked ASR │          │   (ear: transcribe 2 s windows while speech continues;
   │   ▓▓▓▓ ▓▓▓▓ ▓▓│▓         │    at close only the tail is left)
   │               │ tail     │
   │               └─► final transcript ──► MIND: prefix-cached prefill + streaming tokens
   │                                          │ "Pulling that up now." ─► MOUTH clause 1 ─► audio ▶
   │                                          │ [tool call] ...
   │                                          │ "Your morning is clear," ─► clause 2 ─► audio ▶
   │                                          │ "and Priya replied an hour ago." ─► clause 3 ▶
   barge-in at any point: cancels queued clauses, sends {interrupted}, reopens the mic
```

**Stage contracts.**

- **Ear.** `VADEndpointer` (Silero when available, RMS fallback — kept) opens and closes the utterance. New: *speculative chunked transcription* — while speech is open, every 2 s window is transcribed and the partial text is sent as `{type:"partial_transcript"}`; at close, only audio since the last chunk boundary is transcribed and the pieces are re-joined by a final full-utterance pass **only if** the utterance is ≤ 8 s (the chunk seams are the accuracy risk; a short utterance is re-done whole because it is cheap). Endpoint-to-final-transcript target: **≤ 300 ms on CUDA, ≤ 1,200 ms on CPU** for a 5 s utterance.
- **Mind.** `_generate_agent` with `session_ctx={"is_voice": True, ...}` (kept), plus two changes: (a) a `on_text_delta` callback threaded through `_oai_agentic_loop` → `send_fn` so the *final* round streams (`stream: true` on the OpenAI-compatible leg; tool-call rounds stay non-streaming because their content is consumed whole); (b) the voice prompt is assembled prefix-stable (§4.4). Reply cap stays `_voice_reply_cap()` (300 tokens default).
- **Mouth.** A clause chunker cuts the token stream at `. ! ? ; :` and at `,` after ≥ 6 words, with a 12-word hard cut, so the first clause synthesizes while the model is still writing. Each clause is one `GpuQueue` job (or in-process on CPU). Output is 24 kHz PCM16 mono in `PLAYBACK_CHUNK_BYTES` frames — the wire format the client worklet already plays. Target first-audio after first clause complete: **≤ 400 ms**.
- **Barge-in.** The client's `{type:"barge"}` (exists) cancels queued mouth jobs, sets the turn's cancel flag so the mind's stream is abandoned, sends `{type:"interrupted"}` (exists; the frame that reopens the client's playback gate), and marks the receipt `outcome: aborted`.

**Latency budget** (targets; the acceptance measurements are §12 Phase 2):

| Segment | Target, GPU ear + GPU mouth | Target, CPU ear + CPU mouth |
|---|---|---|
| VAD close → final transcript | 300 ms (0.2 s measured per whole utterance) | 800 ms (tail only; 2.2 s measured per whole utterance at beam 1) |
| transcript → first token (warm prefix) | 700 ms | 700 ms |
| first clause complete → first audio byte | 400 ms (Kokoro CUDA) | 500 ms (Piper) · ≤ 1,000 ms (Kokoro CPU, 1.6× realtime measured) |
| **first audible word after the user stops** | **≈ 1.5 s** | **≈ 2.5 s** (Piper) · **≈ 3 s** (Kokoro CPU) |
| tool turn: announcement sentence audible before the tool starts | yes | yes |

Against RECEIPT-2026-09-10's 10.7–24.6 s, that is the difference between a voice assistant and a voicemail.

### 4.3 Engines (D6)

The engine interface is one small ABC per stage (`ear.Engine.transcribe(pcm16_16k) -> str` with an optional `transcribe_partial`; `mouth.Engine.synthesize_stream(text) -> iter[bytes]`), so adding an engine is a module plus a manifest entry, not a branch in the session.

**Ear (ASR).**

| Engine | Device | Verdict | Why |
|---|---|---|---|
| **faster-whisper `small`, CTranslate2** | **CUDA int8_float16 (primary when leased)** | **default** | Same package, same on-disk model, no download; CTranslate2 already sees the GPU in this venv (**MEASURED-2026-09-16**); §2.4 has the numbers. |
| faster-whisper `small` | CPU int8 | **floor** | Works today; §2.3 shows ~4.7 s per utterance whole, which the chunked ear in §4.2 hides behind the user's own speech. |
| Nemotron 3.5 streaming 0.6B via NeMo | CUDA | **experimental, off by default** | 147 ms ear (**RECEIPT-2026-09-10**) is the best number in this document, but it cost 4 GB never returned, 1.5 GB of checkpoints not on disk, and `nemo-toolkit` in the hot path. Revisit *only* under D2's worker model; the code stays behind the interface. |
| Parakeet-TDT 0.6B via sherpa-onnx | CPU | candidate, **unmeasured** | Apache-2.0, Windows wheels, int8 model available (**VENDOR**). Worth measuring if the CPU ear ever has to be the default on a machine without CUDA. Not installed. |

**Mouth (TTS).**

| Engine | Device | Verdict | Why |
|---|---|---|---|
| **Kokoro-82M via `kokoro` (torch)** | **CUDA when leased (default policy: `if free`)** | **default voice** | The voice the owner already uses (`af_heart`), 2.9–10.3× realtime on the card (**voice-mode-diagnosis-and-repair.md**); 39–56 s cold load on CUDA and a torch process resident on the card → lives in a worker under a lease (D2), loaded at arm time with a visible progress state, unloaded on idle. |
| Kokoro-82M (torch) | CPU | **option, not default** | **1.6× realtime, 1.44 s to first chunk** (**MEASURED-2026-09-16**) — behind the clause pipeline that is ≈ 1 s to first audio, tolerable, not fast. Also threw a `TypeError` inside its g2p on the second probe sentence, so **every clause synthesis is wrapped and a failed clause is spoken by Piper** (the clause-fallback rule), announced once per session. |
| Kokoro-82M via `kokoro-onnx` | CPU int8 | **candidate, measured in Phase 2** | Apache-2.0 model, MIT wrapper, ~80 MB int8, needs the ~27 MB voices file (§2.5). If it measures ≤ 500 ms first-chunk on this CPU it becomes the CPU default over Piper; if not, it is offered as an option with its measured number beside it. The `model_q8f16.onnx` already in `runtime/kokoro-onnx/` is an ONNX-community export whose loader differs; the builder uses the `kokoro-onnx` pair unless it proves the on-disk export loads. |
| Piper `en_US-amy-medium` | CPU | **floor and CPU default** | Present, **4.7× realtime** on this CPU (**MEASURED-2026-09-16**), 22 kHz, robotic. Never removed; it is what speaks when everything else is refused, and what speaks a clause Kokoro failed on. |
| NeMo FastPitch + HiFi-GAN | CUDA | experimental, off | Same reasons as Nemotron. |
| LuxTTS | CUDA (< 1 GB) / CPU | **candidate behind a gate** | See §10. Apache-2.0, 48 kHz, cloned from a ≥ 3 s reference — an *owned* Friday voice with no vendor lock, which is the one thing `cloud-voice-providers.md` Q1 wanted and could not get from ElevenLabs. Its requirements pull `lhotse`, `vocos`, `librosa`, `transformers`, a git-installed codec (**VENDOR**, requirements.txt read 2026-09-16): several hundred MB and an unpinned tree. Not installed until its gate (§10.3) passes. |
| Cloud TTS (ElevenLabs) | network | kept as built | "Local listening, cloud voice", `cloud_voice.py`, unchanged. |

**Mind.** The FridayWeaver seat (D4). Nothing else. §4.6 is the only place a model change is contemplated.

**Why both default to the GPU, and why the ear is admitted first.** The ear's cost is paid *while the user waits* and the GPU makes it ten times smaller (0.2 s against 2.2 s). The mouth's cost overlaps the model's token stream and the audio already playing, so a CPU mouth is *tolerable* — but at 1.6× realtime it is the difference between 0.4 s and 1 s to the first word, which the user hears every turn. The seat leaves ~5.9 GB free (§2.2); both fit. When only one can be admitted, the ear wins (§5.4), and the mouth falls to Piper with a notice.

### 4.4 The prompt: prefix-stable, cache-verified (D5)

The seat runs one llama-server slot with `cache_prompt` on (`context-assembly.md` §, **TREE**): the server reuses the longest byte-identical prefix of the previous request. A ~26k-token voice prompt whose first 26k tokens never change between turns costs a few hundred tokens of prefill per turn. One that changes at token 4,200 (the minute-resolution clock, `context-assembly.md`) costs the whole 26k every turn — which is a large share of the 3–15 s brain times in §2.3.

The voice prompt is therefore assembled in this order, and the order is a test:

1. `manifest.describe_for_model()` — changes only when a proof changes (§3.1).
2. Persona and the voice rules (length, no markdown, `VOICE_TOOL_CHOREOGRAPHY` — kept verbatim).
3. Tool declarations, in registry order (the OpenAI `tools` field is part of the prefix as llama-server templates it).
4. The stable context: `_get_friday_system_prompt(...)` with its volatile block moved *after* `prompt_cache.VOLATILE_MARKER` (the mechanism exists).
5. Everything volatile: the clock, `_build_session_continuity_block()`, `_build_emotional_tone_block()`, the transcript.

**Verification is built in, not assumed:** the session reads llama-server's `timings` from each completion; `prompt_n` (tokens actually prefilled) is written into the turn receipt as `prefill_tokens`, and the HUD's debug drawer shows it. Acceptance: on turn 2 of any session, `prefill_tokens` < 2,000. If the seat is shared with text chat and a text turn intervenes, the prefix is lost for one turn; that is accepted and visible. (Running the seat with `--parallel 2` to give voice its own slot halves each slot's window; it is a Phase 3 measurement, not a Phase 1 change.)

### 4.5 Local brain, cloud mouth: the realignment (D7)

Gemini Live is a duplex model: ears, mind and mouth in one hop, with barge-in and prosody nothing local matches. It also has 16 fixed tools and no path to the knowledge graph, memory, or the vault — which is exactly why it "utterly failed to reach my context". The two paths are not competitors for the same job; they are good at different halves of it.

**The realignment:** cloud voice keeps its duplex ears and mouth, and *borrows the local mind for anything about the user*.

- One new Gemini Live tool, **`ask_friday(question: string)`** — "Ask Friday's local model, which has full access to the user's notes, memory, knowledge graph, files, calendar and email. Use it for any question about the user's own context, and for anything that needs a tool you do not have."
- Its handler runs `_generate_agent` on the FridayWeaver seat with the *full* contract (§3.3), `session_ctx={"authenticated": ..., "provider": "local", "is_voice": True}`, reply cap 300, and returns the text — **after `egress_gate` seals it for `google-gemini`** (`_gate_voice_tool_result` exists and already withholds rather than partially redacts). The vault's TIER_2/3 content therefore never crosses: the local model reads it, the sealed answer is what Google sees.
- Choreography: Gemini announces ("Let me ask Friday."), calls the tool, and speaks the sealed answer. The local agent's 3–15 s (§2.3; faster after §4.4) sits inside a tool call that the duplex model narrates naturally.
- Honesty: the `contract` frame for a cloud session reads *"16 native tools + ask_friday → your context is reached through Friday's local model"*, and the manifest's `describe_for_model()` says so to Gemini. The egress line (`egress_notice`, exists) stays on screen for the whole session.
- Local voice never uses cloud for anything (C1–C3). The realignment is one-directional by design.

**What this is not.** It is not a cascade replacing the duplex model (decision (B) intact), and it is not a way for Gemini to run local tools directly — it asks a question and receives a sealed answer.

### 4.6 The custom tune question (D8)

**Verdict: not required to ship, and the decision rule is written down so it is a measurement, not a mood.**

The local mind's two jobs in voice are (1) call the right tool with the right arguments and (2) answer in spoken register — short, no markdown, announce-then-act. FridayWeaver-1.0 already went from 57.4 % to 98.1 % lenient on held-out tool calling (`frontier-on-12gb-deepseek-derived.md`); (1) is handled. (2) is currently a prompt rule, and the receipts show it mostly works — 32 to 179 characters per reply on 2026-09-10 — with the 794-character outliers being error text, not the model.

**Trigger for a voice-register SFT pass** (measured over 100 real voice turns after Phase 3):

| Metric | Bar | If missed |
|---|---|---|
| median spoken reply length | ≤ 60 words | tune |
| replies containing markdown or list syntax | ≤ 2 % | tune |
| tool turns where the announcement sentence precedes the call | ≥ 95 % | tune |
| strict tool-call accuracy on the t2-single harness | must not drop by > 1 point after tuning | reject the tune |

The recipe if triggered: the *same* FridayWeaver line, an additional ~2k-example SFT set of spoken-register transcripts (announce → tool → confirm → one-to-three-sentence answer), trained under `Friday-Models` (do-not-modify constraint: needs the owner's go), scored on the served GGUF+LoRA runtime — the endpoint scoring mode that document already says is missing. No new base model. No Qwen3 (R4).

### 4.7 Native audio-in

The FridayWeaver seat's GGUF carries the audio tower (`mmproj.gguf`, **TREE**). The prior spec's `understand` mode — audio straight into the model, opt-in, 30 s ceiling, `native_audio.py` decision layer — is compatible with this architecture as a second *ear* engine (`ear: native-audio`) that bypasses the mouth's text input in the same way. It is not on the critical path; §12 does not schedule it.

---

## 5. Resource model

### 5.1 Leases

Every GPU-resident voice engine is a row in `services/arbiter.py` (`gpu_vram`, `holder="voice:ear"` / `"voice:mouth"`, `evictable=True`, `ttl_s=voice_idle_unload_s`), renewed on each job, released on worker exit. `plan_eviction` may evict a voice lease to serve an image job or a heavy turn; the eviction produces `local_voice_gpu_evicted` (§7.3) and the stage falls to CPU **with a notice** — never silently.

### 5.2 The voice worker

`friday-voice-worker` is a small Python entry point (`python -m agent_friday.voice.worker --engine kokoro-cuda`) launched by the session's engine factory. Protocol over stdio, length-prefixed frames: `{"op":"load"} → {"ok":true,"resident_mib":512}`, `{"op":"synth","text":...} → n × PCM frames → {"op":"done"}`, `{"op":"transcribe"} + PCM → {"text":...}`, `{"op":"cancel"}`. The parent holds the lease; the child never talks to the arbiter. A child that stops answering within `stage_budget_ms` is killed (the per-stage watchdog of the prior spec's R2.2, now with a process to kill). CPU engines (Piper, Kokoro-ONNX, whisper-CPU) may run in-process; the interface is the same.

### 5.3 The GPU queue

One `GpuQueue` per process serialises jobs to GPU workers. A job carries `turn_id`; barge cancels every job of that turn. The queue exposes depth to the HUD's debug drawer. It is not a mutex around the seat — the seat is a separate process with its own server; the queue only orders Friday's *voice* work.

### 5.4 Precedence

1. Foreign holds (a training run) — voice never competes; the arbiter already refuses.
2. The brain seat — never displaced by voice.
3. Image jobs and heavy turns — may evict voice leases (announced).
4. Voice ear, then voice mouth — the ear is admitted first because its latency is user-visible (§4.3).

### 5.5 Disk

Engine downloads go through the existing allowlisted `voice_installer` job with a pre-flight against `DISK_FLOOR_MIB`; the Settings card shows the size before the click. Nothing downloads on a code path the mic click can block on.

---

## 6. The session state machine and the wire contract (D10)

### 6.1 States

```
 idle ──arm──► proving ──all proven──► ready ──socket open──► listening
                 │  any stage refused                         │  VAD open
                 ▼                                            ▼
              refused (code, reason, action)               hearing (partial transcripts)
                                                              │  VAD close
                                                              ▼
                              ◄──── turn_end ────── speaking ◄── thinking (first token)
                                                      ▲            │ tool call
                                                      └── acting ◄─┘
 any state ──stage lost (lease evicted, worker died)──► degraded (stage → CPU, one notice) ──► back to the state it was in
```

`degraded` is a transient overlay, not a terminal: the session continues on the CPU engine and the HUD lamp for that stage turns amber with the reason.

### 6.2 Frames

Kept unchanged (client already handles them): `audio`, `text`, `input_transcript`, `status`, `turn_end`, `voice_turn_done`, `interrupted`, `hb`, `error`, `action`, `cite`, `tts_pause`/`tts_resume`, `egress_notice`, `egress_receipt`.

New:

| Frame | When | Payload |
|---|---|---|
| `manifest` | session start, and whenever a proof changes | `manifest.snapshot()` (§3.1) |
| `contract` | session start | §3.3 contract; replaces `context_reach` (kept for one release as an alias) |
| `stage` | each transition | `{stage: "ear"|"mind"|"mouth", state: "idle"|"busy"|"degraded"|"refused", detail}` |
| `partial_transcript` | during speech | `{text}` |
| `error-nonfatal` | any `PREFERRED → ACTIVE` change | `{code, message, action}` — the prior spec's one-notice rule, now the only degrade frame |
| `turn_receipt` | turn end | the `TurnReceipt` record (exists) plus `prefill_tokens`, `clauses`, `first_clause_ms` |

Client → server, kept: `audio`, `text`, `end`, `barge`, `conversation`, `image`. Auth is unchanged (token / cookie / loopback).

### 6.3 Routes

`/ws/voice-local` is renamed `/ws/voice` with the old path kept as an alias for one release. `/ws/live` is unchanged in transport and gains `ask_friday` (§4.5). `/api/voice/session-info` returns the manifest snapshot plus `ws_url`. `POST /api/voice/arm` replaces `/api/voice/warm`: it runs `prove()` for all three stages (single-flight), and `GET` reports per-stage progress. `POST /api/voice/prove` forces a re-proof (the Settings "Prove voice now" button).

---

## 7. Failure taxonomy

Shape unchanged from `voice-system-spec.md` §8: `{code, user_message, action}`. Existing codes are kept. New or re-scoped:

| Code | When | User message | Action |
|---|---|---|---|
| `voice_stage_unproven` | mic armed and a stage's proof failed or expired | "Friday couldn't prove her {ear/voice/reasoning} works right now: {detail}." | Prove again / open Voice settings |
| `voice_contract_does_not_fit` | §3.3 arithmetic fails | "The local model's window can't hold Friday's tools alongside this conversation ({numbers})." | Raise seat context (link) |
| `local_voice_brain_absent` | no resident seat (kept from prior spec) | "Local voice can hear you, but no local model is loaded to answer." | Load the model |
| `local_voice_gpu_refused` | admission refused | "GPU voice not loaded: {free} MiB free against a {reserve} MiB display reserve. Using the CPU for {stage}." | none needed; informational |
| `local_voice_gpu_evicted` | lease evicted mid-session | "Friday's {stage} was moved off the GPU to make room for {holder}. Continuing on the CPU." | none |
| `local_voice_turn_timeout` | stage watchdog fired | "Voice stopped responding during {stage}. Nothing was sent anywhere." | Retry / self-test |
| `voice_worker_died` | child exited unexpectedly | "Friday's {stage} engine crashed and was restarted on the CPU." | Report (log path) |
| `cloud_voice_context_via_local` | cloud session start | "Gemini Live is speaking; questions about your own context are answered by Friday's local model and relayed after the privacy gate." | none; persistent line |

Every one is *surfaced and offered*. None changes engine, provider or mode without the user.

---

## 8. UI (R6)

`index.html` is the served file and the only one edited; `ui_parts/app.html` is a hand-maintained mirror nothing builds from (**TREE**, its own header note) and is left alone.

### 8.1 Settings → Voice

Replaces `SettingsTabVoice` + `TtsEngineRows`. Three sections.

**A. Mode** (one row of three buttons; the `voice_engine` setting, values `local` | `gemini` | `local-listen-cloud-voice`; `auto` is removed from the UI and read as `local`):

- **Local** — "Ears, reasoning and voice on this machine. Nothing leaves it."
- **Cloud (Gemini Live)** — "Google's speech-to-speech model. Your microphone audio goes to Google. Questions about your own context go through Friday's local model." Greyed with reason when no valid key, offline, or local-only mode.
- **Local listening, cloud voice** — "Friday hears and thinks here; ElevenLabs speaks. Only the reply text leaves." Greyed with reason when no key / GA rule (`cloud_voice.available_providers()`, exists).

Selecting a cloud mode shows the four-line disclosure from `cloud-voice-providers.md` §4.3 before it takes effect (exists for ElevenLabs; extended to Gemini with the §4.5 line).

**B. Voice Stack card** — the manifest, rendered. Three rows:

```
 EAR    faster-whisper small · GPU (fp16)      ● proven 09:12 · 0.20 s      [engine ▾] [GPU: if free ▾]
 MIND   FridayWeaver-1.0 · :8095 · 131k         ● proven 09:12 · 75 tools · KG ✓ · memory ✓
 MOUTH  Kokoro af_heart · GPU                  ● proven 09:12 · 0.28 s      [engine ▾] [voice ▾] [GPU: if free ▾]
        idle unload in 9:40                                                 [Prove voice now]
```

Row states: **● proven** (green, with time and latency) · **◐ proving…** (with elapsed seconds and the stage's progress text) · **○ unproven — last proven 14 min ago** (grey) · **✕ refused** (red, reason, action button). When `effective ≠ selected` the row reads *"You chose Kokoro on GPU; serving Kokoro on CPU — 2,100 MiB free against a 2,560 MiB reserve."* Idle-unload countdown shows on a GPU row while its worker is resident.

Engine pickers list every engine the manifest knows, greyed with a reason when unavailable (`kokoro-onnx: voices file not downloaded (27 MB) — Download`). The GPU policy per stage is `never` | `if free` | `required` (`required` refuses the session rather than falling to CPU — for the user who would rather wait than hear Piper).

**C. Behaviour** — silence endpoint (`voice_silence_ms`), reply length (`voice_max_tokens`), idle unload minutes, interruption mode (kept), and the existing Voice Setup Wizard for installs.

**Dead-settings rule** (`docs/decisions/2026-09-04-five-dead-settings.md`): every control above ships with the code that reads it and a test that fails if that code is removed. `auto` is deleted from the picker because it is now a synonym.

### 8.2 In-session HUD

Beside the mic button:

```
 ● LIVE · Local          EAR ● MIND ◐ MOUTH ●        75 tools · KG ✓ · memory ✓
 "what's on my calendar tomorrow morning and did ja…"        (partial transcript, italic)
 ☁ MIC → Google (Gemini Live) · 2.1 MB                        (cloud sessions only, not dismissible)
```

- The three lamps are the `stage` frames: green idle/ready, blue busy, amber degraded (hover shows the reason), red refused.
- The contract line is the `contract` frame.
- The partial transcript replaces the old blank wait; it becomes the `input_transcript` line at VAD close.
- Cloud sessions keep the egress line for the whole session and show the byte receipt after (`egress_receipt`, exists).
- A degrade shows one persistent toast with the taxonomy's message and action; it does not replace the status line and is not re-shown for the same code in the same session.
- A debug drawer (`FRIDAY_VOICE_DEBUG`, exists) lists per-turn: the five timestamps, `prefill_tokens`, clause count, GPU queue depth, and the engines that served.

### 8.3 What the user sees over time

| Moment | What is shown |
|---|---|
| Opens Settings → Voice | The card renders instantly from the last snapshot (grey rows if proofs are stale) and starts proving in the background; rows flip to green one by one with their latency. |
| Clicks the mic with stale proofs | Mic button shows "proving…" with the slowest stage's progress ("loading voice engine, 12 s"); the socket opens only when `ready`. The 40–56 s Kokoro cold load, if that engine is chosen, is *seen* here once, never paid silently on the first sentence. |
| Speaks | Partial transcript grows under the HUD; EAR lamp blue. |
| Stops | Transcript settles; MIND lamp blue; first clause is audible ~1.5 s later on the GPU-ear path. |
| A tool runs | "Pulling that up now." is heard before the tool starts (choreography); the process orb (exists) shows the tool; MIND lamp stays blue. |
| Interrupts | Audio stops within one clause; status "listening". |
| GPU taken by an image job | One toast: "Friday's ear moved to the CPU to make room for image generation." EAR lamp amber. Replies keep coming, slower. |
| Cloud mode, asks about own notes | Gemini says "Let me ask Friday," the HUD shows MIND busy with "asking local model", the answer is spoken; the egress line never went away. |
| Something is refused | The mic button itself carries the state ("setup needed") and opens Settings → Voice at the red row, with the action button. The raw code never renders alone. |

---

## 9. Verification

### 9.1 Tests (CI, no GPU, no network)

| Test | Asserts |
|---|---|
| `test_manifest_cannot_be_proven_without_running` | §3.1 rule 5 |
| `test_manifest_is_the_only_source` | `session-info`, the settings payload and `describe_for_model()` are byte-derived from one `snapshot()`; a monkeypatched proof changes all three |
| `test_describe_for_model_never_names_unproven_engine` | with the mouth `refused`, the description says so and names no engine for it |
| `test_gpu_admission_refuses_on_conservative_figure` | kept (F2) |
| `test_voice_worker_exit_releases_lease` | §3.2 rule 7 |
| `test_gpu_queue_barge_cancels_turn_jobs` | queued clauses for turn N vanish on barge |
| `test_voice_contract_reserves_floor_first` | floor tools survive any window; `fits=false` when they cannot |
| `test_voice_contract_never_trims_floor` | over a 30-turn synthetic session |
| `test_voice_prompt_is_prefix_stable` | two consecutive assembled prompts share their prefix up to the volatile marker; the clock is after it |
| `test_clause_chunker` | boundaries, hard cut, no empty clauses |
| `test_streaming_final_round_only` | tool rounds non-streaming, final round streams deltas |
| `test_ask_friday_is_egress_gated` | the tool's return passes `_gate_voice_tool_result`; a withheld string is returned whole, never partial |
| `test_cloud_contract_names_ask_friday` | §4.5 honesty line |
| `test_resolve_engine_auto_is_local` / `..._local_terminates` | kept; the pre-existing failing `test_session_info_falls_back_to_cloud_when_local_missing` (`tests/api/test_voice_local.py`) contradicts the settled 2026-09-09 decision and is **rewritten** to assert termination |
| `voice_offline_no_cloud` | kept and extended: proving does no network I/O |
| `test_dead_setting_*` for each §8.1 control | dead-settings rule |

### 9.2 Live acceptance (manual, GPU-gated, recorded in the doc when run)

1. Arm from cold: all three rows reach proven; time each.
2. Ten spoken turns on the local path, GPU ear: median first-audible-word ≤ 1.5 s; `prefill_tokens` < 2,000 from turn 2.
3. Same with GPU policy `never`: median ≤ 2.5 s.
4. A tool turn (calendar): announcement audible before the orb appears; `knowledge_query` present in the contract.
5. Barge mid-reply: silence within one clause; receipt `aborted`.
6. Start an image job mid-session: one toast, EAR amber, replies continue.
7. Idle 10 min: worker gone from `nvidia-smi`; free VRAM back within 100 MiB.
8. Cloud mode: ask about a wiki page; hear "let me ask Friday"; the sealed answer; the egress line present throughout.
9. `friday.log` alone reconstructs each turn (the prior spec's R0 acceptance).

---

## 10. voicebox: adopt, vendor, or borrow? (D9)

`github.com/jamiepine/voicebox` — MIT, Tauri + Rust shell over a FastAPI backend, React/Zustand front end, SQLite; engines Qwen3-TTS, Qwen CustomVoice, LuxTTS, Chatterbox (multilingual and Turbo), TADA, Kokoro; STT via Whisper (base→large, turbo); per-model unload; a serial execution queue; an MCP server (`voicebox.speak`, `voicebox.transcribe`, `voicebox.list_captures`, `voicebox.list_profiles`); global dictation hotkey with auto-paste on macOS only, Windows/Linux "planned" (**VENDOR**, README read 2026-09-16).

### 10.1 Adopt over MCP — no

`voicebox.speak` gives Friday a mouth and `voicebox.transcribe` an ear; neither gives her a mind. R1/R2 are about the model in the loop, which voicebox does not have and does not claim to. Adopting it would also mean a second FastAPI process, a second model store on a disk with 15 GB free, a second torch tree, and a UI Friday does not control — while the two things it solves well (unload, serial queue) are ten lines each once the worker model exists. Its dictation feature, the one user-facing thing Friday lacks, is not shipped on Windows.

### 10.2 Vendor engines — one candidate, gated

LuxTTS is the only engine on its roster that changes Friday's options: 48 kHz, < 1 GB VRAM, Apache-2.0, cloned from a short reference, so Friday's voice can be *hers* — a recorded reference on disk, no vendor account. The brief's "150× realtime on CPU" is not what the README says: it claims 150× on a single GPU and "faster than realtime" on CPU (**VENDOR**). Its dependency list (§4.3) is heavy and unpinned.

### 10.3 The LuxTTS gate

Install into a *separate* venv under `runtime/` (not Friday's), on a day with ≥ 12 GB free, then: (1) synthesize the manifest's proof line on CPU and on CUDA, record RTF, first-chunk latency, resident MiB; (2) A/B the 48 kHz output against Kokoro on three sentences with the owner listening; (3) confirm the streaming shape (its README does not mention streaming). It ships as `mouth: luxtts` only if CPU first-chunk ≤ 500 ms or CUDA resident ≤ 1,000 MiB with first-chunk ≤ 250 ms, *and* the owner prefers the sound. Otherwise the record of the measurement is the deliverable.

### 10.4 Borrow — yes

- Per-model unload → D2's process-bound lease, which is stronger than an in-process unload.
- Serial execution queue → `GpuQueue` (§5.3).
- Engine roster as data with a uniform interface → the engine ABCs (§4.3) and the manifest.
- MCP `speak` → Friday already exposes `voice_health`/`voice_state` over `friday-core`; a `voice_speak` MCP tool that routes through the mouth stage and the egress gate is a cheap Phase 3 addition so subagents can speak without a second stack.

---

## 11. What survives, what changes, what goes

| Piece | Verdict | Note |
|---|---|---|
| `services/voice_receipt.py` (routing + turn receipts) | **keep**, extend with `prefill_tokens`, `clauses` | already the R0 channel |
| `services/voice_indicator.py` | keep | fed from the manifest |
| `services/tool_budget.py` floor + `_surface_override` | keep | the contract wraps it |
| `services/arbiter.py` leases, `hardware_profile.vram_headroom`, `headroom_contract.resolve_display_reserve` | keep | D2's admission path |
| `services/nemo_voice.gpu_status()` conservative verdict + cache | keep the probe, retire it as an admission authority | admission moves to §3.2 rule 3 |
| `local_voice.VADEndpointer`, `WhisperASR`, `PiperTTS`, `split_sentences` | keep behind the ear/mouth ABCs | `split_sentences` is replaced by the clause chunker on the streaming path |
| `kokoro_voice.KokoroTTS` | keep as the `kokoro-cuda` worker engine | runs in a child process |
| `LocalVoiceEngine` tier machinery (`resolve_tier`, `select_tier`, `effective_tts`, `peek_tier`) | **replace** by the manifest | tiers become per-stage engine + device policy |
| `routes/voice.py` `ws_voice_local` | **rewrite** as `VoiceSession` (§6) in `services/voice_session.py`; the route becomes thin | keeps auth, `_persist_voice_turn`, `_spawn_voice_distill`, `_voice_actions_for`, `_voice_reply_cap`, the system-prompt gating by brain provider |
| `_resolve_voice_engine` | rewrite to read the manifest; the `auto`/`local` decisions are kept verbatim | |
| `/api/voice/warm` | becomes `/api/voice/arm` | |
| `native_audio.py` | keep | future ear engine |
| `cloud_voice.py`, `cloud_voice_routes.py`, `voice_engine.py` Live tools | keep; add `ask_friday` | |
| `nemo_voice.NeMoASR/NeMoTTS` | keep, off by default, `experimental` in the picker | |
| `ui_parts/app.html` | untouched | |

---

## 12. Build order — for the implementing session

The implementer is a **fresh Claude Code session on Fable 5.1 at low reasoning effort**. Each phase is a commit that leaves the tree working; no phase lands half-wired. Run `venv/Scripts/python.exe -m pytest tests/unit tests/api tests/gauntlet -x -q` with `FRIDAY_TESTING=1` after each. Use the `friday-dev` launch config (port 3210) for browser checks, never the production port.

### Hazards, first

- **Do not disturb `llama-server.exe`** if it is running on 8095 (it was not on 2026-09-16 — see Implementation notes). Do not start it with ad-hoc commands; if a live check needs the seat, ask the owner, or use the Arbiter's own spawn once it can pass `--lora`. Keep `MAX_SEAT_NUM_CTX = 131072`.
- **Never broad-kill `python.exe`/`pythonw.exe`.** Friday's tray and server are Python. Voice workers are stopped by PID through their lease.
- **Do not restart WSL.** The seat's GGUF is mmapped over `\\wsl.localhost`.
- **Disk: 15.1 GB free, 10 GiB floor.** The only permitted downloads are §2.5's (≤ 40 MB). Do not install LuxTTS, NeMo checkpoints, or any torch wheel.
- **Do not revert the uncommitted work in the tree** (tool_budget floor, agent.py refusal passthrough, intelligence.py R5 fix, local_image.py fit check, index.html WebGL context-loss recovery, nemo_voice caching, the 131072 raise, the six voice-mode fixes). `index.html` is CRLF in the tree against LF at HEAD — review with `git diff -w`; commit voice changes on their own.
- Python changes need a Friday restart; coordinate rather than bouncing the production server.

### Phase 0 — Manifest and proofs (D1)  *no engine changes*

1. `services/voice_manifest.py`: the object, `prove()` per stage using the *existing* engines (WhisperASR CPU, PiperTTS, KokoroTTS, seat via `local_seats.resolve("brain")` + one `/v1/chat/completions` with `max_tokens: 8` and the real tools), TTL, `snapshot()`, `describe_for_model()`.
2. `/api/voice/session-info` and `/api/voice/arm` (`GET`/`POST`) read/run it; `/api/voice/warm` aliases `arm`.
3. `ws_voice_local`: send `manifest` + `contract` at session start; the voice prefix's first paragraph becomes `describe_for_model()`.
4. Settings: replace `TtsEngineRows` with the Voice Stack card (§8.1 B), read-only pickers for now.
5. Tests: `test_manifest_*`, `test_describe_for_model_*`. Rewrite the contradicting `test_session_info_falls_back_to_cloud_when_local_missing`.

**Gate:** with the Kokoro `run` monkeypatched to raise, Settings shows MOUTH refused and `describe_for_model()` says so; nothing reports ready that has not run.

### Phase 1 — Leases and the worker (D2)

1. `agent_friday/voice/worker.py` + `services/voice_workers.py` (spawn, protocol, lease acquire/renew/release on exit, idle unload, `GpuQueue`).
2. Move `KokoroTTS` (CUDA) and faster-whisper (CUDA, `int8_float16`) behind the ear/mouth ABCs as worker engines; CPU engines in-process.
3. Admission through `vram_headroom(reserve_mib=resolve_display_reserve(...))` + `nemo_voice.gpu_status(fresh=True)`'s conservative figure; refusal → `local_voice_gpu_refused`.
4. Tests: `test_voice_worker_exit_releases_lease`, `test_gpu_queue_barge_cancels_turn_jobs`, F2 kept.

**Gate:** manual, GPU: session → idle → `nvidia-smi` shows the worker gone and VRAM returned.

### Phase 2 — The streamed turn (D5, D6)

1. `services/voice_session.py`: the state machine, chunked ear, clause chunker, streaming mind (`on_text_delta` through `_generate_agent` → `_oai_agentic_loop` → the OpenAI-compatible `send_fn` with `stream: true` on the final round), receipts with `prefill_tokens` from `timings`.
2. Prefix-stable prompt assembly (§4.4) and `test_voice_prompt_is_prefix_stable`.
3. Clause-fallback rule (a failed Kokoro clause is spoken by Piper, one notice per session). Then measure `kokoro-onnx` on this CPU (download the voices file through `voice_installer`, allowlisted, size shown); it becomes the CPU default only if first-chunk ≤ 500 ms, and the number is written into §2.4 either way.
4. `/ws/voice` (alias `/ws/voice-local`); HUD lamps, contract line, partial transcript (§8.2).

**Gate:** §9.2 items 2–5 measured and written into this document's §2.4.

### Phase 3 — Cloud realignment and polish (D7, D8, D9)

1. `ask_friday` in `_VOICE_LIVE_TOOLS` + handler + egress gating + the cloud contract line. `test_ask_friday_is_egress_gated`.
2. Settings Mode picker with disclosures; dead-settings tests; remove `auto` from the picker.
3. The spoken-register metrics (§4.6) collected from receipts (word count, markdown flag, announce-before-tool flag) and shown in the debug drawer; the tune decision is taken from real numbers.
4. Optional, measured: `--parallel 2` on the seat; `voice_speak` over `friday-core` MCP; the LuxTTS gate (§10.3) only if disk allows.

**Gate:** §9.2 item 8; the taxonomy table's messages all reachable from a test.

---

## 13. Open questions for the owner

Only three; everything else is decided above.

- **Q1 — GPU policy defaults.** This document defaults both ear and mouth to `if free`, which means two voice workers (~1.5 GB together) resident beside the seat while voice is armed, and a 40–56 s Kokoro load the first time after each idle period, shown as progress. If you would rather keep the card for the seat alone, set the mouth to `never` and hear Piper.
- **Q2 — LuxTTS as Friday's owned voice.** §10.3's gate needs ≥ 12 GB free disk for a throwaway venv and ten minutes of your listening. Worth scheduling, or park it?
- **Q3 — The seat's own launch.** The Arbiter cannot relaunch the FridayWeaver seat (`--lora` not passed). Voice depends on that seat being up. Making the Arbiter own it is a small residency change outside this document's scope; say if the voice builder should do it.

---

## 14. Sources

**Repository, read 2026-09-16, `main` @ `3c58538` plus the uncommitted tree** — `routes/voice.py` (`ws_voice_local`, `_resolve_voice_engine`, `_voice_context_reach`, `_local_brain_ready`, `VOICE_TOOL_CHOREOGRAPHY`, `_voice_tool_surface_note`), `services/local_voice.py`, `services/nemo_voice.py`, `services/kokoro_voice.py`, `services/voice_receipt.py`, `services/voice_indicator.py`, `services/native_audio.py`, `services/cloud_voice.py`, `services/voice_engine.py` (`_VOICE_LIVE_TOOLS`, `_build_voice_live_tools`, `_voice_tool_run`), `services/tool_budget.py`, `services/agent.py` (`_generate_agent`, `_via_ollama`, `_oai_agentic_loop`), `services/local_seats.py`, `services/residency_arbiter.py` (`MAX_SEAT_NUM_CTX`, the ladder, `grant()`), `services/arbiter.py`, `services/hardware_profile.py`, `services/headroom_contract.py`, `services/machine_monitor.py`, `services/provider_registry.py`, `services/local_call.py`, `index.html` (voice client, `SettingsTabVoice`, `TtsEngineRows`, HUD), `tests/api/test_voice_local.py`, `tests/gauntlet/test_voice_offline_no_cloud.py`, `.claude/launch.json`; `~/.friday/runtime/models/models.json`; `~/.friday/friday.log` (2026-09-10 receipts).

**Design record** — `voice-mode-diagnosis-and-repair.md` (F1–F6), `frontier-on-12gb-deepseek-derived.md`, `local-voice-repair-and-native-audio.md`, `cloud-voice-providers.md`, `elevenlabs-voice.md`, `voice-system-spec.md`, `context-assembly.md`, `docs/decisions/2026-09-04-five-dead-settings.md`, `docs/DECISIONS.md`.

**External, read 2026-09-16** — github.com/jamiepine/voicebox (README); github.com/ysharma3501/LuxTTS (README, `requirements.txt` on `master`); github.com/thewh1teagle/kokoro-onnx; github.com/SYSTRAN/faster-whisper; github.com/k2-fsa/sherpa-onnx.


---

## 15. Measurements appended by the implementing session (2026-09-16)

Appended, not rewritten (build rule 8). Tags as in the Implementation notes.

### 15.1 §2.4 additions

| Stage | Engine / device | Result | Tag |
|---|---|---|---|
| ear proof asset | Piper `en_US-amy-medium` → `resources/voice_proof.wav`, 2.55 s, 16 kHz | faster-whisper `small` CPU int8 beam 1 transcribes it **exactly** (`Friday, what time is it right now?`): load 10.1 s cold, 3.93 s transcribe on a loaded machine (the suite was running concurrently) | MEASURED-2026-09-16 |
| mouth | Kokoro via `kokoro-onnx` 0.6.1, CPU | **NOT MEASURED.** The on-disk `runtime/kokoro-onnx/model_q8f16.onnx` (ONNX-community export, 86 MB) **segfaults ONNX Runtime 1.26.0 in `InferenceSession()` on the CPU provider** — reproduced with bare `onnxruntime`, so it is the export, not the wrapper. The wrapper's own release model is an 88 MB download, outside this phase's ≤ 40 MB budget (§2.5), so it was not fetched. What was fetched: the `kokoro-onnx` wheel (26 kB, installed `--no-deps` because it declares `phonemizer`, which shares an import name with the `phonemizer-fork` the Kokoro torch path uses) and `voices-v1.0.bin` (28.2 MB) via the allowlisted `voice_installer` target `kokoro-onnx`. **Piper stays the CPU default**; `kokoro-onnx` is an installer target whose model file is a separate, sized decision. | MEASURED-2026-09-16 |
| worker protocol | `friday-voice-worker --engine fake`, child spawn → `load` reply | 0.1 s | MEASURED-2026-09-16 |

§9.2 items 2–5 (end-to-end first-audio, prefill_tokens on turn 2, tool-turn choreography, barge) **were not measured**: the FridayWeaver seat was not running and the build rules forbid starting it; no browser was available to the implementing session. They remain the live GPU acceptance.

## 16. Deviations (code wins; spec noted)

| # | Spec says | Tree / decision | Why |
|---|---|---|---|
| D-1 | §6.3: `/api/voice/warm` becomes `/api/voice/arm` | `/api/voice/warm` is **kept** as the F5 engine pre-loader (its tests in `tests/api/test_voice_mode_repair_routes.py` pin that shape: `state: loading/ready/skipped`, `engine`, `tier`); `/api/voice/arm` (GET/POST) and `/api/voice/prove` are **added** and run the proofs. | Arming proves; warming loads. Aliasing would have silently changed a tested contract. |
| D-2 | §3.1 rule 1: the mind proof "must receive a completion with `timings`" | A completion with content or tool_calls proves the mind; `timings.prompt_n` is recorded when present and becomes `effective.prefill_tokens`. | Only llama-server returns `timings`; an Ollama-served seat would never prove. |
| D-3 | §4.2: "tool-call rounds stay non-streaming" | Every round streams on the OpenAI-compatible leg (the transport already did); the session's `DeltaFilter` withholds everything from `<\|` to `\|>`, so a tool round streams exactly its announcement sentence and nothing after the call. `test_streaming_final_round_only` pins that. | The announcement sentence is what the choreography wants audible *before* the tool runs; a round's finality is unknowable until it ends. |
| D-4 | §12 Phase 0 item 4: "replace `TtsEngineRows` with the Voice Stack card, read-only pickers for now" | The card replaces `TtsEngineRows`; its engine/voice/GPU-policy pickers are **live** (they save the same settings the old rows did). | Disabling working controls to satisfy "read-only for now" would have removed a feature. |
| D-5 | §11: `services/arbiter.py` "keep" | It was an **untracked** file in the working tree (never committed), as was `tests/unit/test_arbiter.py`. `services/arbiter.py` is committed in Phase 1 (with `renew()`/`lease_state()` added) because the leases depend on it; the test file and `routes/arbiter.py` are left untracked. | Cannot depend on an uncommitted module. |
| D-6 | §3.2 rule 2: "the worker reports its measured resident set … so the declaration is corrected" | Measured as the CUDA free-memory delta across `load()` inside the child (`torch.cuda.mem_get_info`), rounded up to 64 MiB, persisted in `~/.friday/local_voice/working_sets.json`. For CTranslate2 (whisper) torch's own allocator reports nothing, but `mem_get_info` is driver-level, so the delta is still real. | Unmeasured live on a GPU this session. |
| D-7 | §4.3: kokoro-onnx "becomes the CPU default if ≤ 500 ms first-chunk" | Not measured (15.1); Piper remains the CPU default; no `KokoroOnnxMouth` engine was written because nothing could load. | Honest absence over a guessed engine. |
| D-8 | §6.2: `manifest` frame "whenever a proof changes" | Sent at session start and re-sent from a manifest subscription whenever any stage is re-proved (`VoiceManifest.subscribe`). Live re-proof mid-session is not triggered automatically; `/api/voice/prove` does it. | — |
| D-9 | §3.3 rule 5: "Turn-time trimming is forbidden on the voice path" | The contract is computed at proof time from the real prompt. At turn time `_generate_agent → _via_ollama/_call_openai` still calls `fit_tools_to_seat`; on a 131,072 seat nothing trims, and `compute_contract` refuses the session (`voice_contract_does_not_fit`) when the floor would not fit at proof time. A per-turn assertion (`test_voice_contract_never_trims_floor` over 30 turns) was **not** written: the loop already compacts the transcript (`compaction.maybe_compact`) rather than the tools, and `FittedTools` is single-decision. | Time-boxed; noted as remaining. |
| D-10 | §8.1 A: three Mode buttons (`local` \| `gemini` \| `local-listen-cloud-voice`) | The picker has Local, Cloud (Gemini Live), one "Local listening, cloud voice (<provider>)" row per `cloud_voice.available_providers()` entry (values are the tree's provider names, `elevenlabs` / `inworld`, not a `local-listen-cloud-voice` literal), and a fourth "Local GPU (NeMo, experimental)" row for `local-gpu`. `auto` is gone from the picker and the catalog; it is still accepted on write and read as `local`. | `local-gpu` is how the NeMo tier is selected today (`resolve_tier`); §11 keeps NeMo "off by default, experimental in the picker". |
| D-11 | §8.1 A: the cloud disclosure "exists for ElevenLabs; extended to Gemini" | No disclosure UI existed in `index.html`; only the server side (`/api/voice/cloud/disclosure`) did. The Mode picker now shows the four lines before a cloud mode takes effect: Gemini's are client-side text (with the §4.5 relay line), a provider's come from that route. | Code wins. |
| D-12 | §4.5: `ask_friday` "handler runs `_generate_agent` … with the full contract" | The handler (`voice_engine._tool_ask_friday`) imports `_build_voice_system_prompt`, `_voice_reply_cap` and `_gate_voice_tool_result` lazily from `routes.voice` (a service reaching into a route) and seals the answer itself, in addition to the Live runner's gate. Contract fit is not re-checked per relay; the manifest's mind proof covers it. | The prompt builder and the gate are route-owned; moving them was out of scope. |
| D-13 | §9.1: `test_voice_contract_never_trims_floor` (30-turn synthetic session) | Not written (see D-9). | Time-boxed. |
| D-14 | §12 Phase 3 item 3 (spoken-register metrics in the debug drawer) and item 4 | Not built; optional per the build brief. `turn_receipt` frames carry the raw material (agent_text via `voice_turn_done`, clauses, first_clause_ms). | Out of the mandated scope. |
