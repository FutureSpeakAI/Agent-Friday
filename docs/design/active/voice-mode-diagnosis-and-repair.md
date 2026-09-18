# Voice mode: full diagnosis and repair spec

Written 2026-09-10 from measurements on Stephen's machine (RTX 4070 12GB,
Windows 11, `~/.friday/friday.log`). Every number below was observed, not
estimated. Implementer: keep it that way — if a claim here cannot be
reproduced, fix the claim rather than coding to it.

## The two paths, and what each actually is

| | local (`/ws/voice-local`) | cloud (`/ws/live`) |
|---|---|---|
| brain | `_generate_agent` — the same agentic pipeline a typed chat turn uses | Gemini Live, speech-to-speech |
| tools | full core surface (~75 before trimming) | **16**, fixed declarations |
| knowledge graph | `knowledge_query` available | **absent** |
| ASR | faster-whisper (cpu tier) / Nemotron streaming (gpu tier) | Gemini |
| TTS | Piper (cpu) / NeMo FastPitch+HiFi-GAN or Kokoro (gpu) | Gemini |

Stephen's standing requirement (2026-09-10): "local voice always needs to
involve a model in the loop that can call tools and do research into the
knowledge graph." The local path satisfies this. The cloud path structurally
cannot: its 16 declarations are `check_email`, `get_article_deep_dive`,
`get_source_trust`, `navigate_workspace`, `open_path`, `open_url`,
`query_calendar`, `read_file`, `screenshot`, `search_email`, `search_files`,
`search_news`, `search_web`, `search_wiki`, `spawn_task`, `write_file`.

## Findings

### F1 — NeMo re-measures VRAM 12 times a minute, forever, on every engine

`services/nemo_voice.py` logs "VRAM measurement disputed" at a steady **12
events per minute**, rising to **47 in the minute of a live voice session**
(09:32). Each event runs a torch CUDA memory query *and* shells out to
`nvidia-smi`. This happens while the **cloud** engine is selected, when NeMo
is not in the loop at all.

This is the leading suspect for the skippy audio Stephen reported on Gemini
Live: process spawns and CUDA driver calls on the same thread budget as audio
streaming. It is also why the log is unreadable — 12 identical warnings a
minute drown everything else.

**Fix:** cache the reading with a TTL (30s is ample for an admission check),
suppress the log line unless the verdict *changes*, and do not probe at all
unless the active engine is a local GPU tier. Acceptance: a five-minute idle
cloud session produces zero NeMo VRAM lines; a local GPU session produces at
most two per minute.

### F2 — the disputed reading is resolved the optimistic way

Every one of those events reads `torch=11.5GB nvidia-smi=6.7GB gap=4.8GB`.
torch reports what it *could* obtain by making the driver page other work out;
nvidia-smi reports what is genuinely unused. Admission has been granted on the
torch figure. That is how the display got starved to 448 MiB against a 2,560
MiB reserve at 08:38, which is what killed the holographic scene.

**Fix:** admission uses the **conservative** figure (`nvidia-smi`) for any
decision that could starve the display. Keep reporting both — the disagreement
is real information — but never admit on the larger number. Acceptance: a test
that feeds torch=11.5/nvidia-smi=2.0 and asserts refusal.

### F3 — cloud voice cannot reach Stephen's context, and does not say so

"It did successfully triage my emails and calendar, but then... utterly failing
to reach my context." Both halves are explained by the 16-tool table above:
email and calendar are in it, the knowledge graph and memory are not.

**Fix:** when the selected voice engine cannot put a tool-capable,
KG-reaching model in the loop, say so at session start — one line in the
session-info payload and one line the UI shows. Do not silently run the
degraded loop. This is `feedback-transparency` applied to voice: fail visibly
and offer, never substitute quietly.

### F4 — "tier: cpu" with Kokoro selected reads as a contradiction, and nothing explains it

`local_voice_tts_engine = "kokoro"`, `local_voice_kokoro_allow_cpu = false`,
and `/api/voice/session-info` resolves `tier: cpu`.

**Correction (2026-09-10, implementation pass).** The original text said
Kokoro refuses here and Piper serves. That did not reproduce. The `tier` is
NeMo *ASR's* tier (`resolve_tier` asks `gpu_tier_ready`, which needs 4 GB for
the 0.6B RNN-T); Kokoro decides its own device in `KokoroTTS._resolve_device`
from `torch.cuda.is_available()` alone. `friday.log` 2026-09-10 09:06:28:
`tier1 tts engine=kokoro voice=af_heart` then `kokoro load voice=af_heart
device=cuda`. Kokoro serves on the GPU while the tier reads "cpu"; nothing
was substituted. Kokoro itself works: measured 2.9x realtime on a short line
and 10.3x on a sentence, 24kHz, through Friday's own `KokoroTTS`.

What remains true: the settings panel showed a selection and a tier that
looked contradictory, "Serving now" was null until something loaded, and
when Kokoro *cannot* run (no CUDA, no CPU opt-in) the session refuses and
offers Piper — a refusal the user only meets at the mic click.

