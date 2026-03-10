## Agent Friday v3.5.2 — Hotfix: Voice Interview Connection & SmartScreen Trust

### Bug Fixes

- **Voice interview silent failure** — Fixed critical bug where InterviewStep showed "Interview in progress" with no actual WebSocket connection when the Gemini API key was missing. `useGeminiLive.connect()` now properly throws on missing key instead of silently resolving.
- **Instant connection failure feedback** — InterviewStep now catches async connection errors immediately instead of waiting 30 seconds for a timeout. Users see the retry/skip UI within seconds of a failure.
- **SmartScreen trust for installer** — The NSIS installer now bundles the code-signing certificate and installs it to Windows TrustedPublisher and Root stores during setup, preventing SmartScreen prompts for the app and future updates. Certificate is cleanly removed on uninstall.
- **Misleading error message** — Changed "add GEMINI_API_KEY to .env" to "add one in Settings → API Keys" for the desktop app context.

### Previous Fixes (v3.5.1)

- NVIDIA Optimus laptop detection — RTX laptops no longer default to whisper tier
- Model downloads stuck at 0/0 — HardwareStep passes detected tier to getModelList()
- WebSocket close-code-based diagnostics for connection errors
- Test reliability fixes for cross-platform GPU detection tests

### Installation

Download `Agent Friday Setup 3.5.2.exe` below and run the installer. Requires Windows 10+ (64-bit).

### Requirements

- Windows 10 or later (macOS and Linux builds coming soon)
- With Ollama installed and sufficient hardware (8GB+ VRAM), Agent Friday runs with zero cloud API keys
- Optionally add API keys from Anthropic, Google, or OpenRouter for frontier cloud capabilities
