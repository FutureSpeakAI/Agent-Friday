# Agent Friday Voice System -- Technical Specification

> **ACCURACY ADDENDUM (2026-07-06, verified by live API probes):** §1.1's
> model-retirement claims are partly WRONG. Real `bidiGenerateContent`
> connect probes show `gemini-3.1-flash-live-preview` and
> `gemini-2.5-flash-native-audio-preview-12-2025` both still connect fine,
> while `gemini-2.5-flash-preview-native-audio` — introduced below as a
> "verified" fallback — does not exist upstream (1008 on connect). The
> shipped chain is now `gemini-2.5-flash-native-audio-latest` →
> `gemini-2.5-flash-native-audio-preview-09-2025` →
> `gemini-3.1-flash-live-preview`, with a `_RETIRED_LIVE_MODELS` denylist.
> Also: pyttsx3 is installed (the "missing" claim below is stale), and the
> auto-correction described in §1.2 was inert as written (the marker
> heuristic vouched for the very IDs it was meant to catch) — fixed. The
> forward-looking spec is `docs/VOICE_SYSTEM_SPEC.md`; treat this document
> as the historical incident record.

**Document:** `docs/VOICE_SYSTEM_OVERHAUL_SPEC.md`
**Status:** Post-overhaul reference (July 2026)
**Codebase:** the `friday-desktop` repository root
**Audience:** FutureSpeak.AI engineering, contributors, and future maintainers


---

## 1. Executive Summary

### 1.1 What Was Broken

Agent Friday's voice system was reported as non-functional ("voice is broken"). Investigation revealed the root cause was **not** the local voice pipeline -- that was fully operational. The failures were concentrated in the Gemini Live cloud path (Tier 3):

1. **Stale model ID:** `settings.json` carried `gemini-2.5-flash-native-audio-preview-12-2025`, a model Google had retired. The code's hardcoded default was `gemini-3.1-flash-live-preview`, also unverified. Both triggered 1008 WebSocket close codes that the old error handler misreported as API-key authentication failures, misdirecting debugging effort toward key rotation when the real problem was a dead model ID.

2. **pyttsx3 not installed:** The legacy TTS fallback (Windows SAPI5 via pyttsx3) was dead. When Gemini TTS failed, the fallback chain terminated instead of degrading to Piper.

3. **torch CPU-only:** An RTX 4070 GPU is present but `torch==2.12.0+cpu` was installed -- no CUDA support. Tier-2 NeMo voice could never activate.

4. **NeMo not installed:** The Tier-2 GPU voice stack (`nemo_toolkit`) was never installed. This is by design (opt-in), but it means the GPU tier reports `status: missing`.

### 1.2 What Was Fixed

- **LIVE_MODEL** default changed to `gemini-2.5-flash-native-audio-latest` (verified working).
- **LIVE_MODEL_FALLBACK2** updated from a retired model to `gemini-2.5-flash-preview-native-audio`.
- **Auto-correction logic** added to `_resolve_voice_engine()`: stale model IDs detected via `validate_live_model()` are automatically reset to the verified default and persisted to `settings.json`.
- **pyttsx3** installed. `_local_tts_available()` and `_synthesize_tts_wav_local()` enhanced to fall through to Piper when pyttsx3 is absent.
- **Error classifier** `_classify_live_error()` added to distinguish `model-missing` from `auth` failures, preventing the false "rotate your key" diagnosis.
- **`_compose_final_voice_error()`** now re-validates the key via REST when any attempt produces an auth-flavored error, so the final message can definitively state whether the KEY or the MODEL is the problem.
- **Torch CPU-only and NeMo not installed** documented as manual steps (not auto-fixable).

### 1.3 Current State

| Tier | Status | Notes |
|------|--------|-------|
| Tier 1 (Local CPU) | **Fully operational** | faster-whisper, Piper, onnxruntime, silero_vad installed. Models downloaded. |
| Tier 2 (Local GPU) | Not installed | Requires torch-CUDA + nemo_toolkit. RTX 4070 present, CUDA unavailable. |
| Tier 3 (Gemini Live) | **Operational** | Model ID corrected. Fallback chain verified. API key resolution hardened. |
| pyttsx3 fallback | **Operational** | Windows SAPI5 TTS available as last-resort offline fallback. |
| Piper fallback | **Operational** | Falls through from pyttsx3 when the latter is absent. |


---

## 2. Architecture Audit

### 2.1 File Map

```
src/agent_friday/
  services/
    voice_engine.py     Gemini TTS, Live session config, tool surface,
                        key validation/resolution, model validation,
                        system prompt construction, turn persistence
    local_voice.py      Tier-1 CPU: faster-whisper ASR + Piper TTS +
                        VADEndpointer + LocalVoiceEngine singleton
    nemo_voice.py       Tier-2 GPU: NeMo Nemotron ASR + FastPitch+HiFi-GAN TTS
  routes/
    voice.py            HTTP endpoints + WebSocket handlers
                        (/ws/voice-local, /ws/live), barge-in detector,
                        session resumption, liveness watchdog
```

### 2.2 Data Flow -- Local Voice (Tier 1 / Tier 2)

```
  Browser mic (16 kHz PCM16 mono)
       |
       | {type:'audio', data:<b64>}
       v
  /ws/voice-local (Flask-Sock WebSocket handler)
       |
       v
  VADEndpointer.feed(pcm)
  [energy gate >= 600 RMS, or Silero if installed]
  [accumulate speech, fire on 800ms trailing silence]
       |
       | (utterance bytes)
       v
  LocalVoiceEngine.transcribe(pcm16_16k)
       |
       |--- Tier 1: WhisperASR (faster-whisper, CTranslate2 INT8, CPU)
       |--- Tier 2: NeMoASR (Nemotron-3.5, CUDA GPU)
       |
       v
  transcript text
       |
       v
  _generate_agent(user_text, system=system_prompt)
  [same agentic dispatcher as text chat -- tools, vault, routing]
       |
       v
  agent reply text
       |
       v
  split_sentences(text)  [per-sentence streaming for latency]
       |
       v
  LocalVoiceEngine.synthesize(sentence)
       |
       |--- Tier 1: PiperTTS (VITS ONNX, CPU, 22.05kHz -> 24kHz)
       |--- Tier 2: NeMoTTS (FastPitch+HiFi-GAN, CUDA, 22.05kHz -> 24kHz)
       |
       v
  24 kHz PCM16 mono bytes, chunked to 9600-byte frames
       |
       | {type:'audio', data:<b64 PCM16@24k>}
       v
  friday-pcm-player AudioWorklet -> speaker
```

### 2.3 Data Flow -- Gemini Live (Tier 3)

```
  Browser mic (16 kHz PCM16 mono)
       |
       | {type:'audio', data:<b64>}
       v
  /ws/live (Flask-Sock WebSocket handler)
       |
       | send_realtime_input(audio=Blob(data, 'audio/pcm;rate=16000'))
       v
  Gemini Live bidiGenerateContent (persistent WebSocket)
       |
       | server_content.model_turn.parts[].inline_data.data (PCM16@24k)
       | server_content.input_transcription / output_transcription
       | tool_call (function_calls[])
       | session_resumption_update (new_handle)
       | go_away (time_left)
       v
  /ws/live writer() task
       |
       |--- audio -> {type:'audio', data:<b64>} -> browser worklet
       |--- transcript -> {type:'text'} / {type:'input_transcript'}
       |--- tool_call -> _voice_tool_run() -> send_tool_response()
       |--- go_away -> drain + renew via handle
       v
  friday-pcm-player AudioWorklet -> speaker
```

