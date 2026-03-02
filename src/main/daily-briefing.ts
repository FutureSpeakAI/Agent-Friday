/**
 * daily-briefing.ts — Track IV Phase 2: Daily Briefing System.
 *
 * Compiles morning briefings, mid-day refreshes, and end-of-day summaries
 * from calendar events, commitments, context graph activity, and pending
 * follow-ups.  Briefings are delivered through a configurable channel
 * cascade (dashboard → telegram → email).
 *
 * cLaw Gate: Briefings are READ-ONLY informational outputs. No message
 * is ever sent, no commitment is ever resolved, and no action is ever
 * taken without explicit user approval.  This module generates context —
 * it never acts autonomously.
 */

import { app } from 'electron';
import fs from 'fs/promises';
import path from 'path';
import crypto from 'crypto';

// ── Data Model ──────────────────────────────────────────────────────

export type BriefingType = 'morning' | 'midday' | 'evening';
export type BriefingChannel = 'dashboard' | 'telegram' | 'voice' | 'email';
export type SectionPriority = 'critical' | 'high' | 'normal' | 'low';
export type BriefingLength = 'short' | 'medium' | 'long';
export type DeliveryStatus = 'success' | 'failed' | 'skipped' | 'pending';

export interface BriefingSection {
  title: string;
  content: string;
  priority: SectionPriority;
  source: 'calendar' | 'commitments' | 'pending' | 'priorities' | 'workstream' | 'world' | 'eod_summary';
  metadata?: {
    itemCount?: number;
    timeSpan?: string;
    actionable?: boolean;
  };
}

export interface DeliveryAttempt {
  channel: BriefingChannel;
  attemptedAt: number;
  status: DeliveryStatus;
  reason?: string;
}

export interface DailyBriefing {
  id: string;
  generatedAt: number;
  deliveredAt: number | null;
  scheduledFor: number;
  type: BriefingType;

  /** 1–2 sentence executive summary */
  summary: string;
  sections: BriefingSection[];

  metadata: {
    calendarEventCount: number;
    commitmentCount: number;
    pendingItemCount: number;
    overdueCount: number;
    wordCount: number;
    estimatedReadTimeSec: number;
  };

  deliveryAttempts: DeliveryAttempt[];
}

export interface DailyBriefingConfig {
  /** Whether the briefing system is enabled */
  enabled: boolean;
  /** Morning briefing time in HH:MM (24-hour) */
  morningTime: string;
  /** Optional evening summary time in HH:MM */
  eveningTime: string | null;
  /** Primary delivery channel */
  primaryChannel: BriefingChannel;
  /** Fallback channels in order */
  fallbackChannels: BriefingChannel[];
  /** Stale threshold (ms) — if brief is older than this, regenerate */
  staleThresholdMs: number;
  /** Include world/market context if available */
  includeWorldEvents: boolean;
  /** Maximum sections in a single briefing */
  maxSections: number;
  /** Maximum days to retain briefing history */
  retentionDays: number;
  /** Maximum stored briefings */
  maxBriefings: number;
}

/** Raw context data collected from other subsystems */
export interface BriefingSourceData {
  calendarEvents?: CalendarEvent[];
  activeCommitments?: CommitmentSnapshot[];
  overdueCommitments?: CommitmentSnapshot[];
  upcomingDeadlines?: CommitmentSnapshot[];
  unrepliedMessages?: UnrepliedSnapshot[];
  followUpSuggestions?: FollowUpSnapshot[];
  recentActivity?: ActivitySnapshot[];
  /** End-of-day: what was accomplished */
  sessionSummary?: string;
}

export interface CalendarEvent {
  title: string;
  startTime: number;
  endTime: number;
  attendees?: string[];
  location?: string;
}

export interface CommitmentSnapshot {
  id: string;
  description: string;
  personName: string;
  direction: string;
  deadline: number | null;
  status: string;
}

export interface UnrepliedSnapshot {
  recipient: string;
  channel: string;
  summary: string;
  sentAt: number;
  expectedReplyByMs: number;
}

export interface FollowUpSnapshot {
  personName: string;
  type: string;
  reason: string;
  urgency: string;
}

export interface ActivitySnapshot {
  timestamp: number;
  summary: string;
  type: string;
}

export interface DailyBriefingStatus {
  initialized: boolean;
  enabled: boolean;
  totalBriefings: number;
  lastBriefingAt: number | null;
  lastBriefingType: BriefingType | null;
  nextScheduledAt: number | null;
  morningTime: string;
  eveningTime: string | null;
}

