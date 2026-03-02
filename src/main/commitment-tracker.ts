/**
 * commitment-tracker.ts — Track IV Phase 1: Temporal Reasoning.
 *
 * Extracts commitments (promises, deadlines, follow-ups) from conversation
 * and messages, tracks their lifecycle, detects unreplied communications,
 * and generates proactive nudges. The "chief of staff" temporal intelligence.
 *
 * cLaw Gate: All proactive outputs are SUGGESTIONS only. No message is ever
 * sent, no action is ever taken without explicit user approval. This module
 * generates context and recommendations — it never acts autonomously.
 */

import { app } from 'electron';
import fs from 'fs/promises';
import path from 'path';
import crypto from 'crypto';

// ── Data Model ──────────────────────────────────────────────────────

export type CommitmentStatus = 'active' | 'completed' | 'overdue' | 'cancelled' | 'snoozed';
export type CommitmentSource = 'conversation' | 'email' | 'message' | 'meeting' | 'calendar' | 'manual';
export type CommitmentDirection = 'user_promised' | 'other_promised' | 'mutual';
export type FollowUpUrgency = 'low' | 'medium' | 'high' | 'critical';

export interface Commitment {
  id: string;
  /** What was committed to */
  description: string;
  /** Who made the commitment */
  direction: CommitmentDirection;
  /** Person involved (other party) */
  personName: string;
  /** Where the commitment was detected */
  source: CommitmentSource;
  /** Current status */
  status: CommitmentStatus;
  /** When this was detected */
  createdAt: number;
  /** When this is due (null = no explicit deadline) */
  deadline: number | null;
  /** Optional domain context */
  domain: string;
  /** Context snippet where commitment was detected */
  contextSnippet: string;
  /** How confident we are this is a real commitment (0-1) */
  confidence: number;
  /** Whether the user has been reminded about this */
  reminded: boolean;
  /** When the user was last reminded */
  lastRemindedAt: number | null;
  /** When this was completed/cancelled */
  resolvedAt: number | null;
  /** Notes or resolution details */
  notes: string;
}

export interface OutboundMessage {
  id: string;
  /** Who it was sent to */
  recipient: string;
  /** Communication channel */
  channel: string;
  /** Brief summary of what was sent */
  summary: string;
  /** When it was sent */
  sentAt: number;
  /** Whether a reply has been received */
  replyReceived: boolean;
  /** When reply was received (null if not yet) */
  replyReceivedAt: number | null;
  /** Expected response time in hours (learned per contact/channel) */
  expectedResponseHours: number;
  /** Whether we've already suggested a follow-up */
  followUpSuggested: boolean;
}

export interface FollowUpSuggestion {
  id: string;
  /** Related outbound message or commitment */
  relatedId: string;
  type: 'unreplied_message' | 'approaching_deadline' | 'overdue_commitment' | 'check_in';
  /** Who to follow up with */
  personName: string;
  /** What to say (suggestion) */
  suggestedAction: string;
  /** How urgent */
  urgency: FollowUpUrgency;
  /** When this suggestion was generated */
  createdAt: number;
  /** Whether it's been shown to the user */
  delivered: boolean;
  /** Whether the user acted on it */
  actedOn: boolean;
}

export interface CommitmentTrackerConfig {
  /** Maximum commitments to track */
  maxCommitments: number;
  /** Maximum outbound messages to track */
  maxOutboundMessages: number;
  /** Days to retain resolved commitments */
  retentionDays: number;
  /** Default expected response time in hours */
  defaultResponseHours: number;
  /** Hours before deadline to start reminding */
  reminderLeadHours: number;
  /** Minimum confidence to create a commitment */
  minConfidence: number;
}

export interface CommitmentMention {
  description: string;
  personName: string;
  direction: CommitmentDirection;
  source: CommitmentSource;
  deadline: number | null;
  domain: string;
  confidence: number;
  contextSnippet: string;
}

export interface CommitmentTrackerStatus {
  activeCommitments: number;
  overdueCommitments: number;
  pendingFollowUps: number;
  trackedOutboundMessages: number;
  totalCommitmentsTracked: number;
}

