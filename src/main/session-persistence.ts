/**
 * session-persistence.ts — JSONL DAG session persistence for Agent Friday.
 *
 * Inspired by Pi coding agent's session format: append-only JSONL with
 * id/parentId forming a DAG for conversation branching. Full history is
 * always preserved; only in-memory context is compacted.
 *
 * Features:
 *   - Append-only JSONL (crash-safe — partial writes lose only one entry)
 *   - DAG structure with id/parentId for conversation branching
 *   - Auto-compaction when context approaches model's context window
 *   - LLM-generated structured summaries for compacted portions
 *   - Session replay from disk (load full conversation from JSONL)
 *   - Cost tracking per entry (integrates with cost-tracker.ts)
 *
 * Storage: ${userData}/sessions/<session-id>.jsonl
 */

import { app } from 'electron';
import * as fs from 'fs/promises';
import * as path from 'path';
import crypto from 'crypto';
import { llmClient, type ChatMessage } from './llm-client';
import { encode } from 'gpt-tokenizer';

// ── Types ─────────────────────────────────────────────────────────────

/** Every JSONL entry has these fields */
interface BaseEntry {
  /** 8-char hex ID */
  id: string;
  /** Parent entry ID (null for root) */
  parentId: string | null;
  /** Entry type discriminator */
  type: string;
  /** When this entry was created */
  timestamp: number;
}

/** Session header — always the first entry (id=root, parentId=null) */
export interface SessionHeader extends BaseEntry {
  type: 'header';
  version: 3;
  cwd?: string;
  model?: string;
  provider?: string;
  parentSession?: string;
}

/** A conversation message (user, assistant, or tool result) */
export interface SessionMessageEntry extends BaseEntry {
  type: 'message';
  message: ChatMessage;
  /** Token count for this message (for budget tracking) */
  tokens?: number;
  /** Model that generated this (for assistant messages) */
  model?: string;
  /** Provider that served this */
  provider?: string;
  /** Cost in USD for this turn */
  costUsd?: number;
}

/** Compaction checkpoint — summarizes older messages */
export interface CompactionEntry extends BaseEntry {
  type: 'compaction';
  /** Structured summary of compacted content */
  summary: CompactionSummary;
  /** ID of the first entry that was kept (everything before this was summarized) */
  firstKeptEntryId: string;
  /** How many entries were compacted */
  compactedCount: number;
  /** Total tokens that were compacted */
  compactedTokens: number;
}

/** Model change event */
export interface ModelChangeEntry extends BaseEntry {
  type: 'model_change';
  fromModel: string;
  toModel: string;
  reason?: string;
}

/** Branch summary — created when navigating away from a branch */
export interface BranchSummaryEntry extends BaseEntry {
  type: 'branch_summary';
  summary: string;
  branchEntryCount: number;
}

/** User bookmark / label */
export interface LabelEntry extends BaseEntry {
  type: 'label';
  label: string;
  targetEntryId: string;
}

export type SessionEntry =
  | SessionHeader
  | SessionMessageEntry
  | CompactionEntry
  | ModelChangeEntry
  | BranchSummaryEntry
  | LabelEntry;

/** Structured summary for compacted portions */
export interface CompactionSummary {
  goal: string;
  constraints: string[];
  progress: string[];
  keyDecisions: string[];
  nextSteps: string[];
  criticalContext: string;
  filesModified: string[];
}

// ── Session Manager ───────────────────────────────────────────────────

export class SessionManager {
  private sessionsDir = '';
  private currentSessionId: string | null = null;
  private entries: SessionEntry[] = [];
  private currentBranchHead: string | null = null;
  private writeStream: fs.FileHandle | null = null;

  // --- TUNABLE ---
  /** Reserve tokens for model response (don't fill context completely) */
  private readonly RESERVE_TOKENS = 16384;
  /** Minimum recent tokens to keep uncompacted */
  private readonly KEEP_RECENT_TOKENS = 20000;
  /** Maximum token count for compaction summary */
  private readonly SUMMARY_MAX_TOKENS = 2000;
  // --- /TUNABLE ---

  async initialize(): Promise<void> {
    this.sessionsDir = path.join(app.getPath('userData'), 'sessions');
    await fs.mkdir(this.sessionsDir, { recursive: true });
  }

