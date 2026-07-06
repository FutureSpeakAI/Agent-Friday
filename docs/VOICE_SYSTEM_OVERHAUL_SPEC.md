# Voice System Overhaul — Specification

**Status:** Draft · **Author:** Engineering · **Date:** 2026-07-06
**Goal:** Make every voice tier work reliably, installable from the UI, and bulletproof for a brand-new user who has never opened a terminal.

---

## 0. Executive summary

Friday has three voice tiers and a genuinely sophisticated backend. The problem is **not** that voice is architecturally broken — the `/ws/live` bridge in particular is one of the most hardened pieces of code in the repo (reconnect loops, session-resumption handles, liveness watchdogs, echo-aware barge-in, model fallback chains, auth-vs-model error classification). The problem is that reliability lives almost entirely on the **server**, while the **client and the setup story are thin**, and a handful of **install/packaging gaps** silently disable fallbacks.

### What was actually found (runtime probe, 2026-07-06, this machine)

| Tier | Component | State | Evidence |
|---|---|---|---|
| **1 — CPU local** | faster-whisper + Piper | ✅ **Working** | deps import; `whisper-small` + `en_US-amy-medium` on disk under `~/.friday/local_voice/` |
| **2 — GPU local** | NeMo Nemotron + FastPitch/HiFi-GAN | ❌ **Cannot run** | `nemo` not installed; `torch` present but **CUDA not available** (CPU-only wheel) → `gpu_tier_ready()==False` |
| **3 — Cloud** | Gemini Live | ✅ **Key valid right now** | `resolve_gemini_key()` → `valid=True, HTTP 200`; configured model `gemini-2.5-flash-native-audio-preview-12-2025` recognized |
| **Fallback TTS** | pyttsx3 | ❌ **Dead** | `pyttsx3` declared in `[all]` but **not installed** in this venv → `_local_tts_available()==False` |

### The five real problems

1. **The offline/PII TTS fallback is dead.** `_synthesize_tts_wav_local()` depends on `pyttsx3`, which isn't installed — even though **Piper (installed, working) could do the same job**. So `/api/voice/tts`, the News briefing audio, and the PII-safe local-TTS egress path all fail silently when Gemini is unreachable. (`services/voice_engine.py:411-465`, `:483-517`)
2. **The client never falls through tiers.** If `/ws/live` emits `{type:'error'}`, the browser disarms auto-reconnect and shows the message — it does **not** retry on `/ws/voice-local`. All tier-switching intelligence is server-side and only *within* Gemini model variants. (`ui_parts/app.html:8358-8372`)
3. **Tier-2 install requires a terminal.** There is no "Upgrade to GPU Voice" button; `docs/TIER2_NEMO_VOICE.md` tells the user to run `pip install` by hand. The installers "do not currently auto-detect GPUs."
4. **The first-run wizard is two disconnected things.** `setup_wizard.py` is a **rich/terminal CLI** (never seen by a UI-only user); the in-UI wizard has only a thin "hardware" step that polls `/api/voice/setup/status`. Neither requests mic permission, shows a model-download progress bar, or offers the GPU tier. (`setup_wizard.py:519-569`, `ui_parts/app.html:6139-6217`, `:6621-6645`)
5. **No single health surface.** There is `/api/voice/fallback-status`, `/api/voice/session-info`, `/api/voice/setup/status`, and the `local_voice` block of `/api/health/full` — but **no `GET /api/voice/health`** giving per-tier `available/unavailable/error + reason` for the UI to poll. The status indicator shows `LIVE · <status>` with no tier label. (`ui_parts/app.html:9507-9512`)

Everything below is scoped to close those five gaps without regressing the hard-won `/ws/live` robustness.

---

## 1. Voice architecture audit

### 1.1 File map (every voice-related module and its role)