// ── Default Config ──────────────────────────────────────────────────

const DEFAULT_CONFIG: CommitmentTrackerConfig = {
  maxCommitments: 200,
  maxOutboundMessages: 100,
  retentionDays: 90,
  defaultResponseHours: 48,
  reminderLeadHours: 24,
  minConfidence: 0.5,
};

// ── Response Time Baselines (per channel, in hours) ─────────────────

const CHANNEL_RESPONSE_BASELINES: Record<string, number> = {
  email: 48,
  slack: 4,
  teams: 4,
  telegram: 8,
  text: 2,
  discord: 12,
  whatsapp: 4,
  meeting: 168, // action items from meetings — a week
  default: 48,
};

// ── Core Engine ─────────────────────────────────────────────────────

class CommitmentTracker {
  private commitments: Commitment[] = [];
  private outboundMessages: OutboundMessage[] = [];
  private followUpSuggestions: FollowUpSuggestion[] = [];
  private filePath = '';
  private config: CommitmentTrackerConfig = { ...DEFAULT_CONFIG };
  private saveQueued = false;
  private initialized = false;

  // ── Initialization ──────────────────────────────────────────────

  async initialize(config?: Partial<CommitmentTrackerConfig>): Promise<void> {
    if (config) {
      this.config = { ...DEFAULT_CONFIG, ...config };
    }

    this.filePath = path.join(app.getPath('userData'), 'commitments.json');

    try {
      const data = await fs.readFile(this.filePath, 'utf-8');
      const parsed = JSON.parse(data);
      this.commitments = Array.isArray(parsed.commitments) ? parsed.commitments : [];
      this.outboundMessages = Array.isArray(parsed.outboundMessages) ? parsed.outboundMessages : [];
      this.followUpSuggestions = Array.isArray(parsed.followUpSuggestions) ? parsed.followUpSuggestions : [];
    } catch {
      this.commitments = [];
      this.outboundMessages = [];
      this.followUpSuggestions = [];
    }

    // Lifecycle maintenance on load
    this.updateOverdueStatus();
    this.pruneOld();
    this.initialized = true;

    console.log(
      `[CommitmentTracker] Initialized: ${this.commitments.filter(c => c.status === 'active').length} active, ` +
      `${this.commitments.filter(c => c.status === 'overdue').length} overdue, ` +
      `${this.outboundMessages.filter(m => !m.replyReceived).length} awaiting reply`
    );
  }

  // ── Commitment CRUD ─────────────────────────────────────────────

  /**
   * Add a commitment from extracted mention data.
   * Deduplicates by description similarity + person + time window.
   */
  addCommitment(mention: CommitmentMention): Commitment | null {
    if (mention.confidence < this.config.minConfidence) return null;

    // Dedup: skip if a very similar commitment exists within last hour
    const oneHourAgo = Date.now() - 60 * 60 * 1000;
    const isDupe = this.commitments.some(c =>
      c.personName.toLowerCase() === mention.personName.toLowerCase() &&
      c.createdAt > oneHourAgo &&
      this.textSimilarity(c.description, mention.description) > 0.7
    );
    if (isDupe) return null;

    const commitment: Commitment = {
      id: crypto.randomUUID().slice(0, 12),
      description: mention.description.slice(0, 500),
      direction: mention.direction,
      personName: mention.personName.slice(0, 100),
      source: mention.source,
      status: 'active',
      createdAt: Date.now(),
      deadline: mention.deadline,
      domain: mention.domain.slice(0, 50),
      contextSnippet: mention.contextSnippet.slice(0, 300),
      confidence: Math.max(0, Math.min(1, mention.confidence)),
      reminded: false,
      lastRemindedAt: null,
      resolvedAt: null,
      notes: '',
    };

    this.commitments.push(commitment);
    this.enforceLimit();
    this.queueSave();

    console.log(
      `[CommitmentTracker] New commitment: "${commitment.description.slice(0, 60)}..." ` +
      `(${commitment.direction}, confidence: ${commitment.confidence.toFixed(2)})`
    );

    return commitment;
  }

