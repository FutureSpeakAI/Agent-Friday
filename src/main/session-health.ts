/**
 * session-health.ts — Session health monitoring and observability.
 * Tracks reconnects, voice anchors, tool calls, errors, and prompt size.
 */

import { BrowserWindow } from 'electron';

interface SessionMetrics {
  sessionStartTime: number;
  reconnectCount: number;
  reconnectLog: Array<{ timestamp: number; type: 'preemptive' | 'auto-retry'; success: boolean }>;
  voiceAnchorCount: number;
  toolCalls: Map<string, { success: number; failure: number; totalMs: number }>;
  wsCloseReasons: Array<{ timestamp: number; code: number; reason: string }>;
  promptSizeChars: number;
  errors: Array<{ timestamp: number; source: string; message: string }>;
}

class SessionHealth {
  private metrics: SessionMetrics;
  private mainWindow: BrowserWindow | null = null;

  constructor() {
    this.metrics = this.freshMetrics();
  }

  private freshMetrics(): SessionMetrics {
    return {
      sessionStartTime: 0,
      reconnectCount: 0,
      reconnectLog: [],
      voiceAnchorCount: 0,
      toolCalls: new Map(),
      wsCloseReasons: [],
      promptSizeChars: 0,
      errors: [],
    };
  }

  setMainWindow(win: BrowserWindow) {
    this.mainWindow = win;
  }

  sessionStarted() {
    this.metrics.sessionStartTime = Date.now();
  }

  recordReconnect(type: 'preemptive' | 'auto-retry', success: boolean) {
    this.metrics.reconnectCount++;
    this.metrics.reconnectLog.push({ timestamp: Date.now(), type, success });
    // Keep last 20
    if (this.metrics.reconnectLog.length > 20) {
      this.metrics.reconnectLog = this.metrics.reconnectLog.slice(-20);
    }
    this.emit();
  }

  recordVoiceAnchor() {
    this.metrics.voiceAnchorCount++;
    this.emit();
  }

  recordToolCall(name: string, success: boolean, durationMs: number) {
    const existing = this.metrics.toolCalls.get(name) || { success: 0, failure: 0, totalMs: 0 };
    if (success) {
      existing.success++;
    } else {
      existing.failure++;
    }
    existing.totalMs += durationMs;
    this.metrics.toolCalls.set(name, existing);
  }

  recordWsClose(code: number, reason: string) {
    this.metrics.wsCloseReasons.push({ timestamp: Date.now(), code, reason });
    if (this.metrics.wsCloseReasons.length > 20) {
      this.metrics.wsCloseReasons = this.metrics.wsCloseReasons.slice(-20);
    }
  }

  recordPromptSize(chars: number) {
    this.metrics.promptSizeChars = chars;
  }

  recordError(source: string, message: string) {
    this.metrics.errors.push({ timestamp: Date.now(), source, message });
    if (this.metrics.errors.length > 50) {
      this.metrics.errors = this.metrics.errors.slice(-50);
    }
    this.emit();
  }

  getHealthSummary(): Record<string, unknown> {
    const uptime = this.metrics.sessionStartTime
      ? Math.round((Date.now() - this.metrics.sessionStartTime) / 1000)
      : 0;

    let totalToolCalls = 0;
    let totalToolFailures = 0;
    const toolStats: Record<string, { success: number; failure: number; avgMs: number }> = {};

    for (const [name, stats] of this.metrics.toolCalls.entries()) {
      totalToolCalls += stats.success + stats.failure;
      totalToolFailures += stats.failure;
      const total = stats.success + stats.failure;
      toolStats[name] = {
        success: stats.success,
        failure: stats.failure,
        avgMs: total > 0 ? Math.round(stats.totalMs / total) : 0,
      };
    }

    return {
      uptimeSeconds: uptime,
      reconnects: this.metrics.reconnectCount,
      reconnectLog: this.metrics.reconnectLog,
      voiceAnchorsApplied: this.metrics.voiceAnchorCount,
      totalToolCalls,
      totalToolFailures,
      toolStats,
      promptSizeChars: this.metrics.promptSizeChars,
      recentErrors: this.metrics.errors.slice(-10),
      wsCloseReasons: this.metrics.wsCloseReasons.slice(-5),
    };
  }

  reset() {
    this.metrics = this.freshMetrics();
  }

  private emit() {
    if (this.mainWindow && !this.mainWindow.isDestroyed()) {
      this.mainWindow.webContents.send('session-health:update', this.getHealthSummary());
    }
  }
}

export const sessionHealth = new SessionHealth();