  /** Start a new session */
  async startSession(options?: {
    cwd?: string;
    model?: string;
    provider?: string;
    parentSession?: string;
  }): Promise<string> {
    const sessionId = crypto.randomUUID();
    this.currentSessionId = sessionId;
    this.entries = [];

    const header: SessionHeader = {
      id: this.genId(),
      parentId: null,
      type: 'header',
      version: 3,
      timestamp: Date.now(),
      cwd: options?.cwd,
      model: options?.model,
      provider: options?.provider,
      parentSession: options?.parentSession,
    };

    this.entries.push(header);
    this.currentBranchHead = header.id;

    // Open file for appending
    const filePath = this.getSessionPath(sessionId);
    this.writeStream = await fs.open(filePath, 'a');
    await this.appendToFile(header);

    return sessionId;
  }

  /** Load an existing session from disk */
  async loadSession(sessionId: string): Promise<SessionEntry[]> {
    const filePath = this.getSessionPath(sessionId);
    const content = await fs.readFile(filePath, 'utf-8');
    const lines = content.split('\n').filter(l => l.trim());

    this.entries = [];
    for (const line of lines) {
      try {
        const entry = JSON.parse(line) as SessionEntry;
        this.entries.push(entry);
      } catch {
        console.warn('[SessionPersistence] Skipping malformed JSONL line');
      }
    }

    this.currentSessionId = sessionId;
    // Set branch head to the last entry
    if (this.entries.length > 0) {
      this.currentBranchHead = this.entries[this.entries.length - 1].id;
    }

    // Reopen file for appending
    this.writeStream = await fs.open(filePath, 'a');

    return this.entries;
  }

  /** Append a message to the current session */
  async appendMessage(
    message: ChatMessage,
    metadata?: {
      model?: string;
      provider?: string;
      costUsd?: number;
    }
  ): Promise<SessionMessageEntry> {
    const tokens = this.estimateTokens(message);

    const entry: SessionMessageEntry = {
      id: this.genId(),
      parentId: this.currentBranchHead,
      type: 'message',
      timestamp: Date.now(),
      message,
      tokens,
      model: metadata?.model,
      provider: metadata?.provider,
      costUsd: metadata?.costUsd,
    };

    this.entries.push(entry);
    this.currentBranchHead = entry.id;
    await this.appendToFile(entry);

    return entry;
  }

  /** Record a model change */
  async recordModelChange(fromModel: string, toModel: string, reason?: string): Promise<void> {
    const entry: ModelChangeEntry = {
      id: this.genId(),
      parentId: this.currentBranchHead,
      type: 'model_change',
      timestamp: Date.now(),
      fromModel,
      toModel,
      reason,
    };

    this.entries.push(entry);
    this.currentBranchHead = entry.id;
    await this.appendToFile(entry);
  }

  /** Add a user bookmark / label */
  async addLabel(label: string, targetEntryId?: string): Promise<void> {
    const entry: LabelEntry = {
      id: this.genId(),
      parentId: this.currentBranchHead,
      type: 'label',
      timestamp: Date.now(),
      label,
      targetEntryId: targetEntryId ?? this.currentBranchHead ?? '',
    };

    this.entries.push(entry);
    await this.appendToFile(entry);
  }

  /**
   * Build the in-memory context for LLM consumption.
   *
   * Walks the DAG from root to current branch head, assembling messages.
   * If a compaction entry exists, uses the summary + messages after it.
   * Returns ChatMessage[] ready to send to llmClient.
   */
  buildContext(): ChatMessage[] {
    const path = this.getPathToHead();
    const messages: ChatMessage[] = [];

    // Find the latest compaction entry in the path
    let startIdx = 0;
    for (let i = path.length - 1; i >= 0; i--) {
      if (path[i].type === 'compaction') {
        const compaction = path[i] as CompactionEntry;
        // Inject the compaction summary as a system-like user message
        messages.push({
          role: 'user',
          content: this.formatCompactionSummary(compaction.summary),
        });
        messages.push({
          role: 'assistant',
          content: 'Understood. I have the context from our previous conversation. Let me continue from where we left off.',
        });
        startIdx = i + 1;
        break;
      }
    }

    // Add messages from startIdx onward
    for (let i = startIdx; i < path.length; i++) {
      const entry = path[i];
      if (entry.type === 'message') {
        messages.push((entry as SessionMessageEntry).message);
      } else if (entry.type === 'branch_summary') {
        // Inject branch context
        messages.push({
          role: 'user',
          content: `[Context from a previous conversation branch: ${(entry as BranchSummaryEntry).summary}]`,
        });
      }
    }

    return messages;
  }

