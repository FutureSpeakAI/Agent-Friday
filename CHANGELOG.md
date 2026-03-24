# Changelog

All notable changes to Agent Friday are documented in this file.

---

## [3.12.1] — 2026-03-24 — Onboarding Hotfix

### Summary

Critical fix for vault-locked write failure that blocked onboarding at the API key step, prevented voice/text during the interview, and broke agent finalization. All settings write paths now fall back to plaintext when the vault is not yet initialized, then auto-encrypt on vault unlock.

### Fixed — Onboarding

- **API key save fails during onboarding** — `setApiKey()`, `setSetting()`, `saveAgentConfig()`, and `save()` all called `vaultWrite()` which throws when vault is locked. Vault init happens at step 7 (Privacy), but API keys are entered at step 4 (Providers). Added `writeSettingsFile()` helper that checks `isVaultUnlocked()` and falls back to `fs.writeFile` plaintext when locked.
- **"Error invoking remote method 'onboarding:finalize-agent'"** — Same root cause: `saveAgentConfig()` called `vaultWrite()` directly. Now uses the `writeSettingsFile()` fallback.
- **Voice/text not working during interview** — Consequence of the API key save failure: keys not persisted meant `connectToGemini()` found no Gemini key, and if Ollama wasn't available, no voice backend existed. With keys saving correctly, the voice cascade now resolves properly.
- **Vault init failure silently ignored** — `PrivacyPermissionsStep` called `vault.initializeNew()` but never checked the `{ ok: false, error }` return value, marking vault as ready even on failure. Now checks `result.ok` before advancing.
- **ModelsStep skip could hang** — `handleSkip` called `settings.set()` without try/catch; if it threw, `onComplete()` was never called and the user was stuck. Added error handling.
- **Gateway toggle silently dropped** — `gatewayEnabled` was in the `sensitiveFields` blocklist, so `setSetting('gatewayEnabled', ...)` silently rejected. Removed from blocklist since it's a boolean preference, not a credential.

---

## [3.12.0] — 2026-03-24 — Living Architecture & Data Persistence

### Summary

Comprehensive reverse engineering of all operational flows using Nick Tune's living architecture methodology. Fixed critical silent data loss where setSetting() rejected 7 onboarding fields because they were missing from the FridaySettings interface. Added dedicated Telegram credential setter. Added Ollama model pulling during onboarding. Removed TTS gate that blocked text-only local mode. Added 11 documented operational flows with Mermaid diagrams. Redesigned onboarding with new steps: ModelsStep, ProvidersStep, IntegrationsStep, VoiceIdentityStep, PersonalityStep, PrivacyPermissionsStep. Added PersonaPlex voice path and Gemini WebSocket proxy.

### Added — Architecture & Documentation

- **11 operational flow docs** — Nick Tune living architecture methodology, Mermaid diagrams, in `docs/architecture/flows/`

### Added — Voice & Proxy

- **PersonaPlex voice server** — New voice path via `personaplex-server.ts` and `personaplex-voice-path.ts`
- **Gemini WebSocket proxy** — API key stays in main process (`gemini-ws-proxy.ts`, `ws-proxy.ts`)

### Added — Onboarding Redesign

- **New onboarding steps** — ModelsStep, ProvidersStep, IntegrationsStep, VoiceIdentityStep, PersonalityStep, PrivacyPermissionsStep
- **Dedicated setTelegramConfig setter** — Bypasses sensitive fields blocklist for Telegram credentials
- **Ollama model pulling** — Chat and embedding models pulled during ModelsStep onboarding
- **7 missing FridaySettings fields** — `whisperModel`, `embeddingModel`, `personalitySliders`, `memoryDepth`, `piiFiltering`, `telemetry`, `localProcessing`

### Fixed — Data Persistence

- **Silent settings data loss** — `setSetting()` guard rejected keys not in FridaySettings interface, causing all onboarding configuration to be silently dropped
- **Text-only local mode blocked** — TTS gate abandoned local path when TTS unavailable during onboarding
- **Telegram credentials blocked** — Sensitive fields list had no dedicated setter, preventing credential storage
- **Ollama models never pulled** — ModelsStep saved model names but never called `pullModel`

### Removed — Onboarding Steps

- **ApiKeysStep** — Replaced by ProvidersStep
- **EnvironmentStep** — Functionality merged into other steps
- **PrivacyStep** — Replaced by PrivacyPermissionsStep

---

## [3.11.1] — 2026-03-23 — Fix Silent IPC Failure

### Summary

Root cause fix for text prompting being silently broken. `local-conversation:start` returned `{ ok: false, error }` instead of throwing, but the renderer never checked — setting `localConversationActiveRef=true` on a dead conversation.

### Fixed — IPC Error Handling

- **App.tsx checks startResult.ok** — Throws on failure instead of silently continuing with a dead conversation
- **types.d.ts return type** — `start()` returns `{ ok, error }` instead of `void`
- **Duplicate user-transcript emit** — `local-conversation.ts` skips redundant user-transcript event
- **StatusBar beacon rename** — "TTS" beacon renamed to "E11" (ElevenLabs)
- **.gitignore update** — Added `releases/` directory

---

## [3.11.0] — 2026-03-23 — Self-Contained Python

### Summary

App is now fully self-contained. Auto-downloads Python 3.12 from python-build-standalone when no compatible system Python exists (~46MB one-time). Fixes text-mode regression from v3.10.1.

### Added — Bundled Python

- **Auto-download Python 3.12** — Downloads from python-build-standalone when no compatible system Python is found

### Fixed — Text Mode

- **Text prompt regression** — v3.10.1's TTS check killed local conversation when TTS unavailable; `isPythonAvailable()` now always returns true with bundled runtime
- **Simplified CUDA detection** — Uses `nvidia-smi` directly instead of routing through Python

---

## [3.10.1] — 2026-03-22 — Instant Barge-In

### Summary

Voice-start event triggers instant TTS stop before Whisper transcript completes. Gemini Live fallback when local TTS fails. Python compatibility fix for Chatterbox.

### Added — Barge-In

- **Instant barge-in** — AudioCapture `voice-start` event triggers immediate TTS stop before Whisper transcript completes
- **Barge-in forwarding** — Events forwarded to renderer AudioPlaybackEngine

### Fixed — Voice Fallback

- **Gemini Live voice fallback** — Falls back to Gemini Live when local TTS fails to load
- **Chatterbox Python compat** — Requires Python 3.10–3.12, upgraded to cu126

---

## [3.10.0] — 2026-03-22 — Chatterbox Turbo TTS

### Summary

Adds Chatterbox Turbo as top-priority local TTS backend (350M param, GPU-accelerated Python sidecar). TTS cascade: chatterbox > kokoro-js > kokoro > piper.

### Added — Chatterbox TTS

- **Chatterbox Turbo backend** — 350M param GPU-accelerated Python sidecar (`chatterbox-server.ts`)
- **useLocalMicCapture hook** — Bridges renderer mic input to main process
- **HardwareStep auto-install** — Chatterbox installed during onboarding

### Fixed — Input Handling

