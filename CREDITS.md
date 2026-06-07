# Credits & Acknowledgments

Agent Friday Desktop is built by **Stephen C. Webster** — journalist-turned-AI-architect, former Editor-in-Chief of The Raw Story, former Senior Director at Aquent Studios, and founder of **FutureSpeak.AI**.

**Claude by Anthropic** serves as AI development partner. Friday's core intelligence runs on the Claude model family; the codebase itself was built collaboratively with Claude as a pair-programming partner.

This file aims to be exhaustive and honest. If a library is installed, an idea was borrowed, or a pattern was adapted, it belongs here. Where we learned from someone else's *mistakes* rather than their code, we say so too.

---

## Creator

**Stephen C. Webster** — Architect, designer, and sole human author of Agent Friday. Every design decision reflects his values: privacy by default, local-first data sovereignty, editorial independence, and the belief that AI should amplify human agency, not replace it.

**FutureSpeak.AI** — The company banner under which Friday is developed and operated.

---

## Inspirations & Borrowed Patterns

These are concepts, designs, and patterns we adapted. Most are not vendored code — they are ideas we learned from and reimplemented in Friday's own architecture.

### Headroom — Context Compression
- **Repository:** https://github.com/chopratejas/headroom
- **Author:** Tejas Chopra
- **License:** Apache 2.0
- **Used for:** Compressing tool outputs, JSON, code, and prose in conversation context before sending to LLMs
- **Impact:** 60-95% token reduction on tool outputs with preserved answer quality

In Friday's chat pipeline, Headroom is the compression layer beneath the semantic context pruner (`context_pruner.py`). The pruner selects *which* conversation turns to keep via embedding retrieval; Headroom then compresses the *content* of those turns. The two compound: prune selects, Headroom squeezes. Friday's wrapper lives in `context_compressor.py`, and savings are exposed at `GET /api/compression-stats`.

**Build note (native core).** Headroom's heavy transforms are implemented in a compiled Rust extension, `headroom._core` — a hard import with no Python fallback. The plain `pip install "headroom-ai[all]"` builds it from source, which requires a Rust toolchain and, on Windows, the MSVC build tools (`cl.exe`/`link.exe` from Visual Studio Build Tools). If the core isn't present, Headroom's pipeline falls back to passing messages through unchanged. Friday is built for exactly this: the wrapper imports lazily and degrades gracefully, so a missing or unbuildable core never breaks a chat — and full compression activates automatically, with no code change, the moment `headroom._core` becomes importable.

### Microsoft SkillOpt — Skill Evolution
- **Inspiration for:** The SkillOpt engine (`skillopt_engine.py`)
- **Concepts adopted:** Training epochs, validation gates, composite scoring, regression tolerance
- **Friday's implementation:** Skills evolve through versioned optimization cycles with a validation gate that prevents regressions (candidate must score within 5% of all-time best AND beat the immediate baseline)

### Andrej Karpathy — Auto-Research Loop
- **Inspiration for:** The auto-research loop within SkillOpt (`skillopt_engine.py`, `skill_capture.py`)
- **Concept:** Self-improving AI systems that investigate their own quality drift and propose fixes
- **Friday's implementation:** When a skill's 10-execution rolling mean drops >10% below its all-time best, the loop generates hypotheses (error patterns, latency spikes, quality drift), proposes skill edits, and hands candidates to the training pipeline for validation

### Adrian (secureagentics/adrian) — Behavioral Anomaly Detection
- **Repository:** https://github.com/secureagentics/adrian
- **Inspiration for:** The behavioral monitor (`behavioral_monitor.py`)
- **Concept:** Watching an agent's tool-use loop for behavioral anomalies (scope drift, privilege escalation, exfiltration patterns, repetition/brute-force)
- **Friday's adaptation:** Where Adrian sits *outside* an agent as an external watcher, Friday implements the same scoring *internally* as governance — it scores its own tool-use loops against the user's stated intent ("remit"), produces four risk sub-scores plus a cross-session correlation pass, and scales its response (log → warn → block) with the composite score

### Anthropic — Agent Skills / `SKILL.md` Format & `agentskills.io`
- **Reference:** https://agentskills.io · Anthropic Agent Skills convention
- **Used for:** The portable skill format in `skill_registry.py` — YAML frontmatter plus a markdown body in a `SKILL.md` "skill folder"
- **Why credited:** Friday's skill registry follows this open convention so skills are shareable as folders/zips and importable from other agents. We adopted the standard rather than inventing a bespoke one.

