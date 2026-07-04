# Local Voice Integration Spec — NVIDIA NeMo (and the lighter path)

**Status:** Tier-1 (CPU faster-whisper + Piper) and Tier-2 (NVIDIA NeMo GPU) are
both implemented. Tier-1 is the default/universal path; Tier-2 is the opt-in GPU
upgrade (`services/nemo_voice.py`, `.[voice-local-gpu]`) — see
[`docs/TIER2_NEMO_VOICE.md`](docs/TIER2_NEMO_VOICE.md). The sections below are the
original design doc.
**Author:** prepared for Stephen Webster
**Date:** 2026-06-23
**Scope:** Add provider-agnostic *local* (offline-capable) voice — streaming ASR + TTS — alongside the existing Gemini Live cloud voice, without disrupting what already works.

> **TL;DR for the busy reader.** Gemini Live is one model that hears, thinks, and speaks in a single duplex stream. NeMo is *not that*: it gives us a microphone-to-text engine and a text-to-speech engine with **no brain in between**. So "local voice" is not a swap of one socket for another — it's a new pipeline shape (`mic → VAD → ASR → existing LLM brain → TTS → speaker`) that we run *next to* Gemini and select per-user. The good news: the browser audio plumbing, the holographic UI signals, and the provider registry were all built generic enough that ~80% of the integration is reuse, not new surface. The honest news: a local STT→LLM→TTS pipeline feels more turn-based ("walkie-talkie") than Gemini's fluid duplex, and the heavyweight NeMo/CUDA stack is a 3–6 GB GPU-only dependency. **My recommendation is a two-tier strategy** — ship a tiny CPU-friendly tier first (works for *every* user), then offer NeMo as the premium GPU upgrade. Details in §13.

---

## Table of contents