// ── Defaults ────────────────────────────────────────────────────────

const DEFAULT_CONFIG: DailyBriefingConfig = {
  enabled: true,
  morningTime: '08:00',
  eveningTime: '17:30',
  primaryChannel: 'dashboard',
  fallbackChannels: ['telegram'],
  staleThresholdMs: 4 * 60 * 60 * 1000, // 4 hours
  includeWorldEvents: false,
  maxSections: 8,
  retentionDays: 30,
  maxBriefings: 200,
};

// ── Core Engine ─────────────────────────────────────────────────────

class DailyBriefingEngine {
  private briefings: DailyBriefing[] = [];
  private filePath = '';
  private config: DailyBriefingConfig = { ...DEFAULT_CONFIG };
  private saveQueued = false;
  private initialized = false;

  // ── Initialization ──────────────────────────────────────────────

  async initialize(config?: Partial<DailyBriefingConfig>): Promise<void> {
    this.config = { ...DEFAULT_CONFIG, ...config };
    this.filePath = path.join(app.getPath('userData'), 'daily-briefings.json');

    try {
      const raw = await fs.readFile(this.filePath, 'utf-8');
      const data = JSON.parse(raw);
      this.briefings = Array.isArray(data.briefings) ? data.briefings : [];
    } catch {
      this.briefings = [];
    }

    // Prune old briefings
    this.prune();
    this.initialized = true;

    const recent = this.briefings.filter(
      b => b.generatedAt > Date.now() - 24 * 60 * 60 * 1000
    );
    console.log(
      `[DailyBriefing] Initialized: ${this.briefings.length} stored, ` +
      `${recent.length} from last 24h`
    );
  }

  // ── Briefing Generation ─────────────────────────────────────────

