# Sprint 5 Review -- "The Vision"

**Sprint period:** 2026-03-08
**Test count:** 4,165 (Sprint 4 end) -> 4,205 (Sprint 5 end) = **+40 tests**
**Test files:** 116 (all passing)
**Safety Gate:** PASSED on every phase

## Sprint Goal

Build the complete vision pipeline for Agent Friday: local vision-language model
integration via Moondream on Ollama, screenshot capture via Electron desktopCapturer,
multi-source image input (clipboard, file picker, drag-drop), and a full vision
circle integration that enriches LLM conversations with visual context. Agent Friday
can now see the screen, understand user-provided images, and reference visual
content in responses.

## Phase Summary

| Phase | Track | Name | Tests Added | Key Deliverable |
|-------|-------|------|-------------|-----------------|
| M.1 | M | The Gaze | +10 | VisionProvider -- local VLM via Ollama (moondream) |
| M.2 | M | The Glance | +10 | ScreenContext -- screenshot capture + description caching |
| M.3 | M | The Focus | +10 | ImageUnderstanding -- clipboard/file/drop image input |
| N.1 | N | The Sight | +10 | Integration test: full vision circle (10 criteria) |

**Total: 4 phases, 3 modules built, 1 integration suite, +40 tests**

## Architecture Overview

```
User provides image (clipboard paste, file drop, file picker)
    |
    v
ImageUnderstanding (validates format/size, routes to VisionProvider)
    |
    +---> VisionProvider.describe(Buffer) or .answer(Buffer, question)
    |         |
    |         v
    |     Ollama /api/generate (moondream:latest, local GPU)
    |         |
    |         v
    |     Natural language description
    |
    v
ImageResult { description, source, timestamp, imageSizeBytes }
    |
    +---> emit('image-result') --> wireVisionCircle listener
    |                                  |
    |                                  v
    |                              lastImageDescription cached
    |
    v
LLM context enrichment:
    systemPrompt += screenContext.getContext()   (ambient awareness)
    messages.unshift({ role: 'user', content: '[Image context: ...]' })
    |
    v
llmClient.complete({ systemPrompt, messages }) --> context-aware response

--- Screen capture path ---
ScreenContext.captureScreen()
    |
    +---> desktopCapturer.getSources({ types: ['screen'] })
    |         |
    |         v
    |     NativeImage.toPNG() --> Buffer
    |
    +---> VisionProvider.describe(buffer)
    |         |
    |         v
    |     lastContext cached, emit('context-update')
    |
    +---> startAutoCapture(30_000) --> periodic re-capture

--- VRAM management ---
image provided + !visionIsReady --> loadModel() (on-demand)
60s no image-result --> unloadModel() (frees VRAM)
```

## VRAM Budget

Agent Friday runs three models simultaneously on a single GPU:

| Model | Purpose | VRAM |
|-------|---------|------|
| nomic-embed-text | Embedding for context/search | ~0.5 GB |
| llama3:8b-instruct-q4_K_M | Local LLM (reasoning, chat) | ~5.5 GB |
| moondream:latest | Vision-language model (Q4) | ~1.2 GB |
| **Total** | | **~7.2 GB** |

On a 12 GB GPU, this leaves ~4.8 GB headroom for KV cache and OS overhead.
The vision model loads on-demand and unloads after 60 seconds of inactivity,
so the steady-state VRAM usage is ~6.0 GB (embed + LLM only).

## Architecture Decisions

### 1. Singleton Pattern with resetInstance()
All vision modules (VisionProvider, ScreenContext, ImageUnderstanding) continue
the singleton-with-reset pattern established in Sprint 4. This provides clean
test isolation and predictable resource lifecycle.

### 2. Event Emitter with Unsubscribe Return
All modules return an unsubscribe function from `.on()` calls, following the
pattern established by AudioCapture. This enables deterministic cleanup in the
wireVisionCircle teardown and prevents listener leaks across test runs.

### 3. On-Demand Model Loading
The vision model is not loaded at startup. It loads only when the first image
is processed, avoiding VRAM consumption for users who never use vision features.
The 60-second inactivity timeout automatically unloads the model when not needed.

### 4. Two-Layer Context Enrichment
Screen context is injected into the system prompt (ambient, always-on awareness).
Image context is injected as a user message prefix (conversational, per-exchange).
This separation ensures the LLM treats screen context as environmental and image
context as topical.

### 5. Graceful Degradation Over Hard Failure
The vision circle degrades gracefully at every boundary:
- No vision model: images are not described, but text chat works normally
- No screen capture: system prompt has no screen context, but responds to queries
- No GPU: moondream can run in CPU mode (slower) or be skipped entirely
This ensures Agent Friday remains fully functional regardless of vision capability.

### 6. Buffer-Based Image Pipeline
All image data flows as Node.js Buffers internally. File paths and base64 strings
are resolved to Buffers at the entry points (ImageUnderstanding, VisionProvider).
This simplifies the internal pipeline and avoids encoding/decoding overhead.

## Technical Insights

### Mock Boundary: Module Interface Level
Integration tests mock at the module interface (VisionProvider.describe(),
screenContext.getContext(), etc.) rather than at the hardware level. This is
because vision hardware varies widely (GPU brand, driver version, screen count)
and hardware-level mocks would be fragile. The module interfaces are stable
contracts that accurately represent the integration surface.

### Vision Circle vs Voice Circle Complexity
The voice circle requires real-time event choreography with timing constraints:
VAD detection -> buffering -> transcription -> LLM -> TTS -> barge-in handling ->
turn-taking. The vision circle is request-response with caching: an image arrives,
gets described, the description is cached, and the next LLM call includes it.
The complexity in vision is resource management (VRAM, on-demand loading, timeout)
rather than event timing.

### Thumbnail Size for VLM Efficiency
ScreenContext captures thumbnails at 768x768 max rather than full resolution.
This is optimized for Moondream's input resolution and reduces the base64 payload
sent to Ollama's /api/generate endpoint. Full-resolution capture would waste
bandwidth and processing time without improving description quality.

## What Sprint 5 Proved

Agent Friday can now see. The vision pipeline enables:

1. **Screen awareness** -- Capture and describe the user's desktop in natural language
2. **Image understanding** -- Process user-provided images via clipboard, file, or drop
3. **Visual question answering** -- Answer specific questions about images
4. **Context enrichment** -- Inject visual context into LLM conversations
5. **Resource efficiency** -- Load vision model on-demand, unload after timeout
6. **VRAM management** -- Three models (embed + LLM + vision) within 12 GB budget
7. **Graceful degradation** -- Full functionality without vision hardware
8. **Format flexibility** -- PNG, JPEG, WebP, GIF input with 10 MB size limit

## What Sprint 6 Builds On

Sprint 6 will connect the vision and voice pipelines to create a multimodal agent:
- Voice-initiated image analysis ("What's on my screen?")
- Continuous screen monitoring with context-aware alerts
- Visual tool use (screenshot -> extract data -> execute action)
- Image-to-code generation from UI screenshots

## Metrics

- **40 new tests** across 4 phases
- **0 regressions** -- all 4,165 pre-existing tests remained green
- **3 source files created** (main process modules)
- **4 test files created** (3 unit + 1 integration)
- **10 integration criteria** validated the full vision circle
- **2 tracks advanced** (M: Vision Pipeline, N: Vision Integration)
- **VRAM budget:** 7.2 GB peak / 6.0 GB steady-state on 12 GB GPU
