## Session Journal: Track N, Phase 1 -- "The Sight"
**Date:** 2026-03-08
**Sprint:** 5 (Vision Pipeline)
**Phase:** N.1 -- Vision Circle Integration

### What Was Built
Integration test suite validating the full see-understand-respond pipeline:
`ScreenContext -> ImageUnderstanding -> VisionProvider -> LLM context enrichment`

No new production modules were created. This is a pure integration test verifying
that Phases M.1 (VisionProvider), M.2 (ScreenContext), and M.3 (ImageUnderstanding)
compose correctly into a coherent vision circle.

### Architecture

```
ScreenContext.getContext()
    |
    v
LLM system prompt enrichment ("Current screen context: ...")
    |
    +--- ImageUnderstanding.on('image-result')
    |        |
    |        v
    |    lastImageDescription cached
    |        |
    |        v
    |    LLM message injection ("[Image context: ...]")
    |
    v
llmClient.complete({ systemPrompt, messages })
    |
    v
LLM response (context-aware, references screen + image)

--- On-demand loading ---
image provided + !visionIsReady --> visionProvider.loadModel()
--- Inactivity timeout ---
60s no image-result --> visionProvider.unloadModel() (frees VRAM)
```

### Wire Pattern: wireVisionCircle()
The integration test defines a `wireVisionCircle()` helper that mirrors the
`wireVoiceCircle()` pattern from Sprint 4 L.1. It:

1. Subscribes to `imageUnderstanding.on('image-result')` to cache descriptions
2. Enriches the LLM system prompt with `screenContext.getContext()`
3. Injects image context as a prefixed user message
4. Manages an inactivity timer that unloads the vision model after 60s
5. Returns a teardown function for clean test cleanup

### 10 Validation Criteria

| # | Criterion | Status |
|---|-----------|--------|
| 1 | Screen context in LLM system prompt when available | PASS |
| 2 | User image description flows to LLM as context message | PASS |
| 3 | LLM references image content in response | PASS |
| 4 | Graceful degradation: VisionProvider unavailable | PASS |
| 5 | Graceful degradation: screen capture denied | PASS |
| 6 | Vision model loads on-demand when image provided | PASS |
| 7 | Vision model unloads after 60s inactivity timeout | PASS |
| 8 | VRAM tracking accurate with vision model loaded | PASS |
| 9 | Full round-trip: image -> description -> LLM response | PASS |
| 10 | No regressions in basic LLM operation | PASS |

### Key Design Choices

1. **Mock at the module boundary, not the hardware boundary**: Unlike the voice
   circle which mocks at AudioCapture events, the vision circle mocks at the
   module interface level (VisionProvider, ScreenContext, ImageUnderstanding).
   This is because vision hardware (GPU, screen capture) is more varied and
   harder to simulate than audio I/O.

2. **System prompt enrichment over message injection for screen context**: Screen
   context goes into the system prompt (persistent environmental awareness),
   while image context goes into the messages array (conversational context).
   This distinction matters because screen context is ambient -- it applies to
   all queries -- while image context is specific to the current exchange.

3. **Inactivity timeout with clearTimeout reset**: Each new image-result resets
   the 60-second timer. This prevents premature model unloading during active
   image analysis sessions while still freeing VRAM when the user stops
   providing images.

4. **VRAM budget validation in test**: Test 8 explicitly validates the VRAM
   budget arithmetic: Embed(0.5GB) + LLM(5.5GB) + Vision(1.2GB) = 7.2GB
   on a 12GB GPU. This ensures the three-model budget stays within hardware
   limits.

### What Surprised Me
The vision circle is simpler than the voice circle. Voice requires real-time
event choreography (VAD -> transcription -> LLM -> TTS -> barge-in -> turn-taking)
with timing constraints. Vision is request-response with caching: capture or receive
an image, describe it, inject the description into context. The complexity is in
resource management (VRAM, on-demand loading, timeout) rather than event flow.

### Test Metrics
- **10 new tests** in 1 integration test file
- **0 regressions** -- all 4,195 pre-existing tests remained green
- **4,205 total tests** across 116 test files
- Safety gate: `tsc --noEmit` 0 errors, `vitest run` 0 failures
