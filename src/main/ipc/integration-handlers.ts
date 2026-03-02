/**
 * Integration IPC handlers — intelligence, self-improve, connectors, gateway, call, session health.
 */
import { ipcMain } from 'electron';
import { intelligenceEngine } from '../intelligence';
import {
  readProjectFile,
  listProjectFiles,
  proposeCodeChange,
  handleChangeResponse,
} from '../self-improve';
import { connectorRegistry } from '../connectors/registry';
import { gatewayManager } from '../gateway/gateway-manager';
import { createTelegramAdapter } from '../gateway/adapters/telegram';
import { callIntegration } from '../call-integration';
import { sessionHealth } from '../session-health';
import { settingsManager } from '../settings';
import { assertToolCallArgs, assertString } from './validate';

export function registerIntegrationHandlers(): void {
  // ── Intelligence ────────────────────────────────────────────────────
  ipcMain.handle('intelligence:get-briefing', async () => {
    return intelligenceEngine.getBriefingSummary();
  });

  ipcMain.handle('intelligence:list-all', () => {
    return intelligenceEngine.getAllBriefings();
  });

  ipcMain.handle(
    'intelligence:setup',
    async (
      _event,
      topics: Array<{ topic: string; schedule: string; priority: string }>,
    ) => {
      return intelligenceEngine.setupFromOnboarding(topics);
    },
  );

  // ── Self-improvement ────────────────────────────────────────────────
  // Note: readProjectFile/listProjectFiles/proposeCodeChange all have internal validatePath()
  // that confines paths to PROJECT_ROOT. These type checks are defense-in-depth at the IPC layer.
  ipcMain.handle('self-improve:read-file', async (_event, filePath: unknown) => {
    assertString(filePath, 'self-improve:read-file filePath', 1_000);
    return readProjectFile(filePath as string);
  });

  ipcMain.handle('self-improve:list-files', async (_event, dirPath: unknown) => {
    assertString(dirPath, 'self-improve:list-files dirPath', 1_000);
    return listProjectFiles(dirPath as string);
  });

  // Crypto Sprint 8 (CRITICAL): Cap newContent size to prevent memory exhaustion.
  ipcMain.handle(
    'self-improve:propose',
    async (_event, filePath: unknown, newContent: unknown, description: unknown) => {
      assertString(filePath, 'self-improve:propose filePath', 1_000);
      assertString(newContent, 'self-improve:propose newContent', 1_000_000); // 1MB cap
      assertString(description, 'self-improve:propose description', 10_000);
      return proposeCodeChange(filePath as string, newContent as string, description as string);
    },
  );

  ipcMain.handle('self-improve:respond', async (_event, id: string, approved: boolean) => {
    return handleChangeResponse(id, approved);
  });

  // ── Connector registry ──────────────────────────────────────────────
  ipcMain.handle('connectors:list-tools', () => connectorRegistry.getAllTools());

  // Crypto Sprint 8 (CRITICAL): Validate tool name and args before dispatching.
  ipcMain.handle(
    'connectors:call-tool',
    async (_event, toolName: unknown, args: unknown) => {
      const { validatedName, validatedArgs } = assertToolCallArgs(toolName, args, 'connectors:call-tool');
      return connectorRegistry.executeTool(validatedName, validatedArgs);
    },
  );

  ipcMain.handle('connectors:is-connector-tool', (_event, toolName: string) => {
    return connectorRegistry.isConnectorTool(toolName);
  });

  ipcMain.handle('connectors:status', () => connectorRegistry.getStatus());
  ipcMain.handle('connectors:get-tool-routing', () => connectorRegistry.buildToolRoutingContext());

  // ── Messaging gateway ───────────────────────────────────────────────
  ipcMain.handle('gateway:get-status', () => gatewayManager.getStatus());

  ipcMain.handle('gateway:set-enabled', async (_event, enabled: boolean) => {
    await settingsManager.setSetting('gatewayEnabled', enabled);
    if (enabled && !gatewayManager.isRunning()) {
      await gatewayManager.initialize();
      const settings = settingsManager.get();
      if (settings.telegramBotToken) {
        const adapter = createTelegramAdapter(settings.telegramBotToken);
        await gatewayManager.registerAdapter(adapter);
      }
    } else if (!enabled && gatewayManager.isRunning()) {
      await gatewayManager.stop();
    }
    return gatewayManager.getStatus();
  });

  ipcMain.handle('gateway:get-pending-pairings', () => gatewayManager.getPendingPairings());
  ipcMain.handle('gateway:get-paired-identities', () => gatewayManager.getPairedIdentities());

  // Crypto Sprint 21: Validate pairing code and trust tier at IPC boundary.
  ipcMain.handle('gateway:approve-pairing', async (_event, code: unknown, tier?: unknown) => {
    assertString(code, 'gateway:approve-pairing code', 200);
    if (tier !== undefined && tier !== null) {
      assertString(tier, 'gateway:approve-pairing tier', 50);
    }
    return gatewayManager.approvePairing(code as string, tier as any);
  });

  ipcMain.handle('gateway:revoke-pairing', async (_event, identityId: unknown) => {
    assertString(identityId, 'gateway:revoke-pairing identityId', 500);
    return gatewayManager.revokePairing(identityId as string);
  });

  ipcMain.handle('gateway:get-active-sessions', () => gatewayManager.getActiveSessions());

  // ── Call integration ────────────────────────────────────────────────
  ipcMain.handle('call:is-virtual-audio-available', () => {
    return callIntegration.isVirtualAudioAvailable();
  });

  ipcMain.handle('call:enter-call-mode', (_event, meetingUrl?: unknown) => {
    if (meetingUrl !== undefined && meetingUrl !== null) {
      assertString(meetingUrl, 'call:enter-call-mode meetingUrl', 2_048);
    }
    return callIntegration.enterCallMode(meetingUrl as string | undefined);
  });

  ipcMain.handle('call:exit-call-mode', () => callIntegration.exitCallMode());
  ipcMain.handle('call:is-in-call-mode', () => callIntegration.isInCallMode());

  ipcMain.handle('call:open-meeting-url', (_event, url: unknown) => {
    assertString(url, 'call:open-meeting-url url', 2_048);
    return callIntegration.openMeetingUrl(url as string);
  });

  ipcMain.handle('call:get-context-string', () => callIntegration.getContextString());

  // ── Session health ──────────────────────────────────────────────────
  ipcMain.handle('session-health:get', () => sessionHealth.getHealthSummary());
  ipcMain.handle('session-health:reset', () => sessionHealth.reset());
  ipcMain.handle('session-health:session-started', () => sessionHealth.sessionStarted());

  ipcMain.handle(
    'session-health:record-tool-call',
    (_event, name: string, success: boolean, durationMs: number) => {
      sessionHealth.recordToolCall(name, success, durationMs);
    },
  );

  ipcMain.handle('session-health:record-error', (_event, source: string, message: string) => {
    sessionHealth.recordError(source, message);
  });

  ipcMain.handle('session-health:record-ws-close', (_event, code: number, reason: string) => {
    sessionHealth.recordWsClose(code, reason);
  });

  ipcMain.handle(
    'session-health:record-reconnect',
    (_event, type: 'preemptive' | 'auto-retry', success: boolean) => {
      sessionHealth.recordReconnect(type, success);
    },
  );

  ipcMain.handle('session-health:record-voice-anchor', () => {
    sessionHealth.recordVoiceAnchor();
  });

  ipcMain.handle('session-health:record-prompt-size', (_event, chars: number) => {
    sessionHealth.recordPromptSize(chars);
  });
}
