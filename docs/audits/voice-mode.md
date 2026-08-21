# Voice mode — diagnosis and fix (2026-08-21)

**Investigator:** Claude (Cowork session)
**Evidence captured:** 2026-08-21 10:11–10:35 local
**Machine:** VADERSCASTLE · server PID 30624, started 08:58:18, uptime healthy throughout
**Tree:** `friday-desktop` @ `higgsfield-integration`
**Git:** nothing committed, staged, unstaged, or `git add`ed. The seven staged files were
verified untouched before and after. **No source file was edited.** The server was **not**
restarted — no restart was needed.

---

## Bottom line

**Voice mode was not broken. Friday was running the wrong voice architecture.**

`docs/design/elevenlabs-voice.md` §6.2 records the decision: Friday's speaking voice is
**Gemini Flash Live native audio**, speech-to-speech, one model. She was not on it. The
server was routing every voice session to the **local Tier-1 cascade** (`/ws/voice-local`:
faster-whisper → the agent brain → Piper), and that cascade **hangs indefinitely** on this
machine tonight: it transcribes you, emits `status: thinking`, and then never speaks, never
errors, and never times out.

From Stephen's chair that is exactly "voice mode isn't working": you talk, the orb reacts,
and nothing ever comes back.

The Gemini Live path was healthy the entire time and had been all along. Two settings
values — both user-facing knobs the code already provides — were pointing away from it.

**Fixed by settings change only, verified live:**

| Setting | Was | Now |
|---|---|---|
| `voice_engine` | `auto` | `gemini` |
| `voice_model` | `gemini-3.1-flash-live-preview` | `gemini-2.5-flash-native-audio-latest` |

Applied via `POST /api/settings`. Backup at `~/.friday/settings.json.bak-voicefix-20260821-102514`.
Picked up without a restart (settings are re-read per session).

---

## 1. Establishing the symptom (before any theorising)

Stephen reported only "voice mode isn't working," so I drove the audio path directly rather
than reading code. Harness: `ws_live_probe.py` — a WebSocket client that speaks the same
contract as the browser (`{type:'audio', data:<b64 PCM16@16k>}`), fed with **real speech**
synthesised through Windows SAPI at 16 kHz/16-bit/mono (6.58 s, 210,560 bytes):

> "Hello Friday. Please say the word banana out loud, and then tell me what day it is."

### 1.1 The server-side Gemini path — healthy, first try

`ws://localhost:3000/ws/live`:

```
 2.02s connected
 2.07s status: loading context
 3.93s status: live
21.87s INPUT_TRANSCRIPT: 'Hello Friday. Please say the word banana out loud.
                          And then tell me what day it is.'
21.88s TEXT: 'Banana. And today is Friday, August 21st, 2026. Is there anything
              in your briefing or calendar you want to dive into?'
21.88s AUDIO #1 … #25  → 279,842 bytes  (~5.8 s of PCM16 @ 24 kHz)
```

Audio in, correct transcript, correct answer, audio out. **The thing reported as broken
worked on the first attempt.** That reframed the whole investigation.

### 1.2 What the browser is actually told to use

```
GET /api/voice/session-info
{"engine":"local","label":"Local (private, on-device)","models_ready":true,
 "reason":"local default","tier":"cpu","ws_url":"/ws/voice-local"}
```

The browser was never connecting to `/ws/live`. It was being sent to the local cascade.

### 1.3 The local cascade — reproduced hanging, twice

`ws://localhost:3000/ws/voice-local`, same audio, same contract:

```
 2.05s status: starting local voice
 2.05s status: live
21.91s INPUT_TRANSCRIPT: 'Hello Friday.'
21.91s status: thinking
       … nothing. Ever.
```

Run 1: 90 s window — silence. Run 2: 240 s window — silence. No `text`, no `audio`, no
`turn_end`, no `error`. ASR works (it transcribed real speech). The turn then disappears.

Two aggravating details, both visible in that trace:

- **The VAD segmented at the first pause and only the first fragment was ever handled.**
  "Hello Friday." made it; the actual question did not. `_handle_turn` runs **inline in the
  receive loop** (`routes/voice.py:1020`), so while the brain blocks, the socket stops being
  drained and stops heartbeating.
