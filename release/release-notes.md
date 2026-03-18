## Agent Friday v3.6.3 — Onboarding Hardware Step Fix

Fixes a crash on first launch where the onboarding hardware detection screen threw a React error instead of displaying model recommendations.

### Bug Fixes

- **Hardware step crash** — The onboarding "Your Hardware, Your AI" screen crashed with React error #31 (Objects are not valid as a React child) because the hardware profiler returned full model-requirement objects (`{name, diskBytes, vramBytes, ...}`) but the UI expected plain strings. Model names are now correctly extracted before rendering.

### Installation

Download `Agent Friday Setup 3.6.3.exe` below and run the installer. Requires Windows 10+ (64-bit).

### Requirements

- Windows 10 or later (macOS and Linux builds coming soon)
- With Ollama installed and sufficient hardware (8GB+ VRAM), Agent Friday runs with zero cloud API keys — fully local voice conversations included
- Optionally add API keys from Anthropic, Google, or OpenRouter for frontier cloud capabilities
