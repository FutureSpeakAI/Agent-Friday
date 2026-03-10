## Agent Friday v3.5.1 — Hotfix: Optimus GPU Detection & WebSocket Diagnostics

### Bug Fixes

- **NVIDIA Optimus laptop detection** — Hardware profiler now always tries nvidia-smi on Windows, even when Chromium reports an AMD/Intel iGPU as primary. Fixes RTX laptops incorrectly defaulting to whisper tier.
- **Model downloads stuck at 0/0** — HardwareStep now passes the detected tier to getModelList(), fixing the empty model list during onboarding.
- **WebSocket error diagnostics** — Replaced misleading "API key may be invalid" error with close-code-based diagnostics: network/API key issues (1006), auth rejection (1008), and service unavailable (1001).
- **Test reliability** — Fixed execFile mock signature (3-arg → 4-arg) and added process.platform mock so GPU detection tests pass on all platforms.

### Installation

Download `Agent Friday Setup 3.5.1.exe` below and run the installer. Requires Windows 10+ (64-bit).

### Requirements

- Windows 10 or later (macOS and Linux builds coming soon)
- With Ollama installed and sufficient hardware (8GB+ VRAM), Agent Friday runs with zero cloud API keys
- Optionally add API keys from Anthropic, Google, or OpenRouter for frontier cloud capabilities