- **There is no timeout and no failure path for "the brain never returned."**
  `routes/voice.py:951–959` catches exceptions; a hang is not an exception. Nothing is ever
  sent. This is the `DEVNULL` failure mode from `server-death-forensics.md` wearing a
  different coat — degradation that costs the user nothing visible and therefore goes
  uninvestigated.

### 1.4 Client-side prerequisites — all fine, ruled out early

Measured in the page at `http://localhost:3000`:

```
mic_probe        : GRANTED  tracks=Microphone (EMEET SmartCam C960) (328f:0121)
audioInputs      : EMEET SmartCam C960 (+ Default / Communications aliases)
secureContext    : true
hasWorklet       : true
```

Microphone permission, hardware, secure context and `AudioWorklet` were never the problem.

---

## 2. Root cause

### 2.1 The resolver: `"auto"` does not mean auto

`routes/voice.py:495 _resolve_voice_engine` reads `settings.voice_engine ∈ {local, gemini, auto}`:

```python
# Default + auto both prefer local (the ethos).
if local_ok:
    return {**_pick("local"), "reason": "local default"}
```

`"auto"` and `"local"` are the same branch. Stephen's setting was **`auto`** — a value that
reads as "pick whichever works" and behaves as "always local, regardless." There is no
signal anywhere that the choice was collapsed.

### 2.2 The check vouches for two thirds of the pipeline

```python
eng        = get_local_voice_engine()
local_ok   = eng.available()        # faster-whisper + piper deps
models_ready = eng.models_ready()   # ASR/TTS model files on disk
```

The local voice path needs **three** things: ASR, **an LLM brain**, and TTS. `local_ok` asks
about ASR and TTS only. It never asks whether a turn can actually complete. So the resolver
certified the local engine as ready, on the strength of two components, while the third was
failing — and routed every session into it.

**This is the same defect shape as the other three tonight**: a gate reasoning about the
*form* of the thing (are the packages installed?) rather than its *meaning* (can this
pipeline answer a question?). `/api/voice/setup/status` reports `"ready": true` for the same
reason. Suspect the check before the work — that was the right order again.

### 2.3 What the brain is doing

Corroborated in `~/.friday/friday.log` and in the live UI:

```
WARNING friday.local_call — local_call HTTP 404 from gemma4:e2b:
        {"error":"model 'gemma4:e2b' not found"}
```

`ollama list` has no `gemma4:e2b` and no `gemma4:12b`. The real artefacts are
`hf.co/HauhauCS/Gemma-4-E4B-Uncensored-…` and `hf.co/HauhauCS/Gemma4-12B-QAT-…`. The UI
shows the seat binding flapping between the two names:

```
⚙ Seat change: orchestrator seat hf.co/HauhauCS/Gemma-4-E4B-…:Q4_K_M → gemma4:e2b.
⚙ Seat change: orchestrator seat gemma4:e2b → hf.co/HauhauCS/Gemma-4-E4B-…:Q4_K_M.
```

Text chat survives this (`POST /api/chat` → `"banana"`, 200, **16.9 s**) because it falls
back to Anthropic. Voice does not survive it — and the fallback is where the second bite comes:

```
WARNING friday.egress — BLOCK provider=anthropic field=tool.description tier=TIER_2 (sensitive-tool-desc)   ×3
WARNING friday.egress — BLOCK provider=anthropic field=system tier=TIER_3 (withheld 8/150 paragraphs)
```

The cloud fallback is handed tools whose **descriptions have been withheld**. A model given
tools it cannot read is a model that will loop.

### 2.4 The sensitivity force-route, caught in the act on a voice path

Stephen flagged this as a maybe. It is not a maybe. From the live UI, immediately after a
voice session ended:

> **Task complete: Voice session: distill to wiki**
> *"This request touches vault-protected data, so it was only tried on the local model —
> which failed (local (gemma4:e2b): HTTP Error 404 …). It was NOT sent to a cloud provider."*

`_spawn_voice_distill` fires at the end of **every** voice session. The classifier force-routes
it to a local seat that does not exist, so it 404s every time. Voice transcripts do pass
through the classifier, and this is the proof.

### 2.5 The model was also wrong on the Gemini path

`voice_model` was `gemini-3.1-flash-live-preview`. Per `voice_engine.py:870–883` that is a
**standard** Live model, not native audio — `_model_supports_affective_dialog()` returns
`False` for it, so affective dialog and proactive audio were both off and the v1alpha attempt
was never made. It is `LIVE_MODEL_FALLBACK2`, the last rung of the degradation ladder, pinned
into settings as if it were the choice. The decided architecture is `LIVE_MODEL` —
`gemini-2.5-flash-native-audio-latest`.

