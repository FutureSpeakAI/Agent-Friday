import { app, BrowserWindow, session, Tray, Menu, globalShortcut, nativeImage, dialog } from 'electron';
import path from 'path';
import fs from 'fs';
import { startServer } from './server';
import { mcpClient } from './mcp-client';

// ── Global error handlers (must be first) ───────────────────────────
const logPath = path.join(app.getPath('userData'), 'crash.log');

function writeCrashLog(label: string, err: unknown): void {
  const ts = new Date().toISOString();
  const msg = err instanceof Error ? `${err.message}\n${err.stack}` : String(err);
  const entry = `[${ts}] ${label}: ${msg}\n\n`;
  try {
    fs.appendFileSync(logPath, entry);
  } catch {
    // If we can't even write logs, there's nothing else to do
  }
}

process.on('uncaughtException', (err) => {
  console.error('[FATAL] Uncaught exception:', err);
  writeCrashLog('uncaughtException', err);
  dialog.showErrorBox(
    'Agent Friday — Unexpected Error',
    `An unexpected error occurred:\n\n${err.message}\n\nThe app will try to continue, but you may want to restart.\nFull details saved to: ${logPath}`
  );
});

process.on('unhandledRejection', (reason) => {
  console.error('[ERROR] Unhandled promise rejection:', reason);
  writeCrashLog('unhandledRejection', reason);
});

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
import { ensureProfileOnDisk } from './eve-profile';
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
import { gitLoader } from './git-loader';
import { trustGraph } from './trust-graph';
import { meetingIntelligence } from './meeting-intelligence';
import { startContextStreamBridge, stopContextStreamBridge } from './context-stream-bridge';
import { contextGraph } from './context-graph';
import { commitmentTracker } from './commitment-tracker';
import { dailyBriefingEngine } from './daily-briefing';
import { workflowRecorder } from './workflow-recorder';
import { workflowExecutor } from './workflow-executor';
import { unifiedInbox } from './unified-inbox';
import { outboundIntelligence } from './outbound-intelligence';
import { intelligenceRouter } from './intelligence-router';
import { agentNetwork } from './agent-network';
import { superpowerEcosystem } from './superpower-ecosystem';
import { stateExport } from './state-export';
import { memoryQuality } from './memory-quality';
import { personalityCalibration } from './personality-calibration';
import { memoryPersonalityBridge } from './memory-personality-bridge';

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
} from './ipc';

// ── Application state ───────────────────────────────────────────────
let mainWindow: BrowserWindow | null = null;
let tray: Tray | null = null;
let isQuitting = false;
let serverPort = 3333;

