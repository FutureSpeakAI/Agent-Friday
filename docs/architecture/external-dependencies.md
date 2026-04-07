# External Dependencies Reference

> Agent Friday v3.14.0 — Last updated 2026-04-07

---

## Binary Executables

All local binaries follow a three-step discovery order unless noted otherwise:

1. `~/.nexus-os/bin/` (user-managed)
2. App resources `resources/bin/` (bundled with installer)
3. System `PATH`

| Binary | Purpose | Discovery | Spawn Timeout |
|---|---|---|---|
| `whisper-cpp` (whisper-cli / main) | Speech-to-text via whisper.cpp | ~/.nexus-os/bin/ → resources/bin/ → PATH | 60 s |
| `sherpa-onnx-offline-tts` | TTS synthesis (Kokoro backend) | ~/.nexus-os/bin/ → resources/bin/ → PATH | 30 s |
| `piper` | TTS synthesis (fallback backend) | ~/.nexus-os/bin/ → resources/bin/ → PATH | 30 s |
| `docker` | Container-isolated code execution | PATH only | 300 s |
| `ffmpeg` | Audio/video transcoding & processing | PATH only | 120 s |
| `ffprobe` | Media metadata extraction | PATH only | 120 s |

---

## Cloud AI APIs

### Gemini (Google)

| Endpoint | Protocol | Usage |
|---|---|---|
| `wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent` | WebSocket | Bidirectional real-time audio conversation (Gemini Live) |
| `generativelanguage.googleapis.com` REST | HTTPS | VEO-3 video generation, audio generation (30 prebuilt voices), standard chat |

- Env var: `GEMINI_API_KEY`
- SDK: `@google/generative-ai`

### Anthropic Claude

| Detail | Value |
|---|---|
| Default model | `claude-sonnet-4-20250514` |
| SDK | `@anthropic-ai/sdk` |
| Env var | `ANTHROPIC_API_KEY` |
| Usage | Primary text LLM for reasoning, tool use, memory extraction |

### OpenAI

| Detail | Value |
|---|---|
| Models | `o3` (reasoning), `gpt-4-turbo` (vision) |
| SDK | `openai` npm package |
| Env var | `OPENAI_API_KEY` |
| Usage | Complex reasoning tasks, image understanding |

### Perplexity

| Detail | Value |
|---|---|
| Models | `sonar`, `sonar-pro`, `sonar-deep-research`, `sonar-reasoning-pro` |
| Env var | `PERPLEXITY_API_KEY` |
| Usage | Real-time web search and research synthesis |

### Firecrawl

| Detail | Value |
|---|---|
| Capabilities | `search`, `scrape`, `crawl` |
| Env var | `FIRECRAWL_API_KEY` |
| Usage | Web content extraction, site crawling, search |

### HuggingFace Inference

| Detail | Value |
|---|---|
| Default model | `Llama-3.3-70B-Instruct` |
| Env var | `HUGGINGFACE_API_KEY` |
| Usage | Fallback cloud LLM via HF Inference API |

### OpenRouter

| Detail | Value |
|---|---|
| Models | 200+ model gateway (routes to any provider) |
| Env var | `OPENROUTER_API_KEY` |
| Usage | Flexible multi-provider model access |

### ElevenLabs

| Detail | Value |
|---|---|
| Env var | `ELEVENLABS_API_KEY` |
| Usage | Premium cloud voice synthesis (optional) |

---

## Local Services

### Ollama

| Detail | Value |
|---|---|
| Base URL | `http://localhost:11434` |
| Health poll | Every 30 seconds |
| Key endpoints | `/api/chat`, `/api/tags`, `/api/pull`, `/api/generate` |
| Managed models | `llama3.1:8b`, `llama3.2`, `nomic-embed-text`, Gemma 4 family (varies by user) |
| IPC namespace | `eve.ollama` |

#### Gemma 4 Models (via Ollama)

| Model | Parameters | Context Window | License |
|---|---|---|---|
| `gemma4:e2b` | ~2B | 128K | Apache 2.0 |
| `gemma4:e4b` | ~4B | 128K | Apache 2.0 |
| `gemma4:26b` | 26B MoE | 256K | Apache 2.0 |
| `gemma4:31b` | 31B Dense | 256K | Apache 2.0 |

- Zero cost, fully local via Ollama — no API key required
- Native tool calling support (function declarations in chat API)
- Apache 2.0 licensed — no usage restrictions, no telemetry
- E2B/E4B suitable for lightweight tasks; 26B MoE and 31B Dense for complex reasoning

Ollama lifecycle is managed by the main process. The app detects Ollama availability on startup and emits health-change events (`ollama:event:healthy`, `ollama:event:unhealthy`). Model pull progress is streamed via `ollama:event:pull-progress`.

### Internal Express Server