Note the self-healing in `_resolve_voice_engine:512` that would have corrected a stale model
**only runs when `pref == "gemini"`**. Under `auto` it never ran. Two defects covering for
each other.

---

## 3. The fix, and why this one

Two settings values, no code. Both are knobs the code already exposes; both now match the
decided architecture in `docs/design/elevenlabs-voice.md` §6.2.

I deliberately did **not** rewrite `_resolve_voice_engine`. The `"auto"`-means-local
behaviour is a *product* stance ("LOCAL is the default, cloud is the opt-in") that sits in
direct tension with §6.2 ("Friday's voice — decided: no change, she stays on Gemini Flash
Live"). Those two documents disagree, and resolving that disagreement by unilaterally
flipping the ethos in a route file, mid-commit-sequence, is not mine to do. It is flagged in
§5 instead.

I also did not touch the brain / seat-alias problem: it lives in `services/model_router.py`,
which is **staged**.

---

## 4. Verification — live, and what it does and does not cover

### 4.1 Server → Gemini, on the native-audio model (after the fix)

Same probe, same speech, `/ws/live`:

```
 3.93s status: live
 5.53s TEXT: "Hey boss, Friday's here."          ← unprompted: proactive audio is on
 5.97s AUDIO #1 46,080B …
21.87s interrupted                                ← barge-in fired when I spoke over her
21.9s  INPUT_TRANSCRIPT streamed incrementally, word by word
23.89s TEXT: 'Banana! Today is Friday.'
       AUDIO → 136,320 B
26.91s voice_turn_done / turn_end
```

Proactive audio, incremental input transcription and barge-in all appear **only after** the
model change — direct confirmation that `gemini-3.1-flash-live-preview` had been costing
real capability, not just nominal compliance.

### 4.2 Browser playback — measured, not assumed

In the unpatched page, decoding the server's PCM16@24k and rendering it through
`AudioContext.destination` with an `AnalyserNode` on the output:

```
ws_url "/ws/live"  engine "gemini"
audio_frames 48 · audio_bytes 136,320 · audio_seconds 2.84
analyser_peak_amplitude 128        ← full scale; real signal reached the output device
model_text "browser playback check, one two three."
```

### 4.3 The full acoustic loop, through the real microphone

The strongest test available without a person in the room: with voice mode started from the
**real mic button in the unpatched UI**, I played the speech WAV out of the machine's
speakers so the EMEET microphone would hear it.

The app's own mic meter, room noise → playback → back to room noise:

```
10:32:53  peak=0.0195   (silence)
10:32:57  peak=0.1920
10:33:00  peak=0.3173   (my audio, through the air)
10:33:03  recent=0.0092 (silence again)
```

And the resulting exchange, in Friday's transcript, both turns tagged **VOICE**:

| Time | Speaker | Text |
|---|---|---|
| 10:33:05 | You · VOICE | "Hello friend." |
| 10:33:05 | Friday · VOICE | "Hey. Still Friday, boss." |
| 10:33:12 | You · VOICE | "Please say the word outline." |
| 10:33:12 | Friday · VOICE | "Banana. What's up, boss?" |

Speaker → air → physical microphone → browser capture → server → Gemini Live → reply. The
loop closes. (Transcription is imperfect — "Hello friend", "the word outline" — which is what
a robotic SAPI voice degraded across a room sounds like to an ASR, not a defect in the path.)

### 4.4 Friday serves in a browser

Confirmed **visually**, not by port: Chrome on `localhost:3000` renders the live UI — AGENT
FRIDAY header, GENESIS LATTICE, green **LIVE**, ticking clock, populated chat rail with the
voice turns above. `/api/health` 200, `/api/startup-report` `{"status":"ok",
"blueprint_count":62,"skipped":[],"degraded_capabilities":[]}` — nothing skipped or degraded,
and nothing audio-related in the blueprint policy. The server was never restarted.

### 4.5 What I could **not** verify

Stated plainly:

1. **I never spoke into the microphone myself.** §4.3 is a loudspeaker played into the room
   mic. It exercises every link, but a human voice at conversational distance is not tested.
   If Stephen's mic gain, positioning or noise floor is the issue, this would not have caught it.
2. **I did not put a meter on the app's own `friday-pcm-player` AudioWorklet during §4.3.**
   That her reply came *out of the speakers* in that specific run is inferred from §4.2
   (which measured the identical decode-and-render path with a real signal) plus the reply
   appearing in the transcript. I could not hear the machine.
3. **One-machine, one-session sample.** I did not test session resumption (>10–15 min turns),
   reconnect-after-sleep, the `no-barge` interruption mode, or camera/image frames.
4. **The local cascade is left broken.** I established *that* it hangs and *where*
   (`_handle_turn` → `_generate_agent` never returns); I did not isolate the exact blocking
   call inside the agent loop. If the local engine is ever selected again, it will hang again.
5. **Two orphaned server threads.** My two hung `/ws/voice-local` probes are presumably still
   blocked in `_generate_agent` inside the server process. They are harmless (a fresh
   `/ws/live` session served correctly alongside them) but they will not clear without a
   restart. I did not restart, since nothing required it.
6. **One earlier browser observation is void.** A mid-investigation run where the app appeared
   to receive nothing from the server was my own `window.WebSocket` monkey-patch clobbering
   the non-enumerable `WebSocket.OPEN` constant, not a product defect. A clean reload behaved
   correctly. Recording it because it would otherwise look like a real finding in the logs.

---

## 5. Open items — for Stephen to sequence, not for me to land

Ordered by how much they cost when they next fire.

1. **`"auto"` must either mean auto or stop being offered.** Today it silently means "local".
   Either make it probe cloud when local cannot complete a turn, or remove the option. The
   present behaviour is a setting that lies to the person who chose it.
   *(`routes/voice.py` — clean in the working tree, no competing edits.)*

2. **A voice turn must not be able to hang silently.** Bound `_generate_agent` in
   `_handle_turn`, and on expiry send an `error` frame the user can hear. Silence is the one
   failure mode audio cannot express. Same principle as `server_stderr.log`: *degradation must
   cost something visible.*

3. **`local_ok` should mean "can complete a turn," not "deps are installed."** As written it
   certifies ASR+TTS and stays silent about the brain. That is what routed Stephen into a dead
   pipeline. `/api/voice/setup/status` inherits the same optimism and reports `"ready": true`.

4. **The `gemma4:e2b` / `gemma4:12b` aliases resolve to nothing in Ollama.** Seat bindings flap
   between the alias and the real `hf.co/HauhauCS/…` id; every force-routed local call 404s.
   Touches `services/model_router.py` — **staged, not mine to edit.**

5. **`_spawn_voice_distill` fails on every voice session** via the sensitivity force-route
   (§2.4). Every conversation is currently lost to the wiki, silently, and it announces this
   as *"Task complete."*

6. **The egress gate withholds `tool.description` from the cloud fallback.** A model handed
   unreadable tools is set up to loop. Fourth instance tonight of a check firing on the shape
   of text rather than its meaning.

7. **`voice_model` drifting to `LIVE_MODEL_FALLBACK2` and sticking.** The auto-correction at
   `_resolve_voice_engine:512` only runs under `pref == "gemini"`, so a degraded pick becomes
   permanent under `auto`. Whatever wrote that value should be found; a fallback that persists
   itself into settings is a ratchet.

8. **`docs/design/elevenlabs-voice.md` §6.2 and the resolver's stated ethos contradict each
   other.** §6.2: Friday's voice is Gemini Flash Live, decided. `routes/voice.py:497`: local is
   the default, cloud is the opt-in. Both are load-bearing and they cannot both be right. Worth
   one sentence in the design doc settling it, because this contradiction is what the fix in §3
   had to step around.

---

## 6. Artefacts

| Path | What |
|---|---|
| `~/.friday/settings.json.bak-voicefix-20260821-102514` | pre-fix settings backup |
| `%TEMP%\voicediag\ws_live_probe.py` | the WebSocket audio probe |
| `%TEMP%\voicediag\probe.wav` | SAPI speech, PCM16 16 kHz mono, 6.58 s |
| `%TEMP%\voicediag\*.out` | raw probe transcripts for every run quoted above |

`_voicediag_probe.wav` was placed in `~/.friday/audio-cache/` during testing and has been
removed. No other file on the machine was created or modified outside `%TEMP%`, except the two
settings values in §3.