### 2.4 Dependency Chain

```
voice_engine.py
  imports from: core, agent, calendar_engine, model_router, news_engine
  depends on:   google-genai (cloud TTS/Live), pyttsx3 (optional fallback)
  exports:      _synthesize_tts_wav, resolve_gemini_key, validate_gemini_key,
                validate_live_model, _build_voice_live_tools, _voice_tool_run,
                _persist_voice_turn, LIVE_MODEL, LIVE_MODEL_FALLBACK/2

local_voice.py
  imports from: (stdlib only at module level)
  lazy imports: faster_whisper, piper, silero_vad, numpy
  depends on:   faster-whisper, piper-tts, onnxruntime
  exports:      LocalVoiceEngine, VADEndpointer, get_local_voice_engine,
                local_voice_health, split_sentences, deps_installed, deps_status

nemo_voice.py
  imports from: local_voice (PLAYBACK_RATE, _module_installed, _resample_pcm16)
  lazy imports: torch, nemo.collections.asr/tts
  depends on:   torch (CUDA), nemo_toolkit, NVIDIA GPU with 4GB+ free VRAM
  exports:      NeMoASR, NeMoTTS, gpu_tier_ready, gpu_status, nemo_health

voice.py (routes)
  imports from: core, agent, local_voice, model_router, voice_engine
  depends on:   flask-sock (WebSocket), google-genai (Live API)
  exports:      voice_bp (Blueprint), HTTP endpoints, WS handlers
```


---

## 3. Per-Tier Requirements

### 3.1 Tier 1 -- Local CPU (DEFAULT)

**What it provides:** Fully offline voice with no cloud dependency. ASR + TTS on the CPU. Works everywhere.

**Hardware:**
- Any x86_64 CPU (tested on Intel and AMD)
- ~300 MB disk for models (downloaded on first use)
- ~1 GB RAM for faster-whisper small + Piper

**Packages:**

| Package | Version | Purpose |
|---------|---------|---------|
| `faster-whisper` | latest | CTranslate2-based Whisper ASR, INT8 quantized |
| `piper-tts` | latest | VITS text-to-speech via ONNX |
| `onnxruntime` | latest | ONNX inference backend for Piper |
| `silero-vad` | latest (optional) | Neural VAD for better endpointing |
| `numpy` | latest | Audio buffer manipulation |

**Install:**
```bash
pip install -e ".[voice-local-lite]"
```

**Models (lazy download to `~/.friday/local_voice/`):**
- ASR: `faster-whisper` "small" model (~460 MB CTranslate2 checkpoint) -> `~/.friday/local_voice/whisper/`
- TTS: Piper `en_US-amy-medium` voice (~60 MB ONNX + config) -> `~/.friday/local_voice/piper/`
  - Source: `https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx`

**Audio specs:**
- ASR input: 16 kHz mono PCM16
- TTS output: Piper native rate (~22.05 kHz) resampled to 24 kHz PCM16 mono via stdlib linear interpolation

### 3.2 Tier 2 -- Local GPU (Opt-in)

**What it provides:** GPU-accelerated voice with better prosody than Tier 1. Same privacy guarantees. Falls back to Tier 1 when unavailable.

**Hardware:**
- NVIDIA RTX GPU (tested on RTX 4070)
- 4 GB+ free VRAM (`MIN_VRAM_GB = 4.0`)
- CUDA toolkit installed

**Packages:**

| Package | Version | Purpose |
|---------|---------|---------|
| `torch` | 2.x (CUDA build) | GPU compute framework |
| `nemo_toolkit` | latest | NVIDIA NeMo ASR + TTS models |
| All Tier-1 deps | -- | Fallback path |

**Install:**
```bash
# Step 1: Install torch with CUDA (replace cuXXX with your CUDA version)
pip install torch --index-url https://download.pytorch.org/whl/cu124

# Step 2: Install NeMo
pip install -e ".[voice-local-gpu]"
```

**Models (lazy download to `~/.friday/models/nemo/`):**
- ASR: `nvidia/nemotron-3.5-asr-streaming-0.6b` (600M params, ~1.5 GB)
  - Cache-aware FastConformer RNN-T, streaming, GPU-only
  - `att_context_size = [56, 3]` (320 ms chunks, latency/WER sweet spot)
- TTS: `nvidia/tts_en_fastpitch` + `nvidia/tts_hifigan`
  - Spectrogram generator + neural vocoder
  - Native rate: 22.05 kHz, resampled to 24 kHz

**GPU detection chain:**
1. `torch.cuda.is_available()` + `torch.cuda.mem_get_info()` (authoritative)
2. `nvidia-smi` via `ollama_manager.detect_hardware()` (fallback, total VRAM only)

### 3.3 Tier 3 -- Gemini Live (Cloud, Opt-in)

**What it provides:** Bidirectional streaming voice via Google Gemini Live API. Affective dialog, proactive audio, tool calling, session resumption.

**Requirements:**
- `GEMINI_API_KEY` (AI Studio or Cloud Platform)
- Network connectivity
- `google-genai` Python SDK

**Models (fallback chain):**

| Priority | Model ID | Status |
|----------|----------|--------|
| Primary | `gemini-2.5-flash-native-audio-latest` | Verified working (July 2026) |
| Fallback 1 | `gemini-2.5-flash-native-audio-latest` | Same as primary (resilience) |
| Fallback 2 | `gemini-2.5-flash-preview-native-audio` | Verified working |

**RETIRED models (do NOT use):**
- `gemini-2.5-flash-native-audio-preview-12-2025` -- 1008 "not found"
- `gemini-live-2.5-flash-preview` -- 1008 "not found"
- `gemini-3.1-flash-live-preview` -- unverified, likely stale

**Gemini TTS (non-Live, for /api/voice/tts):**
- Model: `gemini-2.5-flash-preview-tts`
- Output: 24 kHz PCM16 mono WAV

### 3.4 pyttsx3 Fallback

**What it provides:** Last-resort offline TTS via the OS speech engine (Windows SAPI5). No ASR -- text-to-speech only.

**Package:** `pyttsx3`
**Used when:** Both Gemini TTS and Piper are unavailable, or when PII is detected in the text (egress gate routes to local voice).

### 3.5 Auto-Tier Resolution

The `resolve_tier()` method on `LocalVoiceEngine` determines which local tier to activate:

| `voice_engine` setting | Resolution |
|------------------------|------------|
| `local` (default) | Tier 1 (CPU) |
| `local-gpu` / `gpu` / `nemo` | Tier 2 if `gpu_tier_ready()`, else Tier 1 |
| `auto` | Tier 2 if `gpu_tier_ready()`, else Tier 1 |
| `gemini` | Cloud path (`/ws/live`), no local tier involved |

The `_resolve_voice_engine()` function in `voice.py` determines the overall engine:

| Preference | Cloud available | Local available | Result |
|------------|----------------|-----------------|--------|
| `gemini` | yes | -- | Cloud (`/ws/live`) |
| `gemini` | no | yes | Local (`/ws/voice-local`) with reason |
| `gemini` | no | no | Demo (text only) |
| `local` / `auto` | -- | yes | Local (`/ws/voice-local`) |
| `local` / `auto` | yes | no | Cloud (`/ws/live`) with reason |
| `local` / `auto` | no | no | Demo (text only) |


---

## 4. Voice Engine Settings

All settings are read from `settings.json` via `core._load_settings()`. Changes take effect on the next voice session (no server restart required).