  /**
   * Check if compaction is needed and perform it if so.
   *
   * Triggers when estimated context tokens > contextWindow - RESERVE_TOKENS.
   * Walks backward from newest, keeping KEEP_RECENT_TOKENS, then asks
   * the LLM to summarize everything before that cut point.
   */
  async compactIfNeeded(contextWindow: number): Promise<boolean> {
    const contextMessages = this.buildContext();
    const totalTokens = contextMessages.reduce(
      (sum, m) => sum + this.estimateTokens(m), 0
    );

    if (totalTokens <= contextWindow - this.RESERVE_TOKENS) {
      return false; // No compaction needed
    }

    console.log(
      `[SessionPersistence] Compaction triggered: ${totalTokens} tokens ` +
      `> ${contextWindow - this.RESERVE_TOKENS} threshold`
    );

    // Walk backward to find the cut point
    const path = this.getPathToHead();
    const messageEntries = path.filter(
      (e): e is SessionMessageEntry => e.type === 'message'
    );

    let keptTokens = 0;
    let cutIdx = messageEntries.length;
    for (let i = messageEntries.length - 1; i >= 0; i--) {
      keptTokens += messageEntries[i].tokens ?? this.estimateTokens(messageEntries[i].message);
      if (keptTokens >= this.KEEP_RECENT_TOKENS) {
        cutIdx = i;
        break;
      }
    }

    if (cutIdx <= 1) {
      // Not enough to compact
      return false;
    }

    // Build the content to summarize
    const toSummarize = messageEntries.slice(0, cutIdx);
    const summaryText = toSummarize
      .map(e => {
        const content = typeof e.message.content === 'string'
          ? e.message.content
          : JSON.stringify(e.message.content);
        // Truncate tool results for summary
        const truncated = content.length > 2000
          ? content.slice(0, 2000) + '...'
          : content;
        return `[${e.message.role}]: ${truncated}`;
      })
      .join('\n\n');

    // Ask LLM to generate structured summary
    let summary: CompactionSummary;
    try {
      const summaryResponse = await llmClient.text(
        `Summarize this conversation excerpt into a structured format. ` +
        `Return ONLY valid JSON with these fields:\n` +
        `{"goal":"main objective","constraints":["list"],"progress":["what was done"],` +
        `"keyDecisions":["important choices"],"nextSteps":["what comes next"],` +
        `"criticalContext":"anything essential to continue","filesModified":["file paths"]}\n\n` +
        `Conversation:\n${summaryText}`,
        { maxTokens: this.SUMMARY_MAX_TOKENS }
      );

      try {
        summary = JSON.parse(summaryResponse);
      } catch {
        summary = {
          goal: 'Conversation context',
          constraints: [],
          progress: [summaryResponse.slice(0, 500)],
          keyDecisions: [],
          nextSteps: [],
          criticalContext: summaryResponse.slice(0, 1000),
          filesModified: [],
        };
      }
    } catch (err) {
      console.warn('[SessionPersistence] Compaction summary failed:', err);
      // Fallback: simple truncation summary
      summary = {
        goal: 'Previous conversation context',
        constraints: [],
        progress: [`${toSummarize.length} messages exchanged`],
        keyDecisions: [],
        nextSteps: [],
        criticalContext: toSummarize.slice(-3).map(e =>
          typeof e.message.content === 'string'
            ? e.message.content.slice(0, 200)
            : ''
        ).join(' | '),
        filesModified: [],
      };
    }

    // Calculate compacted stats
    const compactedTokens = toSummarize.reduce(
      (sum, e) => sum + (e.tokens ?? this.estimateTokens(e.message)), 0
    );

    const firstKept = messageEntries[cutIdx];

    // Append compaction entry
    const compactionEntry: CompactionEntry = {
      id: this.genId(),
      parentId: this.currentBranchHead,
      type: 'compaction',
      timestamp: Date.now(),
      summary,
      firstKeptEntryId: firstKept.id,
      compactedCount: toSummarize.length,
      compactedTokens,
    };

    this.entries.push(compactionEntry);
    this.currentBranchHead = compactionEntry.id;
    await this.appendToFile(compactionEntry);

    console.log(
      `[SessionPersistence] Compacted ${toSummarize.length} entries ` +
      `(${compactedTokens} tokens) into summary`
    );

    return true;
  }