  /**
   * Process multiple commitment mentions from memory extraction.
   */
  processCommitmentMentions(mentions: CommitmentMention[]): Commitment[] {
    const added: Commitment[] = [];
    for (const mention of mentions) {
      const commitment = this.addCommitment(mention);
      if (commitment) added.push(commitment);
    }
    return added;
  }

  /**
   * Mark a commitment as completed.
   */
  completeCommitment(commitmentId: string, notes?: string): boolean {
    const c = this.commitments.find(x => x.id === commitmentId);
    if (!c || c.status === 'completed' || c.status === 'cancelled') return false;

    c.status = 'completed';
    c.resolvedAt = Date.now();
    if (notes) c.notes = notes;
    this.queueSave();
    return true;
  }

  /**
   * Cancel a commitment (user says it's no longer relevant).
   */
  cancelCommitment(commitmentId: string, reason?: string): boolean {
    const c = this.commitments.find(x => x.id === commitmentId);
    if (!c || c.status === 'completed' || c.status === 'cancelled') return false;

    c.status = 'cancelled';
    c.resolvedAt = Date.now();
    if (reason) c.notes = reason;
    this.queueSave();
    return true;
  }

  /**
   * Snooze a commitment (delay reminder).
   */
  snoozeCommitment(commitmentId: string, untilMs: number): boolean {
    const c = this.commitments.find(x => x.id === commitmentId);
    if (!c || c.status === 'completed' || c.status === 'cancelled') return false;

    c.status = 'snoozed';
    c.lastRemindedAt = untilMs; // Reuse as "don't remind until"
    this.queueSave();
    return true;
  }

  // ── Outbound Message Tracking ───────────────────────────────────

  /**
   * Track an outbound message for follow-up detection.
   */
  trackOutboundMessage(msg: {
    recipient: string;
    channel: string;
    summary: string;
  }): OutboundMessage {
    const baseline = CHANNEL_RESPONSE_BASELINES[msg.channel.toLowerCase()]
      ?? CHANNEL_RESPONSE_BASELINES.default;

    // Check for per-contact learned baseline
    const contactBaseline = this.getContactResponseBaseline(msg.recipient, msg.channel);
    const expectedHours = contactBaseline ?? baseline;

    const outbound: OutboundMessage = {
      id: crypto.randomUUID().slice(0, 12),
      recipient: msg.recipient.slice(0, 100),
      channel: msg.channel.slice(0, 30),
      summary: msg.summary.slice(0, 300),
      sentAt: Date.now(),
      replyReceived: false,
      replyReceivedAt: null,
      expectedResponseHours: expectedHours,
      followUpSuggested: false,
    };

    this.outboundMessages.push(outbound);

    // Cap outbound messages
    if (this.outboundMessages.length > this.config.maxOutboundMessages) {
      // Remove oldest replied messages first
      const replied = this.outboundMessages
        .filter(m => m.replyReceived)
        .sort((a, b) => a.sentAt - b.sentAt);
      if (replied.length > 0) {
        this.outboundMessages = this.outboundMessages.filter(m => m.id !== replied[0].id);
      } else {
        this.outboundMessages.shift();
      }
    }

    this.queueSave();
    return outbound;
  }

  /**
   * Record that a reply was received for a tracked outbound message.
   * Matches by recipient (fuzzy) and channel within reasonable time window.
   */
  recordReply(recipient: string, channel: string): boolean {
    const lowerRecip = recipient.toLowerCase();
    const lowerChan = channel.toLowerCase();

    // Find the most recent unreplied message to this recipient on this channel
    const msg = [...this.outboundMessages]
      .reverse()
      .find(m =>
        !m.replyReceived &&
        m.recipient.toLowerCase().includes(lowerRecip) &&
        m.channel.toLowerCase() === lowerChan
      );

    if (!msg) return false;

    msg.replyReceived = true;
    msg.replyReceivedAt = Date.now();
    this.queueSave();
    return true;
  }

  // ── Follow-Up Detection ─────────────────────────────────────────

