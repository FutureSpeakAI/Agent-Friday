## Agent Friday v3.6.2 — Onboarding Tool Scoping

Fixes Gemini Live connection failures during onboarding caused by sending 200-400+ tool declarations in the WebSocket setup message.

### Bug Fixes

- **Onboarding tool scoping** — Voice interview connects with only 4 onboarding tools instead of the full 200-400+ toolkit, eliminating payload bloat that caused connection failures
- **Close code 1009 handling** — "Message Too Big" WebSocket rejections now produce a clear error message
- **Reconnect preserves onboarding mode** — Auto-reconnect paths carry the onboarding flag to prevent tool re-bloat during interviews
- **Variable scoping fix** — Dynamic tool declarations properly scoped to prevent runtime errors in onboarding mode

### Installation

Download `Agent Friday Setup 3.6.2.exe` below and run the installer. Requires Windows 10+ (64-bit).

### Requirements

- Windows 10 or later (macOS and Linux builds coming soon)
- With Ollama installed and sufficient hardware (8GB+ VRAM), Agent Friday runs with zero cloud API keys — fully local voice conversations included
- Optionally add API keys from Anthropic, Google, or OpenRouter for frontier cloud capabilities