- **Text input race condition** — Fixed race condition in `handleTextSend`
- **Mic input bridge** — Mic input now works via new local mic capture bridge

---

## [3.9.1] — 2026-03-21 — Fix VoiceFallbackManager Bypass

### Summary

Track 6 block called `startBestPath()` and returned early, skipping renderer-side listener setup. Fix reduces Track 6 to priority-setting only.

### Fixed — Voice Event Routing

- **VoiceFallbackManager bypass** — Track 6 no longer calls `startBestPath()` directly, reduced to priority-setting only
- **Renderer listener setup** — Renderer now correctly receives voice events and knows connection is active

---

## [3.9.0] — 2026-03-21 — Local-First Onboarding Voice Interview

### Summary

Voice interview with transcript UI and processing states for local-first onboarding.

### Added — Voice Interview UI

- **Transcript UI** — Real-time display of voice interview transcript
- **Processing state indicators** — Visual feedback during voice interview processing
- **Local-first voice interview** — Full voice interview support for local-first onboarding

---

## [3.8.0] — 2026-03-20 — Make the App Whole

### Summary

Binary auto-download for voice tools, direct code execution fallback, camera capture. All 5064 tests passing across 141 test files.

### Added — Auto-Download & Execution

- **Voice binary auto-downloader** — Downloads whisper-cpp, sherpa-onnx, and Piper models automatically
- **Direct code execution fallback** — Python/Bash/Node subprocess execution when sandboxed execution unavailable
- **Camera capture** — Saves to `~/Pictures/Agent Friday/`
- **FridayWeather resilient fetch** — Parallel fetch with graceful degradation

### Fixed — Test Infrastructure

- **21 broken import paths** — Wrong relative import paths in test files corrected
- **Missing mock exports** — Added `node:os` and `node:fs/promises` mock exports
- **Model test fixtures** — Added missing `category` field in model test fixtures

---

## [3.7.8] — 2026-03-20 — Technical Debt Resolution

### Summary

Race conditions, listener leaks, error handling improvements, and test coverage expansion.

### Fixed — Stability

- **Race conditions in LocalConversation start()** — Eliminated startup race conditions
- **Listener accumulation** — Fixed listener leaks in `ollama-handlers` and `voice-pipeline-handlers`
- **WebSocket re-entry guard** — `useGeminiLive` `connect()` now guards against re-entrant calls

### Added — Security & Testing

- **PassphraseGate entropy validation** — Validates passphrase strength before vault initialization
- **PowerShell concurrency limiter** — Maximum 5 concurrent PowerShell processes
- **Renderer error telemetry** — Error events forwarded to telemetry pipeline
- **.env.example** — API key placeholders for onboarding reference
- **Test suites** — desktop-tools (74 tests), telemetry (31 tests), app-store (60 tests)

---

## [3.7.7] — 2026-03-19 — Voice Resilience Overhaul

### Summary

Replaces fragile dual-path voice with state-machine-governed cascade: Cloud > Local > Text.

### Added — Voice State Machine

- **VoiceStateMachine** — 13 states, 45 transitions, per-state timeouts
- **VoiceFallbackManager** — Bidirectional Cloud ↔ Local ↔ Text fallback cascade
- **ConnectionStageMonitor** — 6-stage granular timeouts for connection tracking
- **PreFlightChecks** — Pre-connection validation of voice requirements
- **VoiceErrorClassifier** — Categorizes voice errors for appropriate fallback decisions
- **VoiceHealthMonitor** — Continuous health monitoring of active voice connections
- **AudioPlaybackEngine** — Context resurrection and backpressure support

---

## [3.7.6] — 2026-03-19 — Onboarding Completeness

### Summary

Wires the Sovereignty step (vault passphrase + agent identity) into the onboarding wizard, adds automatic validation and download of the default chat model (llama3.2) during hardware detection, and fixes the hardcoded version string in the HUD footer to read dynamically from package.json.

### Added — Sovereignty Step

- **EnvironmentStep wired into wizard** — The 8th onboarding step ("SOVEREIGNTY") is now active between Privacy and API Keys. Users initialize their encrypted vault with a passphrase and configure agent identity (name, gender, voice feel) before proceeding. Vault initialization uses AES-256-GCM + Argon2id KDF. Auto-skips vault section if already initialized
- **Progress bar updated** — Progress indicator now shows all 6 intermediate steps (Mission → Hardware → Privacy → Sovereignty → API Keys → Interview)

### Added — Default Model Validation

- **Auto-pull llama3.2** — After confirming Ollama is running, HardwareStep now checks if the default chat model (`llama3.2`) is available. If missing, it auto-pulls with a progress indicator. Non-fatal: if pull fails, users can install later from Settings
- **Continue button gating** — Continue is disabled while Whisper or default model downloads are in progress

### Fixed — Version String

- **Dynamic version** — HUD footer now reads version from `package.json` via Vite's `define` mechanism (`__APP_VERSION__`), replacing the hardcoded "v3.1.1" that was 6 major versions behind
- **Type declaration** — Added `__APP_VERSION__` global type to `vite-env.d.ts`

---

## [3.7.5] — 2026-03-19 — Voice Pipeline & VAD Fix

### Summary

Fixes three issues preventing voice from working end-to-end. The Gemini WebSocket setup was rejected because `START_SENSITIVITY_MEDIUM` is not a valid VAD enum (only `LOW` and `HIGH` exist). Local TTS audio was generated but never reached the speakers — the `voice:play-chunk` IPC event had no renderer listener. Post-creation reconnects also missed the `startListening()` call.

### Fixed — Gemini Voice

- **Invalid VAD config** — Changed `start_of_speech_sensitivity` from `START_SENSITIVITY_MEDIUM` (invalid) to `START_SENSITIVITY_HIGH`. This was the error visible in the status bar: "Invalid value at setup.realtime_input_config..."

### Fixed — Local TTS Audio

- **Orphaned audio chunks** — `speech-synthesis.ts` sent `voice:play-chunk` via IPC but no renderer code listened. Added preload bridge (`onPlayChunk`), renderer handler in `App.tsx`, and an `AudioPlaybackEngine` instance that enqueues local TTS audio to the system speakers

### Fixed — Mic Initialization

- **AgentCreation reconnect** — Both post-onboarding reconnect paths now call `startListening()` after `geminiLive.connect()`, ensuring mic access is requested

---

## [3.7.4] — 2026-03-19 — Runtime Wiring Fixes

### Summary

Fixes four interconnected runtime issues that prevented voice, text chat, and status beacons from working after onboarding. The Ollama health check had a race condition where the renderer queried status before the first HTTP poll completed (always returning "not running"). The Gemini Live path never called `startListening()` after WebSocket setup, so mic access was never requested. Text messages were silently dropped when neither backend was active. API health beacons hardcoded "ready" based on key existence without actually pinging the services.

### Fixed — Ollama Detection

