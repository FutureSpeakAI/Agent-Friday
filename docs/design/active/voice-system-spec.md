# Agent Friday Voice System — Out-of-the-Box Spec

**Document:** `docs/VOICE_SYSTEM_SPEC.md`
**Status:** Forward-looking product + technical spec (drives the next implementation run)
**Codebase:** the `friday-desktop` repository root
**Companion docs (read, do not duplicate):**
- `docs/VOICE_SYSTEM_OVERHAUL_SPEC.md` — post-mortem / current-state reference. This spec references it by section (e.g. "OVERHAUL §7.9") rather than restating it.
- `docs/TIER2_NEMO_VOICE.md` — Tier-2 NeMo install/health/test reference.

**Audience:** FutureSpeak.AI engineering.

**One-line thesis:** *Every new user gets working voice on first run, or a one-click in-UI path to it — no terminal, no silent failures, no dead greyed buttons.*

---

## 0. How this spec differs from the OVERHAUL spec

The OVERHAUL spec (July 2026) documented what was fixed **on the developer's own machine**, where the venv already carried `faster_whisper` / `piper` / `pyttsx3`. That masked the new-user reality: the Tier-1 stack lives only in the `voice-local-lite` extra (`pyproject.toml:28-34`), so a plain `pip install -e .` ships **zero** local-voice deps. This spec closes the gap between "works on the dev box" and "works on first run for a stranger." It is scoped to four verified gaps:

1. Tier-1 deps are not in core `dependencies` and there is **no in-UI installer** (`GET/POST /api/voice/setup/install` does not exist — grep-confirmed; only `/api/voice/setup/status` at `voice.py:570` and `/api/voice/setup/test` at `voice.py:659`).
2. Tier-2 GPU is invisible: CPU-only torch short-circuits GPU detection, no in-UI install, `_tool_install_package` hard-caps pip at 180 s (`agent.py:538,552`).
3. Tier-3 default model + picker drifted: `LIVE_MODEL` was corrected to `gemini-2.5-flash-native-audio-latest` at `voice_engine.py:632`, but the registry/UI defaults still carry `gemini-3.1-flash-live-preview`, and `validate_live_model` (`voice_engine.py:808`) uses a substring heuristic (`_LIVE_MODEL_MARKERS` at `:805`) that green-lights stale ids.
4. Actionable `detail` fields are dropped by the client error handler; failures surface as terse slugs.

---

## 1. Goals and Non-Goals

### 1.1 Goals

**G1 — Working voice on first run, or one click to it.** After a standard install, clicking the mic either speaks, or opens a single in-UI flow (install / download / pick-model) that ends in working voice. No terminal, ever, for Tier 1 and Tier 3.

**G2 — Every tier reachable from the UI.** Tier 1 (CPU), Tier 2 (GPU), Tier 3 (Gemini Live) each have a discoverable, in-UI activation path with dependency detection, progress, and graceful degradation.

**G3 — No silent downgrades.** Any tier change the user did not explicitly request produces a visible, dismissible notice naming the reason and the remediation (OVERHAUL §7 taxonomy extended in §8 here).

**G4 — Errors distinguish root cause.** Bad-key vs dead-model vs network vs egress-blocked vs missing-deps vs missing-models are never conflated. Each maps to one user-facing message + one remediation action (§8).

**G5 — Provable privacy for local tiers.** Local tiers bypass the egress gate by classification; Tier 3 seals text; audio-egress limits are documented and surfaced. The boot-time egress self-test verdict is visible in the UI (§9).

**G6 — CI-runnable verification.** A per-tier synthetic-WAV smoke test (audio in → STT → canned response → TTS → byte-level output assertion) runs in CI (mocked GPU/cloud) and from the UI (real) (§10).

### 1.2 Non-Goals

- **NG1** — We do not make Tier-2 NeMo bulletproof on Windows. It stays best-effort (OVERHAUL §12.1). The guarantee is: attempt in-UI install with honest progress, and **always** fall back to Tier 1 on failure.
- **NG2** — We do not gate raw microphone audio through the text egress gate (structurally impossible — the gate is a text classifier, `egress_gate.py`). We *guard the routing* so a local-only user is never routed to `/ws/live`, and we surface an explicit "raw audio leaves the device on Tier 3" notice (§9.3).
- **NG3** — We do not build wake-word, multi-language Piper, or streaming partial ASR in Phase 1. They are roadmap (§11).
- **NG4** — We do not change the ethos default: `voice_engine` stays `"local"`. Cloud is opt-in.
- **NG5** — We do not vendor model checkpoints in the repo (license + size). Offline users get a pre-stage path (§5.4), not a bundled blob.

---

## 2. Tier Matrix

| | **Tier 1 — Local CPU** | **Tier 2 — Local GPU** | **Tier 3 — Gemini Live** | **pyttsx3 / SAPI5 fallback** |
|---|---|---|---|---|
| **Role** | Default. Universal fallback. | Premium local. Opt-in. | Cloud, lowest latency. Opt-in. | Last-resort TTS-only. |
| **ASR** | faster-whisper (CTranslate2 INT8) | `nvidia/nemotron-3.5-asr-streaming-0.6b` | Native (no separate ASR) | none (TTS only) |
| **TTS** | Piper (VITS/ONNX) | NeMo FastPitch + HiFi-GAN | Native audio | OS SAPI5 |
| **Deps** | `faster-whisper`, `piper-tts`, `onnxruntime`, `silero-vad` (opt), `numpy` | torch-CUDA + `nemo_toolkit[asr,tts]>=2.6` + all Tier-1 | `google-genai`, `flask-sock` | `pyttsx3` |
| **Install extra** | `.[voice-local-lite]` (in `[all]`) | `.[voice-local-gpu]` (NOT in `[all]`) | core (`google-genai` already in `dependencies`) | `.[voice]` |
| **Hardware** | any x86_64 CPU, ~1 GB RAM | NVIDIA GPU, ≥4 GB free VRAM, CUDA | network + valid key | Windows (SAPI5) |
| **Disk (deps)** | ~150–300 MB | ~3–6 GB | negligible | negligible |
| **Model download** | whisper `small` ~460 MB + Piper amy-medium ~60 MB → `~/.friday/local_voice/` | NeMo checkpoints ~1.5 GB → `~/.friday/models/nemo/` | none (server-side) | none |
| **First-word latency** | 2–5 s (VAD 800 ms + ASR 300–500 ms + LLM 1–3 s + TTS) | ASR 100–200 ms; LLM still bottleneck → ~1.5–3 s | < 1 s | ASR-less; TTS instant |
| **Quality** | Good, robotic-ish prosody | Better prosody, streaming-capable | Best, affective/proactive, barge-in | Robotic |
| **Egress** | Bypasses gate (local classification) | Bypasses gate | Text sealed; raw audio ungated (§9.3) | Local, no egress |
| **WS route** | `/ws/voice-local` | `/ws/voice-local` (tier resolved server-side) | `/ws/live` | in-process (TTS endpoints) |