  /**
   * Compile a daily briefing from all available sources.
   *
   * cLaw Gate: This method is a pure read — it gathers context from
   * other subsystems and formats it into a structured briefing.
   * It never modifies any state in upstream systems.
   */
  generateBriefing(
    type: BriefingType,
    sourceData: BriefingSourceData
  ): DailyBriefing {
    const now = Date.now();
    const sections: BriefingSection[] = [];

    // ── Calendar Section ────────────────────────────────────────
    if (sourceData.calendarEvents && sourceData.calendarEvents.length > 0) {
      const events = sourceData.calendarEvents;
      const lines = events.map(e => {
        const time = new Date(e.startTime).toLocaleTimeString('en-US', {
          hour: 'numeric',
          minute: '2-digit',
        });
        const attendeeStr = e.attendees?.length
          ? ` (with ${e.attendees.slice(0, 3).join(', ')}${e.attendees.length > 3 ? ` +${e.attendees.length - 3}` : ''})`
          : '';
        const locationStr = e.location ? ` @ ${e.location}` : '';
        return `- ${time}: ${e.title}${attendeeStr}${locationStr}`;
      });
      sections.push({
        title: "Today's Schedule",
        content: lines.join('\n'),
        priority: 'critical',
        source: 'calendar',
        metadata: { itemCount: events.length, timeSpan: 'today' },
      });
    }

    // ── Overdue Commitments ─────────────────────────────────────
    if (sourceData.overdueCommitments && sourceData.overdueCommitments.length > 0) {
      const items = sourceData.overdueCommitments;
      const lines = items.map(c => {
        const direction = c.direction === 'user_promised' ? 'You promised' : `${c.personName} promised`;
        const deadlineStr = c.deadline
          ? ` (due ${new Date(c.deadline).toLocaleDateString()})`
          : '';
        return `- [OVERDUE] ${direction}: ${c.description.slice(0, 80)}${deadlineStr}`;
      });
      sections.push({
        title: 'Overdue Items',
        content: lines.join('\n'),
        priority: 'critical',
        source: 'commitments',
        metadata: { itemCount: items.length, actionable: true },
      });
    }

    // ── Upcoming Deadlines ──────────────────────────────────────
    if (sourceData.upcomingDeadlines && sourceData.upcomingDeadlines.length > 0) {
      const items = sourceData.upcomingDeadlines;
      const lines = items.map(c => {
        const direction = c.direction === 'user_promised' ? 'You committed' : `${c.personName} committed`;
        const deadlineStr = c.deadline
          ? ` (due ${new Date(c.deadline).toLocaleDateString()})`
          : '';
        return `- ${direction}: ${c.description.slice(0, 80)}${deadlineStr}`;
      });
      sections.push({
        title: 'Upcoming Deadlines',
        content: lines.join('\n'),
        priority: 'high',
        source: 'commitments',
        metadata: { itemCount: items.length, timeSpan: '72h' },
      });
    }

    // ── Unreplied Messages ──────────────────────────────────────
    if (sourceData.unrepliedMessages && sourceData.unrepliedMessages.length > 0) {
      const items = sourceData.unrepliedMessages;
      const lines = items.map(m => {
        const daysSince = Math.round((now - m.sentAt) / (24 * 60 * 60 * 1000));
        return `- ${m.recipient} via ${m.channel}: "${m.summary.slice(0, 60)}" (${daysSince}d ago)`;
      });
      sections.push({
        title: 'Awaiting Replies',
        content: lines.join('\n'),
        priority: 'high',
        source: 'pending',
        metadata: { itemCount: items.length, actionable: true },
      });
    }

    // ── Follow-Up Suggestions ───────────────────────────────────
    if (sourceData.followUpSuggestions && sourceData.followUpSuggestions.length > 0) {
      const items = sourceData.followUpSuggestions.slice(0, 5);
      const lines = items.map(s => {
        const urgencyTag = s.urgency === 'critical' || s.urgency === 'high'
          ? ` [${s.urgency.toUpperCase()}]`
          : '';
        return `- ${s.personName}${urgencyTag}: ${s.reason.slice(0, 80)}`;
      });
      sections.push({
        title: 'Suggested Follow-Ups',
        content: lines.join('\n'),
        priority: 'normal',
        source: 'pending',
        metadata: { itemCount: items.length, actionable: true },
      });
    }

    // ── Active Commitments Summary ──────────────────────────────
    if (sourceData.activeCommitments && sourceData.activeCommitments.length > 0) {
      const items = sourceData.activeCommitments.slice(0, 8);
      const lines = items.map(c => {
        const arrow = c.direction === 'user_promised' ? '→' : '←';
        return `- ${arrow} ${c.personName}: ${c.description.slice(0, 70)}`;
      });
      sections.push({
        title: 'Active Commitments',
        content: lines.join('\n'),
        priority: 'normal',
        source: 'commitments',
        metadata: { itemCount: sourceData.activeCommitments.length },
      });
    }

    // ── Recent Activity (for end-of-day) ────────────────────────
    if (type === 'evening' && sourceData.recentActivity && sourceData.recentActivity.length > 0) {
      const items = sourceData.recentActivity.slice(0, 10);
      const lines = items.map(a => {
        const time = new Date(a.timestamp).toLocaleTimeString('en-US', {
          hour: 'numeric',
          minute: '2-digit',
        });
        return `- ${time}: ${a.summary.slice(0, 80)}`;
      });
      sections.push({
        title: "Today's Activity",
        content: lines.join('\n'),
        priority: 'high',
        source: 'workstream',
        metadata: { itemCount: items.length, timeSpan: 'today' },
      });
    }

    // ── End-of-Day Session Summary ──────────────────────────────
    if (type === 'evening' && sourceData.sessionSummary) {
      sections.push({
        title: 'Session Summary',
        content: sourceData.sessionSummary.slice(0, 1000),
        priority: 'high',
        source: 'eod_summary',
      });
    }

    // Enforce max sections
    const finalSections = sections
      .sort((a, b) => priorityRank(a.priority) - priorityRank(b.priority))
      .slice(0, this.config.maxSections);

    // Build executive summary
    const summary = this.buildExecutiveSummary(type, sourceData, finalSections);

    // Calculate metadata
    const allText = finalSections.map(s => s.content).join(' ');
    const wordCount = allText.split(/\s+/).filter(Boolean).length;

    const briefing: DailyBriefing = {
      id: crypto.randomUUID().slice(0, 12),
      generatedAt: now,
      deliveredAt: null,
      scheduledFor: now,
      type,
      summary,
      sections: finalSections,
      metadata: {
        calendarEventCount: sourceData.calendarEvents?.length ?? 0,
        commitmentCount: (sourceData.activeCommitments?.length ?? 0) +
                         (sourceData.overdueCommitments?.length ?? 0),
        pendingItemCount: sourceData.unrepliedMessages?.length ?? 0,
        overdueCount: sourceData.overdueCommitments?.length ?? 0,
        wordCount,
        estimatedReadTimeSec: Math.max(15, Math.round(wordCount / 3.5)), // ~210 wpm spoken
      },
      deliveryAttempts: [],
    };

    this.briefings.push(briefing);
    this.enforceLimit();
    this.queueSave();

    console.log(
      `[DailyBriefing] Generated ${type} briefing: ${finalSections.length} sections, ` +
      `~${briefing.metadata.estimatedReadTimeSec}s read time`
    );

    return briefing;
  }