- **Health check race condition** — `OllamaLifecycle` now exposes `getHealthAsync()` which waits for the first HTTP poll to complete before returning status. The IPC handler uses this, preventing the renderer from seeing stale `{running: false}` state on startup
- **LocalConversation Ollama gate** — Removed the cached `isAvailable()` pre-check that returned false before health cache was populated. Now goes directly to a fresh `checkHealth()` HTTP call

### Fixed — Voice Interview

- **Microphone initialization** — After Gemini Live WebSocket setup completes, `startListening()` is now called to request mic permission and begin audio capture. Previously the WebSocket connected successfully but `navigator.mediaDevices.getUserMedia()` was never invoked

### Fixed — Text Chat

- **Silent message drop** — When neither local conversation nor Gemini WebSocket is active, `handleTextSend` now shows an error message in chat instead of silently discarding the user's input
- **Empty Ollama responses** — When Ollama returns an empty response, a diagnostic message is now emitted so the user isn't left waiting for a reply that will never come

### Fixed — API Health Beacons

- **Real endpoint pings** — New `settings:check-api-health` IPC handler performs lightweight HTTP pings to Gemini, Anthropic, OpenRouter, and ElevenLabs APIs using stored keys. Beacons now show actual reachability (`connected` / `offline` / `no-key`) instead of hardcoded "ready" for any stored key
- **Periodic refresh** — API health is re-checked every 60 seconds so beacons stay current during the session
- **StatusBar + HudOverlay types** — Updated `ApiStatus` interface to support `'connected' | 'offline'` states for Claude, OpenRouter, and ElevenLabs

---

## [3.7.3] — 2026-03-18 — Graceful Voice Degradation

### Summary

Fixes the voice interview failing to initialize during onboarding when the Whisper STT model is missing. LocalConversation now degrades gracefully from full voice to text-only mode rather than aborting entirely — Whisper and TTS are optional, Ollama is the only hard requirement. Also adds Whisper auto-download during onboarding and a visible settings gear button to the HUD.

### Fixed — Voice Initialization

- **Graceful degradation** — LocalConversation supports three modes: full voice (Whisper + Ollama + TTS), text + TTS (Ollama + TTS), and text-only (Ollama only). Missing Whisper or TTS models no longer abort the session
- **Text message routing** — Messages route correctly through Ollama even when the voice pipeline is unavailable; previously text was silently dropped when no voice backend connected
- **Non-fatal error handling** — Whisper/TTS warnings during local conversation startup don't block the connection error overlay; the session starts and clears the error
- **Prop type fix** — `connectToGemini` return type corrected from `void` to `Promise<void> | void` in InterviewStep and OnboardingWizard

### Added — Onboarding & UX

- **Whisper auto-download** — HardwareStep automatically downloads the Whisper tiny model (~75MB from HuggingFace) when Ollama is detected running, with progress indicator
- **WhisperProvider.downloadModel()** — Streaming download from HuggingFace with progress callbacks and partial download cleanup on failure
- **Download IPC** — New `voice:whisper:download-model` and `voice:whisper:is-model-downloaded` IPC handlers with preload bridge
- **Settings gear button** — Visible gear icon in the HUD header next to the clock/laws status, replacing the undiscoverable telemetry-bar-click pattern

---

## [3.7.2] — 2026-03-18 — Voice Interview Identity Discovery

### Summary

Moves agent identity configuration (name, voice gender, voice feel) out of the EnvironmentStep form and into the voice interview conversation itself, creating a genuine "Her"-style moment where the agent's personality emerges through dialogue rather than dropdown menus. The onboarding wizard is reduced from 8 steps to 7.

### Changed — Onboarding Flow

- **Interview-driven identity** — Agent name, gender, and voice character are discovered naturally through the voice interview instead of being pre-selected in a form step
- **EnvironmentStep removed** — Vault creation now happens automatically; the separate Environment configuration step is no longer needed
- **7-step wizard** — Onboarding reduced to: Awakening, Mission, Hardware, Privacy, ApiKeys, Interview, Reveal
- **Default personality profiles** — Skip Interview applies a curated default personality (male/female/neutral variants with voice, backstory, traits, and identity line)

---

## [3.7.1] — 2026-03-18 — Local-First Voice with Gemini Fallback

### Summary

Implements the local-first voice architecture: when Ollama is running, the app always tries the local voice path (Whisper + Ollama + TTS) first and only falls back to Gemini Live if local voice fails. API key health indicators show connection status for all providers in the HUD.

### Added — Local-First Architecture

- **Local-first routing** — `connectToGemini()` checks Ollama health first; if healthy, starts LocalConversation before attempting Gemini WebSocket
- **API status indicators** — HUD left sidebar shows real-time connection status (green/yellow/red) for Gemini, Claude, Router, Voice, and Browser
- **Automatic fallback** — If local voice fails (Whisper missing, TTS unavailable), transparently falls back to Gemini Live if an API key is configured

---

## [3.7.0] — 2026-03-18 — Ollama Dependency Check

### Summary

Adds an Ollama health check to the onboarding flow so users know whether local AI is available before proceeding. Shows clear instructions for downloading Ollama if not detected, with a "Check Again" button and "Skip — Use Cloud Only" fallback.

### Added — Onboarding

- **Ollama detection step** — HardwareStep checks for a running Ollama instance after hardware detection
- **Install instructions** — Step-by-step guide with link to ollama.com/download when Ollama is not detected
- **Cloud-only skip** — Users without Ollama can skip to cloud-only mode without confusion

---

## [3.6.6] — 2026-03-18 — Download Progress Fix

### Summary

Fixes the model download progress callback signature mismatch that caused download tracking to fail silently during onboarding.

### Fixed

- **Download progress callback** — SetupWizard download progress events now emit with the correct signature, restoring per-model progress bars during HardwareStep downloads

---

## [3.6.5] — 2026-03-18 — Settings & Key Validation Fixes

### Summary

Fixes CORS-related API key validation failures, non-Ollama model pull errors, and a crash in the Settings panel.

### Fixed

- **CORS key validation** — API key validation requests no longer fail due to CORS restrictions by using the main process for HTTP calls
- **Non-Ollama model pulls** — Model download logic correctly handles non-Ollama providers
- **Settings panel crash** — Resolved a rendering error in the Settings component that prevented it from opening

---

## [3.6.4] — 2026-03-18 — API Key Validation & Download Stall

### Summary

Fixes API key validation being blocked by Content Security Policy and model downloads stalling during the onboarding HardwareStep.

### Fixed

- **CSP key validation** — API key validation now routes through the main process to avoid CSP fetch restrictions in the renderer
- **Download stall** — Model download progress tracking properly signals completion, preventing the UI from appearing stuck

---

## [3.6.3] — 2026-03-18 — Hardware Step Crash Fix

### Summary

Fixes a crash in the onboarding HardwareStep that occurred on first launch when hardware detection returned unexpected data shapes.

### Fixed

- **HardwareStep crash** — Null-safe handling of hardware profile data prevents crashes when GPU detection returns incomplete results

---

## [3.6.2] — 2026-03-11 — Onboarding Tool Scoping

### Summary

Fixes Gemini Live connection failures during onboarding caused by sending 200-400+ tool declarations in the WebSocket setup message. The onboarding interview now sends only the 4 tools it actually needs, dramatically reducing the payload size and preventing Google from rejecting oversized messages.

