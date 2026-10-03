# Agent Friday — CREDITS.md
# Acknowledgments and Inspirations

## Architectural Inspirations

### Goose (github.com/aaif-goose/goose)
License: Apache-2.0 | Agentic AI Foundation at the Linux Foundation

The following Agent Friday features were inspired by architectural patterns
observed in the Goose project. All implementations are original Python/Flask
code written specifically for Agent Friday's architecture. No code was copied.

- **Recipe/Workflow System** — Inspired by Goose's YAML-based recipe format
  with parameterization and step sequencing.
- **Declarative Provider Registry** — Inspired by Goose's JSON-file-based
  provider registration enabling zero-code provider addition.
- **Hint System (.fridayhints)** — Inspired by Goose's .goosehints mechanism
  for per-project agent behavior customization.
- **Scoped Subagent Delegation** — Inspired by Goose's isolated agent spawning
  with restricted tool sets.
- **Extension Security Model** — Inspired by Goose's env-var blocklists,
  audit logging, and trust-level gating for MCP extensions.
- **Composable Prompt Manager** — Inspired by Goose's keyed prompt segment
  architecture for modular system prompt construction.
- **Custom Distributions** — Inspired by Goose's distro system for
  preconfigured agent profiles with different tool/workspace/provider defaults.

### Adrian (github.com/secureagentics/adrian)
- **Behavioral Anomaly Detection** — Runtime self-monitoring with 4-score
  anomaly detection (scope drift, privilege escalation, data exfiltration,
  repetition anomaly).

### SkillOpt (github.com/microsoft/SkillOpt)
License: MIT | Microsoft Corporation

Friday's skill optimizer (`skillopt_engine.py`) is inspired by Microsoft's
SkillOpt: a text-space optimizer that trains reusable natural-language skills
against frozen models, with training epochs and regression gates. The
implementation is original; no SkillOpt code is used.

### podcastfy (github.com/souzatharsis/podcastfy)
License: Apache-2.0 | Tharsis T. P. Souza and contributors

Ideas from podcastfy shaped Friday's local podcasts (`services/podcast_engine.py`):
two tagged speakers who alternate, an outline written first, chapters written
one at a time carrying the conversation so far, and a cleaning pass. Only the
idea is borrowed; no code or prompt text is, so no notice is required. The
credit is courtesy.

### Anthropic Research
- **Asimov's cLaws** — Governance framework inspired by Asimov's Laws of
  Robotics, adapted into a formal specification for AI agent constraints.

## Ported code and copied prompts

These are the places where Friday carries another project's code or text, not
only its ideas. Each project's licence notice is reproduced in
THIRD_PARTY_LICENSES.md ("Code and prompts carried in this repository") and
named in NOTICE. A source file that carries such material says so in its
header ("ported from", "vendored from", "port of"); tests/unit/test_source_credits.py
fails when a marker has no entry here.

### obsidian-wiki (github.com/Ar9av/obsidian-wiki)
License: MIT | Copyright (c) 2026 Ar9av

Ported into `services/knowledge_graph/`: community detection, god nodes and
surprising connections (`graph_analysis.py`), question classification and
structural answers (`structural_query.py`), wikilink and frontmatter parsing
(`wiki_graph.py`) and the canonical-path rule for the manifest (`store.py`).
The ports run over Friday's own graph tiers and model calls; the algorithms
and their shape are obsidian-wiki's.

### Microsoft GraphRAG (github.com/microsoft/graphrag), via graphrag-workbench
License: MIT | Copyright (c) Microsoft Corporation

The prompt set under `services/knowledge_graph/prompts/` (extract_graph,
summarize_descriptions, the community report prompts and the local, global
and drift search prompts) is Microsoft GraphRAG's prompt text. The copies
were taken from graphrag-workbench's `prompts/` directory (seven are still
byte-identical to it; Friday's `extract_graph.txt` has since been edited) and
are used as constants; Friday runs them through its own model router and
egress gate.

### graphrag-workbench (github.com/ChristopherLyon/graphrag-workbench)
License: MIT | Copyright (c) 2026 Lyon Industries (Christopher Lyon)

Besides the prompt copies above: the Entity, Relationship and Community
artifact contract Friday's graph store follows (`store.py`, `wiki_graph.py`),
and the force-simulation layout design ported to Python (`layout.py`).

## Generated Art

### Knowledge Galaxy textures (`static/galaxy/`)
Generated with Higgsfield (Google Nano Banana), 2026-09-22, from a written
brief; resized, edge-blended and compressed for the app. No photographs,
logos or third-party artwork.

- `nebula_backdrop.jpg` — equirectangular deep-space backdrop
- `star_glow.png` — star burst sprite (star map, Big Bang flash)
- `gas_giant.jpg` — planet surface (Rings arrangement)
- `ring_profile.png` — radial ring-dust profile (Rings arrangement)
- `shockwave.png` — Big Bang shock ring
- `spiral_haze.png` — face-on galaxy glow (Spiral arrangement)

### Crawl4AI (github.com/unclecode/crawl4ai)
License: Apache-2.0 (with an attribution requirement) | UncleCode (https://x.com/unclecode)

Friday's page reader (`services/page_reader.py`) takes its ideas from
Crawl4AI: scoring page blocks to drop clutter, choosing passages by the
question (BM25) inside a token budget, keeping links as numbered references
beside the text, reading the page's own metadata, and conditional feed
fetches. No Crawl4AI code or dependency is used.

### Headroom (github.com/chopratejas/headroom)
License: Apache-2.0 | Tejas Chopra and the Headroom Contributors

Friday's context compression (`services/context_compressor.py`) uses Headroom
when its compiled core is installed, and passes text through unchanged when it
is not.

## Works With

### espeak-ng (github.com/espeak-ng/espeak-ng) and phonemizer (github.com/bootphon/phonemizer)
License: GPL-3.0-or-later | the espeak-ng and phonemizer authors

Friday works with espeak-ng to pronounce names Kokoro's dictionary does not
know. It is an optional install, shown with its licence before it is offered,
and it runs as its own helper program (`agent_friday/voice/espeak_helper.py`)
that Friday talks to over a pipe; Friday never imports or loads it, and this
repository contains none of its code or binaries. Without it, Friday spells
such names out.

## Open Source Dependencies
Every third-party component Friday ships, installs or downloads, with its
version, license and license text, is listed in THIRD_PARTY_LICENSES.md; the
attributions those licenses require are in NOTICE.