### Hermes Agent (Nous Research) — Project Context Injection
- **Repository:** https://github.com/NousResearch/hermes-agent
- **License:** MIT
- **Inspiration for:** Project-directory context-file injection — drop a `.friday-context.md` or `AGENTS.md` in a project directory and Friday auto-injects it when messaging from that directory (`server.py` trajectory/context management, first-run setup wizard)
- **Also:** Friday's portable skill registry interoperates with Hermes-style skill bundles; Hermes is one of the two systems Friday is benchmarked against in `docs/COMPETITIVE_MATRIX.md`

### OpenClaw — Skill Interop & Security Lessons (Negative Credit)
- **Repository:** https://github.com/openclaw/openclaw
- **Used for:** `skill_registry.py` normalizes and imports OpenClaw-style skill packages alongside the `agentskills.io` format
- **Negative credit:** Several of Friday's security postures (per-action zero-trust gating, PII scrub before any cloud hop, re-gate on provider switch) were sharpened by studying where OpenClaw-class agents leave gaps. We learned from their failure modes as much as their features. See `docs/COMPETITIVE_MATRIX.md` and `docs/EXEC_SUMMARY.md`.

### addyosmani/agent-skills — Agent Best Practices
- **Repository:** https://github.com/addyosmani/agent-skills
- **License:** MIT
- **Used for:** `AGENT_BEST_PRACTICES.md` — the six-phase task lifecycle and engineering discipline that every task prompt references — is derived from this work (plus hard-won lessons from production failures)

### Zero-Trust Agent Security Architecture
- **Inspiration for:** Friday's per-action authorization model (`vault_access.py`, `dynamic_rings.py`, `server.py` continuous vault authorization, `cognitive_memory.py` quarantine-by-source)
- **Concept:** Zero-trust as articulated for AI agents — authorize *per action*, not per session; verify every privilege elevation; treat memory and tool calls as untrusted until checked
- **Friday's implementation:** Dynamic privilege rings (0–3, single-call elevation), HMAC + Ed25519 signed governance, and a per-tool-call gate that re-evaluates on every action and on provider switch

### sentence-transformers `all-MiniLM-L6-v2` — Embedding Model
- **Model card:** https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2
- **Authors:** UKP Lab / Nils Reimers et al.
- **License:** Apache 2.0
- **Used for:** The actual embedding weights behind semantic context pruning (`context_pruner.py`) — distinct from the `sentence-transformers` library that loads them. Downloaded (~80MB) on first use.

---

## Core Python Dependencies

From `requirements.txt`.

