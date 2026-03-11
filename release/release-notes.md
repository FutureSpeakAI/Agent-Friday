## Agent Friday v3.6.1 — Cloud-Only UX & Key Validation

Improves the experience for users on lightweight hardware (Surface Pro, integrated graphics) who rely entirely on cloud APIs.

### Bug Fixes

- **Whisper tier "Cloud Mode" card** — Devices with 0 available VRAM (e.g. Surface Pro with Intel iGPU) now see a clear "Cloud Mode" explanation instead of an empty model list during onboarding
- **API key pre-validation** — Gemini, Anthropic, and OpenRouter keys are validated before saving, both in Settings and during onboarding; invalid keys show immediate error messages
- **Voice interview staged progress** — Connection status cycles through progress stages instead of showing a static message for up to 30 seconds
- **Faster auth failure detection** — Connection timeout reduced from 30s to 15s; auth failures produce specific error messages
- **Better failure hints** — Failed voice interview suggests checking the API key in Settings

### Installation

Download `Agent Friday Setup 3.6.1.exe` below and run the installer. Requires Windows 10+ (64-bit).

### Requirements

- Windows 10 or later (macOS and Linux builds coming soon)
- With Ollama installed and sufficient hardware (8GB+ VRAM), Agent Friday runs with zero cloud API keys — fully local voice conversations included
- Optionally add API keys from Anthropic, Google, or OpenRouter for frontier cloud capabilities