### Fixed — Connection Reliability

- **Onboarding tool scoping** — Voice interview connects with only 4 onboarding tools instead of the full 200-400+ toolkit, eliminating WebSocket setup payload bloat that caused connection failures on some accounts
- **Close code 1009 handling** — "Message Too Big" WebSocket rejections now produce a clear error message instead of a generic disconnect
- **Reconnect preserves onboarding mode** — Auto-reconnect and SessionManager reconnect paths now carry the onboarding flag, preventing tool re-bloat during active interviews
- **Variable scoping fix** — Dynamic tool declarations are properly scoped to prevent runtime errors in onboarding mode

---

## [3.6.1] — 2026-03-11 — Cloud-Only UX & Key Validation

### Summary

Improves the experience for users on lightweight hardware (Surface Pro, integrated graphics) who rely entirely on cloud APIs. API keys are now pre-validated before saving, and the voice interview provides staged progress feedback instead of hanging silently on auth failures.

### Fixed — Cloud-Only Device Experience

- **Whisper tier "Cloud Mode" card** — Devices with 0 available VRAM (e.g. Surface Pro with Intel iGPU) now see a clear "Cloud Mode" explanation instead of an empty model list during onboarding, with feature chips showing what works via cloud APIs
- **API key pre-validation** — Gemini, Anthropic, and OpenRouter keys are validated via lightweight REST calls before saving, both in Settings and during onboarding; invalid keys show immediate, specific error messages instead of causing cryptic WebSocket failures later
- **Voice interview staged progress** — Connection status cycles through "Connecting...", "Authenticating...", "Loading agent tools...", "Opening audio channel..." instead of showing a static "Connecting to voice session..." for up to 30 seconds
- **Faster auth failure detection** — Connection timeout reduced from 30s to 15s; auth failures from WebSocket close codes now produce specific error messages ("Authentication failed — check your Gemini API key in Settings")
- **Better failure hints** — Failed voice interview now suggests checking the API key in Settings instead of only offering to skip

---

## [3.6.0] — 2026-03-10 — Local Voice OS

### Summary

Agent Friday now works as a fully local, voice-first AI operating system with zero cloud API keys required. The new `LocalConversation` orchestrator chains Whisper STT → Ollama LLM → Kokoro/Piper TTS for real-time voice conversations entirely on-device. Every post-onboarding blocker that prevented local-only users from using the app has been resolved — the app automatically falls back to the local voice path when no Gemini API key is configured.

### Added — Local Voice Conversation Loop

- **LocalConversation orchestrator** (`src/main/local-conversation.ts`) — Main-process EventEmitter that manages the full voice conversation lifecycle: microphone capture → VAD → Whisper transcription → Ollama completion (with tool calling) → TTS speech synthesis → audio playback
- **Three-tier tool routing** — Conversation tools route through onboarding tools, feature setup tools, and desktop/MCP tools depending on conversation phase
- **IPC bridge** (`src/main/ipc/local-conversation-handlers.ts`) — Full preload API at `window.eve.localConversation.*` with start/stop/sendText + 5 event listeners (started, transcript, response, agent-finalized, error)
- **Barge-in support** — When the user starts speaking while TTS is active, speech synthesis stops immediately and the new input is processed

### Fixed — Post-Onboarding Local-Only Blockers

- **Post-onboarding conversation path** — App.tsx now continues local conversation after onboarding completes; previously local voice was gated to the onboarding interview only
- **Silent message loss** — `handleTextSend` now routes to local conversation when Gemini is unavailable instead of silently dropping messages
- **System event routing** — Scheduler, predictor, and system events route through the local conversation instead of being hardcoded to `geminiLive.sendTextToGemini`
- **AgentCreation local fallback** — `onComplete` callback works without a Gemini API key
- **ConnectionOverlay `isLocalMode`** — Shows correct connection status text for local-only users instead of suggesting they need a Gemini API key
- **TextInput connection indicator** — Visual status dot showing current connection state (local active, cloud connected, or disconnected)

---

## [3.5.2] — 2026-03-10 — Hotfix: Voice Interview Connection & SmartScreen Trust

### Fixed

- **Voice interview silent failure** — `useGeminiLive.connect()` now properly throws on missing Gemini API key instead of silently resolving, which left InterviewStep showing "Interview in progress" with no actual WebSocket connection
- **Instant connection failure feedback** — InterviewStep catches async connection errors immediately instead of waiting 30 seconds for a timeout; users see the retry/skip UI within seconds
- **SmartScreen trust for installer** — NSIS installer now bundles the code-signing certificate and installs it to Windows TrustedPublisher and Root stores during setup, preventing SmartScreen prompts for the app and future updates; certificate is cleanly removed on uninstall
- **Misleading error message** — Changed "add GEMINI_API_KEY to .env" to "add one in Settings → API Keys" for the desktop app context

---

## [3.5.1] — 2026-03-09 — Hotfix: GPU Detection & Model Downloads

### Fixed

- **NVIDIA Optimus laptop detection** — RTX laptops no longer incorrectly default to whisper tier
- **Model downloads stuck at 0/0** — HardwareStep passes detected tier to `getModelList()`
- **WebSocket close-code diagnostics** — Connection errors now include close code for debugging
- **Cross-platform GPU detection tests** — Test reliability fixes for GPU detection across platforms

---

## [3.5.0] — 2026-03-09 — Trust-First Onboarding

### Summary

Complete narrative redesign of the onboarding ceremony. The 6-step wizard is now an 8-step trust-first experience that tells the story of "the most trustworthy AI system in the world" — explaining local-first AI priority, Privacy Shield filtering, optional cloud credentials, and auto-downloading local models during hardware detection. DirectivesStep and EnginesStep are replaced by four new purpose-built steps.

### Changed — Onboarding Flow (6 → 8 steps)

- **Step restructure** — Awakening → Mission → Hardware → Privacy → ApiKeys → Environment → Interview → Reveal
- **DirectivesStep → MissionStep** — Asimov axiom cards replaced by five trust pillars: Local-First Intelligence, Zero-Knowledge Vault, Privacy Shield, Transparent Routing, Immutable Directives
- **EnginesStep → HardwareStep + ApiKeysStep** — Hardware detection and model downloading split from API key entry; models auto-download with real-time progress bars; API keys presented as optional
- **EnvironmentStep** — Agent name defaults to "Friday" with simple hint text; suggestion chips removed
- **OnboardingWizard** — Updated STEPS array, added `detectedTier` state passed from HardwareStep to ApiKeysStep, updated progress bar for 8-step flow

### Added — New Onboarding Steps

