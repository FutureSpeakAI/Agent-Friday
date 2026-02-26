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

export function registerIntegrationHandlers(): void {
  // ── Intelligence ────────────────────────────────────────────────────
  ipcMain.handle('intelligence:get-briefing', async () => {
    return intelligenceEngine.getBriefingSummary();
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
  ipcMain.handle('self-improve:read-file', async (_event, filePath: string) => {
    return readProjectFile(filePath);
  });

  ipcMain.handle('self-improve:list-files', async (_event, dirPath: string) => {
    return listProjectFiles(dirPath);
  });

  ipcMain.handle(
    'self-improve:propose',
    async (_event, filePath: string, newContent: string, description: string) => {
      return proposeCodeChange(filePath, newContent, description);
    },
  );

  ipcMain.handle('self-improve:respond', async (_event, id: string, approved: boolean) => {
    return handleChangeResponse(id, approved);
  });

  // ── Connector registry ──────────────────────────────────────────────
  ipcMain.handle('connectors:list-tools', () => connectorRegistry.getAllTools());

  ipcMain.handle(
    'connectors:call-tool',
    async (_event, toolName: string, args: Record<string, unknown>) => {
      return connectorRegistry.executeTool(toolName, args);
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

  ipcMain.handle('gateway:approve-pairing', async (_event, code: string, tier?: string) => {
    return gatewayManager.approvePairing(code, tier as any);
  });

  ipcMain.handle('gateway:revoke-pairing', async (_event, identityId: string) => {
    return gatewayManager.revokePairing(identityId);
  });

  ipcMain.handle('gateway:get-active-sessions', () => gatewayManager.getActiveSessions());

  // ── Call integration ────────────────────────────────────────────────
  ipcMain.handle('call:is-virtual-audio-available', () => {
    return callIntegration.isVirtualAudioAvailable();
  });

  ipcMain.handle('call:enter-call-mode', (_event, meetingUrl?: string) => {
    return callIntegration.enterCallMode(meetingUrl);
  });

  ipcMain.handle('call:exit-call-mode', () => callIntegration.exitCallMode());
  ipcMain.handle('call:is-in-call-mode', () => callIntegration.isInCallMode());

  ipcMain.handle('call:open-meeting-url', (_event, url: string) => {
    return callIntegration.openMeetingUrl(url);
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