const isDev = !app.isPackaged;

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
    },
  });

  setMainWindow(mainWindow);
  setMainWindowForSelfImprove(mainWindow);
  setConsentWindow(mainWindow);

  mainWindow.maximize();
  mainWindow.once('ready-to-show', () => {
    mainWindow?.show();
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

  if (isDev) {
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
  // Initialize settings first (API keys depend on it)
  try {
    await settingsManager.initialize();
    console.log('[EVE] Settings loaded');

    if (!isDev && settingsManager.get().autoLaunch) {
      app.setLoginItemSettings({ openAtLogin: true, path: app.getPath('exe') });
    }
  } catch (err) {
    console.warn('[EVE] Settings init failed:', err);
  }

  // Initialize memory system (personality depends on it)
  try {
    await memoryManager.initialize();
    console.log('[EVE] Memory system initialized');
  } catch (err) {
    console.warn('[EVE] Memory init failed:', err);
  }

  // Initialize integrity system (depends on settings + memory)
  try {
    await integrityManager.initialize();

    // Check memory integrity on startup
    const longTerm = memoryManager.getLongTerm();
    const mediumTerm = memoryManager.getMediumTerm();
    const memoryChanges = integrityManager.checkMemories(longTerm, mediumTerm);
    if (memoryChanges) {
      console.log('[EVE] Memory changes detected since last session — agent will be notified');
    }

    // Verify agent identity integrity
    const agentCfg = settingsManager.getAgentConfig();
    if (agentCfg.onboardingComplete) {
      const identityJson = JSON.stringify(agentCfg, Object.keys(agentCfg).sort());
      const identityOk = integrityManager.verifyIdentity(identityJson);
      if (!identityOk) {
        console.warn('[EVE] Agent identity has been modified externally — agent will be notified');
      }
    }

    // Sign everything on first run (when no manifest exists yet)
    const state = integrityManager.getState();
    if (state.initialized && state.lawsIntact && !integrityManager.isInSafeMode()) {
      const userName = agentCfg.userName || '';
      const lawsText = getCanonicalLaws(userName);
      const identityJson = JSON.stringify(agentCfg, Object.keys(agentCfg).sort());
      const ltJson = JSON.stringify(longTerm, null, 2);
      const mtJson = JSON.stringify(mediumTerm, null, 2);
      const ltSnap = longTerm.map((e) => ({ id: e.id, fact: e.fact }));
      const mtSnap = mediumTerm.map((e) => ({ id: e.id, observation: e.observation }));
      await integrityManager.signAll(lawsText, identityJson, ltSnap, mtSnap, ltJson, mtJson);
    }

    console.log('[EVE] Integrity system initialized');
  } catch (err) {
    console.warn('[EVE] Integrity init failed:', err);
  }

  // Start the Express API server
  serverPort = await startServer();
  console.log(`[EVE] API server running on port ${serverPort}`);

  // Initialize MCP client
  try {
    await mcpClient.connect();
    console.log('[EVE] MCP client connected');
  } catch (err) {
    console.warn('[EVE] MCP client failed to connect:', err);
  }

  // Auto-grant microphone and camera permissions
  session.defaultSession.setPermissionRequestHandler((_webContents, permission, callback) => {
    const allowed = ['media', 'mediaKeySystem', 'display-capture', 'audioCapture'];
    callback(allowed.includes(permission));
  });

  createWindow();

  if (mainWindow) {
    sessionHealth.setMainWindow(mainWindow);
  }

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
    console.warn('[Tray] Failed to load icon from', iconPath, err);
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
    console.log('[EVE] Intelligence engine initialized');
  } catch (err) {
    console.warn('[EVE] Intelligence engine init failed:', err);
  }

  ensureProfileOnDisk().catch((err) => {
    console.warn('[EVE] Profile write failed:', err);
  });

  if (mainWindow) {
    taskScheduler.initialize(mainWindow).catch((err) => {
      console.warn('[EVE] Scheduler init failed:', err);
    });

    predictor.initialize(mainWindow);
    ambientEngine.initialize();

    sentimentEngine.initialize().catch((err) => {
      console.warn('[EVE] Sentiment init failed:', err);
    });

    episodicMemory.initialize().catch((err) => {
      console.warn('[EVE] Episodic memory init failed:', err);
    });

    relationshipMemory.initialize().catch((err) => {
      console.warn('[EVE] Relationship memory init failed:', err);
    });

    trustGraph.initialize().catch((err) => {
      console.warn('[EVE] Trust graph init failed:', err);
    });

    semanticSearch.initialize().then(async () => {
      console.log('[EVE] Semantic search initialized');
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
        console.log(`[EVE] Indexed ${items.length} existing memories for semantic search`);
      }
    }).catch((err) => {
      console.warn('[EVE] Semantic search init failed:', err);
    });

    memoryConsolidation.initialize();
    notificationEngine.initialize(mainWindow);
    clipboardIntelligence.initialize(mainWindow);
    projectAwareness.initialize(mainWindow);
    documentIngestion.initialize(mainWindow);

    agentRunner.initialize(mainWindow);
    console.log('[EVE] Agent runner initialized');

    // Initialize GitLoader (GitHub repo loading + code intelligence)
    gitLoader.initialize().then(() => {
      console.log('[EVE] GitLoader initialized');
    }).catch((err) => {
      console.warn('[EVE] GitLoader init failed:', err);
    });

    // Initialize office manager (pixel-art agent visualization)
    officeManager.setMainWindow(mainWindow);
    officeManager.setServerPort(serverPort);
    console.log('[EVE] Office manager initialized');

    calendarIntegration.init().then(() => {
      meetingPrep.init(mainWindow!);
      console.log('[EVE] Calendar + meeting prep initialized');
    }).catch((err) => {
      console.warn('[EVE] Calendar init failed:', err);
    });

    // Initialize Meeting Intelligence engine
    meetingIntelligence.initialize().then(() => {
      console.log('[EVE] Meeting Intelligence initialized');
    }).catch((err) => {
      console.warn('[EVE] Meeting Intelligence init failed:', err);
    });

    communications.init().catch((err) => {
      console.warn('[EVE] Communications init failed:', err);
    });

    commitmentTracker.initialize().catch((err) => {
      console.warn('[EVE] Commitment tracker init failed:', err);
    });

    dailyBriefingEngine.initialize().catch((err) => {
      console.warn('[EVE] Daily briefing init failed:', err);
    });

    workflowRecorder.initialize().catch((err) => {
      console.warn('[EVE] Workflow recorder init failed:', err);
    });

    workflowExecutor.initialize().catch((err) => {
      console.warn('[EVE] Workflow executor init failed:', err);
    });

    unifiedInbox.initialize().catch((err) => {
      console.warn('[EVE] Unified inbox init failed:', err);
    });

    outboundIntelligence.initialize().catch((err) => {
      console.warn('[EVE] Outbound intelligence init failed:', err);
    });

    intelligenceRouter.initialize().catch((err) => {
      console.warn('[EVE] Intelligence router init failed:', err);
    });

    agentNetwork.initialize().catch((err) => {
      console.warn('[EVE] Agent network init failed:', err);
    });

    superpowerEcosystem.initialize().catch((err) => {
      console.warn('[EVE] Superpower ecosystem init failed:', err);
    });

    stateExport.initialize().catch((err) => {
      console.warn('[EVE] State export engine init failed:', err);
    });

    memoryQuality.initialize().catch((err) => {
      console.warn('[EVE] Memory quality engine init failed:', err);
    });

    personalityCalibration.initialize().catch((err) => {
      console.warn('[EVE] Personality calibration init failed:', err);
    });

    memoryPersonalityBridge.initialize().catch((err) => {
      console.warn('[EVE] Memory-personality bridge init failed:', err);
    });

    // Start the context stream bridge (after all engines are initialized)
    startContextStreamBridge();
    contextGraph.start();
    console.log('[EVE] Context stream bridge + graph started');

    connectorRegistry.initialize().catch((err) => {
      console.warn('[EVE] Connector registry init failed:', err);
    });

    const gatewaySettings = settingsManager.get();
    if (gatewaySettings.gatewayEnabled) {
      gatewayManager.initialize().then(async () => {
        if (gatewaySettings.telegramBotToken) {
          const telegramAdapter = createTelegramAdapter(gatewaySettings.telegramBotToken);
          await gatewayManager.registerAdapter(telegramAdapter);
        }
        console.log('[EVE] Messaging gateway initialized');
      }).catch((err) => {
        console.warn('[EVE] Gateway init failed:', err);
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

  // ── Hot-reload registration ─────────────────────────────────────
  registerHotReload('personality.ts', async () => {
    invalidateModuleCache('src/main/personality.ts');
    console.log('[HotReload] personality.ts reloaded — changes will apply on next session');
  });
  registerHotReload('eve-profile.ts', async () => {
    invalidateModuleCache('src/main/eve-profile.ts');
    console.log('[HotReload] eve-profile.ts reloaded');
  });
});

// ── Application lifecycle ───────────────────────────────────────────
app.on('before-quit', () => {
  isQuitting = true;
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
  contextGraph.stop();
  stopContextStreamBridge();
  communications.stop();
  agentNetwork.stop().catch(() => {});
  globalShortcut.unregisterAll();
  await mcpClient.disconnect();
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (mainWindow === null) createWindow();
});
