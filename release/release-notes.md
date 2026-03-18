## Agent Friday v3.6.4 — API Key Validation & Model Download Fixes

Fixes two critical onboarding issues: API key validation failing for Anthropic/OpenRouter, and local model downloads silently stalling.

### Bug Fixes

- **API key validation blocked by CSP** — Anthropic and OpenRouter key validation failed with "Could not reach servers" because `connect-src` didn't include `api.anthropic.com` or `openrouter.ai`. CSP now permits both endpoints.
- **Model downloads never signal completion** — `startModelDownload()` finished downloading and loading models but never emitted the `setup-complete` event, leaving the UI stuck on the downloading phase indefinitely. The wizard now calls `completeSetup()` after loading.
- **Download progress stuck at 0%** — Early Ollama progress events with `total: 0` were dropped by a falsy check (`progress.total &&`), so the UI showed no progress until the server reported a non-zero total. Fixed to `progress.total !== undefined`.

### Installation

Download `Agent Friday Setup 3.6.4.exe` below and run the installer. Requires Windows 10+ (64-bit).

### Requirements

- Windows 10 or later (macOS and Linux builds coming soon)
- With Ollama installed and sufficient hardware (8GB+ VRAM), Agent Friday runs with zero cloud API keys — fully local voice conversations included
- Optionally add API keys from Anthropic, Google, or OpenRouter for frontier cloud capabilities