Latency/quality figures reconcile with OVERHAUL §12.5. Sizes reconcile with `TIER2_NEMO_VOICE.md` and OVERHAUL §3.

---

## 3. Out-of-the-Box Behavior Per Persona (Acceptance Criteria)

Each persona journey is expressed as pass/fail acceptance criteria. IDs map to the requirements they satisfy.

### 3.1 Persona A — No-GPU, non-technical, no terminal

- **A-AC1 (R1):** On a clean machine, the standard installer/venv includes Tier-1 deps. `local_voice.deps_installed()` returns `True` with no extra flags. Verify: fresh `pip install` (the exact command the shipped installer runs) → `GET /api/voice/setup/status` step `deps.status == "ok"`.
- **A-AC2 (R1):** If deps are present but models are not, clicking the mic connects `/ws/voice-local`, streams `{type:'status'}` download frames, and completes end-to-end. Never reaches the `demo` dead-end.
- **A-AC3 (R2):** If deps are NOT bundled, clicking the mic (or finishing onboarding) surfaces a **"Set up voice"** button that calls `POST /api/voice/setup/install`, streams progress, and ends with `deps_installed()==True` and a working mic — entirely in-UI.
- **A-AC4 (R3, R4):** On any voice-unavailable failure, the recovery UI shows plain-language guidance **plus** the server's `detail` string **plus** a one-click fix action. The raw slug `error: local_voice_unavailable` never renders.
- **A-AC5 (R5):** A no-GPU user completing onboarding either has working local voice or has explicitly seen and dismissed a "voice needs setup" step. They never finish believing voice works when it does not.
- **A-AC6 (R7):** When voice is unavailable, the mic control visibly signals "setup needed" and routes the click to the setup flow instead of opening a doomed socket.

### 3.2 Persona B — RTX 4070 power user, wants Tier 2

- **B-AC1 (R1):** With `torch 2.x+cpu` installed and an RTX 4070 present, `gpu_status()` reports `device="NVIDIA GeForce RTX 4070"`, `vram_gb≈12` via the nvidia-smi source (detection falls through when torch reports CUDA unavailable). Settings → Voice shows "GPU detected — Tier 2 available", not a context-free greyed button.
- **B-AC2 (R2, R3):** A **"Install GPU Voice (Tier 2)"** button runs a background job that force-reinstalls torch from the cu124 index, installs `.[voice-local-gpu]`, streams byte/percent/phase progress, and on completion flips the `Local GPU (NeMo)` engine button to enabled **without a restart**. `torch.cuda.is_available()` becomes `True`. The job is not killed by the 180 s cap.
- **B-AC3 (R4):** The Voice Setup Wizard shows the full Tier-2 checklist (torch-CUDA, NeMo, CUDA GPU, VRAM, models) whenever a GPU is detected, **regardless of the active engine**, with an Install action against failing steps. It no longer collapses a `local-gpu` selection to a CPU-only checklist.
- **B-AC4 (R5):** Selecting `Local GPU (NeMo)` with NeMo absent yields a persistent, dismissible banner ("Running CPU voice — GPU voice needs a one-time install. Install now?") with a working install action, while voice continues on CPU. Never a silent swap to Piper.
- **B-AC5 (R6):** First GPU activation shows a determinate progress bar (percent/bytes/ETA) for the ~1.5 GB NeMo checkpoints with a cancel option, then transitions to `live (GPU/NeMo)`.

### 3.3 Persona C — Gemini-key user, wants Tier 3 primary

- **C-AC1 (R1):** A clean install with no `settings.json` reports `voice_model == "gemini-2.5-flash-native-audio-latest"` identically from `/api/status`, `/api/models` (selected), the onboarding default, and `DEFAULT_AGENT_SETTINGS`. Grep for `gemini-3.1-flash-live-preview` **as a default** returns zero hits (single source of truth).
- **C-AC2 (R2):** The Voice-role dropdown lists `gemini-2.5-flash-native-audio-latest` and `gemini-2.5-flash-preview-native-audio` as selectable; the retired `gemini-2.5-flash-native-audio-preview-12-2025` is absent or shown disabled with a "retired" tag. Any offered model produces a working `/ws/live` session.
- **C-AC3 (R3):** First-run wizard and Settings → Voice expose **"Test cloud voice"** that opens a real short `/ws/live` session and reports the actual outcome. With a deliberately stale model configured, the test **fails** with "model stale — pick a current one" and does **not** fall through to local pyttsx3.
- **C-AC4 (R4):** `validate_live_model('gemini-3.1-flash-live-preview')` returns `unknown`/`stale` when Google does not list it as a Live model (capability probe, not substring). The boot check (`server.py`) and `_resolve_voice_engine` auto-correct then reset to the verified default and persist.
- **C-AC5 (R6):** On Tier-3 failure the UI (both `index.html` and `friday_live.html`) names the discriminator: auth → "key invalid, paste a fresh key…"; retired model → "key is fine, model is stale…"; offline → network message. All render in-app, not just `friday.log`.

### 3.4 Persona D — Privacy-focused, offline-only

- **D-AC1 (R1-offline):** With no network and no pre-staged models, clicking the mic yields an error that **names the target paths** (`~/.friday/local_voice/whisper/`, `/piper/`) and the download source, detected **before** attempting a fetch. After manually placing checkpoints there (no server internet), a voice session works end-to-end.
- **D-AC2 (R2-local-brain):** With `voice_engine=local` and local-only toggle on, a spoken turn produces **zero** cloud-provider entries in the egress log and `/api/voice/session-info` reports `brain_provider=local`. If no local brain is reachable, the session refuses with an actionable message instead of silently using the cloud orchestrator.
- **D-AC3 (R3-guard):** With `offline_auto_local`/local-only set, no combination of missing local deps routes the mic to `/ws/live`; it degrades to text-only with a clear reason. A test asserts `_resolve_voice_engine` never returns `engine=gemini` when the local-only flag is set.
- **D-AC4 (R4-egress-proof):** `/api/voice/setup/status` (or `/api/health/full`) includes an `egress_gate` block `{self_test_passed, cloud_routing_enabled}`; breaking the classifier import shows `cloud_routing_enabled=false` in the UI and blocks a cloud voice attempt.
- **D-AC5 (R6-VAD):** `local_voice_health()` returns `vad_backend ∈ {silero, rms}`; forcing Silero unavailable yields `vad_backend=rms` and a surfaced one-time status frame.

