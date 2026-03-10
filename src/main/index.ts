import { app, BrowserWindow, session, Tray, Menu, globalShortcut, nativeImage, dialog, ipcMain, shell, crashReporter } from 'electron';
import path from 'path';
import fs from 'fs';
import { startServer, flushTextSession } from './server';
import { chatHistoryStore } from './chat-history';
import { mcpClient } from './mcp-client';
import { initializeProviders } from './providers';

// ── Global error handlers (must be first) ───────────────────────────
const logPath = path.join(app.getPath('userData'), 'crash.log');

function writeCrashLog(label: string, err: unknown): void {
  const ts = new Date().toISOString();
  let msg = err instanceof Error ? `${err.message}\n${err.stack}` : String(err);

  // Crypto Sprint 6 (HIGH): Sanitize crash log entries to prevent API key leakage.
  // Many HTTP libraries include request URLs (containing API keys) in error messages.
  // Redact anything that looks like an API key (20+ alphanumeric chars) or known prefixes.
  // Crypto Sprint 14: Extended — added sk_ (ElevenLabs), fc- (Firecrawl), pplx- (Perplexity),
  // ya29. (Google OAuth), xox[bpsa]- (Slack), bot token patterns (Telegram/Discord).
  msg = msg.replace(/(?:AIza|sk-|sk_|ant-|fc-|pplx-|ya29\.|xox[bpsa]-|key=)[A-Za-z0-9_.-]{15,}/g, '[REDACTED]');
  msg = msg.replace(/(?:Bearer\s+)[A-Za-z0-9_.-]{20,}/g, 'Bearer [REDACTED]');
  msg = msg.replace(/bot[0-9]{8,}:[A-Za-z0-9_-]{30,}/gi, '[REDACTED-BOT-TOKEN]');

  // Cap crash.log file size to 5MB to prevent disk exhaustion from crash loops
  try {
    const stats = fs.statSync(logPath);
    if (stats.size > 5 * 1024 * 1024) {
      fs.writeFileSync(logPath, `[${ts}] Log rotated (exceeded 5MB)\n\n`);
    }
  } catch {
    // File doesn't exist yet — ok
  }

  const entry = `[${ts}] ${label}: ${msg}\n\n`;
  try {
    fs.appendFileSync(logPath, entry);
  } catch {
    // If we can't even write logs, there's nothing else to do
  }
}

process.on('uncaughtException', (err) => {
  // Crypto Sprint 13: Sanitize — raw err objects may contain API keys in closured scope.
  console.error('[FATAL] Uncaught exception:', err.message);
  writeCrashLog('uncaughtException', err);
  dialog.showErrorBox(
    'Agent Friday — Unexpected Error',
    `An unexpected error occurred:\n\n${err.message}\n\nThe app will try to continue, but you may want to restart.\nFull details saved to: ${logPath}`
  );
});

process.on('unhandledRejection', (reason) => {
  // Crypto Sprint 13: Sanitize — raw reason may contain secrets.
  console.error('[ERROR] Unhandled promise rejection:', reason instanceof Error ? reason.message : 'Unknown error');
  writeCrashLog('unhandledRejection', reason);
});

// ── Native Crash Reporter ────────────────────────────────────────────
// Captures V8 segfaults and native crashes that the JS error handlers above
// cannot catch. Dumps are stored locally — nothing is sent to a remote server.
crashReporter.start({
  productName: 'Agent Friday',
  submitURL: '',       // empty = local-only crash dumps, no remote server
  uploadToServer: false,
  compress: true,
});
console.log('[CrashReporter] Dumps directory:', app.getPath('crashDumps'));

// ── TLS Hardening (Crypto Sprint 2) ─────────────────────────────────
// Ensure TLS certificate verification is NEVER disabled, even if a dependency
// or environment variable tries to set NODE_TLS_REJECT_UNAUTHORIZED=0.
// This prevents MITM attacks on API connections (Anthropic, Gemini, etc.)
// in corporate proxy environments or compromised networks.
if (process.env.NODE_TLS_REJECT_UNAUTHORIZED === '0') {
  console.error('[Security] ⚠ NODE_TLS_REJECT_UNAUTHORIZED=0 detected — overriding to enforce TLS verification');
  delete process.env.NODE_TLS_REJECT_UNAUTHORIZED;
}
// Guard against runtime re-disabling of TLS verification.
// NOTE: Electron's process.env is a native bridge and does NOT support
// accessor descriptors (getter/setter), so we use a periodic check instead.
const _tlsGuardInterval = setInterval(() => {
  if (process.env.NODE_TLS_REJECT_UNAUTHORIZED === '0') {
    console.error('[Security] ⚠ Blocked attempt to disable TLS verification');
    delete process.env.NODE_TLS_REJECT_UNAUTHORIZED;
  }
}, 5_000);
// Clean up on exit so the timer doesn't hold the process open
process.once('exit', () => clearInterval(_tlsGuardInterval));

