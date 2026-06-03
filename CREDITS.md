# Credits & Acknowledgments

Agent Friday Desktop is built by **Stephen C. Webster** — journalist-turned-AI-architect, former Editor-in-Chief of The Raw Story, former Senior Director at Aquent Studios, and founder of **FutureSpeak.AI**.

**Claude by Anthropic** serves as AI development partner. Friday's core intelligence runs on the Claude model family; the codebase itself was built collaboratively with Claude as a pair-programming partner.

---

## Creator

**Stephen C. Webster** — Architect, designer, and sole human author of Agent Friday. Every design decision reflects his values: privacy by default, local-first data sovereignty, editorial independence, and the belief that AI should amplify human agency, not replace it.

**FutureSpeak.AI** — The company banner under which Friday is developed and operated.

---

## Third-Party Libraries & Inspirations

### Headroom — Context Compression
- **Repository:** https://github.com/chopratejas/headroom
- **Author:** Tejas Chopra
- **License:** Apache 2.0
- **Used for:** Compressing tool outputs, JSON, code, and prose in conversation context before sending to LLMs
- **Impact:** 60-95% token reduction on tool outputs with preserved answer quality

In Friday's chat pipeline, Headroom is the compression layer beneath the semantic context pruner (`context_pruner.py`). The pruner selects *which* conversation turns to keep via embedding retrieval; Headroom then compresses the *content* of those turns. The two compound: prune selects, Headroom squeezes. Friday's wrapper lives in `context_compressor.py`, and savings are exposed at `GET /api/compression-stats`.

**Build note (native core).** Headroom's heavy transforms are implemented in a compiled Rust extension, `headroom._core` — a hard import with no Python fallback. The plain `pip install "headroom-ai[all]"` builds it from source, which requires a Rust toolchain and, on Windows, the MSVC build tools (`cl.exe`/`link.exe` from Visual Studio Build Tools). If the core isn't present, Headroom's pipeline falls back to passing messages through unchanged. Friday is built for exactly this: the wrapper imports lazily and degrades gracefully, so a missing or unbuildable core never breaks a chat — and full compression activates automatically, with no code change, the moment `headroom._core` becomes importable.

### Microsoft SkillOpt — Skill Evolution Inspiration
- **Inspiration for:** The SkillOpt engine (`skillopt_engine.py`)
- **Concepts adopted:** Training epochs, validation gates, composite scoring, regression tolerance
- **Friday's implementation:** Skills evolve through versioned optimization cycles with a validation gate that prevents regressions (candidate must score within 5% of all-time best AND beat the immediate baseline)

### Andrej Karpathy — Auto-Research Loop
- **Inspiration for:** The auto-research loop within SkillOpt
- **Concept:** Self-improving AI systems that investigate their own quality drift and propose fixes
- **Friday's implementation:** When a skill's 10-execution rolling mean drops >10% below its all-time best, the loop generates hypotheses (error patterns, latency spikes, quality drift), proposes skill edits, and hands candidates to the training pipeline for validation

---

## Core Dependencies

| Library | License | Purpose |
|---------|---------|---------|
| [Flask](https://flask.palletsprojects.com/) | BSD-3 | Web server and API framework |
| [Anthropic SDK](https://github.com/anthropics/anthropic-sdk-python) | MIT | Claude API client |
| [google-genai](https://github.com/googleapis/python-genai) | Apache 2.0 | Gemini API (TTS, creative, voice) |
| [sentence-transformers](https://www.sbert.net/) | Apache 2.0 | Embeddings for semantic context pruning |
| [headroom-ai](https://github.com/chopratejas/headroom) | Apache 2.0 | Context compression |
| [Rich](https://github.com/Textualize/rich) | MIT | Terminal formatting |
| [Colorama](https://github.com/tartley/colorama) | BSD-3 | Windows terminal colors |
| [PyAutoGUI](https://github.com/asweigart/pyautogui) | BSD-3 | OS control (Ring 3) |
| [BeautifulSoup4](https://www.crummy.com/software/BeautifulSoup/) | MIT | HTML parsing for web tools |
| [Requests](https://requests.readthedocs.io/) | Apache 2.0 | HTTP client |
| [PyYAML](https://pyyaml.org/) | MIT | Skill file parsing |
| [NumPy](https://numpy.org/) | BSD-3 | Embedding similarity computation |

---

## Frontend

| Technology | Purpose |
|------------|---------|
| [Three.js](https://threejs.org/) | Holographic 3D visualization |
| WebGL Shaders | Geometric structure rendering |
| Web Audio API | Audio reactivity and voice input |
| Progressive Web App | Installable manifest + service worker |

---

## Acknowledgments

- The **Anthropic** team for Claude and the Claude API
- **Tejas Chopra** for Headroom — the compression engine that makes long conversations viable
- The **Microsoft Research** team behind SkillOpt for the skill evolution framework
- **Andrej Karpathy** for articulating the auto-research loop concept
- The open-source community behind Flask, sentence-transformers, Three.js, and every dependency listed above