### 3.5 Persona E — Tier-3 power user, wants a fluid conversation for HOURS

The conversation must be fluid and continue for literally hours with no
perceptible loss in quality or context. (First-class requirements added
2026-07-06; see §13 for the grounded config.)

- **E-AC1 (interruptible):** In the default interruption mode, the user talking
  over Friday stops her audio within **~200 ms**. `_build_realtime_input_config`
  yields `activity_handling = START_OF_ACTIVITY_INTERRUPTS`, the server
  forwards `server_content.interrupted`, and the client flushes the playback
  ring on `{type:'interrupted'}`. A regression test asserts the default (and
  the legacy `speaker`/`headphones` values) map to `START_OF_ACTIVITY_INTERRUPTS`,
  and only the explicit `no-barge` opt-out maps to `NO_INTERRUPTION`.
- **E-AC2 (no audible degradation over time):** Playback runs off a jitter
  buffer with a ~120 ms prefill (re-primed after any underrun) so network gaps
  never underrun into clicks; output stays 24 kHz PCM16 mono end-to-end; the
  ring is large enough to hold a full faster-than-realtime response and an
  anti-wrap guard prevents pointer-lap corruption without truncating a long
  turn. No progressive rasp across a long session.
- **E-AC3 (context survives connection cycling):** `context_window_compression`
  (sliding window) is on by default so the session never hits the ~15-min
  audio duration cap; the GoAway→reconnect-with-`session_resumption`-handle loop
  survives the independent ~10-min per-connection cap, carrying full context.
  A multi-hour session continues without a perceptible break or context reset.

---

## 4. Dependency Detection and Graceful Degradation

### 4.1 Detection functions (existing, authoritative)

| Check | Function | File | Returns |
|---|---|---|---|
| Tier-1 deps importable | `deps_installed()` / `deps_status()` | `services/local_voice.py` | bool / per-dep map (`faster_whisper`, `piper`, `onnxruntime`, `silero_vad`) |
| Tier-1 models present | `LocalVoiceEngine.models_ready` / `local_voice_health()` | `services/local_voice.py` | bool + paths |
| VAD backend | `local_voice_health().vad_backend` | `services/local_voice.py` | `silero` \| `rms` |
| Tier-2 deps | `nemo_deps_status()` | `services/nemo_voice.py` | per-dep (`torch`, `nemo`) |
| GPU hardware/VRAM | `gpu_status()` | `services/nemo_voice.py` | `{cuda, device, vram_gb, source, detail}` |
| Tier-2 readiness ladder | `nemo_health()` | `services/nemo_voice.py` | `ok`\|`needs_download`\|`down`\|`missing` |
| Tier-2 gate | `gpu_tier_ready()` | `services/nemo_voice.py` | bool (torch+NeMo+CUDA+VRAM≥`MIN_VRAM_GB`) |
| Key resolution/validity | `resolve_gemini_key()` / `validate_gemini_key()` | `services/voice_engine.py` | working key + source; cached 600 s |
| Live model validity | `validate_live_model()` | `services/voice_engine.py:808` | status (see §4.4) |
| Overall engine resolution | `_resolve_voice_engine()` | `routes/voice.py:471` | engine + ws_url + reason |

### 4.2 When checks run (and caching)

- **Boot:** `server.py` runs the egress self-test (OVERHAUL §9-adjacent) and `validate_live_model` boot warning. **Change:** the boot warning must also fire on `stale`, not only `unknown` (see §4.4).
- **First launch / onboarding:** onboarding calls `GET /api/voice/setup/status` for the resolved engine. **Change:** onboarding must additionally call `gpu_status()` (Tier-2 discovery) and, for a key-holder, offer the Tier-3-primary choice (§3.2 B-AC1, §3.3 C-AC?).
- **Mic click:** `session-info` → `_resolve_voice_engine` runs pre-flight; the mic button reflects readiness (R7).
- **Wizard open:** `GET /api/voice/setup/status` refresh + `gpu_status()` unconditionally (B-AC3).
- **Caching:** key checks cached per-key-hash for 600 s (`_KEY_CHECK_TTL`). `gpu_status()` **must not** be cached across sessions on the free-VRAM value (it can transiently dip; see §4.5). `validate_live_model` capability probe cached 10 min per model id.

### 4.3 GPU detection fall-through (fix for B-AC1)

`gpu_status()` in `nemo_voice.py` currently early-returns "torch installed but CUDA not available" when `torch` is importable but `cuda.is_available()==False`, skipping the nvidia-smi fallback. **Change the control flow** so that whenever CUDA is reported unavailable *by torch*, detection **continues** to the nvidia-smi path (via `ollama_manager.detect_hardware()` or a direct `nvidia-smi --query-gpu=name,memory.total,memory.free`). Result shape gains `source ∈ {torch, nvidia-smi}` so the UI can say "GPU detected (CPU-only torch) — install GPU voice to use it."

### 4.4 Live-model validation (fix for C-AC4)

Replace the substring heuristic (`_LIVE_MODEL_MARKERS = ("-live", "live-", "native-audio")` at `voice_engine.py:805`) with a **capability probe**:

```
validate_live_model(model_id) ->
  if FRIDAY_TESTING: return {status: "ok"}  # test seam, no network
  probe = cached (10 min): GET v1beta/models/{model_id}
          check supportedGenerationMethods contains 'bidiGenerateContent'
  200 + bidi supported            -> {status: "ok"}
  200 + not bidi                  -> {status: "stale", detail: "not a Live model"}
  404 / not listed                -> {status: "stale", detail: "retired or unknown"}
  network error                   -> {status: "unknown", detail: "unverifiable offline"}  # never brand bad offline
```

Gate consumers on `status in {"stale","unknown-with-known-good-available"}`:
- Boot warning (`server.py`): warn on `stale` (was: only `unknown`).
- Auto-correct in `_resolve_voice_engine` (`voice.py`): reset `voice_model` to `LIVE_MODEL` and persist on `stale` (was: only `unknown`).
- Wizard `model` step reflects `ok`/`stale`/`unknown`.

Keep `_KNOWN_LIVE_MODELS` as an offline allowlist so the probe short-circuits to `ok` for known-good ids when offline.

### 4.5 Graceful degradation ladder (summarized; state machine in §7)

