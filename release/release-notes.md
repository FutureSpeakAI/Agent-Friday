## Agent Friday v3.7.2 — Conversational Agent Identity

Moves agent personality configuration from a form into the voice interview itself.

### Changes

- **Agent identity discovered through conversation** — Removed the Environment step (form-based name/gender/voice selection). The voice interview now naturally asks the user what they'd like to name their agent, their preferred voice gender, and voice character. This creates a genuine "Her"-style moment: the interviewer uses a stock voice, then after the conversation, the agent's actual voice appears for the first time with the personality the user described.
- **Streamlined onboarding** — 7 steps instead of 8: Awakening, Mission, Hardware, Privacy, API Keys, Interview, Reveal.

### Installation

Download `Agent Friday Setup 3.7.2.exe` below and run the installer. Requires Windows 10+ (64-bit).

### Requirements

- **Ollama** (free, no account) — Download from [ollama.com](https://ollama.com/download) for local AI
- Windows 10 or later (macOS and Linux builds coming soon)
- With Ollama installed and 8GB+ VRAM, Agent Friday runs fully local with zero cloud API keys
- Optionally add API keys from Anthropic, Google, or OpenRouter for frontier cloud capabilities
