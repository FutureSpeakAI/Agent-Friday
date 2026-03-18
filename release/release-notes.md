## Agent Friday v3.7.0 — Ollama Dependency Check & Download Fix

Adds an Ollama dependency check during onboarding and fixes the download progress display that was broken since launch.

### New Features

- **Ollama dependency check during onboarding** — Before showing model recommendations, Agent Friday now checks if Ollama is running. If not, users see clear step-by-step instructions: download from ollama.com, run the installer (no account needed), and click "Check Again". Users can also skip to cloud-only mode.

### Bug Fixes

- **Download progress callback mismatch (v3.6.6)** — The preload bridge strips IPC events before calling renderer callbacks, but HardwareStep.tsx expected `(_event, data)` instead of just `(data)`. The download data was always going into the wrong parameter, so `setDownloads()` was never called. This was the root cause of "0 models installed" since launch.
- **Non-Ollama models sent to Ollama pull (v3.6.5)** — CPU-only models (Piper, Whisper, Kokoro) and diffusion models are now correctly skipped during the Ollama download phase.
- **API key validation CORS block (v3.6.5)** — Validation now runs in the main process via IPC, bypassing renderer CORS restrictions.
- **Settings crash on open (v3.6.5)** — React hooks ordering violation fixed.

### Installation

Download `Agent Friday Setup 3.7.0.exe` below and run the installer. Requires Windows 10+ (64-bit).

### Requirements

- **Ollama** (free, no account) — Download from [ollama.com](https://ollama.com/download) for local AI. Agent Friday guides you through this during setup.
- Windows 10 or later (macOS and Linux builds coming soon)
- With Ollama installed and 8GB+ VRAM, Agent Friday runs fully local with zero cloud API keys
- Optionally add API keys from Anthropic, Google, or OpenRouter for frontier cloud capabilities
