## Agent Friday v3.7.1 — Local-First Voice & API Key Health Indicators

Makes voice truly local-first and adds live validation indicators during API key setup.

### New Features

- **Local-first voice architecture** — Voice now always attempts local conversation (Ollama + Whisper + TTS) first. If local voice fails, gracefully falls back to Gemini Live when a key is available. If neither works, shows a clear error instead of hanging.
- **Live API key validation indicators** — Each key field in the onboarding API Keys step now shows a real-time status indicator: yellow dot while checking, green checkmark when valid, red alert when invalid. Validation is debounced (800ms) and runs via main-process IPC.

### Bug Fixes

- **Voice interview hanging** — The interview step could hang indefinitely when the voice connection failed silently. Now the local-first path either succeeds or cleanly falls through to Gemini, with clear error messages at each stage.

### Installation

Download `Agent Friday Setup 3.7.1.exe` below and run the installer. Requires Windows 10+ (64-bit).

### Requirements

- **Ollama** (free, no account) — Download from [ollama.com](https://ollama.com/download) for local AI. Agent Friday guides you through this during setup.
- Windows 10 or later (macOS and Linux builds coming soon)
- With Ollama installed and 8GB+ VRAM, Agent Friday runs fully local with zero cloud API keys
- Optionally add API keys from Anthropic, Google, or OpenRouter for frontier cloud capabilities
