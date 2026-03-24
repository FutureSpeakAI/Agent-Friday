# Application Boot Sequence

## Quick Reference

| Property | Value |
|----------|-------|
| **Status** | Active |
| **Type** | Lifecycle / Initialization |
| **Complexity** | Very High (2-phase boot, 50+ subsystem inits, vault-gated secret engines) |
| **Last Analyzed** | 2026-03-24 |

## Overview

Agent Friday uses a two-phase boot sequence. Phase A initializes the UI shell, settings, memory, Express server, MCP client, and all non-secret-dependent engines. The vault remains locked until the user enters their passphrase via the PassphraseGate component in the renderer. Phase B (triggered by `vault:unlock` or `vault:initialize-new` IPC) unlocks the vault, re-reads encrypted settings/memory, injects the HMAC signing key, initializes the integrity system, and starts the agent network. The renderer mounts React into a sandboxed BrowserWindow with context isolation, CSP, and navigation blocking.

## Flow Boundaries

| Boundary | Location |
|----------|----------|
| **Start** | `electron.app.whenReady()` fires in `src/main/index.ts:382` |
| **End** | Renderer receives `vault:boot-complete` event and transitions to main UI |

## Component Reference

| Component | File | Purpose |
|-----------|------|---------|
| Main Entry | `src/main/index.ts` | Electron main process: window creation, subsystem init, IPC registration |
| Preload Bridge | `src/main/preload.ts` | contextBridge exposing `window.eve.*` (~1747 lines, 40+ IPC namespaces) |
| Renderer Entry | `src/renderer/main.tsx` | React mount with ErrorBoundary, global error handlers |
| Root Component | `src/renderer/App.tsx` | Zustand store, app phase routing (passphrase -> onboarding -> main) |
| Settings Manager | `src/main/settings.ts` | Persistent settings: `{userData}/friday-settings.json` |
| PassphraseGate | `src/renderer/components/PassphraseGate.tsx` | Vault unlock/init UI |
| IPC Registry | `src/main/ipc/index.ts` | Barrel export of 50+ handler registration functions |
| Vault | `src/main/vault.ts` | Sovereign vault: Argon2id KDF, encrypted file I/O |
| Integrity Manager | `src/main/integrity/index.ts` | HMAC signing, core law verification, safe mode |
| Consent Gate | `src/main/consent-gate.ts` | Centralized consent for side-effect actions |

## Detailed Flow

### Phase 0: Pre-Ready (`index.ts:1-252`)

1. **Global error handlers** (`index.ts:12-63`): `uncaughtException` and `unhandledRejection` handlers write sanitized crash logs (API keys redacted via regex). On uncaught exception, vault and HMAC keys are zeroed synchronously.

2. **Native crash reporter** (`index.ts:68-74`): `crashReporter.start()` with local-only dump storage.

3. **TLS hardening** (`index.ts:81-95`): Detects and removes `NODE_TLS_REJECT_UNAUTHORIZED=0` at startup, then guards against runtime re-disabling with a 5-second periodic check.

4. **Signal handling** (`index.ts:101-106`): `SIGTERM` and `SIGINT` funneled into `app.quit()` for graceful shutdown.

5. **Single instance lock** (`index.ts:248-252`): `app.requestSingleInstanceLock()` prevents multiple instances from racing on the vault.

### Phase A: UI Shell + Non-Secret Engines (`index.ts:382-807`)

`app.whenReady()` fires and begins the main initialization cascade:

#### A1. IPC Error Boundary (`index.ts:388-398`)
Wraps `ipcMain.handle` so all handler errors are sanitized before reaching the renderer. Internal details (file paths, stack traces) are logged server-side but never sent to the renderer.

#### A2. Settings (`index.ts:400-411`)
`settingsManager.initialize()` reads `{userData}/friday-settings.json`. If `autoLaunch` is enabled in production, sets login item settings.

#### A3. LLM Providers (`index.ts:414-419`)
`initializeProviders()` configures Anthropic/Gemini/OpenRouter clients using API keys from settings.

#### A4. Memory System (`index.ts:422-427`)
`memoryManager.initialize()` loads 3-tier memory (long-term facts, medium-term observations, episodic episodes).