- Tier 2 requested, `gpu_tier_ready()==False` → Tier 1, **with a persistent banner** (not a transient status line). Fix: `ensure_ready()` swap must emit an `{type:'error-nonfatal', code:'gpu_degraded', detail:...}` frame in addition to loading CPU (B-AC4).
- Tier 1 requested, deps missing → **do not** open the socket; route to setup (R7). If the socket is reached anyway, emit `{type:'error', error:'local_voice_unavailable', detail:...}` and the client **must render `detail`** (R4).
- Silero unavailable → RMS gate, one-time `{type:'status', text:'using basic voice detection (silero unavailable)'}` + `vad_backend=rms` in health (D-AC5).
- Cloud requested, key/model bad, local available → **only if not local-only** fall to `/ws/voice-local` with reason; local-only → text-only (D-AC3).

---

## 5. In-UI Install Flows

All install jobs run as **long-lived background tasks** with a streamed progress channel, replacing the 180 s pip cap (`agent.py:538,552`). Introduce a shared `VoiceInstallJob` runner in a new `services/voice_installer.py`.

### 5.1 New endpoints

| Route | Method | Purpose |
|---|---|---|
| `/api/voice/setup/install` | POST | Body `{tier: "cpu"|"gpu"}`. Starts a `VoiceInstallJob`. Returns `{job_id}`. |
| `/api/voice/setup/install/status` | GET | `?job_id=` → `{phase, percent, bytes_done, bytes_total, log_tail, state}` where `state ∈ {running, done, error, cancelled}`. |
| `/api/voice/setup/install/cancel` | POST | `{job_id}` → best-effort terminate; partial artifacts cleaned. |
| `/api/voice/setup/download-models` | POST | Body `{tier}`. Triggers checkpoint download with progress (Tier 1 whisper+piper, Tier 2 nemo). |
| `/api/voice/setup/test-cloud` | POST | Real `/ws/live` smoke test (see §6 Stage R, §10). Returns classified outcome, never local-TTS fallback. |

`VoiceInstallJob` streams progress either via a Server-Sent-Events channel or via the existing `/ws/voice-local` status frames (reuse the frame contract). Prefer a dedicated `/ws/voice-install` WS to avoid coupling install progress to a voice session.

### 5.2 Tier-1 CPU install (Persona A path)

```
POST /api/voice/setup/install {tier:"cpu"}
 phase "preflight": disk-space check (need ~500 MB free in venv + ~600 MB in ~/.friday); refuse with actionable error if short.
 phase "pip":  <venv-python> -m pip install -e ".[voice-local-lite]"
               stream pip stdout -> log_tail; no 180 s cap.
 phase "verify": re-import faster_whisper/piper/onnxruntime -> deps_installed()==True
 phase "models": call /api/voice/setup/download-models {tier:"cpu"} (or inline)
 phase "done":  flip UI, re-check /api/voice/setup/status
```

Resume: pip is idempotent; on retry it skips satisfied requirements. Model download resumes via `.part` rename (existing pattern, `local_voice.py`).

### 5.3 Tier-2 GPU install (Persona B path)

```
POST /api/voice/setup/install {tier:"gpu"}
 phase "preflight": gpu_status() must show a CUDA-capable NVIDIA GPU (source torch|nvidia-smi);
                    disk check ~8 GB free; refuse otherwise with the exact shortfall.
 phase "torch":  <venv-python> -m pip install --force-reinstall torch \
                    --index-url https://download.pytorch.org/whl/cu124
                 # --force-reinstall is REQUIRED: an existing +cpu wheel satisfies the
                 # bare requirement and leaves CUDA unavailable (OVERHAUL §7.5; TIER2 doc gap).
 phase "nemo":   <venv-python> -m pip install -e ".[voice-local-gpu]"
 phase "verify": torch.cuda.is_available()==True AND nemo_deps_status all True AND gpu_tier_ready()
 phase "done":   flip Local GPU (NeMo) button enabled WITHOUT restart (hot-swap next session)
 on any failure: honest error + Tier-1 remains fully working (NG1). No partial-broken state.
```

Progress: torch cu124 wheel is ~2.5 GB — surface byte/percent by parsing pip's download progress or wrapping the download. NeMo pulls a large dep tree — show phase names even where byte totals are unknown (indeterminate-with-phase, not a frozen spinner).

Cancellation: terminate the pip subprocess; leave Tier-1 intact; mark job `cancelled`.

### 5.4 Offline / air-gapped model provisioning (Persona D path)

- **Pre-stage locations** (documented + accepted by the loader):
  - `~/.friday/local_voice/whisper/` (CTranslate2 checkpoint)
  - `~/.friday/local_voice/piper/` (`en_US-amy-medium.onnx` + `.onnx.json`)
  - `~/.friday/models/nemo/` (Tier 2)
- **`friday voice download-models` CLI:** runs on a networked machine, fetches all checkpoints into the pre-stage dirs, so the tree can be copied to the air-gapped box. Also expose `POST /api/voice/setup/download-models` for the online case.
- **Offline detection before fetch:** `ensure_ready()` must probe reachability (short-timeout HEAD to the HF host, or reuse the network-state signal) **before** attempting `urlretrieve`. If offline and models absent, return `{type:'error', error:'models_missing_offline', detail:'No network and no local models. Place whisper checkpoint in ~/.friday/local_voice/whisper/ and en_US-amy-medium.onnx(.json) in ~/.friday/local_voice/piper/, then reload. Source: huggingface.co/rhasspy/piper-voices …'}`. Do **not** swallow to the generic `local_voice_load_failed`.
- **"Point me at a local model dir" setting:** `local_voice_model_dir` override in `settings.json`.

### 5.5 Checkpoint integrity (Persona D, R5)

Piper downloads currently `urlretrieve` to `.part` then rename with no verification (`local_voice.py`). Add optional SHA-256 pinning: ship an `assets/voice_model_hashes.json` mapping filename → sha256; verify the `.part` before rename; refuse to load a mismatch with a clear error. Happy path unchanged when a hash is present and matches.

---

## 6. First-Run Voice Diagnostics Wizard

Replace the diagnose-only `VoiceSetupWizard` (`ui_parts/app.html:6619`, only Refresh + Test TTS) with a **stage-by-stage** wizard that can *fix*, not just report. Each stage: `pass | warn | fail` + a remediation action button. Surfaced on mic failure (R3/R7), from Settings → Voice, and during onboarding (R5).

