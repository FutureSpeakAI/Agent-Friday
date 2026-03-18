## Agent Friday v3.6.6 — Model Download Progress Fix

Fixes the root cause of "0 models installed" during onboarding: download progress was never reaching the UI.

### Bug Fixes

- **Download progress never updating (the real bug)** — The preload bridge strips the IPC event before calling renderer callbacks (`callback(data)` not `callback(event, data)`), but HardwareStep.tsx expected two arguments `(_event, progressData)`. The actual download data was landing in the `_event` parameter while `progressData` was always `undefined`, so `Array.isArray(undefined)` was always false and `setDownloads()` was never called. Fixed all three callbacks (onDownloadProgress, onComplete, onError) to match the preload's single-argument signature.

### Installation

Download `Agent Friday Setup 3.6.6.exe` below and run the installer. Requires Windows 10+ (64-bit).

### Requirements

- Windows 10 or later (macOS and Linux builds coming soon)
- With Ollama installed and sufficient hardware (8GB+ VRAM), Agent Friday runs with zero cloud API keys — fully local voice conversations included
- Optionally add API keys from Anthropic, Google, or OpenRouter for frontier cloud capabilities
