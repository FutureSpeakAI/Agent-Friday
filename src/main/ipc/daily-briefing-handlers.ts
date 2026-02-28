/**
 * IPC handlers for Daily Briefing System — Track IV Phase 2.
 * Exposes briefing generation, scheduling, history, and delivery
 * tracking to the renderer.
 *
 * cLaw Gate: Briefing generation is a READ-ONLY aggregation.
 * Delivery tracking only records what happened — it never initiates
 * outbound communication. No actions without explicit user approval.
 */

import { ipcMain } from 'electron';
import { dailyBriefingEngine } from '../daily-briefing';
import type { BriefingSourceData, BriefingType, BriefingChannel } from '../daily-briefing';

export function registerDailyBriefingHandlers(): void {
  // ── Generation ──────────────────────────────────────────────────

  ipcMain.handle('briefing:generate', (_event, type: BriefingType, sourceData: BriefingSourceData) => {
    if (!type || !['morning', 'midday', 'evening'].includes(type)) {
      throw new Error('briefing:generate requires type: morning | midday | evening');
    }
    return dailyBriefingEngine.generateBriefing(type, sourceData || {});
  });

  ipcMain.handle('briefing:should-generate', () => {
    return dailyBriefingEngine.shouldGenerateBriefing();
  });

  ipcMain.handle('briefing:adaptive-length', (_event, sourceData: BriefingSourceData) => {
    return dailyBriefingEngine.calculateAdaptiveLength(sourceData || {});
  });

  // ── Queries ─────────────────────────────────────────────────────

  ipcMain.handle('briefing:get-latest', (_event, type?: BriefingType) => {
    return dailyBriefingEngine.getLatestBriefing(type);
  });

  ipcMain.handle('briefing:get-latest-today', (_event, type: BriefingType) => {
    if (!type) throw new Error('briefing:get-latest-today requires a type');
    return dailyBriefingEngine.getLatestBriefingToday(type);
  });

  ipcMain.handle('briefing:get-by-id', (_event, id: string) => {
    if (typeof id !== 'string' || !id) {
      throw new Error('briefing:get-by-id requires a string id');
    }
    return dailyBriefingEngine.getBriefingById(id);
  });

  ipcMain.handle('briefing:get-history', (_event, limit?: number) => {
    return dailyBriefingEngine.getBriefingHistory(
      typeof limit === 'number' ? limit : 10
    );
  });

  ipcMain.handle('briefing:get-all', () => {
    return dailyBriefingEngine.getAllBriefings();
  });

  // ── Delivery Tracking ───────────────────────────────────────────

  ipcMain.handle('briefing:mark-delivered', (_event, id: string, channel: BriefingChannel) => {
    if (typeof id !== 'string' || !id) {
      throw new Error('briefing:mark-delivered requires a string id');
    }
    if (!channel) {
      throw new Error('briefing:mark-delivered requires a channel');
    }
    return dailyBriefingEngine.markDelivered(id, channel);
  });

  ipcMain.handle('briefing:mark-delivery-failed', (_event, id: string, channel: BriefingChannel, reason: string) => {
    if (typeof id !== 'string' || !id) {
      throw new Error('briefing:mark-delivery-failed requires a string id');
    }
    return dailyBriefingEngine.markDeliveryFailed(id, channel, reason || 'unknown');
  });

  // ── Staleness & Scheduling ──────────────────────────────────────

  ipcMain.handle('briefing:is-stale', (_event, type: BriefingType) => {
    if (!type) throw new Error('briefing:is-stale requires a type');
    return dailyBriefingEngine.isBriefingStale(type);
  });

  ipcMain.handle('briefing:scheduled-time-today', (_event, timeStr: string) => {
    if (typeof timeStr !== 'string') {
      throw new Error('briefing:scheduled-time-today requires a time string');
    }
    return dailyBriefingEngine.getScheduledTimeToday(timeStr);
  });

  // ── Formatting ──────────────────────────────────────────────────

  ipcMain.handle('briefing:format-text', (_event, id: string) => {
    const briefing = dailyBriefingEngine.getBriefingById(id);
    if (!briefing) throw new Error(`Briefing not found: ${id}`);
    return dailyBriefingEngine.formatAsText(briefing);
  });

  ipcMain.handle('briefing:format-markdown', (_event, id: string) => {
    const briefing = dailyBriefingEngine.getBriefingById(id);
    if (!briefing) throw new Error(`Briefing not found: ${id}`);
    return dailyBriefingEngine.formatAsMarkdown(briefing);
  });

  // ── Context & Status ────────────────────────────────────────────

  ipcMain.handle('briefing:context-string', () => {
    return dailyBriefingEngine.getContextString();
  });

  ipcMain.handle('briefing:prompt-context', () => {
    return dailyBriefingEngine.getPromptContext();
  });

  ipcMain.handle('briefing:status', () => {
    return dailyBriefingEngine.getStatus();
  });

  ipcMain.handle('briefing:config', () => {
    return dailyBriefingEngine.getConfig();
  });
}