### 4.1 Engine Selection

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `voice_engine` | string | `"local"` | Engine preference: `"local"` (Tier 1/2), `"gemini"` (Tier 3 cloud), `"auto"`. Local is the default per the ethos: "Local is the default, cloud is the opt-in, always." |
| `voice_model` | string | `"gemini-2.5-flash-native-audio-latest"` | Gemini Live model ID for cloud voice. Env override: `FRIDAY_LIVE_MODEL`. |

### 4.2 Voice & Language

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `tts_voice` | string | `"Aoede"` | Gemini TTS voice name. Applied to both one-shot TTS (`/api/voice/tts`) and Live sessions. Env override: `FRIDAY_LIVE_VOICE`. |
| `voice_language` | string | `""` | BCP-47 language code (e.g., `"en-US"`). Empty uses server default. |
| `voice_style_prompt` | string | `""` | Custom speaking-style instruction prepended to all TTS prompts. Overrides built-in style presets (`briefing`, `chat`, `plain`). |

### 4.3 Live Session Behavior

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `voice_temperature` | float/null | null | Gemini generation temperature for Live sessions. |
| `voice_max_tokens` | int | 0 | Max output tokens for Live responses. 0 = unlimited. |
| `voice_affective` | bool | auto | Enable affective dialog (emotional prosody). Auto-detected based on model: true for `native-audio` models, false otherwise. Only active on `v1alpha` endpoint. |
| `voice_proactive` | bool | auto | Enable proactive audio (model can initiate). Auto-detected based on model. Only active on `v1alpha` endpoint. |
| `voice_tools` | bool | true | Enable the agentic voice tool surface (calendar, email, news, web search, navigation, etc.). |
| `voice_context_compression` | bool | false | Enable sliding-window context compression for extended sessions. |

### 4.4 Interruption & Barge-In

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `voice_interruption_mode` | string | `"speaker"` | `"speaker"` = NO_INTERRUPTION + bridge-side barge detector (echo-safe). `"headphones"` = START_OF_ACTIVITY_INTERRUPTS (native barge-in, no echo concern). |
| `voice_barge_grace_ms` | int | 800 | Grace window at the start of each spoken response during which the `LiveBargeDetector` only learns the speaker-bleed level, never fires. |
| `voice_barge_sustain_ms` | int | 200 | How long deliberate speech above the threshold must be sustained before a barge-in fires. |

### 4.5 Local Voice

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `local_voice_asr_model` | string | `"small"` | faster-whisper model size for Tier 1. Options: `"tiny"`, `"base"`, `"small"`, `"medium"`, `"large-v3"`. |
| `local_voice_tts_voice` | string | `"en_US-amy-medium"` | Piper voice name for Tier 1. Also available: `"en_US-lessac-medium"`. |
| `local_voice_gpu_asr_model` | string | `"nvidia/nemotron-3.5-asr-streaming-0.6b"` | NeMo ASR model for Tier 2. |
| `local_voice_gpu_tts` | string | `"fastpitch-hifigan"` | NeMo TTS voice for Tier 2. |
| `voice_local_rate` | int/null | null | pyttsx3 speech rate (words per minute). Only affects the pyttsx3 fallback path. |
| `voice_silence_ms` | int | 800 | VAD trailing silence duration before end-of-utterance. |

### 4.6 Fallback & Privacy

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `offline_voice_fallback` | bool | true | When Gemini TTS fails, fall back to local pyttsx3/Piper TTS instead of raising. |
| `off_record` | bool | false | When true, voice turns are not logged to context or chat history. |


---

## 5. In-App Installation

### 5.1 Setup Status Endpoint

**`GET /api/voice/setup/status`** (login required)

Returns a structured readiness report:

```json
{
  "ready": true,
  "engine": "local",
  "steps": [
    {
      "id": "deps",
      "label": "Python dependencies (faster-whisper / piper)",
      "status": "ok",
      "detail": "local voice ready"
    },
    {
      "id": "models",
      "label": "ASR / TTS model files (~300 MB, one-time download)",
      "status": "ok",
      "detail": "local voice ready"
    },
    {
      "id": "mic",
      "label": "Microphone",
      "status": "unknown",
      "detail": "Click the mic button to test -- browser will prompt for permission."
    }
  ],
  "engine_info": { "engine": "local", "ws_url": "/ws/voice-local", ... }
}
```

For cloud engine, the steps include:
- `key`: API key validation status (`ok` | `invalid` | `missing`)
- `model`: Live model validation status (`ok` | `unknown`)
- `mic`: Microphone permission status

Step `status` values: `ok`, `missing`, `needs_download`, `invalid`, `unknown`, `unavailable`.

### 5.2 Test Utterance Endpoint

**`POST /api/voice/setup/test`** (login required)

Runs a test TTS synthesis and returns base64-encoded WAV audio:

```json
// Request
{ "text": "Hello, I'm Friday. Voice setup is complete." }

// Response
{
  "status": "ok",
  "audio_b64": "<base64 WAV>",
  "format": "wav"
}
```

Uses `_synthesize_tts_wav()` with `allow_local=True`, so it exercises whichever TTS path is available (Gemini cloud, pyttsx3, or Piper).

### 5.3 Session Info Endpoint

**`GET /api/voice/session-info`**

Returns the resolved engine and WebSocket URL for the current session:

```json
{
  "status": "ok",
  "engine": "local",
  "ws_url": "/ws/voice-local",
  "label": "Local (private, on-device)",
  "tier": "cpu",
  "models_ready": true,
  "reason": "local default"
}
```

### 5.4 Fallback Status Endpoint

**`GET /api/voice/fallback-status`**

Reports degraded voice capabilities when offline:

```json
{
  "status": "ok",
  "network": { "offline": false, ... },
  "cloud_voice": true,
  "local_tts": true,
  "local_llm": true,
  "recommended_mode": "cloud",
  "voice_model": "gemini-2.5-flash-native-audio-latest"
}
```

Recommended modes: `cloud` > `local` > `tts_only` > `unavailable`.


---

## 6. Voice Setup Wizard

### 6.1 Wizard Flow

The setup wizard should guide the user through voice activation without requiring terminal access. The following steps describe the ideal wizard implementation for the Settings UI.

**Step 1: Check Dependencies**

```
Call: GET /api/voice/setup/status
Read: steps[id="deps"].status

if status == "ok":
    Show green check, proceed to Step 2
elif status == "missing":
    Show install prompt:
      "Voice requires additional Python packages.
       Install now? (This runs pip install in the background.)"
    Action: POST to a future /api/voice/setup/install endpoint
            that runs `pip install -e ".[voice-local-lite]"`
    On success: re-check deps, proceed to Step 2
```

**Step 2: Download Models**

```
Read: steps[id="models"].status

if status == "ok":
    Show green check, proceed to Step 3
elif status == "needs_download":
    Show download prompt:
      "Voice models need to download (~300 MB, one-time).
       This happens automatically on first voice session,
       or you can trigger it now."
    Action: Connect briefly to /ws/voice-local and disconnect.
            The handler calls engine.ensure_ready(progress=...)
            which downloads models. The progress callback emits
            {type:'status', text:'Downloading voice models...'} frames.
    On success: re-check, proceed to Step 3
```

**Step 3: Test Microphone**

```
Action: Request navigator.mediaDevices.getUserMedia({audio: true})
        in the browser.

if permission granted:
    Capture 2 seconds of audio
    Compute RMS of the captured PCM16
    if RMS > 200 (speech detected):
        Show green check, proceed to Step 4
    else:
        Show warning: "Microphone is accessible but no audio detected.
                       Check your input device selection."
elif permission denied:
    Show warning: "Microphone permission denied.
                   Voice requires mic access -- click the lock icon
                   in your browser's address bar to allow it."
```