  // ── Executive Summary Builder ───────────────────────────────────

  private buildExecutiveSummary(
    type: BriefingType,
    data: BriefingSourceData,
    sections: BriefingSection[]
  ): string {
    const parts: string[] = [];

    if (type === 'morning' || type === 'midday') {
      const eventCount = data.calendarEvents?.length ?? 0;
      if (eventCount > 0) {
        parts.push(`${eventCount} event${eventCount !== 1 ? 's' : ''} today`);
      }
      const overdueCount = data.overdueCommitments?.length ?? 0;
      if (overdueCount > 0) {
        parts.push(`${overdueCount} overdue item${overdueCount !== 1 ? 's' : ''}`);
      }
      const unreplied = data.unrepliedMessages?.length ?? 0;
      if (unreplied > 0) {
        parts.push(`${unreplied} awaiting repl${unreplied !== 1 ? 'ies' : 'y'}`);
      }
      const upcoming = data.upcomingDeadlines?.length ?? 0;
      if (upcoming > 0) {
        parts.push(`${upcoming} deadline${upcoming !== 1 ? 's' : ''} approaching`);
      }
    } else {
      // Evening
      const activity = data.recentActivity?.length ?? 0;
      parts.push(`${activity} activity item${activity !== 1 ? 's' : ''} recorded`);
      const completed = data.activeCommitments?.filter(c => c.status === 'completed').length ?? 0;
      if (completed > 0) {
        parts.push(`${completed} commitment${completed !== 1 ? 's' : ''} completed`);
      }
    }

    if (parts.length === 0) {
      return type === 'evening'
        ? 'Quiet day — no significant activity tracked.'
        : 'Clear schedule — no outstanding items.';
    }

    const prefix = type === 'morning'
      ? 'Good morning.'
      : type === 'midday'
        ? 'Midday update.'
        : 'End of day.';

    return `${prefix} ${parts.join(', ')}.`;
  }

  // ── Delivery Tracking ───────────────────────────────────────────

  markDelivered(briefingId: string, channel: BriefingChannel): boolean {
    const b = this.briefings.find(x => x.id === briefingId);
    if (!b) return false;
    b.deliveredAt = Date.now();
    b.deliveryAttempts.push({
      channel,
      attemptedAt: Date.now(),
      status: 'success',
    });
    this.queueSave();
    return true;
  }

  markDeliveryFailed(briefingId: string, channel: BriefingChannel, reason: string): boolean {
    const b = this.briefings.find(x => x.id === briefingId);
    if (!b) return false;
    b.deliveryAttempts.push({
      channel,
      attemptedAt: Date.now(),
      status: 'failed',
      reason,
    });
    this.queueSave();
    return true;
  }

  // ── Adaptive Length ─────────────────────────────────────────────

  /**
   * Decide how long the briefing should be based on volume.
   * - Packed day (5+ events, 3+ overdue) → 'long'
   * - Normal day (2-4 events) → 'medium'
   * - Light day (0-1 events, nothing overdue) → 'short'
   */
  calculateAdaptiveLength(data: BriefingSourceData): BriefingLength {
    const events = data.calendarEvents?.length ?? 0;
    const overdue = data.overdueCommitments?.length ?? 0;
    const pending = data.unrepliedMessages?.length ?? 0;
    const upcoming = data.upcomingDeadlines?.length ?? 0;

    const score = events * 2 + overdue * 3 + pending + upcoming;

    if (score >= 12) return 'long';
    if (score >= 4) return 'medium';
    return 'short';
  }

  // ── Staleness Check ─────────────────────────────────────────────

  /**
   * Check if the most recent briefing of a given type is stale.
   * Used to decide whether to generate a fresh briefing or reuse cached.
   */
  isBriefingStale(type: BriefingType): boolean {
    const latest = this.getLatestBriefing(type);
    if (!latest) return true;
    return (Date.now() - latest.generatedAt) > this.config.staleThresholdMs;
  }

