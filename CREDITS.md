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

### Anthropic Research
- **Asimov's cLaws** — Governance framework inspired by Asimov's Laws of
  Robotics, adapted into a formal specification for AI agent constraints.

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

### Headroom (github.com/chopratejas/headroom)
License: Apache-2.0 | Tejas Chopra and the Headroom Contributors

Friday's context compression (`services/context_compressor.py`) uses Headroom
when its compiled core is installed, and passes text through unchanged when it
is not.

## Open Source Dependencies
Every third-party component Friday ships, installs or downloads, with its
version, license and license text, is listed in THIRD_PARTY_LICENSES.md; the
attributions those licenses require are in NOTICE.
