## Onboarding Wizard Flow
**Status:** Current | **Type:** User-initiated (first launch) | **Complexity:** High
**Last analyzed:** 2026-03-24

### Overview
The onboarding wizard is a cinematic 10-step first-run experience that takes the user from a fresh install to a fully configured agent. It collects hardware capabilities, API keys, local model preferences, agent identity (name/gender/voice), vault passphrase, privacy settings, integrations, and personality calibration. The wizard supports crash recovery via checkpointing and offers three personality paths: full voice interview, manual slider calibration, or skip-to-defaults.

### Flow Boundaries
- **Start:** `App.tsx` detects `onboardingComplete === false` from `window.eve.onboarding.isComplete()` and sets `appPhase = 'onboarding'`
- **End:** `RevealStep` calls `window.eve.onboarding.clearCheckpoint()` then `onComplete(agentName)`, which sets `appPhase = 'ready'` in App.tsx

### Quick Reference
| Component | File | Purpose |
|-----------|------|---------|
| App (orchestrator) | `src/renderer/App.tsx` | Determines if onboarding is needed; renders `<OnboardingWizard>` when `appPhase === 'onboarding'` |
| OnboardingWizard | `src/renderer/components/OnboardingWizard.tsx` | Step state machine; manages `currentStep`, transitions, checkpointing |
| AwakeningStep | `src/renderer/components/onboarding/AwakeningStep.tsx` | Cinematic splash — "FRIDAY." title + tagline; auto-advances after 4.5s |
| MissionStep | `src/renderer/components/onboarding/MissionStep.tsx` | Trust pillars presentation (5 cards); staggered reveal |
| HardwareStep | `src/renderer/components/onboarding/HardwareStep.tsx` | GPU/VRAM/RAM detection, Ollama check, tier assignment, model downloads |
| ProvidersStep | `src/renderer/components/onboarding/ProvidersStep.tsx` | All 8 API key inputs + routing preference selector |
| ModelsStep | `src/renderer/components/onboarding/ModelsStep.tsx` | Local model selection (Chat LLM, Whisper STT, TTS, Embeddings) |
| VoiceIdentityStep | `src/renderer/components/onboarding/VoiceIdentityStep.tsx` | Agent name, gender, voice feel, voice engine selection, live preview |
| PrivacyPermissionsStep | `src/renderer/components/onboarding/PrivacyPermissionsStep.tsx` | Vault passphrase creation, privacy toggles, memory depth |
| IntegrationsStep | `src/renderer/components/onboarding/IntegrationsStep.tsx` | Google Calendar, Obsidian, Telegram gateway, auto-launch toggles |
| PersonalityStep | `src/renderer/components/onboarding/PersonalityStep.tsx` | Path selector: Interview / Manual Sliders / Skip |
| InterviewStep | `src/renderer/components/onboarding/InterviewStep.tsx` | Voice/text interview with waveform visualization + transcript |
| RevealStep | `src/renderer/components/onboarding/RevealStep.tsx` | Terminal boot sequence + cinematic agent name reveal |
| onboarding.ts | `src/main/onboarding.ts` | Voice map, default profiles, prompt builders, tool declarations |
| onboarding-handlers.ts | `src/main/ipc/onboarding-handlers.ts` | IPC handlers for onboarding, psych profile, feature setup |
| settings.ts | `src/main/settings.ts` | FridaySettings persistence, `saveAgentConfig()`, `setSetting()` |
| vault.ts | `src/main/vault.ts` | Vault initialization/unlock triggered from PrivacyPermissionsStep |
| preload.ts | `src/main/preload.ts:111-145` | `window.eve.onboarding` namespace (IPC bridge) |
| types.d.ts | `src/renderer/types.d.ts:357-388` | TypeScript declarations for `window.eve.onboarding` |

### Steps

**1. App Phase Detection** (`App.tsx:987-1049`)
On mount, App.tsx calls `window.eve.onboarding.isComplete()`. If false and the vault is not initialized, it sets `appPhase = 'onboarding'`, rendering `<OnboardingWizard>` as a full-screen overlay (z-index 200). All other UI (chat, HUD, desktop viz) is hidden.

**2. Checkpoint Recovery** (`OnboardingWizard.tsx:85-100`)
On mount, the wizard calls `window.eve.onboarding.getCheckpoint()`. If a checkpoint exists (from a crash), the wizard restores `currentStep` and `identityChoices` from the saved state.

**3. Step 1 - Awakening** (`AwakeningStep.tsx`)
Cinematic splash: phases through `logo -> title -> tagline -> ready` on timers (800ms, 1800ms, 2800ms). Auto-advances after 4500ms, or immediately on click/Enter/Space. No IPC calls. Pure UI animation.

