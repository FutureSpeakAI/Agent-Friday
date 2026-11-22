# PC voice for a paired device (phone, tablet, TV): wire contract v1

Stephen, 2026-11-21: "PC voice cannot speak on my tablet, and likely not on my phone either. ... local voice mode on
the PC should also work." Agreed with the phone lane on the LEDGER, 2026-11-22. Code: `tv_gateway/voice_bridge.py`;
tests: `tests/unit/test_tv_voice_bridge.py`.

| Door | Who | What |
|---|---|---|
| `wss /tv/v1/voice/session` | the owner's own phone or tablet (a personal device bound to the owner) | a live voice session with the PC, in whatever engine the PC's voice setting selects |
| `POST /tv/v1/tts` `{text, voice?}` | any paired device, the TV included | the text spoken in the PC's selected voice, as `audio/wav` |

Both sit on the gateway's pinned TLS (LAN listener or tailnet listener) behind the device-session gate:
`Authorization: Friday-Device <session>`; `X-Friday-Principal` is accepted and ignored (owner-only door). Refused as
HTTP before the upgrade: `401 session_required`, `403 owner_only`, `400 bad_request` (a bad `?conversation_id=`).
`?conversation_id=<cid>` binds the turns to that chat (default: the owner's Main).

## How it works

The gateway does not re-implement the voice pipeline. After the upgrade it opens a loopback WebSocket to the PC's own
session route for the selected engine and translates frames both ways:

| PC voice setting | Route the PC runs | Where the audio goes |
|---|---|---|
| Local | `/ws/voice-local` | stays on the PC: ear (Nemotron or faster-whisper), the 1.7B front, Bonsai2, mouth (Kokoro or Piper) |
| Gemini Live | `/ws/live` | to Google through the PC's key, which stays on the PC |
| OpenAI Realtime | `/ws/openai-live` | to OpenAI through the PC's key |
| none ready | refused: `error no_engine`, then `closed` | nothing |

Tools, the governance gate and approval cards are exactly the desktop's. A device is not the owner's desk voice (no
desk key), so a SPOKEN yes from a device never approves a card; answer cards through the approval door.

## Device to PC

Binary frame: PCM16 mono 16 kHz, 20 to 100 ms, at most 64 KB (more ends the session). Text frames (JSON, at most 4 KB):

```
{"type":"start","engine":"pc|local|gemini_live|openai","voice":"?"}   optional, advisory (below)
{"type":"press"}      the turn starts; while she is speaking it is a barge-in
{"type":"release"}    the turn ends: the PC appends about 1 s of silence so the engine's endpointer closes it
{"type":"barge_in"}   her audio stops at once and your turn starts; the PC answers {"type":"speaking","who":"you"}
{"type":"stop"}       end the session and release the PC's voice stages
{"type":"ping"}       keeps the session alive; answered {"type":"pong"}
```

`start.engine` is advice: the PC runs whatever ITS setting selects. Naming a different engine gets
`error engine_not_selected` and the session closes; the PC never switches (that is `POST /tv/v1/voice/switch` plus the
owner's question on the PC). `voice` is reserved. Anything else (images, setup, malformed JSON, `phone_action`) is
dropped. Push-to-talk is expected: keep the device's own VAD off.

## PC to device

Binary frame: PCM16 mono 24 kHz in clauses; play in order. Text frames:

```
{"type":"engine","engine":"local|gemini|openai","where":"local|cloud|none","local":true,"label":"...",
 "ear":"?","mind":"?","mouth":"?","voice":"?"}      the who-spoke badge: at open, again when the local stages are known, in every turn_done
{"type":"speaking","who":"you|friday"}              'you' when the PC starts hearing you; 'friday' at her first audio
{"type":"transcript","who":"you|friday","text":"...","final":false}   your words (partials, then final) and hers (final when complete)
{"type":"turn_done","engine":{...}}
{"type":"status","text":"Warming up the voice - about 20 s"}   local cold start is 25-35 s: show it, do not time out before 60 s
{"type":"error","code":"no_engine|start_failed|engine_not_selected|unauthorized|voice_error","words":"..."}   say words as written
{"type":"closed","reason":"...","code":"stopped|idle|time_limit|replaced|frame_too_large|engine_not_selected|no_engine|start_failed|upstream_ended|device_gone"}
```

The desktop's own panel frames (`manifest`, `contract`, `context_reach`, `stage`, receipts) and raw error text are never
forwarded. One session per device (a second replaces the first); 120 s with no frame from the device and a 30 minute
limit close the session. No server-side reconnect.

### The who-spoke badge

`engine` plus `where` (`local`: the audio never left the PC; `cloud`: it left through the PC's key) is what a device
shows next to the answer. For local voice `ear`, `mind` and `mouth` name what served the turn, for example
`faster-whisper@cuda`, `Ternary Bonsai 1.7B@gpu`, `kokoro@cuda`. `POST /tv/v1/tts` carries the same two facts as the
headers `X-Friday-Voice-Engine` and `X-Friday-Voice-Where`.

### Phone actions (v2, reserved)

The PC's voice session has no phone tools today, so v1 never sends `phone_action`. The name `{"type":"phone_action",
"verb","args"}` is reserved: when it arrives (v2) the phone gates, taints and cards it; the PC never executes on the
phone. Ignore unknown frame types.

## `POST /tv/v1/tts`

Request `{"text": "...", "voice": "optional"}`, at most 2000 characters after markdown is removed (links become "the
link", code fences are skipped). Response `200 audio/wav` (mono 16-bit; 24 kHz). Refusals are coded JSON:
`400 empty`, `413 too_long`, `503 no_engine` (no voice can speak text now; OpenAI Realtime included),
`503 voice_unavailable` (the engine failed), `401 session_required`.

## Latency

The bridge adds one loopback hop and no buffering. The bar is the desktop's: first audio within 0.8 s p50 and 2.0 s p95
after the end of speech (the desktop's measured local numbers, 2026-11-21, are on the LEDGER). The real-socket test
asserts the bridge's own contribution is far under that bar.

## Not here

No camera or image frames. No key ever reaches a device. A TV's dictation stays on `POST /tv/v1/stt`.