**Step 4: Test TTS**

```
Call: POST /api/voice/setup/test
      { "text": "Hello, I'm Friday. Voice setup is complete." }

if status == "ok" and audio_b64 is not null:
    Decode base64 WAV, play through AudioContext
    Show green check: "You should hear Friday speaking."
    Ask: "Did you hear the test audio?"
    if yes: proceed to Step 5
    if no: offer troubleshooting (check output device, volume)
elif status == "error":
    Show error message from response
    Offer: "Try switching to local voice engine in Settings."
```

**Step 5: Configure Tier**

```
Show the resolved engine info from /api/voice/session-info.

Display options:
  [ ] Local (private, on-device) -- DEFAULT
      Uses: faster-whisper + Piper
      Privacy: nothing leaves this machine

  [ ] Cloud (Gemini Live) -- opt-in
      Uses: Google Gemini Live API
      Requires: API key + internet
      Features: affective dialog, proactive audio, tools

  [ ] Local GPU (NeMo) -- premium (if GPU detected)
      Uses: NVIDIA Nemotron + FastPitch
      Requires: RTX GPU + torch-CUDA + NeMo

On selection: update settings.json voice_engine value
              re-check via /api/voice/session-info
```

### 6.2 Health Integration

The `LocalVoiceEngine.health()` method returns a structured status block for `/api/health/full`:

```json
{
  "engine": "local-voice-lite",
  "status": "ok",
  "detail": "local voice ready",
  "available": true,
  "models_ready": true,
  "deps": {
    "faster_whisper": true,
    "piper": true,
    "onnxruntime": true,
    "silero_vad": true
  },
  "asr_model": "small",
  "tts_voice": "en_US-amy-medium",
  "active_tier": "cpu",
  "perf": {
    "asr_ms": 342.1,
    "asr_count": 15,
    "tts_ms": 187.3,
    "tts_count": 15,
    "tier": "cpu"
  },
  "gpu": {
    "engine": "nvidia-nemo",
    "status": "missing",
    "detail": "NeMo GPU voice not installed...",
    "available": false,
    "models_ready": false
  }
}
```


---

## 7. Error Recovery

### 7.1 Stale Model IDs

**Symptom:** Voice connect fails with 1008 close code. Old error handler reports "API key invalid" but the key is fine.

**Root cause:** Google retires Gemini Live model IDs without notice. A stale ID in `settings.json` or the code default triggers a 1008 "not found for API version" error that looks like an auth failure.

**Detection:** `_classify_live_error()` parses the error message for "not found for api version" or "not supported for bidigeneratecontent" and returns `model-missing` instead of `auth`.

**Auto-recovery:** `_resolve_voice_engine()` calls `validate_live_model()` before connecting. If the configured model has status `unknown`, it resets `voice_model` to `LIVE_MODEL` in settings.json.

**Manual fix:** Settings -> Voice -> change voice model to a known-good ID listed in `_KNOWN_LIVE_MODELS`.

### 7.2 Rotated API Keys

**Symptom:** Voice was working, now gets auth errors.

**Root cause:** The user updated their key via `setx` or System Properties, but the running process inherited the old key at startup and `os.environ` is frozen.

**Auto-recovery:** `resolve_gemini_key()` probes multiple sources in order:
1. Process env `GEMINI_API_KEY`
2. Process env `GOOGLE_API_KEY`
3. `settings.json` `gemini_api_key`
4. Windows registry `HKCU\Environment` (live-read via `winreg`)
5. Server startup value (`core.GEMINI_API_KEY`)

The first key that passes `validate_gemini_key()` (cached REST probe to `v1beta/models?pageSize=1`) wins and is installed into `core.GEMINI_API_KEY`.

**Manual fix:** Paste a fresh key from aistudio.google.com into Settings -> Providers -> Google Gemini. Takes effect on next voice session.

### 7.3 Retired Models

**Symptom:** Same as 7.1 but across the entire fallback chain.

**Detection:** `_compose_final_voice_error()` aggregates per-attempt errors. When all attempts report `model-missing`, the message explicitly says "this is a MODEL problem, not an API-key problem" and lists what was tried.

**Fix:** Update `LIVE_MODEL`, `LIVE_MODEL_FALLBACK`, and `LIVE_MODEL_FALLBACK2` in `voice_engine.py` to current model IDs. Verify with: `curl -H "x-goog-api-key: $KEY" "https://generativelanguage.googleapis.com/v1beta/models?pageSize=100"`.

### 7.4 pyttsx3 Missing

**Symptom:** `_synthesize_tts_wav_local()` returns `None`. Offline TTS unavailable.

**Detection:** `_local_tts_available()` catches the import failure and tries Piper as an alternative.

**Recovery chain:** pyttsx3 -> Piper (via `get_local_voice_engine()`) -> `None` (caller uses Gemini TTS or raises).

**Fix:** `pip install pyttsx3`.

### 7.5 torch CPU-Only

**Symptom:** Tier 2 never activates despite an NVIDIA GPU being present.

**Detection:** `gpu_status()` reports `cuda: false, detail: "torch installed but CUDA not available"`.

**Fix:**
```bash
pip uninstall torch
pip install torch --index-url https://download.pytorch.org/whl/cu124
```

### 7.6 CUDA Unavailable

**Symptom:** `gpu_tier_ready()` returns false. Tier 2 cannot activate.

**Possible causes:**
- No NVIDIA GPU
- GPU present but VRAM < 4 GB (`MIN_VRAM_GB`)
- CUDA driver not installed or version mismatch
- torch CPU-only (see 7.5)

**Recovery:** `LocalVoiceEngine.ensure_ready()` automatically falls back to Tier 1 CPU. The user always gets local voice.

### 7.7 Microphone Permission Denied

**Symptom:** `getUserMedia()` fails in the browser. No audio frames reach the WebSocket.

**Detection:** `no_audio_watchdog()` fires after 5 seconds with zero audio chunks: `"WARNING: no audio chunks received from browser after 5s -- mic likely silent or WS not flowing"`. Sends `{type:'status', text:'no mic audio reaching server'}` to the client.

**Fix:** User must grant microphone permission in the browser. The lock icon in the address bar allows re-prompting.

### 7.8 VAD Not Detecting Speech

**Symptom:** Audio arrives at the server but no transcription occurs.

**Possible causes:**
- Microphone gain too low -- RMS below the `start_rms=600.0` threshold
- Silero VAD rejecting atypical audio (synthetic tones, heavily compressed)
- `voice_silence_ms` set too low -- utterances truncated before completion

**Debug:** Enable `FRIDAY_VOICE_DEBUG=1` for per-chunk RMS logging. The reader task logs `rms=` and `peak=` for every chunk at indices 1, 5, 25, and every 50th chunk.

**Fix:** Increase microphone gain, or set `voice_silence_ms` to 1000+.

### 7.9 Echo / Barge-In Problems

**Symptom (echo):** Friday cuts herself off mid-sentence on speakers. Her own voice, re-captured by the mic, triggers interruption.

**Fix applied:** `voice_interruption_mode = "speaker"` (default) sets `ActivityHandling.NO_INTERRUPTION` in the Live API config, plus `TurnCoverage.TURN_INCLUDES_ONLY_ACTIVITY`. The model never self-interrupts from echo.

**Symptom (no barge-in):** With `NO_INTERRUPTION`, the user cannot interrupt at all via Gemini's native mechanism.

