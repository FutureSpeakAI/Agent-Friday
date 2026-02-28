/**
 * Tests for commitment-tracker.ts — Track IV Phase 1: Temporal Reasoning.
 * Validates commitment CRUD, deduplication, outbound message tracking,
 * follow-up suggestion generation, context generation, pruning, and edge cases.
 *
 * cLaw Gate assertion: All proactive outputs are SUGGESTIONS only.
 * No message is ever sent, no action is ever taken without explicit user approval.
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
      // Counter in FIRST 8 chars so .slice(0,12) produces unique IDs
      return `${String(n).padStart(8, '0')}-${String(n).padStart(4, '0')}-4000-8000-000000000000`;
    },
  },
}));

// ── Import under test (after mocks) ─────────────────────────────────
import type { CommitmentMention } from '../../src/main/commitment-tracker';

function createTracker() {
  // Re-import fresh instance each time
  vi.resetModules();
  uuidCounter = 0;
  return import('../../src/main/commitment-tracker');
}

// ── Helper factory ───────────────────────────────────────────────────
function makeMention(overrides: Partial<CommitmentMention> = {}): CommitmentMention {
  return {
    description: 'Send the quarterly report to finance team',
    personName: 'Alice',
    direction: 'user_promised',
    source: 'conversation',
    deadline: Date.now() + 48 * 60 * 60 * 1000, // 48h from now
    domain: 'finance',
    confidence: 0.85,
    contextSnippet: 'I promised Alice I would send the quarterly report by Friday',
    ...overrides,
  };
}

// ═══════════════════════════════════════════════════════════════════════
// Test Suite
// ═══════════════════════════════════════════════════════════════════════

describe('CommitmentTracker', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-02-26T12:00:00Z'));
    mockReadFile.mockReset();
    mockWriteFile.mockReset();
    mockReadFile.mockRejectedValue(new Error('ENOENT')); // fresh start
    mockWriteFile.mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  // ── Initialization ───────────────────────────────────────────────

  describe('initialize()', () => {
    it('should initialize with empty state on fresh start', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();
      expect(commitmentTracker.getAllCommitments()).toHaveLength(0);
      expect(commitmentTracker.getUnrepliedMessages()).toHaveLength(0);
    });

    it('should load persisted data from disk', async () => {
      const saved = {
        commitments: [{
          id: 'test1', description: 'test', direction: 'user_promised',
          personName: 'Bob', source: 'conversation', status: 'active',
          createdAt: Date.now(), deadline: null, domain: '', contextSnippet: '',
          confidence: 0.8, reminded: false, lastRemindedAt: null,
          resolvedAt: null, notes: '',
        }],
        outboundMessages: [],
        followUpSuggestions: [],
      };
      mockReadFile.mockResolvedValue(JSON.stringify(saved));

      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();
      expect(commitmentTracker.getAllCommitments()).toHaveLength(1);
      expect(commitmentTracker.getAllCommitments()[0].id).toBe('test1');
    });

    it('should apply custom config on init', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize({ maxCommitments: 50, minConfidence: 0.7 });
      const config = commitmentTracker.getConfig();
      expect(config.maxCommitments).toBe(50);
      expect(config.minConfidence).toBe(0.7);
      // Defaults preserved for unspecified
      expect(config.retentionDays).toBe(90);
    });

    it('should mark overdue commitments on load', async () => {
      const pastDeadline = Date.now() - 2 * 60 * 60 * 1000; // 2h ago
      const saved = {
        commitments: [{
          id: 'overdue1', description: 'past deadline', direction: 'user_promised',
          personName: 'Bob', source: 'conversation', status: 'active',
          createdAt: Date.now() - 72 * 60 * 60 * 1000, deadline: pastDeadline,
          domain: '', contextSnippet: '', confidence: 0.9, reminded: false,
          lastRemindedAt: null, resolvedAt: null, notes: '',
        }],
        outboundMessages: [],
        followUpSuggestions: [],
      };
      mockReadFile.mockResolvedValue(JSON.stringify(saved));

      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();
      expect(commitmentTracker.getOverdueCommitments()).toHaveLength(1);
      expect(commitmentTracker.getAllCommitments()[0].status).toBe('overdue');
    });

    it('should un-snooze expired snoozed commitments on load', async () => {
      const pastSnooze = Date.now() - 1000;
      const saved = {
        commitments: [{
          id: 'snoozed1', description: 'snoozed item', direction: 'user_promised',
          personName: 'Bob', source: 'conversation', status: 'snoozed',
          createdAt: Date.now() - 72 * 60 * 60 * 1000, deadline: null,
          domain: '', contextSnippet: '', confidence: 0.9, reminded: true,
          lastRemindedAt: pastSnooze, resolvedAt: null, notes: '',
        }],
        outboundMessages: [],
        followUpSuggestions: [],
      };
      mockReadFile.mockResolvedValue(JSON.stringify(saved));

      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();
      // Should be active again (no deadline → not overdue)
      expect(commitmentTracker.getAllCommitments()[0].status).toBe('active');
      expect(commitmentTracker.getAllCommitments()[0].reminded).toBe(false);
    });

    it('should handle corrupt JSON gracefully', async () => {
      mockReadFile.mockResolvedValue('NOT VALID JSON {{{');
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();
      expect(commitmentTracker.getAllCommitments()).toHaveLength(0);
    });

    it('should handle partial data (missing fields)', async () => {
      mockReadFile.mockResolvedValue(JSON.stringify({ commitments: 'not-an-array' }));
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();
      expect(commitmentTracker.getAllCommitments()).toHaveLength(0);
    });
  });

  // ── Commitment CRUD ──────────────────────────────────────────────

  describe('addCommitment()', () => {
    it('should add a valid commitment', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      const result = commitmentTracker.addCommitment(makeMention());
      expect(result).not.toBeNull();
      expect(result!.description).toBe('Send the quarterly report to finance team');
      expect(result!.personName).toBe('Alice');
      expect(result!.direction).toBe('user_promised');
      expect(result!.status).toBe('active');
      expect(result!.confidence).toBe(0.85);
    });

    it('should reject low-confidence mentions', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      const result = commitmentTracker.addCommitment(makeMention({ confidence: 0.3 }));
      expect(result).toBeNull();
      expect(commitmentTracker.getAllCommitments()).toHaveLength(0);
    });

    it('should deduplicate similar descriptions for same person within time window', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      commitmentTracker.addCommitment(makeMention({
        description: 'Send the quarterly report to the finance team',
      }));
      const dupe = commitmentTracker.addCommitment(makeMention({
        description: 'Send quarterly report to finance team',
      }));

      expect(dupe).toBeNull();
      expect(commitmentTracker.getAllCommitments()).toHaveLength(1);
    });

    it('should allow similar descriptions for different people', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      commitmentTracker.addCommitment(makeMention({ personName: 'Alice' }));
      const second = commitmentTracker.addCommitment(makeMention({ personName: 'Bob' }));

      expect(second).not.toBeNull();
      expect(commitmentTracker.getAllCommitments()).toHaveLength(2);
    });

    it('should allow same person after dedup time window expires', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      commitmentTracker.addCommitment(makeMention());

      // Advance 2 hours past dedup window
      vi.advanceTimersByTime(2 * 60 * 60 * 1000);

      const second = commitmentTracker.addCommitment(makeMention());
      expect(second).not.toBeNull();
      expect(commitmentTracker.getAllCommitments()).toHaveLength(2);
    });

    it('should clamp confidence to [0, 1]', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      const high = commitmentTracker.addCommitment(makeMention({ confidence: 1.5 }));
      expect(high!.confidence).toBe(1);

      vi.advanceTimersByTime(2 * 60 * 60 * 1000);
      const low = commitmentTracker.addCommitment(makeMention({ confidence: -0.5 }));
      // -0.5 is below minConfidence 0.5 default, should be rejected
      expect(low).toBeNull();
    });

    it('should truncate long fields', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      const long = commitmentTracker.addCommitment(makeMention({
        description: 'x'.repeat(1000),
        personName: 'y'.repeat(200),
        contextSnippet: 'z'.repeat(500),
        domain: 'd'.repeat(100),
      }));

      expect(long!.description.length).toBeLessThanOrEqual(500);
      expect(long!.personName.length).toBeLessThanOrEqual(100);
      expect(long!.contextSnippet.length).toBeLessThanOrEqual(300);
      expect(long!.domain.length).toBeLessThanOrEqual(50);
    });

    it('should generate unique IDs', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      const c1 = commitmentTracker.addCommitment(makeMention({ personName: 'Alice' }));
      const c2 = commitmentTracker.addCommitment(makeMention({ personName: 'Bob' }));
      const c3 = commitmentTracker.addCommitment(makeMention({ personName: 'Charlie' }));

      expect(c1!.id).not.toBe(c2!.id);
      expect(c2!.id).not.toBe(c3!.id);
    });

    it('should queue a save after adding', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      commitmentTracker.addCommitment(makeMention());
      // Save is debounced at 2s
      vi.advanceTimersByTime(2500);

      expect(mockWriteFile).toHaveBeenCalled();
    });
  });

  describe('processCommitmentMentions()', () => {
    it('should process multiple mentions and return only added ones', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      const mentions = [
        makeMention({ description: 'Task A', personName: 'Alice' }),
        makeMention({ description: 'Task B', personName: 'Bob' }),
        makeMention({ description: 'Task C', personName: 'Charlie', confidence: 0.1 }), // low confidence
      ];

      const added = commitmentTracker.processCommitmentMentions(mentions);
      expect(added).toHaveLength(2);
      expect(commitmentTracker.getAllCommitments()).toHaveLength(2);
    });
  });

  describe('completeCommitment()', () => {
    it('should mark active commitment as completed', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      const c = commitmentTracker.addCommitment(makeMention())!;
      const result = commitmentTracker.completeCommitment(c.id, 'Done!');

      expect(result).toBe(true);
      expect(commitmentTracker.getCommitmentById(c.id)!.status).toBe('completed');
      expect(commitmentTracker.getCommitmentById(c.id)!.notes).toBe('Done!');
      expect(commitmentTracker.getCommitmentById(c.id)!.resolvedAt).not.toBeNull();
    });

    it('should return false for already completed commitment', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      const c = commitmentTracker.addCommitment(makeMention())!;
      commitmentTracker.completeCommitment(c.id);
      expect(commitmentTracker.completeCommitment(c.id)).toBe(false);
    });

    it('should return false for non-existent ID', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();
      expect(commitmentTracker.completeCommitment('nonexistent')).toBe(false);
    });
  });

  describe('cancelCommitment()', () => {
    it('should cancel an active commitment', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      const c = commitmentTracker.addCommitment(makeMention())!;
      const result = commitmentTracker.cancelCommitment(c.id, 'No longer needed');

      expect(result).toBe(true);
      expect(commitmentTracker.getCommitmentById(c.id)!.status).toBe('cancelled');
      expect(commitmentTracker.getCommitmentById(c.id)!.notes).toBe('No longer needed');
    });

    it('should not cancel already cancelled commitments', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      const c = commitmentTracker.addCommitment(makeMention())!;
      commitmentTracker.cancelCommitment(c.id);
      expect(commitmentTracker.cancelCommitment(c.id)).toBe(false);
    });
  });

  describe('snoozeCommitment()', () => {
    it('should snooze an active commitment', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      const c = commitmentTracker.addCommitment(makeMention())!;
      const futureMs = Date.now() + 4 * 60 * 60 * 1000;
      const result = commitmentTracker.snoozeCommitment(c.id, futureMs);

      expect(result).toBe(true);
      expect(commitmentTracker.getCommitmentById(c.id)!.status).toBe('snoozed');
    });

    it('should not snooze completed commitments', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      const c = commitmentTracker.addCommitment(makeMention())!;
      commitmentTracker.completeCommitment(c.id);
      expect(commitmentTracker.snoozeCommitment(c.id, Date.now() + 3600000)).toBe(false);
    });
  });

  // ── Outbound Message Tracking ────────────────────────────────────

  describe('trackOutboundMessage()', () => {
    it('should track a new outbound message', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      const msg = commitmentTracker.trackOutboundMessage({
        recipient: 'Bob',
        channel: 'email',
        summary: 'Sent project proposal',
      });

      expect(msg.recipient).toBe('Bob');
      expect(msg.channel).toBe('email');
      expect(msg.replyReceived).toBe(false);
      expect(msg.expectedResponseHours).toBe(48); // email default
    });

    it('should use channel-specific baselines', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      const slack = commitmentTracker.trackOutboundMessage({
        recipient: 'Bob', channel: 'slack', summary: 'Quick question',
      });
      expect(slack.expectedResponseHours).toBe(4);

      const text = commitmentTracker.trackOutboundMessage({
        recipient: 'Bob', channel: 'text', summary: 'Hey',
      });
      expect(text.expectedResponseHours).toBe(2);
    });

    it('should use default baseline for unknown channels', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      const msg = commitmentTracker.trackOutboundMessage({
        recipient: 'Bob', channel: 'carrier_pigeon', summary: 'Coo',
      });
      expect(msg.expectedResponseHours).toBe(48); // default
    });

    it('should cap outbound messages at limit', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize({ maxOutboundMessages: 3 });

      for (let i = 0; i < 5; i++) {
        commitmentTracker.trackOutboundMessage({
          recipient: `Person${i}`, channel: 'email', summary: `msg ${i}`,
        });
      }

      expect(commitmentTracker.getUnrepliedMessages().length).toBeLessThanOrEqual(3);
    });

    it('should truncate long fields', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      const msg = commitmentTracker.trackOutboundMessage({
        recipient: 'x'.repeat(200),
        channel: 'y'.repeat(50),
        summary: 'z'.repeat(500),
      });

      expect(msg.recipient.length).toBeLessThanOrEqual(100);
      expect(msg.channel.length).toBeLessThanOrEqual(30);
      expect(msg.summary.length).toBeLessThanOrEqual(300);
    });
  });

  describe('recordReply()', () => {
    it('should record reply for matching outbound message', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      commitmentTracker.trackOutboundMessage({
        recipient: 'Bob', channel: 'email', summary: 'Hello',
      });

      const result = commitmentTracker.recordReply('Bob', 'email');
      expect(result).toBe(true);
      expect(commitmentTracker.getUnrepliedMessages()).toHaveLength(0);
    });

    it('should match recipient case-insensitively', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      commitmentTracker.trackOutboundMessage({
        recipient: 'Bob Smith', channel: 'slack', summary: 'Hey',
      });

      expect(commitmentTracker.recordReply('bob', 'slack')).toBe(true);
    });

    it('should return false when no matching message found', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      expect(commitmentTracker.recordReply('Nobody', 'email')).toBe(false);
    });

    it('should match most recent unreplied message first', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      const msg1 = commitmentTracker.trackOutboundMessage({
        recipient: 'Bob', channel: 'email', summary: 'First message',
      });
      vi.advanceTimersByTime(1000);
      const msg2 = commitmentTracker.trackOutboundMessage({
        recipient: 'Bob', channel: 'email', summary: 'Second message',
      });

      commitmentTracker.recordReply('Bob', 'email');
      // Most recent (msg2) should be matched
      const unreplied = commitmentTracker.getUnrepliedMessages();
      expect(unreplied).toHaveLength(1);
      expect(unreplied[0].summary).toBe('First message');
    });
  });

  // ── Follow-Up Suggestions ────────────────────────────────────────

  describe('generateFollowUpSuggestions()', () => {
    it('should generate unreplied message suggestion after expected time', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      commitmentTracker.trackOutboundMessage({
        recipient: 'Alice', channel: 'email', summary: 'Important proposal',
      });

      // Advance past 48h email baseline
      vi.advanceTimersByTime(49 * 60 * 60 * 1000);

      const suggestions = commitmentTracker.generateFollowUpSuggestions();
      expect(suggestions).toHaveLength(1);
      expect(suggestions[0].type).toBe('unreplied_message');
      expect(suggestions[0].personName).toBe('Alice');
      expect(suggestions[0].urgency).toBe('low'); // just past threshold
    });

    it('should not re-suggest for already suggested messages', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      commitmentTracker.trackOutboundMessage({
        recipient: 'Alice', channel: 'slack', summary: 'Question',
      });

      vi.advanceTimersByTime(5 * 60 * 60 * 1000); // past 4h slack baseline
      commitmentTracker.generateFollowUpSuggestions();

      vi.advanceTimersByTime(1 * 60 * 60 * 1000);
      const second = commitmentTracker.generateFollowUpSuggestions();
      expect(second).toHaveLength(0);
    });

    it('should generate approaching deadline suggestion', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      const deadlineIn12h = Date.now() + 12 * 60 * 60 * 1000;
      commitmentTracker.addCommitment(makeMention({ deadline: deadlineIn12h }));

      const suggestions = commitmentTracker.generateFollowUpSuggestions();
      expect(suggestions).toHaveLength(1);
      expect(suggestions[0].type).toBe('approaching_deadline');
      expect(suggestions[0].urgency).toBe('medium'); // > 4h
    });

    it('should mark approaching deadline as high urgency when <= 4h', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      const deadlineIn2h = Date.now() + 2 * 60 * 60 * 1000;
      commitmentTracker.addCommitment(makeMention({ deadline: deadlineIn2h }));

      const suggestions = commitmentTracker.generateFollowUpSuggestions();
      expect(suggestions[0].urgency).toBe('high');
    });

    it('should generate overdue commitment suggestion', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      // Create commitment that's already overdue via past deadline
      const pastDeadline = Date.now() - 2 * 60 * 60 * 1000;
      const saved = {
        commitments: [{
          id: 'overdue1', description: 'Deliver presentation', direction: 'user_promised',
          personName: 'Boss', source: 'meeting', status: 'overdue',
          createdAt: Date.now() - 72 * 60 * 60 * 1000, deadline: pastDeadline,
          domain: 'work', contextSnippet: '', confidence: 0.9, reminded: false,
          lastRemindedAt: null, resolvedAt: null, notes: '',
        }],
        outboundMessages: [],
        followUpSuggestions: [],
      };
      mockReadFile.mockResolvedValue(JSON.stringify(saved));

      const mod = await createTracker();
      await mod.commitmentTracker.initialize();

      const suggestions = mod.commitmentTracker.generateFollowUpSuggestions();
      expect(suggestions.some(s => s.type === 'overdue_commitment')).toBe(true);
    });

    it('should compute urgency levels correctly', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      // Slack message: 4h expected
      commitmentTracker.trackOutboundMessage({
        recipient: 'Alice', channel: 'slack', summary: 'Question',
      });

      // 4x past expected → critical
      vi.advanceTimersByTime(16 * 60 * 60 * 1000);
      const suggestions = commitmentTracker.generateFollowUpSuggestions();
      expect(suggestions[0].urgency).toBe('critical');
    });

    it('should limit overdue reminders to once per 24 hours', async () => {
      const { commitmentTracker } = await createTracker();

      const saved = {
        commitments: [{
          id: 'od1', description: 'Overdue thing', direction: 'user_promised',
          personName: 'Boss', source: 'conversation', status: 'overdue',
          createdAt: Date.now() - 5 * 24 * 60 * 60 * 1000,
          deadline: Date.now() - 3 * 24 * 60 * 60 * 1000,
          domain: '', contextSnippet: '', confidence: 0.9, reminded: false,
          lastRemindedAt: null, resolvedAt: null, notes: '',
        }],
        outboundMessages: [],
        followUpSuggestions: [],
      };
      mockReadFile.mockResolvedValue(JSON.stringify(saved));
      await commitmentTracker.initialize();

      const first = commitmentTracker.generateFollowUpSuggestions();
      expect(first).toHaveLength(1);

      // 12 hours later — should NOT remind again
      vi.advanceTimersByTime(12 * 60 * 60 * 1000);
      const second = commitmentTracker.generateFollowUpSuggestions();
      expect(second).toHaveLength(0);

      // 25 hours later from first — should remind again
      vi.advanceTimersByTime(13 * 60 * 60 * 1000);
      const third = commitmentTracker.generateFollowUpSuggestions();
      expect(third).toHaveLength(1);
    });

    it('should cap suggestions at 100', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      // Create 120 outbound messages then advance time to trigger suggestions
      for (let i = 0; i < 120; i++) {
        commitmentTracker.trackOutboundMessage({
          recipient: `Person${i}`, channel: 'text', summary: `msg ${i}`,
        });
      }

      vi.advanceTimersByTime(3 * 60 * 60 * 1000); // past text baseline (2h)
      commitmentTracker.generateFollowUpSuggestions();

      const pending = commitmentTracker.getPendingSuggestions();
      expect(pending.length).toBeLessThanOrEqual(100);
    });
  });

  describe('markSuggestionDelivered()', () => {
    it('should mark suggestion as delivered', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      commitmentTracker.trackOutboundMessage({
        recipient: 'Alice', channel: 'text', summary: 'Hey',
      });
      vi.advanceTimersByTime(3 * 60 * 60 * 1000);
      const [suggestion] = commitmentTracker.generateFollowUpSuggestions();

      expect(commitmentTracker.markSuggestionDelivered(suggestion.id)).toBe(true);
      expect(commitmentTracker.getPendingSuggestions()).toHaveLength(0);
    });

    it('should return false for non-existent suggestion', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();
      expect(commitmentTracker.markSuggestionDelivered('fake')).toBe(false);
    });
  });

  describe('markSuggestionActedOn()', () => {
    it('should mark suggestion as acted on', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      commitmentTracker.trackOutboundMessage({
        recipient: 'Alice', channel: 'text', summary: 'Hey',
      });
      vi.advanceTimersByTime(3 * 60 * 60 * 1000);
      const [suggestion] = commitmentTracker.generateFollowUpSuggestions();

      expect(commitmentTracker.markSuggestionActedOn(suggestion.id)).toBe(true);
    });
  });

  // ── Queries ──────────────────────────────────────────────────────

  describe('getActiveCommitments()', () => {
    it('should return active and overdue commitments', async () => {
      const { commitmentTracker } = await createTracker();
      const saved = {
        commitments: [
          { id: 'a1', status: 'active', personName: 'A', description: '', direction: 'user_promised', source: 'conversation', createdAt: Date.now(), deadline: null, domain: '', contextSnippet: '', confidence: 0.8, reminded: false, lastRemindedAt: null, resolvedAt: null, notes: '' },
          { id: 'a2', status: 'overdue', personName: 'B', description: '', direction: 'user_promised', source: 'conversation', createdAt: Date.now(), deadline: Date.now() - 1000, domain: '', contextSnippet: '', confidence: 0.8, reminded: false, lastRemindedAt: null, resolvedAt: null, notes: '' },
          { id: 'a3', status: 'completed', personName: 'C', description: '', direction: 'user_promised', source: 'conversation', createdAt: Date.now(), deadline: null, domain: '', contextSnippet: '', confidence: 0.8, reminded: false, lastRemindedAt: null, resolvedAt: Date.now(), notes: '' },
        ],
        outboundMessages: [],
        followUpSuggestions: [],
      };
      mockReadFile.mockResolvedValue(JSON.stringify(saved));
      await commitmentTracker.initialize();

      const active = commitmentTracker.getActiveCommitments();
      expect(active).toHaveLength(2);
      expect(active.map(c => c.id).sort()).toEqual(['a1', 'a2']);
    });
  });

  describe('getCommitmentsByPerson()', () => {
    it('should filter by person name (case-insensitive, partial match)', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      commitmentTracker.addCommitment(makeMention({ personName: 'Alice Smith', description: 'Task A' }));
      commitmentTracker.addCommitment(makeMention({ personName: 'Bob Jones', description: 'Task B' }));
      commitmentTracker.addCommitment(makeMention({ personName: 'Alice Brown', description: 'Task C' }));

      const aliceCommitments = commitmentTracker.getCommitmentsByPerson('alice');
      expect(aliceCommitments).toHaveLength(2);
    });
  });

  describe('getUpcomingDeadlines()', () => {
    it('should return commitments with deadlines within window', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      const in24h = Date.now() + 24 * 60 * 60 * 1000;
      const in96h = Date.now() + 96 * 60 * 60 * 1000;

      commitmentTracker.addCommitment(makeMention({ deadline: in24h, personName: 'A', description: 'Soon' }));
      commitmentTracker.addCommitment(makeMention({ deadline: in96h, personName: 'B', description: 'Later' }));
      commitmentTracker.addCommitment(makeMention({ deadline: null, personName: 'C', description: 'No deadline' }));

      const upcoming48 = commitmentTracker.getUpcomingDeadlines(48);
      expect(upcoming48).toHaveLength(1);
      expect(upcoming48[0].description).toBe('Soon');

      const upcoming120 = commitmentTracker.getUpcomingDeadlines(120);
      expect(upcoming120).toHaveLength(2);
    });

    it('should sort by deadline ascending', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      commitmentTracker.addCommitment(makeMention({ deadline: Date.now() + 48 * 3600000, personName: 'Later', description: 'Task B' }));
      commitmentTracker.addCommitment(makeMention({ deadline: Date.now() + 12 * 3600000, personName: 'Sooner', description: 'Task A' }));

      const upcoming = commitmentTracker.getUpcomingDeadlines(72);
      expect(upcoming[0].description).toBe('Task A');
      expect(upcoming[1].description).toBe('Task B');
    });
  });

  describe('getCommitmentById()', () => {
    it('should return commitment by ID', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      const c = commitmentTracker.addCommitment(makeMention())!;
      expect(commitmentTracker.getCommitmentById(c.id)).toEqual(c);
    });

    it('should return null for unknown ID', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();
      expect(commitmentTracker.getCommitmentById('unknown')).toBeNull();
    });
  });

  describe('getStatus()', () => {
    it('should return accurate status counts', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      commitmentTracker.addCommitment(makeMention({ personName: 'A', description: 'Active1' }));
      commitmentTracker.addCommitment(makeMention({ personName: 'B', description: 'Active2' }));
      const c3 = commitmentTracker.addCommitment(makeMention({ personName: 'C', description: 'Done' }))!;
      commitmentTracker.completeCommitment(c3.id);

      commitmentTracker.trackOutboundMessage({ recipient: 'D', channel: 'email', summary: 'msg' });

      const status = commitmentTracker.getStatus();
      expect(status.activeCommitments).toBe(2);
      expect(status.overdueCommitments).toBe(0);
      expect(status.trackedOutboundMessages).toBe(1);
      expect(status.totalCommitmentsTracked).toBe(3);
    });
  });

  // ── Context Generation ───────────────────────────────────────────

  describe('getContextString()', () => {
    it('should return empty string when nothing to report', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();
      expect(commitmentTracker.getContextString()).toBe('');
    });

    it('should include overdue items', async () => {
      const { commitmentTracker } = await createTracker();
      const saved = {
        commitments: [{
          id: 'od1', description: 'File the report', direction: 'user_promised',
          personName: 'Boss', source: 'conversation', status: 'overdue',
          createdAt: Date.now() - 5 * 24 * 3600000,
          deadline: Date.now() - 2 * 24 * 3600000,
          domain: '', contextSnippet: '', confidence: 0.9, reminded: false,
          lastRemindedAt: null, resolvedAt: null, notes: '',
        }],
        outboundMessages: [],
        followUpSuggestions: [],
      };
      mockReadFile.mockResolvedValue(JSON.stringify(saved));
      await commitmentTracker.initialize();

      const ctx = commitmentTracker.getContextString();
      expect(ctx).toContain('OVERDUE');
      expect(ctx).toContain('File the report');
      expect(ctx).toContain('You promised');
    });

    it('should include upcoming deadlines', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      commitmentTracker.addCommitment(makeMention({
        deadline: Date.now() + 12 * 3600000,
        description: 'Finish the slides',
      }));

      const ctx = commitmentTracker.getContextString();
      expect(ctx).toContain('UPCOMING');
      expect(ctx).toContain('Finish the slides');
    });

    it('should include unreplied message info', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      commitmentTracker.trackOutboundMessage({
        recipient: 'CEO', channel: 'email', summary: 'Budget request',
      });

      // Advance past expected response time
      vi.advanceTimersByTime(49 * 3600000);

      const ctx = commitmentTracker.getContextString();
      expect(ctx).toContain('AWAITING REPLY');
      expect(ctx).toContain('CEO');
    });

    it('should show other_promised direction correctly', async () => {
      const { commitmentTracker } = await createTracker();
      const saved = {
        commitments: [{
          id: 'op1', description: 'Review my PR', direction: 'other_promised',
          personName: 'DevTeam', source: 'conversation', status: 'overdue',
          createdAt: Date.now() - 3 * 24 * 3600000,
          deadline: Date.now() - 1 * 24 * 3600000,
          domain: '', contextSnippet: '', confidence: 0.9, reminded: false,
          lastRemindedAt: null, resolvedAt: null, notes: '',
        }],
        outboundMessages: [],
        followUpSuggestions: [],
      };
      mockReadFile.mockResolvedValue(JSON.stringify(saved));
      await commitmentTracker.initialize();

      const ctx = commitmentTracker.getContextString();
      expect(ctx).toContain('DevTeam promised');
    });
  });

  describe('getPromptContext()', () => {
    it('should return empty string when nothing to report', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();
      expect(commitmentTracker.getPromptContext()).toBe('');
    });

    it('should return compact status string', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      commitmentTracker.addCommitment(makeMention({ personName: 'A', description: 'Task1' }));
      commitmentTracker.trackOutboundMessage({ recipient: 'B', channel: 'email', summary: 'msg' });

      const ctx = commitmentTracker.getPromptContext();
      expect(ctx).toContain('Commitments:');
      expect(ctx).toContain('1 active commitments');
      expect(ctx).toContain('1 awaiting reply');
    });
  });

  // ── Pruning ──────────────────────────────────────────────────────

  describe('pruning', () => {
    it('should prune resolved commitments past retention period', async () => {
      const { commitmentTracker } = await createTracker();

      const oldResolved = Date.now() - 100 * 24 * 3600000; // 100 days ago
      const saved = {
        commitments: [
          {
            id: 'old1', description: 'Old completed', direction: 'user_promised',
            personName: 'X', source: 'conversation', status: 'completed',
            createdAt: oldResolved - 10 * 24 * 3600000,
            deadline: null, domain: '', contextSnippet: '', confidence: 0.8,
            reminded: false, lastRemindedAt: null,
            resolvedAt: oldResolved, notes: '',
          },
          {
            id: 'recent1', description: 'Recent completed', direction: 'user_promised',
            personName: 'Y', source: 'conversation', status: 'completed',
            createdAt: Date.now() - 5 * 24 * 3600000,
            deadline: null, domain: '', contextSnippet: '', confidence: 0.8,
            reminded: false, lastRemindedAt: null,
            resolvedAt: Date.now() - 5 * 24 * 3600000, notes: '',
          },
        ],
        outboundMessages: [],
        followUpSuggestions: [],
      };
      mockReadFile.mockResolvedValue(JSON.stringify(saved));
      await commitmentTracker.initialize();

      // 'old1' should be pruned (100d > 90d retention)
      expect(commitmentTracker.getAllCommitments()).toHaveLength(1);
      expect(commitmentTracker.getAllCommitments()[0].id).toBe('recent1');
    });

    it('should not prune active/overdue commitments regardless of age', async () => {
      const { commitmentTracker } = await createTracker();
      const saved = {
        commitments: [{
          id: 'ancient', description: 'Very old active', direction: 'user_promised',
          personName: 'Z', source: 'conversation', status: 'active',
          createdAt: Date.now() - 200 * 24 * 3600000, deadline: null,
          domain: '', contextSnippet: '', confidence: 0.8, reminded: false,
          lastRemindedAt: null, resolvedAt: null, notes: '',
        }],
        outboundMessages: [],
        followUpSuggestions: [],
      };
      mockReadFile.mockResolvedValue(JSON.stringify(saved));
      await commitmentTracker.initialize();

      expect(commitmentTracker.getAllCommitments()).toHaveLength(1);
    });

    it('should prune old delivered suggestions after 7 days', async () => {
      const { commitmentTracker } = await createTracker();
      const saved = {
        commitments: [],
        outboundMessages: [],
        followUpSuggestions: [
          {
            id: 's1', relatedId: 'x', type: 'unreplied_message',
            personName: 'A', suggestedAction: 'Follow up',
            urgency: 'low', createdAt: Date.now() - 10 * 24 * 3600000,
            delivered: true, actedOn: false,
          },
          {
            id: 's2', relatedId: 'y', type: 'unreplied_message',
            personName: 'B', suggestedAction: 'Follow up',
            urgency: 'low', createdAt: Date.now() - 2 * 24 * 3600000,
            delivered: true, actedOn: false,
          },
          {
            id: 's3', relatedId: 'z', type: 'unreplied_message',
            personName: 'C', suggestedAction: 'Follow up',
            urgency: 'low', createdAt: Date.now() - 10 * 24 * 3600000,
            delivered: false, actedOn: false,
          },
        ],
      };
      mockReadFile.mockResolvedValue(JSON.stringify(saved));
      await commitmentTracker.initialize();

      const pending = commitmentTracker.getPendingSuggestions();
      // s1 should be pruned (delivered + > 7d old)
      // s2 should remain (delivered but only 2d old)
      // s3 should remain (not delivered)
      expect(pending).toHaveLength(1);
      expect(pending[0].id).toBe('s3');
    });

    it('should prune old replied outbound messages after 30 days', async () => {
      const { commitmentTracker } = await createTracker();
      const saved = {
        commitments: [],
        outboundMessages: [
          {
            id: 'm1', recipient: 'A', channel: 'email', summary: 'Old',
            sentAt: Date.now() - 35 * 24 * 3600000, replyReceived: true,
            replyReceivedAt: Date.now() - 34 * 24 * 3600000,
            expectedResponseHours: 48, followUpSuggested: false,
          },
          {
            id: 'm2', recipient: 'B', channel: 'email', summary: 'Recent',
            sentAt: Date.now() - 5 * 24 * 3600000, replyReceived: true,
            replyReceivedAt: Date.now() - 4 * 24 * 3600000,
            expectedResponseHours: 48, followUpSuggested: false,
          },
          {
            id: 'm3', recipient: 'C', channel: 'email', summary: 'Unreplied',
            sentAt: Date.now() - 35 * 24 * 3600000, replyReceived: false,
            replyReceivedAt: null, expectedResponseHours: 48, followUpSuggested: false,
          },
        ],
        followUpSuggestions: [],
      };
      mockReadFile.mockResolvedValue(JSON.stringify(saved));
      await commitmentTracker.initialize();

      const unreplied = commitmentTracker.getUnrepliedMessages();
      // m1 should be pruned (replied + > 30d)
      // m2 should remain (replied but only 5d)
      // m3 should remain (unreplied)
      expect(unreplied).toHaveLength(1);
      expect(unreplied[0].id).toBe('m3');
    });
  });

  // ── Enforce Limit ────────────────────────────────────────────────

  describe('enforceLimit()', () => {
    it('should remove oldest resolved when over limit', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize({ maxCommitments: 3 });

      // Add 3 and complete first
      const c1 = commitmentTracker.addCommitment(makeMention({ personName: 'A', description: 'First' }))!;
      commitmentTracker.addCommitment(makeMention({ personName: 'B', description: 'Second' }));
      commitmentTracker.completeCommitment(c1.id);

      vi.advanceTimersByTime(2 * 3600000); // past dedup window
      commitmentTracker.addCommitment(makeMention({ personName: 'C', description: 'Third' }));

      vi.advanceTimersByTime(2 * 3600000);
      commitmentTracker.addCommitment(makeMention({ personName: 'D', description: 'Fourth' }));

      // Should have evicted the completed one first
      const all = commitmentTracker.getAllCommitments();
      expect(all.length).toBeLessThanOrEqual(3);
      expect(all.find(c => c.id === c1.id)).toBeUndefined();
    });
  });

  // ── Persistence ──────────────────────────────────────────────────

  describe('persistence', () => {
    it('should debounce saves (2 second quiet period)', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      commitmentTracker.addCommitment(makeMention({ personName: 'A', description: 'T1' }));
      commitmentTracker.addCommitment(makeMention({ personName: 'B', description: 'T2' }));
      commitmentTracker.addCommitment(makeMention({ personName: 'C', description: 'T3' }));

      // Before debounce fires
      vi.advanceTimersByTime(1000);
      expect(mockWriteFile).not.toHaveBeenCalled();

      // After debounce
      vi.advanceTimersByTime(1500);
      expect(mockWriteFile).toHaveBeenCalledTimes(1);
    });

    it('should save all three data arrays', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      commitmentTracker.addCommitment(makeMention());
      commitmentTracker.trackOutboundMessage({ recipient: 'X', channel: 'email', summary: 'y' });

      vi.advanceTimersByTime(3000);

      const writeCall = mockWriteFile.mock.calls[0];
      const data = JSON.parse(writeCall[1]);
      expect(data).toHaveProperty('commitments');
      expect(data).toHaveProperty('outboundMessages');
      expect(data).toHaveProperty('followUpSuggestions');
      expect(data.commitments).toHaveLength(1);
      expect(data.outboundMessages).toHaveLength(1);
    });
  });

  // ── Learned Response Baselines ───────────────────────────────────

  describe('per-contact response baselines', () => {
    it('should use learned baseline when enough history exists', async () => {
      const { commitmentTracker } = await createTracker();

      // Pre-load historical replied messages
      const history: any[] = [];
      for (let i = 0; i < 5; i++) {
        history.push({
          id: `h${i}`, recipient: 'Alice', channel: 'email',
          summary: `msg ${i}`,
          sentAt: Date.now() - (20 + i) * 24 * 3600000,
          replyReceived: true,
          replyReceivedAt: Date.now() - (20 + i) * 24 * 3600000 + 6 * 3600000, // 6h reply time
          expectedResponseHours: 48, followUpSuggested: false,
        });
      }

      mockReadFile.mockResolvedValue(JSON.stringify({
        commitments: [],
        outboundMessages: history,
        followUpSuggestions: [],
      }));
      await commitmentTracker.initialize();

      // Now track a new message to Alice — should use learned baseline (~6h p80)
      const msg = commitmentTracker.trackOutboundMessage({
        recipient: 'Alice', channel: 'email', summary: 'New question',
      });

      // With 5 identical 6h response times, p80 should be 6
      expect(msg.expectedResponseHours).toBe(6);
    });

    it('should fall back to channel default with < 3 data points', async () => {
      const { commitmentTracker } = await createTracker();
      const history = [
        {
          id: 'h1', recipient: 'NewPerson', channel: 'slack',
          summary: 'Hey', sentAt: Date.now() - 10 * 24 * 3600000,
          replyReceived: true, replyReceivedAt: Date.now() - 10 * 24 * 3600000 + 3600000,
          expectedResponseHours: 4, followUpSuggested: false,
        },
      ];
      mockReadFile.mockResolvedValue(JSON.stringify({
        commitments: [], outboundMessages: history, followUpSuggestions: [],
      }));
      await commitmentTracker.initialize();

      const msg = commitmentTracker.trackOutboundMessage({
        recipient: 'NewPerson', channel: 'slack', summary: 'Question',
      });
      expect(msg.expectedResponseHours).toBe(4); // slack default, not learned
    });
  });

  // ── cLaw Gate: Read-Only Assertions ──────────────────────────────

  describe('cLaw Gate compliance', () => {
    it('follow-up suggestions are data objects only (no actions)', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      commitmentTracker.trackOutboundMessage({
        recipient: 'Alice', channel: 'text', summary: 'Hey',
      });
      vi.advanceTimersByTime(3 * 3600000);

      const suggestions = commitmentTracker.generateFollowUpSuggestions();
      for (const s of suggestions) {
        // Suggestions contain only data, no executable callbacks
        expect(typeof s.suggestedAction).toBe('string');
        expect(s).not.toHaveProperty('execute');
        expect(s).not.toHaveProperty('run');
        expect(s).not.toHaveProperty('send');
        expect(s).not.toHaveProperty('callback');
      }
    });

    it('context string contains only informational text', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      commitmentTracker.addCommitment(makeMention({ deadline: Date.now() + 6 * 3600000 }));
      const ctx = commitmentTracker.getContextString();

      // Should not contain any action verbs directed at the system
      expect(ctx).not.toContain('SEND');
      expect(ctx).not.toContain('EXECUTE');
      expect(ctx).not.toContain('AUTO');
    });
  });

  // ── Edge Cases ───────────────────────────────────────────────────

  describe('edge cases', () => {
    it('should handle empty mentions array', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();
      expect(commitmentTracker.processCommitmentMentions([])).toHaveLength(0);
    });

    it('should handle commitments with no deadline', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      const c = commitmentTracker.addCommitment(makeMention({ deadline: null }));
      expect(c).not.toBeNull();
      expect(c!.deadline).toBeNull();
    });

    it('should not crash on suggestions with no active data', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();
      expect(commitmentTracker.generateFollowUpSuggestions()).toHaveLength(0);
    });

    it('getAllCommitments returns a copy, not the internal array', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();
      commitmentTracker.addCommitment(makeMention());

      const all = commitmentTracker.getAllCommitments();
      all.push({ id: 'injected' } as any);
      expect(commitmentTracker.getAllCommitments()).toHaveLength(1);
    });

    it('getConfig returns a copy', async () => {
      const { commitmentTracker } = await createTracker();
      await commitmentTracker.initialize();

      const config = commitmentTracker.getConfig();
      config.maxCommitments = 9999;
      expect(commitmentTracker.getConfig().maxCommitments).toBe(200);
    });
  });
});