**Backend — transport & orchestration**
| File | Role |
|---|---|
| `src/agent_friday/routes/voice.py` | The two WebSocket handlers: `/ws/voice-local` (Tier 1/2 local) and `/ws/live` (Tier 3 Gemini). Also `/api/voice/tts`, `/api/voice/fallback-status`, `/api/voice/session-info`, `/api/voice/setup/status`, `/api/voice/setup/test`. Owns `_resolve_voice_engine()` (the tier picker), `LiveBargeDetector`, the cross-connection resumption cache, and the reconnect/leg-renewal loop. |
| `src/agent_friday/services/voice_engine.py` | Non-WS voice services: Gemini key validation + multi-source self-healing (`resolve_gemini_key`, `validate_gemini_key`), Live model constants + validation (`LIVE_MODEL`, `_get_live_model`, `validate_live_model`), the voice tool surface (`_VOICE_LIVE_TOOLS`, `_voice_tool_run`), TTS synthesis (`_synthesize_tts_wav` + Gemini/local backends), voice-turn persistence + distillation. |
| `src/agent_friday/services/local_voice.py` | Tier-1 engine: `VADEndpointer`, `WhisperASR`, `PiperTTS`, `LocalVoiceEngine` singleton (tier resolution, lazy model download, `health()`), `split_sentences`. Pure-CPU, stdlib audio helpers, never imports heavy libs eagerly. |
| `src/agent_friday/services/nemo_voice.py` | Tier-2 engine: `NeMoASR`, `NeMoTTS`, GPU/VRAM probing (`gpu_status`, `gpu_tier_ready`), `nemo_health`. Interface-compatible with Tier-1 so `LocalVoiceEngine` swaps it in. |
| `src/agent_friday/voice_personality.py` | Builds the Live system instruction with mood/affective-dialog awareness (`get_voice_personality`). |

**Backend — setup & config**
| File | Role |
|---|---|
| `src/agent_friday/setup_wizard.py` | **Terminal** first-run wizard (rich). Has a voice-engine step + TTS-persona step. Not reachable by a UI-only user. |
| `src/agent_friday/routes/onboarding.py`, `services/onboarding.py` | In-UI onboarding backend. |

**Frontend — `ui_parts/app.html`** (compiled into `index.html`)
| Region | Role |
|---|---|
| `7956-7998` | `FridayPCMPlayer` AudioWorklet — 60 s ring buffer @ 24 kHz, linear-interpolation resample on the audio thread. |
| `8057-8179` | Mic capture: `getUserMedia`, ScriptProcessor (4096), 16 kHz PCM16 encode → `{type:'audio'}`, silence guards + mic auto-recovery. |
| `8190-8206` | Session start: fetch `/api/voice/session-info` → open WS at `ws_url`. |
| `8216-8441` | Client resilience: heartbeat watchdog, exponential-backoff reconnect, error handling. |
| `8500-8517` | Escape-key barge-in. |
| `9507-9561` | Status indicator, mic button, audio-device dropdown. |
| `6139-6217`, `6621-6724` | Setup wizard steps + Settings → Voice panel. |

### 1.2 The full chain: mic click → playback

```
[mic button click]  app.html:9529 toggleVoice()
   │
   ├─► GET /api/voice/session-info        voice.py:539 _resolve_voice_engine()
   │        └─ returns {engine, ws_url, tier, models_ready, reason}
   │            engine=gemini → ws_url=/ws/live ; engine=local → /ws/voice-local
   │
   ├─► new WebSocket(ws_url?t=<ephemeral token>)   app.html:8205
   │
   ├─► getUserMedia({audio: echoCancel+noiseSuppress, deviceId})   app.html:8069
   │        └─ ScriptProcessor(4096) → f2pcm(16k) → base64 → ws.send {type:'audio'}   app.html:8132
   │
   ▼  ── SERVER ────────────────────────────────────────────────────────────────
   LOCAL PATH (/ws/voice-local)              CLOUD PATH (/ws/live)
   voice.py:667                              voice.py:930
   ├ auth (token/password/loopback)          ├ auth + claim connection generation
   ├ get_local_voice_engine()                ├ resolve_gemini_key() (self-heal)
   ├ select_tier_from_settings() cpu|gpu     ├ build vault-gated system prompt + tools
   ├ ensure_ready() (lazy DL, gpu→cpu)       ├ runner(): attempt plan
   ├ VADEndpointer.feed(pcm)                 │   v1alpha(affective)→v1beta→FALLBACK→FALLBACK2
   │   └ endpoint → engine.transcribe()      ├ reconnect loop w/ resumption handles
   │        (WhisperASR | NeMoASR)           │   reader() mic→Gemini + barge detect
   ├ _generate_agent(text) ── the brain      │   writer() Gemini→browser audio/text/tools
   ├ engine.synthesize() per sentence        │   liveness_watchdog / heartbeat / GoAway drain
   │   (PiperTTS | NeMoTTS) → 24k PCM16       │
   └ ws.send {type:'audio'} chunks           └ ws.send {type:'audio'} 24k chunks
   │
   ▼  ── BROWSER ───────────────────────────────────────────────────────────────
   b64ToI16 → Float32 → playerNode.port.postMessage({samples})   app.html:8262
   └─► FridayPCMPlayer ring buffer → speakers   app.html:7956
```