  /**
   * Scan for items needing follow-up and generate suggestions.
   * Called periodically (e.g., every 5 minutes) or on-demand.
   * Returns NEW suggestions only.
   */
  generateFollowUpSuggestions(): FollowUpSuggestion[] {
    const now = Date.now();
    const newSuggestions: FollowUpSuggestion[] = [];

    // 1. Unreplied outbound messages past expected response time
    for (const msg of this.outboundMessages) {
      if (msg.replyReceived || msg.followUpSuggested) continue;

      const elapsedHours = (now - msg.sentAt) / (60 * 60 * 1000);
      if (elapsedHours >= msg.expectedResponseHours) {
        const urgency = this.computeFollowUpUrgency(elapsedHours, msg.expectedResponseHours);
        const daysSince = Math.round(elapsedHours / 24);

        const suggestion: FollowUpSuggestion = {
          id: crypto.randomUUID().slice(0, 12),
          relatedId: msg.id,
          type: 'unreplied_message',
          personName: msg.recipient,
          suggestedAction: `No reply from ${msg.recipient} on ${msg.channel} after ${daysSince > 0 ? daysSince + ' day(s)' : Math.round(elapsedHours) + ' hours'}. ` +
            `Original: "${msg.summary.slice(0, 80)}". Consider a gentle follow-up.`,
          urgency,
          createdAt: now,
          delivered: false,
          actedOn: false,
        };

        newSuggestions.push(suggestion);
        msg.followUpSuggested = true;
      }
    }

    // 2. Approaching deadlines
    for (const c of this.commitments) {
      if (c.status !== 'active' || !c.deadline || c.reminded) continue;

      const hoursUntilDeadline = (c.deadline - now) / (60 * 60 * 1000);
      if (hoursUntilDeadline <= this.config.reminderLeadHours && hoursUntilDeadline > 0) {
        const suggestion: FollowUpSuggestion = {
          id: crypto.randomUUID().slice(0, 12),
          relatedId: c.id,
          type: 'approaching_deadline',
          personName: c.personName,
          suggestedAction: this.formatDeadlineReminder(c, hoursUntilDeadline),
          urgency: hoursUntilDeadline <= 4 ? 'high' : 'medium',
          createdAt: now,
          delivered: false,
          actedOn: false,
        };

        newSuggestions.push(suggestion);
        c.reminded = true;
        c.lastRemindedAt = now;
      }
    }

    // 3. Overdue commitments (not yet reminded)
    for (const c of this.commitments) {
      if (c.status !== 'overdue') continue;
      // Only remind once per 24 hours for overdue items
      if (c.lastRemindedAt && (now - c.lastRemindedAt) < 24 * 60 * 60 * 1000) continue;

      const hoursOverdue = (now - (c.deadline || c.createdAt)) / (60 * 60 * 1000);
      const suggestion: FollowUpSuggestion = {
        id: crypto.randomUUID().slice(0, 12),
        relatedId: c.id,
        type: 'overdue_commitment',
        personName: c.personName,
        suggestedAction: this.formatOverdueReminder(c, hoursOverdue),
        urgency: hoursOverdue > 72 ? 'critical' : 'high',
        createdAt: now,
        delivered: false,
        actedOn: false,
      };

      newSuggestions.push(suggestion);
      c.lastRemindedAt = now;
    }

    if (newSuggestions.length > 0) {
      this.followUpSuggestions.push(...newSuggestions);
      // Cap follow-up suggestions at 100
      if (this.followUpSuggestions.length > 100) {
        this.followUpSuggestions = this.followUpSuggestions.slice(-100);
      }
      this.queueSave();
    }

    return newSuggestions;
  }

  /**
   * Mark a follow-up suggestion as delivered (shown to user).
   */
  markSuggestionDelivered(suggestionId: string): boolean {
    const s = this.followUpSuggestions.find(x => x.id === suggestionId);
    if (!s) return false;
    s.delivered = true;
    this.queueSave();
    return true;
  }

  /**
   * Mark a follow-up suggestion as acted on.
   */
  markSuggestionActedOn(suggestionId: string): boolean {
    const s = this.followUpSuggestions.find(x => x.id === suggestionId);
    if (!s) return false;
    s.actedOn = true;
    this.queueSave();
    return true;
  }