  // ── Context String (for system prompt injection) ────────────────

  /**
   * Returns a concise context string for system prompt injection.
   * Only includes information from the latest briefing if recent.
   */
  getContextString(): string {
    const latest = this.getLatestBriefing('morning')
      || this.getLatestBriefing('midday');

    if (!latest) return '';
    // Only inject if generated in last 12 hours
    if (Date.now() - latest.generatedAt > 12 * 60 * 60 * 1000) return '';

    const lines: string[] = [];
    lines.push(`[DAILY BRIEFING — ${latest.type} @ ${new Date(latest.generatedAt).toLocaleTimeString()}]`);
    lines.push(latest.summary);

    for (const section of latest.sections) {
      if (section.priority === 'critical' || section.priority === 'high') {
        lines.push(`\n## ${section.title}`);
        lines.push(section.content);
      }
    }

    return lines.join('\n');
  }

  /**
   * Compact context for prompt budget constrained scenarios.
   */
  getPromptContext(): string {
    const latest = this.getLatestBriefing('morning')
      || this.getLatestBriefing('midday');

    if (!latest) return '';
    if (Date.now() - latest.generatedAt > 12 * 60 * 60 * 1000) return '';

    return `DAILY BRIEF: ${latest.summary} | ${latest.sections.length} sections, ` +
      `${latest.metadata.calendarEventCount} events, ` +
      `${latest.metadata.overdueCount} overdue, ` +
      `${latest.metadata.pendingItemCount} pending`;
  }

  // ── Scheduling Helpers ──────────────────────────────────────────

  /**
   * Parse HH:MM time string to today's Date.
   */
  getScheduledTimeToday(timeStr: string): number {
    const [hours, minutes] = timeStr.split(':').map(Number);
    if (isNaN(hours) || isNaN(minutes)) return 0;
    const d = new Date();
    d.setHours(hours, minutes, 0, 0);
    return d.getTime();
  }

  /**
   * Determine if it's time to generate a briefing based on current time
   * and last briefing timestamp.
   */
  shouldGenerateBriefing(): { should: boolean; type: BriefingType } {
    if (!this.config.enabled) return { should: false, type: 'morning' };

    const now = Date.now();
    const morningMs = this.getScheduledTimeToday(this.config.morningTime);
    const eveningMs = this.config.eveningTime
      ? this.getScheduledTimeToday(this.config.eveningTime)
      : 0;

    // Check morning briefing
    if (morningMs > 0 && now >= morningMs) {
      const latestMorning = this.getLatestBriefingToday('morning');
      if (!latestMorning) {
        // Check if it's past stale threshold (should generate midday instead)
        if (now - morningMs > this.config.staleThresholdMs) {
          const latestMidday = this.getLatestBriefingToday('midday');
          if (!latestMidday) {
            return { should: true, type: 'midday' };
          }
        } else {
          return { should: true, type: 'morning' };
        }
      }
    }

    // Check evening briefing
    if (eveningMs > 0 && now >= eveningMs) {
      const latestEvening = this.getLatestBriefingToday('evening');
      if (!latestEvening) {
        return { should: true, type: 'evening' };
      }
    }

    return { should: false, type: 'morning' };
  }

  // ── Queries ─────────────────────────────────────────────────────

  getLatestBriefing(type?: BriefingType): DailyBriefing | null {
    const filtered = type
      ? this.briefings.filter(b => b.type === type)
      : this.briefings;
    if (filtered.length === 0) return null;
    return { ...filtered[filtered.length - 1] };
  }

  getLatestBriefingToday(type: BriefingType): DailyBriefing | null {
    const todayStart = new Date();
    todayStart.setHours(0, 0, 0, 0);
    const todayMs = todayStart.getTime();

    const today = this.briefings.filter(
      b => b.type === type && b.generatedAt >= todayMs
    );
    if (today.length === 0) return null;
    return { ...today[today.length - 1] };
  }

  getBriefingById(id: string): DailyBriefing | null {
    const b = this.briefings.find(x => x.id === id);
    return b ? { ...b } : null;
  }

  getBriefingHistory(limit = 10): DailyBriefing[] {
    return this.briefings.slice(-limit).reverse().map(b => ({ ...b }));
  }

