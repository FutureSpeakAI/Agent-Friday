# Local Voice Conversation Flow (Whisper + Ollama + TTS)

## Quick Reference

| Property | Value |
|----------|-------|
| **Entry Point** | `window.eve.localConversation.start(systemPrompt, tools, initialPrompt)` |
| **Orchestrator** | `src/main/local-conversation.ts` (class `LocalConversation`) |
| **Process** | Electron main process (all components are main-process singletons) |
| **STT Engine** | Whisper.cpp via `whisper-binding` native addon |
| **LLM Engine** | Ollama (localhost HTTP API) |
| **TTS Engine** | Kokoro or Piper (ONNX models via `tts-binding` native addon) |
| **Mic Format** | 16kHz mono Float32 PCM |
| **TTS Output** | 24kHz mono Float32 PCM |
| **VAD Threshold** | 0.01 RMS energy |
| **VAD Silence Duration** | 300ms before voice-end |
| **VAD Lookback** | 2 chunks (prevents clipping speech onset) |
| **LLM Timeout** | 90 seconds per completion |
| **Max Tool Iterations** | 5 per turn |
| **Tool Timeout** | 15 seconds per individual tool call |
| **Graceful Degradation** | Full voice -> Text+TTS -> Text-only |

## Degradation Modes

The local conversation degrades gracefully based on component availability.
Only Ollama is a hard requirement -- Whisper and TTS are optional.

| Mode | Components | User Experience |
|------|-----------|-----------------|
| **Full Voice** | Whisper + Ollama + TTS | Speak and hear responses |
| **Text + TTS** | Ollama + TTS | Type input, hear spoken responses |
| **Text-only** | Ollama only | Type input, read text responses |

## Flow Phases

### 1. Initialization

When `start()` is called, the following steps execute sequentially:

1. **Verify Ollama** (required): Fresh HTTP health check via `ollamaProvider.checkHealth()`.
   Bypasses the cached `isAvailable()` to avoid stale state. Aborts entirely if Ollama is unreachable.

2. **Load Whisper** (optional): Calls `whisperProvider.loadModel()` to load the `ggml-tiny.bin`
   (or configured size) model into memory. If loading fails, sets `voiceAvailable = false`
   and continues in text-input mode. Emits an error event so the renderer can notify the user.

3. **Load TTS** (optional): Calls `ttsEngine.loadEngine()` which auto-detects the backend
   (Kokoro preferred, Piper fallback) and loads ONNX models from `~/.nexus-os/models/tts/`.
   If loading fails, sets `ttsAvailable = false` and continues with text output only.

4. **Initialize conversation state**: Resets message history, sets system prompt and tools.

5. **Start TranscriptionPipeline**: Only if Whisper loaded successfully. Subscribes to
   `transcript` and `error` events. Calls `transcriptionPipeline.start()` which in turn
   starts `AudioCapture` IPC coordination with the renderer.

6. **Emit `started`**: The renderer receives this event and dispatches `gemini-audio-active`
   to show the active call UI.

7. **Send initial prompt**: If `initialPrompt` was provided (e.g., during onboarding),
   it is immediately processed as the first user turn.

### 2. Audio Capture and VAD

Audio capture is coordinated between the renderer and main process via IPC:

1. **Renderer**: Calls `getUserMedia()` to access the microphone.
2. **Renderer**: Sends audio chunks to the main process via IPC (`audio-capture:chunk`).
3. **Main Process**: `AudioCapture` singleton (`src/main/voice/audio-capture.ts`) receives chunks.

#### VAD (Voice Activity Detection)

`AudioCapture` uses energy-based VAD with the following parameters:

| Parameter | Value | Description |
|-----------|-------|-------------|
| `vadThreshold` | 0.01 | RMS energy threshold for speech detection |
| `silenceDuration` | 300ms | Silence duration before `voice-end` fires |
| `maxBufferDuration` | 30,000ms | Maximum single utterance length |
| `LOOKBACK_CHUNKS` | 2 | Pre-speech chunks prepended to buffer |

**VAD Flow:**

1. Each incoming chunk has its RMS energy computed.
2. If RMS >= `vadThreshold` and not currently in speech: emit `voice-start`, begin buffering.
   The 2-chunk lookback buffer is prepended so the very beginning of speech is not clipped.
3. While in speech: accumulate chunks into `speechBuffer`.
4. If RMS < `vadThreshold`: start silence timer.
5. If silence exceeds 300ms: emit `voice-end` with the accumulated audio buffer.
6. If buffer exceeds `maxBufferDuration` (30s): force `voice-end` to prevent unbounded growth.

### 3. Whisper STT

The `TranscriptionPipeline` (`src/main/voice/transcription-pipeline.ts`) orchestrates
AudioCapture to WhisperProvider:

1. Subscribes to `AudioCapture` events: `voice-start`, `voice-end`, `audio-chunk`.
2. On `voice-end`: concatenates the speech buffer into a single Float32Array.
3. Queues the audio for transcription (internal serialization queue prevents concurrent Whisper calls).
4. `WhisperProvider` (`src/main/voice/whisper-provider.ts`) transcribes the audio:
   - Input: Float32Array at 16kHz mono
   - Output: `TranscriptionResult` with text, language, segments (with timestamps), duration, processing time
