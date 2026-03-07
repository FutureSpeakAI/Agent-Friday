# Phase N.1 — The Sight
## VisionCircle: The Complete See→Understand→Respond Integration

### Hermeneutic Focus
*Each visual organ exists — the gaze, the glance, the focus. But does the system truly see? This phase tests the full vision circle: image → understanding → LLM context → response. Only through integration do we discover if sight produces insight.*

### Current State (Post-M.3)
- VisionProvider: image → description/VQA via Moondream
- ScreenContext: screenshot → description
- ImageUnderstanding: clipboard/file/drop → description
- LLM pipeline: local-first routing with confidence assessment
- No integration between vision and conversation exists

### Architecture Context
```
Vision Circle Integration
├── ScreenContext.getContext() → LLM system context (background awareness)
├── ImageUnderstanding.on('image-result') → LLM message with image context
├── VisionProvider as tool → LLM can request "look at this image"
├── Graceful degradation → works without vision model
└── VRAM coordination → vision loads/unloads based on need
```

### Validation Criteria (Test-First)
1. Screen context automatically included in LLM system prompt when available
2. User-provided image flows through to LLM as contextual message
3. LLM can reference image content in its response
4. System degrades gracefully when VisionProvider unavailable
5. System degrades gracefully when screen capture denied
6. Vision model loads on-demand when image is provided
7. Vision model unloads after inactivity timeout (frees VRAM)
8. OllamaLifecycle accurately reports VRAM with vision model loaded
9. Full round-trip: image input → vision description → LLM response verified
10. No regressions in voice circle or intelligence circle

### Socratic Inquiry

**Boundary:** *How does visual context enter the conversation?*
Two paths: (1) Background — ScreenContext periodically updates, latest description appended to system prompt. (2) Active — user provides image, description injected as a user message before the user's question. Both are text by the time they reach the LLM.

**Inversion:** *What if the vision model and chat model compete for VRAM?*
Moondream (1.2GB) + Llama 8B (5.5GB) + embed (0.5GB) = 7.2GB, fitting in 12GB. On smaller cards, OllamaLifecycle should unload vision when chat needs VRAM and vice versa. This phase tests that coordination.

**Synthesis:** *How do voice and vision interact?*
User can say "What's on my screen?" → TranscriptionPipeline transcribes → LLM receives text + current screen context → responds with description → SpeechSynthesis speaks it. Voice + vision compose naturally through the LLM.

**Safety Gate:** *Can screen capture leak sensitive information?*
Screen descriptions are text, processed locally by Moondream. They enter the LLM pipeline which is local-first. If the LLM escalates to cloud (via CloudGate consent), the screen description text would go to the cloud provider. This is acceptable — the user consented to cloud escalation, and the description is a summary, not raw pixels.

### Boundary Constraints
- Creates `tests/sprint-5/integration/vision-circle.test.ts` (~180-220 lines)
- Does NOT create new production modules — only integration test
- Mocks: VisionProvider, ScreenContext, ImageUnderstanding, LLM provider
- Tests the wiring, not the individual components

### Files to Read
1. `tests/sprint-4/integration/voice-circle.test.ts` — Integration test pattern
2. `tests/sprint-3/integration/local-intelligence-circle.test.ts` — Earlier integration pattern
3. `src/main/vision/vision-provider.ts` — Vision API

### Session Journal Reminder
Write `socratic-roadmaps/journals/track-n-phase-1.md` before closing.
Write `evolution/sprint-5-review.md` — this completes the fifth full sprint.