**Fix applied:** `LiveBargeDetector` implements bridge-side barge-in. It:
1. Learns the speaker-bleed baseline during a grace window (default 800ms) at the start of each spoken response.
2. Seeds from the 25th percentile of grace-window samples (robust against early interjections).
3. Fires only when mic RMS is both above an absolute floor (550) AND >= 3x the learned bleed level, sustained for 200ms.
4. On fire: sends `{type:'interrupted'}` to the browser (flushes its playback buffer), sends a `client_content` cancel to Gemini, and swallows remaining audio from the model.
5. 1.5-second refractory period between barge firings.

The `BLEED_EMA_CAP = 1500` prevents runaway baseline learning when the user talks during the grace window.

**Headphones mode:** Set `voice_interruption_mode = "headphones"` to restore native Gemini barge-in (`START_OF_ACTIVITY_INTERRUPTS`). No speaker bleed concern with headphones.

### 7.10 GoAway Renewals

**Symptom:** Voice session ends abruptly after 10-15 minutes.

**Cause:** Gemini sends a `go_away` event when the connection lifetime or context cap is reached.

**Handling:** The writer task receives `go_away`, computes a grace period (capped at 0.5--8.0 seconds from `time_left`), and drains the current response before ending the leg. The reconnect loop then renews via the latest session resumption handle. The user hears no break.

**With context compression:** `voice_context_compression = true` enables sliding-window compression, which removes the session-duration cap. Hours-long calls become possible with periodic GoAway-driven renewals.

### 7.11 WebSocket Drops

**Browser socket drops (wifi blip, sleep/wake, tab reload):**
- The client auto-reconnects to `/ws/live`.
- The module-level `_LIVE_RESUME` cache stores the latest resumption handle (TTL: 600 seconds).
- `_live_resume_load()` retrieves it for the matching model+voice, and the new handler resumes the same Gemini conversation.
- Stale audio drain: on reconnect, all buffered mic frames from the old socket are drained and discarded (`_stale_audio` counter) to prevent Gemini from transcribing old speech.

**Gemini socket drops (NAT timeout, proxy, half-open):**
- The `liveness_watchdog()` detects sustained silence from Gemini while the user was recently speaking.
- Fires after `LIVE_STALL_SECONDS = 40` seconds of upstream silence, provided the user finished speaking 8-90 seconds ago and speech occurred after the last Gemini traffic.
- Sets `sdone` to end the leg, triggering renewal via the handle.

### 7.12 Zombie Handlers

**Symptom:** Two `/ws/live` handlers fight for the same Gemini session. Duplicate audio, corrupted resume cache.

**Cause:** Browser reconnects while the old handler's TCP socket hasn't FIN'd yet (half-open).

**Fix:** Connection-generation fencing via `_LIVE_CONN_GEN`. Each handler claims a generation number via `_live_conn_next()`. Only the current generation may:
- Write to the resume cache (`_live_resume_store`)
- Clear the resume cache (`_live_resume_clear`)
- Renew session legs (checked at the top of the reconnect loop via `_live_conn_current`)

A zombie handler that fails the generation check logs "superseded by a newer voice connection -- zombie handler exiting" and sets `done`.


---

## 8. Gemini Live Session Management

### 8.1 Connection Lifecycle

```
Browser /ws/live connect
  |
  v
_live_conn_next() -- claim generation
resolve_gemini_key() -- find working key
  |
  v
Build attempt plan: [(api_version, model_name), ...]
  - v1alpha + configured model (if affective/proactive)
  - v1beta + configured model
  - v1beta + LIVE_MODEL_FALLBACK
  - v1beta + LIVE_MODEL_FALLBACK2
  |
  v
For each attempt:
  Build per-model LiveConnectConfig (strip affective/proactive for non-v1alpha)
  Connect via client.aio.live.connect(model, config)
    |
    v
  Reconnect loop (legs):
    Check _live_conn_current(gen) -- zombie fence
    Try connect with handle (3 retries: handle, handle, fresh)
    Drain stale audio from browser buffer
    Send greeting (first leg of new conversation only)
    Launch asyncio tasks: reader, writer, liveness_watchdog, heartbeat, no_audio_watchdog
    Wait for first core task to finish
    Cancel pending tasks (6s grace)
    If done: break
    Else: renew (leg++)
    Quick-death breaker: 3 consecutive legs dying <10s = give up on this model
```

### 8.2 Resumption Handles

**Purpose:** Enable transparent session renewal after GoAway, stale audio drain on reconnect, and browser reconnect.

**Storage:**
- Per-handler: `resume_handle[0]` (closured list for mutability in nested functions)
- Per-module: `_LIVE_RESUME` dict with `handle`, `ts`, `model`, `voice` fields
- TTL: 600 seconds (`_LIVE_RESUME_TTL_S`)
- Access: guarded by `_LIVE_RESUME_LOCK` + generation fence

**Flow:**
1. Writer receives `session_resumption_update` with `new_handle` from Gemini.
2. Stores in `resume_handle[0]` and mirrors to `_LIVE_RESUME` via `_live_resume_store(gen=conn_gen)`.
3. On leg end (GoAway, stall, stream exhaustion): reconnect loop uses `resume_handle[0]` to build `SessionResumptionConfig(handle=...)`.
4. On browser reconnect: new handler loads from `_live_resume_load(model, voice)` if TTL is valid.
5. On deliberate stop (`{type:'end'}` or `{type:'bye'}`): `_live_resume_clear(gen=conn_gen)` wipes the cache so the next session starts fresh.

### 8.3 GoAway Draining

When Gemini sends `go_away`:
1. Compute grace period: `max(0.5, min(time_left or 3.0, 8.0))` seconds.
2. If model is NOT currently speaking: end leg immediately (`sdone.set()`).
3. If model IS speaking: continue receiving until `turn_complete` or grace deadline, then end leg.
4. Reconnect loop takes over and renews via handle.

### 8.4 Stale Audio Drain

On reconnect (leg > 0 or resuming from handle):
1. Non-blocking drain: `ws.receive(timeout=0)` in a loop.
2. Audio frames are counted and discarded.
3. Control frames (`bye`, `end`) are honored (clear resume, set done).
4. `speaking` frames update the client playback state.
5. Log: `"seam drain: dropped N stale mic chunks buffered during reconnect"`.

### 8.5 Liveness Watchdog

Runs as an asyncio task alongside reader/writer. Checks every 5 seconds:
- If a voice tool is in-flight (`_tool_inflight[0] > 0`): skip (the writer is busy, not stalled).
- If Gemini has been quiet > `LIVE_STALL_SECONDS` (40s) AND the user spoke after the last Gemini traffic AND the user finished speaking 8--90 seconds ago: end the leg for renewal.

The guard against firing during long monologues (models that do not stream input transcription mid-turn) prevents false positives that previously caused unnecessary renewals and conversation desyncs.

### 8.6 LiveBargeDetector

Class in `voice.py`. Single-threaded use within the asyncio reader task.

**State:**
- `ema`: exponential moving average of speaker-bleed RMS (alpha=0.1 during post-grace tracking)
- `sustained`: accumulated milliseconds of above-threshold speech
- `_grace_samples`: RMS values collected during the grace window

**Algorithm:**
1. `reset_turn()` called when model response starts speaking (or client reports `speaking:on`).
2. During grace window: collect `_grace_samples`, never fire.
3. At grace-window end: seed `ema` from 25th percentile of samples (capped at `BLEED_EMA_CAP = 1500`).
4. Post-grace: `threshold = max(floor=550, mult=3.0 * ema)`. RMS above threshold accumulates `sustained`; below threshold decays ema and resets sustained.
5. Fires when `sustained >= sustain_ms` (200ms default).

