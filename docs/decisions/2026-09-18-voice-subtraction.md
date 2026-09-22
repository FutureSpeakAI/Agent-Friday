# Voice subtraction — survey, measurements and the deletion proposal

> **Status:** PROPOSED — awaiting Stephen's go on the deletion list
> **Written:** 2026-09-18 (Fable 5.1, high effort)
> **Supersedes when accepted:** `docs/design/active/voice-system-clean-sheet.md`,
> `voice-system-spec.md`, `voice-mode-diagnosis-and-repair.md`,
> `local-voice-repair-and-native-audio.md`, `cloud-voice-providers.md`,
> `elevenlabs-voice.md` (all six move to `docs/design/historical/`)

Stephen's requirement, verbatim: *"I hate how the UI gets in the way now and
it's super laggy. I want Gemini Live working fast, and I want the very latest
version. I also want ONE on-device voice in/out option, and ONE
hotkey-toggle-to-transcribe option that does not include voice out and works
on the default model."*

Read as: exactly three things exist. Everything else stops occupying the UI
and stops costing latency at idle.

Every number below carries a tag: **MEASURED** (today, on this machine),
**TREE** (read from the code today), **VENDOR** (Google's published docs).

---

## 1. What exists today (TREE)

### 1.1 Python surface — 17 modules, ~14,800 lines

| Module | Lines | State in tree | What it is |
|---|---|---|---|
| `routes/voice.py` | 3,414 | committed | Gemini Live bridge (`/ws/live`), local session route (`/ws/voice`, `/ws/voice-local`), setup wizard routes, arm/prove/warm, TTS one-shot |
| `routes/voice_context.py` | 257 | committed | workspace voice prompts, `/api/voice/start-my-day` |
| `routes/cloud_voice_routes.py` | 131 | **untracked since 09-09** | ElevenLabs/Inworld cloud TTS routes |
| `services/voice_engine.py` | 1,478 | committed | misnamed: Live tools + `ask_friday`, Gemini key resolution, Live context, `_persist_voice_turn`, **and `_notif_engine`, imported by 11 non-voice modules** |
| `services/local_voice.py` | 1,139 | modified, uncommitted since 09-10 | `VADEndpointer`, `WhisperASR`, `PiperTTS` + a 550-line `LocalVoiceEngine` tier resolver that dispatches to NeMo and Kokoro |
| `services/nemo_voice.py` | 751 | modified, uncommitted since 09-10 | NeMo ASR/TTS + `gpu_status()` — the probe behind the "VRAM measurement disputed" flood |
| `services/kokoro_voice.py` | 527 | untracked since 09-09 | Kokoro-82M torch TTS |
| `services/native_audio.py` | 306 | untracked since 09-10 | "understand" listening-mode decision layer — **zero importers** |
| `services/voice_workers.py` | 855 | committed 09-16 | GPU child workers, arbiter leases, `GpuQueue`, `CpuWhisperEar`, `PiperMouth` |
| `voice/worker.py` | 358 | committed 09-16 | the child process (Kokoro CUDA / whisper CUDA / fake) |
| `services/voice_manifest.py` | 746 | committed 09-16 | ear/mind/mouth proofs, TTLs, `describe_for_model`, tool contract |
| `services/voice_session.py` | 606 | committed 09-16 | the streamed local turn (VAD, speculative chunked ASR, clause chunker, barge) |
| `services/voice_receipt.py` | 289 | committed | `friday.log` receipts |
| `services/voice_installer.py` | 398 | committed | in-UI installer for NeMo / Kokoro / lite deps |
| `services/voice_indicator.py` | 176 | untracked since 09-10 | "what served" singleton for cloud TTS |
| `services/cloud_voice.py` | 815 | untracked since 09-09 | ElevenLabs / Inworld TTS providers |
| `voice_personality.py` | 168 | committed | mood profiles for Gemini affective dialog |

Plus: `static/cloud_voice_playback.js` (never loaded by any page — dead),
`static/live/friday_live.html` (the standalone PWA client on `/ws/live`;
keeps working), `src/agent_friday/VOICE_DEMO.md` (loaded into the Live
prompt; kept).

### 1.2 UI surface — `index.html` (authoritative; `ui_parts/app.html` is a stale mirror)

| Lines | Block | Idle cost |
|---|---|---|
| 38928–39182 | `TTS_VOICES`, `VOICE_LANGUAGES`, `VOICE_MARKUP_TAGS`, `VOICE_STYLE_PRESETS`, `previewVoice` | **dead, zero references** (~255 lines) |
| 34513–34801 | `VoiceSetupWizard` | mounted on click only |
| 34849–34966 | `VoiceStackCard` / `VoiceStackRow` / `VoiceModePicker` | on the Settings → Voice tab: arms the proofs on mount, self-polls `/api/voice/arm` every 1.2 s while proving |
| 34967–35190 | `SettingsTabVoice` — 16 controls | — |
| 40042–41521 | live-session engine (worklet player, mic, WS, teardown, mic/speaker tests) | none idle; all timers torn down |
| 43922–43995 | in-session HUD strip (lamps, contract line, partial transcript, notices, egress) | two conditional divs at idle |
| 38406–38448 | `ArbiterStrip` — **not voice**; polls `/api/arbiter/status` every 5 s, always | nvidia-smi + a 150 ms blocking CPU sample per poll |
| 3348–3358, 518–525, 30595–30616, 39865–39871 | dead scene mic, dead CSS, dead constants | dead |

### 1.3 Settings keys read by voice code — 30

`voice_engine`, `voice_model`, `tts_voice`, `voice_style_prompt`,
`voice_language`, `voice_temperature`, `voice_max_tokens`, `voice_affective`,
`voice_proactive`, `voice_context_compression`, `voice_interruption_mode`,
`voice_barge_grace_ms`, `voice_barge_sustain_ms`, `voice_tools`,
`voice_silence_ms`, `voice_ear_gpu`, `voice_mouth_gpu`, `voice_idle_unload_s`,
`voice_local_rate`, `offline_voice_fallback`, `local_voice_asr_model`,
`local_voice_tts_engine`, `local_voice_tts_voice`, `local_voice_kokoro_voice`,
`local_voice_kokoro_allow_cpu`, `local_voice_gpu_asr_model`,
`local_voice_gpu_tts`, `elevenlabs_model/_voice_id`, `inworld_model/_voice_id`,
`inworld_plan_tier`.

### 1.4 Tests — ~57 files mention voice; ~500 tests

Per module (files / tests that reference it): nemo_voice 8/139, cloud_voice
6/74, voice_manifest 6/49, kokoro_voice 3/47, voice_indicator 2/27,
voice_installer 2/23, cloud_voice_routes 2/23, voice_workers 2/22,
native_audio 1/22, voice_session 2/16.

---

## 2. Where the idle cost comes from (MEASURED + TREE)

**The flood.** `friday.log` has 7,022 "VRAM measurement disputed" lines. Chain:
the always-mounted `QuickSwitch` pill polls `GET /api/intelligence` every 20 s
→ `model_catalog.build_catalog()` (uncached) → `_tts_engines()` →
`kokoro_health()` → `nemo_voice.gpu_status()` **and** `_voice_engines()` →
`is_provider_available("nvidia-nemo")` → `gpu_tier_ready()` →
`gpu_status()`. The cache TTL is 30 s (not 300 s) because Stephen's
`local_voice_tts_engine` is `kokoro`, so a fresh torch CUDA query plus an
`nvidia-smi` subprocess runs roughly every 40 s, all day, and logs the
dispute each time. Deleting Kokoro and NeMo deletes the chain.

**Not voice, but adjacent (flagged, not touched):**
- `ArbiterStrip` polls `/api/arbiter/status` every 5 s: one `nvidia-smi` plus
  a blocking `psutil.cpu_percent(interval=0.15)` per poll. Biggest idle poll in
  the UI.
- `machine_monitor` runs `nvidia-smi` and a PowerShell `Get-Counter` every 60 s.
- `/api/health` every 15 s (cached server-side; cheap).

**Nothing voice-related runs on a server-side timer at boot.** The cost is
all request-driven through the catalog.

---

## 3. Gemini Live — what Google shipped since this was integrated (VENDOR + MEASURED)

- **`gemini-3.8-live`** and **`gemini-3.8-live-extended-thinking`** were
  released **2026-09-15** (three days ago), both stable. Google's models page
  now labels `gemini-3.1-flash-live-preview` "legacy" and recommends 3.8 Live.
  `gemini-2.0-flash-live-001` and `gemini-live-2.5-flash-preview` were shut
  down 2025-12-09. Stephen's key sees all of: 3.8-live, 3.8-live-extended-
  thinking, 3.1-flash-live-preview, 2.5-flash-native-audio-{latest,09-2025,
  12-2025}, 3.5-transcribe, 3.5-transcribe-live, three TTS previews.
- Stephen is on `gemini-2.5-flash-native-audio-latest`.
- **Real connects today, with his key, on the installed SDK (google-genai
  1.72.0), text prompt "Say hi in three words", time to first audio byte:**

| Model | Endpoint | First audio | Notes |
|---|---|---|---|
| `gemini-2.5-flash-native-audio-latest` (current default) | v1beta | **3.59 s** | |
| same, with affective + proactive | v1alpha | 2.55 s | the path the current code tries first |
| **`gemini-3.8-live`** | v1beta | **0.76–0.86 s** (3 runs) | compression + session resumption both accepted |
| `gemini-3.1-flash-live-preview` | v1beta | 0.65 s | "legacy" per Google |
| `gemini-3.8-live` + `enable_affective_dialog` | v1beta / v1alpha | **rejected** (1007 invalid argument) | |
| `gemini-3.8-live` + `proactivity` | v1beta | **rejected** ("Unknown name proactivity") | |
| `gemini-3.8-live-extended-thinking` | v1beta | no audio in 1.3 s; `turn_complete` with empty output | requires `thinking_level`; not adopting until it proves audio |

- **SDK:** 1.72.0 installed, 2.24.0 current (2026-09-16). The 2.0.0 notes say
  the breaking changes are in the Interactions API only; the Live API is
  unaffected, and 3.8-live connected fine on 1.72. **No SDK upgrade in this
  pass** — it would touch every Gemini call in the app for no measured gain.
- **Pricing (paid tier, per 1M tokens):** 3.8 Live / 3.1 Flash Live: text in
  $0.75, audio in $3.00, text out $4.50, audio out $12.00. 2.5 native audio:
  text in $0.50, audio in $3.00, text out $2.00, audio out $12.00. The cost
  meter currently bills Live at the text rate per 1K ($0.0005/$0.002); the 3.8
  entries will be added at the same convention with a note.
- Session limits are unchanged: 15 min audio-only without compression,
  compression makes it unlimited, resumption handles valid 2 h.

**Consequence:** the current attempt chain (v1alpha-first when affective or
proactive is on → v1beta → two 2.5 fallbacks), the affective/proactive/
temperature toggles and `voice_personality.py`'s affective scaffolding are
dead weight on the new model.

---

## 4. Local voice — what the CPU can actually do (MEASURED)

GPU: RTX 4070, 12,282 MiB. Under Stephen's testing the reasoning seat holds
~11 GB, so GPU voice cannot be admitted anyway. Right now the seat is **not**
running (1,660 MiB in use, no `llama-server.exe`, nothing on 8099); I did not
touch it.

CPU-only probes today (i7-10700F, 8 cores), int8, beam 1, `cpu_threads=8`,
two Piper-synthesized 6 s utterances, three runs each, CUDA hidden:

| Ear | Load | Per 6 s utterance | Transcript |
|---|---|---|---|
| faster-whisper `small` (on disk today, 464 MB) | 1.4 s | **2.26–2.31 s** | correct |
| `small.en` | 14 s (download) | 2.09–2.21 s | correct |
| `distil-small.en` | 8 s (download) | 1.78–1.82 s | correct |
| **`base.en`** (145 MB) | 4.3 s (download) | **0.64–0.74 s** | correct on both, incl. "4.15" and "future speak" |

| Mouth | Load | Speed |
|---|---|---|
| **Piper `en_US-amy-medium`** (on disk, 61 MB) | 2.9 s | 3.1× realtime first synthesis (4.7× warm, 09-16) |
| Kokoro-82M torch, CPU (09-16 measurement) | 3.1 s | 1.6× realtime, and threw a `TypeError` in g2p on sentence 2 |

Downloads for this went to my scratchpad, not `~/.friday`.

The 2026-09-18 live run recorded the CPU-only path at **8–13 s to
transcript** because the speculative 2 s chunk passes and the full re-pass
serialise on one CPU. With one pass at VAD close on `base.en` that is ~0.7 s.

---

## 5. The proposal — three things exist

### 5.1 Gemini Live (cloud, fast, current)
- Default `gemini-3.8-live`; fallback `gemini-3.1-flash-live-preview`; both
  verified by a real connect today. v1beta only. Context compression and
  session resumption on. Native barge-in (`START_OF_ACTIVITY_INTERRUPTS`).
- Kept: tools + the `ask_friday` relay (how cloud voice reaches Stephen's
  context through the local model), egress gates, spend guard, cost metering,
  turn persistence, the PWA client.
- Deleted: v1alpha attempt, affective dialog, proactive audio, voice
  temperature, max-tokens, `voice_personality.py`, the model-marker heuristics
  in `validate_live_model`.
- Settings kept: `voice_model` (a select of the two verified ids), `tts_voice`
  (Gemini voice name), `voice_style_prompt`, `voice_interruption_mode`
  (auto / no-barge — kept because it exists for a real speaker-echo problem).

### 5.2 One on-device voice in/out
- **Ear:** faster-whisper `base.en`, int8, beam 1, 8 threads, CPU, one pass at
  VAD close. In-process, lazy-loaded on first use, unloaded after 10 min idle.
  If real-mic accuracy disappoints, the swap is one constant to `small.en`
  (+1.5 s per utterance).
- **Mouth:** Piper `en_US-amy-medium`, CPU, streamed per clause (the clause
  chunker stays; first audio lands while the model is still writing).
- **Mind:** unchanged — the resident seat through the same agentic pipeline
  text chat uses, prefix-stable prompt, precomputed tool contract.
- Nothing on the GPU, no leases, no child workers, no proofs, no TTL. If the
  ear fails to load the session says so in one `error` frame.
- Settings kept: `voice_engine` (`local` | `gemini`), `voice_silence_ms`.

### 5.3 One hotkey toggle to transcribe
- In the app: **Ctrl+Space** toggles capture (a `micPulse` on the composer
  button shows it is on). Press again → PCM goes to a new
  `POST /api/voice/transcribe` → the same `base.en` ear → the text lands in the
  composer and is sent as an ordinary chat turn to whatever the default model
  is. Text reply, no speech out, no Live session, no `/ws/*`.
- A **global OS hotkey** (works when the window is not focused) needs the
  tray/launcher, which is being edited by the other session — not in this
  pass; say the word and it is a 30-line pynput addition there later.

### 5.4 What the UI keeps
- Composer mic button (🎤 = start local/Gemini voice per `voice_engine`; ■ =
  stop), Ctrl+Space transcribe, the 🎚 device picker.
- In-session: one line — `LIVE · listening|thinking|speaking` plus the
  transcript (voice turns already appear as messages). When the Gemini path
  is active, one "☁ mic → Gemini Live" marker (transparency constraint C2).
- Settings → Voice: six controls. Mode (Local / Gemini Live), Gemini model,
  Gemini voice, speaking style, interruption mode, silence threshold.

---

## 6. The deletion list

### 6.1 Files deleted outright (~5,300 lines of Python, 3 assets)
| File | Lines | Why it can go |
|---|---|---|
| `services/nemo_voice.py` | 751 | NeMo engines are unreachable from `/ws/voice` already; `gpu_status` is the flood; consumers all go with it (platform `/api/health/full` entry dropped) |
| `services/kokoro_voice.py` | 527 | slower than Piper on CPU, crashed on sentence 2, needs the GPU the seat holds |
| `services/native_audio.py` | 306 | zero importers in `src/` |
| `services/voice_workers.py` | 855 | GPU workers + leases + `GpuQueue`; `CpuWhisperEar`/`PiperMouth` (~60 lines) move into `local_voice.py` |
| `voice/worker.py`, `voice/__init__.py` | 364 | the child process |
| `services/voice_manifest.py` | 746 | proofs, TTLs, arm/prove; `compute_contract` (~60 lines) moves to `routes/voice.py` |
| `services/voice_installer.py` | 398 | installs NeMo/Kokoro/lite; the one remaining pair downloads on first use with a status line |
| `services/cloud_voice.py` | 815 | ElevenLabs/Inworld: not one of the three; never wired into a page |
| `routes/cloud_voice_routes.py` | 131 | same |
| `services/voice_indicator.py` | 176 | replaced by the one egress marker the WS frame already carries |
| `voice_personality.py` | 168 | affective-dialog scaffolding; rejected by 3.8-live |
| `static/cloud_voice_playback.js` | 136 | not loaded anywhere |
| `ui_parts/app.html.ae0.bak` | — | backup file in the tree |

### 6.2 Files shrunk
| File | From → to (est.) | What goes |
|---|---|---|
| `routes/voice.py` | 3,414 → ~1,900 | setup/install routes, arm/prove/warm, `_resolve_voice_engine` tier logic, `_voice_context_reach`/`_local_mind_proven`, `fallback-status`, v1alpha chain, affective/proactive/temperature plumbing, `_warm_local_voice_async`; **adds** `POST /api/voice/transcribe` |
| `services/local_voice.py` | 1,139 → ~450 | `LocalVoiceEngine` tier resolver, hot-swap, NeMo/Kokoro dispatch, `peek_tier`/`effective_tts`; keeps `VADEndpointer`, `WhisperASR`, `PiperTTS`, resamplers, a static `health()` for cli/platform/footprint |
| `services/voice_session.py` | 606 → ~520 | `gpu_queue`, speculative chunked ASR, `partial_transcript` |
| `services/voice_engine.py` | 1,478 → ~1,400 | retired-model set, affective heuristic, marker heuristic; new ids and chain. **Everything else stays** (11 non-voice importers) |
| `services/model_catalog.py` | `_tts_engines` deleted; `_voice_engines` becomes two static entries with no probe | this is what stops the idle GPU query |
| `services/provider_registry.py` | `nvidia-nemo` provider removed; Google voice `model_meta` → the two verified ids | |
| `services/provider_health.py` | `nemo-local` branch removed | |
| `services/cost_meter.py` | + `gemini-3.8-live`, `gemini-3.8-live-extended-thinking` rates | |
| `core/__init__.py` | 24 voice settings defaults removed | |
| `index.html` | ~2,900 voice lines → ~1,700 | wizard, stack card, mode picker, 10 of 16 settings controls, HUD lamps/contract/notices, the 255 dead lines, dead scene fragments; **adds** ~90 lines for Ctrl+Space capture reusing the existing mic path |
| `creative_engine.py`, `setup_wizard.py`, `routes/core_routes.py`, `routes/platform.py`, `routes/intelligence.py`, `cli.py`, `footprint_measure.py`, `prewarm.py` | one-line reference updates | |

### 6.3 Settings keys removed (24)
`voice_affective`, `voice_proactive`, `voice_temperature`, `voice_max_tokens`,
`voice_context_compression`, `voice_barge_grace_ms`, `voice_barge_sustain_ms`,
`voice_tools`, `voice_language`, `voice_local_rate`, `offline_voice_fallback`,
`voice_ear_gpu`, `voice_mouth_gpu`, `voice_idle_unload_s`,
`local_voice_asr_model`, `local_voice_tts_engine`, `local_voice_tts_voice`,
`local_voice_kokoro_voice`, `local_voice_kokoro_allow_cpu`,
`local_voice_gpu_asr_model`, `local_voice_gpu_tts`, `elevenlabs_model`,
`elevenlabs_voice_id`, `inworld_*`. Existing values in `settings.json` are
ignored, not migrated; `voice_engine: auto|local-gpu` reads as `local`.

### 6.4 Tests
Delete the files for deleted modules (`test_nemo_voice`, `test_kokoro_readiness`,
`test_native_audio_mode`, `test_voice_workers`, `test_voice_manifest`,
`test_voice_manifest_routes`, the four cloud-voice gauntlets,
`test_inworld_ga_unestablished`, `test_voice_personality`, `test_voice_tiers`,
`test_local_voice_tier`, `test_voice_mode_repair*`, `test_voice_setup_routes`,
`test_breeze_voice` leftovers). Rewrite `test_voice_dead_settings`,
`test_local_voice_settings_surface`, `test_voice_local`, `test_voice_session`,
`test_voice_streaming`, `test_voice_live_helpers` to the new surface. Add:
transcribe endpoint (auth, PCM shape, empty audio), the model chain (3.8 →
3.1, no v1alpha), the catalog no longer imports torch, settings surface = 6
keys. Net ≈ −350 tests, +15.

### 6.5 Docs
The six active voice specs move to `docs/design/historical/`; this file is the
decision of record; `docs/DECISIONS.md` gets one line.

### 6.6 Left on disk, NOT deleted by me (Stephen's data)
`~/.friday/models/nemo` (2.8 GB), `~/.friday/runtime/kokoro-onnx` (109 MB),
`~/.friday/local_voice/whisper/models--Systran--faster-whisper-small`
(464 MB, replaced by `base.en` 145 MB). Reclaimable after the change lands.

---

## 7. Not deleted, and why

- `services/voice_engine.py` bulk — `_notif_engine`, the Live tool surface,
  key resolution, `_persist_voice_turn` are imported by server bootstrap,
  scheduler, notifications, cost_meter, model_router, connectors, file_grants,
  news. Trimmed, not removed.
- `services/arbiter.py`, `residency_arbiter.py`, `routes/arbiter.py` — GPU
  leases for seats and image models; voice was one tenant. Untouched.
- `routes/voice_context.py` — workspace voice prompts and start-my-day. Kept.
- `/api/voice/tts` one-shot Gemini TTS — used by "Read aloud" on briefings and
  the front page. Kept; not a voice mode.
- `static/live/` PWA client — keeps working on `/ws/live`.
- `ArbiterStrip` 5 s poll and `machine_monitor` — not voice; flagged in §2.
- The excluded files (`tool_selector`, `tool_budget`, `model_router`,
  `tool_receipts`, scheduler, tray, holographic UI) — untouched. Voice's tool
  contract reads `tool_budget` and does not need to change it.

---

## 8. Trade-offs Stephen should veto or accept

1. **The GPU ear goes.** When the card is free, whisper on CUDA measured
   0.2 s vs 0.7 s for `base.en` on CPU. Under his actual testing the GPU
   path is refused anyway, and the worker/lease machinery is ~2,000 lines.
2. **`base.en` over `small`.** 3× faster; accuracy measured only on clean
   synthesized speech, not a real mic. One-constant swap if it disappoints.
3. **No engine menu at all.** No Kokoro, no NeMo, no cloud TTS providers, no
   GPU policy. That is the ask; it is stated here so it is a decision.
4. **Affective/proactive audio gone.** Not available on 3.8-live at all.
5. **`voice_interruption_mode` survives** as the one hardware-dependent
   toggle (speaker echo). Easy to delete too if he wants five controls.

---

## 9. Constraints on the build

- Friday on :3000 and the seat are never restarted or touched. Verification
  runs on a new launch entry `friday-voice-dev` (port 3210, `FRIDAY_TESTING`
  off so voice runs, `FRIDAY_HOME` in my scratchpad with a copy of
  `settings.json`, the Piper voice and the `base.en` model). Local-mind
  verification needs the seat; it is down as of 11:10 today, so the local
  path is verified ear+mouth+transcribe until it is up.
- The tree carries other sessions' uncommitted hunks in `index.html`,
  `server.py`, `core/__init__.py`, `model_catalog.py`, `cost_meter.py`,
  `local_voice.py`, `nemo_voice.py`. I stage only my hunks (filtered patches
  against a pre-edit snapshot of each file, `git apply --cached --3way`). The
  simplest unblock is for the owners of those hunks to commit first.
- Untracked files being deleted were last modified 2026-09-09/10 and belong to
  no live session.
- No downloads into `~/.friday` before the go; `base.en` lands there on the
  first local session (145 MB) or by an explicit setup step.

## 10. Build order once approved

0. Branch `voice/subtraction-2026-09-18`; this doc committed first.
1. **Gemini:** ids, chain, config trim, registry/catalog/cost/settings, tests.
2. **Local:** `local_voice.py` rewrite, `voice_session.py` trim,
   `routes/voice.py` trim, module deletions, test pruning; full `pytest`.
3. **Hotkey:** `POST /api/voice/transcribe` + Ctrl+Space capture + auto-send.
4. **UI:** Settings tab, HUD, dead code; `ui_parts/app.html` untouched (stale
   mirror, documented in `build_ui.py`).
5. **Verify** on `friday-voice-dev`: Gemini first-audio latency from the
   browser, local ear/mouth turn, transcribe round trip, idle log quiet for
   10 min, no torch import from `/api/intelligence`. Then commit per phase
   with filtered staging.

---

## 11. Addendum (same day): "distill to wiki" runs once per voice session

Stephen, verbatim: *"The distill to wiki task runs way too often. Maybe it
should be part of the dreaming routine that runs when the computer is idle?"*

### 11.1 What happens today (TREE)
- `routes/voice.py:3382` (Gemini path) and the `distill` hook of the local
  session (`routes/voice.py:1904`) call `voice_engine._spawn_voice_distill`
  at the end of **every** session.
- `_spawn_voice_distill` (`voice_engine.py:1445`) builds a prompt over that
  one session's transcript and calls `agent._spawn_task(name='Voice session:
  distill to wiki', …)`. `_spawn_task` is the general background-task path:
  it journals the task, runs a full agent call with the whole tool registry,
  and on completion the UI posts the `✅ Task complete: **Voice session:
  distill to wiki**` chat bubble (`index.html:42090`) and the tray notice —
  whether or not anything was proposed. `_summarize_task_outcome`
  (`agent.py:2705`) even synthesises a summary for the "found nothing" case.
- Voice turns are already indexed into conversation memory
  (`_persist_voice_turn` → `_index_chat_turn`), so the nightly
  `memory_dreaming.dream()` heuristic pass already sees them; the distill's
  only distinct value is the model call that can queue `propose_wiki_update`.
- The builtin "Memory dreaming" schedule (`sch_memory_dreaming`) is daily
  03:00, `notify: "silent"`, **`enabled: false`** in `~/.friday/schedules.json`
  since 2026-09-09 (last summary "Nothing to consolidate."). The scheduler has
  daily / weekly / every_minutes triggers and no idle trigger. The existing
  "runs when the machine is idle" mechanism is `work_queue`'s away-drain,
  which only drains the `heavy` class from the scheduler tick.
- The task journal is vault-encrypted, so I could not count past distill
  outcomes; the per-session firing is visible in the code path alone.

### 11.2 Design
1. **Session end becomes a write, not a model call.** `routes/voice.py`
   (both paths) calls `voice_distill.queue_session(turn_log, conversation_id)`
   which appends one JSON line to `~/.friday/voice_distill_queue.jsonl`.
   No task, no notification, no tokens. `_spawn_voice_distill` is deleted.
2. **One batch pass in the dreaming routine.** `memory_dreaming.dream()`
   gains a post-step that calls `voice_distill.drain()`:
   - reads every queued session (oldest first), joins them into one
     transcript with session headers, caps the batch at ~24k characters and
     leaves the remainder queued for the next night;
   - makes **one** agent call directly through `_generate_agent` with the tool
     list narrowed to `propose_wiki_update` and the same vault control the
     task worker uses — not through `_spawn_task`, so no task record, no
     "Task complete" bubble, no tray notice;
   - if the reply looks like a provider failure (`_looks_like_provider_failure`),
     the sessions stay queued and one WARNING goes to `friday.log`;
   - counts `propose_wiki_update` calls in the tool trace. **≥ 1:** one
     notification via `services.notifications.push` — "Voice review: N wiki
     updates are waiting for your approval" — pointing at the existing
     `/api/wiki/pending` queue. **0:** one INFO receipt line in `friday.log`
     ("voice distill: 4 sessions, nothing new") and nothing else;
   - clears the drained sessions and records the pass in the dream's
     summary so `/api/memory/dream` history shows it.
3. **"When the computer is idle."** The pass rides the existing 03:00 schedule
   (`notify: silent` already). Enabling it means flipping `enabled` on
   `sch_memory_dreaming` in Stephen's `schedules.json` — his data, so it is
   part of the go, or he flips it in the Schedules panel. An idle-triggered
   variant (drain when `work_queue.is_away()` for 15 min, at most once a day)
   needs a trigger in `scheduler.py`, which the other session owns; I will
   write it as a follow-up note, not now.
4. **A manual run still works:** `POST /api/memory/dream` already exists and
   will drain the voice queue too.

### 11.3 Files
- New `services/voice_distill.py` (~140 lines): `queue_session`, `drain`,
  `pending_count`.
- `voice_engine.py`: `_spawn_voice_distill` deleted (its callers are the two
  sites in `routes/voice.py` — mine).
- `memory_dreaming.py`: one post-step call (+ the summary line).
- `agent.py:3545` comment block referencing `_spawn_voice_distill` stays
  accurate in spirit; wording updated in the same hunk only if the file is
  otherwise untouched (it carries other sessions' hunks).
- Tests: `queue_session` appends and never calls the model; `drain` batches
  N sessions into one call, stays silent on zero proposals, notifies once on
  ≥1, re-queues on provider failure, respects the character cap.