Both paths speak the **identical** browser↔server JSON contract, which is the single best design decision in the system and must be preserved:

```
browser → server:  {audio|image|text|end|bye|barge|speaking}
server → browser:  {status|input_transcript|text|audio|turn_end|
                    voice_turn_done|interrupted|action|cite|hb|error}
```

### 1.3 Failure-point inventory

| # | Failure point | Location | Current behavior | Verdict |
|---|---|---|---|---|
| F1 | Tier picker chooses cloud but key revoked | `voice.py:487` | `resolve_gemini_key().valid` gates cloud; routes to local if invalid | ✅ good |
| F2 | Gemini `1008` at connect | `voice.py:293 _classify_live_error` | Correctly distinguishes auth vs retired-model vs other; **does not** misreport model-missing as auth | ✅ good (historically the #1 misdiagnosis) |
| F3 | Configured Live model retired | `runner()` attempts | Falls back to `LIVE_MODEL_FALLBACK`/`2` | ✅ good |
| F4 | Single Live connection ages out (~10 min) | GoAway drain + resumption | Renews leg via handle, no audible seam | ✅ good |
| F5 | Browser socket drops mid-call | `_LIVE_RESUME` cache | New WS resumes same Gemini session | ✅ good |
| F6 | `/ws/live` fails **entirely** (all models) | client `app.html:8358` | Shows error, **disarms retry, no fall-through to local** | ❌ **gap #2** |
| F7 | Local deps missing | `voice.py:721` | Sends actionable error; client does not auto-install | ⚠️ partial |
| F8 | Local models not downloaded | `voice.py:742` | Lazy download on first session w/ status orb | ✅ good, but no % progress |
| F9 | GPU tier requested, can't run | `local_voice.py:577` | Graceful `gpu→cpu` swap | ✅ good |
| F10 | Gemini/offline, need spoken output | `voice_engine.py:498` | Falls to `pyttsx3` — **not installed** → silent failure | ❌ **gap #1** |
| F11 | Mic silent / permission denied | `app.html:8149` | Silence guards, "No audio detected" | ✅ good, but no explicit permission prompt UX |
| F12 | No unified health for UI | — | Four partial endpoints, no `/api/voice/health` | ❌ **gap #5** |

---

## 2. Tier 1 — Local CPU voice (no GPU, no cloud)

**Stack:** faster-whisper (CTranslate2, INT8 CPU) ASR + Piper (VITS→ONNX) TTS + energy/Silero VAD. **This is the default engine and the universal floor — it must work on any machine with a mic.**

### 2.1 Current state — WORKS

- Deps ride `[all]` and `.[voice-local-lite]`: `faster-whisper`, `piper-tts`, `onnxruntime` (`pyproject.toml:42-48, 95-97`). All import on this machine.
- Models present: `~/.friday/local_voice/whisper/models--Systran--faster-whisper-small` and `~/.friday/local_voice/piper/en_US-amy-medium.onnx` (+`.json`).
- `LocalVoiceEngine.available()==True`, `models_ready()==True`.
- Round-trip path is `mic → VADEndpointer → WhisperASR → _generate_agent → PiperTTS → 24 kHz`. The brain is the **same agentic dispatcher as text chat**, so tools/vault gating/provider routing behave identically.

### 2.2 Out-of-the-box requirements

| Need | Current | Change |
|---|---|---|
| Whisper `small` (~460 MB) | Lazy DL on first session, `download_root=WHISPER_DIR` (`local_voice.py:298-304`) | Add **byte-level progress** (see 2.3) |
| Piper voice (~63 MB) | Lazy DL from HF on first session (`local_voice.py:334-354`) | Same |
| First-time UX | Status orb text only ("Downloading voice models…") | **Progress bar** in wizard + Settings |
| No API keys | ✅ none required | — |
| Fallback if models absent | Session sends status, downloads inline | Add **pre-download button** so first *conversation* isn't the download |

### 2.3 Model-download progress (new)

Today `PiperTTS._ensure_voice_file` uses `urllib.request.urlretrieve` with no progress, and Whisper downloads opaquely inside CTranslate2. Add:

- **New endpoint** `POST /api/voice/local/install` → starts a background thread that force-triggers `WhisperASR.load()` + `PiperTTS.load()`, streaming progress.
- **New endpoint** `GET /api/voice/local/install/status` → `{state: idle|downloading|verifying|ready|error, pct, bytes_done, bytes_total, detail}`.
- Implement Piper download with a `urlretrieve` `reporthook` (it already gives `(block, blocksize, total)`); for Whisper, report file-count/size deltas by polling `WHISPER_DIR` size against a known target (~460 MB) — coarse but honest.
- UI reuses the existing Ollama-pull polling pattern (`app.html:6179-6217`, 4 s interval) but bytes-based.

### 2.4 Test procedure (Tier 1)

1. `rm -rf ~/.friday/local_voice` (simulate fresh user).
2. Settings → Voice → **Local (CPU)**.
3. Click **Install Local Voice** → progress bar fills to 100 % (both models), ends "ready".
4. Click mic → status `live` within ~2 s.
5. Say "What time is it?" → transcript appears (`input_transcript`), Friday replies in Piper voice, cube animates on playback.
6. `GET /api/health/full` → `local_voice.perf.asr_ms`/`tts_ms` populated.
7. Airplane-mode the machine, repeat 4–5 → **still works** (no network dependency).
8. Automated: `tests/unit/test_local_voice.py` covers the wiring with `FakeASR`/`FakeTTS`.

---

## 3. Tier 2 — Local GPU voice (NVIDIA)

**Stack:** Nemotron-3.5 streaming ASR + FastPitch/HiFi-GAN TTS via NeMo. Premium quality for RTX-class machines. Same `/ws/voice-local` contract; `LocalVoiceEngine` hot-swaps the backend.

### 3.1 Current state — CANNOT RUN HERE

- `nemo` **not installed**; `.[voice-local-gpu]` is deliberately **excluded from `[all]`** (opt-in, `pyproject.toml:50-51, 94`).
- `torch` installed but **CUDA unavailable** (CPU-only wheel) → `gpu_status()` reports `"torch installed but CUDA not available"`, `gpu_tier_ready()==False`.
- Correct consequence: `voice_engine='local-gpu'`/`auto` **gracefully degrades to CPU** (`local_voice.py:577-603`). Voice never breaks because of this — it just silently runs Tier 1.

### 3.2 Detection & upgrade path

`gpu_status()` (`nemo_voice.py:97`) already returns the right signal:
- `source:"torch"` + `cuda:true` + `vram_free_gb` → offer GPU tier.
- `source:"nvidia-smi"` (torch CPU-only but NVIDIA card seen via `ollama_manager.detect_hardware`) → "GPU detected, install the CUDA stack to enable premium voice."

**UI:** In Settings → Voice, when `nemo_health().gpu.cuda || gpu.source=='nvidia-smi'` and status ≠ `ok`, show:

> ⚡ **NVIDIA GPU detected (`{device}`, {vram_gb} GB).** Upgrade to premium local voice (NeMo) for sharper transcription and richer speech. [**Install GPU Voice**]

### 3.3 In-app installation (the hard part)

NeMo needs **two** installs a UI-only user can't do today: a CUDA `torch` wheel and `nemo_toolkit[asr,tts]`. Plan:

- **New endpoint** `POST /api/voice/gpu/install` → background thread runs, in order:
  1. `pip install torch --index-url https://download.pytorch.org/whl/cu124` (index configurable per driver).
  2. `pip install -e .[voice-local-gpu]`.
  3. Verify: re-probe `gpu_tier_ready()`.
- **New endpoint** `GET /api/voice/gpu/install/status` → `{state, step, log_tail, pct_est, detail}`. Stream `pip` stdout tail (last ~20 lines) so a stall is visible.
- **Guardrails:**
  - Only offered when `gpu_status().cuda` **or** an NVIDIA card is detected. Never on non-NVIDIA machines.
  - Show a plain-language cost banner: "~3–6 GB download, several minutes."
  - Windows caveat surfaced verbatim from `docs/TIER2_NEMO_VOICE.md` (NeMo is Linux-first; WSL2 is the reliable path). If the install fails, the UI says so and **stays on Tier 1** — never a dead state.
- Models (~1.5 GB) still download lazily on first GPU session into `~/.friday/models/nemo/` with the same progress mechanism as Tier 1 (§2.3).

### 3.4 Graceful degradation (already correct — keep it)

`ensure_ready()` does: GPU-requested-but-not-runnable → swap to CPU **before** importing the heavy stack; GPU load throws → one CPU retry (`local_voice.py:575-603`). The status orb reports "GPU voice not ready — using local CPU voice." **Do not regress this.**

### 3.5 Test procedure (Tier 2) — manual, needs an RTX box

Per `docs/TIER2_NEMO_VOICE.md:82-103`: install → `friday health` shows `needs_download` + GPU line → select Local GPU → first mic click downloads (~1.5 GB) → round-trip in NeMo voice → compare `perf.asr_ms`/`tts_ms` vs CPU → uninstall NeMo and confirm CPU fallback still speaks. Add: **the install itself** must be driven from the UI button, not the shell.

---

## 4. Tier 3 — Gemini Live (cloud)

**Stack:** Gemini Live over WebSocket bridge. Best expressiveness; needs `GEMINI_API_KEY` + network.

### 4.1 The persistent `1008` — root-cause analysis

`1008` is a **policy-violation close code that Gemini Live uses for BOTH bad credentials AND unknown/retired model ids.** Reading the close code as "auth failure" is the historical bug that "cost days" (`voice.py:293-314`). Root causes, in observed order:

1. **Stale key pinned by a launcher.** `os.environ` freezes at process start; a launcher script with a rotated key kept the process on a revoked key while the *working* key sat in the Windows user registry. → **Solved** by `resolve_gemini_key()` (`voice_engine.py:701`): validates candidates over REST and self-heals to the first key Google accepts, no restart. Verified this session: key resolved from launcher env, `valid=True, HTTP 200`.
2. **Retired model id.** `gemini-live-2.5-flash-preview` was retired upstream; connecting returns `1008 "not found for API version…"`. → **Solved** by `_classify_live_error` → `model-missing` + the fallback chain (`voice.py:1305`, `voice_engine.py:601-613`).
3. **v1alpha rejecting API-key auth.** Affective/proactive need the `v1alpha` endpoint, which on the AI-Studio key tier sometimes returns `1008 "Expected OAuth 2 access token"`. → **Solved** by trying `v1alpha` first *only when affective/proactive are on*, then falling through to `v1beta` with those stripped (`voice.py:1287-1304, 1396-1409`).

**Conclusion:** the `1008` failure modes are already correctly diagnosed and worked around server-side. The remaining risk is **cosmetic/telemetry** — making sure the *final* user-facing message (`_compose_final_voice_error`, `voice.py:317`) is always shown and never leaks a raw close code.

### 4.2 Key validation before connect (already present — formalize)

- `_resolve_voice_engine` gates cloud on `resolve_gemini_key().valid` (cheap cached REST probe, `voice.py:487-492`), so a revoked key routes the mic to **local** instead of a doomed `/ws/live`. Keep.
- `validate_live_model()` (`voice_engine.py:780`) flags a stale/renamed model id up front in Settings → Voice, advisory. Keep and surface in the health endpoint (§8).

### 4.3 Model availability & fallback chain (already present)

Attempt plan built in `runner()` (`voice.py:1293-1306`):
```
[v1alpha, configured]   (only if affective/proactive)
[v1beta,  configured]
[v1beta,  LIVE_MODEL_FALLBACK   = gemini-2.5-flash-native-audio-latest]
[v1beta,  LIVE_MODEL_FALLBACK2  = gemini-2.5-flash-native-audio-preview-12-2025]
```
Auth or model-missing errors stop the ladder early with a truthful message; other errors advance. **Keep**; the only change is the cross-tier fallback in §5.

### 4.4 Clear error messages

`_compose_final_voice_error` already produces actionable text ("key invalid/revoked from `<source>`" vs "MODEL problem, pick a current Live model"). **Add:** on any terminal `/ws/live` failure, the client should show `"Gemini voice unavailable — switching to local voice"` and actually do it (§5), rather than a dead end.

---

## 5. Tier Auto — smart selection & cross-tier fallback

### 5.1 Selection priority

`_resolve_voice_engine()` (`voice.py:471`) already resolves an engine per settings + availability with a no-dead-ends chain. Consolidate the **Auto** policy as:

```
GPU local (gpu_tier_ready)  →  Cloud (key valid + online)  →  CPU local (always)  →  demo/text
```
Rationale: GPU best quality on-device; Cloud best expressiveness but needs net+key; CPU always works. **Privacy note:** the repo ethos is "local default, cloud opt-in," so `local` (not `auto`) remains the shipped default, and `auto` never silently sends audio to the cloud when a working local tier exists **unless** the user picked `auto` explicitly.

### 5.2 Real-time cross-tier fallback (NEW — the biggest client gap, F6)

Today `/ws/live` failure is terminal on the client (`app.html:8358-8372`). Implement **session-info-driven fallback**:

- `session-info` gains a `fallback_ws_url` field: for `engine=gemini` it is `/ws/voice-local` when local is available.
- On a **terminal** `{type:'error'}` from `/ws/live` (not a transient reconnect), the client:
  1. shows `"Gemini voice unavailable — using local voice"`,
  2. closes the cloud socket,
  3. opens `fallback_ws_url` **without dropping the mic stream** (reuse the live `MediaStream` + worklet; only the WS swaps),
  4. updates the tier badge (§5.3).
- Guard against flapping: only fall back once per user-initiated session; a second failure surfaces the error.
- Mid-conversation cloud→local is acceptable to lose Gemini-side context (local brain starts fresh) — the transcript log persists either way.

### 5.3 Active-tier status indicator (NEW)

The status line shows `LIVE · <status>` with **no tier label** (`app.html:9507-9512`). Add a compact badge fed by `session-info` + the health poll:
- 🔒 **Local** (cpu) · ⚡ **Local GPU** · ☁ **Cloud** · with a title tooltip carrying the `reason` string from `_resolve_voice_engine`.
- Flip live when §5.2 fallback fires.

---

## 6. Voice setup wizard

**Problem:** two disjoint wizards. `setup_wizard.py` is terminal-only (a UI user never sees it); the in-UI wizard's "hardware" step is thin (`app.html:6139-6217`, `:6621-6645`). Unify on **one in-UI flow** backed by the existing `/api/voice/setup/status` + `/api/voice/setup/test` (both already implemented, `voice.py:550-662`).

### 6.1 The flow (Settings → Voice **and** first-run onboarding)

1. **"Let's set up your voice. Checking your system…"** → `GET /api/voice/setup/status` (returns per-step `deps/models/mic/key/model`).
2. **Microphone** → call `navigator.mediaDevices.getUserMedia({audio})` to trigger the **browser permission prompt**; on grant, enumerate devices (`app.html:8650`) and let the user pick + persist (`audio_input_device_id`). On deny, show how to re-enable in the browser.
3. **Local voice models** → if `models: needs_download`, show **Install Local Voice** with the §2.3 progress bar. This guarantees the first *conversation* isn't a silent 500 MB wait.
4. **GPU** → if `gpu.cuda || gpu.source=='nvidia-smi'`, offer **Install GPU Voice** (§3.3). Otherwise skip silently.
5. **Cloud key** → if a `GEMINI_API_KEY` validates, offer "Enable cloud voice"; if present-but-invalid, show the `_compose_final_voice_error`-style guidance and a paste-key field (Settings → Providers).
6. **Test** → **"Say something and I'll repeat it back."** Round-trip through the selected tier. (Minimal version: `POST /api/voice/setup/test` already returns a base64 TTS sample; the wizard plays it. Full version: a 5-second mic→ASR→echo test.)
7. **"Voice is ready. Change anything in Settings → Voice."**

### 6.2 Requirements

- Reachable from **both** first-run onboarding (`routes/onboarding.py`) and Settings → Voice.
- Every step degrades: a skipped/failed step never blocks reaching a *working* tier (local is always the floor).
- The terminal `setup_wizard.py` voice steps stay for CLI installs but must **write the same `settings.json` keys** the UI reads (`voice_engine`, `tts_voice`, `local_voice_*`), which they already do.

---

## 7. In-app installation (never open a terminal)

Consolidated from §2.3 and §3.3. All installs run in **background threads** with UI progress; the WS handlers already download *models* lazily — this section adds **dependency + pre-download** installs.

| Button (Settings → Voice) | Shown when | Does | Progress endpoint |
|---|---|---|---|
| **Install Local Voice** | `local_voice_health().status ∈ {missing, needs_download}` | If deps missing (shouldn't be, they're in `[all]`): `pip install -e .[voice-local-lite]`; then pre-download Whisper + Piper | `GET /api/voice/local/install/status` |
| **Upgrade to GPU Voice** | NVIDIA GPU detected & `nemo_health().status ≠ ok` | CUDA torch wheel + `.[voice-local-gpu]` + model pre-download | `GET /api/voice/gpu/install/status` |
| **Test voice** | always | `POST /api/voice/setup/test` → play sample | — |

**Implementation notes**
- Background installer = `subprocess.Popen([sys.executable,'-m','pip',...])`, stream stdout tail into the status endpoint. Never block the request thread.
- Idempotent: re-clicking when already installed just re-verifies.
- Failures are captured and surfaced (last ~20 log lines), never swallowed.
- **Security:** these endpoints run `pip` — gate behind `@login_required` + loopback-trusted, and never accept an arbitrary package name from the client (fixed command templates only).

---

## 8. Error recovery

### 8.1 Principles (every voice failure must)

1. **Log the specific error** — not "voice failed." The server already does (`_vlog`, `_classify_live_error`, `_compose_final_voice_error`); extend to the local path (currently `print()` in `_speak`/`_handle_turn`, `voice.py:811, 900`).
2. **Try the next tier automatically** — §5.2 (client cross-tier fallback) is the missing piece.
3. **Tell the user what happened and what's next** — status badge + one-line human message.
4. **Never leave a broken state with no feedback** — the demo/text engine (`voice.py:521`) is the terminal floor; even it must say "voice unavailable, here's why."

### 8.2 `GET /api/voice/health` (NEW — F12)

Single endpoint the UI polls (~every 10 s while the panel is open) for the tier badge and Settings status. Composes existing probes; **no new detection logic**:

```json
{
  "active_engine": "gemini",
  "reason": "user selected cloud",
  "tiers": {
    "cpu":   {"status":"available","detail":"local voice ready","models_ready":true},
    "gpu":   {"status":"unavailable","detail":"torch installed but CUDA not available"},
    "cloud": {"status":"available","detail":"key from launcher env, HTTP 200",
              "model":"gemini-2.5-flash-native-audio-preview-12-2025","model_ok":true}
  },
  "recommended": "cpu"
}
```
Sources: `local_voice_health()` (cpu), `nemo_health()` (gpu), `resolve_gemini_key()` + `validate_live_model()` (cloud), `_resolve_voice_engine()` (active/reason). Each tier is `available | unavailable | error` **with a reason string** — exactly what §5.3's badge and §6's wizard render.

### 8.3 Fix the dead fallback-TTS path (F1/F10)

`_synthesize_tts_wav_local` depends on `pyttsx3` (not installed) — so the offline, PII-safe, and Gemini-failure fallbacks in `_synthesize_tts_wav` (`voice_engine.py:483-517`) all no-op. **Fix: route the local TTS fallback through Piper**, which is installed and already produces 24 kHz PCM16:

- Add `local_voice_engine.synthesize()` as the primary local-TTS backend in `_synthesize_tts_wav_local`, wrapping PCM16 → WAV; keep `pyttsx3` as a secondary only if Piper unavailable.
- This makes `/api/voice/tts`, the News briefing audio, and the PII-egress "speak locally" path actually work offline — closing a silent, user-invisible gap.
- Update `_local_tts_available()` and `/api/voice/fallback-status` (`voice.py:439`) to report Piper as the local-TTS provider, so `recommended_mode` becomes `local`/`tts_only` correctly when cloud is down.

---

## 9. Testing plan

### 9.1 Unit (offline, CI — extend existing suites)

- `tests/unit/test_local_voice.py`, `tests/unit/test_nemo_voice.py` — keep the `FakeASR`/`FakeTTS` wiring coverage.
- **New** `test_voice_health.py` — `/api/voice/health` composes the three probes; each tier's `status`+`reason` correct under monkeypatched deps/key.
- **New** `test_tts_fallback_piper.py` — with `pyttsx3` absent, `_synthesize_tts_wav_local` returns Piper WAV bytes (not `None`).
- **New** `test_resolve_voice_engine.py` — cloud-invalid-key → routes to local; local-missing + cloud-ok → cloud; nothing → demo. (Extends the `_resolve_voice_engine` matrix.)

### 9.2 Integration (round-trip)

- Local: synthetic 16 kHz PCM of a known phrase → `/ws/voice-local` → assert `input_transcript` + `audio` frames + `voice_turn_done`. (Whisper on CI is slow; gate behind an opt-in marker or use `FakeASR`.)
- Cloud: mocked Gemini session (the test conftest already stubs LLM entry points) → assert the attempt-plan order and that a mocked `1008 model-missing` advances the ladder while `1008 auth` stops it.

### 9.3 Failure injection

| Test | Inject | Expect |
|---|---|---|
| ASR mid-stream kill | `engine.transcribe` raises | turn logs error, session survives, next utterance works |
| TTS mid-stream kill | `engine.synthesize` raises | `_speak` skips sentence, `turn_end` still sent |
| Network drop (cloud) | close upstream w/o FIN | `liveness_watchdog` renews leg via handle (`voice.py:1784`) |
| Retired model | first attempt → `1008 not found` | falls to `LIVE_MODEL_FALLBACK`, no auth message |
| Bad key | `resolve_gemini_key` invalid | mic routes to local; **no `1008` shown**; clear "key invalid from `<source>`" if user forced cloud |
| Cloud total failure | all attempts fail | **client falls through to `/ws/voice-local`** (§5.2), badge flips to 🔒 |

### 9.4 Key-validation test (explicit — no `1008` leak)

Assert that a revoked key never reaches `/ws/live`: `_resolve_voice_engine` with an invalid key returns `engine:"local"` (or `demo`), and if the user explicitly forced `gemini`, the surfaced message is `_compose_final_voice_error`'s key-invalid text, **not** a raw close code.

---

## 10. What else should we do

- **VAD tuning.** Local `VADEndpointer` defaults: `start_rms=600`, `silence_ms=800`, `min_speech_ms=200` (`local_voice.py:182`); Silero preferred when installed (it *is*, `silero_vad` imports). Cloud uses `START_SENSITIVITY_LOW` + `silence_duration_ms=800` (`voice.py:373-382`). **Action:** expose `voice_silence_ms` (already a setting) prominently in the wizard's test step, and consider auto-calibrating `start_rms` from the first 1 s of room noise (mirrors the barge detector's bleed-learning).
- **Barge-in.** Cloud speaker-mode barge-in is a genuine achievement: `LiveBargeDetector` learns speaker bleed and fires on real talk-over under `NO_INTERRUPTION` (`voice.py:179-260, 1524-1536`); Escape-key is an explicit client barge (`app.html:8500`). **Local path has no barge-in** — `_handle_turn` holds a lock for the whole turn (`voice.py:827`). **Action:** add cooperative interruption to `/ws/voice-local` (check a `done`/`interrupt` flag between sentences in `_speak`, honor a client `{type:'barge'}`).
- **Audio device selection.** Dropdown works and persists (`audio_input_device_id`/`audio_output_device_id`), hot-swap detected (`app.html:8650-8678, 9535-9561`). **Gap:** output-device routing relies on `setSinkId`, not verified on all browsers — add a test tone through the selected output in the wizard.
- **Voice personality / SOUL.** `voice_personality.get_voice_personality()` builds the Live system instruction with mood + affective dialog; `voice_style_prompt` (currently "warm, a bit spoony, and genuinely caring") prefixes both cloud and Piper paths. **Confirm** SELF.md/SOUL.md tone actually reaches the local path's `system_prompt` (it goes through `_get_friday_system_prompt`, so yes) — but Piper's *acoustic* delivery is fixed by the voice model; only word choice/pacing is controllable. Document that expectation so "make her sound warmer" maps to prompt, not engine, on Tier 1.
- **Accessibility.** Transcripts (`input_transcript`/`text`) already render, which helps screen-reader users follow the conversation. **Action:** ensure the mic button + status have ARIA live-region semantics; verify Bluetooth hearing-aid output works via the output-device picker + `setSinkId`.
- **Mobile companion.** The PWA path is why `_est_play_end_ts` estimates the playback window when the client can't send `{type:'speaking'}` (`voice.py:1350-1364`). When the mobile app lands: keep the identical WS contract, prefer Tier-3 cloud (mobile CPUs can't run Whisper-small comfortably), and have the client report playback transitions so barge-in stays precise.

