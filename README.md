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

**Easiest (Windows):** download the `AgentFriday-Setup-*.zip` attached to the [latest GitHub release](https://github.com/FutureSpeakAI/Agent-Friday/releases/latest), unzip it anywhere, and double-click **Install Agent Friday.cmd**. No Python, Git or terminal needed.

> **The `AgentFriday.exe` on the releases page is not a current build.** It was
> built on 6 July 2026 and predates every egress-gate fix made since — see
> [docs/INSTALLATION.md](docs/INSTALLATION.md#option-0-download-the-packaged-app-no-python-required)
> for what that means. Use the installer zip, or run from source.

Or install from source:

```bash
git clone https://github.com/FutureSpeakAI/Agent-Friday.git
cd Agent-Friday
pip install -e .
friday models    # what your machine can run — and what it can't, with the reasons
friday models --install
friday           # launches the server, opens http://localhost:3000
```

**New here? [docs/TUTORIAL.md](docs/TUTORIAL.md) gets you to a first working
conversation in about twenty minutes and then stops.**

**Install from a clone, not from a wheel.** `data/` and `skills/` live at the
repository root and are not packaged, so a wheel install gets a career pipeline
that cannot work. This is a known structural issue rather than an oversight —
resolving it means deciding what the skills system *is*, which is deliberately
not being answered inside a bug fix. See
[KNOWN_ISSUES.md](KNOWN_ISSUES.md) §3.

**No API key? No problem — but which way you run her is now a question you get asked.**
Friday can talk with no cloud key at all, through a model running on your own machine via
Ollama. Nothing is bundled: the model is downloaded during setup, and the Windows installer
asks first and sizes the answer to your graphics card. On a card with room it downloads one.
On a small card it recommends the Claude key instead and downloads nothing — a model
squeezed onto a small card is slower than the key and can stall on long answers. Either way
you can change your mind later in **Settings → Intelligence**.

Which model you get is decided by your hardware, not by a default in a config file: the
planner takes the largest brain that fits your card, from `qwen3:4b` (2.5 GB) up to
`gemma4:12b` (7.5 GB, measured at 49–54 tok/s on a 12 GB card). Run `friday models` to see
what your machine can hold, and the arithmetic behind anything it refuses.

**One honest limit at the bottom of the range.** `gemma3:4b` has no native tool calling,
and Friday does **not** gate the local path on that capability — it passes the tool registry
to whatever model is seated, so a model that cannot call tools can still *narrate* a call it
never made. `tool_integrity.find_pseudo_toolcalls` catches that after the fact rather than
preventing it. `qwen3:4b`, `qwen3:8b` and `gemma4:12b` all do have native tool calling and
use tools fully offline, with no key. See [KNOWN_ISSUES.md](KNOWN_ISSUES.md) §3.

On first run, Friday greets you by voice and walks you through setup. Cloud keys are
*optional upgrades* for sharper reasoning, image/video generation, and richer voice — add
them any time in Settings (creative/voice degrade gracefully with a clear notice until you
do).

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
- **Layered Safety Classifier** — Fail-closed egress gate with sensitivity classifier as single source of truth; HMAC-SHA256 signed behavioral constraints (Asimov's cLaws). The classifier declares four layers and **tells you at every boot how many are actually running** — usually two in the packaged `.exe`, three from source. See [THREAT_MODEL.md](THREAT_MODEL.md) for what ships, and for why Presidio NER was evaluated and deliberately left unenforced.
- **Your files, on your terms** — Friday can find and read local documents: `search_files` searches Documents, Downloads, Desktop and her own creations (by name, or inside extractable text), and she extracts **real text from PDFs and `.docx`** rather than handing a model raw bytes. She never searches the vault, and when a PDF has no text layer she says so instead of guessing.
- **File Grants — a permission model, not a switch** — The egress gate is fail-closed, which makes Friday least useful on exactly the documents you most want help with. So you can grant a specific file to the cloud, deliberately and on the record. Grants are **content-pinned by SHA-256** (edit the file and the grant goes stale); folder and glob grants **must expire within 30 days**; a **deny always beats a grant**; **no model on any surface can create one** — not chat, not voice, not a prompt-injected document; and the append-only HMAC'd ledger at `~/.friday/privacy/file_grants.jsonl` is built so that corrupting it can only ever *tighten* what may be sent. Full design in [docs/FILE_GRANTS.md](docs/FILE_GRANTS.md).
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
- **~16 GB free disk** — roughly 5–10 GB consumed (a local brain of 2.5–7.5 GB depending
  on your card, embeddings ~90 MB, venv and dependencies ~2–3 GB) against a 10 GB
  free-space floor the planner enforces. Choosing the cloud key instead of a local model
  takes the brain out of that figure entirely.
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