  getAllBriefings(): DailyBriefing[] {
    return this.briefings.map(b => ({ ...b }));
  }

  getStatus(): DailyBriefingStatus {
    const latest = this.getLatestBriefing();
    const { should, type: nextType } = this.shouldGenerateBriefing();

    return {
      initialized: this.initialized,
      enabled: this.config.enabled,
      totalBriefings: this.briefings.length,
      lastBriefingAt: latest?.generatedAt ?? null,
      lastBriefingType: latest?.type ?? null,
      nextScheduledAt: should ? Date.now() : this.getNextScheduledTime(),
      morningTime: this.config.morningTime,
      eveningTime: this.config.eveningTime,
    };
  }

  getConfig(): DailyBriefingConfig {
    return { ...this.config };
  }

  // ── Format for Delivery ─────────────────────────────────────────

  /**
   * Format briefing as plain text for dashboard / telegram.
   */
  formatAsText(briefing: DailyBriefing): string {
    const lines: string[] = [];
    lines.push(`📋 ${briefing.summary}`);
    lines.push('');

    for (const section of briefing.sections) {
      const icon = sectionIcon(section.source);
      lines.push(`${icon} ${section.title}`);
      lines.push(section.content);
      lines.push('');
    }

    lines.push(`— Generated ${new Date(briefing.generatedAt).toLocaleTimeString()}`);
    return lines.join('\n');
  }

  /**
   * Format briefing as markdown for rich display.
   */
  formatAsMarkdown(briefing: DailyBriefing): string {
    const lines: string[] = [];
    lines.push(`# ${briefing.type === 'evening' ? 'End of Day Summary' : 'Daily Briefing'}`);
    lines.push('');
    lines.push(`> ${briefing.summary}`);
    lines.push('');

    for (const section of briefing.sections) {
      lines.push(`## ${section.title}`);
      lines.push('');
      lines.push(section.content);
      lines.push('');
    }

    lines.push(`---`);
    lines.push(`*Generated ${new Date(briefing.generatedAt).toLocaleString()} · ` +
      `~${briefing.metadata.estimatedReadTimeSec}s read time*`);
    return lines.join('\n');
  }

  // ── Private Helpers ─────────────────────────────────────────────

  private getNextScheduledTime(): number | null {
    const morningMs = this.getScheduledTimeToday(this.config.morningTime);
    const eveningMs = this.config.eveningTime
      ? this.getScheduledTimeToday(this.config.eveningTime)
      : 0;
    const now = Date.now();

    if (morningMs > now) return morningMs;
    if (eveningMs > now) return eveningMs;

    // Next day morning
    return morningMs + 24 * 60 * 60 * 1000;
  }

  private enforceLimit(): void {
    if (this.briefings.length <= this.config.maxBriefings) return;
    // Remove oldest first
    this.briefings = this.briefings.slice(-this.config.maxBriefings);
  }

  private prune(): void {
    const cutoff = Date.now() - this.config.retentionDays * 24 * 60 * 60 * 1000;
    this.briefings = this.briefings.filter(b => b.generatedAt >= cutoff);
  }

  // ── Persistence ─────────────────────────────────────────────────

  private queueSave(): void {
    if (this.saveQueued) return;
    this.saveQueued = true;
    setTimeout(() => {
      this.saveQueued = false;
      this.save().catch(err => {
        // Crypto Sprint 17: Sanitize error output.
        console.warn('[DailyBriefing] Save failed:', err instanceof Error ? err.message : 'Unknown error');
      });
    }, 2000);
  }

  private async save(): Promise<void> {
    const data = { briefings: this.briefings };
    await fs.writeFile(this.filePath, JSON.stringify(data, null, 2), 'utf-8');
  }
}

// ── Utility ─────────────────────────────────────────────────────────

function priorityRank(p: SectionPriority): number {
  const ranks: Record<SectionPriority, number> = {
    critical: 0,
    high: 1,
    normal: 2,
    low: 3,
  };
  return ranks[p] ?? 3;
}

function sectionIcon(source: BriefingSection['source']): string {
  const icons: Record<string, string> = {
    calendar: '📅',
    commitments: '🤝',
    pending: '⏳',
    priorities: '🎯',
    workstream: '📊',
    world: '🌍',
    eod_summary: '📝',
  };
  return icons[source] ?? '📌';
}

// ── Singleton Export ─────────────────────────────────────────────────

export const dailyBriefingEngine = new DailyBriefingEngine();
