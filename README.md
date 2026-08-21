# Agent Friday

[![CI](https://github.com/FutureSpeakAI/Agent-Friday/actions/workflows/tests.yml/badge.svg)](https://github.com/FutureSpeakAI/Agent-Friday/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://python.org)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

> **Note:** Agent Friday Desktop is the standalone desktop application (this repo). It is distinct from the [Asimov's Mind Claude Code plugin](https://futurespeak.ai/asimovs-mind), which is a separate product built for the Claude Code environment.

---

## What is this?

**Agent Friday** is a privacy-first, self-improving personal AI that runs entirely on your machine. It features a tiered data vault that keeps sensitive information off the cloud, a holographic Three.js interface, a layered content-safety classifier, and a skill-evolution engine — all served by a local Flask app backed by Anthropic Claude, Google Gemini, Ollama, or any OpenAI-compatible provider (OpenRouter first-class, ten providers built in).

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

**Easiest (Windows):** download `AgentFriday.exe` from the [latest GitHub release](https://github.com/FutureSpeakAI/Agent-Friday/releases/latest) and run it — no Python required. Or install from source:

```bash
git clone https://github.com/FutureSpeakAI/Agent-Friday.git
cd Agent-Friday
pip install -e .
friday doctor    # verify Python, Ollama, the bundled Gemma model, keys, disk
friday           # launches the server, runs voice-first onboarding, opens http://localhost:3000
```

**No API key? No problem.** Agent Friday ships a bundled local model — **`gemma3:4b`**
(Google's open Gemma 3 4B, ~3.3 GB on disk, needs **16 GB system RAM**). The installers
auto-install Ollama and pull it, so **chat works fully offline with zero cloud keys**.
At that floor, local chat works but local *tool use* does not — `gemma3:4b` lacks native
tool calling, so Friday disables tools for the turn rather than let the model narrate
calls it never made. On first run, Friday
greets you by voice and walks you through setup. Cloud keys are *optional upgrades* for
sharper reasoning, image/video generation, and richer voice — add them any time in
Settings (creative/voice degrade gracefully with a clear notice until you do).

> Bigger machine? Upgrade the local brain: `ollama pull gemma3:12b` (or `gemma3:27b`)
> and set it in Settings → Models.

**Adding cloud keys (optional).** The recommended way is `friday setup` — it
stores each key **encrypted** via the credential store (DPAPI/AES-256-GCM),
never in plaintext, never in the repo:

```bash
friday setup        # interactive: keys, model, vault passphrase — all encrypted at rest
```

Or add them any time in the running app under **Settings → Providers**. Both
paths write to the encrypted store. Environment variables (`ANTHROPIC_API_KEY`,
`GEMINI_API_KEY`, `OPENAI_API_KEY`) still work for CI or advanced setups, but a
plaintext key file (e.g. a hand-edited `start.bat`) is **not** the recommended
pattern — prefer `friday setup`.

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
- **Knowledge Galaxy** — Your wiki as a navigable 3D galaxy: pages are stars, links and title-mentions are filaments, wiki sections cluster into glowing constellations. Fly through it, hover to trace connections, double-click a star to open the page. Behind it, a two-tier knowledge graph: an always-on structural tier (no LLM, instant, works offline) plus an opt-in GraphRAG semantic tier — **local-only by default**, with sensitive-derived data vault-encrypted at rest and an adversarial egress test suite guarding the cloud boundary.
- **Voice Mode** — Real-time WebSocket audio pipeline; on-device Whisper + Piper by default (Tier-1 CPU, Tier-2 NeMo GPU), or optional Google Gemini Live cloud voice with barge-in interruption and auto-reconnecting hours-long sessions.
- **Universal Tool Loop** — Anthropic, Gemini, Ollama (Gemma native tool calling), and OpenAI-compatible providers share a single agentic tool loop.
- **Creator Economy Layer** — Music (Lyria 3), video (Veo), image generation (Nano Banana Pro/2), provenance (C2PA), federation (Ed25519 identity, X25519+ChaCha20-Poly1305 transport), marketplace, and economy engine.
- **Creation Tools** — Ask in chat or voice for a slide deck (`create_presentation`) or a multi-page website (`create_website`); a deterministic template renders a polished, self-contained HTML artifact into the Studio gallery — keyboard-nav decks with speaker notes and print-to-PDF, responsive sites that deploy anywhere.
- **Content Pipeline** — A full social-media publishing system: compose platform-native posts from your Studio creations (in your voice, with per-platform previews), schedule them on a calendar with learned optimal times, and Friday publishes autonomously via the internal scheduler — LinkedIn, X, Instagram, YouTube, TikTok, Bluesky, Mastodon, Reddit, Substack/Medium (assisted), and the Friday Federation. Every post passes the harm floor and egress gate (hold-for-review, never silent redaction), carries Ed25519 provenance, and feeds local-only analytics that learn what works.
- **Self-Improvement** — Weekly epistemic calibration, SkillOpt nightly loop, closed-loop learning from real usage.
- **Defederation & Moderation** — Asimov-governed defederation protocol, H1–H4 harm floor, community content-policy packs.

---

## Requirements

- **Python 3.10+**
- **16 GB system RAM.** This is a floor, not a recommendation. Friday's own budget rule
  reserves 6 GB for the OS and takes 75% of the remainder, so at 8 GB the arithmetic
  resolves to zero available and **every model seat is refused**. Earlier versions of this
  README claimed 8 GB; that was wrong and the code always disagreed with it.
- **~16 GB free disk** — roughly 6 GB consumed (`gemma3:4b` ~3.3 GB, embeddings ~90 MB,
  venv and dependencies ~2–3 GB) against a 10 GB free-space floor the planner enforces.
- **Ollama** — effectively required, not optional. It is the zero-key default and the only
  local inference path on macOS and Linux ([ollama.com](https://ollama.com), auto-installed
  by the installers).
- **NVIDIA GPU** — required for local image generation and for the residency layer's
  managed model seats. 12 GB VRAM is the only configuration with measured evidence behind
  it. AMD GPUs are not detected at all (`nvidia-smi` is the only probe). There is a
  CPU-only path for chat, and its throughput is unmeasured.
- **Anthropic API key** — *optional upgrade* for live Claude reasoning ([get one](https://console.anthropic.com/settings/keys))
- **Google Gemini API key** — *optional* for voice and creative features ([get one](https://aistudio.google.com/apikey))

### Platform support, stated plainly

Agent Friday runs on **Windows 10/11 with an NVIDIA GPU**. macOS and Linux can run the
server, the web UI, cloud providers, and local chat through Ollama — but the system tray,
the local model residency layer (llama-server seats), GPU-aware seat planning, and
OS-protected credential storage are **Windows-only today**. On other platforms credentials
fall back to plaintext unless you set `FRIDAY_PASSWORD`, and Apple Silicon is explicitly
refused by the residency planner because no MLX or Metal backend exists in the tree.

A clearly-scoped Windows product is more useful than a vaguely cross-platform one, so that
is what this is. See [KNOWN_ISSUES.md](KNOWN_ISSUES.md) for everything else that is broken
or unverified.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Issues and pull requests are welcome — please read the [Code of Conduct](CODE_OF_CONDUCT.md) first.

---

## License

MIT License. Copyright 2026 FutureSpeak.AI. See [LICENSE](LICENSE).

Created by **[FutureSpeak.AI](https://futurespeak.ai)** · Built with **Claude by Anthropic** as AI development partner.
