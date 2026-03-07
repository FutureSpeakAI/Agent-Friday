# Sprint 6 Gap Map — "The Body"

## Baseline
- Tests: ~4,350 (after Sprint 5)
- Sprint 5 delivered: VisionProvider, ScreenContext, ImageUnderstanding, VisionCircle

## What Exists (Post-Sprint 5)

| Module | Location | Status |
|--------|----------|--------|
| OllamaProvider | `src/main/providers/ollama-provider.ts` | Local LLM via Ollama |
| OllamaLifecycle | `src/main/ollama-lifecycle.ts` | Health, model discovery, VRAM |
| EmbeddingPipeline | `src/main/embedding-pipeline.ts` | Local text embeddings |
| ConfidenceAssessor | `src/main/confidence-assessor.ts` | Output quality scoring |
| CloudGate | `src/main/cloud-gate.ts` | Consent-gated cloud access |
| WhisperProvider | `src/main/voice/whisper-provider.ts` | Local STT |
| TTSEngine | `src/main/voice/tts-engine.ts` | Local TTS |
| VisionProvider | `src/main/vision/vision-provider.ts` | Local vision |
| Settings system | `src/main/settings.ts` | Preference persistence |
| Electron app | `src/main/main.ts` | Window, IPC, lifecycle |

## What's Missing

All the organs exist but the system doesn't know its own body. It can't detect what hardware it's running on, can't recommend which AI models to use, and has no first-run setup experience. A new user faces a blank slate with no guidance.

### Gap O — The Nerves (Hardware Awareness)

The system has no hardware self-awareness. It doesn't know its GPU, VRAM, RAM, or CPU capabilities. It can't make intelligent decisions about which models to load or how to configure itself.

| Subgap | Description | Delivered By |
|--------|-------------|-------------|
| O.1 | HardwareProfiler — Detect GPU, VRAM, RAM, CPU, disk space | Phase O.1 |
| O.2 | TierRecommender — Map hardware profile to model tier recommendation | Phase O.2 |
| O.3 | ModelOrchestrator — Coordinate model loading/unloading based on VRAM budget | Phase O.3 |

### Gap P — The Birth (First-Run Experience)

Even with hardware detection, there's no setup wizard to guide a new user through initial configuration. No model download management, no profile creation, no first conversation.

| Subgap | Description | Delivered By |
|--------|-------------|-------------|
| P.1 | SetupWizard — First-run flow: detect hardware → recommend tier → download models | Phase P.1 |
| P.2 | ProfileManager — User profile creation, settings migration, config export/import | Phase P.2 |
| P.3 | TheAwakening — Integration test: fresh install → setup → first conversation | Phase P.3 |

## Hardware Detection APIs

```
GPU Detection:
  Electron: app.getGPUInfo('complete')  → GPU name, VRAM, driver
  Fallback: child_process nvidia-smi    → NVIDIA-specific details

System Info:
  Node.js:  os.totalmem()              → Total RAM
  Node.js:  os.cpus()                  → CPU info
  Node.js:  os.freemem()              → Available RAM
  Electron: app.getPath('userData')    → Available disk space
```

## Hardware Tier Map

| Tier | GPU VRAM | Models | Experience |
|------|----------|--------|------------|
| Whisper | 0 GB (CPU only) | None local | Cloud-only LLM, CPU Whisper tiny |
| Light | 4 GB | Embed only | Local embeddings, cloud LLM |
| Standard | 8 GB | Embed + 8B Q4 | Local LLM + embeddings |
| Full | 12 GB | Embed + 8B + Vision | Full local stack |
| Sovereign | 24+ GB | All + larger models | Best local quality |
