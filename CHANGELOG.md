# Changelog

All notable changes to Agent Friday are documented in this file.

---

## [2.3.1] — 2026-03-08

### Added — Graceful Degradation & Local-First Operation

- **Tier-aware WelcomeGate**: On Standard+ hardware (6 GB+ VRAM), all API keys are optional. A "Run Locally" button lets users skip cloud configuration entirely and run via Ollama. Hardware tier is detected in parallel with settings load and displayed as a badge
- **Intelligence router auto-configuration**: When no cloud API keys are present and `preferredProvider` is `'ollama'`, the router automatically switches its fallback model from `anthropic/claude-sonnet-4` to a local model and sets `localModelPolicy: 'preferred'`
- **Psychological profile graceful fallback**: If the LLM call fails during onboarding (no API key, unreachable provider, invalid JSON response), a balanced default profile is used instead of blocking the setup flow
- **Sub-agent voice graceful degradation**: `agentVoice.speak()` returns `null` instead of throwing when no ElevenLabs API key is configured. `synthesizeAndSpeak()` in the agent runner detects this and delivers text-only results via IPC, so sub-agents (Atlas, Nova, Cipher) work without voice synthesis

### Changed

- `WelcomeGate.tsx` — Hardware tier detection, `effectiveConfigs` that override `required: false` for local-capable tiers, `handleSkipLocal()` callback, tier-aware UI text and layout
- `agent-voice.ts` — `speak()` return type changed to `Promise<VoiceSynthResult | null>`; no-key path returns `null` with console log instead of throwing
- `agent-runner.ts` — `synthesizeAndSpeak()` handles `null` voice result by sending text-only IPC payload
- `intelligence-router.ts` — New `autoConfigureForLocalIfNeeded()` method called during `initialize()`; imports `settingsManager` for key detection
- `psychological-profile.ts` — `generatePsychologicalProfile()` catches LLM errors and JSON parse failures, returning `DEFAULT_PROFILE` instead of throwing

---

## [2.3.0] — 2026-03-08

### Added — Sprint 7: Integration Wiring

- **5 new IPC handler modules** wiring 90 new IPC channels to their backend implementations:
  - `hardware-handlers.ts` — 16 channels: GPU/CPU/RAM/VRAM detection, hardware profiles, thermals, display info
  - `setup-handlers.ts` — 18 channels: Setup wizard, profile creation, intake responses, customization, first-run flow
  - `ollama-handlers.ts` — 7 channels: Local model management (list, pull, delete, status, generate, chat, embeddings)
  - `voice-pipeline-handlers.ts` — 32 channels: Whisper STT (5), audio capture (4), transcription pipeline (4), TTS engine (6), voice profiles (6), speech synthesis (7)
  - `vision-pipeline-handlers.ts` — 17 channels: Vision model inference (6), screen capture (6), image understanding (5)
- **29 event forwarding streams** from main process to renderer via `webContents.send()`: audio capture events (4), transcription pipeline events (3), speech synthesis events (4), screen context events (1), image understanding events (1), plus 16 additional hardware/setup/ollama streams
- **6 new preload bridge namespaces** in `preload.ts`: `hardware`, `setup`, `profile`, `ollama`, `voice`, `vision`
- **`validate.ts`** — Shared input validation utilities (assertString, assertObject, assertNumber, assertStringArray) with length limits used across all Sprint 7 handlers
- **5 new test suites** (82 new tests):
  - `tests/ipc/hardware-handlers.test.ts` — 16 tests
  - `tests/ipc/setup-handlers.test.ts` — 13 tests
  - `tests/ipc/ollama-handlers.test.ts` — 11 tests
  - `tests/ipc/voice-pipeline-handlers.test.ts` — 23 tests
  - `tests/ipc/vision-pipeline-handlers.test.ts` — 19 tests

### Fixed

- **Electron boot crash (TLS hardening)**: `Object.defineProperty(process.env, 'NODE_TLS_REJECT_UNAUTHORIZED', { get, set })` crashes Electron's native C++ process.env bridge. Replaced with `setInterval` periodic guard that checks and deletes the env var every 5 seconds
- **VRAM display TypeError**: `profile.vramMB` → `Math.round((profile.vram?.total ?? 0) / (1024 * 1024 * 1024))` to match actual `HardwareProfile` interface
- **Async IPC handler test pattern**: Changed `expect(() => invoke(...)).toThrow()` to `await expect(invoke(...)).rejects.toThrow()` for async handlers that validate and throw — async throws become rejected promises, not synchronous exceptions

### Stats

- Total source files: 294 (was 210)
- Total tests: 4,347 across 127 test suites (was 3,496 across 63)
- IPC handler modules: 47 (was 28)
- Preload bridge namespaces: 28+

---

## [2.2.0] — 2026-03-02

### Added

