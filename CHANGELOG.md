# Changelog

All notable changes to Agent Friday are documented in this file.

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