- **MissionStep** (`onboarding/MissionStep.tsx`) — "Your AI. Your Terms." header with five staggered trust-pillar cards using lucide-react icons (Cpu, Lock, Shield, Eye, Fingerprint)
- **HardwareStep** (`onboarding/HardwareStep.tsx`) — Three-phase step: hardware detection → tier recommendation with model list → auto-download with per-model progress bars; uses existing SetupWizard IPC (`window.eve.setup.*` and `window.eve.hardware.*`)
- **PrivacyStep** (`onboarding/PrivacyStep.tsx`) — Privacy Shield explainer with visual flow diagram (Your Message → Scrub PII → Cloud AI → Restore → Response) and PII category badge chips
- **ApiKeysStep** (`onboarding/ApiKeysStep.tsx`) — Optional cloud API key entry for Gemini, Anthropic, and OpenRouter; tier-aware messaging; always skippable

### Removed

- **DirectivesStep.tsx** — Replaced by MissionStep
- **EnginesStep.tsx** — Replaced by HardwareStep + ApiKeysStep

### Stats

- **Tests:** 4,701 / 4,701 (100% pass rate, 133 suites)
- **TypeScript:** 0 errors
- **Files changed:** 8 (+4 new steps, 2 edited, 2 deleted)

---

## [3.4.0] — 2026-03-09 — The Cyberpunk Onboarding Update

### Summary

Complete visual and UX overhaul of the onboarding ceremony. The 7-step wizard is now a 6-step cinematic experience with a cyberpunk HUD aesthetic — cursor-following glow, holographic diamond entity, cyber grid backgrounds, floating-label inputs, and laser-line buttons. Sovereignty and Identity steps are merged into a single **Environment** step. API key onboarding reduced from 7 to 3. Text input fallback added to voice interview.

### Changed — Onboarding Flow (7 → 6 steps)

- **Step consolidation** — Sovereignty (vault) and Identity (name/voice) merged into a single **EnvironmentStep** with both sections visible simultaneously
- **API key reduction** — Onboarding now shows only 3 keys (Gemini, Anthropic, OpenRouter); remaining providers configurable in Settings
- **AwakeningStep** — New "FRIDAY." typography with cyan accent dot, reduced to 20 particles, 4.5s auto-advance
- **DirectivesStep** — "System Foundation." header with full-width axiom cards using colored top borders
- **EnginesStep** — "Compute Architecture." header with segmented tier scale bar replacing 5-dot indicator; CyberInput floating-label fields
- **EnvironmentStep** — "Data Sovereignty." header; vault passphrase with strength meter + zero-knowledge badges (AES-256-GCM, Argon2id KDF, HMAC-SHA256); identity wallet with name suggestion chips, voice gender radio group, voice feel grid; auto-skips vault section if already initialized
- **InterviewStep** — Added full-width text input fallback for users without microphone access; `sendTextToGemini` prop threaded from App.tsx
- **RevealStep** — "{NAME}_ONLINE" display format with "Systems Nominal // Awaiting Command" subtext

### Added — Shared Onboarding Components

- **NextButton** (`onboarding/shared/NextButton.tsx`) — Reusable button with laser-line `::after` hover animation; variants: primary, secondary, skip
- **CyberInput** (`onboarding/shared/CyberInput.tsx`) — Input with floating label that animates up on focus/value; monospace option for passphrases
- **HolographicDiamond** (`onboarding/shared/HolographicDiamond.tsx`) — Rotating 45° square with pulsing border; `intense` prop for awakening/reveal glow
- **CyberGrid** (`onboarding/shared/CyberGrid.tsx`) — SVG pattern background with subtle drift animation
- **CursorGlow** (`onboarding/shared/CursorGlow.tsx`) — Mouse-following 300px radial gradient using `useRef` + `requestAnimationFrame` (zero re-renders)

### Added — CSS Foundation

- New CSS variables: `--diamond-border`, `--diamond-glow`
- New keyframes: `onb-diamond-rotate`, `onb-diamond-pulse`, `onb-grid-drift`, `onb-laser-sweep`, `onb-reveal-expand`
- New `.onb-next-btn` base class for laser-line button animations

### Removed

- **SovereigntyStep.tsx** — Vault setup merged into EnvironmentStep
- **IdentityStep.tsx** — Agent identity merged into EnvironmentStep

### Stats

- **Tests:** 4,701 / 4,701 (100% pass rate, 133 suites)
- **TypeScript:** 0 errors
- **Files changed:** 13 (+5 new shared components, 6 step rewrites, 2 deleted)
- **New shared components:** 5 (NextButton, CyberInput, HolographicDiamond, CyberGrid, CursorGlow)

---

## [3.3.0] — 2026-03-09 — The Privacy Shield Update

### Summary

Frontier model providers are now identity-blind compute nodes. A new **Privacy Shield** engine scrubs all PII from outbound API requests and rehydrates placeholders in responses — covering 14 files, 44 call sites, and every cloud provider path. Also fixes three user-facing bugs: voice interview connection failures, reinstall passphrase gate, and passphrase recovery.

### Added — Privacy Shield (`privacy-shield.ts`)

- **PII Scrubber Engine** — Regex-based detection for emails, phone numbers, SSNs, credit cards, IP addresses, dates of birth, physical addresses, and URLs containing PII
- **Named entity recognition** — OS username detection and registered known-name matching from user settings
- **Deterministic placeholders** — `[EMAIL_1]`, `[PHONE_1]`, `[NAME_1]` etc., consistent within a session via session nonce
- **Bidirectional mapping** — `scrub()` produces placeholders, `rehydrate()` restores originals client-side
- **Request-level API** — `scrubRequest()` handles full message arrays (string content + content-block formats)
- **Smart routing** — Cloud providers (Anthropic, OpenRouter, OpenAI, Gemini, Perplexity, ElevenLabs) get full scrub+rehydrate; local providers (Ollama, HuggingFace) pass through untouched

### Added — Privacy Shield Integration (14 files, 44 call sites)

- **LLM chokepoint** (`llm-client.ts`) — All `text()`, `complete()`, `stream()` calls to cloud providers auto-shielded
- **Main chat loop** (`server.ts`) — Direct Anthropic SDK, OpenRouter, and Gemini TTS calls shielded
- **Meeting intelligence** (`meeting-intelligence.ts`) — Refactored from direct fetch to `llmClient.text()` for automatic shield coverage
- **Search & RAG** (`semantic-search.ts`, `firecrawl.ts`, `perplexity.ts`) — Document embeddings, web search queries, and deep research scrubbed
- **Voice** (`agent-voice.ts`, `voice-audition.ts`, `audio-gen.ts`) — Spoken text and TTS prompts scrubbed
- **Media generation** (`multimedia-engine.ts`, `art-evolution.ts`, `video-gen.ts`) — Image/video/music generation prompts scrubbed
- **Agents** (`builtin-agents.ts`) — Agent prompt classification scrubbed
- **OpenAI services** (`openai-services.ts`) — DALL-E, embeddings, o3 reasoning, and Whisper transcription scrubbed+rehydrated
- **Provider init** (`providers/index.ts`) — Registers known names from user settings at startup

### Fixed — Voice Interview Connection

- **Root cause**: `gemini-audio-active` DOM event was listened for in `InterviewStep` but never dispatched
- **Fix 1** (`App.tsx`): Dispatch `window.dispatchEvent(new Event('gemini-audio-active'))` after `geminiLive.connect()` succeeds
- **Fix 2** (`InterviewStep.tsx`): Increased connection timeout from 10s to 30s (tool gathering on cold start takes time)