- **Sovereign Vault v2 — Passphrase-Only Root of Trust**: Complete redesign of the cryptographic foundation. Replaced DPAPI/Keychain/safeStorage dependency with a pure passphrase-derived key hierarchy. User's passphrase (≥8 words) → Argon2id (256MB, 4 iterations) → masterKey → BLAKE2b KDF → {vaultKey, hmacKey, identityKey}
- **SecureBuffer**: Guard-paged, mlocked memory wrapping `sodium_malloc()` with NOACCESS/READONLY/READWRITE protection states, canary bytes, and withAccess() borrow pattern for minimum exposure
- **PassphraseGate UI**: Full-screen sovereign vault gate with two modes (CREATE for first-time, UNLOCK for returning), progressive rate-limiting (5s→15s→60s after failed attempts), and "THIS IS YOUR ONLY KEY" warning modal
- **Two-Phase Boot Architecture**: Phase A (vault locked, graceful plaintext fallback) → Phase B (vault unlocked, encrypted state reloaded, HMAC key injected, integrity verified, agent network initialized)
- **Phase B State Reload**: Settings, Memory, Trust Graph, and Calendar all implement `reloadFromVault()` for seamless post-unlock restoration
- **Passphrase-derived HMAC v2**: HMAC signing key injected from vault derivation instead of machine-bound key, enabling cross-machine portability
- **Identity Encryption v2**: Ed25519/X25519 private keys encrypted with XSalsa20-Poly1305 using vault-derived identity key instead of machine-bound encryption
- **Phase B Initialization Timeouts**: `withTimeout<T>()` helper wrapping integrity and agent-network init with 30-second timeouts for graceful degradation instead of hanging
- **198 crypto-specific tests** across 8 test suites covering SecureBuffer, PassphraseKDF, Vault v2, HMAC v2, Identity Encryption, cLaw Attestation, P2P Crypto, and deep-sort-keys

### Changed

- Vault key derivation migrated from `crypto.scrypt(N=2²⁰)` + machine fingerprint to `Argon2id(opslimit=4, memlimit=256MB)` + user passphrase via `sodium-native`
- HMAC key source changed from machine-bound derivation to vault-derived sub-key (context "AF_HMAC_")
- Identity key encryption changed from machine-bound to vault-derived sub-key (context "AF_IDENT")
- All vault-encrypted files use random 12-byte IVs per write (no IV reuse)
- TypeScript type declarations (`types.d.ts`) updated from v1 vault API to v2 API surface
- `node-machine-id` dependency fully removed — no platform keystore dependencies remain
- `sodium-native` added as the sole cryptographic dependency (libsodium bindings)

### Removed

- **DPAPI/Keychain/safeStorage dependency** — No platform-specific key storage. Vault is fully portable
- **12-word recovery phrase system** — Replaced with passphrase-only model (the passphrase IS the recovery mechanism)
- **Machine fingerprint binding** — Keys no longer tied to hardware, enabling machine migration by entering the same passphrase

### Security

- All key material held in `sodium_malloc` backed SecureBuffer with guard pages, mlock, and canary bytes
- Argon2id parameters tuned for 1-4 second derivation time (anti-brute-force by design)
- Canary verification distinguishes wrong passphrase from vault corruption
- Progressive client-side rate-limiting on failed unlock attempts
- Legacy `safe:` prefix detection with clear error messaging for v1→v2 migration
- Zero vault-related TypeScript errors (verified via `tsc --noEmit`)

### Stats

- 210 source files, ~98,000 lines of TypeScript
- 3,496 tests across 63 test suites — all passing
- 198 crypto-specific tests across 8 suites
- 0 TypeScript errors (excluding 4 pre-existing cast warnings in intelligence-router-handlers and onboarding-handlers)

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

- **First-launch crypto hang (hermeneutic circle fix)** — Traced the full initialization chain and identified THREE synchronous blocking operations. The primary villain was `crypto.scryptSync(N=2^20)` in vault key derivation, which blocks the entire Node.js event loop for 5-30 seconds. Replaced with async `crypto.scrypt()` (runs in libuv background thread). Also replaced `dialog.showMessageBoxSync()` with async `dialog.showMessageBox()` in HMAC init. Previous fix (`machineIdSync` → `machineId`) was necessary but insufficient.
- **Identity save race condition** — Agent network now flushes crypto identity to disk immediately on first run instead of deferring via 2-second debounce timer, ensuring vault initialization has keys available
- **All test failures resolved** — Fixed 6 test failures across agent-network persistence and trust graph decay tests by introducing a late-bound `getVault()` pattern with graceful fallback
- **All TypeScript errors resolved** — Fixed missing renderer type declarations (`vault.isRecoveryPhraseShown`, `integrity.reset`), TS4094 errors in orchestrator exports, and nullable vault return types

### Changed

- Vault key derivation uses async `crypto.scrypt()` instead of blocking `crypto.scryptSync()`
- HMAC security warning uses async `dialog.showMessageBox()` instead of blocking `showMessageBoxSync()`
- Vault initialization includes timing diagnostics for future debugging
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