**Barge execution (`_fire_barge`):**
1. Set `_barged_turn[0] = True` (swallow remaining model audio).
2. Reset playback-window estimates.
3. Send `{type:'interrupted'}` to browser (flushes playback buffer).
4. Send `{type:'status', text:'listening'}`.
5. Send `client_content` to Gemini with a "user interrupted" marker, `turn_complete=False`.

### 8.7 Connection Generation Fencing

Purpose: prevent zombie handlers from corrupting the resume cache or fighting for the Gemini session.

- `_LIVE_CONN_GEN[0]`: monotonically increasing counter, incremented by each new `/ws/live` handler.
- `_live_conn_next()`: atomically increment and return the new generation.
- `_live_conn_current(gen)`: True only if `gen` matches the current value.
- Checked at: resume cache writes, resume cache clears, top of reconnect loop.
- A failed check triggers `done.set()` and the handler exits with a log message.


---

## 9. API Key Resolution

### 9.1 resolve_gemini_key()

**Purpose:** Pick the freshest WORKING Gemini key across every known source, self-healing across key rotations without a server restart.

**Candidate order:**
1. `os.environ["GEMINI_API_KEY"]` -- from the launcher script
2. `os.environ["GOOGLE_API_KEY"]` -- from the launcher script
3. `settings.json` `gemini_api_key` -- from Settings -> Providers
4. Windows registry `HKCU\Environment` `GEMINI_API_KEY` or `GOOGLE_API_KEY` -- live-read via `winreg.OpenKey`
5. `core.GEMINI_API_KEY` -- the value the server booted with

**Validation:** Each candidate is probed with `validate_gemini_key()`:
- `GET https://generativelanguage.googleapis.com/v1beta/models?pageSize=1` with `x-goog-api-key` header.
- HTTP 2xx = valid. HTTP 4xx = invalid. Network error = `(True, "unverifiable -- assuming ok")` so being offline never brands a key bad.
- Results cached per-key for 600 seconds (`_KEY_CHECK_TTL`). Cache key is `sha256(key)[:16]`.

**On success:** The winning key is installed into `core.GEMINI_API_KEY` and `core._genai_client` is reset to `None` (rebuilt lazily with the fresh key). All endpoints (voice, TTS, creative) self-heal.

**Return value:**
```python
{
    "key": "AIzaSy...",
    "source": "Windows user environment (registry)",
    "valid": True,
    "detail": "HTTP 200"
}
```

**Test seam:** Under `FRIDAY_TESTING=1`, returns `core.GEMINI_API_KEY` without network probes.

### 9.2 Windows Registry Key Read

`_read_windows_user_env_key()` live-reads `HKCU\Environment` for `GEMINI_API_KEY` or `GOOGLE_API_KEY`. This catches the classic rotation trap where the user updates the key via `setx` but the running process still holds the old value.

Returns empty string on non-Windows or when unset.


---

## 10. Voice Tool Surface

### 10.1 Tool Declarations

The `_VOICE_LIVE_TOOLS` list defines the function declarations exposed to the Gemini Live session. Each entry is `(name, description, {param: (type, desc)}, [required])`.

| Tool | Description | Parameters |
|------|-------------|------------|
| `query_calendar` | Today's + tomorrow's calendar events | (none) |
| `check_email` | Recent email with urgent/unread flags | `urgent_only` (bool), `limit` (int) |
| `search_news` | Live news feed search | `query` (string), `limit` (int) |
| `search_web` | Real-time web search | `query` (string, required) |
| `open_url` | Open URL in browser (ask permission first) | `url` (string, required), `title` (string), `confirmed` (bool) |
| `get_source_trust` | Trust profile for a news source | `domain` (string, required) |
| `get_article_deep_dive` | Deep-read + summarize an article | `url` (string, required), `title` (string) |
| `search_wiki` | Search Friday's personal wiki | `query` (string, required), `limit` (int) |
| `navigate_workspace` | Switch the desktop UI to a workspace | `workspace` (string, required), `confirmed` (bool) |

### 10.2 Tool Declaration Rendering

`_build_voice_live_tools(types)` renders `_VOICE_LIVE_TOOLS` into `google.genai.types.Tool` objects:
- Maps type strings (`"string"`, `"integer"`, `"boolean"`, `"number"`) to `types.Type` enums.
- Calls `_navigate_tool_description()` to fill the `{workspace_ids}` placeholder from `_WORKSPACE_ALIASES`.
- Returns a single-element list `[types.Tool(function_declarations=[...])]`.

### 10.3 Tool Execution Flow

1. Gemini returns a `tool_call` chunk with `function_calls[]`.
2. `_run_tool_calls(sess, tc)` in the writer task iterates over each function call.
3. For each call: extract `name`, `args`, `id`.
4. Send `{type:'status', text:'tool_name'}` to browser.
5. Execute `_voice_tool_run(name, args, send_client)` in a worker thread (`asyncio.to_thread`).
6. `_tool_inflight[0]` is incremented before execution and decremented after (prevents liveness watchdog from false-firing during long tool runs).
7. Build `types.FunctionResponse(name, response, id)`.
8. Send all responses back via `sess.send_tool_response(function_responses=[...])`.

### 10.4 Tool-Specific Behaviors

**Confirmation pattern (`open_url`, `navigate_workspace`):**
If `confirmed` is not true, the tool returns a prompt asking the model to get spoken permission first. Only when the user agrees and the model re-calls with `confirmed=true` does the action execute.

**Side effects via `send_client`:**
- `navigate_workspace`: Sends `{type:'action', actions:[{type:'navigate', workspace:id}]}` to the browser.
- `open_url`: Sends `{type:'cite', label:'Opened', sources:[...]}` citation chip.
- `search_news`: Sends `{type:'cite', label:'Related stories', sources:[...]}` for up to 6 hits.
- `get_article_deep_dive`: Sends a `{type:'cite', label:'Deep dive', ...}` chip.

**TTS pause/resume:** `navigate_workspace` and `open_url` send `{type:'tts_pause'}` before the action and `{type:'tts_resume'}` after, so Friday's speech does not overlap with navigation sounds or browser activity.

### 10.5 Backend Handlers

| Tool | Handler | Data Source |
|------|---------|-------------|
| `query_calendar` | `_tool_query_calendar` | `_fetch_calendar_today()` from calendar_engine |
| `check_email` | `_tool_check_email` | `_collect_messages()` from calendar_engine |
| `search_news` | `_tool_search_news` | RSS feed via agent module |
| `search_web` | `_tool_search_web` | Web search via agent module |
| `open_url` | `_tool_open_url` | OS browser launch via agent module |
| `get_source_trust` | `_tool_get_source_trust` | Trust graph from `core.get_source_trust_graph()` |
| `get_article_deep_dive` | `_tool_get_article_deep_dive` | `_deep_dive_article()` from news_engine |
| `search_wiki` | `_tool_search_wiki` | Wiki search via agent module |
| `navigate_workspace` | `_tool_navigate` | Workspace resolver via agent module |


---

## 11. Testing Plan

### 11.1 Unit Tests -- Tier 1