  // ── Queries ─────────────────────────────────────────────────────

  getActiveCommitments(): Commitment[] {
    return this.commitments.filter(c => c.status === 'active' || c.status === 'overdue');
  }

  getOverdueCommitments(): Commitment[] {
    return this.commitments.filter(c => c.status === 'overdue');
  }

  getCommitmentsByPerson(personName: string): Commitment[] {
    const lower = personName.toLowerCase();
    return this.commitments.filter(c =>
      c.personName.toLowerCase().includes(lower) &&
      (c.status === 'active' || c.status === 'overdue')
    );
  }

  getUpcomingDeadlines(withinHours: number = 72): Commitment[] {
    const now = Date.now();
    const cutoff = now + withinHours * 60 * 60 * 1000;
    return this.commitments
      .filter(c => c.status === 'active' && c.deadline && c.deadline <= cutoff && c.deadline > now)
      .sort((a, b) => (a.deadline || 0) - (b.deadline || 0));
  }

  getUnrepliedMessages(): OutboundMessage[] {
    return this.outboundMessages.filter(m => !m.replyReceived);
  }

  getPendingSuggestions(): FollowUpSuggestion[] {
    return this.followUpSuggestions.filter(s => !s.delivered);
  }

  getCommitmentById(id: string): Commitment | null {
    return this.commitments.find(c => c.id === id) || null;
  }

  getAllCommitments(): Commitment[] {
    return [...this.commitments];
  }

  getStatus(): CommitmentTrackerStatus {
    return {
      activeCommitments: this.commitments.filter(c => c.status === 'active').length,
      overdueCommitments: this.commitments.filter(c => c.status === 'overdue').length,
      pendingFollowUps: this.followUpSuggestions.filter(s => !s.delivered).length,
      trackedOutboundMessages: this.outboundMessages.filter(m => !m.replyReceived).length,
      totalCommitmentsTracked: this.commitments.length,
    };
  }

  getConfig(): CommitmentTrackerConfig {
    return { ...this.config };
  }

  // ── Context String Generation (for system prompt) ───────────────

  /**
   * Generate a context string for system prompt injection.
   * Compact representation of temporal awareness.
   */
  getContextString(): string {
    const active = this.getActiveCommitments();
    const upcoming = this.getUpcomingDeadlines(48);
    const overdue = this.getOverdueCommitments();
    const unreplied = this.getUnrepliedMessages();
    const pending = this.getPendingSuggestions();

    if (active.length === 0 && unreplied.length === 0) return '';

    const lines: string[] = [];

    // Overdue — highest priority
    if (overdue.length > 0) {
      lines.push('⚠ OVERDUE:');
      for (const c of overdue.slice(0, 5)) {
        const daysOverdue = Math.round((Date.now() - (c.deadline || c.createdAt)) / (24 * 60 * 60 * 1000));
        const who = c.direction === 'user_promised' ? 'You promised' : `${c.personName} promised`;
        lines.push(`  - ${who}: "${c.description.slice(0, 60)}" (${daysOverdue}d overdue)`);
      }
    }

    // Upcoming deadlines
    if (upcoming.length > 0) {
      lines.push('📅 UPCOMING:');
      for (const c of upcoming.slice(0, 5)) {
        const hoursUntil = Math.round((c.deadline! - Date.now()) / (60 * 60 * 1000));
        const who = c.direction === 'user_promised' ? 'You committed' : `${c.personName} committed`;
        const timeStr = hoursUntil < 24
          ? `${hoursUntil}h`
          : `${Math.round(hoursUntil / 24)}d`;
        lines.push(`  - ${who}: "${c.description.slice(0, 60)}" (due in ${timeStr})`);
      }
    }

    // Unreplied messages
    if (unreplied.length > 0) {
      const overdueReplies = unreplied.filter(m => {
        const elapsed = (Date.now() - m.sentAt) / (60 * 60 * 1000);
        return elapsed >= m.expectedResponseHours;
      });
      if (overdueReplies.length > 0) {
        lines.push('📬 AWAITING REPLY:');
        for (const m of overdueReplies.slice(0, 3)) {
          const daysSince = Math.round((Date.now() - m.sentAt) / (24 * 60 * 60 * 1000));
          lines.push(`  - ${m.recipient} (${m.channel}, ${daysSince}d): "${m.summary.slice(0, 50)}"`);
        }
      }
    }

    // Active commitments count (if not already shown above)
    const activeNotShown = active.filter(c =>
      c.status === 'active' && !overdue.includes(c) && !upcoming.includes(c)
    );
    if (activeNotShown.length > 0) {
      lines.push(`📋 ${activeNotShown.length} other active commitment(s) being tracked`);
    }

    // Pending suggestions
    if (pending.length > 0) {
      lines.push(`💡 ${pending.length} follow-up suggestion(s) ready`);
    }

    return lines.join('\n');
  }

