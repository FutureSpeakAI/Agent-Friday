# Changelog

All notable changes to Agent Friday are documented in this file.

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
