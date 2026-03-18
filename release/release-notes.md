## Agent Friday v3.6.5 — Onboarding & Settings Stability

Fixes three critical issues: API key validation failing due to CORS, model downloads sending non-Ollama models to Ollama, and Settings panel crashing from a hooks violation.

### Bug Fixes

- **API key validation (CORS)** — Anthropic and OpenRouter key validation always failed because the renderer's `fetch()` was blocked by CORS (not just CSP). Validation now runs in the main process via IPC where there are no CORS restrictions.
- **Model downloads sending non-Ollama models** — CPU-only models (Piper TTS, Whisper STT, Kokoro) and diffusion models were sent to `ollama pull`, which silently failed for all of them. The setup wizard now skips non-Ollama models and only pulls LLM, embedding, and vision models through Ollama.
- **Settings crash (React error #310)** — Opening Settings triggered a hooks violation because `useState` was called after a conditional early return. Moved the hook above the return to comply with React's rules of hooks.

### Installation

Download `Agent Friday Setup 3.6.5.exe` below and run the installer. Requires Windows 10+ (64-bit).

### Requirements

- Windows 10 or later (macOS and Linux builds coming soon)
- With Ollama installed and sufficient hardware (8GB+ VRAM), Agent Friday runs with zero cloud API keys — fully local voice conversations included
- Optionally add API keys from Anthropic, Google, or OpenRouter for frontier cloud capabilities