### Fixed — Reinstall Passphrase Gate

- **Root cause**: `App.tsx` lumped `!vaultInitialized || !vaultUnlocked` into passphrase gate; Windows uninstallers leave AppData, so vault files survive reinstall
- **Fix 1** (`App.tsx`): Split logic — uninitialized vault routes to onboarding, initialized-but-locked routes to passphrase gate
- **Fix 2** (`vault.ts`): Added `resetVaultFiles()` to wipe vault files from disk
- **Fix 3** (`index.ts` + `preload.ts` + `types.d.ts`): Wired `vault:reset-all` IPC handler that wipes vault and relaunches
- **Fix 4** (`PassphraseGate.tsx`): Added "Forgot passphrase? Start fresh" link with destructive-action confirmation dialog (skull icon, red warning, explicit "Erase & Start Fresh" button)

### Stats

- **Tests:** 4,701 / 4,701 (100% pass rate, 133 suites)
- **TypeScript:** 0 errors
- **Files changed:** 25 (+378 / -112 lines)
- **New files:** `privacy-shield.ts`
- **Privacy coverage:** 14 files, 44 scrub/rehydrate call sites, all cloud provider paths

---

## [3.2.0] — 2026-03-09 — The Fortified Update

### Summary

Ship-readiness hardening across safety, accessibility, infrastructure, and portability. Resolved **5 security blockers** and **14 warnings** identified in a comprehensive audit. Agent Friday is now single-instance locked, file-system confined, IPC error-bounded, auto-updating, accessible, theme-ready, and cross-platform Chrome-aware.

### Added — Safety & Integrity

- **Single-instance lock** — `app.requestSingleInstanceLock()` prevents vault data races from dual launches; second instance focuses the existing window
- **IPC error boundary** — Monkey-patched `ipcMain.handle` wraps all 580+ handlers in try/catch; renderer never receives internal stack traces
- **File system confinement** — All file handlers enforce `assertConfinedPath(os.homedir())` boundary; reads outside home directory throw `ConfinementError`
- **URL validation** — `shell.openExternal` calls in main window and Agent Office validate with `new URL()` before opening
- **COM Invoke sanitization** — PowerShell connector validates method names against `^[a-zA-Z_][a-zA-Z0-9_]*$` before string interpolation

### Added — Infrastructure

- **Auto-updater** — `electron-updater` with GitHub Releases provider; checks on launch + every 4 hours; user-prompted download and install; no silent updates
- **Native crash reporter** — `crashReporter.start()` captures V8-level crash dumps locally (no remote upload); complements existing JS-level crash logging
- **Production logger** — `src/main/utils/logger.ts` gates debug/info output behind `app.isPackaged`; renderer suppresses `console.log`/`console.debug` in production

### Added — Accessibility

- **ARIA landmarks** — All onboarding steps wrapped in `<section>` with descriptive `aria-label`
- **Custom control semantics** — Gender and voice feel pickers use `role="radiogroup"` / `role="radio"` with `aria-checked`
- **Form accessibility** — All inputs linked to `<label>` elements via `htmlFor`/`id`; `aria-required` on required fields; `aria-describedby` for hints
- **Live regions** — Status updates, errors, and the boot sequence terminal use `aria-live="polite"` / `role="status"` / `role="log"` / `role="alert"`
- **Progress bar** — Onboarding progress indicator has `role="progressbar"` with `aria-valuenow`/`aria-valuemin`/`aria-valuemax`
- **Decorative isolation** — Icons, waveforms, and terminal dots marked `aria-hidden="true"`

### Changed — Onboarding UX

- **Back navigation** — All middle steps (Directives through Interview) now have a ← Back button
- **RevealStep skip** — Click or keypress fast-forwards the 7.7s boot animation; "Click to skip" hint after 2s
- **Passphrase strength** — Color-coded meter (Weak → Fair → Good → Strong) with real-time feedback; minimum "Fair" required to proceed
- **InterviewStep resilience** — 10-second connection timeout with explicit failed state; Retry and Skip buttons; event-based connection confirmation replaces fragile timer
- **CSS variable migration** — ~90 hardcoded colors replaced with `var(--*)` references across all 8 onboarding files; 18 new custom properties added to global.css for opacity scales and onboarding surfaces

### Changed — Build & Distribution

- **Code splitting** — Vite `manualChunks` splits Three.js (525KB), react-markdown (165KB), and React core into separate cacheable chunks; main bundle reduced from ~1.2MB to ~512KB
- **Cross-platform Chrome** — `browser.ts` now discovers Chrome on Windows, macOS, and Linux using `platform()` detection with fallback to `CHROME_PATH` env var
- **Dependency triage** — Moved `three`, `react-markdown`, `remark-gfm`, `lucide-react` from dependencies to devDependencies (renderer-only, bundled by Vite)
- **Stale asarUnpack cleanup** — Removed dead `sodium-native` and `sharp` references from electron-builder config
- **Vite type reference** — Added `src/renderer/vite-env.d.ts` for `import.meta.env` TypeScript support
- **Publish config** — Added GitHub Releases provider to electron-builder for auto-updater compatibility

### Stats

- **Tests:** 4,701 / 4,701 (100% pass rate, 133 suites)
- **TypeScript:** 0 errors
- **Files changed:** 31 files across 2 sprints (+1,287 / -358 lines)
- **New files:** `updater.ts`, `logger.ts`, `vite-env.d.ts`

---

## [3.1.1] — 2026-03-09 — The Airtight Update

### Summary

Full test suite hardening — **4,701 tests, 0 failures** across 133 test suites. Fixed vision IPC channel alignment, wired local embedding pipeline, resolved libsodium ESM resolution for Vitest, cleaned up repo by removing 687 tracked files of development artifacts.

### Fixed

- **Vision IPC channel alignment** — Renamed vision pipeline channels (`vision:screen:capture` → `vision:screen:capture-screen`, etc.) to match source-of-truth handler registrations; updated all tests
- **Local embedding pipeline wiring** — Connected `local-embedding-provider.ts` to the embedding pipeline dispatcher for on-device vector embedding without API keys
- **libsodium ESM resolution** — Created `scripts/fix-libsodium-esm.js` postinstall patch that copies `libsodium-sumo.mjs` to where ESM relative imports expect it (fixes Vitest/ESM mode failures in vault crypto tests)
- **Passphrase KDF tests** — Migrated from removed `sodium-native` to `libsodium-wrappers-sumo` with WASM initialization
- **Adapter engine sandbox** — Added `require`, `module`, `exports` to VM sandbox context for connector execution
- **File manager mock contracts** — Added missing `birthtime` property to stat mocks
- **Tier-4 handler assertions** — Fixed argument count in `toggleHidden` handler test

### Changed

- **Repo cleanup** — Removed 687 tracked files:
  - `socratic-roadmaps/` (134 files) — Development planning artifacts
  - `tools/` (551 files) — Vendored third-party Python repos (browser-use, self-operating-computer)
  - `docs/SOCRATIC-FORGE.md` — Development methodology doc
  - `NEXUS_V2_ARCHITECTURE.md` — Superseded by `ARCHITECTURE.md`