#### A5. Express Server (`index.ts:434-435`)
`startServer()` launches the Express API server (default port 3333) that serves the renderer and handles API routes.

#### A6. MCP Client (`index.ts:438-443`)
`mcpClient.connect()` establishes the Model Context Protocol connection.

#### A7. Content Security Policy (`index.ts:448-478`)
CSP headers are injected via `session.defaultSession.webRequest.onHeadersReceived`:
- `script-src 'self'` (no inline scripts)
- `style-src 'self' 'unsafe-inline' https://fonts.googleapis.com` (Vite HMR needs inline styles)
- `connect-src` allows `generativelanguage.googleapis.com`, `api.anthropic.com`, `openrouter.ai`
- `object-src 'none'`, `frame-ancestors 'none'`

#### A8. Permission Grants (`index.ts:481-484`)
Auto-grants: `media`, `mediaKeySystem`, `display-capture`, `audioCapture`.

#### A9. Window Creation (`index.ts:282-379`, called at `index.ts:486`)

`createWindow()` creates the BrowserWindow:
- **Dimensions**: 1400x900, min 900x600, maximized on show
- **Security**: `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true`
- **Preload**: `path.join(__dirname, 'preload.js')`
- **Style**: Frameless with `titleBarOverlay` (custom title bar)
- **Navigation blocking** (`index.ts:320-326`): Only `http://localhost:` and `file://` allowed
- **Popup blocking** (`index.ts:330-341`): `setWindowOpenHandler` denies all, opens external URLs via `shell.openExternal`
- **Loading**: In dev with `VITE_DEV_SERVER=1`, loads `http://localhost:5199`; otherwise loads `http://localhost:{serverPort}`
- **Close behavior**: On close, window hides (to tray) unless `isQuitting` is true

Cross-references are established: `setMainWindow()`, `setMainWindowForSelfImprove()`, `setConsentWindow()`.

#### A10. System Tray (`index.ts:498-536`)
Tray icon with context menu (Show/Quit). Double-click shows and focuses window.

#### A11. Global Hotkey (`index.ts:539-546`)
`Ctrl+Shift+N` toggles window visibility.

#### A12. Engine Initialization Cascade (`index.ts:549-806`)

50+ subsystems are initialized, most via `.catch()` to prevent cascade failure:

| Order | Engine | Blocking? |
|-------|--------|-----------|
| 1 | Intelligence Engine | await |
| 2 | Friday Profile (disk write) | fire-and-forget |
| 3 | Task Scheduler | fire-and-forget |
| 4 | Predictor | sync |
| 5 | Ambient Engine | sync |
| 6 | Sentiment Engine | fire-and-forget |
| 7 | Telemetry Engine | fire-and-forget |
| 8 | Episodic Memory | fire-and-forget |
| 9 | Chat History Store | fire-and-forget |
| 10 | Relationship Memory | fire-and-forget |
| 11 | Trust Graph | fire-and-forget |
| 12 | Container Engine | fire-and-forget |
| 13 | Art Evolution | fire-and-forget (chain: init -> check) |
| 14 | Semantic Search + Bulk Index | fire-and-forget |
| 15 | Memory Consolidation | sync |
| 16 | Notification Engine | sync |
| 17 | Clipboard Intelligence | sync |
| 18 | Project Awareness | sync |
| 19 | Document Ingestion | sync |
| 20 | Agent Runner | sync |
| 21 | GitLoader | fire-and-forget |
| 22 | Office Manager | sync |
| 23 | Calendar + Meeting Prep | fire-and-forget (chain) |
| 24 | Meeting Intelligence | fire-and-forget |
| 25 | Communications | fire-and-forget |
| 26 | Commitment Tracker | fire-and-forget |
| 27 | Daily Briefing Engine | fire-and-forget |
| 28 | Workflow Recorder | fire-and-forget |
| 29 | Workflow Executor | fire-and-forget |
| 30 | Unified Inbox | fire-and-forget |
| 31 | Outbound Intelligence | fire-and-forget |
| 32 | Intelligence Router + Model Discovery | fire-and-forget (chain) |
| 33 | File Transfer Engine | fire-and-forget |
| 34 | Superpower Ecosystem | fire-and-forget |
| 35 | State Export | fire-and-forget |
| 36 | Memory Quality | fire-and-forget |
| 37 | Personality Calibration | fire-and-forget |
| 38 | Memory-Personality Bridge | fire-and-forget |
| 39 | Multimedia Engine | fire-and-forget |
| 40 | Hardware Profiler | fire-and-forget |
| 41 | Ollama Lifecycle | fire-and-forget |
| 42 | Context Stream Bridge | sync |
| 43 | Context Graph | sync |
| 44 | Live Context Bridge | sync |
| 45 | Performance Monitor | sync |
| 46 | OS Events | sync |
| 47 | File Watcher | sync |
| 48 | Briefing Pipeline | sync |
| 49 | Briefing Delivery | sync |
| 50 | Connector Registry | fire-and-forget |
| 51 | Gateway Manager (if enabled) | fire-and-forget |

