/**
 * IPC handlers for Commitment Tracker — Track IV Phase 1.
 * Exposes commitment tracking, follow-up detection, and outbound
 * message tracking to the renderer.
 *
 * cLaw Gate: Follow-up suggestions are READ-ONLY queries.
 * Commitment creation only happens through explicit user action or
 * memory extraction. No messages are ever sent automatically.
 */

import { ipcMain } from 'electron';
import { commitmentTracker } from '../commitment-tracker';
import type { CommitmentMention } from '../commitment-tracker';

export function registerCommitmentTrackerHandlers(): void {
  // ── Commitment Queries ──────────────────────────────────────────

  ipcMain.handle('commitment:get-active', () => {
    return commitmentTracker.getActiveCommitments();
  });

  ipcMain.handle('commitment:get-overdue', () => {
    return commitmentTracker.getOverdueCommitments();
  });

  ipcMain.handle('commitment:get-by-person', (_event, personName: string) => {
    if (typeof personName !== 'string' || !personName) {
      throw new Error('commitment:get-by-person requires a string personName');
    }
    return commitmentTracker.getCommitmentsByPerson(personName);
  });

  ipcMain.handle('commitment:get-upcoming', (_event, withinHours?: number) => {
    return commitmentTracker.getUpcomingDeadlines(
      typeof withinHours === 'number' ? withinHours : 72
    );
  });

  ipcMain.handle('commitment:get-by-id', (_event, id: string) => {
    if (typeof id !== 'string' || !id) {
      throw new Error('commitment:get-by-id requires a string id');
    }
    return commitmentTracker.getCommitmentById(id);
  });

  ipcMain.handle('commitment:get-all', () => {
    return commitmentTracker.getAllCommitments();
  });

  // ── Commitment Mutations ────────────────────────────────────────

  ipcMain.handle('commitment:add', (_event, mention: CommitmentMention) => {
    if (!mention || typeof mention.description !== 'string') {
      throw new Error('commitment:add requires a CommitmentMention with description');
    }
    return commitmentTracker.addCommitment(mention);
  });

  ipcMain.handle('commitment:complete', (_event, id: string, notes?: string) => {
    if (typeof id !== 'string' || !id) {
      throw new Error('commitment:complete requires a string id');
    }
    return commitmentTracker.completeCommitment(id, notes);
  });

  ipcMain.handle('commitment:cancel', (_event, id: string, reason?: string) => {
    if (typeof id !== 'string' || !id) {
      throw new Error('commitment:cancel requires a string id');
    }
    return commitmentTracker.cancelCommitment(id, reason);
  });

  ipcMain.handle('commitment:snooze', (_event, id: string, untilMs: number) => {
    if (typeof id !== 'string' || !id) {
      throw new Error('commitment:snooze requires a string id');
    }
    if (typeof untilMs !== 'number' || untilMs <= Date.now()) {
      throw new Error('commitment:snooze requires a future timestamp');
    }
    return commitmentTracker.snoozeCommitment(id, untilMs);
  });

  // ── Outbound Message Tracking ───────────────────────────────────

  ipcMain.handle('commitment:track-outbound', (_event, msg: {
    recipient: string;
    channel: string;
    summary: string;
  }) => {
    if (!msg || typeof msg.recipient !== 'string' || !msg.recipient) {
      throw new Error('commitment:track-outbound requires { recipient, channel, summary }');
    }
    return commitmentTracker.trackOutboundMessage(msg);
  });

  ipcMain.handle('commitment:record-reply', (_event, recipient: string, channel: string) => {
    if (typeof recipient !== 'string' || typeof channel !== 'string') {
      throw new Error('commitment:record-reply requires string recipient and channel');
    }
    return commitmentTracker.recordReply(recipient, channel);
  });

  ipcMain.handle('commitment:get-unreplied', () => {
    return commitmentTracker.getUnrepliedMessages();
  });

  // ── Follow-Up Suggestions ──────────────────────────────────────

  ipcMain.handle('commitment:generate-suggestions', () => {
    return commitmentTracker.generateFollowUpSuggestions();
  });

  ipcMain.handle('commitment:get-pending-suggestions', () => {
    return commitmentTracker.getPendingSuggestions();
  });

  ipcMain.handle('commitment:mark-suggestion-delivered', (_event, id: string) => {
    if (typeof id !== 'string' || !id) {
      throw new Error('commitment:mark-suggestion-delivered requires a string id');
    }
    return commitmentTracker.markSuggestionDelivered(id);
  });

  ipcMain.handle('commitment:mark-suggestion-acted-on', (_event, id: string) => {
    if (typeof id !== 'string' || !id) {
      throw new Error('commitment:mark-suggestion-acted-on requires a string id');
    }
    return commitmentTracker.markSuggestionActedOn(id);
  });

  // ── Context & Status ────────────────────────────────────────────

  ipcMain.handle('commitment:context-string', () => {
    return commitmentTracker.getContextString();
  });

  ipcMain.handle('commitment:prompt-context', () => {
    return commitmentTracker.getPromptContext();
  });

  ipcMain.handle('commitment:status', () => {
    return commitmentTracker.getStatus();
  });

  ipcMain.handle('commitment:config', () => {
    return commitmentTracker.getConfig();
  });
}
