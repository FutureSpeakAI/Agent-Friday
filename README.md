# Agent Friday

[![CI](https://github.com/FutureSpeakAI/Agent-Friday/actions/workflows/tests.yml/badge.svg)](https://github.com/FutureSpeakAI/Agent-Friday/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://python.org)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

**A privacy-first personal AI desktop that runs locally and keeps your private
data on your machine — with optional cloud reasoning that only ever sees what
its gate lets through.**

> Agent Friday Desktop is the standalone desktop application in this repository.
> It is distinct from the [Asimov's Mind Claude Code plugin](https://futurespeak.ai/asimovs-mind),
> a separate product built for the Claude Code environment.

## Demo

[![Agent Friday — Live Demo](https://img.youtube.com/vi/JeAywoHd_jg/maxresdefault.jpg)](https://youtu.be/JeAywoHd_jg)

| | |
|---|---|
| [![Full Explainer](https://img.youtube.com/vi/uFKAQ3uz2U4/hqdefault.jpg)](https://youtu.be/uFKAQ3uz2U4) | [![Defeating Disinformation](https://img.youtube.com/vi/Do2ONuv_UbM/hqdefault.jpg)](https://youtu.be/Do2ONuv_UbM) |
| Full system explainer | Defeating disinformation |

## What Agent Friday is

Agent Friday is a self-improving personal AI served by a local Flask
application with a holographic Three.js interface. The application runs on your
machine and supports both local inference (Ollama, or its own managed model
seats) and optional cloud providers — Anthropic Claude, Google Gemini,
OpenRouter, and any OpenAI-compatible endpoint.

What makes it different is the data model. Everything you tell Friday is
classified into tiers. Public content can go to any model. Private and
sensitive content stays with local models; a cloud provider receives a
placeholder or nothing at all, enforced by a fail-closed egress gate that sits
in front of every outbound call. Think of a sharp newsroom editor with a
sovereign conscience and a zero-trust data policy.

## Core capabilities

- **Sovereign Vault** — TIER 1/2/3 access control with AES-256-GCM and Argon2id
  encryption at rest; private and sensitive material never leaves local models.
- **Egress gate** — a fail-closed sensitivity classifier in front of every cloud
  call, with [file grants](docs/user-guide/file-grants.md) as the deliberate,
  content-pinned, expiring exception you control.
- **Your files, on your terms** — searches Documents, Downloads, Desktop and its
  own creations; extracts real text from PDFs and Word documents; never searches
  the vault.
- **Universal tool loop** — one agentic loop shared by Anthropic, Gemini, Ollama
  and OpenAI-compatible providers, so local models use tools fully offline.
- **Voice** — on-device Whisper and Piper by default, an NVIDIA NeMo GPU tier,
  or Gemini Live cloud voice with barge-in and long-running sessions.
- **Knowledge galaxy** — your wiki as a navigable 3D graph, backed by an
  always-on structural tier and an opt-in local-only semantic tier.
- **Creation tools** — slide decks, multi-page websites, images, video and
  music through a deterministic template pipeline and pluggable creative
  providers.
- **Content pipeline** — compose, schedule and publish to eleven platforms with
  the harm floor and egress gate applied to every post.
- **Self-improvement** — weekly epistemic calibration, a nightly skill
  optimisation loop, and closed-loop learning from real usage.
- **Spend controls** — an alert-only budget by default and an opt-in hard stop
  that halts cloud spend when reached.

## Supported platforms

Agent Friday is a **Windows 10/11 product with an NVIDIA GPU** as its reference
platform. macOS and Linux run the server, the web UI, cloud providers, and local
chat through Ollama, but not the system tray, the local model residency layer,
GPU-aware seat planning, or OS-protected credential storage. Apple Silicon and
AMD GPUs are not supported by the local-model planner.

Requirements: Python 3.10+, 16 GB of system RAM, about 16 GB of free disk, and
Ollama for zero-key local chat. An NVIDIA GPU is required for local image
generation and managed model seats; 12 GB of VRAM is the configuration with
measured evidence behind it. Cloud keys are optional upgrades. Details and the
reasoning are in [Installation](docs/getting-started/installation.md).

## Quick start

**Windows, no Python needed:** download `AgentFriday-Setup-<version>.zip` from
the [latest release](https://github.com/FutureSpeakAI/Agent-Friday/releases/latest),
unzip it anywhere, and double-click **Install Agent Friday.cmd**.

**From source (Windows, macOS, Linux):**

```bash
git clone https://github.com/FutureSpeakAI/Agent-Friday.git
cd Agent-Friday
pip install -e .
friday models            # what your machine can run, and why not
friday models --install
friday                   # starts the server and opens http://localhost:3000
```

New here? The [tutorial](docs/getting-started/tutorial.md) gets you to a first
conversation in about twenty minutes. The full matrix of supported install
paths — and which artifacts are not supported — is in
[Installation](docs/getting-started/installation.md).

## Local and cloud intelligence

Friday can talk with no cloud key at all through a model on your own machine.
Nothing is bundled: the installer sizes a local model to your graphics card and
downloads one, or, on a small card, recommends a cloud key instead and
downloads nothing. Every model the planner offers calls tools natively. You can
change your mind at any time in **Settings → Intelligence**.

Cloud keys — Anthropic for sharper reasoning, Gemini for voice and creative
work, OpenRouter for hundreds of models through one key — are optional and are
added in **Settings → Providers**, where they are stored encrypted. The
model ladder, the VRAM arithmetic, and the honest limits of small models are
documented in [Installation](docs/getting-started/installation.md).

## Privacy and security

- Private and sensitive vault content never reaches a cloud provider through
  the normal call path. The gate fails closed: content it cannot classify is
  withheld.
- Which classifier layers are active depends on how you installed Friday. The
  boot log prints the real count, and the
  [threat model](docs/security/threat-model.md) states what each build runs.
- Credentials entered in the app are encrypted at rest. Credentials entered
  through the command-line wizard are written to local files in plaintext;
  [SECURITY.md](SECURITY.md) says exactly where each one lives.
- Four background connections leave a default install (a connectivity probe,
  news feeds, fonts, and MediaPipe bundles). Each is listed and can be disabled:
  [background network activity](docs/user-guide/background-network.md).

## Architecture

A Flask server (`src/agent_friday/`) exposes a REST and WebSocket API to a
single-page holographic UI (`index.html`). Requests flow through a model router
that chooses a seat (local or cloud), an egress gate that classifies the
assembled payload, and a universal tool loop that executes tools under a
ring-based governance gate. A residency layer plans and arbitrates GPU seats
for local models; a scheduler runs background work; a content pipeline,
creative engines, and a knowledge graph sit on top. The
[architecture overview](docs/architecture/overview.md) has the diagrams.

## Documentation

Start at [docs/README.md](docs/README.md), the documentation index. Key entries:

| Document | Purpose |
|---|---|
| [Tutorial](docs/getting-started/tutorial.md) | Zero to first conversation |
| [Installation](docs/getting-started/installation.md) | Supported install paths, prerequisites, troubleshooting |
| [Configuration](docs/user-guide/configuration.md) | Every setting and environment variable |
| [API reference](docs/reference/api.md) | Every endpoint |
| [Architecture](docs/architecture/overview.md) | Diagrams and pipeline flows |
| [Threat model](docs/security/threat-model.md) | Security guarantees and their limits |
| [Known issues](KNOWN_ISSUES.md) | What is broken, unverified, or deliberately limited |
| [Changelog](CHANGELOG.md) · [Release notes](RELEASE_NOTES.md) | What shipped |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, the required checks, and the
sensitive subsystems that get extra review. Please read the
[Code of Conduct](CODE_OF_CONDUCT.md) first. Security problems go to
[SECURITY.md](SECURITY.md), never to a public issue.

## License

MIT License. Copyright 2026 FutureSpeak.AI. See [LICENSE](LICENSE) and
[NOTICE](NOTICE) for third-party attribution.

Created by [FutureSpeak.AI](https://futurespeak.ai) · Built with Claude by
Anthropic as AI development partner.