**4. Step 2 - Mission** (`MissionStep.tsx`)
Displays 5 trust pillar cards (Local-First, Zero-Knowledge Vault, Privacy Shield, Transparent Routing, Immutable Directives) with staggered reveal (300ms intervals). "I'm In" button appears at 2500ms. No IPC calls.

**5. Step 3 - Hardware** (`HardwareStep.tsx`)
- **Detecting phase:** Calls `window.eve.hardware.detect()` to get GPU/VRAM/RAM profile, then `window.eve.hardware.getTier(profile)` for tier assignment (whisper/light/standard/full/sovereign), then `window.eve.hardware.getModelList(tier)` for recommended models.
- **Checking Ollama phase:** For non-whisper tiers, calls `window.eve.ollama.getHealth()` to check if Ollama is running. If not, shows install instructions with a link to ollama.com/download.
- **Recommending phase:** Shows hardware summary, tier badge, tier override radio buttons (user can manually select a different tier), and model list. User can "Install Models" or "Skip Downloads".
- **Downloading phase:** Calls `window.eve.setup.start()`, `setup.confirmTier(tier)`, subscribes to `setup.onDownloadProgress/onComplete/onError`, then calls `setup.startDownload()`. Shows per-model progress bars.
- **Complete phase:** Shows installed model count. Calls `setup.complete()` on continue.
- **Output:** Returns selected `TierName` to OnboardingWizard via `onComplete(tier)`.

**6. Step 4 - Providers** (`ProvidersStep.tsx`)
- Loads existing settings via `window.eve.settings.get()` to show already-saved keys.
- Presents 8 API key inputs in 3 sections: Reasoning Engine (Anthropic, OpenRouter, OpenAI, HuggingFace), Voice & Conversation (Gemini, ElevenLabs), Web Intelligence (Perplexity, Firecrawl).
- Each key has debounced validation (800ms) via `window.eve.settings.validateApiKey(keyType, value)` which runs server-side HTTP validation in main process.
- Includes routing preference radio group (Anthropic Direct / OpenRouter / Local First / Auto).
- On save: validates all entered keys, then calls `window.eve.settings.setApiKey(id, value)` for each, saves extra inputs (model IDs, endpoints) via `settings.set()`, saves routing preference.
- Keys with `extraInput` (OpenRouter model ID, HuggingFace endpoint) get additional text inputs.

**7. Step 5 - Models** (`ModelsStep.tsx`)
- Checks Ollama health on mount.
- Four selection sections: Chat Model (llama3.2/llama3.1:8b/Custom/None), Whisper STT (tiny/base/small/None), TTS (Chatterbox/Kokoro/Cloud/None), Embeddings (nomic-embed-text/None).
- Calculates total disk + VRAM from selections. Defaults are tier-aware.
- On save: persists `voiceEngine`, `localModelId`, `localModelEnabled`, `whisperModel`, `embeddingModel` via `settings.set()`. Pulls missing Ollama models via `ollama.pullModel()`.

**8. Step 6 - Voice Identity** (`VoiceIdentityStep.tsx`)
- Agent name input (default "Friday", max 24 chars).
- Gender radio buttons (Male/Female/Neutral).
- Voice Feel selector (Warm/Sharp/Deep/Soft/Bright) with color-coded cards.
- Voice Engine radio group (Auto/Chatterbox/Kokoro/Cloud/Text Only).
- Voice preview section: maps feel+gender to Gemini voice name via `VOICE_MAP`, "Try" button calls `window.eve.voice.profiles.preview(voiceName)`.
- On continue: saves voice engine via `window.eve.settings.setVoiceEngine(engine)`.
- **Output:** Updates `identityChoices` in OnboardingWizard state (agentName, gender, voiceFeel).

**9. Step 7 - Privacy & Permissions** (`PrivacyPermissionsStep.tsx`)
- **Sovereign Vault:** Checks `window.eve.vault.isInitialized()`. If not initialized, shows passphrase creation form with strength meter (weak/fair/good/strong), confirmation field, and "Initialize Vault" button. On submit: calls `window.eve.vault.initializeNew(passphrase)` which triggers Argon2id KDF (256MB, 4 ops) + key derivation + canary creation + vault metadata write. Shows "Vault initialized and secured" with crypto badges (AES-256-GCM, Argon2id KDF, HMAC-SHA256, Zero-Knowledge).
- **Privacy Controls:** Three toggles (PII Filtering=on, Telemetry=off, Local Processing=on), each saved via `settings.set(key, value)`.
- **Memory Depth:** Radio group (Minimal/Standard/Comprehensive), saved via `settings.set('memoryDepth', value)`.
- **Continue is blocked until vault is initialized** (`disabled={!vaultReady}`).