| Stage | Check | Data source | Remediation on fail |
|---|---|---|---|
| **Deps** | `deps_installed()` (Tier 1); for GPU-detected machines also `nemo_deps_status()` | `/api/voice/setup/status` + `gpu_status()` | Button → `POST /api/voice/setup/install` (cpu or gpu) with progress |
| **Models** | `models_ready`; Tier-2 `nemo_health()==needs_download` | status | Button → `/api/voice/setup/download-models` with progress bar |
| **Mic** | `getUserMedia({audio:true})` + 2 s capture, RMS > 200 | browser | Device picker (§11) + browser-permission guidance |
| **VAD** | feed captured audio to VADEndpointer; confirm it fires; report `vad_backend` | `local_voice_health` | If `rms`: advise mic gain / `voice_silence_ms`; offer Silero install |
| **STT** | transcribe captured audio; show the transcript back | `/ws/voice-local` short session or a `/api/voice/setup/test-stt` | If empty: mic gain / model-size advice |
| **Response** | brain round-trip on the transcript; show `brain_provider` | `session-info` | If cloud brain while local-only: prompt to enable local brain (D-AC2) |
| **TTS** | `POST /api/voice/setup/test`; play WAV | existing endpoint | Output-device picker; volume |
| **Speaker loopback** | play a tone, capture via mic, confirm bleed detected (calibrates barge grace) | browser + LiveBargeDetector params | Advise headphones mode if excessive echo |
| **Cloud (Tier 3 only)** | `POST /api/voice/setup/test-cloud` — real `/ws/live` frame | new endpoint | Classified error (§8): key / model / network — never local-TTS pass |

**Key wizard fixes vs today:**
- The GPU/NeMo sub-block renders whenever a GPU is detected, **independent of the active engine** (B-AC3). Today `voice_setup_status` takes the `local` branch because `_resolve_voice_engine` collapses `local-gpu`→`local` (`voice.py:534-536`); the wizard must call `gpu_status()`/`nemo_health()` directly rather than depend on the resolved engine.
- The onboarding voice step (`WIZARD_VOICES` Gemini names at `app.html:6140-6146`) is replaced/augmented by a tier-aware step that probes deps and offers setup (R5), rather than presenting only cloud voice names.

---

## 7. Automatic Tier Fallback Policy (State Machine)

**Principle (G3):** the *preferred* tier is what the user selected; any deviation is announced. No silent downgrade.

### 7.1 States