Phase A ends with: `console.log('[Friday] Phase A complete -- waiting for vault passphrase...')`

#### A13. IPC Handler Registration (`index.ts:809-873`)

50+ handler modules are registered synchronously via their `register*Handlers()` functions. This includes all domain handlers (core, memory, tools, agents, onboarding, integrations, integrity, superpowers, trust graph, meeting intelligence, etc.) plus Sprint 3-6 module handlers (hardware, setup, Ollama, voice pipeline, vision pipeline, chat history, local conversation, voice state, fallback, connection stage, PersonaPlex).

#### A14. Vault IPC Handlers (`index.ts:876-929`)

Three vault IPC channels:
- `vault:is-initialized` -- checks if vault files exist
- `vault:initialize-new` -- creates vault with passphrase (first-time user)
- `vault:unlock` -- unlocks existing vault with passphrase

Both `initialize-new` and `unlock` call `completeBootAfterUnlock()` on success.

### Phase B: Secret-Dependent Engines (`index.ts:961-1026`)

`completeBootAfterUnlock()` runs after vault is unlocked:

1. **Re-read settings** (`index.ts:966-971`): `settingsManager.reloadFromVault()` -- encrypted settings files are now decryptable.

2. **Re-read memory** (`index.ts:973-978`): `memoryManager.reloadFromVault()` -- encrypted memory files restored.

3. **Re-read trust graph** (`index.ts:980-985`): `trustGraph.reloadFromVault()` -- encrypted trust data.

4. **Re-read calendar tokens** (`index.ts:988-993`): OAuth tokens may be vault-encrypted.

5. **Inject HMAC key** (`index.ts:996-1002`): `initializeHmac(getHmacKey())` -- derived via `Passphrase -> Argon2id -> masterKey -> crypto_kdf(id=2, ctx="AF_HMAC_") -> hmacKey`. Key stored in a `SecureBuffer` (guard-paged, mlocked, zeroed on destroy).

6. **Initialize integrity** (`index.ts:1006-1011`): `integrityManager.initialize()` with 30s timeout. Verifies core laws HMAC, loads manifest, checks meta-signature.

7. **Initialize agent network** (`index.ts:1015-1020`): `agentNetwork.initialize()` with 30s timeout. Ed25519 keypair generation, peer list loading.

8. **Notify renderer** (`index.ts:1025`): `mainWindow.webContents.send('vault:boot-complete')`.

### Renderer Mount (`main.tsx:1-47`)

1. **Production console gate** (`main.tsx:4-9`): `console.log` and `console.debug` silenced in production.
2. **Global error handlers** (`main.tsx:19-37`): `window.onerror` and `window.onunhandledrejection` catch errors that slip past React, recording them via `window.eve.sessionHealth.recordError()`.
3. **React mount** (`main.tsx:40-47`): `createRoot` renders `<App />` wrapped in `<ErrorBoundary>` and `<React.StrictMode>`.

### App Component Initialization (`App.tsx:49+`)