**10. Step 8 - Integrations** (`IntegrationsStep.tsx`)
- Google Calendar: "Connect with Google" button calls `window.eve.calendar.authenticate()`.
- Obsidian Vault: Text input for vault path, saved via `window.eve.settings.setObsidianVaultPath(path)`.
- Messaging Gateway: Toggle for gateway enabled (via `window.eve.gateway.setEnabled()`), Telegram bot token + owner ID (via `settings.setTelegramConfig()`). Discord shown as "Coming soon".
- System: Auto-launch toggle (`settings.setAutoLaunch()`), file watcher toggle (`settings.set('fileWatcherEnabled')`).
- All integrations are optional; "Continue" always available.

**11. Step 9 - Personality** (`PersonalityStep.tsx`)
Three paths presented as cards:
- **"Interview Me"** (voice/text interview): calls `onComplete('interview')` -> OnboardingWizard navigates to InterviewStep.
- **"Manual Calibration"** (5 sliders): Shows sliders for Communication Style, Emotional Tone, Initiative Level, Humor, Formality. On complete: saves `personalitySliders` JSON to settings, calls `onComplete('firstContact')` -> OnboardingWizard navigates to InterviewStep with `firstContact=true`.
- **"Skip -- Use Defaults"**: calls `onComplete('skip')` -> OnboardingWizard navigates directly to RevealStep.

**12. Step 9b - Interview** (`InterviewStep.tsx`)
- Fetches canonical VOICE_MAP and DEFAULT_PROFILES from main process via `window.eve.onboarding.getDefaults()`.
- After 1200ms delay, calls `connectVoice()` (passed from App.tsx) which starts either Gemini Live or local-first (Whisper+Ollama+TTS) voice session.
- Shows animated waveform, real-time transcript panel, and text input fallback.
- Listens for DOM events: `interview-user-transcript`, `interview-ai-response`, `interview-processing-state`, `interview-connection-failed`.
- Connection stage monitoring via `window.eve.connectionStage` IPC for real-time progress, with legacy timeout fallback (120s).
- Voice session uses onboarding-specific tools from `buildAllOnboardingToolDeclarations()`: `acknowledge_introduction`, `save_intake_responses`, `transition_to_customization`, `finalize_agent_identity`.
- When `finalize_agent_identity` tool is called by the AI, the main process handler (`onboarding:finalize-agent`) saves agent config, initializes feature setup, signs identity, and dispatches `agent-finalized` DOM event.
- On `agent-finalized`: advances to RevealStep with the chosen agent name.
- **Skip path:** Calls `window.eve.onboarding.finalizeAgent()` with default profile for the chosen gender.
- Safety timeout: 5 minutes without finalization shows "Continue with Defaults" nudge.

**13. Step 10 - Reveal** (`RevealStep.tsx`)
- Terminal boot sequence: 16 lines scroll with staggered timing (0-4500ms), including "Loading personality vectors", "Sovereign vault attached", "cLaws directives SIGNED", "Awakening..."
- Agent name reveal: "{NAME}_ONLINE" with cyan glow effect.
- Click/keypress fast-forwards the animation.
- "Begin" button appears after animation completes.
- On complete: calls `window.eve.onboarding.clearCheckpoint()` to remove crash recovery data, then calls `onComplete(agentName)` which triggers App.tsx to set `appPhase = 'ready'` and start normal operation.