| Test | What it validates | Approach |
|------|-------------------|----------|
| `test_vad_endpointer_fires_on_silence` | VAD accumulates speech and fires after `silence_ms` of trailing silence | Synthetic PCM16 square wave (speech) followed by silence. Use `use_silero=False` to force the RMS gate. |
| `test_vad_endpointer_respects_min_speech` | VAD does not fire on a sub-`min_speech_ms` burst | Short burst + long silence. Assert `feed()` returns `None`. |
| `test_vad_flush` | `flush()` returns accumulated audio and resets | Feed speech, call `flush()`, verify bytes returned and state reset. |
| `test_whisper_asr_transcribe` | `WhisperASR.transcribe()` returns text from PCM16 | Inject a `FakeASR` that returns a canned string. Verify the engine calls `transcribe()` with the right bytes. |
| `test_piper_tts_synthesize` | `PiperTTS.synthesize()` returns 24kHz PCM16 | Inject a `FakeTTS`. Verify `synthesize()` is called and output is resampled. |
| `test_resample_pcm16` | `_resample_pcm16()` correctly changes sample rate | 16kHz -> 24kHz on known data. Verify length ratio and no crashes on edge cases (empty, same rate). |
| `test_pcm16_rms` | `_pcm16_rms()` computes correct RMS | Known signal. Verify against hand-calculated value. |
| `test_engine_ensure_ready_loads_models` | `ensure_ready()` calls `asr.load()` and `tts.load()` | Inject fakes, verify `load()` called with progress callback. |
| `test_engine_gpu_fallback_to_cpu` | GPU tier that fails to load degrades to CPU | Set tier to "gpu", make `_gpu_tier_ready()` return False. Verify tier swaps to "cpu". |
| `test_split_sentences` | `split_sentences()` correctly splits on sentence boundaries | "Hello. World! How are you?" -> ["Hello.", "World!", "How are you?"] |

### 11.2 Unit Tests -- Tier 2

| Test | What it validates | Approach |
|------|-------------------|----------|
| `test_nemo_deps_status` | `nemo_deps_status()` correctly probes importability | Mock `importlib.util.find_spec`. |
| `test_gpu_status_torch` | `gpu_status()` reads CUDA info from torch | Mock `torch.cuda.is_available()`, `torch.cuda.get_device_name()`, `torch.cuda.mem_get_info()`. |
| `test_gpu_status_smi_fallback` | Falls back to nvidia-smi when torch unavailable | Mock `_module_installed("torch")` as False, mock `ollama_manager`. |
| `test_float_to_pcm16` | `_float_to_pcm16()` clips and converts correctly | Known float32 array. Verify PCM16 output. |
| `test_nemo_health_ladder` | `nemo_health()` returns correct status for each condition | Mock deps/gpu at each ladder rung (ok, needs_download, down, missing). |

### 11.3 Unit Tests -- Tier 3 / Voice Engine

| Test | What it validates | Approach |
|------|-------------------|----------|
| `test_validate_gemini_key_valid` | Returns `(True, ...)` for a valid key | Mock `urllib.request.urlopen` to return HTTP 200. |
| `test_validate_gemini_key_invalid` | Returns `(False, ...)` for a revoked key | Mock HTTP 401 or 403 response. |
| `test_validate_gemini_key_offline` | Returns `(True, "unverifiable...")` when network is down | Mock socket error. |
| `test_resolve_gemini_key_priority` | Picks the first valid key in candidate order | Set env vars and settings with different keys, make first invalid, verify second wins. |
| `test_resolve_gemini_key_installs` | Winning key is written to `core.GEMINI_API_KEY` | Verify `core.GEMINI_API_KEY` updated and `core._genai_client` reset. |
| `test_validate_live_model_known` | Returns `ok` for `_KNOWN_LIVE_MODELS` members | Direct call with known model IDs. |
| `test_validate_live_model_unknown` | Returns `unknown` for unrecognized IDs | Call with `"gemini-nonexistent-model"`. |
| `test_classify_live_error` | Correctly classifies error messages | Test strings for model-missing, auth, and other patterns. |
| `test_compose_final_voice_error_auth` | Produces correct message when key is bad | Mock `validate_gemini_key(force=True)` to return `(False, ...)`. |
| `test_compose_final_voice_error_model` | Produces correct message when all models missing | All attempts with kind=`model-missing`. |
| `test_voice_tool_run_confirm_gate` | Tools with `confirmed` gate return prompt when not confirmed | Call `_voice_tool_run("open_url", {"url": "..."})` without `confirmed=true`. |
| `test_persist_voice_turn` | Voice turns saved to CHAT_HISTORY and context log | Call `_persist_voice_turn()` and verify CHAT_HISTORY entries with `via: 'voice'`. |

### 11.4 Integration Tests

| Test | What it validates | Approach |
|------|-------------------|----------|
| `test_local_voice_e2e` | Full local pipeline: audio -> VAD -> ASR -> brain -> TTS -> audio | Inject FakeASR + FakeTTS into the engine. Connect a test WebSocket client to `/ws/voice-local`. Send a b64-encoded audio frame with speech-like RMS. Verify the handler produces `input_transcript`, `text`, `audio`, and `turn_end` frames. |
| `test_voice_session_info` | `/api/voice/session-info` returns correct engine/URL | Call the endpoint with different `voice_engine` settings. Verify `ws_url` and `engine` fields. |
| `test_voice_setup_status` | `/api/voice/setup/status` reports correct readiness steps | Call with deps installed vs. missing. Verify step statuses. |
| `test_voice_setup_test` | `/api/voice/setup/test` returns base64 WAV audio | Call the endpoint. Verify `audio_b64` decodes to a valid WAV. |
| `test_barge_detector_echo_safe` | Barge detector does not fire on speaker bleed | Simulate grace-window bleed samples + post-grace bleed below threshold. Verify no fire. |
| `test_barge_detector_fires_on_speech` | Barge detector fires on deliberate speech over speaker | Simulate low bleed in grace, then high RMS sustained past threshold. Verify fire. |

### 11.5 Manual Test Procedures

**Local Voice (Tier 1):**
1. Set `voice_engine: "local"` in settings.json.
2. Open the Friday desktop. Click the mic button.
3. Verify the status shows "starting local voice" then "live".
4. Speak a question. Verify transcript appears (`input_transcript`) and Friday responds with audio.
5. Verify the holographic cube animates during speech.
6. End the session. Verify `voice_turn_done` frame contains `user_text` and `agent_text`.

**Gemini Live (Tier 3):**
1. Set `voice_engine: "gemini"` in settings.json.
2. Verify a valid API key is configured (`/api/voice/setup/status` step `key: ok`).
3. Click the mic button. Verify "loading context" then "live" status.
4. Ask "what's on my calendar?" -- verify the `query_calendar` tool fires and Friday speaks the results.
5. Ask "open that article in my browser" about a news story -- verify the confirmation flow (Friday asks, you confirm, then it opens).
6. Test barge-in (speaker mode): while Friday is speaking, say "stop" clearly. Verify she stops and listens.
7. Wait >10 minutes for a GoAway. Verify the session renews transparently (no audible break, "reconnecting" status briefly appears).
8. Close and reopen the browser tab. Verify the session resumes from the cached handle.

**Fallback Verification:**
1. Remove `GEMINI_API_KEY` from all sources. Set `voice_engine: "gemini"`.
2. Click mic. Verify it falls back to local voice with reason "cloud unavailable, using local".
3. Remove `faster-whisper` and `piper-tts`. Set `voice_engine: "local"`.
4. Click mic. Verify it degrades to "demo" (text only) with reason "install .[voice-local-lite]".


---

## 12. Known Limitations & Future Work

### 12.1 NeMo on Windows

NeMo is Linux-first. On Windows with an RTX card, it usually works under a recent torch-CUDA wheel, but installation can be painful (C++ build tools, protobuf version conflicts, numba/llvmlite ABI mismatches). If the clean install proves too difficult, the Tier-1 fallback (onnxruntime, rock-solid on Windows) keeps voice working. This is an acceptable trade-off: Tier 2 is a premium upgrade, not a requirement.

### 12.2 Piper Voice Selection