5. Emits `transcript` event with the result. `LocalConversation` receives this.
6. For long utterances (> 2s), partial transcription is attempted every 2 seconds.

### 4. Ollama LLM Inference

When a transcript arrives (or text is sent via `sendText()`):

1. **Barge-in check**: If TTS is currently speaking, `speechSynthesis.stop()` is called immediately.
   The user's new input takes priority over the agent's current response.

2. **Queue serialization**: If a previous turn is still processing, the input is pushed to
   `pendingInputs[]` and processed when the current turn completes. Inputs are never dropped.

3. **Message assembly**: The user text is appended to the conversation `messages[]` array.

4. **LLM completion**: `llmClient.complete()` is called with:
   - Provider: `'ollama'`
   - System prompt and tools from the `start()` configuration
   - Max tokens: 2048
   - Temperature: 0.7
   - Timeout: 90 seconds (`AbortSignal.timeout(90_000)`)

5. **Tool loop**: If the response contains `toolCalls`, they are executed in a loop:
   - Max 5 iterations to prevent infinite tool loops.
   - Each tool call has a 15-second timeout.
   - Tool results are appended as `role: 'tool'` messages.
   - LLM is re-invoked with the updated message history.
   - Tool categories: onboarding tools, feature setup tools, desktop tools, MCP tools.

6. **Response emission**: The final text response is emitted as `ai-response` for UI display.

7. **Queue drain**: After processing completes, if `pendingInputs[]` is non-empty,
   the next queued input is dequeued and processed. This ensures no user speech is lost.

### 5. TTS Synthesis

If TTS is available, the response text is spoken via the `SpeechSynthesisManager`:

1. **Sentence splitting**: Text is split on sentence boundaries (`/(?<=[.!?])\s+/`).
   Each sentence is synthesized independently for lower latency (first sentence plays
   while subsequent sentences are being synthesized).

2. **TTS Engine** (`src/main/voice/tts-engine.ts`): Converts text to audio:
   - Backend: Kokoro (preferred) or Piper (fallback)
   - Output: Float32Array at 24kHz mono
   - Models loaded from `~/.nexus-os/models/tts/` (ONNX format)
   - Internal queue for sequential processing

3. **SpeechSynthesisManager** (`src/main/voice/speech-synthesis.ts`): Manages the utterance queue:
   - Max queue depth: 5 utterances
   - Generation counter: prevents stale utterance processing after `stop()`
   - Events: `utterance-start`, `utterance-end`, `queue-empty`, `interrupted`

4. **IPC to renderer**: Synthesized audio chunks are sent to the renderer's
   `AudioPlaybackEngine` via IPC for gapless playback through Web Audio API.

### 6. Barge-in

When the user starts speaking while the agent is responding:

1. `TranscriptionPipeline` emits a new `transcript` event.
2. `LocalConversation.onUserSpeech()` detects `speechSynthesis.isSpeaking()`.
3. `speechSynthesis.stop()` is called immediately:
   - Clears the utterance queue
   - Increments generation counter (stale utterances become no-ops)
   - Emits `interrupted` event
4. The new user input is processed as a normal turn.

## Audio Specifications

| Stage | Format | Sample Rate | Bit Depth | Channels | Notes |
|-------|--------|-------------|-----------|----------|-------|
| Microphone input | Float32 PCM | 16,000 Hz | 32-bit float | 1 (mono) | Captured in renderer, sent via IPC |
| AudioCapture VAD | Float32 PCM | 16,000 Hz | 32-bit float | 1 (mono) | RMS energy computed per chunk |
| Whisper input | Float32 PCM | 16,000 Hz | 32-bit float | 1 (mono) | Concatenated speech buffer |
| Whisper output | Text + segments | N/A | N/A | N/A | Includes timestamps per segment |
| TTS output | Float32 PCM | 24,000 Hz | 32-bit float | 1 (mono) | Kokoro or Piper ONNX inference |
| AudioPlaybackEngine | Float32 PCM | 24,000 Hz | 32-bit float | 1 (mono) | Web Audio API scheduled playback |

## Key Files

| File | Role |
|------|------|
| `src/main/local-conversation.ts` | Orchestrator -- chains STT -> LLM -> TTS, manages conversation state and queue |
| `src/main/voice/audio-capture.ts` | Main-process singleton: IPC coordination, energy-based VAD, speech buffering |
| `src/main/voice/transcription-pipeline.ts` | Wires AudioCapture events to WhisperProvider; manages transcription queue |
| `src/main/voice/whisper-provider.ts` | Whisper.cpp wrapper: model loading, audio transcription, result formatting |
| `src/main/voice/tts-engine.ts` | Kokoro/Piper TTS: text to 24kHz Float32 audio, backend auto-detection |
| `src/main/voice/speech-synthesis.ts` | Utterance queue manager: sentence splitting, IPC audio delivery, barge-in support |
| `src/main/llm-client.ts` | LLM abstraction layer with Ollama provider for chat completions |
| `src/main/desktop-tools.ts` | Desktop tool execution (file system, notifications, etc.) |
| `src/main/mcp-client.ts` | MCP tool execution fallback for tools not handled by desktop-tools |
| `src/renderer/audio/AudioPlaybackEngine.ts` | Gapless Web Audio playback with backpressure signaling |

## Diagram

See [diagram.mermaid](./diagram.mermaid) for the visual flow.