- **Issue templates** — Moved `bug_report.md`, `feature_request.md`, `config.yml` from root to `.github/ISSUE_TEMPLATE/`
- **.gitignore** — Cleaned corrupted UTF-16 entries; added exclusions for development planning directories
- **README.md** — Updated test count to 4,701 tests across 133 suites (100% pass rate)

---

## [3.1.0] — 2026-03-09 — The Awakening Update

### Summary

Agent Friday gets a **cinematic 7-step onboarding wizard**, **multi-backend sub-agent TTS** (Gemini cloud + local Kokoro/Piper), a revamped **FridayFiles** file manager, **chat history** persistence, and hardened crypto primitives. ElevenLabs dependency removed — sub-agent voices now work with the same Gemini API key or fully offline via local TTS.

### Added — Cinematic Onboarding Wizard

- **OnboardingWizard.tsx** — 7-step first-run wizard replacing the old WelcomeGate + voice-only onboarding:
  - **Step 0: Awakening** — Animated splash with logo, particles, and tagline auto-advance
  - **Step 1: Directives** — Asimov's cLaws presented one at a time with scrolling reveal
  - **Step 2: Engines** — Hardware profiler HUD + tier-aware API key entry (reuses IPC from hardware-profiler)
  - **Step 3: Sovereignty** — Vault passphrase setup with data sovereignty explanation
  - **Step 4: Identity** — Agent name, voice gender, and voice feel picker
  - **Step 5: Interview** — Gemini Live voice interview with animated waveform visualisation
  - **Step 6: Reveal** — Terminal-style boot sequence with cinematic agent name reveal
- **Space Grotesk** font added for onboarding display text
- **lucide-react** added for onboarding iconography

### Added — Multi-Backend Sub-Agent TTS

- **agent-voice.ts** — Complete rewrite: ElevenLabs removed, replaced with dual-backend fallback chain:
  - **Gemini TTS** (cloud) — Uses the same Gemini API key as the main LLM; Gemini 2.0 Flash `generateContent` with `response_modalities: ['AUDIO']`
  - **Local TTS** (Kokoro/Piper) — Fully offline, no API key needed; lazy-loads on first call
  - **Graceful degradation** — Falls through to text-only if no TTS provider available
  - Provider order determined by `preferredProvider` setting: local/ollama users try local first
- **agent-personas.ts** — Rewritten: `voiceId: string` → `voices: VoiceMapping` with per-provider mappings:
  - Atlas: Iapetus (Gemini) / af_heart (Kokoro) — deep, authoritative
  - Nova: Aoede (Gemini) / af_bella (Kokoro) — warm, energetic
  - Cipher: Puck (Gemini) / am_adam (Kokoro) — precise, sharp
- **agent-runner.ts** — Updated to pass `persona.voices` (VoiceMapping) and use dynamic `contentType` from voice result

### Added — Chat History & File Management

- **chat-history.ts** — Persistent conversation history with JSON storage
- **chat-history-handlers.ts** — IPC handlers for chat history CRUD operations
- **FridayFiles.tsx** — Major revamp of the file manager UI with improved navigation, file operations, and visual polish
- **files-manager.ts** — Enhanced file management backend with new operations
- **files-handlers.ts** — Extended IPC handlers for file operations

### Added — Superpowers & Server Enhancements

- **superpower-sandbox.ts** — Enhanced sandboxed execution environment with improved security
- **superpower-store.ts** — Extended superpower discovery and installation capabilities
- **server.ts** — Enhanced Express API with new endpoints and improved routing

### Changed

- **App.tsx** — Simplified phase machine: `gate` and `onboarding` phases merged into single `onboarding` phase handled by OnboardingWizard
- **DesktopViz.tsx** — Minor rendering improvements
- **useGeminiLive.ts** — Enhanced voice session handling
- **useWakeWord.ts** — Minor improvements
- **global.css** — Space Grotesk font import added
- **types.d.ts** — New window API type declarations for onboarding
- **preload.ts** — New IPC bridge channels for onboarding, chat history, and file operations
- **index.ts** — Updated main process initialization for new handlers
- **mcp-config.ts** — Configuration updates
- **personality.ts** — Enhanced personality composition
- **vault.ts** — Minor vault improvements

### Changed — Crypto Hardening

- **passphrase-kdf.ts** — Refactored Argon2id key derivation with improved error handling
- **secure-buffer.ts** — Enhanced guard-paged memory management

### Removed

- **WelcomeGate.tsx** — Fully replaced by OnboardingWizard (Engines step subsumes all API key entry logic)
- **ElevenLabs dependency** — Sub-agent TTS no longer requires ElevenLabs; uses Gemini TTS (cloud) or Kokoro/Piper (local)

---

## [3.0.0] — 2026-03-08 — The Polymath Update

### Summary

Agent Friday becomes a **true polymath creative agent** — capable of generating images, videos, music, sound effects, speech, podcasts, and code through a unified creative engine. 7 implementation tracks, 22 phases, 6 new connector modules, 1 new UI app, and a unified creative dispatch router. Every creative domain flows through **The Stage** — a single output feed with domain filtering, pinning, and export.

### Added — Track A: The Voice (TTS/STT Integration)

- **tts-binding.ts** — Google Cloud TTS integration with streaming audio synthesis, voice selection (Neural2/Studio/WaveNet), SSML support, audio format configuration, and automatic chunking for long text
- **whisper-binding.ts** — Local Whisper STT via whisper.cpp with model management (tiny→large-v3), language detection, audio preprocessing (WAV conversion, resampling), and configurable transcription parameters
- **Voice pipeline IPC** — Full renderer↔main bridge for voice synthesis and speech recognition

### Added — Track B: The Canvas (ComfyUI Local Image Generation)

- **comfyui.ts** — ComfyUI connector (17 tools) for local Stable Diffusion image generation:
  - `comfyui_generate` — txt2img with configurable model, sampler, steps, CFG, resolution
  - `comfyui_img2img` — Image-to-image transformation with denoising strength
  - `comfyui_list_models` — Enumerate available checkpoint models
  - `comfyui_list_samplers` — Available sampling algorithms
  - `comfyui_list_schedulers` — Available noise schedulers
  - `comfyui_get_history` — Generation history retrieval
  - `comfyui_get_queue` — Queue status monitoring
  - `comfyui_interrupt` — Cancel running generation
  - `comfyui_upload_image` — Upload reference images for img2img
  - `comfyui_get_image` — Retrieve generated images by filename
  - Plus 7 workflow management tools for saving, loading, and executing custom ComfyUI workflows
- **Auto-detection** — Probes localhost:8188 for ComfyUI availability with configurable host/port
- **Queue polling** — Monitors generation progress with 500ms polling until completion

### Added — Track C: The Director (AI Video Generation)