1. [The core architectural decision](#1-the-core-architectural-decision)
2. [Architecture & data flow (cloud vs local)](#2-architecture--data-flow)
3. [ASR integration](#3-asr-integration)
4. [TTS integration](#4-tts-integration)
5. [Hardware requirements](#5-hardware-requirements)
6. [Dependency management](#6-dependency-management)
7. [New-user experience](#7-new-user-experience)
8. [Existing-user migration](#8-existing-user-migration)
9. [Offline capability](#9-offline-capability)
10. [Provider agnosticism — where it plugs in](#10-provider-agnosticism)
11. [Holographic UI integration](#11-holographic-ui-integration)
12. [Testing strategy](#12-testing-strategy)
13. [Alternatives & the 80% path](#13-alternatives--the-80-path)
14. [Risk assessment](#14-risk-assessment)
15. [Phased implementation plan + effort](#15-phased-implementation-plan)
16. [Open questions for Stephen](#16-open-questions-for-stephen)

---

## 1. The core architectural decision

This is the fact that everything else hangs on, so it goes first.

**Gemini Live** (today's voice) is a *speech-to-speech* model. The browser opens one WebSocket (`/ws/live`), streams raw mic PCM up, and Gemini does **everything** server-side in one model: voice-activity detection, transcription, reasoning, tool-calling, and expressive audio synthesis — then streams audio back down the same socket. Turn-taking, barge-in, and emotional prosody are all *inside* that one model. Our code is a thin bridge (`routes/voice.py` → `services/voice_engine.py`).

**NeMo** is not a speech-to-speech model. It is two independent components:

- **ASR** (`nemotron-3.5-asr-streaming-0.6b`): audio in → **text** out. No reasoning. No audio out.
- **TTS** (FastPitch+HiFi-GAN / Piper / etc.): **text** in → audio out. No understanding.

There is **no brain between them**. To make local voice conversational, *we* must supply the brain and the turn-taking glue:

```
LOCAL VOICE = mic → VAD → [ASR] → text → [Friday's existing LLM brain] → text → [TTS] → speaker
                                            (Anthropic / Ollama / OpenAI,
                                             routed by model_router.py exactly
                                             as a typed chat turn is today)
```

**Consequences that ripple through the rest of this spec:**

| Dimension | Gemini Live (cloud) | NeMo local pipeline |
|---|---|---|
| Topology | 1 duplex model | 3 stages we orchestrate |
| VAD / endpointing | Gemini-managed (server VAD) | **We own it** (Silero VAD) |
| Barge-in | `ActivityHandling.NO_INTERRUPTION` (a Gemini config) | We replicate via half-duplex mic mute (already in browser) |
| The "brain" | Inside the voice model | The existing chat/agent pipeline |
| Latency budget | One network round-trip | Sum of ASR + **LLM** + TTS (LLM dominates) |
| Expressiveness | High (affective dialog, prosody) | Good but flatter (FastPitch) |
| Privacy / offline | None (cloud) | Full (if brain is local Ollama too) |

**Design principle adopted throughout:** *keep the browser↔server message contract identical between cloud and local.* The browser should not care which engine is talking — it connects to a URL chosen by the resolved voice provider and consumes the same `{type:'audio'|'text'|'input_transcript'|'turn_end'|...}` events. This is what makes ~80% of the work reuse instead of rewrite.

---

## 2. Architecture & data flow

### 2.1 Current (cloud) path — for reference

```
┌─────────── Browser (ui_parts/app.html) ───────────┐         ┌──── Server ────┐        ┌── Google ──┐
│ getUserMedia (16 kHz, AEC on)                      │         │ routes/voice.py │        │ Gemini      │
│   └─ ScriptProcessor → f2pcm(16k) → i16ToB64       │  WS     │  /ws/live       │  Live  │ Live API    │
│        └────────────── {type:'audio'} ────────────────────► │  reader()──────────────► │ (STT+brain  │
│                                                    │         │                 │        │  +tools+TTS)│
│ AudioWorklet 'friday-pcm-player' (24 kHz ring buf) │ ◄──────────── writer() ◄───────────────────────  │
│   ◄─ {type:'audio'} (24 kHz PCM16 b64)             │  WS     │  {type:'audio'/ │        │            │
│   analyser → _fridayHoloAmplitude + ttsActive      │         │   'text'/...}   │        └────────────┘
└────────────────────────────────────────────────────┘        └─────────────────┘
```

### 2.2 Proposed local path — new `/ws/voice-local`, same client contract

```
┌─────────── Browser (UNCHANGED audio plumbing) ─────────┐        ┌──────────────── Server: services/voice_local.py ────────────────┐
│ getUserMedia 16 kHz ─ f2pcm ─ i16ToB64                 │  WS    │  ┌── Silero VAD (CPU, ONNX) ── endpoint detection            │
│   └────────── {type:'audio'} ──────────────────────────────────►│  │     │ speech segment                                       │
│                                                        │        │  ▼                                                          │
│                                                        │        │  Nemotron-3.5 streaming ASR (GPU)  ──► partial + final text  │
│   ◄─ {type:'input_transcript'} (live partials) ◄───────────────│──┘                                  │                        │
│                                                        │        │                                     ▼                        │
│                                                        │        │  model_router.route()  ─► LLM brain (Anthropic|Ollama|OpenAI)│
│   ◄─ {type:'text'} (assistant text, streamed) ◄────────────────│─────────────────────────────────────┘  │  (streams tokens)   │
│                                                        │        │                                          ▼                   │
│ AudioWorklet 'friday-pcm-player' (24 kHz) ◄────────────────────│  TTS (NeMo FastPitch+HiFiGAN | Piper) ─► PCM ─► resample 24 kHz│
│   ◄─ {type:'audio'} (24 kHz PCM16 b64) ◄───────────────────────│       (synthesize per-sentence as tokens arrive → low latency) │
│   analyser → _fridayHoloAmplitude + ttsActive          │        │  ◄─ {type:'turn_end'} when done                              │
└────────────────────────────────────────────────────────┘        └───────────────────────────────────────────────────────────────┘
```

Key reuse points (each is an existing artifact we do **not** rewrite):
- Mic capture, `f2pcm`/`i16ToB64`/`b64ToI16`, the `friday-pcm-player` worklet, and the `{type:...}` event protocol — all unchanged.
- The "brain" is `model_router.route()` + the existing agentic loop — the *same* code path a typed chat turn uses.
- The holo signal wiring (`_fridayHoloAmplitude`, `_fridayMoodSignals.ttsActive`) reads from the playback analyser, so it works for *any* audio source (see §11).

### 2.3 How the router decides cloud vs local, and can they coexist?

Yes, they coexist — and should. The decision is a **per-user setting**, resolved at session start, not a global build flag:

- `settings.voice_engine` ∈ `{"auto","gemini","local"}` (new key; default `"auto"`).
- `"auto"` resolution order: if `voice_engine_local_ready` (GPU + models present, or CPU tier installed) **and** user opted in → local; else if Gemini key present → gemini; else → demo/text.
- The browser asks `GET /api/voice/session-info` (extends the existing `/api/voice/fallback-status`) which returns `{engine, ws_url}`. The mic button connects to `/ws/live` or `/ws/voice-local` accordingly. **One toggle, one branch.**

Coexistence patterns this enables:
- **Hybrid by sensitivity:** vault/private topics → local brain + local voice (never leaves device); general chat → Gemini Live for fluidity. The router already force-routes vault traffic local (`model_router._route_vault`); voice can honor the same signal.
- **Hybrid by connectivity:** online → Gemini; offline (the existing `core.NETWORK_STATE` overlay) → auto-switch to local. This is a natural extension of the offline-first work already in the codebase.

---

## 3. ASR integration

### 3.1 The model (verified specs, June 2026)

`nvidia/nemotron-3.5-asr-streaming-0.6b` — Hugging Face, released **2026-06-04**:
- **600M params**, "Cache-Aware FastConformer-RNNT with Prompt", 24 encoder layers.
- **40 language-locales** (19 transcription-ready incl. English/Spanish/French/German/Italian/Portuguese/Russian; 13 broad-coverage; 8 adaptation-ready). English-only sibling: `nvidia/nemotron-speech-streaming-en-0.6b`.
- **Streaming chunk sizes (the latency dial):** 80 / 160 / 320 / 560 / 1120 ms, via `att_context_size` ∈ `[56,0] [56,1] [56,3] [56,6] [56,13]`. Smaller chunk = lower latency, slightly higher WER.
- **WER @ 1.12 s chunk:** English 7.91 %, Spanish 4.11 %, Italian 4.25 %, German 8.31 %, French 9.03 %.
- **GPU required** (Turing→Blackwell + Jetson). *Not designed for CPU.* — this is the single biggest constraint and the reason for the two-tier strategy in §13.
- **Runtime:** NeMo 26.06 (`pip install nemo_toolkit[asr]`). **License: OpenMDW-1.1** (must be reviewed for redistribution terms before we bundle — see §14).

### 3.2 Cache-aware streaming vs Gemini's managed VAD

Gemini does VAD for us. Locally, the cache-aware encoder consumes fixed audio chunks continuously and emits partial hypotheses — but it does **not** by itself decide *when a user turn ends*. We add **Silero VAD** (tiny, ONNX, CPU, ~2 MB) in front to:
1. Gate the ASR (don't burn GPU on silence).
2. Detect end-of-utterance (endpointing) → triggers the LLM turn.

Recommended endpointing mirrors the tuning we already settled on for Gemini: ~**800 ms** trailing silence to end a turn (matches `silence_duration_ms=800` in `routes/voice.py`), with low start-sensitivity so speaker echo doesn't false-trigger.

### 3.3 Latency budget (single-stream, RTX-class GPU)

| Stage | Expected | Notes |
|---|---|---|
| Chunk buffering | = chunk size (e.g. 320 ms) | The latency dial; 320 ms is a good default |
| ASR inference per chunk | ≪ real-time on RTX (0.6B model) | RNN-T is faster-than-real-time; not the bottleneck |
| Endpoint silence wait | ~800 ms | Same as cloud today |
| **LLM brain** | **0.3–3 s+** | **The dominant term.** Cloud Anthropic ~0.5–1.5 s to first token; local Ollama can be much higher (see Risk §14) |
| TTS first audio | 50–300 ms | Piper sub-50 ms; FastPitch fast; synthesize per-sentence |
| **Perceived "I spoke → it starts replying"** | **~1.5–4 s** | Acceptable but not Gemini-fluid |

**Mitigation that matters most:** stream the LLM tokens and synthesize TTS **sentence-by-sentence** so Friday starts speaking the first sentence while later sentences are still being generated. Without this, perceived latency balloons to full-response time.

### 3.4 Barge-in & echo (the problem we already solved for Gemini)

Our Gemini fix was `ActivityHandling.NO_INTERRUPTION` (speaker mode) so room echo can't self-interrupt. That's a Gemini-API concept; locally we replicate the *behavior* with mechanisms already in the browser:
- `getUserMedia({echoCancellation:true, noiseSuppression:true})` — already on.
- `MUTE_MIC_WHILE_SPEAKING` half-duplex gate — **already implemented** in `app.html` (currently default-off). For local "speaker mode" we flip it on: mic is muted while `ttsActive`/playback is non-empty, eliminating echo self-interruption with zero new code.
- "Headphones mode" (barge-in): leave mic hot; run VAD continuously; if confident speech is detected during playback, stop TTS playback (drain the worklet ring buffer) and start a new ASR turn. This is genuinely harder locally than with Gemini and is a Phase-4 refinement, not v1.

---

## 4. TTS integration

### 4.1 Model choice — recommend Piper as the default, NeMo as the premium tier

| Option | Quality | Footprint | Hardware | Streaming | License |
|---|---|---|---|---|---|
| **Piper** (VITS→ONNX) | Good, clearly synthetic | ~30 MB/voice, onnxruntime only | **CPU**, 10× RT on desktop, sub-50 ms first audio | Yes (sentence) | GPL (review) |
| **NeMo FastPitch+HiFi-GAN** | Better prosody, 900× RT mel | Larger; needs torch | GPU preferred | Yes | NeMo terms |
| NeMo VITS | End-to-end, natural | torch | GPU | Yes | NeMo terms |
| XTTS v2 | Best + voice cloning | ~2 GB, 4–6 GB VRAM | GPU | Limited | Coqui (review) |

**Recommendation:** Piper for the universal/CPU tier (it's the pragmatic 80% win — instant, tiny, runs everywhere), NeMo FastPitch+HiFi-GAN for the GPU tier when the user wants higher fidelity and is already paying the CUDA cost for ASR. XTTS only if voice-cloning ("make Friday sound like X") becomes a product goal.

**Honest quality note:** none of these match Gemini Live's affective, emotionally-shaded delivery. Local TTS is "clear and natural" but flatter. For a voice-first product this is the most user-visible regression and Stephen should hear samples before greenlighting a default switch (Phase 0 deliverable).

### 4.2 Getting audio back to the browser — reuse the worklet, resample server-side

The `friday-pcm-player` worklet is constructed with `srcRate=24000` and already resamples to the AudioContext rate via linear interpolation. NeMo TTS is typically 22.05 kHz; Piper voices vary (16/22.05 kHz). **Cleanest path: resample TTS output to 24 kHz PCM16 server-side**, then emit the exact same `{type:'audio', data:<b64>}` messages. The worklet, the analyser, the holo amplitude — all unchanged. (Alternative — instantiate a second worklet node at the TTS-native rate — adds browser complexity for no benefit; rejected.)

So the TTS server module's contract is dead simple: `text → 24 kHz PCM16 chunks → b64 → existing socket`.

---

## 5. Hardware requirements

### 5.1 NeMo ASR (`nemotron-3.5-0.6b`) — GPU only

- A 0.6B RNN-T in fp16 is light: ~1.2 GB weights + activations/cache → **~2–3 GB VRAM** single-stream streaming. (Blog claims of "16+ GB" refer to **batch-128 throughput** configs, not real-time single-stream — ignore for our use case.)
- **Minimum practical GPU:** any RTX with ≥4 GB (RTX 2050/3050 laptop and up). Add ~1–2 GB if NeMo TTS shares the GPU.
- **Stephen's RTX:** comfortably over-provisioned. Expect faster-than-real-time ASR; the perceived latency will be dominated by the LLM brain, not ASR/TTS. A spike on his actual card is the Phase-0 gate.
- **CPU-only / integrated graphics:** Nemotron streaming **will not run.** This is non-negotiable per the model card → these users get the **CPU tier** (faster-whisper INT8 + Piper, §13) or stay on cloud.

### 5.2 Recommended minimum-spec matrix

| Machine | ASR | TTS | Brain | Result |
|---|---|---|---|---|
| RTX ≥6 GB (Stephen) | Nemotron (GPU) | NeMo or Piper | Cloud or local Ollama | Full premium local voice |
| RTX/GTX 4 GB | Nemotron (GPU) | Piper (CPU) | Cloud recommended | Good local voice |
| No GPU / iGPU, decent CPU | faster-whisper INT8 (CPU) | Piper (CPU) | Cloud recommended | "Lite" local voice, works everywhere |
| Weak CPU / unsupported | — | — | — | Cloud Gemini Live or text-only |

---

## 6. Dependency management

### 6.1 The bloat problem and the rule

torch (CUDA wheels) + nemo_toolkit[asr] + cuDNN is **~3–6 GB** and pulls heavy transitive deps that frequently conflict on CUDA versions. This must **never** land in the base install or in `[all]`.

**Hard rule:** today `install.bat/.sh/.ps1` all hardcode `pip install -e .[all]`. We must (a) ensure `[all]` does **not** include the heavy voice group, and (b) gate the heavy group behind an explicit, separate, opt-in install step. New users must be able to get a working app without ever downloading torch.

### 6.2 Proposed pyproject groups

```toml
[project.optional-dependencies]
# existing: voice (pyttsx3), creative, google, local, compression, federation, windows, all

# NEW — tiny, CPU, universal. Safe to consider for broader inclusion.
voice-local-lite = [
  "onnxruntime>=1.17",       # CPU inference for Piper + Silero VAD
  "piper-tts>=1.2",          # ~30 MB voices, no torch
  "faster-whisper>=1.0",     # CTranslate2 backend, CPU INT8 ASR
  "silero-vad>=5.0",         # tiny VAD
]

# NEW — heavy, GPU, opt-in ONLY. NOT in [all].
voice-local-gpu = [
  "nemo_toolkit[asr,tts]>=2.6",   # pin to NeMo 26.06 line
  # torch + CUDA installed via a dedicated installer step with the
  # correct CUDA index URL — NOT a blind pip dep (version-matrix hell).
]
```

`[all]` includes `voice-local-lite` (cheap, universal) but **excludes** `voice-local-gpu`.

### 6.3 First-time setup cost

| Tier | Download | First-run model fetch | Setup time |
|---|---|---|---|
| Lite (CPU) | ~150–300 MB (onnxruntime + wheels) | Piper voice ~30 MB + whisper-small ~150 MB | 1–3 min |
| GPU (NeMo) | ~3–6 GB (torch-CUDA + NeMo) | Nemotron ~0.6 GB + TTS ~few hundred MB | 5–20 min (network-bound) |

Model checkpoints download lazily **on first use**, not at install, with a visible progress UI (§7). Never block app launch on a multi-GB download.

---

## 7. New-user experience

A new user clones the repo / runs the Windows installer. Goal: **they get working voice with zero friction, and local voice is a discoverable upgrade, never a prerequisite.**

### 7.1 Installer (`install.ps1` / `install.bat` / `install.sh`)

These already detect a GPU (`Get-CimInstance Win32_VideoController` / `nvidia-smi`). We extend the existing detection block:
- Base install always includes `voice-local-lite` (tiny) so *every* user has offline voice capability out of the box.
- If an NVIDIA GPU is detected: prompt **"Detected NVIDIA GPU. Install premium local voice (NeMo, ~4 GB download)? [y/N]"** — default **No**. Yes → run the dedicated torch-CUDA + `[voice-local-gpu]` install step.
- No GPU: say nothing about NeMo; lite tier already covers them.

### 7.2 First-run wizard (`setup_wizard.py`, currently 9 steps)

Augment the existing **Voice Persona** step (step 7) rather than adding a whole new step:
- Detect readiness via the existing `ollama_manager.detect_hardware()` (already returns gpu/vram).
- Present voice engine choice contextually:
  - GPU + NeMo installed → "Cloud (Gemini, most expressive) · **Local GPU (NeMo, private)** · Local Lite (CPU)"
  - GPU but NeMo not installed → offer cloud/lite now + "you can enable premium local voice later in Settings."
  - No GPU → "Cloud (Gemini) · Local Lite (CPU, private)".
- Writes `voice_engine` + `tts_voice` to settings. Quick-mode keeps today's default (Gemini if key present).

### 7.3 No-GPU graceful fallback

Strictly ordered, no dead ends: **local-gpu (if ready) → local-lite (if installed) → Gemini cloud (if key) → pyttsx3 offline TTS (existing) → text-only.** The existing `/api/voice/fallback-status` already reports capability tiers; we extend it to include the local engines.

### 7.4 Settings UI

Lives in the existing **Settings → Audio & Voice** section. Below today's "Voice Model (Live)" picker, add:
- **Voice Engine** selector: Cloud (Gemini) / Local GPU (NeMo) / Local Lite (CPU) / Auto — only showing tiers the machine supports.
- **Model download manager**: status chip + "Download / Update" button + progress bar for ASR/TTS checkpoints.
- The existing **Settings → Hardware** section gains a "Local voice: ready / needs NeMo / GPU not detected" line, sourced from `/api/health/full`.

---

## 8. Existing-user migration (Stephen)

**Purely additive. Gemini stays the default; nothing he relies on changes until he opts in.**

1. `git pull` + re-run install → base gets `voice-local-lite` automatically; his existing `settings.json` is untouched (`voice_engine` absent ⇒ resolves to `"auto"` ⇒ Gemini, since he has a key). **Voice behaves exactly as today.**
2. To try local: Settings → Audio & Voice → "Install premium local voice" (one click runs the GPU dep step) → pick "Local GPU". First use downloads checkpoints with a progress bar.
3. He can flip Cloud ↔ Local per session from the same dropdown. No restart needed beyond the existing no-reloader caveat for route changes (the new `/ws/voice-local` route ships in the same build, so no special restart dance).

There is **no replacement** of Gemini. NeMo is a sibling engine selected by a setting.

---

## 9. Offline capability

With **local ASR + local TTS + local brain (Ollama)**, Friday is fully voice-first with zero internet:

- **Works offline:** wake → speak → transcribe → reason (Ollama gemma4) → speak. Vault access, local file/app/task tools (already open to local models per the universal-tool-calling work).
- **Degrades offline:** anything needing cloud — web/news search, Gmail/Calendar fetch, Anthropic-quality reasoning, image/video generation, Gemini's expressive prosody. The existing `core.NETWORK_STATE` overlay already forces `local_only` and can auto-select the local voice engine.
- **Experience:** turn-based and a bit slower than Gemini duplex, brain quality = whatever local model is installed, but genuinely private and airplane-proof. This is the headline feature for privacy-sensitive users and the strongest reason to build local voice at all.

---

## 10. Provider agnosticism — where it plugs in

Local voice fits the existing registry pattern cleanly. The extension points (all verified in the codebase):

1. **`services/provider_registry.py`** — register `nvidia-nemo` (type `nemo-local`, `auth:{type:none}`, `roles:[voice]`, `capabilities:[asr,tts]`) and a `local-voice-lite` entry (Piper/faster-whisper). Custom providers can also be dropped as `~/.friday/providers/*.json` with **no code change** — but because these need real health logic, bake them into `DEFAULT_PROVIDERS`.

2. **Capability model — recommended shape.** Today `capability_routing.voice = {provider, model}` and voice is *one* capability because Gemini does everything. Local voice splits voice into ASR + TTS. To stay honest *and* keep UX simple:
   - Add `"asr"` and `"tts"` to the `CAPABILITIES` tuple (`services/capability_router.py`) and to `capability_routing` in `core.py` `DEFAULT_SETTINGS`, both defaulting to `{provider:"google-gemini"}` (Gemini fulfills them implicitly).
   - Keep a single user-facing **`voice_engine`** selector that sets `asr`/`tts` routing under the hood (gemini ⇒ both gemini; local-gpu ⇒ both nvidia-nemo; local-lite ⇒ both local-voice-lite). Power users can override asr/tts independently.
   - `_sync_capability_routing` in `core.py` handles canonical↔flat sync; new caps that have no legacy flat key (like `embedding`) are already handled by passing straight through — asr/tts follow that precedent.

3. **`services/provider_health.py`** — add a `nemo-local` branch: report `ok` only if torch+NeMo importable **and** GPU present **and** checkpoint downloaded; else `missing`/`down` with an actionable detail. Mirror the existing Ollama branch (which reports `ok` only if a model is installed).

4. **`services/capability_router.py` `resolve()`** — local providers report `available:true` and degrade gracefully, exactly like the existing `embedding`/`local` special-case.

5. **`services/model_catalog.py` + `GET /api/models`** — NeMo/Piper "models" tagged `roles:[voice]` automatically surface in the voice picker; `provider_family()` in `model_router.py` gains prefixes (`nemo-`, `piper-`) returning `"local"`.

6. **`services/demo_mode.py`** — add a canned `voice` line noting local voice availability when no cloud key is present.

**Answer to "is nvidia-nemo a provider or a capability category?"** — It's a **provider** (like `ollama-local`), and it fulfills the **asr** and **tts** capability categories. We add the two capability keys; we do not invent a parallel "local voice" subsystem. That keeps everything inside the one source of truth (`settings.json` → `capability_routing`).

---

## 11. Holographic UI integration

This is the cleanest part, and it's a genuine design win from the existing architecture.

The holo scene is driven by exactly two browser globals:
- `window._fridayHoloAmplitude` (0–1 RMS), computed by `_voiceAmpLoop` from an **analyser node attached to the playback path**.
- `window._fridayMoodSignals.ttsActive`, set true while amplitude is recent (350 ms hangover), consumed by `fridaySetHoloState(state, amp)` on a 1 s poll. The rule — **animate only when speaking, color-shift only for processing** — lives entirely in this layer.

Because these read from the **playback analyser**, not from Gemini specifically, they are **source-agnostic by construction.** As long as local TTS audio flows through the same `friday-pcm-player` worklet (which §4.2 guarantees by resampling to 24 kHz and reusing the same `{type:'audio'}` messages), the cube behaves **identically** regardless of engine. No new signals, no new wiring.

The only thing to preserve: the server must emit the same lifecycle events — `{type:'audio'}` during speech, `{type:'input_transcript'}` for live partials (drives nothing visual but feeds the transcript), and `{type:'turn_end'}` so `ttsActive` decays naturally. The `voice_local.py` orchestrator must emit these on the same schedule the Gemini bridge does. **Acceptance test:** record the cube under Gemini and under local for the same utterance; motion/color traces should be visually indistinguishable.

One subtlety to honor the "no fake motion" rule (per the memory note): during the **LLM-thinking gap** (which is longer locally), do **not** synthesize amplitude. Emit a processing **state** (color shift) only, via `_fridayMoodSignals.chatLoad`, exactly as typed chat does. Motion resumes only when real TTS PCM starts flowing.

---

## 12. Testing strategy

CI has no GPU; the existing suite (~1,870 offline tests) autouse-stubs every LLM entry point and redirects `$HOME` to temp. We follow that philosophy exactly.

1. **Pure-Python wiring tests (no torch, run in CI):** provider registers; `nvidia-nemo`/`local-voice-lite` resolve through `capability_router`; health reports `missing` when torch absent; `voice_engine` setting flips `asr`/`tts` routing; `/api/models` lists the voice models; `_sync_capability_routing` keeps asr/tts coherent. This covers the majority of the integration surface and needs no model.

2. **Mocked inference (run in CI):** a `FakeASR` returning canned transcripts and a `FakeTTS` returning a fixed 24 kHz PCM sine — injected the same way the LLM stubs are. Lets us test the **whole orchestration** (`voice_local.py`: VAD-gate → ASR → router → TTS → socket events) and assert the emitted event sequence matches the Gemini bridge's contract — without any GPU.

3. **CPU-tier smoke (optional, opt-in):** faster-whisper + Piper actually run on CPU, so a `@pytest.mark.slow` test can do a real tiny round-trip on a dev box (and even in CI if we accept ~seconds). This is the cheap way to test *real* audio without a GPU.

4. **GPU integration (manual, gated):** `@pytest.mark.skipif(not cuda_available)` tests run on Stephen's RTX for NeMo specifically. Document in `MANUAL_TEST_PROCEDURES.md`.

5. **Browser protocol:** unchanged event contract ⇒ existing voice UI tests cover the client. Add a Playwright check that the mic button connects to `/ws/voice-local` when `voice_engine=local`.

---

## 13. Alternatives & the 80% path

Stephen explicitly asked: *is there a lighter path that gets 80% of the quality?* Yes, and I recommend building it **first**.

### 13.1 The two-tier strategy (recommendation)

| Tier | ASR | TTS | Deps | Hardware | Who it's for |
|---|---|---|---|---|---|
| **Tier 1 — Lite (build first)** | faster-whisper INT8 (CPU) | Piper (CPU) | onnxruntime + small wheels, **no torch/CUDA** | **Everyone**, incl. no-GPU laptops | New users, default offline path |
| **Tier 2 — Premium (build second)** | Nemotron-3.5 streaming (GPU) | NeMo FastPitch+HiFi-GAN | torch-CUDA + NeMo (3–6 GB) | RTX users (Stephen) | Lowest latency, true streaming, best local fidelity |

**Why Lite first:**
- It de-risks the *entire* feature — VAD, the STT→LLM→TTS orchestration, the `/ws/voice-local` route, the holo-signal reuse, the Settings/wizard plumbing — all get built and validated against tiny CPU models that run in CI and on any machine. NeMo then drops in as a swappable backend behind the same interface.
- It gives **every** user offline voice, not just the RTX minority. For a voice-first product shipped to strangers, "works on my laptop with no GPU and no 4 GB download" is the higher-leverage win.
- It sidesteps CUDA/torch version hell for the common case.

NeMo is the right Tier 2 — genuinely better streaming (cache-aware, true low-latency partials) and fidelity on a GPU — but it should be the *upgrade*, not the *gate*.

### 13.2 Other options weighed

- **Whisper large-v3 / turbo:** best multilingual (99 langs), but **not natively streaming** (chunked/buffered) → higher latency; large-v3 ~10 GB VRAM, turbo ~6 GB. Good for *batch* transcription features, wrong for live turn-taking. `faster-whisper` (CTranslate2) makes the **small** model viable on CPU for Tier 1.
- **Canary / Canary-Qwen 2.5B:** top accuracy (5.63 % WER) but slower (RTFx ~418) and some variants are **CC-BY-NC** (non-commercial) — license risk for a public repo. Not for low-latency live.
- **Parakeet TDT 0.6b v2/v3:** the non-prompt sibling of Nemotron; excellent throughput. Nemotron-3.5 (cache-aware + prompt + 40 langs) is the better streaming choice for us, but Parakeet is a fine fallback.
- **Moonshine v2:** purpose-built on-device streaming ASR, latency-critical — worth a look as an alternative Tier-1 ASR if faster-whisper latency disappoints.
- **XTTS v2 / StyleTTS2:** higher TTS quality + voice cloning, but heavier (4–6 GB VRAM). Only if "custom Friday voice" becomes a goal.

---

## 14. Risk assessment (honest)

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | **UX regression: turn-based feel vs Gemini duplex.** Local pipeline can't match Gemini's barge-in fluidity/expressiveness. | High | High (it's a *voice-first* product) | Set expectations; stream-TTS-per-sentence for latency; keep Gemini the default; position local as "private/offline," not "better." **Phase-0 demo before greenlight.** |
| R2 | **CUDA/torch dependency hell.** NeMo pins specific torch/CUDA; conflicts with other GPU users on the machine; broken installs. | High | High | Tier-1 (no torch) is the escape hatch; install torch via dedicated step with correct CUDA index URL; never blind-pip; isolate in opt-in group; pin versions. |
| R3 | **3–6 GB download scares off / breaks new users.** | Med | Med | Heavy group is opt-in only, never in base/`[all]`; lazy checkpoint download with progress UI; Tier-1 covers everyone without it. |
| R4 | **LLM latency dominates.** Local Ollama can be slow (memory notes ~130 s pathological under memory pressure). | Med | High | Default the local-voice *brain* to cloud Anthropic unless user chooses full-offline; stream tokens; warn in UI when brain is a slow local model. |
| R5 | **Quality gap in TTS** (flat prosody) disappoints. | Med | Med | Offer NeMo (better) on GPU; sample-test voices in Phase 0; XTTS as future option. |
| R6 | **License.** Nemotron is OpenMDW-1.1; Piper is GPL; Canary is non-commercial. Repo is **public**. | Med | High | Legal review of OpenMDW-1.1 redistribution + GPL implications **before** bundling; download models at runtime (not vendored) to reduce redistribution exposure; avoid CC-BY-NC models entirely. |
| R7 | **GPU VRAM contention** if NeMo ASR+TTS + local LLM all want the GPU. | Med | Med | Default TTS to Piper-CPU even on GPU tier; document VRAM budget; let user pick brain location. |
| R8 | **Maintenance surface.** A second full voice path doubles the voice code to maintain. | Med | Med | Keep the client contract identical (one browser path); share the agentic brain; isolate engine differences behind a narrow ASR/TTS interface. |
| R9 | **Windows + NeMo.** NeMo is Linux-first; Windows GPU support can be rough (cf. the headroom Rust/MSVC note in memory). | Med | High | Validate on Stephen's actual Windows+RTX box in Phase 0 *before* committing to NeMo on Windows; Tier-1 (onnxruntime) is rock-solid on Windows as the fallback. |

**The two risks that could kill the NeMo-specific path:** R9 (does NeMo even run well on Windows+RTX?) and R6 (license). Both are answerable in Phase 0 with a few hours of spike work, and both are *why* Tier-1 should ship first regardless.

---

## 15. Phased implementation plan

Effort = rough engineering-days for one focused dev. Phases 0–1 are the approval-gated foundation; 2–4 deliver working Tier-1 voice; 5 adds NeMo; 6 polishes.

| Phase | Goal | Key work | Effort | Gate |
|---|---|---|---|---|
| **0. Spike & decide** | Prove feasibility, hear quality | Run Nemotron + a NeMo/Piper TTS on Stephen's Windows+RTX; measure latency; record voice samples; confirm OpenMDW/GPL license posture | **1–2 d** | **Stephen approves direction + accepts quality** |
| **1. Provider/capability plumbing** | Local voice is *selectable* (no inference yet) | Register `nvidia-nemo` + `local-voice-lite` providers; add `asr`/`tts` capabilities; `voice_engine` setting; health checks; `/api/voice/session-info`; Settings dropdown + Hardware readout; wizard step 7 augment; **full pure-Python test suite** | **3–5 d** | Tests green; UI shows tiers correctly |
| **2. Local ASR (Tier-1)** | Speech → text, offline, CPU | Silero VAD + faster-whisper; new `services/voice_local.py` + `/ws/voice-local`; emit `input_transcript`/`text` events; mocked-inference tests | **4–6 d** | Live transcript appears in UI, CPU-only |
| **3. Local TTS (Tier-1)** | Text → speech via existing worklet | Piper → 24 kHz PCM → existing socket/worklet; **holo signal parity test** (cube identical to Gemini) | **3–4 d** | Friday speaks; cube animates identically |
| **4. Turn-taking & latency** | Conversational feel | Endpointing tuning; half-duplex echo guard (reuse `MUTE_MIC_WHILE_SPEAKING`); sentence-streamed TTS; offline auto-switch via `NETWORK_STATE` | **3–5 d** | End-to-end offline voice conversation works |
| **5. NeMo premium tier (Tier-2)** | GPU streaming + better fidelity | `[voice-local-gpu]` group + dedicated torch-CUDA installer step; Nemotron streaming backend behind the ASR interface; NeMo TTS option; model-download manager UI; GPU integration tests | **5–8 d** | NeMo path works on Stephen's RTX |
| **6. Polish & docs** | Ship-ready | Fallback ordering hardening; install.* prompts; `friday_cli health` torch/CUDA/NeMo detection; `MANUAL_TEST_PROCEDURES.md`; user docs | **2–3 d** | Clean install on a fresh machine, both tiers |

**Total:** ~21–33 dev-days. **Tier-1 voice (Phases 0–4) is shippable on its own at ~14–22 days** and delivers offline voice to *every* user; Phase 5 (NeMo) is an additive ~5–8 days for the GPU premium experience.

---

## 16. Open questions for Stephen

1. **Tier ordering:** Approve "Lite-first" (CPU faster-whisper+Piper, ships to everyone) before NeMo? Or do you specifically want NeMo/RTX as the first deliverable even though it only serves GPU users?
2. **Default engine:** Should the *default* ever auto-switch to local, or is local always explicitly opt-in with Gemini staying default until you say otherwise?
3. **The brain when offline:** When voice goes fully local/offline, is a local Ollama brain (slower, lower quality) acceptable, or should local voice still prefer the cloud brain when a network exists (privacy tradeoff)?
4. **License appetite:** OK to depend on GPL (Piper) and OpenMDW-1.1 (Nemotron) in a public repo, pending the Phase-0 legal read? Hard-avoid non-commercial (Canary) — agreed?
5. **Quality bar:** Will you make the go/no-go on NeMo *after* hearing Phase-0 voice samples on your own hardware? (Strongly recommend yes.)
6. **Windows commitment:** If Phase-0 shows NeMo is painful on Windows+RTX, are we comfortable making Tier-1 (onnxruntime, rock-solid on Windows) the primary local path and treating NeMo as best-effort?

---

### Sources (model facts, June 2026)
- [nvidia/nemotron-3.5-asr-streaming-0.6b · Hugging Face](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b)
- [nvidia/nemotron-speech-streaming-en-0.6b · Hugging Face](https://huggingface.co/nvidia/nemotron-speech-streaming-en-0.6b)
- [nvidia/parakeet-tdt-0.6b-v2 · Hugging Face](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2)
- [NeMo TTS models — NVIDIA NeMo Framework User Guide](https://docs.nvidia.com/nemo-framework/user-guide/latest/nemotoolkit/tts/models.html)
- [Best open-source STT model in 2026 (benchmarks) — Northflank](https://northflank.com/blog/best-open-source-speech-to-text-stt-model-in-2026-benchmarks)
- [Piper TTS Setup 2026 — Local AI Master](https://localaimaster.com/blog/piper-tts-setup-guide) · [rhasspy/piper (GitHub)](https://github.com/rhasspy/piper)