`PREFERRED(t)` → `ACTIVE(t')` where `t` is the user's `voice_engine` choice and `t'` is what actually ran.

### 7.2 Fallback order per preference

| Preference | Order attempted | Terminal (all fail) |
|---|---|---|
| `local` (Tier 1) | Tier1 → (deps? setup prompt) → text-only | text-only + setup CTA |
| `local-gpu` / `gpu` / `nemo` | Tier2 (if `gpu_tier_ready`) → Tier1 (**banner**) → text-only | text-only |
| `auto` | Tier2 (if ready) → Tier1 → (cloud only if key present AND not local-only) → text-only | text-only |
| `gemini` (Tier 3) | Cloud (model attempt plan) → (local only if not local-only) → text-only | text-only + classified error |

**Local-only override (Airplane/Sovereign mode, §11):** forces `model_routing.mode=local_only`, pins `voice_engine=local`, and makes any cloud branch unreachable. `_resolve_voice_engine` must return `engine != gemini` under this flag (D-AC3).

### 7.3 Notification rule

Every transition `PREFERRED(t) → ACTIVE(t')` with `t' != t` emits exactly one persistent, dismissible notice carrying: reason code, human message, remediation action (see §8). Transient `{type:'status'}` lines are **not** sufficient for a downgrade — a downgrade uses `{type:'error-nonfatal'}` (new frame) so the client renders it as a banner, not a flash.

### 7.4 Cloud model attempt plan (Tier 3, unchanged mechanics — OVERHAUL §8.1)

v1alpha(configured, if affective/proactive) → v1beta(configured) → v1beta(`LIVE_MODEL_FALLBACK`) → v1beta(`LIVE_MODEL_FALLBACK2`). **New:** if the configured model is `stale` per §4.4, auto-correct to `LIVE_MODEL` *before* building the plan and persist, so the plan starts from a good id rather than relying on the invisible fallback (C-AC1/C-AC4).

---

## 8. Actionable Error Taxonomy

Every failure maps to `{code, user_message, action}`. The client **must render `detail`** for all `{type:'error'}` and `{type:'error-nonfatal'}` frames. Fix the handler at `ui_parts/app.html:8358-8371` (currently renders only `m.error`, drops `m.detail`) and mirror in `friday_live.html:312-318`.

| Code | Root cause | Discriminator | User message | Action |
|---|---|---|---|---|
| `local_voice_deps_missing` | Tier-1 deps absent | `deps_installed()==False` | "Local voice isn't installed yet." | **Set up voice** → `POST /api/voice/setup/install {cpu}` |
| `local_voice_models_missing` | deps ok, models absent, online | `models_ready==False`, online | "Downloading voice models (~520 MB, one-time)…" | Auto-download w/ progress |
| `models_missing_offline` | models absent, offline | offline probe true | "No network and no local models. Place checkpoints in `~/.friday/local_voice/…`." (names paths) | Show paths + CLI hint |
| `local_voice_load_failed` | corrupt/incompatible checkpoint | load raises after present | "Voice models failed to load — they may be corrupt." | Re-download / integrity check |
| `gpu_degraded` | Tier-2 requested, can't run | `gpu_tier_ready()==False` | "Running CPU voice — GPU voice needs a one-time install." | **Install GPU voice** → `{gpu}` |
| `gpu_install_failed` | torch/NeMo install error | job state `error` | phase-specific ("torch CUDA install failed", log tail) | Retry / stay on Tier 1 |
| `vad_downgraded` | Silero unavailable | `vad_backend==rms` | "Using basic voice detection." | Raise mic gain / install silero |
| `mic_no_audio` | no frames in 5 s | `no_audio_watchdog` (OVERHAUL §7.7) | "No audio reaching the app — check your mic." | Device picker + permission help |
| `mic_permission_denied` | `getUserMedia` reject | browser | "Microphone access is blocked." | Browser lock-icon guidance |
| `cloud_key_invalid` | bad/rotated key | `validate_gemini_key` 4xx | "Your Gemini key is invalid — paste a fresh one." | Settings → Providers |
| `cloud_key_missing` | no key any source | `resolve_gemini_key` empty | "No Gemini key found." | Settings → Providers |
| `cloud_model_stale` | key ok, model dead | `_compose_final_voice_error` all `model-missing` | "Your key is fine — the voice model is stale. Pick a current one." | Settings → Voice picker |
| `cloud_network` | offline/WS blocked | network error / 1006 | "Can't reach Gemini — check your connection." | Retry / switch to local |
| `egress_gate_blocked` | self-test failed → cloud disabled | `cloud_routing_enabled==False` | "Cloud voice is disabled because the privacy gate failed its check." | Show gate status; local works |

**Discriminator authority:** `_classify_live_error()` (`voice.py:293`) + `_compose_final_voice_error()` (`voice.py:317`) already separate auth vs model-missing and re-validate the key over REST. Keep that; the change is purely to **surface** the discriminator in-app on both clients (C-AC5, R6) and to add the local/GPU/egress codes above.

---

## 9. Egress Gate Interactions

### 9.1 Local tiers bypass (unchanged, verify)

Tier 1 and Tier 2 route the brain through the model router with a local provider; local providers bypass the gate by registry classification (`is_local_provider`). No text is sealed for on-device reasoning. **New guarantee (D-AC2):** for a local-only user, the voice brain *must* resolve to a local provider and *must refuse* to fall back to a cloud orchestrator — surfaced as `brain_provider` in `session-info`.

### 9.2 Tier 3 text sealing (unchanged — OVERHAUL §12.7)

Live system prompt is gated as a cloud provider: `egress_gate.gate_text` drops SENSITIVE / redacts PRIVATE (`voice.py:1069`). Per-utterance TTS text (`_synthesize_tts_wav`) routes PII-bearing text to local TTS at full fidelity, sending only scrubbed text to Gemini TTS.

### 9.3 Tier 3 raw-audio limitation (surface honestly — NG2, Persona C/D)

Raw mic PCM is streamed via `send_realtime_input(audio=Blob(...))` (`voice.py:1572`) with **no** gate — the gate is a text classifier and cannot inspect audio. **Requirement:** an unmissable, one-time in-UI notice on first Tier-3 activation: "Cloud voice streams your microphone audio to Google. It is not filtered by Friday's privacy gate. Use Local voice for private conversations." Plus a docs note. And the routing guard in §7.2 ensures a local-only user is never routed here.

### 9.4 Boot self-test proof surfaced (D-AC4)

`server.py` seals a known SSN+bank probe at boot and disables cloud routing on failure. Expose the verdict: `/api/voice/setup/status` and `/api/health/full` gain an `egress_gate: {self_test_passed: bool, cloud_routing_enabled: bool}` block. The wizard renders it. When `cloud_routing_enabled==false`, a Tier-3 attempt is blocked with `egress_gate_blocked` (§8).

### 9.5 Checkpoint download allowlist

Model downloads (whisper via CT2 hub, Piper/NeMo via HF `urlretrieve`) target `huggingface.co` / `download.pytorch.org`. These are **not** cloud LLM egress and correctly bypass the text gate, but the download hosts must be an explicit allowlist so a firewall/policy layer can permit exactly them. Document the allowlist: `huggingface.co`, `hf.co`, `download.pytorch.org`. Integrity via §5.5.

---

## 10. Verification Gate — Per-Tier End-to-End Smoke Test

A single harness, `tests/integration/test_voice_smoke.py`, parametrized per tier. Same shape everywhere: **synthetic WAV in → STT → canned response → TTS → byte-level output assertion.** Runs in CI (mocked GPU/cloud) and is invocable from the UI (real deps) via `POST /api/voice/setup/test-{stt,cloud}` and the wizard "Run full self-test" button.

### 10.1 Harness contract

```
synth = sine/burst PCM16 @16k mono with speech-like RMS (reuse VAD test fixtures, OVERHAUL §11.1)
Tier 1: inject FakeASR("hello friday") + FakeTTS; connect test WS to /ws/voice-local;
        send {type:'audio', data:b64(synth)}; assert frames:
          input_transcript.text == "hello friday"
          text (agent reply, canned via a stubbed brain)
          audio (b64 PCM16@24k) with len(decoded) > 0 and even byte count (PCM16)
          turn_end
Tier 2: same, but tier="gpu"; CI mocks torch.cuda + NeMo classes (gpu_status source=nvidia-smi stub);
        assert the tier-swap path AND (on a real GPU box, manual per TIER2 doc §82) the NeMo round-trip.
Tier 3: FRIDAY_TESTING=1 seam; mock google-genai Live session to emit one inline_data audio part;
        POST /api/voice/setup/test-cloud; assert classified outcome == ok and one audio frame;
        with a stubbed stale model, assert outcome == cloud_model_stale and NO local-TTS fallback.
```

### 10.2 CI matrix

- `test_voice_smoke_tier1` — full, no external deps (fakes). Must pass in CI.
- `test_voice_smoke_tier2_wiring` — mocked GPU; asserts detection fall-through (§4.3) and degrade banner. Must pass in CI. Real inference is manual (no GPU in CI — TIER2 doc).
- `test_voice_smoke_tier3` — mocked Live; asserts model attempt plan, stale-model failure surfaces (not local pass), and `detail` propagation.
- `test_error_taxonomy` — each §8 code produces the mapped `{code, message, action}` and the client renderer shows `detail`.
- `test_resolve_engine_local_only_never_cloud` — D-AC3.
- `test_egress_status_exposed` — D-AC4.

### 10.3 UI-invoked

The wizard "Run full self-test" button executes the same assertions against real deps and renders per-stage pass/fail (§6), giving the user the CI guarantee on their own machine.

---

## 11. Adjacent Improvements Roadmap

Synthesized from all four personas. Effort: **S** ≤1 day, **M** ~2–4 days, **L** ~1 week+.

| Item | Personas | Effort | Notes |
|---|---|---|---|
| **Push-to-talk (hold-to-talk)** | A, B, D | S–M | Gates mic frames; sidesteps `start_rms=600` false-neg (OVERHAUL §7.8); deterministic turn boundary; on cloud, stops billing ambient audio. Default "simple mode" for A. |
| **Input/output device picker** | A, B, C(PWA), D | M | Desktop `index.html` already honors `audio_input_device_id`/`setSinkId`; port to `friday_live.html:180` (C-AC/R8) and add a Settings picker with a live level meter. |
| **Live captions (both sides)** | A, C, D | S | Pipeline already emits `input_transcript` + `text` (`voice.py:848,864`); render as persistent toggleable captions, not a truncated status line. Doubles as a diagnostic. |
| **Barge-in on local path** | A, B | M | Cloud has `LiveBargeDetector`; `/ws/voice-local` has none. Add a "tap to interrupt" button first (S), then port bridge-side detection (M). |
| **Latency budget + live meter** | A, B, C | S–M | `perf_stats` records `asr_ms`/`tts_ms` (`local_voice.py`); Live timestamps `_last_gemini_ts`. Show a one-time "your device: ~Xs" calibration and a live round-trip readout; warn when a non-native-audio model is selected on Tier 3. |
| **VRAM headroom advisory** | B | S | Free-VRAM gate can bounce Tier 2 when the card is busy. Show "GPU: X GB free / 12 GB" + "close other GPU apps"; consider gating on *total* VRAM with a soft free-VRAM warning rather than a hard bounce. |
| **Streaming partial ASR (Tier 2)** | B | L | Nemotron is cache-aware streaming (`att_context_size=[56,3]`); wire partial transcripts (OVERHAUL §12.3). |
| **Airplane / Sovereign mode toggle** | D | M | Forces `local_only`, pins `voice_engine=local`, refuses `/ws/live`, persistent "nothing leaves this device" indicator. Removes reliance on network-probe timing (D-AC3). |
| **Wake word ("Hey Friday", opt-in local)** | A(later), B, C | L | openWakeWord/porcupine; only opens the mic after firing (aligns cloud with sovereignty ethos). Lowest priority. |
| **Barge-in preset per output device** | C | S | Auto-suggest `headphones` mode when a headphone output is selected (native `START_OF_ACTIVITY_INTERRUPTS`). |
| **Cloud cost/usage surface + auto-stop** | C | M | Registry carries `cost_per_1k`; show session duration/est. cost; auto-stop after N minutes of silence. |
| **Self-refreshing Live-model discovery** | C | M | "Refresh voice models" populates the picker from Google's models endpoint filtered to `bidiGenerateContent`; retirements degrade without a code release (C-AC/R5). Builds on §4.4. |
| **Additional Piper voices / languages** | — | M | `PiperTTS` supports arbitrary names; extend download-URL resolution + a browse/preview UI (OVERHAUL §12.2/§12.6). |

---

## 12. Phased Implementation Plan

### Phase 1 — Restore all three tiers out of the box (blockers)

**Goal:** a new user, per persona, gets working voice or a one-click path — no terminal.

1. **Tier-1 bundling (R1, A-AC1):** move `faster-whisper`, `piper-tts`, `onnxruntime` into core `dependencies` **or** make the shipped Windows installer/venv always install `.[voice-local-lite]`. Prefer the installer route to keep core lean; whichever, `deps_installed()==True` on a standard install.
2. **Client renders `detail` (R4, A-AC4, C-AC5):** fix `app.html:8358-8371` and `friday_live.html:312-318` to show `m.detail` for all error frames. Add the `{type:'error-nonfatal'}` banner path.
3. **Tier-3 default single-source-of-truth (R1/C-AC1):** reconcile `provider_registry.py:147`, `core_routes.py:218`, `index.html:31350`, `app.html:7455` to `gemini-2.5-flash-native-audio-latest` from one constant. Zero `gemini-3.1-flash-live-preview` defaults.
4. **Tier-3 picker (R2/C-AC2):** add the two verified ids to the Voice-role registry list; flag/remove the retired 12-2025 preview.
5. **`validate_live_model` capability probe (R4/C-AC4):** replace the substring heuristic (§4.4); wire boot warning + auto-correct on `stale`.
6. **GPU detection fall-through (B-AC1):** `gpu_status()` continues to nvidia-smi when torch reports no CUDA (§4.3); Settings shows "GPU detected — Tier 2 available".
7. **Offline model error (D-AC1):** `ensure_ready()` detects offline before fetch and emits `models_missing_offline` naming the pre-stage paths (§5.4), not the generic slug.
8. **Local-brain guarantee + routing guard (D-AC2/D-AC3):** local-only forces a local voice brain and blocks `_resolve_voice_engine` from returning `gemini`; `session-info` exposes `brain_provider`.

**Exit:** §10 CI smoke tests (Tier1 real, Tier2 wiring, Tier3 mocked) + taxonomy + local-only tests all green.

### Phase 2 — Installers, diagnostics, proof

9. **`services/voice_installer.py` + endpoints (§5.1):** `install`, `install/status`, `install/cancel`, `download-models`, `test-cloud`. Background jobs, no 180 s cap (replaces `agent.py:538,552` for voice installs).
10. **Tier-1 in-UI install (R2/A-AC3):** one-click "Set up voice" with progress; disk preflight; resume.
11. **Tier-2 in-UI install (R2/R3/B-AC2):** torch `--force-reinstall` cu124 + `.[voice-local-gpu]`, byte/phase progress, cancel, hot-swap enable.
12. **Diagnostics wizard rebuild (§6):** stage-by-stage with remediation actions; GPU sub-block independent of active engine (B-AC3); onboarding tier-aware (R5/A-AC5).
13. **Determinate model-download progress (B-AC5/A-AC6/R6):** percent/bytes/ETA + cancel for whisper/piper/nemo.
14. **Mic readiness affordance (R7):** mic button reflects readiness; unavailable → routes to setup.
15. **Real cloud self-test (C-AC3):** `test-cloud` opens `/ws/live`, asserts one audio frame, never falls to local pyttsx3.
16. **Egress proof in UI (D-AC4):** `egress_gate` block in status/health; wizard renders it; Tier-3 blocked when `cloud_routing_enabled==false`.
17. **Raw-audio Tier-3 notice (§9.3) + checkpoint integrity (§5.5).**

**Exit:** all four personas pass their §3 acceptance criteria on a clean machine; wizard "Run full self-test" green.

### Phase 3 — Adjacent improvements (§11)

Sequence by effort/impact: PTT (S) → device picker incl. PWA (M) → live captions (S) → latency meter (S–M) → Airplane/Sovereign mode (M) → local-path barge-in (M) → self-refreshing Live-model discovery (M) → VRAM advisory (S) → cost surface (M) → streaming ASR (L) → wake word (L) → multi-voice/lang (M).

---

## Appendix A — File / Route Change Index

| Change | File(s) |
|---|---|
| Tier-1 bundling | `pyproject.toml` (or installer script) |
| Client renders `detail` | `ui_parts/app.html:8358-8371`, `friday_live.html:312-318` |
| Tier-3 default reconcile | `services/provider_registry.py:147`, `routes/core_routes.py:218`, `index.html:31350`, `ui_parts/app.html:7455`, `core/__init__.py` |
| Live-model capability probe | `services/voice_engine.py:805,808` |
| GPU detect fall-through | `services/nemo_voice.py` (`gpu_status`) |
| Offline model error | `services/local_voice.py` (`ensure_ready`), `routes/voice.py:764-766` |
| Local-brain guard | `routes/voice.py` (`_resolve_voice_engine:471`, `_generate_agent`), `core/__init__.py:1719-1741` |
| Install jobs, no 180 s cap | new `services/voice_installer.py`; supersede `services/agent.py:511-552` for voice |
| New endpoints | `routes/voice.py` (`/api/voice/setup/install[/status|/cancel]`, `/download-models`, `/test-cloud`) |
| Wizard rebuild | `ui_parts/app.html:6619` (VoiceSetupWizard), `:6139` (onboarding), `:6140-6146` (WIZARD_VOICES) |
| Egress status block | `routes/voice.py` (`voice_setup_status:570`), `server.py` (boot self-test) |
| Smoke tests | new `tests/integration/test_voice_smoke.py`; extend `tests/unit/test_*_voice.py` |

## Appendix B — Verified Facts (this spec is grounded on)

- Tier-1 deps are optional-only: `pyproject.toml:28-34` (`voice-local-lite`), included in `[all]` `:81-83`, absent from core `dependencies` `:4+`.
- No install endpoint exists: only `/api/voice/setup/status` (`voice.py:570`) and `/api/voice/setup/test` (`voice.py:659`).
- `LIVE_MODEL` backend default already corrected: `voice_engine.py:632` = `gemini-2.5-flash-native-audio-latest`; fallbacks `:640-641`.
- `validate_live_model` substring heuristic: `_LIVE_MODEL_MARKERS` `voice_engine.py:805`, function `:808`.
- Stale default still in registry/UI: `gemini-3.1-flash-live-preview` present across `provider_registry.py`, `core_routes.py`, `index.html`, `app.html`, `core/__init__.py`.
- pip 180 s cap: `agent.py:538,552`.

---

## 13. Tier-3 Live Fluidity — interruption, audio sync, hours-long continuity

First-class requirements (2026-07-06). Every fact below was verified against
Google's CURRENT Live API docs (ai.google.dev/gemini-api/docs/live-*, /api/live)
on 2026-07-06 — not training-data recall. Serves Persona E (§3.5).

### 13.1 Audio format (confirmed, not the regression)
- Output: raw **16-bit PCM, 24 kHz, mono, little-endian** (both native-audio and
  half-cascade). Input the API expects: **16-bit PCM, 16 kHz, mono**.
- The client pins the playback `AudioContext` to 24 kHz and feeds a
  `friday-pcm-player` AudioWorklet ring buffer. Raspiness is NOT a format bug —
  it is a jitter/underrun/overlap problem (§13.3).

### 13.2 Interruption / barge-in
- Config: `realtime_input_config = RealtimeInputConfig(automatic_activity_detection=…,
  activity_handling=…, turn_coverage=…)`.
- **`activity_handling` is the load-bearing field.** `NO_INTERRUPTION` = VAD fires
  but the model never stops (reads as "won't interrupt"). `START_OF_ACTIVITY_INTERRUPTS`
  = barge-in. Friday's default is now the latter for every mode except the
  explicit `no-barge` opt-out (`_build_realtime_input_config`, `routes/voice.py`).
- On a user interrupt the server sends `server_content.interrupted=True`; the
  bridge forwards `{type:'interrupted'}` and the client MUST flush queued
  playback (it posts `{type:'flush'}` to the worklet, which drops all pending
  samples and re-primes). Draining instead of flushing is itself a rasp source.
- Echo mitigations for open speakers (so barge-in doesn't self-trigger): mic
  `echoCancellation:true` + `start_of_speech_sensitivity=START_SENSITIVITY_LOW`.
  Users who still self-interrupt pick `no-barge` (then the bridge RMS talk-over
  detector + Esc are the interrupt paths).
- Acceptance: barge-in cuts playback within ~200 ms (E-AC1).

### 13.3 Audio sync / anti-rasp (client worklet)
- **Prefill jitter cushion:** ~120 ms buffered before playback starts, re-primed
  after any underrun, so a network gap never underruns into a click. Accumulated
  clicks are the classic "raspy as she talks, worse over time".
- **Ring sizing + anti-wrap:** the ring holds a full faster-than-realtime
  response (180 s); an anti-wrap guard fast-forwards the reader only if the
  writer approaches lapping it (a >~3 min continuous monologue) — it NEVER
  truncates a normal multi-second turn.
- No per-chunk `BufferSource` scheduling on the worklet path (that legacy
  fallback accumulates resampler/`startAt` rounding = progressive rasp).
- Acceptance: no audible degradation over a long session (E-AC2).

### 13.4 Hours-long session continuity
Three independent limits, all must be handled (docs → live-session):
- **~15 min** audio-only session duration cap → removed by
  `context_window_compression=ContextWindowCompressionConfig(sliding_window=SlidingWindow())`,
  **on by default** (`voice_context_compression`). Prunes only the oldest turns;
  durable facts persist in Friday's memory subsystem.
- **~10 min** single-connection cap (independent of the above) → handled by the
  GoAway loop: `go_away.time_left` triggers a drain-then-reconnect using the
  latest `session_resumption_update.new_handle`, so the socket cycles without a
  user-perceptible break and full context is restored.
- Resumption handles valid ~2 h after last termination.
- Acceptance: context + quality survive multi-hour cycling (E-AC3).

### 13.5 Model feature notes (current)
- `enable_affective_dialog` and `proactivity.proactive_audio` require **v1alpha**
  and are supported on **2.5 native-audio**, NOT on `gemini-3.1-flash-live-preview`
  — the code strips them per-attempt for models/endpoints that don't support them.
- Verified-live chain (real connect probe 2026-07-06):
  `gemini-2.5-flash-native-audio-latest` → `…-preview-09-2025` →
  `gemini-3.1-flash-live-preview`. `gemini-2.5-flash-preview-native-audio` does
  NOT exist upstream (1008) — kept in `_RETIRED_LIVE_MODELS`.

### 13.6 Tests
- `tests/unit/test_voice_live_tuning.py`: interruption default = barge-in, opt-out
  = NO_INTERRUPTION, VAD stays enabled, compression on by default, LOW start
  sensitivity, chain distinct/non-retired.
- Manual/opt-in: `FRIDAY_SMOKE_CLOUD=1` live connect probe of the whole chain;
  long-session soak (barge-in latency, no rasp, GoAway reconnect) is a manual
  procedure in `tests/MANUAL_TEST_PROCEDURES.md` (real audio hardware + hours).