| Detail | Value |
|---|---|
| Default port | `3333` (falls back to next available) |
| Purpose | Local HTTP API for renderer ↔ main process communication |
| IPC channel | `get-api-port` returns the active port |

---

## Data Directories

### User Data (`~/.nexus-os/`)

```
~/.nexus-os/
├── bin/                          # User-installed binaries (whisper, sherpa-onnx, piper)
├── models/
│   ├── tts/
│   │   ├── kokoro/               # Kokoro ONNX voice models + tokens
│   │   └── piper/                # Piper ONNX voice models + configs
│   ├── whisper/                  # Whisper GGML model files (base, small, medium, etc.)
│   └── ollama/                   # Ollama model storage (managed by Ollama daemon)
├── state/                        # Encrypted state backups (persistence module)
└── superpowers/                  # Installed superpower packages
```

### App userData (Electron `app.getPath('userData')`)

```
<userData>/
├── settings.json                 # User preferences, API key references, tier config
├── chat-history/                 # Persisted conversation history (JSON per session)
├── crash.log                     # Crash reports
├── profiles/                     # Agent personality profiles
├── memories/                     # Long-term and medium-term memory stores
├── trust-graph/                  # Person trust records
├── episodic/                     # Episodic memory snapshots
├── briefings/                    # Daily briefing cache
├── commitments/                  # Tracked commitments and promises
├── context-stream/               # Rolling context event log
├── workflows/                    # Recorded and templated workflows
├── inbox/                        # Inbound message store
├── outbound/                     # Outbound draft queue
├── meetings/                     # Meeting intel records
├── notes/                        # User notes
├── documents/                    # Ingested document index
├── vault/                        # Encrypted credential vault
├── ecosystem/                    # Package manifests and transaction history
├── agent-network/                # Peer agent identities and delegation logs
└── multimedia/                   # Generated media (podcasts, visuals, audio)
```

---

## Environment Variables

All API keys are configured via the settings UI or `.env` file. The settings module (`eve.settings.setApiKey`) accepts these key identifiers:

| Env Variable | Settings Key | Required | Service |
|---|---|---|---|
| `GEMINI_API_KEY` | `gemini` | Yes (for voice) | Gemini Live, VEO-3, audio gen |
| `ANTHROPIC_API_KEY` | `anthropic` | Yes (for text) | Claude primary LLM |
| `OPENAI_API_KEY` | `openai` | No | o3 reasoning, GPT-4 vision |
| `FIRECRAWL_API_KEY` | `firecrawl` | No | Web search/scrape/crawl |
| `PERPLEXITY_API_KEY` | `perplexity` | No | Real-time research |
| `OPENROUTER_API_KEY` | `openrouter` | No | Multi-provider model gateway |
| `HUGGINGFACE_API_KEY` | `huggingface` | No | HF Inference fallback |
| `ELEVENLABS_API_KEY` | `elevenlabs` | No | Premium cloud TTS |

---

## Graceful Degradation Table

| Dependency | Required? | Fallback When Missing |
|---|---|---|
| **Gemini API key** | For voice conversation | Falls back to local voice path (Whisper STT → Ollama → Kokoro TTS) |
| **Anthropic API key** | For text intelligence | Falls back to Ollama local models or OpenRouter if configured |
| **Ollama** | No | Cloud-only mode; local models unavailable; intelligence router selects cloud providers |
| **whisper-cpp** | For local STT | Local voice path unavailable; Gemini Live used for voice; text input still works |
| **sherpa-onnx-offline-tts** | For local TTS | Falls back to Piper TTS backend |
| **piper** | No (secondary TTS) | Kokoro is primary; if both missing, TTS unavailable in local mode |
| **docker** | No | Container execution disabled; code runs via direct `code:execute-direct` (less isolated) |
| **ffmpeg / ffprobe** | No | Audio/video processing features disabled; podcast and multimedia creation may fail |
| **OpenAI API key** | No | o3 reasoning and GPT-4 vision routed to other providers by intelligence router |
| **Firecrawl API key** | No | Web scraping tools unavailable; Perplexity search used if available |
| **Perplexity API key** | No | Research features limited; falls back to Firecrawl search or direct web fetch |
| **OpenRouter API key** | No | Model gateway unavailable; direct provider APIs used instead |
| **HuggingFace API key** | No | HF inference unavailable; other cloud or local models used |
| **ElevenLabs API key** | No | Premium voice unavailable; Kokoro/Piper local TTS used |

### Voice Path Fallback Chain

The `voiceFallback` module probes available paths and selects the best:

1. **Gemini Live** (cloud, requires Gemini API key + internet)
2. **Local full** (Whisper STT → Ollama LLM → Kokoro TTS, requires all three binaries/services)
3. **Local partial** (Whisper STT → Ollama LLM → no TTS, text-only responses)
4. **Text-only** (no voice input/output, keyboard/screen only)

The `connectionStage` monitor emits granular progress events during path initialization so the UI can show per-stage status.