// ── Domain module imports (initialization + lifecycle) ───────────────
import { memoryManager } from './memory';
import { screenCapture } from './screen-capture';
import { setMainWindow } from './desktop-tools';
import { taskScheduler } from './scheduler';
import { predictor } from './predictor';
import { ambientEngine } from './ambient';
import { sentimentEngine } from './sentiment';
import { notificationEngine } from './notifications';
import { intelligenceEngine } from './intelligence';
import { ensureProfileOnDisk } from './friday-profile';
import { settingsManager } from './settings';
import { episodicMemory } from './episodic-memory';
import { setMainWindowForSelfImprove, registerHotReload, invalidateModuleCache } from './self-improve';
import { sessionHealth } from './session-health';
import { semanticSearch } from './semantic-search';
import { agentRunner } from './agents/agent-runner';
import { relationshipMemory } from './relationship-memory';
import { memoryConsolidation } from './memory-consolidation';
import { clipboardIntelligence } from './clipboard-intelligence';
import { projectAwareness } from './project-awareness';
import { documentIngestion } from './document-ingestion';
import { calendarIntegration, registerCalendarHandlers } from './calendar';
import { meetingPrep } from './meeting-prep';
import { communications, registerCommunicationsHandlers } from './communications';
import { connectorRegistry } from './connectors/registry';
import { gatewayManager } from './gateway/gateway-manager';
import { createTelegramAdapter } from './gateway/adapters/telegram';
import { integrityManager, getCanonicalLaws } from './integrity';
import { setConsentWindow } from './consent-gate';
import { officeManager } from './agent-office/office-manager';
import { pythonBridge } from './soc-bridge';
import { containerEngine } from './container-engine';
import { gitLoader } from './git-loader';
import { trustGraph } from './trust-graph';
import { meetingIntelligence } from './meeting-intelligence';
import { startContextStreamBridge, stopContextStreamBridge } from './context-stream-bridge';
import { contextGraph } from './context-graph';
import { liveContextBridge } from './live-context-bridge';
import { commitmentTracker } from './commitment-tracker';
import { dailyBriefingEngine } from './daily-briefing';
import { workflowRecorder } from './workflow-recorder';
import { workflowExecutor } from './workflow-executor';
import { unifiedInbox } from './unified-inbox';
import { outboundIntelligence } from './outbound-intelligence';
import { intelligenceRouter } from './intelligence-router';
import { agentNetwork } from './agent-network';
import { fileTransferEngine } from './network/file-transfer';
import { initializeArtEvolution, checkAndEvolve, forceEvolve, getArtEvolutionState, getLatestEvolution } from './art-evolution';
import { superpowerEcosystem } from './superpower-ecosystem';
import { stateExport } from './state-export';
import { memoryQuality } from './memory-quality';
import { personalityCalibration } from './personality-calibration';
import { memoryPersonalityBridge } from './memory-personality-bridge';
import { multimediaEngine } from './multimedia-engine';
import { perfMonitor } from './perf-monitor';
import { osEvents } from './os-events';
import { fileWatcher } from './file-watcher';
import { briefingPipeline } from './briefing-pipeline';
import { briefingDelivery } from './briefing-delivery';
import {
  initializeNewVault,
  unlockVault,
  isVaultUnlocked,
  isVaultInitialized,
  getHmacKey,
  destroyVault,
  resetVaultFiles,
} from './vault';
import { initializeHmac, destroyHmac } from './integrity';

// ── Sprint 3-6 domain modules (wired in Sprint 7) ──────────────────
import { HardwareProfiler } from './hardware/hardware-profiler';
import { OllamaLifecycle } from './ollama-lifecycle';
import { initAutoUpdater } from './updater';

// ── Extracted IPC handler modules ───────────────────────────────────
import {
  registerCoreHandlers,
  registerMemoryHandlers,
  registerToolHandlers,
  registerAgentHandlers,
  registerOnboardingHandlers,
  registerIntegrationHandlers,
  registerIntegrityHandlers,
  registerSuperpowersHandlers,
  registerTrustGraphHandlers,
  registerMeetingIntelligenceHandlers,
  registerCapabilityGapHandlers,
  registerContextStreamHandlers,
  registerContextGraphHandlers,
  registerContextToolRouterHandlers,
  registerCommitmentTrackerHandlers,
  registerDailyBriefingHandlers,
  registerWorkflowRecorderHandlers,
  registerWorkflowExecutorHandlers,
  registerUnifiedInboxHandlers,
  registerOutboundIntelligenceHandlers,
  registerIntelligenceRouterHandlers,
  registerAgentNetworkHandlers,
  registerSuperpowerEcosystemHandlers,
  registerStateExportHandlers,
  registerMemoryQualityHandlers,
  registerPersonalityCalibrationHandlers,
  registerMemoryPersonalityBridgeHandlers,
  registerAgentTrustHandlers,
  registerMultimediaHandlers,
  registerContainerEngineHandlers,
  registerDelegationEngineHandlers,
  registerOsPrimitivesHandlers,
  registerNotesHandlers,
  registerFilesHandlers,
  registerWeatherHandlers,
  registerSystemMonitorHandlers,
  registerExecutionDelegateHandlers,
  registerAppContextHandlers,
  registerContextPushHandlers,
  registerBriefingDeliveryHandlers,
  registerHardwareHandlers,
  registerSetupHandlers,
  registerOllamaHandlers,
  registerVoicePipelineHandlers,
  registerVisionPipelineHandlers,
  registerChatHistoryHandlers,
  registerLocalConversationHandlers,
  type ContextPushCleanup,
} from './ipc';

// ── Single Instance Lock ─────────────────────────────────────────────
// Prevent multiple instances from racing on the vault or corrupting
// encrypted stores. Must run before app.whenReady().
const gotSingleInstanceLock = app.requestSingleInstanceLock();
if (!gotSingleInstanceLock) {
  // Another instance already holds the lock — exit immediately
  app.quit();
}

// ── Application state ───────────────────────────────────────────────
let mainWindow: BrowserWindow | null = null;
let tray: Tray | null = null;
let isQuitting = false;
let serverPort = 3333;
let contextPushCleanup: ContextPushCleanup | null = null;

const isDev = !app.isPackaged;

// When a second instance is launched, focus the existing window instead
app.on('second-instance', () => {
  if (mainWindow) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
  }
});