  /**
   * Prompt-budget-aware compact context (shorter than getContextString).
   */
  getPromptContext(): string {
    const status = this.getStatus();
    if (status.activeCommitments === 0 && status.trackedOutboundMessages === 0) return '';

    const parts: string[] = [];

    if (status.overdueCommitments > 0) {
      parts.push(`${status.overdueCommitments} overdue`);
    }
    if (status.activeCommitments > 0) {
      parts.push(`${status.activeCommitments} active commitments`);
    }
    if (status.trackedOutboundMessages > 0) {
      parts.push(`${status.trackedOutboundMessages} awaiting reply`);
    }
    if (status.pendingFollowUps > 0) {
      parts.push(`${status.pendingFollowUps} follow-up suggestions`);
    }

    return `[Commitments: ${parts.join(' | ')}]`;
  }

  // ── Private Helpers ─────────────────────────────────────────────

  /**
   * Update overdue status for active commitments past deadline.
   */
  private updateOverdueStatus(): void {
    const now = Date.now();
    for (const c of this.commitments) {
      if (c.status === 'active' && c.deadline && c.deadline < now) {
        c.status = 'overdue';
      }
      // Un-snooze if snooze period has passed
      if (c.status === 'snoozed' && c.lastRemindedAt && c.lastRemindedAt < now) {
        c.status = c.deadline && c.deadline < now ? 'overdue' : 'active';
        c.reminded = false; // Allow re-reminder
      }
    }
  }

  /**
   * Remove old resolved commitments and delivered suggestions.
   */
  private pruneOld(): void {
    const cutoff = Date.now() - this.config.retentionDays * 24 * 60 * 60 * 1000;

    this.commitments = this.commitments.filter(c => {
      if (c.status === 'active' || c.status === 'overdue' || c.status === 'snoozed') return true;
      return (c.resolvedAt || c.createdAt) > cutoff;
    });

    // Prune old delivered suggestions
    const suggestionCutoff = Date.now() - 7 * 24 * 60 * 60 * 1000; // 7 days
    this.followUpSuggestions = this.followUpSuggestions.filter(s => {
      if (!s.delivered) return true;
      return s.createdAt > suggestionCutoff;
    });

    // Prune old replied outbound messages
    const msgCutoff = Date.now() - 30 * 24 * 60 * 60 * 1000; // 30 days
    this.outboundMessages = this.outboundMessages.filter(m => {
      if (!m.replyReceived) return true;
      return m.sentAt > msgCutoff;
    });
  }

  /**
   * Enforce maximum commitment count.
   */
  private enforceLimit(): void {
    if (this.commitments.length <= this.config.maxCommitments) return;

    // Remove oldest resolved first
    const resolved = this.commitments
      .filter(c => c.status === 'completed' || c.status === 'cancelled')
      .sort((a, b) => (a.resolvedAt || a.createdAt) - (b.resolvedAt || b.createdAt));

    while (this.commitments.length > this.config.maxCommitments && resolved.length > 0) {
      const toRemove = resolved.shift()!;
      this.commitments = this.commitments.filter(c => c.id !== toRemove.id);
    }
  }

