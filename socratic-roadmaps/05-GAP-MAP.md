# Sprint 5 Gap Map — "The Eyes"

## Baseline
- Tests: ~4,250 (after Sprint 4)
- Sprint 4 delivered: WhisperProvider, AudioCapture, TranscriptionPipeline, TTSEngine, VoiceProfileManager, SpeechSynthesis, VoiceCircle

## What Exists (Post-Sprint 4)

| Module | Location | Status |
|--------|----------|--------|
| OllamaProvider | `src/main/providers/ollama-provider.ts` | Local LLM inference |
| EmbeddingPipeline | `src/main/embedding-pipeline.ts` | Local text embeddings |
| TranscriptionPipeline | `src/main/voice/transcription-pipeline.ts` | Mic → text |
| SpeechSynthesis | `src/main/voice/speech-synthesis.ts` | Text → speech |
| CloudGate | `src/main/cloud-gate.ts` | Consent-gated cloud access |

## What's Missing

Agent Friday can hear and speak but cannot see. It has no ability to understand images, screenshots, or visual context. Moondream (Apache 2.0) provides a lightweight 1.8B vision-language model that runs locally with ~1.2GB VRAM.

### Gap M — The Gaze (Vision Processing)

The system cannot process visual information. No image understanding, no screenshot analysis, no visual context awareness.

| Subgap | Description | Delivered By |
|--------|-------------|-------------|
| M.1 | VisionProvider — Load Moondream model, process images, answer visual questions | Phase M.1 |
| M.2 | ScreenContext — Capture screenshots, identify UI elements, provide visual context to LLM | Phase M.2 |
| M.3 | ImageUnderstanding — Pipeline for processing user-provided images (clipboard, drag-drop, file) | Phase M.3 |

### Gap N — The Sight (Vision Circle)

Individual vision components exist but haven't been tested as a complete visual perception system integrated with the LLM pipeline.

| Subgap | Description | Delivered By |
|--------|-------------|-------------|
| N.1 | VisionCircle integration test — Image → understanding → LLM context → response, graceful degradation | Phase N.1 |

## Hardware Budget

```
Vision VRAM: ~1.2 GB (Moondream 1.8B Q4)
Total with Vision: Embed(0.5) + LLM(5.5) + Vision(1.2) = 7.2GB
Remaining VRAM: ~4.8GB headroom on 12GB card
Voice: Still 0 VRAM (CPU)
```

## Technology Choices

| Component | Primary | Fallback | License | Runs On |
|-----------|---------|----------|---------|---------|
| VLM | Moondream 1.8B | Cloud vision API (gated) | Apache 2.0 | GPU (1.2GB) |
| Screenshot | Electron desktopCapturer | N/A | Electron built-in | System |
| Image input | Clipboard + file dialog | Drag-and-drop | Electron built-in | System |
