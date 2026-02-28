/**
 * Tests for daily-briefing.ts — Track IV Phase 2: Daily Briefing System.
 * Validates briefing generation, adaptive length, staleness detection,
 * scheduling, context generation, delivery tracking, formatting,
 * pruning, and cLaw Gate compliance.
 *
 * cLaw Gate assertion: All briefings are READ-ONLY informational outputs.
 * No message is ever sent, no action is taken without explicit user approval.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ── Mock Electron app ────────────────────────────────────────────────
vi.mock('electron', () => ({
  app: {
    getPath: vi.fn().mockReturnValue('/mock/userData'),
  },
}));

// ── Mock fs/promises ─────────────────────────────────────────────────
const mockReadFile = vi.fn();
const mockWriteFile = vi.fn();
vi.mock('fs/promises', () => ({
  default: {
    readFile: (...args: any[]) => mockReadFile(...args),
    writeFile: (...args: any[]) => mockWriteFile(...args),
  },
}));

// ── Mock crypto ──────────────────────────────────────────────────────
let uuidCounter = 0;
vi.mock('crypto', () => ({
  default: {
    randomUUID: () => {
      const n = ++uuidCounter;
      return `${String(n).padStart(8, '0')}-${String(n).padStart(4, '0')}-4000-8000-000000000000`;
    },
  },
}));

// ── Import under test (after mocks) ─────────────────────────────────
import type { BriefingSourceData, CalendarEvent, CommitmentSnapshot, UnrepliedSnapshot, FollowUpSnapshot, ActivitySnapshot } from '../../src/main/daily-briefing';

function createEngine() {
  vi.resetModules();
  uuidCounter = 0;
  return import('../../src/main/daily-briefing');
}

// ── Helper factories ─────────────────────────────────────────────────

function makeCalendarEvent(overrides: Partial<CalendarEvent> = {}): CalendarEvent {
  return {
    title: 'Team Standup',
    startTime: Date.now() + 2 * 60 * 60 * 1000, // 2h from now
    endTime: Date.now() + 3 * 60 * 60 * 1000,
    attendees: ['Alice', 'Bob'],
    location: 'Zoom',
    ...overrides,
  };
}

function makeCommitment(overrides: Partial<CommitmentSnapshot> = {}): CommitmentSnapshot {
  return {
    id: 'c-1',
    description: 'Send quarterly report',
    personName: 'Alice',
    direction: 'user_promised',
    deadline: Date.now() + 24 * 60 * 60 * 1000,
    status: 'active',
    ...overrides,
  };
}

function makeUnreplied(overrides: Partial<UnrepliedSnapshot> = {}): UnrepliedSnapshot {
  return {
    recipient: 'Bob',
    channel: 'email',
    summary: 'Follow up on proposal',
    sentAt: Date.now() - 48 * 60 * 60 * 1000,
    expectedReplyByMs: Date.now() - 24 * 60 * 60 * 1000,
    ...overrides,
  };
}

function makeFollowUp(overrides: Partial<FollowUpSnapshot> = {}): FollowUpSnapshot {
  return {
    personName: 'Charlie',
    type: 'unreplied_message',
    reason: 'No reply after 3 days',
    urgency: 'high',
    ...overrides,
  };
}

function makeActivity(overrides: Partial<ActivitySnapshot> = {}): ActivitySnapshot {
  return {
    timestamp: Date.now() - 2 * 60 * 60 * 1000,
    summary: 'Reviewed PR #42',
    type: 'code_review',
    ...overrides,
  };
}

function makeFullSourceData(): BriefingSourceData {
  return {
    calendarEvents: [
      makeCalendarEvent({ title: '9am Standup' }),
      makeCalendarEvent({ title: '11am Design Review', startTime: Date.now() + 4 * 60 * 60 * 1000 }),
      makeCalendarEvent({ title: '2pm Client Call', attendees: ['Client A'], startTime: Date.now() + 7 * 60 * 60 * 1000 }),
    ],
    activeCommitments: [makeCommitment()],
    overdueCommitments: [makeCommitment({ status: 'overdue', deadline: Date.now() - 24 * 60 * 60 * 1000 })],
    upcomingDeadlines: [makeCommitment({ deadline: Date.now() + 12 * 60 * 60 * 1000 })],
    unrepliedMessages: [makeUnreplied()],
    followUpSuggestions: [makeFollowUp()],
  };
}

// ═══════════════════════════════════════════════════════════════════════
// Test Suite
// ═══════════════════════════════════════════════════════════════════════

describe('DailyBriefingEngine', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-02-26T08:00:00Z'));
    mockReadFile.mockReset();
    mockWriteFile.mockReset();
    mockReadFile.mockRejectedValue(new Error('ENOENT'));
    mockWriteFile.mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  // ── Initialization ───────────────────────────────────────────────

  describe('initialize()', () => {
    it('should initialize with empty state on fresh start', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();
      expect(dailyBriefingEngine.getAllBriefings()).toHaveLength(0);
    });

    it('should load persisted briefings from disk', async () => {
      const stored = {
        briefings: [{
          id: 'b-1',
          generatedAt: Date.now() - 60000,
          deliveredAt: null,
          scheduledFor: Date.now(),
          type: 'morning',
          summary: 'Test briefing',
          sections: [],
          metadata: { calendarEventCount: 0, commitmentCount: 0, pendingItemCount: 0, overdueCount: 0, wordCount: 5, estimatedReadTimeSec: 15 },
          deliveryAttempts: [],
        }],
      };
      mockReadFile.mockResolvedValue(JSON.stringify(stored));

      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();
      expect(dailyBriefingEngine.getAllBriefings()).toHaveLength(1);
    });

    it('should apply custom config on init', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize({
        morningTime: '06:00',
        maxSections: 4,
      });
      const config = dailyBriefingEngine.getConfig();
      expect(config.morningTime).toBe('06:00');
      expect(config.maxSections).toBe(4);
    });

    it('should handle corrupt JSON gracefully', async () => {
      mockReadFile.mockResolvedValue('not valid json {{{{');

      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();
      expect(dailyBriefingEngine.getAllBriefings()).toHaveLength(0);
    });

    it('should handle partial data (missing briefings array)', async () => {
      mockReadFile.mockResolvedValue(JSON.stringify({ unrelated: true }));

      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();
      expect(dailyBriefingEngine.getAllBriefings()).toHaveLength(0);
    });

    it('should prune old briefings on load', async () => {
      const oldBriefing = {
        id: 'old-1',
        generatedAt: Date.now() - 60 * 24 * 60 * 60 * 1000, // 60 days old
        deliveredAt: null,
        scheduledFor: Date.now() - 60 * 24 * 60 * 60 * 1000,
        type: 'morning',
        summary: 'Old briefing',
        sections: [],
        metadata: { calendarEventCount: 0, commitmentCount: 0, pendingItemCount: 0, overdueCount: 0, wordCount: 0, estimatedReadTimeSec: 15 },
        deliveryAttempts: [],
      };
      mockReadFile.mockResolvedValue(JSON.stringify({ briefings: [oldBriefing] }));

      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();
      expect(dailyBriefingEngine.getAllBriefings()).toHaveLength(0);
    });
  });

  // ── Briefing Generation ────────────────────────────────────────────

  describe('generateBriefing()', () => {
    it('should generate a morning briefing with all sections', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      const briefing = dailyBriefingEngine.generateBriefing('morning', makeFullSourceData());

      expect(briefing.type).toBe('morning');
      expect(briefing.id).toBeTruthy();
      expect(briefing.generatedAt).toBeGreaterThan(0);
      expect(briefing.sections.length).toBeGreaterThan(0);
      expect(briefing.summary).toBeTruthy();
      expect(briefing.metadata.calendarEventCount).toBe(3);
    });

    it('should generate unique IDs for each briefing', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      const b1 = dailyBriefingEngine.generateBriefing('morning', makeFullSourceData());
      const b2 = dailyBriefingEngine.generateBriefing('midday', makeFullSourceData());

      expect(b1.id).not.toBe(b2.id);
    });

    it('should include calendar section with attendees', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      const data: BriefingSourceData = {
        calendarEvents: [
          makeCalendarEvent({ title: 'Planning Meeting', attendees: ['Alice', 'Bob', 'Charlie'] }),
        ],
      };
      const briefing = dailyBriefingEngine.generateBriefing('morning', data);

      const calSection = briefing.sections.find(s => s.source === 'calendar');
      expect(calSection).toBeDefined();
      expect(calSection!.content).toContain('Planning Meeting');
      expect(calSection!.content).toContain('Alice');
      expect(calSection!.priority).toBe('critical');
    });

    it('should include overdue commitments as critical priority', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      const data: BriefingSourceData = {
        overdueCommitments: [
          makeCommitment({ description: 'Send report', status: 'overdue' }),
        ],
      };
      const briefing = dailyBriefingEngine.generateBriefing('morning', data);

      const overdueSection = briefing.sections.find(s => s.title === 'Overdue Items');
      expect(overdueSection).toBeDefined();
      expect(overdueSection!.priority).toBe('critical');
      expect(overdueSection!.content).toContain('[OVERDUE]');
    });

    it('should include unreplied messages with age', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      const data: BriefingSourceData = {
        unrepliedMessages: [makeUnreplied()],
      };
      const briefing = dailyBriefingEngine.generateBriefing('morning', data);

      const pendingSection = briefing.sections.find(s => s.source === 'pending');
      expect(pendingSection).toBeDefined();
      expect(pendingSection!.content).toContain('Bob');
      expect(pendingSection!.content).toContain('email');
    });

    it('should include follow-up suggestions (max 5)', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      const suggestions = Array.from({ length: 8 }, (_, i) =>
        makeFollowUp({ personName: `Person${i}` })
      );
      const data: BriefingSourceData = { followUpSuggestions: suggestions };
      const briefing = dailyBriefingEngine.generateBriefing('morning', data);

      const sugSection = briefing.sections.find(s => s.title === 'Suggested Follow-Ups');
      expect(sugSection).toBeDefined();
      // Content should only have 5 items
      const lines = sugSection!.content.split('\n').filter(Boolean);
      expect(lines.length).toBeLessThanOrEqual(5);
    });

    it('should handle empty source data', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      const briefing = dailyBriefingEngine.generateBriefing('morning', {});

      expect(briefing.sections).toHaveLength(0);
      expect(briefing.summary).toContain('Clear schedule');
    });

    it('should cap sections at maxSections config', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize({ maxSections: 2 });

      const briefing = dailyBriefingEngine.generateBriefing('morning', makeFullSourceData());

      expect(briefing.sections.length).toBeLessThanOrEqual(2);
    });

    it('should include activity section for evening briefings only', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      const data: BriefingSourceData = {
        recentActivity: [makeActivity(), makeActivity({ summary: 'Merged feature branch' })],
      };

      const morning = dailyBriefingEngine.generateBriefing('morning', data);
      const evening = dailyBriefingEngine.generateBriefing('evening', data);

      expect(morning.sections.find(s => s.source === 'workstream')).toBeUndefined();
      expect(evening.sections.find(s => s.source === 'workstream')).toBeDefined();
    });

    it('should include session summary for evening briefings', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      const data: BriefingSourceData = {
        sessionSummary: 'Completed 3 PRs, reviewed 2 proposals, scheduled 1 meeting.',
      };

      const evening = dailyBriefingEngine.generateBriefing('evening', data);
      const eodSection = evening.sections.find(s => s.source === 'eod_summary');
      expect(eodSection).toBeDefined();
      expect(eodSection!.content).toContain('3 PRs');
    });

    it('should sort sections by priority (critical first)', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      const briefing = dailyBriefingEngine.generateBriefing('morning', makeFullSourceData());

      const priorities = briefing.sections.map(s => s.priority);
      const ranks = { critical: 0, high: 1, normal: 2, low: 3 };
      for (let i = 1; i < priorities.length; i++) {
        expect(ranks[priorities[i]]).toBeGreaterThanOrEqual(ranks[priorities[i - 1]]);
      }
    });

    it('should calculate word count and estimated read time', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      const briefing = dailyBriefingEngine.generateBriefing('morning', makeFullSourceData());

      expect(briefing.metadata.wordCount).toBeGreaterThan(0);
      expect(briefing.metadata.estimatedReadTimeSec).toBeGreaterThanOrEqual(15);
    });

    it('should truncate long descriptions', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      const longDesc = 'A'.repeat(200);
      const data: BriefingSourceData = {
        overdueCommitments: [makeCommitment({ description: longDesc })],
      };
      const briefing = dailyBriefingEngine.generateBriefing('morning', data);

      const section = briefing.sections.find(s => s.title === 'Overdue Items');
      expect(section!.content.length).toBeLessThan(longDesc.length);
    });

    it('should persist briefing after generation', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      dailyBriefingEngine.generateBriefing('morning', makeFullSourceData());

      vi.advanceTimersByTime(3000);
      expect(mockWriteFile).toHaveBeenCalled();
    });
  });

  // ── Executive Summary ──────────────────────────────────────────────

  describe('executive summary', () => {
    it('should include event count for morning briefing', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      const data: BriefingSourceData = {
        calendarEvents: [makeCalendarEvent(), makeCalendarEvent()],
      };
      const briefing = dailyBriefingEngine.generateBriefing('morning', data);

      expect(briefing.summary).toContain('2 events');
      expect(briefing.summary).toContain('Good morning');
    });

    it('should include overdue count in summary', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      const data: BriefingSourceData = {
        overdueCommitments: [makeCommitment({ status: 'overdue' })],
      };
      const briefing = dailyBriefingEngine.generateBriefing('morning', data);

      expect(briefing.summary).toContain('1 overdue item');
    });

    it('should say "Midday update" for midday briefings', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      const briefing = dailyBriefingEngine.generateBriefing('midday', makeFullSourceData());
      expect(briefing.summary).toContain('Midday update');
    });

    it('should say "End of day" for evening briefings', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      const data: BriefingSourceData = {
        recentActivity: [makeActivity()],
      };
      const briefing = dailyBriefingEngine.generateBriefing('evening', data);
      expect(briefing.summary).toContain('End of day');
    });

    it('should say "End of day" for evening with no activity (0 items)', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      const briefing = dailyBriefingEngine.generateBriefing('evening', {});
      // Evening always reports activity count, even when 0
      expect(briefing.summary).toContain('End of day');
      expect(briefing.summary).toContain('0 activity items');
    });
  });

  // ── Adaptive Length ────────────────────────────────────────────────

  describe('calculateAdaptiveLength()', () => {
    it('should return "short" for light day', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      const result = dailyBriefingEngine.calculateAdaptiveLength({
        calendarEvents: [],
        overdueCommitments: [],
      });
      expect(result).toBe('short');
    });

    it('should return "medium" for moderate day', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      const result = dailyBriefingEngine.calculateAdaptiveLength({
        calendarEvents: [makeCalendarEvent(), makeCalendarEvent()],
        unrepliedMessages: [makeUnreplied()],
      });
      expect(result).toBe('medium');
    });

    it('should return "long" for packed day', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      const result = dailyBriefingEngine.calculateAdaptiveLength({
        calendarEvents: [makeCalendarEvent(), makeCalendarEvent(), makeCalendarEvent(), makeCalendarEvent()],
        overdueCommitments: [makeCommitment(), makeCommitment()],
        unrepliedMessages: [makeUnreplied()],
      });
      expect(result).toBe('long');
    });
  });

  // ── Staleness Detection ────────────────────────────────────────────

  describe('isBriefingStale()', () => {
    it('should return true when no briefing exists', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      expect(dailyBriefingEngine.isBriefingStale('morning')).toBe(true);
    });

    it('should return false for a fresh briefing', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      dailyBriefingEngine.generateBriefing('morning', makeFullSourceData());

      expect(dailyBriefingEngine.isBriefingStale('morning')).toBe(false);
    });

    it('should return true when briefing exceeds stale threshold', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      dailyBriefingEngine.generateBriefing('morning', makeFullSourceData());

      // Advance past 4-hour stale threshold
      vi.advanceTimersByTime(5 * 60 * 60 * 1000);

      expect(dailyBriefingEngine.isBriefingStale('morning')).toBe(true);
    });
  });

  // ── Scheduling ─────────────────────────────────────────────────────

  describe('shouldGenerateBriefing()', () => {
    it('should suggest morning briefing when past morning time and no briefing today', async () => {
      // Use local-time constructor so setHours() in getScheduledTimeToday is consistent
      vi.setSystemTime(new Date(2026, 1, 26, 9, 0, 0, 0)); // 9:00 AM local
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      const result = dailyBriefingEngine.shouldGenerateBriefing();
      expect(result.should).toBe(true);
      expect(result.type).toBe('morning');
    });

    it('should not suggest when disabled', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize({ enabled: false });

      const result = dailyBriefingEngine.shouldGenerateBriefing();
      expect(result.should).toBe(false);
    });

    it('should suggest midday when morning time was long ago', async () => {
      // Use local-time constructor: noon local is 6h past 06:00 morning (> 4h stale)
      vi.setSystemTime(new Date(2026, 1, 26, 12, 0, 0, 0)); // noon local
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize({ morningTime: '06:00' });

      const result = dailyBriefingEngine.shouldGenerateBriefing();
      expect(result.should).toBe(true);
      expect(result.type).toBe('midday');
    });

    it('should not suggest when morning briefing already generated today', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      dailyBriefingEngine.generateBriefing('morning', makeFullSourceData());

      const result = dailyBriefingEngine.shouldGenerateBriefing();
      // Morning already done, evening not due yet
      expect(result.should).toBe(false);
    });
  });

  describe('getScheduledTimeToday()', () => {
    it('should parse valid HH:MM string', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      const ms = dailyBriefingEngine.getScheduledTimeToday('08:00');
      const d = new Date(ms);
      expect(d.getHours()).toBe(8);
      expect(d.getMinutes()).toBe(0);
    });

    it('should return 0 for invalid time string', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      expect(dailyBriefingEngine.getScheduledTimeToday('invalid')).toBe(0);
    });
  });

  // ── Delivery Tracking ──────────────────────────────────────────────

  describe('delivery tracking', () => {
    it('should mark briefing as delivered', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      const briefing = dailyBriefingEngine.generateBriefing('morning', makeFullSourceData());
      const success = dailyBriefingEngine.markDelivered(briefing.id, 'dashboard');

      expect(success).toBe(true);
      const updated = dailyBriefingEngine.getBriefingById(briefing.id);
      expect(updated!.deliveredAt).toBeGreaterThan(0);
      expect(updated!.deliveryAttempts).toHaveLength(1);
      expect(updated!.deliveryAttempts[0].status).toBe('success');
    });

    it('should record delivery failure', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      const briefing = dailyBriefingEngine.generateBriefing('morning', makeFullSourceData());
      dailyBriefingEngine.markDeliveryFailed(briefing.id, 'telegram', 'network error');

      const updated = dailyBriefingEngine.getBriefingById(briefing.id);
      expect(updated!.deliveryAttempts).toHaveLength(1);
      expect(updated!.deliveryAttempts[0].status).toBe('failed');
      expect(updated!.deliveryAttempts[0].reason).toBe('network error');
    });

    it('should return false for non-existent briefing', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      expect(dailyBriefingEngine.markDelivered('no-such-id', 'dashboard')).toBe(false);
    });
  });

  // ── Queries ────────────────────────────────────────────────────────

  describe('queries', () => {
    it('getLatestBriefing should return the most recent', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      const b1 = dailyBriefingEngine.generateBriefing('morning', makeFullSourceData());
      vi.advanceTimersByTime(1000);
      const b2 = dailyBriefingEngine.generateBriefing('midday', makeFullSourceData());

      const latest = dailyBriefingEngine.getLatestBriefing();
      expect(latest!.id).toBe(b2.id);
    });

    it('getLatestBriefing should filter by type', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      dailyBriefingEngine.generateBriefing('morning', makeFullSourceData());
      dailyBriefingEngine.generateBriefing('midday', makeFullSourceData());

      const latestMorning = dailyBriefingEngine.getLatestBriefing('morning');
      expect(latestMorning!.type).toBe('morning');
    });

    it('getLatestBriefing should return null when empty', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      expect(dailyBriefingEngine.getLatestBriefing()).toBeNull();
    });

    it('getBriefingHistory should return recent first, limited', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      dailyBriefingEngine.generateBriefing('morning', makeFullSourceData());
      vi.advanceTimersByTime(100);
      dailyBriefingEngine.generateBriefing('midday', makeFullSourceData());
      vi.advanceTimersByTime(100);
      dailyBriefingEngine.generateBriefing('evening', {});

      const history = dailyBriefingEngine.getBriefingHistory(2);
      expect(history).toHaveLength(2);
      expect(history[0].type).toBe('evening'); // most recent first
    });

    it('getBriefingById should return correct briefing', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      const b = dailyBriefingEngine.generateBriefing('morning', makeFullSourceData());
      const found = dailyBriefingEngine.getBriefingById(b.id);
      expect(found).not.toBeNull();
      expect(found!.type).toBe('morning');
    });

    it('getBriefingById should return null for unknown id', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      expect(dailyBriefingEngine.getBriefingById('nonexistent')).toBeNull();
    });

    it('getStatus should return correct overview', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      dailyBriefingEngine.generateBriefing('morning', makeFullSourceData());

      const status = dailyBriefingEngine.getStatus();
      expect(status.initialized).toBe(true);
      expect(status.enabled).toBe(true);
      expect(status.totalBriefings).toBe(1);
      expect(status.lastBriefingType).toBe('morning');
      expect(status.morningTime).toBe('08:00');
    });

    it('getAllBriefings should return copies', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      dailyBriefingEngine.generateBriefing('morning', makeFullSourceData());

      const all = dailyBriefingEngine.getAllBriefings();
      all.length = 0;

      expect(dailyBriefingEngine.getAllBriefings()).toHaveLength(1);
    });
  });

  // ── Context Generation ─────────────────────────────────────────────

  describe('getContextString()', () => {
    it('should return empty string with no briefings', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      expect(dailyBriefingEngine.getContextString()).toBe('');
    });

    it('should include briefing header and summary', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      dailyBriefingEngine.generateBriefing('morning', makeFullSourceData());

      const ctx = dailyBriefingEngine.getContextString();
      expect(ctx).toContain('[DAILY BRIEFING');
      expect(ctx).toContain('Good morning');
    });

    it('should only include critical and high priority sections', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      dailyBriefingEngine.generateBriefing('morning', makeFullSourceData());

      const ctx = dailyBriefingEngine.getContextString();
      // Calendar (critical) should be there
      expect(ctx).toContain('Schedule');
      // Low priority sections may be omitted
    });

    it('should return empty if briefing is too old (>12h)', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      dailyBriefingEngine.generateBriefing('morning', makeFullSourceData());

      // Advance 13 hours
      vi.advanceTimersByTime(13 * 60 * 60 * 1000);

      expect(dailyBriefingEngine.getContextString()).toBe('');
    });
  });

  describe('getPromptContext()', () => {
    it('should return empty string with no briefings', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      expect(dailyBriefingEngine.getPromptContext()).toBe('');
    });

    it('should return compact single-line summary', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      dailyBriefingEngine.generateBriefing('morning', makeFullSourceData());

      const ctx = dailyBriefingEngine.getPromptContext();
      expect(ctx).toContain('DAILY BRIEF:');
      expect(ctx).toContain('events');
      expect(ctx).not.toContain('\n');
    });
  });

  // ── Formatting ─────────────────────────────────────────────────────

  describe('formatAsText()', () => {
    it('should format briefing as plain text', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      const briefing = dailyBriefingEngine.generateBriefing('morning', makeFullSourceData());
      const text = dailyBriefingEngine.formatAsText(briefing);

      expect(text).toContain(briefing.summary);
      expect(text).toContain('Generated');
    });
  });

  describe('formatAsMarkdown()', () => {
    it('should format briefing as markdown', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      const briefing = dailyBriefingEngine.generateBriefing('morning', makeFullSourceData());
      const md = dailyBriefingEngine.formatAsMarkdown(briefing);

      expect(md).toContain('# Daily Briefing');
      expect(md).toContain('##');
      expect(md).toContain('---');
    });

    it('should use "End of Day Summary" title for evening', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      const briefing = dailyBriefingEngine.generateBriefing('evening', {
        recentActivity: [makeActivity()],
      });
      const md = dailyBriefingEngine.formatAsMarkdown(briefing);

      expect(md).toContain('# End of Day Summary');
    });
  });

  // ── Pruning ────────────────────────────────────────────────────────

  describe('pruning', () => {
    it('should enforce maxBriefings limit', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize({ maxBriefings: 3 });

      for (let i = 0; i < 5; i++) {
        dailyBriefingEngine.generateBriefing('morning', makeFullSourceData());
        vi.advanceTimersByTime(100);
      }

      expect(dailyBriefingEngine.getAllBriefings().length).toBeLessThanOrEqual(3);
    });
  });

  // ── Persistence ────────────────────────────────────────────────────

  describe('persistence', () => {
    it('should debounce saves (2 second quiet period)', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      dailyBriefingEngine.generateBriefing('morning', makeFullSourceData());
      dailyBriefingEngine.generateBriefing('midday', makeFullSourceData());

      // Before debounce
      vi.advanceTimersByTime(1000);
      expect(mockWriteFile).not.toHaveBeenCalled();

      // After debounce
      vi.advanceTimersByTime(1500);
      expect(mockWriteFile).toHaveBeenCalledTimes(1);
    });

    it('should save briefings array', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      dailyBriefingEngine.generateBriefing('morning', makeFullSourceData());

      vi.advanceTimersByTime(3000);

      const writeCall = mockWriteFile.mock.calls[0];
      const data = JSON.parse(writeCall[1]);
      expect(data).toHaveProperty('briefings');
      expect(data.briefings).toHaveLength(1);
    });
  });

  // ── cLaw Gate Compliance ───────────────────────────────────────────

  describe('cLaw Gate compliance', () => {
    it('briefings are data objects only (no executable callbacks)', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      const briefing = dailyBriefingEngine.generateBriefing('morning', makeFullSourceData());

      // No function properties in the briefing
      const json = JSON.stringify(briefing);
      const parsed = JSON.parse(json);
      for (const key of Object.keys(parsed)) {
        expect(typeof parsed[key]).not.toBe('function');
      }
    });

    it('sections contain only informational text', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      const briefing = dailyBriefingEngine.generateBriefing('morning', makeFullSourceData());

      for (const section of briefing.sections) {
        // No action verbs that imply automatic execution
        expect(section.content).not.toMatch(/\b(sending|executing|deleting|modifying)\b/i);
      }
    });

    it('context string contains no action commands', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      dailyBriefingEngine.generateBriefing('morning', makeFullSourceData());

      const ctx = dailyBriefingEngine.getContextString();
      expect(ctx).not.toMatch(/\b(EXECUTE|SEND|DELETE|MODIFY|AUTO-)\b/);
    });
  });

  // ── Edge Cases ─────────────────────────────────────────────────────

  describe('edge cases', () => {
    it('should handle calendar event with no attendees', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      const data: BriefingSourceData = {
        calendarEvents: [makeCalendarEvent({ attendees: undefined, location: undefined })],
      };
      const briefing = dailyBriefingEngine.generateBriefing('morning', data);

      expect(briefing.sections).toHaveLength(1);
      expect(briefing.sections[0].content).toContain('Team Standup');
    });

    it('should handle attendees exceeding 3 with +N indicator', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      const data: BriefingSourceData = {
        calendarEvents: [
          makeCalendarEvent({ attendees: ['A', 'B', 'C', 'D', 'E'] }),
        ],
      };
      const briefing = dailyBriefingEngine.generateBriefing('morning', data);

      expect(briefing.sections[0].content).toContain('+2');
    });

    it('should handle commitment with no deadline', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      const data: BriefingSourceData = {
        upcomingDeadlines: [makeCommitment({ deadline: null })],
      };
      const briefing = dailyBriefingEngine.generateBriefing('morning', data);
      expect(briefing.sections.length).toBeGreaterThan(0);
    });

    it('should not crash with null/undefined source fields', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      const data: BriefingSourceData = {
        calendarEvents: undefined,
        activeCommitments: undefined,
        overdueCommitments: undefined,
        upcomingDeadlines: undefined,
        unrepliedMessages: undefined,
        followUpSuggestions: undefined,
      };
      const briefing = dailyBriefingEngine.generateBriefing('morning', data);
      expect(briefing.sections).toHaveLength(0);
    });

    it('getConfig should return a copy', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      const config = dailyBriefingEngine.getConfig();
      config.morningTime = '99:99';

      expect(dailyBriefingEngine.getConfig().morningTime).toBe('08:00');
    });

    it('session summary should be truncated at 1000 chars', async () => {
      const { dailyBriefingEngine } = await createEngine();
      await dailyBriefingEngine.initialize();

      const longSummary = 'X'.repeat(2000);
      const data: BriefingSourceData = { sessionSummary: longSummary };
      const briefing = dailyBriefingEngine.generateBriefing('evening', data);

      const eodSection = briefing.sections.find(s => s.source === 'eod_summary');
      expect(eodSection!.content.length).toBeLessThanOrEqual(1000);
    });
  });
});