  /**
   * Simple text similarity (word overlap / Jaccard).
   */
  private textSimilarity(a: string, b: string): number {
    const wordsA = new Set(a.toLowerCase().split(/\s+/).filter(w => w.length > 2));
    const wordsB = new Set(b.toLowerCase().split(/\s+/).filter(w => w.length > 2));
    if (wordsA.size === 0 && wordsB.size === 0) return 1;
    if (wordsA.size === 0 || wordsB.size === 0) return 0;

    let intersection = 0;
    for (const w of wordsA) {
      if (wordsB.has(w)) intersection++;
    }

    return intersection / (wordsA.size + wordsB.size - intersection);
  }

  /**
   * Compute follow-up urgency based on how overdue the response is.
   */
  private computeFollowUpUrgency(elapsedHours: number, expectedHours: number): FollowUpUrgency {
    const ratio = elapsedHours / expectedHours;
    if (ratio >= 4) return 'critical';
    if (ratio >= 2.5) return 'high';
    if (ratio >= 1.5) return 'medium';
    return 'low';
  }

  /**
   * Learn per-contact response baseline from historical data.
   */
  private getContactResponseBaseline(recipient: string, channel: string): number | null {
    const lower = recipient.toLowerCase();
    const lowerChan = channel.toLowerCase();
    const replied = this.outboundMessages.filter(m =>
      m.replyReceived &&
      m.replyReceivedAt &&
      m.recipient.toLowerCase().includes(lower) &&
      m.channel.toLowerCase() === lowerChan
    );

    if (replied.length < 3) return null; // Not enough data

    const responseTimes = replied.map(m =>
      (m.replyReceivedAt! - m.sentAt) / (60 * 60 * 1000)
    );

    // Use 80th percentile as expected response time
    responseTimes.sort((a, b) => a - b);
    const p80Index = Math.floor(responseTimes.length * 0.8);
    return Math.round(responseTimes[p80Index]);
  }

  /**
   * Format a deadline reminder message.
   */
  private formatDeadlineReminder(c: Commitment, hoursUntil: number): string {
    const timeStr = hoursUntil < 1
      ? 'less than an hour'
      : hoursUntil < 24
        ? `${Math.round(hoursUntil)} hour(s)`
        : `${Math.round(hoursUntil / 24)} day(s)`;

    if (c.direction === 'user_promised') {
      return `Reminder: You committed to "${c.description.slice(0, 80)}" ` +
        `${c.personName ? `(with ${c.personName}) ` : ''}— due in ${timeStr}.`;
    } else {
      return `Reminder: ${c.personName} committed to "${c.description.slice(0, 80)}" — due in ${timeStr}. ` +
        `Consider checking in if it hasn't been delivered.`;
    }
  }

  /**
   * Format an overdue commitment reminder.
   */
  private formatOverdueReminder(c: Commitment, hoursOverdue: number): string {
    const timeStr = hoursOverdue < 24
      ? `${Math.round(hoursOverdue)} hour(s)`
      : `${Math.round(hoursOverdue / 24)} day(s)`;

    if (c.direction === 'user_promised') {
      return `Overdue: You promised "${c.description.slice(0, 80)}" ` +
        `${c.personName ? `(to ${c.personName}) ` : ''}— ${timeStr} overdue. ` +
        `Should I help you follow through or reschedule?`;
    } else {
      return `Overdue: ${c.personName} promised "${c.description.slice(0, 80)}" — ${timeStr} overdue. ` +
        `Would you like to follow up?`;
    }
  }

  // ── Persistence ─────────────────────────────────────────────────

  private queueSave(): void {
    if (this.saveQueued) return;
    this.saveQueued = true;
    // Debounce writes — save after 2 seconds of quiet
    setTimeout(() => {
      this.saveQueued = false;
      this.save().catch(err => {
        // Crypto Sprint 17: Sanitize error output.
        console.warn('[CommitmentTracker] Save failed:', err instanceof Error ? err.message : 'Unknown error');
      });
    }, 2000);
  }

  private async save(): Promise<void> {
    if (!this.filePath) return;
    const data = {
      commitments: this.commitments,
      outboundMessages: this.outboundMessages,
      followUpSuggestions: this.followUpSuggestions,
    };
    await fs.writeFile(this.filePath, JSON.stringify(data, null, 2), 'utf-8');
  }
}

export const commitmentTracker = new CommitmentTracker();