| Library | License | Purpose |
|---------|---------|---------|
| [Flask](https://flask.palletsprojects.com/) | BSD-3 | Web server and API framework |
| [Anthropic SDK](https://github.com/anthropics/anthropic-sdk-python) | MIT | Claude API client |
| [google-genai](https://github.com/googleapis/python-genai) | Apache 2.0 | Gemini API (TTS, creative, voice) |
| [google-api-python-client](https://github.com/googleapis/google-api-python-client) | Apache 2.0 | Live Gmail + Calendar reads |
| [google-auth-oauthlib](https://github.com/googleapis/google-auth-library-python-oauthlib) | Apache 2.0 | OAuth flow for read-only Gmail/Calendar scopes |
| [cryptography](https://github.com/pyca/cryptography) | Apache-2.0 / BSD-3 | Sovereign Vault AES-256-GCM + Argon2id (`vault_crypto.py`) |
| [PyNaCl](https://github.com/pyca/pynacl) | Apache 2.0 | Ed25519 attestation keys — Integrity Manifest + Source Trust Federation |
| [sentence-transformers](https://www.sbert.net/) | Apache 2.0 | Embeddings for semantic context pruning |
| [headroom-ai](https://github.com/chopratejas/headroom) | Apache 2.0 | Context compression |
| [feedparser](https://github.com/kurtmckee/feedparser) | BSD-2 | RSS-based news fetcher (`server.py` `_rss_results`) |
| [Rich](https://github.com/Textualize/rich) | MIT | Terminal formatting |
| [Colorama](https://github.com/tartley/colorama) | BSD-3 | Windows terminal colors |
| [PyAutoGUI](https://github.com/asweigart/pyautogui) | BSD-3 | OS control (Ring 3 computer control) |
| [pynput](https://github.com/moses-palmer/pynput) | LGPL-3.0 | Global Ctrl+Shift+Q kill-switch hotkey for Computer Control |
| [BeautifulSoup4](https://www.crummy.com/software/BeautifulSoup/) | MIT | HTML parsing for web tools |
| [Requests](https://requests.readthedocs.io/) | Apache 2.0 | HTTP client |
| [PyYAML](https://pyyaml.org/) | MIT | Skill file parsing |
| [tzdata](https://github.com/python/tzdata) | Apache 2.0 | IANA tz database for `zoneinfo` on Windows (Central-time scheduler) |
| [NumPy](https://numpy.org/) | BSD-3 | Embedding similarity computation |
| [pystray](https://github.com/moses-palmer/pystray) | GPL-3.0/LGPL-3.0 | Windows system tray (`friday_tray.py`) |
| [Pillow](https://python-pillow.org/) | HPND | Image handling for tray icon |
| [flask-sock](https://github.com/miguelgrinberg/flask-sock) | MIT | WebSocket support for voice mode |

---

## Node Dependencies (Asimov's Mind / `friday-core` MCP server)

From `asimovs-mind/mcp/friday-core/package.json`.

| Package | License | Purpose |
|---------|---------|---------|
| [@modelcontextprotocol/sdk](https://github.com/modelcontextprotocol/typescript-sdk) | MIT | MCP server runtime — registers the subsystems and tools |
| [libsodium-wrappers-sumo](https://github.com/jedisct1/libsodium.js) | ISC | All vault crypto primitives (AES-256-GCM, Argon2id, Ed25519) in `core/crypto.js` |
| [ws](https://github.com/websockets/ws) | MIT | P2P WebSocket transport (`subsystems/p2p/transport.js`) |
| [ESLint](https://github.com/eslint/eslint) (dev) | MIT | Linting |

**Testing (dev only).** The repo's `package.json` pulls [@playwright/test](https://github.com/microsoft/playwright) (Apache 2.0) for browser-driven end-to-end tests. It ships no runtime functionality.

---

## Frontend

The holographic UI is a single-page app that loads its libraries from public CDNs at runtime (see the `<script>` tags in `index.html`). None are vendored into the repo.

| Technology | License | Purpose |
|------------|---------|---------|
| [React + ReactDOM 18](https://react.dev/) | MIT | UI component layer |
| [Babel Standalone](https://babeljs.io/) | MIT | In-browser JSX transform |
| [Three.js (r128)](https://threejs.org/) | MIT | Holographic 3D visualization (scene, 13 evolution structures) |
| Three.js postprocessing addons (EffectComposer, RenderPass, ShaderPass, UnrealBloomPass, CopyShader, LuminosityHighPassShader) | MIT | Bloom and post-processing pipeline |
| [marked](https://github.com/markedjs/marked) | MIT | Markdown rendering in chat |
| [highlight.js](https://github.com/highlightjs/highlight.js) | BSD-3 | Code syntax highlighting |
| [MediaPipe](https://github.com/google-ai-edge/mediapipe) (camera_utils, face_detection, hands) | Apache 2.0 | Optional camera-based face/hand gesture input |
| WebGL Shaders | — | Geometric structure rendering |
| Web Audio API | — | Audio reactivity and voice input |
| Progressive Web App | — | Installable manifest + service worker |

**Voice activity detection.** Friday's voice mode does **not** bundle a local VAD (e.g. Silero). Voice activity detection is performed by **Gemini's server-side VAD** over the Live API (`server.py`, `index.html`). Documented here to avoid a false attribution.

---

## Fonts

| Font | Usage | License |
|------|-------|---------|
| [Orbitron](https://fonts.google.com/specimen/Orbitron) | Headings and HUD elements | OFL |
| [Inter](https://fonts.google.com/specimen/Inter) | Body text | OFL |
| [JetBrains Mono](https://fonts.google.com/specimen/JetBrains+Mono) | Code and monospace elements | OFL |

---

## Design Influences

- **Isaac Asimov's Three Laws of Robotics** — the ethical framework (Asimov's cLaws) that governs all agent behavior
- **Jarvis (Iron Man)** — the personal AI assistant archetype: contextual, loyal, capable
- **Cyberpunk / Vaporwave aesthetics** — the holographic UI design language
- **Sci-fi HUD interfaces** — geometric line-art for the generated holographic icons (`generate_holo_icons.py`)

---

## Acknowledgments

- The **Anthropic** team for Claude, the Claude API, and the Agent Skills (`SKILL.md`) convention
- **Tejas Chopra** for Headroom — the compression engine that makes long conversations viable
- The **Microsoft Research** team behind SkillOpt for the skill evolution framework
- **Andrej Karpathy** for articulating the auto-research loop concept
- **secureagentics** for **Adrian**, which inspired Friday's internal behavioral monitor
- **Nous Research** (Hermes Agent) and **OpenClaw** — studied closely for skill interop and security posture; Friday is sharper for it
- **Addy Osmani** for the agent-skills best-practices that shaped Friday's engineering discipline
- The **UKP Lab / Nils Reimers** for the `all-MiniLM-L6-v2` embedding model
- The open-source community behind Flask, sentence-transformers, Three.js, React, and every dependency listed above