// ── Window creation ─────────────────────────────────────────────────
/** Resolve the app icon path for both dev and packaged modes */
function getIconPath(): string {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'icon.png');
  }
  // Dev mode: icon is in build/ relative to project root
  return path.join(__dirname, '..', '..', 'build', 'icon.png');
}

function createWindow() {
  const iconPath = getIconPath();
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 900,
    minHeight: 600,
    frame: false,
    transparent: false,
    backgroundColor: '#060B19',
    show: false,
    icon: iconPath,
    titleBarStyle: 'hidden',
    titleBarOverlay: {
      color: '#0a0a0f',
      symbolColor: '#00f0ff',
      height: 32,
    },
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true, // Crypto Sprint 5: Sandbox renderer to limit OS-level access on compromise
    },
  });

  setMainWindow(mainWindow);
  setMainWindowForSelfImprove(mainWindow);
  setConsentWindow(mainWindow);

  mainWindow.maximize();
  mainWindow.once('ready-to-show', () => {
    mainWindow?.show();
  });

  // Crypto Sprint 5 (HIGH — Navigation Safety): Block navigation to external URLs.
  // Without this, an attacker with HTML injection could redirect the main window
  // to a malicious page that still has access to the preload bridge (200+ IPC handlers).
  mainWindow.webContents.on('will-navigate', (event, url) => {
    const allowed = ['http://localhost:', 'file://'];
    if (!allowed.some(prefix => url.startsWith(prefix))) {
      event.preventDefault();
      console.warn('[Security] Blocked navigation to:', url);
    }
  });

  // Crypto Sprint 5 (HIGH — Popup Safety): Prevent window.open() / target="_blank"
  // from spawning new Electron windows that would inherit the preload bridge.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('https://') || url.startsWith('http://')) {
      // Validate URL is well-formed before opening (defense in depth)
      try {
        new URL(url);
        shell.openExternal(url);
      } catch {
        console.warn('[Security] Blocked malformed external URL:', url);
      }
    }
    return { action: 'deny' };
  });

  mainWindow.webContents.on('did-fail-load', (_e, code, desc, url) => {
    console.error(`[Renderer] Failed to load: ${url} (${code}: ${desc})`);
  });
  mainWindow.webContents.on('render-process-gone', (_e, details) => {
    console.error('[Renderer] Process gone:', details);
  });
  mainWindow.webContents.on('console-message', (_e, level, message, line, sourceId) => {
    if (level >= 2) {
      console.error(`[Renderer:${level}] ${message} (${sourceId}:${line})`);
    }
  });

  if (isDev && process.env.VITE_DEV_SERVER === '1') {
    mainWindow.loadURL('http://localhost:5199').catch(() => {
      mainWindow!.loadURL(`http://localhost:${serverPort}`);
    });
  } else {
    mainWindow.loadURL(`http://localhost:${serverPort}`);
  }

  mainWindow.on('close', (e) => {
    if (!isQuitting) {
      e.preventDefault();
      mainWindow?.hide();
    }
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  mainWindow.webContents.on('before-input-event', (_event, input) => {
    if (input.key === 'F12' && input.type === 'keyDown') {
      mainWindow?.webContents.toggleDevTools();
    }
  });
}

// ── Application ready ───────────────────────────────────────────────
app.whenReady().then(async () => {
  // ── IPC Error Boundary (Crypto Sprint — Path Leakage Prevention) ──
  // Wrap ipcMain.handle so that every handler's errors are sanitized
  // before reaching the renderer. Internal details (file paths, stack
  // traces, dependency versions) are logged server-side but never sent
  // to the renderer, preventing information disclosure to attackers.
  const originalHandle = ipcMain.handle.bind(ipcMain);
  (ipcMain as typeof ipcMain).handle = (channel: string, listener: (event: Electron.IpcMainInvokeEvent, ...args: unknown[]) => unknown) => {
    return originalHandle(channel, async (event: Electron.IpcMainInvokeEvent, ...args: unknown[]) => {
      try {
        return await listener(event, ...args);
      } catch (err: unknown) {
        console.error(`[IPC] ${channel} error:`, err);
        throw new Error('Internal error');
      }
    });
  };

  // Initialize settings first (API keys depend on it)
  try {
    await settingsManager.initialize();
    console.log('[Friday] Settings loaded');

    if (!isDev && settingsManager.get().autoLaunch) {
      app.setLoginItemSettings({ openAtLogin: true, path: app.getPath('exe') });
    }
  } catch (err) {
    // Crypto Sprint 17: Sanitize error output.
    console.warn('[Friday] Settings init failed:', err instanceof Error ? err.message : 'Unknown error');
  }

  // Initialize LLM providers (must come after settings — API keys depend on it)
  try {
    initializeProviders();
    console.log('[Friday] LLM providers initialized');
  } catch (err) {
    console.warn('[Friday] Provider init failed:', err instanceof Error ? err.message : 'Unknown error');
  }

  // Initialize memory system (personality depends on it)
  try {
    await memoryManager.initialize();
    console.log('[Friday] Memory system initialized');
  } catch (err) {
    console.warn('[Friday] Memory init failed:', err instanceof Error ? err.message : 'Unknown error');
  }

  // NOTE: Integrity system initialization is deferred to Phase B (after vault
  // unlock) because it requires the HMAC signing key derived from the passphrase.
  // See completeBootAfterUnlock() for the full integrity init sequence.

  // Start the Express API server
  serverPort = await startServer();
  console.log(`[Friday] API server running on port ${serverPort}`);

  // Initialize MCP client
  try {
    await mcpClient.connect();
    console.log('[Friday] MCP client connected');
  } catch (err) {
    console.warn('[Friday] MCP client failed to connect:', err instanceof Error ? err.message : 'Unknown error');
  }

  // ── Content Security Policy (CSP) ──────────────────────────────────
  // Prevents XSS from reaching the 40+ IPC namespaces exposed via preload.
  // The renderer loads from http://localhost and connects to Gemini WebSocket.
  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    callback({
      responseHeaders: {
        ...details.responseHeaders,
        'Content-Security-Policy': [
          [
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
            "font-src 'self' https://fonts.gstatic.com",
            "img-src 'self' data: blob: https:",
            "media-src 'self' blob: data: mediastream:",
            "connect-src 'self' wss://generativelanguage.googleapis.com https://generativelanguage.googleapis.com blob:",
            "worker-src 'self' blob:",
            "child-src 'self' blob:",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
          ].join('; '),
        ],
      },
    });
  });

  // Auto-grant microphone and camera permissions
  session.defaultSession.setPermissionRequestHandler((_webContents, permission, callback) => {
    const allowed = ['media', 'mediaKeySystem', 'display-capture', 'audioCapture'];
    callback(allowed.includes(permission));
  });

  createWindow();

  if (mainWindow) {
    sessionHealth.setMainWindow(mainWindow);
  }

  // ── Auto-Updater (GitHub Releases) ────────────────────────────────
  // Only active in packaged builds — skipped in dev mode.
  initAutoUpdater();

  // ── System Tray ─────────────────────────────────────────────────
  const iconPath = getIconPath();
  let trayImage: Electron.NativeImage;
  try {
    trayImage = nativeImage.createFromPath(iconPath);
    // Resize for tray — 16x16 on Windows standard DPI, 32x32 for HiDPI
    if (!trayImage.isEmpty()) {
      trayImage = trayImage.resize({ width: 16, height: 16 });
    }
  } catch (err) {
    console.warn('[Tray] Failed to load icon from', iconPath, err instanceof Error ? err.message : 'Unknown error');
    trayImage = nativeImage.createEmpty();
  }
  tray = new Tray(trayImage);

  const agentConfig = settingsManager.getAgentConfig();
  tray.setToolTip(agentConfig.agentName ? `Agent ${agentConfig.agentName}` : 'Agent Friday');
  const trayMenu = Menu.buildFromTemplate([
    {
      label: agentConfig.agentName ? `Show ${agentConfig.agentName}` : 'Show Agent Friday',
      click: () => {
        mainWindow?.show();
        mainWindow?.focus();
      },
    },
    { type: 'separator' },
    {
      label: 'Quit',
      click: () => {
        isQuitting = true;
        app.quit();
      },
    },
  ]);
  tray.setContextMenu(trayMenu);
  tray.on('double-click', () => {
    mainWindow?.show();
    mainWindow?.focus();
  });

  // ── Global Hotkey: Ctrl+Shift+N to summon/hide ──────────────────
  globalShortcut.register('Ctrl+Shift+N', () => {
    if (mainWindow?.isVisible() && mainWindow.isFocused()) {
      mainWindow.hide();
    } else {
      mainWindow?.show();
      mainWindow?.focus();
    }
  });

  // ── Engine initialization cascade ───────────────────────────────
  try {
    await intelligenceEngine.initialize();
    console.log('[Friday] Intelligence engine initialized');
  } catch (err) {
    console.warn('[Friday] Intelligence engine init failed:', err instanceof Error ? err.message : 'Unknown error');
  }

  ensureProfileOnDisk().catch((err) => {
    console.warn('[Friday] Profile write failed:', err instanceof Error ? err.message : 'Unknown error');
  });

  if (mainWindow) {
    taskScheduler.initialize(mainWindow).catch((err) => {
      console.warn('[Friday] Scheduler init failed:', err instanceof Error ? err.message : 'Unknown error');
    });

    predictor.initialize(mainWindow);
    ambientEngine.initialize();

    sentimentEngine.initialize().catch((err) => {
      console.warn('[Friday] Sentiment init failed:', err instanceof Error ? err.message : 'Unknown error');
    });

    episodicMemory.initialize().catch((err) => {
      console.warn('[Friday] Episodic memory init failed:', err instanceof Error ? err.message : 'Unknown error');
    });

    chatHistoryStore.initialize().catch((err) => {
      console.warn('[Friday] Chat history store init failed:', err instanceof Error ? err.message : 'Unknown error');
    });

    relationshipMemory.initialize().catch((err) => {
      console.warn('[Friday] Relationship memory init failed:', err instanceof Error ? err.message : 'Unknown error');
    });

    trustGraph.initialize().catch((err) => {
      console.warn('[Friday] Trust graph init failed:', err instanceof Error ? err.message : 'Unknown error');
    });

    containerEngine.initialize().catch((err) => {
      console.warn('[Friday] Container engine init failed:', err instanceof Error ? err.message : 'Unknown error');
    });

    initializeArtEvolution().then(() => {
      // Check if weekly evolution is due (non-blocking)
      checkAndEvolve().then((record) => {
        if (record) {
          console.log(`[Friday] Art evolution triggered: → structure ${record.targetIndex}`);
        }
      }).catch((err) => {
        console.warn('[Friday] Art evolution check failed:', err instanceof Error ? err.message : 'Unknown error');
      });
    }).catch((err) => {
      console.warn('[Friday] Art evolution init failed:', err instanceof Error ? err.message : 'Unknown error');
    });

    semanticSearch.initialize().then(async () => {
      console.log('[Friday] Semantic search initialized');
      const longTerm = memoryManager.getLongTerm();
      const mediumTerm = memoryManager.getMediumTerm();
      const episodes = episodicMemory.getAll();

      const items = [
        ...longTerm.map((e) => ({
          id: e.id,
          text: e.fact,
          type: 'long-term' as const,
          meta: { category: e.category, confirmed: e.confirmed },
        })),
        ...mediumTerm.map((e) => ({
          id: e.id,
          text: e.observation,
          type: 'medium-term' as const,
          meta: { category: e.category, confidence: e.confidence, occurrences: e.occurrences },
        })),
        ...episodes.map((e) => ({
          id: e.id,
          text: `${e.summary} ${e.topics.join(' ')} ${e.keyDecisions.join(' ')}`,
          type: 'episode' as const,
          meta: { summary: e.summary, topics: e.topics, emotionalTone: e.emotionalTone, startTime: e.startTime },
        })),
      ];

      if (items.length > 0) {
        await semanticSearch.indexBulk(items);
        console.log(`[Friday] Indexed ${items.length} existing memories for semantic search`);
      }
    }).catch((err) => {
      console.warn('[Friday] Semantic search init failed:', err instanceof Error ? err.message : 'Unknown error');
    });

    memoryConsolidation.initialize();
    notificationEngine.initialize(mainWindow);
    clipboardIntelligence.initialize(mainWindow);
    projectAwareness.initialize(mainWindow);
    documentIngestion.initialize(mainWindow);

    agentRunner.initialize(mainWindow);
    console.log('[Friday] Agent runner initialized');

    // Initialize GitLoader (GitHub repo loading + code intelligence)
    gitLoader.initialize().then(() => {
      console.log('[Friday] GitLoader initialized');
    }).catch((err) => {
      console.warn('[Friday] GitLoader init failed:', err instanceof Error ? err.message : 'Unknown error');
    });

    // Initialize office manager (pixel-art agent visualization)
    officeManager.setMainWindow(mainWindow);
    officeManager.setServerPort(serverPort);
    console.log('[Friday] Office manager initialized');

    calendarIntegration.init().then(() => {
      meetingPrep.init(mainWindow!);
      console.log('[Friday] Calendar + meeting prep initialized');
    }).catch((err) => {
      console.warn('[Friday] Calendar init failed:', err instanceof Error ? err.message : 'Unknown error');
    });

    // Initialize Meeting Intelligence engine
    meetingIntelligence.initialize().then(() => {
      console.log('[Friday] Meeting Intelligence initialized');
    }).catch((err) => {
      console.warn('[Friday] Meeting Intelligence init failed:', err instanceof Error ? err.message : 'Unknown error');
    });

    communications.init().catch((err) => {
      console.warn('[Friday] Communications init failed:', err instanceof Error ? err.message : 'Unknown error');
    });

    commitmentTracker.initialize().catch((err) => {
      console.warn('[Friday] Commitment tracker init failed:', err instanceof Error ? err.message : 'Unknown error');
    });

    dailyBriefingEngine.initialize().catch((err) => {
      console.warn('[Friday] Daily briefing init failed:', err instanceof Error ? err.message : 'Unknown error');
    });

    workflowRecorder.initialize().catch((err) => {
      console.warn('[Friday] Workflow recorder init failed:', err instanceof Error ? err.message : 'Unknown error');
    });

    workflowExecutor.initialize().catch((err) => {
      console.warn('[Friday] Workflow executor init failed:', err instanceof Error ? err.message : 'Unknown error');
    });

    unifiedInbox.initialize().catch((err) => {
      console.warn('[Friday] Unified inbox init failed:', err instanceof Error ? err.message : 'Unknown error');
    });

    outboundIntelligence.initialize().catch((err) => {
      console.warn('[Friday] Outbound intelligence init failed:', err instanceof Error ? err.message : 'Unknown error');
    });

    intelligenceRouter.initialize().then(() => {
      // Auto-discover local models (non-blocking — probes local inference endpoint)
      intelligenceRouter.discoverLocalModels().catch((err) => {
        console.warn('[Friday] Local model discovery failed:', err instanceof Error ? err.message : 'Unknown error');
      });
    }).catch((err) => {
      console.warn('[Friday] Intelligence router init failed:', err instanceof Error ? err.message : 'Unknown error');
    });

    // ── Sovereign Vault v2: Two-Phase Boot ─────────────────────────
    // Phase A (here): UI shell, settings, memory, Express, MCP, window.
    //   The vault is NOT unlocked yet — passphrase entry happens in the
    //   renderer via PassphraseGate. Agent network and engines that need
    //   secrets are deferred to Phase B (completeBootAfterUnlock).
    //
    // Phase B (triggered by vault:initialize-new or vault:unlock IPC):
    //   Vault unlocked → inject hmacKey → integrity system → agent network
    //   → file transfer → notify renderer via vault:boot-complete.
    console.log('[Friday] Phase A complete — waiting for vault passphrase...');

    // File transfer can initialize without vault (it reads files, not secrets)
    fileTransferEngine.initialize().catch((err) => {
      console.warn('[Friday] File transfer engine init failed:', err instanceof Error ? err.message : 'Unknown error');
    });

    superpowerEcosystem.initialize().catch((err) => {
      console.warn('[Friday] Superpower ecosystem init failed:', err instanceof Error ? err.message : 'Unknown error');
    });

    stateExport.initialize().catch((err) => {
      console.warn('[Friday] State export engine init failed:', err instanceof Error ? err.message : 'Unknown error');
    });

    memoryQuality.initialize().catch((err) => {
      console.warn('[Friday] Memory quality engine init failed:', err instanceof Error ? err.message : 'Unknown error');
    });

    personalityCalibration.initialize().catch((err) => {
      console.warn('[Friday] Personality calibration init failed:', err instanceof Error ? err.message : 'Unknown error');
    });

    memoryPersonalityBridge.initialize().catch((err) => {
      console.warn('[Friday] Memory-personality bridge init failed:', err instanceof Error ? err.message : 'Unknown error');
    });

    multimediaEngine.initialize().catch((err) => {
      console.warn('[Friday] Multimedia engine init failed:', err instanceof Error ? err.message : 'Unknown error');
    });

    // ── Sprint 7: Hardware detection + Ollama lifecycle ─────────────
    HardwareProfiler.getInstance().detect().then((profile) => {
      console.log(`[Friday] Hardware detected: ${profile.gpu?.name ?? 'no GPU'}, ${Math.round((profile.vram?.total ?? 0) / (1024 * 1024 * 1024))}GB VRAM`);
    }).catch((err) => {
      console.warn('[Friday] Hardware detection failed:', err instanceof Error ? err.message : 'Unknown error');
    });

    OllamaLifecycle.getInstance().start().then(() => {
      console.log('[Friday] Ollama lifecycle started');
    }).catch((err) => {
      console.warn('[Friday] Ollama start failed:', err instanceof Error ? err.message : 'Unknown error');
    });

    // Start the context stream bridge (after all engines are initialized)
    startContextStreamBridge();
    contextGraph.start();
    liveContextBridge.start(mainWindow!);
    console.log('[Friday] Context stream bridge + graph + live context bridge started');

    // ── Phase 0.1: Previously orphaned singletons ─────────────────
    perfMonitor.initialize();
    console.log('[Friday] Performance monitor initialized');

    osEvents.initialize(mainWindow!);
    console.log('[Friday] OS events engine initialized');

    fileWatcher.initialize(mainWindow!);
    console.log('[Friday] File watcher initialized');

    briefingPipeline.start();
    console.log('[Friday] Briefing pipeline started');

    briefingDelivery.start(mainWindow!);
    console.log('[Friday] Briefing delivery started');

    connectorRegistry.initialize().catch((err) => {
      console.warn('[Friday] Connector registry init failed:', err instanceof Error ? err.message : 'Unknown error');
    });

    const gatewaySettings = settingsManager.get();
    if (gatewaySettings.gatewayEnabled) {
      gatewayManager.initialize().then(async () => {
        if (gatewaySettings.telegramBotToken) {
          const telegramAdapter = createTelegramAdapter(gatewaySettings.telegramBotToken);
          await gatewayManager.registerAdapter(telegramAdapter);
        }
        console.log('[Friday] Messaging gateway initialized');
      }).catch((err) => {
        console.warn('[Friday] Gateway init failed:', err instanceof Error ? err.message : 'Unknown error');
      });
    }
  }

  // ── Register all IPC handlers ───────────────────────────────────
  const getMainWindow = () => mainWindow;

  registerCoreHandlers({ getMainWindow, serverPort });
  registerMemoryHandlers();
  registerToolHandlers({ getMainWindow });
  registerAgentHandlers();
  registerOnboardingHandlers();
  registerIntegrationHandlers();
  registerCalendarHandlers();
  registerCommunicationsHandlers();
  registerIntegrityHandlers();
  registerSuperpowersHandlers();
  registerTrustGraphHandlers();
  registerMeetingIntelligenceHandlers();
  registerCapabilityGapHandlers();
  registerContextStreamHandlers();
  registerContextGraphHandlers();
  registerContextToolRouterHandlers();
  registerCommitmentTrackerHandlers();
  registerDailyBriefingHandlers();
  registerWorkflowRecorderHandlers();
  registerWorkflowExecutorHandlers();
  registerUnifiedInboxHandlers();
  registerOutboundIntelligenceHandlers();
  registerIntelligenceRouterHandlers();
  registerAgentNetworkHandlers();
  registerSuperpowerEcosystemHandlers();
  registerStateExportHandlers();
  registerMemoryQualityHandlers();
  registerPersonalityCalibrationHandlers();
  registerMemoryPersonalityBridgeHandlers();
  registerAgentTrustHandlers();
  registerMultimediaHandlers();
  registerContainerEngineHandlers();
  registerDelegationEngineHandlers(mainWindow ?? undefined);
  registerOsPrimitivesHandlers();
  registerNotesHandlers();
  registerFilesHandlers();
  registerWeatherHandlers();
  registerSystemMonitorHandlers();
  registerExecutionDelegateHandlers();
  registerAppContextHandlers();
  contextPushCleanup = registerContextPushHandlers(mainWindow!);
  registerBriefingDeliveryHandlers();

  // ── Sprint 7: Sprint 3-6 module IPC handlers ────────────────────
  registerHardwareHandlers({ getMainWindow });
  registerSetupHandlers({ getMainWindow });
  registerOllamaHandlers({ getMainWindow });
  registerVoicePipelineHandlers({ getMainWindow });
  registerVisionPipelineHandlers({ getMainWindow });
  registerChatHistoryHandlers();
  registerLocalConversationHandlers({ getMainWindow });
  console.log('[IPC] Sprint 3-6 module handlers registered');

  // ── Vault v2 IPC handlers ────────────────────────────────────────
  //
  // Two-phase boot: The renderer sends vault:initialize-new (first time)
  // or vault:unlock (returning user). On success, completeBootAfterUnlock()
  // starts all engines that depend on vault secrets.
  {
    ipcMain.handle('vault:is-initialized', () => isVaultInitialized());
    ipcMain.handle('vault:is-unlocked', () => isVaultUnlocked());

    // Crypto Sprint 8 (HIGH): Cap passphrase length to prevent Argon2id DoS.
    // A 1GB string would cause Argon2id to allocate 256MB+ and hash a huge input,
    // potentially crashing the process. 1KB is more than enough for any passphrase.
    const MAX_PASSPHRASE = 1024;

    ipcMain.handle('vault:initialize-new', async (_event: any, passphrase: string) => {
      try {
        if (typeof passphrase !== 'string' || passphrase.length > MAX_PASSPHRASE) {
          return { ok: false, error: `Passphrase must be a string of max ${MAX_PASSPHRASE} characters` };
        }
        await initializeNewVault(passphrase);
        await completeBootAfterUnlock();
        return { ok: true };
      } catch (err: any) {
        return { ok: false, error: err?.message || 'Vault initialization failed' };
      }
    });

    ipcMain.handle('vault:unlock', async (_event: any, passphrase: string) => {
      try {
        if (typeof passphrase !== 'string' || passphrase.length > MAX_PASSPHRASE) {
          return { ok: false, error: `Passphrase must be a string of max ${MAX_PASSPHRASE} characters` };
        }
        const success = await unlockVault(passphrase);
        if (!success) {
          return { ok: false, error: 'Incorrect passphrase' };
        }
        await completeBootAfterUnlock();
        return { ok: true };
      } catch (err: any) {
        return { ok: false, error: err?.message || 'Vault unlock failed' };
      }
    });

    // Nuclear "Start Fresh" — wipes all vault files so the next launch is a clean slate.
    // Used when a user reinstalls or forgets their passphrase and wants to start over.
    ipcMain.handle('vault:reset-all', async () => {
      console.log('[Vault] Reset-all requested — wiping vault and relaunching');
      await resetVaultFiles();
      app.relaunch();
      app.exit(0);
    });

    console.log('[IPC] Vault v2 handlers registered');
  }

  /**
   * Phase B of two-phase boot: Run after vault is unlocked.
   *
   * 1. Re-load settings and memory (encrypted files now decryptable)
   * 2. Inject hmacKey into HMAC engine
   * 3. Initialize integrity system
   * 4. Initialize agent network (private keys now decryptable)
   * 5. Notify renderer that boot is complete
   *
   * CRITICAL: Settings and memory are first loaded in Phase A (vault locked),
   * which means encrypted files fall back to defaults. After vault unlock,
   * we MUST re-read them so API keys, personality, and memories are restored.
   */
  /** Run an async task with a timeout. Returns undefined (and logs) on timeout. */
  async function withTimeout<T>(label: string, fn: () => Promise<T>, ms = 30_000): Promise<T | undefined> {
    let timer: ReturnType<typeof setTimeout>;
    const timeout = new Promise<undefined>((resolve) => {
      timer = setTimeout(() => {
        console.warn(`[Friday] ${label} timed out after ${ms}ms — skipping`);
        resolve(undefined);
      }, ms);
    });
    try {
      const result = await Promise.race([fn(), timeout]);
      return result;
    } finally {
      clearTimeout(timer!);
    }
  }

  async function completeBootAfterUnlock(): Promise<void> {
    const t0 = Date.now();
    console.log('[Friday] Phase B: Vault unlocked — starting secret-dependent engines...');

    // 1. Re-load settings and memory now that vault can decrypt
    try {
      await settingsManager.reloadFromVault();
      console.log(`[Friday] Settings reloaded from vault (${Date.now() - t0}ms)`);
    } catch (err) {
      console.warn('[Friday] Settings reload failed:', err instanceof Error ? err.message : 'Unknown error');
    }

    try {
      await memoryManager.reloadFromVault();
      console.log(`[Friday] Memory reloaded from vault (${Date.now() - t0}ms)`);
    } catch (err) {
      console.warn('[Friday] Memory reload failed:', err instanceof Error ? err.message : 'Unknown error');
    }

    try {
      await trustGraph.reloadFromVault();
      console.log(`[Friday] Trust graph reloaded from vault (${Date.now() - t0}ms)`);
    } catch (err) {
      console.warn('[Friday] Trust graph reload failed:', err instanceof Error ? err.message : 'Unknown error');
    }

    // Calendar tokens may also be encrypted — reinitialize to pick them up
    try {
      await calendarIntegration.init();
      console.log(`[Friday] Calendar reloaded from vault (${Date.now() - t0}ms)`);
    } catch (err) {
      console.warn('[Friday] Calendar reload failed:', err instanceof Error ? err.message : 'Unknown error');
    }

    // 2. Inject HMAC signing key
    const hmacKey = getHmacKey();
    if (hmacKey) {
      initializeHmac(hmacKey);
      console.log(`[Friday] HMAC engine initialized (${Date.now() - t0}ms)`);
    } else {
      console.warn('[Friday] No HMAC key available from vault');
    }

    // 3. Re-initialize integrity system (now that HMAC is ready)
    // Timeout: integrity reads + hashes all protected files; 30s should be ample.
    try {
      await withTimeout('Integrity init', () => integrityManager.initialize(), 30_000);
      console.log(`[Friday] Integrity system initialized (${Date.now() - t0}ms)`);
    } catch (err) {
      console.warn('[Friday] Integrity init failed:', err instanceof Error ? err.message : 'Unknown error');
    }

    // 4. Initialize agent network (private keys now decryptable via vault)
    // Timeout: generates Ed25519 keypair + reads peer list; 30s should be ample.
    try {
      await withTimeout('Agent network init', () => agentNetwork.initialize(), 30_000);
      console.log(`[Friday] Agent network initialized (${Date.now() - t0}ms)`);
    } catch (err) {
      console.warn('[Friday] Agent network init failed:', err instanceof Error ? err.message : 'Unknown error');
    }

    console.log(`[Friday] Phase B complete — all engines started (${Date.now() - t0}ms total)`);

    // 5. Notify renderer
    mainWindow?.webContents.send('vault:boot-complete');
  }

  // ── File Transfer IPC handlers ─────────────────────────────────
  {
    // Crypto Sprint 8 (CRITICAL): Validate file path to prevent arbitrary file exfiltration.
    // filePath must not contain traversal, UNC, or shell metacharacters.
    ipcMain.handle('file-transfer:prepare', async (_event: any, filePath: string, remoteAgentId: string, remoteAgentName: string, description?: string) => {
      if (!filePath || typeof filePath !== 'string' || filePath.length > 1000) {
        throw new Error('file-transfer:prepare requires a valid filePath string');
      }
      if (/\.\.[/\\]/.test(filePath) || /^\\\\/.test(filePath) || /[\r\n\0;&|`$]/.test(filePath)) {
        throw new Error('file-transfer:prepare rejected: filePath contains dangerous patterns');
      }
      if (!remoteAgentId || typeof remoteAgentId !== 'string') {
        throw new Error('file-transfer:prepare requires a string remoteAgentId');
      }
      if (!remoteAgentName || typeof remoteAgentName !== 'string') {
        throw new Error('file-transfer:prepare requires a string remoteAgentName');
      }
      return fileTransferEngine.prepareOutboundTransfer(filePath, remoteAgentId, remoteAgentName, description);
    });
    ipcMain.handle('file-transfer:get-chunk', (_event: any, transferId: string, chunkIndex: number) => {
      return fileTransferEngine.getOutboundChunk(transferId, chunkIndex);
    });
    // Crypto Sprint 8 (HIGH): Validate request is an object and trust level is a finite number.
    ipcMain.handle('file-transfer:evaluate', (_event: any, request: any, senderTrustLevel: unknown) => {
      if (!request || typeof request !== 'object') {
        throw new Error('file-transfer:evaluate requires a request object');
      }
      if (typeof senderTrustLevel !== 'number' || !Number.isFinite(senderTrustLevel)) {
        throw new Error('file-transfer:evaluate requires a finite number senderTrustLevel');
      }
      return fileTransferEngine.evaluateTransferRequest(request, senderTrustLevel);
    });
    ipcMain.handle('file-transfer:accept', (_event: any, request: any, remoteAgentId: string, remoteAgentName: string) => {
      if (!request || typeof request !== 'object') {
        throw new Error('file-transfer:accept requires a request object');
      }
      return fileTransferEngine.acceptInboundTransfer(request, remoteAgentId, remoteAgentName);
    });
    // Crypto Sprint 8 (CRITICAL): Validate chunk is a non-null object (was typed `any`).
    ipcMain.handle('file-transfer:process-chunk', (_event: any, chunk: any) => {
      if (!chunk || typeof chunk !== 'object') {
        throw new Error('file-transfer:process-chunk requires a chunk object');
      }
      return fileTransferEngine.processInboundChunk(chunk);
    });
    ipcMain.handle('file-transfer:finalize', async (_event: any, transferId: string) => {
      return fileTransferEngine.finalizeInboundTransfer(transferId);
    });
    ipcMain.handle('file-transfer:cancel', (_event: any, transferId: string) => {
      return fileTransferEngine.cancelTransfer(transferId);
    });
    ipcMain.handle('file-transfer:get-active', () => {
      return fileTransferEngine.getActiveTransfers().map((t) => ({
        ...t,
        receivedChunks: Array.from(t.receivedChunks),
        chunkBuffers: undefined, // Don't send raw buffers over IPC
      }));
    });
    ipcMain.handle('file-transfer:get-progress', (_event: any, transferId: string) => {
      return fileTransferEngine.getTransferProgress(transferId);
    });
    ipcMain.handle('file-transfer:get-audit', () => {
      return fileTransferEngine.getAuditLog();
    });
    console.log('[IPC] File transfer handlers registered');
  }

  // ── Hot-reload registration ─────────────────────────────────────
  registerHotReload('personality.ts', async () => {
    invalidateModuleCache('src/main/personality.ts');
    console.log('[HotReload] personality.ts reloaded — changes will apply on next session');
  });
  registerHotReload('friday-profile.ts', async () => {
    invalidateModuleCache('src/main/friday-profile.ts');
    console.log('[HotReload] friday-profile.ts reloaded');
  });
});

// ── Application lifecycle ───────────────────────────────────────────
app.on('before-quit', () => {
  isQuitting = true;
  // Seal any active text chat session into an episode before shutdown
  flushTextSession().catch(() => {});
  // Flush any pending chat history writes
  chatHistoryStore.flush().catch(() => {});
});

app.on('window-all-closed', async () => {
  screenCapture.stop();
  taskScheduler.stop();
  await pythonBridge.stop().catch(() => {});
  await gatewayManager.stop().catch(() => {});
  predictor.stop();
  ambientEngine.stop();
  notificationEngine.stop();
  memoryConsolidation.stop();
  clipboardIntelligence.stop();
  projectAwareness.stop();
  calendarIntegration.stop();
  meetingPrep.stop();
  meetingIntelligence.stop();
  contextPushCleanup?.();
  liveContextBridge.stop();
  contextGraph.stop();
  stopContextStreamBridge();
  communications.stop();
  fileTransferEngine.shutdown();
  osEvents.stop();
  fileWatcher.stop();
  briefingPipeline.stop();
  briefingDelivery.stop();
  perfMonitor.shutdown();
  agentNetwork.stop().catch(() => {});
  containerEngine.shutdown().catch(() => {});
  OllamaLifecycle.getInstance().stop();
  globalShortcut.unregisterAll();
  await mcpClient.disconnect();

  // ── Sovereign Vault v2: zero all key material on shutdown ──
  destroyHmac();
  destroyVault();
  settingsManager.clearApiKeysFromEnv();

  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (mainWindow === null) createWindow();
});
