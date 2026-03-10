## Agent Friday v3.6.0 — Local Voice OS

Agent Friday now works as a fully local, voice-first AI operating system with zero cloud API keys required.

### New Features

- **Local voice conversation loop** — New `LocalConversation` orchestrator chains Whisper STT → Ollama LLM → Kokoro/Piper TTS for real-time voice conversations entirely on-device. The onboarding interview, post-onboarding chat, and all system events work fully offline.
- **Post-onboarding local path** — The app now continues local voice conversations after onboarding completes. Previously, the local voice path was limited to the onboarding interview only.
- **Three-tier tool routing** — Conversation tools automatically route through onboarding, feature setup, and desktop/MCP tool sets depending on the conversation phase.
- **Connection status UI** — ConnectionOverlay shows correct status for local-only users; TextInput displays a connection state indicator.

### Bug Fixes

- **Silent message loss** — `handleTextSend` now routes to local conversation when Gemini is unavailable instead of silently dropping messages
- **System event routing** — Scheduler, predictor, and system events route through the local conversation instead of being hardcoded to Gemini
- **AgentCreation local fallback** — Agent creation completes successfully without a Gemini API key

### Previous Fixes (v3.5.2)

- Voice interview silent failure — proper error on missing Gemini key
- Instant connection failure feedback instead of 30-second timeout
- SmartScreen trust via certificate bundling in NSIS installer
- Fixed misleading "add to .env" error message

### Previous Fixes (v3.5.1)

- NVIDIA Optimus laptop detection for RTX laptops
- Model downloads stuck at 0/0
- WebSocket close-code diagnostics
- Cross-platform GPU detection test fixes

### Installation

Download `Agent Friday Setup 3.6.0.exe` below and run the installer. Requires Windows 10+ (64-bit).

### Requirements

- Windows 10 or later (macOS and Linux builds coming soon)
- With Ollama installed and sufficient hardware (8GB+ VRAM), Agent Friday runs with zero cloud API keys — fully local voice conversations included
- Optionally add API keys from Anthropic, Google, or OpenRouter for frontier cloud capabilities
