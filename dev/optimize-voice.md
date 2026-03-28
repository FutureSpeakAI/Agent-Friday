# Optimize Voice — Reduce Voice Pipeline Latency

## Objective
Minimize end-to-end voice latency (time from user speech to agent response)
while maintaining speech detection accuracy. The voice pipeline has multiple
tunable stages: VAD threshold, silence detection, buffer sizes, and TTS queue depth.

## Editable Surface
- src/main/voice/audio-capture.ts (TUNABLE zone only)
- src/main/voice/speech-synthesis.ts (MAX_QUEUE_DEPTH)

## Metric
Voice round-trip latency in milliseconds — lower is better.
Measure by timing the full pipeline: audio-in → transcription → LLM → TTS → audio-out.

## Loop
1. Read current TUNABLE values from audio-capture.ts
2. Run voice pipeline benchmark (or measure via Symbiont Protocol metrics)
3. Adjust ONE parameter:
   - vadThreshold: lower = more sensitive (catches quieter speech but more false positives)
   - silenceDuration: lower = faster end-of-speech detection (but may cut off mid-sentence)
   - maxBufferDuration: lower = less memory but may truncate long utterances
   - MAX_QUEUE_DEPTH: lower = less latency but may drop utterances
4. Re-run benchmark
5. If latency improved without accuracy loss: commit
6. If accuracy degraded: revert

## Constraints
- vadThreshold must stay between 0.005 and 0.05
- silenceDuration must stay between 150ms and 500ms
- maxBufferDuration must stay between 10000ms and 60000ms
- MAX_QUEUE_DEPTH must stay between 2 and 10
- sampleRate is FIXED at 16000 (Whisper requirement) — do not modify

## Budget
2 minutes per cycle, 12 cycles

## Circuit Breaker
- VAD produces > 50% false positives (detected empirically)
- Utterances are truncated mid-sentence
- TTS queue drops more than 1 utterance per 10 interactions