- **video-gen.ts** — Video generation connector (10 tools) powered by Gemini VEO 3:
  - `video_generate` — Text-to-video generation with aspect ratio, duration, and person generation controls
  - `video_generate_from_image` — Image-to-video with reference frame
  - `video_check_status` — Poll generation jobs for completion
  - `video_download` — Download completed videos to local storage
  - `video_list_jobs` — List all generation jobs with status tracking
  - `video_get_job` — Get detailed job information
  - `video_cancel` — Cancel in-progress generation
  - `video_stitch` — FFmpeg-powered video concatenation
  - `video_convert` — Format conversion (MP4, WebM, MOV, AVI, GIF) with quality control
  - `video_get_info` — FFprobe metadata extraction (duration, resolution, codec, bitrate)
- **Gemini API integration** — Uses `generateVideos` endpoint with operation polling
- **FFmpeg pipeline** — Local video processing for stitching, conversion, and info extraction

### Added — Track D: The Composer (Audio & Music Generation)

- **audio-gen.ts** — Audio generation connector (16 tools) with dual-engine architecture:
  - **Music generation** (Gemini 2.0 Flash): `audio_generate_music` — text-to-music with style, mood, tempo, duration
  - **Sound effects** (Gemini 2.0 Flash): `audio_generate_sfx` — procedural sound effect synthesis
  - **Speech synthesis** (ElevenLabs): `audio_generate_speech` — high-quality TTS with voice selection, stability/similarity boost, style control
  - **Voice cloning** (ElevenLabs): `audio_clone_voice` — instant voice cloning from audio samples
  - **Podcast creation**: `audio_create_podcast` — multi-voice podcast generation with script-to-audio pipeline
  - **Voice listing**: `audio_list_voices` — enumerate available ElevenLabs voices
  - **Audio mixing** (FFmpeg): `audio_mix` — multi-track audio mixing with volume control
  - **Audio effects** (FFmpeg): `audio_apply_effects` — reverb, echo, pitch shift, tempo change, normalize, fade, bass/treble boost
  - **Format conversion** (FFmpeg): `audio_convert` — transcode between MP3, WAV, FLAC, OGG, AAC, M4A with bitrate/sample rate control
  - **Audio info** (FFprobe): `audio_get_info` — metadata extraction (duration, format, bitrate, channels, sample rate)
  - **Audio trim** (FFmpeg): `audio_trim` — precise time-based audio cutting
  - **Audio concatenate** (FFmpeg): `audio_concatenate` — join multiple audio files
  - Plus 4 management tools for job tracking and listing
- **ElevenLabs integration** — Full v1 API client for TTS, voice cloning, and voice management
- **FFmpeg audio pipeline** — 8 audio processing operations via local FFmpeg

### Added — Track E: The Coder (Coding Agent Kit)

- **coding-kit.ts** — Coding agent connector (15 tools) providing full-stack development capabilities:
  - `coding_read_file` — Read files with line-range support
  - `coding_write_file` — Create or overwrite files
  - `coding_edit_file` — Surgical text replacement edits
  - `coding_list_directory` — Directory listing with metadata
  - `coding_search_files` — Glob-pattern file discovery
  - `coding_search_content` — Regex content search across files
  - `coding_execute_shell` — Shell command execution with timeout
  - `coding_get_diagnostics` — TypeScript/ESLint error reporting
  - `coding_ask_ai` — Multi-provider LLM queries (Gemini, Claude, OpenAI, Ollama) for code reasoning
  - `coding_git_status` / `coding_git_diff` / `coding_git_log` — Git operations
  - `coding_create_session` / `coding_get_session` / `coding_list_sessions` — Persistent coding sessions with context tracking
- **GitLoader integration** — Loads external repo ([agent-fridays-coding-kit](https://github.com/FutureSpeakAI/agent-fridays-coding-kit)) for the AI coding agent runtime
- **Multi-provider AI** — Routes coding questions to the best available LLM based on task complexity
- **Session management** — Persistent coding sessions with file history, git context, and AI conversation state

### Added — Track F: Polymath Creative Router

- **polymath-router.ts** — Unified creative dispatch connector (4 tools):
  - `polymath_classify` — Intent classification across 8 creative domains (image, video, music, sfx, speech, podcast, code, document) with confidence scoring and parameter extraction
  - `polymath_dispatch` — Intelligent routing to the best available tool for any creative request, with automatic parameter mapping and fallback chains
  - `polymath_capabilities` — Dynamic capability inventory reporting what creative tools are available
  - `polymath_batch` — Multi-item creative batch processing with sequential execution and result aggregation
- **Domain classification** — 140+ keyword patterns across 8 domains with compound-intent resolution
- **Fallback chains** — If the primary tool is unavailable, automatically falls back to alternatives (e.g., ComfyUI → Nano Banana 2 → DALL-E 3 for image generation)
- **Parameter extraction** — Parses natural language for aspect ratios, resolutions, durations, styles, and model preferences

### Added — Track G: The Stage (Creative Output Presenter)

- **stage-presenter.ts** — Unified creative output feed connector (7 tools):
  - `stage_push_output` — Record a new creative output with domain, renderer hint, metadata
  - `stage_list_outputs` — List recent outputs with domain filtering and pin-only mode
  - `stage_get_output` — Retrieve a single output by ID with full metadata
  - `stage_clear_outputs` — Clear output history (by domain or all) with pinned-item preservation
  - `stage_get_stats` — Aggregate statistics across all creative domains
  - `stage_pin_output` — Pin/unpin outputs to keep them visible
  - `stage_export_feed` — Export the feed as structured JSON for archival
- **FridayStage.tsx** — React component for the unified creative UI:
  - **Create tab** — Prompt input with real-time intent classification and one-click dispatch
  - **Gallery tab** — Domain-filtered output grid with pin toggle and detail modal
  - **Pipelines tab** — Active pipeline tracking with progress indicators
- **Domain-to-renderer mapping** — Automatic render hints (image-viewer, video-player, audio-player, code-block, document-frame) for UI presentation
- **500-output capacity** — In-memory store with smart eviction preserving pinned items

### Changed

- **registry.ts** — 6 new connector modules registered: comfyui, coding-kit, video-gen, audio-gen, polymath-router, stage-presenter
- **app-registry.ts** — New "Stage" app (🎭) added to the media category with Ctrl+Shift+G shortcut
- **git-loader.ts** — Updated for coding-kit repo integration
- **superpowers-registry.ts** — Updated for new creative domain superpowers
- **tts-binding.ts** — Enhanced for Polymath voice pipeline integration
- **whisper-binding.ts** — Enhanced for Polymath speech-to-text pipeline
- **tier-recommender.ts** — Updated hardware tier recommendations for creative workloads

### Stats

- Total source files: 300+ (was 294)
- Total tests: **4,701 across 133 test suites** — all passing (was 4,632 across 132)
- New connector modules: 6 (comfyui, coding-kit, video-gen, audio-gen, polymath-router, stage-presenter)
- New tools: 69 (17 + 15 + 10 + 16 + 4 + 7)
- Creative domains: 8 (image, video, music, sfx, speech, podcast, code, document)
- New React components: 1 (FridayStage)

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
