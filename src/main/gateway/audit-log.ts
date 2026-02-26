/**
 * gateway/audit-log.ts — Append-only audit log for all gateway messages.
 *
 * Every inbound and outbound message is logged as a JSONL line.
 * Files rotate monthly: audit-2026-02.jsonl, audit-2026-03.jsonl, etc.
 *
 * This is a security requirement — provides forensic evidence for
 * any trust-boundary violations or suspicious activity.
 */

import { app } from 'electron';
import fs from 'fs/promises';
import { createWriteStream, WriteStream } from 'fs';
import path from 'path';
import { AuditEntry } from './types';

class AuditLog {
  private gatewayDir = '';
  private currentMonth = '';
  private stream: WriteStream | null = null;

  async initialize(): Promise<void> {
    this.gatewayDir = path.join(app.getPath('userData'), 'gateway');
    await fs.mkdir(this.gatewayDir, { recursive: true });
    this.rotateIfNeeded();
    console.log('[AuditLog] Initialized');
  }

  /**
   * Append an audit entry. Fire-and-forget — never blocks the gateway.
   */
  log(entry: AuditEntry): void {
    try {
      this.rotateIfNeeded();
      // Truncate text to 500 chars for storage efficiency
      const safe: AuditEntry = {
        ...entry,
        text: entry.text.slice(0, 500),
      };
      const line = JSON.stringify(safe) + '\n';
      if (this.stream) {
        this.stream.write(line);
      }
    } catch (err) {
      console.warn('[AuditLog] Write failed:', err);
    }
  }

  /**
   * Log an inbound gateway message.
   */
  logInbound(
    channel: string,
    senderId: string,
    trust: string,
    text: string,
    msgId?: string
  ): void {
    this.log({
      ts: Date.now(),
      dir: 'in',
      channel,
      sender: senderId,
      trust: trust as AuditEntry['trust'],
      text,
      msgId,
    });
  }

  /**
   * Log an outbound gateway response.
   */
  logOutbound(
    channel: string,
    recipientId: string,
    text: string,
    toolCalls?: number,
    durationMs?: number
  ): void {
    this.log({
      ts: Date.now(),
      dir: 'out',
      channel,
      recipient: recipientId,
      text,
      toolCalls,
      durationMs,
    });
  }

  /**
   * Close the current stream.
   */
  close(): void {
    if (this.stream) {
      this.stream.end();
      this.stream = null;
    }
  }

  // ── Private ──────────────────────────────────────────────────────

  private rotateIfNeeded(): void {
    const now = new Date();
    const month = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;

    if (month !== this.currentMonth) {
      this.close();
      this.currentMonth = month;
      const filePath = path.join(this.gatewayDir, `audit-${month}.jsonl`);
      this.stream = createWriteStream(filePath, { flags: 'a' });
      this.stream.on('error', (err) => {
        console.warn('[AuditLog] Stream error:', err);
      });
    }
  }
}

export const auditLog = new AuditLog();