The root `App` component:
1. Initializes Zustand store slices (messages, status, voiceMode, appPhase, etc.).
2. Sets up hooks: `useAppManager`, `useDesktopEvolution`, `useVoiceState`, `useGeminiLive`, `useWakeWord`, `useIPCListeners`, `useKeyboardShortcuts`, `useAudioLevels`, `useLocalMicCapture`.
3. Routes based on `appPhase`:
   - If `?office=true` query param: renders `<AgentOffice />` (pixel-art visualization)
   - Otherwise: renders the main UI with `PassphraseGate` -> `OnboardingWizard` -> main chat interface

### Shutdown (`index.ts:1107-1161`)

`window-all-closed` event triggers orderly shutdown of 30+ subsystems:
1. Flush text session and chat history
2. Stop all engines (screen capture, scheduler, Python bridge, gateway, predictor, ambient, notifications, etc.)
3. Stop context bridges, communications, file transfer, OS events, file watcher
4. Stop agent network, container engine, Ollama, PersonaPlex
5. Stop API health monitor, telemetry
6. Unregister global shortcuts
7. Disconnect MCP client
8. **Zero all key material**: `destroyHmac()`, `destroyVault()`, `settingsManager.clearApiKeysFromEnv()`

## IPC Channels Used (Boot-Specific)

| Channel | Direction | Purpose |
|---------|-----------|---------|
| `vault:is-initialized` | Renderer -> Main | Check if vault files exist (determines passphrase vs. first-run UI) |
| `vault:is-unlocked` | Renderer -> Main | Check if vault is currently unlocked |
| `vault:initialize-new` | Renderer -> Main | Create new vault with passphrase (first-time setup) |
| `vault:unlock` | Renderer -> Main | Unlock existing vault with passphrase |
| `vault:reset-all` | Renderer -> Main | Nuclear reset: wipe vault files, relaunch app |
| `vault:boot-complete` | Main -> Renderer | Notify renderer that Phase B is complete |
| `settings:get` | Renderer -> Main | Fetch current settings (used to determine onboarding state) |

## State Changes

| State | Trigger | Effect |
|-------|---------|--------|
| Settings loaded | Phase A step 2 | API keys available for provider init, agent config accessible |
| Memory loaded | Phase A step 4 | 3-tier memory available for personality and context |
| Express server running | Phase A step 5 | Renderer can load via `http://localhost:{port}` |
| Window created | Phase A step 9 | Renderer begins loading, preload bridge injected |
| Phase A complete | All non-secret engines initialized | App displays PassphraseGate in renderer |
| Vault unlocked | User enters passphrase | HMAC key derived, encrypted files decryptable |
| Settings/memory reloaded | Phase B step 1-3 | Encrypted personality, memories, trust data restored |
| HMAC initialized | Phase B step 5 | Integrity verification possible |
| Integrity verified | Phase B step 6 | Safe mode entered if laws tampered; otherwise normal operation |
| Agent network started | Phase B step 7 | P2P peer discovery, Ed25519 attestation available |
| Boot complete | Phase B step 8 | Renderer notified, transitions from PassphraseGate to main UI |
| App quitting | User clicks Quit or closes all windows | All engines stopped, key material zeroed |

## Error Scenarios

| Scenario | Behavior |
|----------|----------|
| Settings init fails | Warning logged, app continues with defaults |
| Memory init fails | Warning logged, app starts with empty memory |
| Express server fails to start | Fatal: renderer cannot load |
| MCP connection fails | Warning logged, MCP tools unavailable |
| Any engine init fails | Warning logged via `.catch()`, other engines unaffected |
| Vault passphrase incorrect | `{ ok: false, error: 'Incorrect passphrase' }` returned to renderer |
| Vault init fails | Error returned to renderer, user can retry |
| HMAC key unavailable after unlock | Warning logged, integrity checks limited |
| Integrity init times out (30s) | Skipped with warning, app continues without integrity verification |
| Agent network init times out (30s) | Skipped with warning, P2P features unavailable |
| Uncaught exception at any point | Crash logged (sanitized), vault/HMAC zeroed, error dialog shown |
| `NODE_TLS_REJECT_UNAUTHORIZED=0` detected | Overridden to enforce TLS verification |
| Second instance launched | Silently quits; first instance's window is focused |
| Navigation to external URL | Blocked by `will-navigate` handler |
| `window.open()` call | Denied; HTTPS URLs opened in system browser |