---

## Appendix A — Concrete work items (prioritized)

| P | Item | Files | Closes |
|---|---|---|---|
| **P0** | Route local TTS fallback through Piper (kill the dead `pyttsx3` dependency for fallback) | `services/voice_engine.py:411-517` | F10, gap #1 |
| **P0** | `GET /api/voice/health` composing existing probes | `routes/voice.py` | F12, gap #5 |
| **P0** | Client cross-tier fallback cloud→local on terminal error | `ui_parts/app.html:8358-8441`, `session-info` | F6, gap #2 |
| **P1** | Local model **pre-download** endpoints + progress bar | `routes/voice.py`, `services/local_voice.py`, `app.html` | F8, gap #4 |
| **P1** | Active-tier badge in status indicator | `app.html:9507-9512` | §5.3 |
| **P1** | Unify the in-UI voice wizard (mic-permission + model + GPU + test) | `app.html:6139-6217, 6621-6724`, `routes/onboarding.py` | gap #4 |
| **P2** | In-app GPU install button + `/api/voice/gpu/install[/status]` | `routes/voice.py`, `app.html` | gap #3 |
| **P2** | Local-path barge-in | `routes/voice.py:800-860` | §10 |
| **P2** | New unit tests (health, TTS fallback, engine resolution) | `tests/unit/` | §9 |

## Appendix B — What NOT to touch (hard-won robustness)

The `/ws/live` reconnect loop, session-resumption cache + connection-generation fence, GoAway drain, `LiveBargeDetector`, liveness watchdog, and `_classify_live_error`/`_compose_final_voice_error` are correct and battle-tested. This overhaul **wraps** them with a better client and setup story — it does not rewrite them.
