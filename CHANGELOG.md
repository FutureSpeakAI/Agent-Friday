# Changelog

All notable changes to Agent Friday are documented in this file.

---

## [2.1.0] — 2026-03-01

### Added

- **Multi-Agent Network (Track XI)** — Full peer-to-peer multi-agent operating system:
  - **Container Engine** — Sandboxed execution environments with cLaw governance, resource limits, and automatic cleanup
  - **Delegation Engine** — Structured task delegation between agents with trust-gated approval, progress tracking, and result verification
  - **Orchestration Bridge** — Wires the orchestrator to the delegation engine for cross-instance task decomposition
  - **Awareness Mesh** — Inter-agent coordination layer for broadcasting state changes, capability updates, and heartbeat signals
  - **Capability Map** — Intelligent agent routing that matches tasks to best-suited agents based on declared capabilities
  - **Symbiont Protocol** — Self-improving agent performance through cross-agent learning and collaborative skill refinement
- **Connector System Test Suite** — 172 integration tests covering the full connector registry
- **Trust Graph Engine** — Multi-dimensional trust scoring with hermeneutic re-evaluation, person resolution, and evidence tracking

### Fixed

- **First-launch crypto hang** — Replaced blocking `machineIdSync()` with async `machineId()` in vault initialization, eliminating a 5-30 second UI freeze on Windows caused by synchronous WMI system UUID reads
- **Identity save race condition** — Agent network now flushes crypto identity to disk immediately on first run instead of deferring via 2-second debounce timer, ensuring vault initialization has keys available
- **All test failures resolved** — Fixed 6 test failures across agent-network persistence and trust graph decay tests by introducing a late-bound `getVault()` pattern with graceful fallback
- **All TypeScript errors resolved** — Fixed missing renderer type declarations (`vault.isRecoveryPhraseShown`, `integrity.reset`), TS4094 errors in orchestrator exports, and nullable vault return types

### Changed

- Vault module import uses `machineId` (async) instead of `machineIdSync` (sync)
- Agent network identity persistence is immediate on first run (no debounce)
- Test infrastructure: persistence tests now clear init-phase mock writes for isolation

### Stats

- 208 source files, ~98,000 lines of TypeScript
- 3,270 tests across 53 test suites — all passing
- 0 TypeScript errors
- 18 connector modules in the registry

---

## [2.0.0] — 2026-02-28

### Added

- **Sovereign Vault** — AES-256-GCM at-rest encryption for all agent state files, keyed from Ed25519 private key + machine fingerprint via scrypt
- **cLaw Attestation** — Cryptographic governance verification for agent-to-agent messaging
- **Trusted File Transfer** — Chunked, SHA-256-verified file transfer between agents with trust-gated acceptance
- **Multimedia Creation Engine** — Nano Banana 2 image generation with 14 aspect ratios and text rendering
- **Holographic Desktop** — 13 evolution structures with Three.js rendering, bloom post-processing, mood-reactive visuals
- **Weekly Art Evolution** — AI art therapy sessions that evolve the desktop visualization
- **OpenRouter Integration** — Access to 200+ models through a unified API
- **Trust Graph Engine** — Evidence-based people credibility scoring
- **Context Stream Pipeline** — Real-time context streaming to renderer
- **Superpowers Ecosystem** — Dynamic agent power system
- **Workflow Recorder/Executor** — Record and replay workflow sequences
- **Git Analysis Suite** — Repository analysis, monitoring, review, and sandboxed operations
- **Security Hardening** — CSP enforcement, vault keyphrase gate, environment sanitization
- **Personality Calibration** — Drift detection and correction
- **Memory Quality System** — Scoring and intelligent pruning
- **3,270+ comprehensive tests** across all subsystems

---

## [1.2.0] — 2026-02-27

- Gemini connection fix, voice orb sizing, Perplexity domains
- Initial connector registry architecture

---

## [1.1.5] — 2026-02-26

- Bug fixes and stability improvements
- Self-signed code signing removed for cross-machine installs

---

*For older releases, see the [GitHub releases page](https://github.com/FutureSpeakAI/Agent-Friday/releases).*