### IPC Channels Used
| Channel | Direction | Payload |
|---------|-----------|---------|
| `onboarding:is-first-run` | Renderer -> Main | none -> `boolean` |
| `onboarding:is-complete` | Renderer -> Main | none -> `boolean` |
| `onboarding:get-config` | Renderer -> Main | none -> `AgentConfig` |
| `onboarding:get-tool-declarations` | Renderer -> Main | none -> tool declaration array |
| `onboarding:get-first-greeting` | Renderer -> Main | none -> `string` (prompt) |
| `onboarding:get-defaults` | Renderer -> Main | none -> `{ voiceMap, defaultProfiles }` |
| `onboarding:finalize-agent` | Renderer -> Main | `AgentConfig` -> `{ success: boolean }` |
| `onboarding:save-checkpoint` | Renderer -> Main | `{ step, identityChoices }` -> void |
| `onboarding:get-checkpoint` | Renderer -> Main | none -> checkpoint or null |
| `onboarding:clear-checkpoint` | Renderer -> Main | none -> void |
| `vault:is-initialized` | Renderer -> Main | none -> `boolean` |
| `vault:initialize-new` | Renderer -> Main | `passphrase: string` -> `{ ok, error? }` |
| `settings:get` | Renderer -> Main | none -> masked settings object |
| `settings:set` | Renderer -> Main | `(key, value)` -> void |
| `settings:set-api-key` | Renderer -> Main | `(keyType, value)` -> void |
| `settings:validate-api-key` | Renderer -> Main | `(keyType, value)` -> `{ valid, error? }` |
| `settings:set-obsidian-vault-path` | Renderer -> Main | `vaultPath: string` -> void |
| `settings:set-auto-launch` | Renderer -> Main | `enabled: boolean` -> void |
| `settings:set-telegram-config` | Renderer -> Main | `(botToken, ownerId)` -> void |
| `hardware:detect` | Renderer -> Main | none -> hardware profile |
| `hardware:get-tier` | Renderer -> Main | `profile` -> tier string |
| `hardware:get-model-list` | Renderer -> Main | `tier` -> model name array |
| `ollama:get-health` | Renderer -> Main | none -> `{ running: boolean }` |
| `ollama:is-model-available` | Renderer -> Main | `modelName` -> `boolean` |
| `ollama:pull-model` | Renderer -> Main | `modelName` -> void |
| `setup:start` | Renderer -> Main | none -> void |
| `setup:confirm-tier` | Renderer -> Main | `tier` -> void |
| `setup:start-download` | Renderer -> Main | none -> void |
| `setup:skip` | Renderer -> Main | none -> void |
| `setup:complete` | Renderer -> Main | none -> void |
| `setup:event:download-progress` | Main -> Renderer | download progress array |
| `setup:event:complete` | Main -> Renderer | none |
| `setup:event:error` | Main -> Renderer | `{ error: string }` |
| `psych:save-intake` | Renderer -> Main | `IntakeResponses` -> `{ success }` |
| `psych:generate` | Renderer -> Main | `IntakeResponses` -> `PsychologicalProfile` |

### State Changes

**Settings persisted during onboarding:**
- `onboardingComplete: true` (via `saveAgentConfig`)
- `agentName`, `agentVoice`, `agentGender`, `agentAccent`, `agentBackstory`, `agentTraits`, `agentIdentityLine`, `userName` (via `saveAgentConfig`)
- `geminiApiKey`, `anthropicApiKey`, + 6 other API keys (via `setApiKey`)
- `preferredProvider` (routing preference)
- `localModelId`, `localModelEnabled`, `whisperModel`, `embeddingModel`, `voiceEngine` (via `setSetting`)
- `piiFiltering`, `telemetry`, `localProcessing`, `memoryDepth` (privacy settings)
- `personalitySliders` (JSON string, manual calibration only)
- `obsidianVaultPath`, `autoLaunch`, `googleCalendarTokens`, `telegramBotToken`, `telegramOwnerId`, `gatewayEnabled`
- `intakeResponses`, `psychologicalProfile` (interview path only)
- `featureSetupState`, `featureSetupComplete: true`
- `onboardingCheckpoint` (cleared on completion)

**Vault files created (PrivacyPermissionsStep):**
- `.vault-salt` (16-byte random salt)
- `.vault-canary` (encrypted known plaintext for passphrase verification)
- `.vault-meta.json` (`{ version: 2, initialized: true, createdAt }`)

**Identity signing:** After `saveAgentConfig`, the identity JSON is cryptographically signed via `integrityManager.signIdentity()`.

### Error Scenarios

1. **Vault initialization failure:** PrivacyPermissionsStep shows error message; Continue button remains disabled until vault is successfully initialized.
2. **API key validation failure:** ProvidersStep shows per-key error hint (e.g., "Gemini keys start with 'AIza'", "API key is invalid or has been revoked"). Save is blocked until keys validate.
3. **Ollama not running:** HardwareStep shows install instructions and "Check Again" / "Skip -- Use Cloud Only" buttons.
4. **Model download failure:** HardwareStep shows per-model failure status with "Continue Anyway" and "Skip Remaining" options.
5. **Voice connection failure in Interview:** InterviewStep shows failure detail + recovery suggestion (e.g., "Install Ollama for local voice or add a Gemini API key"). Offers "Retry" and "Skip Interview" (applies defaults).
6. **Interview timeout (5 min):** Shows "Continue with Defaults" nudge without terminating the active session.
7. **App crash during onboarding:** On restart, checkpoint recovery restores the last completed step and identity choices. Checkpoint is saved on every `goTo()` transition.
8. **Settings save failure:** The `SettingsManager.save()` serializes writes via a promise chain. Errors propagate to callers. The `setSetting` guard blocks sensitive fields (API keys, bot tokens) from the generic setter.