  /** List all session files */
  async listSessions(): Promise<Array<{ id: string; timestamp: number; size: number }>> {
    try {
      const files = await fs.readdir(this.sessionsDir);
      const sessions = [];

      for (const file of files) {
        if (!file.endsWith('.jsonl')) continue;
        const id = file.replace('.jsonl', '');
        const stat = await fs.stat(path.join(this.sessionsDir, file));
        sessions.push({
          id,
          timestamp: stat.mtimeMs,
          size: stat.size,
        });
      }

      return sessions.sort((a, b) => b.timestamp - a.timestamp);
    } catch {
      return [];
    }
  }

  /** Get session stats */
  getStats(): {
    entryCount: number;
    messageCount: number;
    compactionCount: number;
    estimatedTokens: number;
  } {
    const messageCount = this.entries.filter(e => e.type === 'message').length;
    const compactionCount = this.entries.filter(e => e.type === 'compaction').length;
    const estimatedTokens = this.entries
      .filter((e): e is SessionMessageEntry => e.type === 'message')
      .reduce((sum, e) => sum + (e.tokens ?? 0), 0);

    return {
      entryCount: this.entries.length,
      messageCount,
      compactionCount,
      estimatedTokens,
    };
  }

  /** Close the current session (flush + close file handle) */
  async close(): Promise<void> {
    if (this.writeStream) {
      await this.writeStream.close();
      this.writeStream = null;
    }
    this.currentSessionId = null;
    this.entries = [];
    this.currentBranchHead = null;
  }

  /** Get the current session ID */
  getCurrentSessionId(): string | null {
    return this.currentSessionId;
  }

  // ── Private ─────────────────────────────────────────────────────────

  /** Generate an 8-char hex entry ID */
  private genId(): string {
    return crypto.randomBytes(4).toString('hex');
  }

  /** Get the file path for a session */
  private getSessionPath(sessionId: string): string {
    return path.join(this.sessionsDir, `${sessionId}.jsonl`);
  }

  /** Append an entry to the JSONL file */
  private async appendToFile(entry: SessionEntry): Promise<void> {
    if (!this.writeStream) return;
    const line = JSON.stringify(entry) + '\n';
    await this.writeStream.write(line);
  }

  /**
   * Walk the DAG from root to current branch head.
   * Returns entries in chronological order along the current branch.
   */
  private getPathToHead(): SessionEntry[] {
    if (!this.currentBranchHead) return [];

    // Build a lookup map
    const byId = new Map<string, SessionEntry>();
    for (const entry of this.entries) {
      byId.set(entry.id, entry);
    }

    // Walk backward from head to root
    const reversePath: SessionEntry[] = [];
    let current = byId.get(this.currentBranchHead);
    while (current) {
      reversePath.push(current);
      current = current.parentId ? byId.get(current.parentId) : undefined;
    }

    return reversePath.reverse();
  }

  /** Format a compaction summary as a context injection message */
  private formatCompactionSummary(summary: CompactionSummary): string {
    const parts = [
      `[Previous Conversation Summary]`,
      `Goal: ${summary.goal}`,
    ];

    if (summary.constraints.length > 0) {
      parts.push(`Constraints: ${summary.constraints.join('; ')}`);
    }
    if (summary.progress.length > 0) {
      parts.push(`Progress: ${summary.progress.join('; ')}`);
    }
    if (summary.keyDecisions.length > 0) {
      parts.push(`Key Decisions: ${summary.keyDecisions.join('; ')}`);
    }
    if (summary.nextSteps.length > 0) {
      parts.push(`Next Steps: ${summary.nextSteps.join('; ')}`);
    }
    if (summary.criticalContext) {
      parts.push(`Critical Context: ${summary.criticalContext}`);
    }
    if (summary.filesModified.length > 0) {
      parts.push(`Files Modified: ${summary.filesModified.join(', ')}`);
    }

    return parts.join('\n');
  }

  /** Estimate token count for a message */
  private estimateTokens(message: ChatMessage): number {
    const content = typeof message.content === 'string'
      ? message.content
      : JSON.stringify(message.content);

    try {
      return encode(content || '').length;
    } catch {
      // Fallback: rough estimate
      return Math.ceil((content || '').length / 4);
    }
  }
}

/** Singleton session manager */
export const sessionManager = new SessionManager();