**Fix:** the Voice settings panel must show the *effective* engine beside the
*selected* one whenever they differ, with the reason. The `TtsEngineRows`
component already has a "Serving now" row — it is reading `/api/health/full`'s
`local_voice.running`, which is null until something loads. Make it also
report the resolved tier before a session starts.

**Built:** `LocalVoiceEngine.effective_tts()` mirrors the loader's decisions
(importable? CUDA? CPU opt-in?) and `peek_tier()` resolves the tier with no
side effects (`resolve_tier` logs a degrade warning every call, which a
20-second health poll must not trigger). `health()` carries
`effective_tts`, `resolved_tier`, `tier_reason`; `TtsEngineRows` renders
"Effective: kokoro on cuda" / "the session will refuse and offer Piper —
<reason>" under the engine buttons, and "Serving now" names the tier and
engine a session started now would get.

### F5 — cold model load is 40–56 seconds, paid on the first utterance

Measured: 39.0s, 48.6s, 55.9s across three cold loads of `KokoroTTS`.
Synthesis after that is fast. This is the "very, very slow" Stephen reported.

**Fix:** warm the selected local TTS engine when the Voice settings panel is
opened or when voice mode is armed, not on the first spoken turn. Show a
determinate "loading voice engine" state. Acceptance: first utterance after
arming begins speaking in under 3 seconds.

### F6 — mic audio egress is logged but not surfaced

`egress ALLOW provider=google-gemini field=mic_audio tier=UNCLASSIFIABLE
bytes=7294560 (live voice session closed)`. 7.3 MB of Stephen's microphone
audio went to Google in one session. The gate allowed it correctly and the
receipt exists, but nothing told him at the time.

**Fix:** cloud voice sessions show a persistent indicator naming the provider
receiving the audio, and the session-end receipt reports the byte count.
`services/voice_indicator.py` exists for this and is not wired.

## Implementation status (2026-09-10)

| | status | where |
|---|---|---|
| F1 | built | `nemo_voice.gpu_status()` caches the probe (30 s while a local GPU consumer — NeMo or Kokoro — is selected, 300 s otherwise); the dispute line is a WARNING only when the admission verdict changes, DEBUG otherwise. `fresh=True` bypasses the cache and is passed by the two admission points (`ensure_ready`'s pre-import gate, `native_audio.check_vram_admission`). |
| F2 | built | `sufficient` is nvidia-smi's verdict whenever nvidia-smi answered; torch's is kept as `sufficient_reachable`. Test: torch=11.5 / nvidia-smi=2.0 refuses. |
| F3 | built | `_voice_context_reach()` from the resolved tool names; in `/api/voice/session-info` as `context_reach`, and sent as `{"type":"context_reach"}` by both WS handlers at session start. The client keeps it on screen under the LIVE line. |
| F5 | built | `POST /api/voice/warm` loads the selected local engine on a thread (single-flight; no-op for cloud, already-loaded, or models-not-downloaded); `GET` reports state/progress/elapsed. `TtsEngineRows` posts it on mount and shows "Loading voice engine… (Ns)". |
| F4 | built, claim corrected | see F4 above. |
| F6 | built | `voice_indicator.record_cloud_session(start/open/close)`; `/ws/live` sends `egress_notice` at leg open ("☁ MIC → Google (Gemini Live)" stays on screen) and `egress_receipt` at leg close with the cumulative byte count, shown after the session ends. |

Deviation from F1's text: "do not probe at all unless the active engine is a
local GPU tier" became "probe at most once per five minutes" in that case,
so the settings panel and the GPU-tier setup step stay truthful. On an idle
cloud session that is one `nvidia-smi` spawn per five minutes and zero log
lines, which meets the acceptance criterion.

Not yet verified live: every Python change waits on a Friday restart, and
F5's "first utterance under 3 s" needs a session on the real GPU after that
restart.

## Order of work

1. F1 (the lag — cache, gate by engine, stop the log flood)
2. F2 (conservative admission — one-line change plus a test)
3. F3 (say when the engine cannot reach tools/KG)
4. F5 (pre-warm)
5. F4 (selected vs effective in the UI)
6. F6 (egress indicator)

## Hazards — read before touching anything

- **Do not restart or disturb the `llama-server.exe` on port 8095.** It serves
  FridayWeaver-1.0 and was started by hand. Port 8095 is inside the Arbiter's
  8090–8130 adoption window and must stay there.
- **Never broad-kill `python.exe` or `pythonw.exe`.** Friday's tray and server
  are both Python. Stop things by PID only.
- **Do not restart WSL.** `llama-server` mmaps its GGUF over `\\wsl.localhost`.
- Python changes need a Friday restart to take effect; there are already four
  queued fixes waiting on one. Coordinate rather than bouncing it mid-test.
- `ui_parts/app.html` carries a note saying it is a hand-maintained mirror that
  nothing builds from; `index.html` is what the server serves. Verify which
  file is authoritative before editing UI code, and do not "sync" the mirror.
