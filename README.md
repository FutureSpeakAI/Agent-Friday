# Agent Friday

[![CI](https://github.com/FutureSpeakAI/Agent-Friday/actions/workflows/tests.yml/badge.svg)](https://github.com/FutureSpeakAI/Agent-Friday/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://python.org)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

> **Note:** Agent Friday Desktop is the standalone desktop application (this repo). It is distinct from the [Asimov's Mind Claude Code plugin](https://futurespeak.ai/asimovs-mind), which is a separate product built for the Claude Code environment.

---

## What is this?

**Agent Friday** is a privacy-first, self-improving personal AI that runs entirely on your machine. It features a tiered data vault that keeps sensitive information off the cloud, a holographic Three.js interface, a layered content-safety classifier, and a skill-evolution engine — all served by a local Flask app backed by Anthropic Claude, Google Gemini, and/or Ollama.

Think Jarvis with a sharp newsroom editor's instincts, a sovereign conscience, and a zero-trust data policy.

---

## Demo

[![Agent Friday — Live Demo](https://img.youtube.com/vi/JeAywoHd_jg/maxresdefault.jpg)](https://youtu.be/JeAywoHd_jg)

| | |
|---|---|
| [![Full Explainer](https://img.youtube.com/vi/uFKAQ3uz2U4/hqdefault.jpg)](https://youtu.be/uFKAQ3uz2U4) | [![Defeating Disinformation](https://img.youtube.com/vi/Do2ONuv_UbM/hqdefault.jpg)](https://youtu.be/Do2ONuv_UbM) |
| Full system explainer | Defeating disinformation |

---

## Quick Start

```bash
git clone https://github.com/FutureSpeakAI/Agent-Friday.git
cd Agent-Friday
pip install -e .
friday doctor    # verify API keys, Python version, Ollama, disk
friday start     # launches the server and opens http://localhost:3000
```

**No API key?** Friday boots in demo mode — explore the full UI first, connect a provider in Settings whenever you're ready.

Set your keys (no keys are stored in the repo):

```bash
# Linux / macOS
export ANTHROPIC_API_KEY=your-key     # core reasoning
export GEMINI_API_KEY=your-key        # voice + creative (optional)
export OPENAI_API_KEY=your-key        # OpenRouter / any /v1 endpoint (optional)
```

```powershell
# Windows PowerShell
$env:ANTHROPIC_API_KEY = "your-key"
$env:GEMINI_API_KEY    = "your-key"
$env:OPENAI_API_KEY    = "your-key"
```

See [docs/INSTALLATION.md](docs/INSTALLATION.md) for the complete setup guide, including the one-line shell installer, GPU setup, Ollama, and the Windows SmartScreen bypass.

---

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture](docs/ARCHITECTURE.md) | System diagrams, pipeline flows, Mermaid charts |
| [API Reference](docs/API.md) | Every endpoint with methods, paths, request/response |
| [Installation](docs/INSTALLATION.md) | Fresh-machine setup, prerequisites, troubleshooting |
| [Configuration](docs/CONFIGURATION.md) | All `settings.json` options |
| [Skills](docs/SKILLS.md) | Skill system, SkillOpt, auto-research loop |
| [SELF.md](SELF.md) | Friday's self-knowledge document |
| [Credits](CREDITS.md) | Third-party libraries and inspirations |
| [Threat Model](THREAT_MODEL.md) | Security posture, trust boundaries, known gaps |

---

## Key Features

- **Sovereign Vault** — TIER 1/2/3 access control; TIER_2 (private) and TIER_3 (sensitive) data never leave the local model. AES-256-GCM + Argon2id at rest.
- **Layered Safety Classifier** — Fail-closed egress gate with sensitivity classifier as single source of truth; HMAC-SHA256 signed behavioral constraints (Asimov's cLaws).
- **Holographic UI** — Three.js WebGL interface with audio reactivity, process orbs, and personality evolution visualized as progressively complex geometric structures.
- **Voice Mode** — Real-time WebSocket audio pipeline via Google Gemini Live; local Piper/Whisper fallback when offline.
- **Universal Tool Loop** — Anthropic, Gemini, Ollama (gemma4 native tool calling), and OpenAI-compatible providers share a single agentic tool loop.
- **Creator Economy Layer** — Music (Lyria 3), video (Veo), image generation (Nano Banana Pro/2), provenance (C2PA), federation (Ed25519 identity, X25519+ChaCha20-Poly1305 transport), marketplace, and economy engine.
- **Self-Improvement** — Weekly epistemic calibration, SkillOpt nightly loop, closed-loop learning from real usage.
- **Defederation & Moderation** — Asimov-governed defederation protocol, H1–H4 harm floor, community content-policy packs.

---

## Requirements

- Python 3.10+
- An Anthropic API key — for live Claude reasoning ([get one](https://console.anthropic.com/settings/keys))
- Google Gemini API key — for voice and creative features ([get one](https://aistudio.google.com/apikey)) — *optional*
- Ollama — for local models and vault-private queries ([ollama.com](https://ollama.com)) — *optional*

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Issues and pull requests are welcome — please read the [Code of Conduct](CODE_OF_CONDUCT.md) first.

---

## License

MIT License. Copyright 2026 FutureSpeak.AI. See [LICENSE](LICENSE).

Created by **[FutureSpeak.AI](https://futurespeak.ai)** · Built with **Claude by Anthropic** as AI development partner.
