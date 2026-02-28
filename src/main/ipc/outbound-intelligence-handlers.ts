/**
 * IPC handlers for the Outbound Intelligence system.
 *
 * All handlers are prefixed with 'outbound:' and follow the same
 * pattern as other handler modules in the ipc/ directory.
 */

import { ipcMain } from 'electron';
import {
  outboundIntelligence,
  type OutboundChannel,
  type TonePreset,
  type MessagePriority,
  type DraftStatus,
} from '../outbound-intelligence';

export function registerOutboundIntelligenceHandlers(): void {
  // ── Draft CRUD ──────────────────────────────────────────────────

  ipcMain.handle(
    'outbound:create-draft',
    (_event, params: {
      recipientName: string;
      body: string;
      subject?: string;
      context: string;
      channel?: OutboundChannel;
      channelAddress?: string;
      tone?: TonePreset;
      priority?: MessagePriority;
      trigger?: string;
      commitmentId?: string;
    }) => {
      return outboundIntelligence.createDraft(params as any);
    }
  );

  ipcMain.handle('outbound:get-draft', (_event, id: string) => {
    return outboundIntelligence.getDraft(id);
  });

  ipcMain.handle(
    'outbound:edit-draft',
    (_event, id: string, updates: Record<string, unknown>) => {
      return outboundIntelligence.editDraft(id, updates as any);
    }
  );

  ipcMain.handle('outbound:delete-draft', (_event, id: string) => {
    return outboundIntelligence.deleteDraft(id);
  });

  ipcMain.handle(
    'outbound:get-drafts',
    (_event, opts?: { status?: DraftStatus; channel?: OutboundChannel; limit?: number }) => {
      return outboundIntelligence.getAllDrafts(opts);
    }
  );

  ipcMain.handle('outbound:get-pending', () => {
    return outboundIntelligence.getPendingDrafts();
  });

  // ── Approval Workflow ───────────────────────────────────────────

  ipcMain.handle('outbound:approve', (_event, id: string) => {
    return outboundIntelligence.approveDraft(id);
  });

  ipcMain.handle('outbound:reject', (_event, id: string) => {
    return outboundIntelligence.rejectDraft(id);
  });

  ipcMain.handle('outbound:approve-all', () => {
    return outboundIntelligence.approveAll();
  });

  ipcMain.handle('outbound:try-auto-approve', (_event, draftId: string) => {
    return outboundIntelligence.tryAutoApprove(draftId);
  });

  // ── Sending ─────────────────────────────────────────────────────

  ipcMain.handle('outbound:send', async (_event, id: string) => {
    return outboundIntelligence.sendDraft(id);
  });

  ipcMain.handle('outbound:approve-and-send', async (_event, id: string) => {
    return outboundIntelligence.approveAndSend(id);
  });

  ipcMain.handle('outbound:send-all-approved', async () => {
    return outboundIntelligence.sendAllApproved();
  });

  // ── Batch Review ────────────────────────────────────────────────

  ipcMain.handle('outbound:batch-review', () => {
    return outboundIntelligence.getBatchReview();
  });

  // ── Style Profiles ──────────────────────────────────────────────

  ipcMain.handle('outbound:get-style-profile', (_event, recipientPersonId: string) => {
    return outboundIntelligence.getStyleProfile(recipientPersonId);
  });

  ipcMain.handle(
    'outbound:update-style-profile',
    (_event, recipientPersonId: string, recipientName: string, observation: Record<string, unknown>) => {
      return outboundIntelligence.updateStyleProfile(recipientPersonId, recipientName, observation as any);
    }
  );

  ipcMain.handle('outbound:get-all-style-profiles', () => {
    return outboundIntelligence.getAllStyleProfiles();
  });

  // ── Standing Permissions ────────────────────────────────────────

  ipcMain.handle(
    'outbound:add-standing-permission',
    (_event, params: {
      recipientPersonId: string;
      recipientName: string;
      channels: OutboundChannel[];
      maxPriority: MessagePriority;
      expiresAt?: number;
    }) => {
      return outboundIntelligence.addStandingPermission(params);
    }
  );

  ipcMain.handle('outbound:revoke-standing-permission', (_event, id: string) => {
    return outboundIntelligence.revokeStandingPermission(id);
  });

  ipcMain.handle('outbound:delete-standing-permission', (_event, id: string) => {
    return outboundIntelligence.deleteStandingPermission(id);
  });

  ipcMain.handle('outbound:get-standing-permissions', () => {
    return outboundIntelligence.getStandingPermissions();
  });

  ipcMain.handle('outbound:get-all-standing-permissions', () => {
    return outboundIntelligence.getAllStandingPermissions();
  });

  // ── Stats & Config ──────────────────────────────────────────────

  ipcMain.handle('outbound:get-stats', () => {
    return outboundIntelligence.getStats();
  });

  ipcMain.handle('outbound:get-config', () => {
    return outboundIntelligence.getConfig();
  });

  ipcMain.handle('outbound:update-config', (_event, partial: Record<string, unknown>) => {
    return outboundIntelligence.updateConfig(partial as any);
  });

  ipcMain.handle('outbound:get-prompt-context', () => {
    return outboundIntelligence.getPromptContext();
  });
}