Currently two voices are bundled in `_PIPER_VOICE_PATHS`: `en_US-amy-medium` and `en_US-lessac-medium`. The Piper ecosystem has dozens of voices across multiple languages (hosted at `huggingface.co/rhasspy/piper-voices`). A future Settings -> Voice panel could let the user browse and download additional voices, with a preview playback. The `PiperTTS` class already supports arbitrary voice names -- only the download URL resolution needs extending.

### 12.3 Streaming ASR (Tier 2)

The Nemotron-3.5 model supports cache-aware streaming with configurable `att_context_size` (the latency/WER dial). Currently, the ASR backend transcribes complete VAD-endpointed utterances (batch mode). A future enhancement could stream partial transcripts during speech, enabling a "thinking" indicator and reducing perceived latency. The model's `set_default_att_context_size([56, 3])` call is already in place for this.

### 12.4 True Barge-In vs. Speaker Mode

The current `LiveBargeDetector` is a good-enough bridge-side solution, but it has inherent limitations:
- The grace window (800ms) means the first 800ms of a response cannot be interrupted.
- The bleed baseline can be thrown off by sudden environmental noise changes mid-response.
- The barge cancel (`client_content` with `turn_complete=False`) is a heuristic -- Gemini may still generate a few more audio chunks before stopping.

True barge-in (headphones mode) works perfectly because Gemini handles it natively (`START_OF_ACTIVITY_INTERRUPTS`). For speaker users who want instant barge-in without echo, the ideal solution is acoustic echo cancellation in the browser or a server-side AEC filter on the mic stream before it reaches Gemini.

### 12.5 Voice-to-Voice Latency

The Tier-1 local pipeline has perceptible latency: VAD endpoint (~800ms silence) + ASR (~300-500ms for "small" model on CPU) + LLM brain (~1-3s depending on provider) + per-sentence TTS streaming. Total first-word latency is typically 2-5 seconds. Tier 2 (GPU) reduces ASR to ~100-200ms but the LLM brain remains the bottleneck.

Gemini Live (Tier 3) is significantly faster because the model generates audio natively (no separate ASR->text->TTS pipeline). First-audio latency is typically under 1 second.

### 12.6 Multi-Language Support

Piper has voices for 30+ languages. faster-whisper supports 99 languages via the Whisper model family. The infrastructure supports multi-language via `voice_language` (BCP-47) for Gemini and `language=None` (auto-detect) for faster-whisper. However, Piper voice selection is currently English-only in the bundled paths. Extending `_PIPER_VOICE_PATHS` with additional language voices is straightforward.

### 12.7 PII / Egress Gate

The `_synthesize_tts_wav()` function includes a PII gate: text containing vault values (detected by `core._scrub_pii()`) is synthesized locally (pyttsx3 or Piper) at full fidelity. If local TTS is unavailable, only the scrubbed text (with `[redacted]` markers) is sent to Gemini TTS. This is the egress boundary for spoken content. The Gemini Live system instruction is separately gated by `_vault_control` (TIER_1 passes, TIER_2 redacted, TIER_3 dropped).

### 12.8 Voice Turn Persistence

Voice turns are persisted to:
1. `CHAT_HISTORY` (in-memory + JSON file) with `via: 'voice'` marker.
2. Context log (`_log_context` with `voice_user` / `voice_agent` event types).
3. ChromaDB via `_index_chat_turn()` in a daemon thread (cross-session recall).
4. Wiki distillation: `_spawn_voice_distill()` runs a Claude task that reviews the transcript and proposes `propose_wiki_update` calls for new durable facts.

The `off_record` setting suppresses all persistence.

### 12.9 Quick-Death Breaker

If three consecutive session legs die within 10 seconds of connecting, the reconnect loop raises `RuntimeError` and gives up on that model. This prevents a reconnect storm when something systemic is wrong (model retired, API key permanently invalid, network proxy blocking WebSocket upgrades).

### 12.10 v1alpha vs. v1beta Endpoint

The Gemini Live API has two endpoints:
- `v1alpha`: Supports `enable_affective_dialog` and `proactive_audio`. Requires OAuth 2.0 on some tiers (AI Studio API keys may be rejected with 1008 "Expected OAuth 2 access token").
- `v1beta` (default): Reliably accepts API-key auth. Does not support affective/proactive features.

The attempt plan tries `v1alpha` first only when affective or proactive features are enabled AND the model supports them. If `v1alpha` fails with an auth error, it falls through to `v1beta` with those features stripped. This is transparent to the user.


---

## Appendix A: Quick Reference -- File Locations

| Item | Path |
|------|------|
| Voice engine (cloud TTS, Live config, tools) | `src/agent_friday/services/voice_engine.py` |
| Local voice engine (Tier 1, CPU) | `src/agent_friday/services/local_voice.py` |
| NeMo voice engine (Tier 2, GPU) | `src/agent_friday/services/nemo_voice.py` |
| Voice routes + WebSocket handlers | `src/agent_friday/routes/voice.py` |
| Settings file | `~/.friday/settings.json` |
| Tier-1 ASR models | `~/.friday/local_voice/whisper/` |
| Tier-1 TTS voices | `~/.friday/local_voice/piper/` |
| Tier-2 NeMo models | `~/.friday/models/nemo/` |
| Voice debug log | `~/.friday/voice_debug.log` (when `FRIDAY_VOICE_DEBUG=1`) |

## Appendix B: Environment Variables

| Variable | Purpose |
|----------|---------|
| `GEMINI_API_KEY` | Primary Gemini API key (process env) |
| `GOOGLE_API_KEY` | Alternative Gemini API key (process env) |
| `FRIDAY_LIVE_MODEL` | Override default Live model ID |
| `FRIDAY_LIVE_VOICE` | Override default Live voice name |
| `FRIDAY_VOICE_DEBUG` | Set to `1` for per-chunk voice logging |
| `FRIDAY_HOME` | Friday's state directory (replaces `~/.friday` itself, not `~`) |
| `FRIDAY_TESTING` | Set to `1` for test mode (skip network probes, Silero) |
| `FRIDAY_WS_TOKEN` | WebSocket authentication token |

## Appendix C: WebSocket Message Contract

Both `/ws/voice-local` and `/ws/live` use the same message contract:

**Browser -> Server:**

| Type | Fields | Description |
|------|--------|-------------|
| `audio` | `data` (b64 PCM16@16k) | Mic audio chunk |
| `image` | `data` (b64 JPEG) | Camera frame (Live only) |
| `text` | `text` (string) | Typed/queued text turn |
| `speaking` | `on` (bool) | Client playback state transition |
| `barge` | -- | Explicit interrupt request (Escape key) |
| `end` | -- | Deliberate stop with final flush |
| `bye` | -- | Deliberate stop without flush |

**Server -> Browser:**

| Type | Fields | Description |
|------|--------|-------------|
| `audio` | `data` (b64 PCM16@24k) | TTS audio chunk |
| `text` | `text` (string) | Model text or output transcript |
| `input_transcript` | `text` (string) | User speech transcript |
| `status` | `text` (string) | Status message (e.g., "live", "thinking") |
| `turn_end` | -- | Model finished responding |
| `interrupted` | -- | Model response was interrupted |
| `voice_turn_done` | `user_text`, `agent_text` | Complete turn for persistence |
| `error` | `error` (string) | Error message |
| `hb` | `ts` (int) | Heartbeat (every 15s) |
| `action` | `actions` (array) | UI actions (navigate, open) |
| `cite` | `label`, `sources` | Citation chip for tool results |
| `tts_pause` / `tts_resume` | -- | Pause/resume client TTS during actions |
