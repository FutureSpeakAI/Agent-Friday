# Agent Friday

**A sovereign personal AI built by [FutureSpeak.AI](https://futurespeak.ai)**

Agent Friday is a privacy-first, self-improving AI assistant that runs as a local Flask application backed by Anthropic Claude (cloud) and optionally Ollama (local). It features a holographic Three.js interface, a tiered data vault that keeps sensitive information off the cloud, and a skill evolution engine inspired by Microsoft SkillOpt and Karpathy's auto-research loop.

> Think Jarvis meets Hunter S. Thompson's editor.

---

## Architecture at a Glance

```
┌──────────────────────────────────────────────────────────────┐
│                    Holographic UI (Three.js)                  │
│         WebGL shaders · Audio reactivity · Process orbs       │
└──────────────────────┬───────────────────────────────────────┘
                       │ HTTP / WebSocket
┌──────────────────────▼───────────────────────────────────────┐
│                    Flask Server (server.py)                    │
│  Authentication · Tool-use agent · PII Shield · Context log   │
├───────────────────────────────────────────────────────────────┤
│                     Intelligence Pipeline                      │
│                                                               │
│  Context Pruner ──► Context Compressor ──► Model Router        │
│  (semantic RAG       (Headroom,              (cloud/local/     │
│   over own history)   60-95% savings)         smart routing)   │
│                                                               │
├───────────────────────────────────────────────────────────────┤
│                      Privacy Layer                             │
│                                                               │
│  Vault Access Control ──► PII Scrubber ──► HMAC Integrity      │
│  (TIER 1/2/3 gating)     (SSN, CC, phone,   (signed cLaws,    │
│                           address, email)     governance gate)  │
├───────────────────────────────────────────────────────────────┤
│                        Providers                               │
│                                                               │
│  Anthropic Claude ◄──────────────────────► Ollama (local)      │
│  (cloud, tool use)                          (private data,     │
│                                              vault access)     │
│  Google Gemini                                                 │
│  (TTS, creative,                                               │
│   voice mode)                                                  │
└───────────────────────────────────────────────────────────────┘
```

---

## Core Systems

### Sovereign Vault
The most critical subsystem. The Vault holds private data (financial, health, legal, contacts, encrypted PII) and enforces a non-negotiable rule: **vault content is readable by local models only.** Cloud providers never receive TIER_2 (private) or TIER_3 (sensitive) content. The policy engine lives in `vault_access.py`; routing enforcement lives in `model_router.py`.

- **TIER_1 (Public)** — Wiki articles, news, general docs. Any model.
- **TIER_2 (Private)** — Contacts, family, trust graph, personal notes. Local only; cloud gets a redacted placeholder.
- **TIER_3 (Sensitive)** — Financial records, health records, legal/custody data, SSNs, encrypted PII. Local only; cloud gets nothing.

### Privacy Shield
A runtime PII scrubber that processes every outbound message destined for a cloud model. Detects and redacts SSNs, credit card numbers, phone numbers, email addresses, street addresses, and configurable watchlist tokens. Supports bidirectional scrub/rehydrate so the model can reference PII by stable tags without ever seeing the raw values.

### Trust Graph
A scored relationship map with trust dimensions (competence, reliability, emotional safety, alignment). Loaded into context when a conversation references a known person. TIER_2 protected — cloud models see summaries, local models see full entries.

### Cognitive Memory
Long-term memory stored as timestamped entries in `~/.friday/memory/`. A personal wiki under `~/.friday/wiki/` organized by domain (identity, family, professional, health, legal, finance) serves as ground truth. Supports search, read, propose updates, and corrections.

### Personality Evolution
Personality evolves over time through maturity scores, trait weights, temperature adjustments, and session counts. The holographic UI reflects evolution through progressively complex visual structures: Genesis Lattice, Sacred Sphere, Shannon Network, Geodesic Cathedral, Lovelace Astrolabe, Von Neumann Tesseract.

### Epistemic Score
Tracks independence and reliability of reasoning — how well facts are distinguished from speculation, defer-vs-assert ratios, and confidence calibration.

### HMAC Integrity
All behavioral constraints (Asimov's cLaws, governance gates, privilege rings) are cryptographically signed with HMAC-SHA256 and verified before every action. The governance key lives locally and never leaves the machine.

---

## Intelligence Pipeline

Every message flows through three stages before reaching the model:

1. **Context Pruner** (`context_pruner.py`) — When conversations exceed a configurable threshold, semantic retrieval (RAG over the conversation's own history) selects the most relevant archived turns instead of naively truncating from the oldest. Uses `all-MiniLM-L6-v2` embeddings with content-hash caching.

2. **Context Compressor** (`context_compressor.py`) — Powered by [Headroom](https://github.com/chopratejas/headroom) by Tejas Chopra (Apache 2.0). Compresses tool outputs, JSON, code, and prose — 60-95% fewer tokens with preserved answer quality. Falls back gracefully if the Headroom native core isn't available.

3. **Model Router** (`model_router.py`) — Decides whether a request goes to Anthropic (cloud) or Ollama (local). Vault requests are always force-routed local regardless of routing mode.

---

## Model Routing

Three routing modes:

| Mode | Behavior |
|------|----------|
| `cloud_only` (default) | All requests go to Anthropic Claude. Vault requests are still force-routed local or refused. |
| `local_preferred` | Requests go to Ollama when a suitable local model is available. Falls back to cloud for tool use. |
| `smart` | Task-type-aware routing. Simple questions go to the smallest local model. Code/research go to the largest. Tool use and voice stay on cloud. |

The router classifies tasks by scanning the last user message for intent signals (code keywords, research keywords, message length, tool definitions). A `CostTracker` logs every request's provider, model, token count, and cost.

---

## Holographic UI

The interface is a holographic visualization built in Three.js with:

- **WebGL shaders** — Rotating geometric structures that evolve with personality maturity
- **Audio reactivity** — Web Audio API drives vertex displacement and color modulation
- **Process orbs** — Orbiting visualizations representing active background tasks
- **Progressive Web App** — Installable via manifest with service worker support

---

## Voice Mode

Real-time voice interaction powered by Google Gemini's multimodal Live API:

- WebSocket-based streaming audio pipeline
- Gemini `gemini-2.5-flash-preview-native-audio-dialog` model for natural speech
- Vault-gated: voice requests touching private data suggest switching to a local voice pipeline
- Configurable TTS via Gemini's text-to-speech endpoint

---

## Skills System

Friday can build and evolve its own skills:

- **Learnable Skills** — YAML files in `~/.friday/skills/` defining reusable workflows with trigger patterns, tool chains, prompt templates, and success criteria
- **SkillOpt Engine** (`skillopt_engine.py`) — Skills evolve through training epochs, validated against regression gates (must score within 5% of best), and refined by an auto-research loop
- **Karpathy Auto-Research** — When a skill's rolling composite score drops >10% below its best, the system generates hypotheses and proposes improvements

See [docs/SKILLS.md](docs/SKILLS.md) for the full skill system reference.

---

## Quick Start

```bash
# Clone
git clone https://github.com/FutureSpeakAI/friday-desktop.git
cd friday-desktop

# Install dependencies
pip install -r requirements.txt

# Set API keys (no keys are stored in the repo)
set ANTHROPIC_API_KEY=your-key-here
set GEMINI_API_KEY=your-key-here        # optional, for creative/voice

# Run
python server.py
```

Open `http://localhost:3000` in your browser. See [docs/INSTALLATION.md](docs/INSTALLATION.md) for the complete setup guide.

---

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture](docs/ARCHITECTURE.md) | System diagrams, pipeline flows, Mermaid charts |
| [API Reference](docs/API.md) | Every endpoint with methods, paths, request/response |
| [Installation](docs/INSTALLATION.md) | Fresh machine setup, prerequisites, troubleshooting |
| [Configuration](docs/CONFIGURATION.md) | All `settings.json` options |
| [Skills](docs/SKILLS.md) | Skill system, SkillOpt, auto-research |
| [SELF.md](SELF.md) | Friday's self-knowledge document |
| [Credits](CREDITS.md) | Third-party libraries and inspirations |

---

## Ethics: Asimov's cLaws

Friday's ethical framework — compiled Laws:

1. Shall not harm a human being or, through inaction, allow harm.
2. Shall obey user instructions except where they conflict with the First Law.
3. Shall protect its own integrity except where this conflicts with the First or Second Laws.
4. All behavioral constraints are cryptographically signed (HMAC-SHA256) and verified before every action.

A governance gate checks privilege rings before every action:

| Ring | Scope | Authorization |
|------|-------|---------------|
| 0 | Read-only file access, wiki queries | Always allowed |
| 1 | File writes, wiki updates, memory ops | Always allowed |
| 2 | Network access (web, email, calendar) | Requires auth |
| 3 | OS control (screenshot, mouse, packages) | Explicit user enablement |

---

## Credits

Created by **Stephen C. Webster** — journalist-turned-AI-architect, founder of [FutureSpeak.AI](https://futurespeak.ai).

Built with **Claude by Anthropic** as AI development partner.

See [CREDITS.md](CREDITS.md) for the full list of third-party libraries and inspirations.

---

## License

Proprietary. Copyright FutureSpeak.AI. All rights reserved.
