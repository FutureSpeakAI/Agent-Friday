# Phase M.1 — The Gaze
## VisionProvider: Local Vision-Language Model

### Hermeneutic Focus
*The system can hear and speak but remains blind. This phase gives it sight — loading Moondream (a 1.8B vision-language model) to understand images. Like the ear translates sound to text, the gaze translates images to understanding.*

### Current State (Post-Sprint 4)
- OllamaProvider serves LLM inference (can host Moondream via Ollama)
- EmbeddingPipeline generates text vectors
- No image processing exists
- Moondream 1.8B available via Ollama as `moondream:latest`

### Architecture Context
```
VisionProvider (this phase)
├── loadModel(name?)      — Load vision model via Ollama
├── describe(image)       — Image → natural language description
├── answer(image, question) — Visual question answering
├── isReady()             — Model loaded
├── unloadModel()         — Free VRAM
└── getModelInfo()        — Model name, VRAM usage
```

### Validation Criteria (Test-First)
1. `VisionProvider.loadModel()` succeeds when Moondream available in Ollama
2. `VisionProvider.loadModel()` returns graceful error when model missing
3. `VisionProvider.describe(image)` returns text description for valid image
4. `VisionProvider.answer(image, question)` returns answer to visual question
5. `VisionProvider.isReady()` false before load, true after
6. Image input accepts base64 PNG/JPEG and file paths
7. `unloadModel()` frees VRAM, `isReady()` returns false
8. `getModelInfo()` reports VRAM usage (~1.2GB for Moondream Q4)
9. Provider handles malformed/corrupt images gracefully
10. All tests mock Ollama vision API — no model required in CI

### Socratic Inquiry

**Boundary:** *Should VisionProvider use Ollama or load Moondream directly?*
Use Ollama. It already handles model management, VRAM allocation, and provides a consistent API. VisionProvider calls `/api/generate` with image data, just like OllamaProvider calls `/api/chat` for text. Don't reinvent model hosting.

**Inversion:** *What if Moondream isn't installed or VRAM is full?*
`isReady()` returns false. The system operates without vision — images attached to messages get a placeholder ("Image attached but vision model unavailable"). CloudGate can offer cloud vision as a gated fallback.

**Constraint Discovery:** *How much VRAM does Moondream add to the stack?*
Moondream 1.8B Q4: ~1.2GB. Total stack: embed(0.5) + LLM(5.5) + vision(1.2) = 7.2GB. Well within 12GB RTX 4070. But on smaller cards, vision may need to unload when LLM needs full VRAM.

**Precedent:** *How does OllamaProvider handle /api/generate?*
Same HTTP pattern — POST with model name and prompt. For vision, add the `images` field (base64-encoded). Response is streamed text. Follow identical error handling and timeout patterns.

**Tension:** *Always-loaded vs. on-demand for vision model?*
On-demand. Vision is used less frequently than chat. Load when an image arrives, keep loaded for a timeout period (5 min), then unload to free VRAM. OllamaLifecycle already tracks loaded models.

### Boundary Constraints
- Creates `src/main/vision/vision-provider.ts` (~130-160 lines)
- Creates `tests/sprint-5/vision/vision-provider.test.ts`
- Uses Ollama API for model hosting (does NOT load models directly)
- Does NOT handle screenshot capture (that's M.2)
- Does NOT handle clipboard/file input (that's M.3)

### Files to Read
1. `src/main/providers/ollama-provider.ts` — Ollama API pattern
2. `src/main/ollama-lifecycle.ts` — Model loading/unloading pattern

### Session Journal Reminder
Write `socratic-roadmaps/journals/track-m-phase-1.md` before closing.
