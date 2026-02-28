/**
 * Outbound Intelligence — Tests for Track VI Phase 2.
 *
 * Validates:
 *   1. Channel selection heuristics (frequency, recency, formality, inbound preference)
 *   2. Channel normalization (raw strings → OutboundChannel)
 *   3. Message type inference (tone × priority → formal/casual/urgent/informational)
 *   4. Priority detection from natural language
 *   5. Tone detection with style profile fallback
 *   6. Standing permission evaluation (active, expired, channel, priority)
 *   7. Draft CRUD (create, read, edit, delete)
 *   8. Draft approval workflow (approve, reject, approve-all)
 *   9. Sending pipeline (cLaw compliance, status transitions, error handling)
 *  10. Batch review assembly
 *  11. Style profile management (create, update, retrieve)
 *  12. Standing permission lifecycle (add, revoke, delete, get)
 *  13. Auto-approve via standing permission
 *  14. Draft expiry
 *  15. Max drafts enforcement
 *  16. Stats reporting
 *  17. Config management
 *  18. Context generation for prompt injection
 *  19. cLaw safety gate — no send without approval
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  scoreChannels,
  normalizeChannel,
  inferMessageType,
  detectPriority,
  detectTone,
  checkStandingPermission,
  type OutboundDraft,
  type StandingPermission,
  type OutboundChannel,
  type MessagePriority,
  type TonePreset,
  type RecipientStyleProfile,
} from '../../src/main/outbound-intelligence';

// ── Mock Electron + fs so the module can load without runtime ──

vi.mock('electron', () => ({
  app: { getPath: () => '/tmp/test-outbound' },
}));

vi.mock('fs/promises', () => ({
  default: {
    readFile: vi.fn().mockRejectedValue(new Error('no file')),
    writeFile: vi.fn().mockResolvedValue(undefined),
  },
}));

// ═══════════════════════════════════════════════════════════════════
// 1. CHANNEL SELECTION HEURISTICS
// ═══════════════════════════════════════════════════════════════════

describe('Channel Selection Heuristics', () => {
  it('should return scores for all available channels', () => {
    const channels: OutboundChannel[] = ['email', 'slack', 'telegram'];
    const scores = scoreChannels(null, 'formal', channels);
    expect(scores).toHaveLength(3);
    for (const s of scores) {
      expect(s.score).toBeGreaterThanOrEqual(0);
      expect(s.score).toBeLessThanOrEqual(1);
      expect(s.reason).toBeTruthy();
    }
  });

  it('should return scores sorted descending', () => {
    const channels: OutboundChannel[] = ['email', 'slack', 'telegram', 'discord'];
    const scores = scoreChannels(null, 'formal', channels);
    for (let i = 0; i < scores.length - 1; i++) {
      expect(scores[i].score).toBeGreaterThanOrEqual(scores[i + 1].score);
    }
  });

  it('should favor formal channels for formal messages', () => {
    const channels: OutboundChannel[] = ['email', 'slack', 'discord'];
    const scores = scoreChannels(null, 'formal', channels);
    const emailScore = scores.find((s) => s.channel === 'email')!;
    const discordScore = scores.find((s) => s.channel === 'discord')!;
    expect(emailScore.score).toBeGreaterThan(discordScore.score);
  });

  it('should favor instant channels for urgent messages', () => {
    const channels: OutboundChannel[] = ['email', 'telegram', 'sms'];
    const scores = scoreChannels(null, 'urgent', channels);
    const telegramScore = scores.find((s) => s.channel === 'telegram')!;
    const emailScore = scores.find((s) => s.channel === 'email')!;
    expect(telegramScore.score).toBeGreaterThan(emailScore.score);
  });

  it('should handle empty available channels', () => {
    const scores = scoreChannels(null, 'casual', []);
    expect(scores).toHaveLength(0);
  });

  it('should clamp scores between 0 and 1', () => {
    const channels: OutboundChannel[] = ['email', 'slack', 'telegram', 'discord', 'teams', 'sms', 'in-app'];
    const scores = scoreChannels(null, 'formal', channels);
    for (const s of scores) {
      expect(s.score).toBeGreaterThanOrEqual(0);
      expect(s.score).toBeLessThanOrEqual(1);
    }
  });

  it('should deduct score for channels with no history', () => {
    const channels: OutboundChannel[] = ['email'];
    const scores = scoreChannels(null, 'informational', channels);
    // No person ID means no history — should still produce a score
    expect(scores[0].reason).toContain('no history');
  });
});

// ═══════════════════════════════════════════════════════════════════
// 2. CHANNEL NORMALIZATION
// ═══════════════════════════════════════════════════════════════════

describe('Channel Normalization', () => {
  it('should normalize email variants', () => {
    expect(normalizeChannel('email')).toBe('email');
    expect(normalizeChannel('Email')).toBe('email');
    expect(normalizeChannel('gmail')).toBe('email');
    expect(normalizeChannel('smtp')).toBe('email');
  });

  it('should normalize messaging platforms', () => {
    expect(normalizeChannel('slack')).toBe('slack');
    expect(normalizeChannel('Slack')).toBe('slack');
    expect(normalizeChannel('discord')).toBe('discord');
    expect(normalizeChannel('Discord Chat')).toBe('discord');
    expect(normalizeChannel('telegram')).toBe('telegram');
    expect(normalizeChannel('Telegram Bot')).toBe('telegram');
    expect(normalizeChannel('teams')).toBe('teams');
    expect(normalizeChannel('Microsoft Teams')).toBe('teams');
  });

  it('should normalize SMS variants', () => {
    expect(normalizeChannel('sms')).toBe('sms');
    expect(normalizeChannel('text')).toBe('sms');
    expect(normalizeChannel('phone')).toBe('sms');
    expect(normalizeChannel('text message')).toBe('sms');
  });

  it('should default to in-app for unknown channels', () => {
    expect(normalizeChannel('carrier pigeon')).toBe('in-app');
    expect(normalizeChannel('fax')).toBe('in-app');
    expect(normalizeChannel('')).toBe('in-app');
  });

  it('should handle whitespace', () => {
    expect(normalizeChannel('  slack  ')).toBe('slack');
    expect(normalizeChannel('\temail\n')).toBe('email');
  });
});

// ═══════════════════════════════════════════════════════════════════
// 3. MESSAGE TYPE INFERENCE
// ═══════════════════════════════════════════════════════════════════

describe('Message Type Inference', () => {
  it('should return urgent for urgent priority regardless of tone', () => {
    expect(inferMessageType('casual', 'urgent')).toBe('urgent');
    expect(inferMessageType('formal', 'urgent')).toBe('urgent');
    expect(inferMessageType('friendly', 'urgent')).toBe('urgent');
  });

  it('should return formal for formal/professional tones', () => {
    expect(inferMessageType('formal', 'normal')).toBe('formal');
    expect(inferMessageType('professional', 'normal')).toBe('formal');
    expect(inferMessageType('formal', 'high')).toBe('formal');
  });

  it('should return casual for casual/friendly tones', () => {
    expect(inferMessageType('casual', 'normal')).toBe('casual');
    expect(inferMessageType('friendly', 'normal')).toBe('casual');
    expect(inferMessageType('casual', 'low')).toBe('casual');
  });

  it('should return informational for other combinations', () => {
    expect(inferMessageType('direct', 'normal')).toBe('informational');
    expect(inferMessageType('empathetic', 'low')).toBe('informational');
  });
});

// ═══════════════════════════════════════════════════════════════════
// 4. PRIORITY DETECTION
// ═══════════════════════════════════════════════════════════════════

describe('Priority Detection', () => {
  it('should detect urgent keywords', () => {
    expect(detectPriority('This is urgent!')).toBe('urgent');
    expect(detectPriority('Do this ASAP')).toBe('urgent');
    expect(detectPriority('Emergency situation')).toBe('urgent');
    expect(detectPriority('Need this immediately')).toBe('urgent');
    expect(detectPriority('Critical issue')).toBe('urgent');
  });

  it('should detect high priority keywords', () => {
    expect(detectPriority('This is important')).toBe('high');
    expect(detectPriority('High priority task')).toBe('high');
    expect(detectPriority('Deadline approaching')).toBe('high');
    expect(detectPriority('Need it by today')).toBe('high');
    expect(detectPriority('Complete by tomorrow')).toBe('high');
  });

  it('should detect low priority keywords', () => {
    expect(detectPriority('No rush on this')).toBe('low');
    expect(detectPriority('Whenever you get a chance')).toBe('low');
    expect(detectPriority('when you get a chance send it')).toBe('low');
  });

  it('should default to normal for ambiguous context', () => {
    expect(detectPriority('Please send the report')).toBe('normal');
    expect(detectPriority('Hey, just checking in')).toBe('normal');
    expect(detectPriority('')).toBe('normal');
  });
});

// ═══════════════════════════════════════════════════════════════════
// 5. TONE DETECTION
// ═══════════════════════════════════════════════════════════════════

describe('Tone Detection', () => {
  it('should use style profile tone when available', () => {
    const profile: RecipientStyleProfile = {
      recipientPersonId: 'p1',
      recipientName: 'Test',
      preferredTone: 'casual',
      avgLength: 200,
      usesGreeting: true,
      usesSignOff: false,
      signOff: '',
      preferredChannel: null,
      observationCount: 5,
      updatedAt: Date.now(),
    };
    expect(detectTone('formal request', profile)).toBe('casual');
  });

  it('should detect formal tone keywords', () => {
    expect(detectTone('formal notice', null)).toBe('formal');
    expect(detectTone('Dear colleague', null)).toBe('formal');
    expect(detectTone('official communication', null)).toBe('formal');
  });

  it('should detect casual tone keywords', () => {
    expect(detectTone('hey there', null)).toBe('casual');
    expect(detectTone('keep it casual', null)).toBe('casual');
    expect(detectTone('sup dude', null)).toBe('casual');
  });

  it('should detect urgent tone keywords', () => {
    expect(detectTone('this is urgent please', null)).toBe('urgent');
    expect(detectTone('ASAP response needed', null)).toBe('urgent');
  });

  it('should detect empathetic tone keywords', () => {
    expect(detectTone("sorry for your loss, tough time", null)).toBe('empathetic');
    expect(detectTone("I sympathize with the situation", null)).toBe('empathetic');
  });

  it('should detect direct tone keywords', () => {
    expect(detectTone('be direct and blunt', null)).toBe('direct');
    expect(detectTone('give it to me straight', null)).toBe('direct');
  });

  it('should default to professional', () => {
    expect(detectTone('send the report', null)).toBe('professional');
    expect(detectTone('', null)).toBe('professional');
  });
});

// ═══════════════════════════════════════════════════════════════════
// 6. STANDING PERMISSION EVALUATION
// ═══════════════════════════════════════════════════════════════════

describe('Standing Permission Evaluation', () => {
  const makeDraft = (overrides: Partial<OutboundDraft> = {}): OutboundDraft => ({
    id: 'draft-1',
    recipientName: 'Alice',
    recipientPersonId: 'person-1',
    channel: 'email',
    channelAddress: 'alice@co.com',
    subject: 'Test',
    body: 'Hello',
    tone: 'professional',
    priority: 'normal',
    trigger: 'user-request',
    status: 'pending',
    channelReason: 'test',
    commitmentId: null,
    context: 'test context',
    createdAt: Date.now(),
    approvedAt: null,
    sentAt: null,
    sendError: null,
    editCount: 0,
    ...overrides,
  });

  const makePerm = (overrides: Partial<StandingPermission> = {}): StandingPermission => ({
    id: 'perm-1',
    recipientPersonId: 'person-1',
    recipientName: 'Alice',
    channels: ['email', 'slack'],
    maxPriority: 'normal',
    active: true,
    createdAt: Date.now() - 86400000,
    expiresAt: null,
    ...overrides,
  });

  it('should match when all criteria align', () => {
    const draft = makeDraft();
    const perms = [makePerm()];
    expect(checkStandingPermission(draft, perms)).toBe('perm-1');
  });

  it('should reject inactive permissions', () => {
    const draft = makeDraft();
    const perms = [makePerm({ active: false })];
    expect(checkStandingPermission(draft, perms)).toBeNull();
  });

  it('should reject expired permissions', () => {
    const draft = makeDraft();
    const perms = [makePerm({ expiresAt: Date.now() - 1000 })];
    expect(checkStandingPermission(draft, perms)).toBeNull();
  });

  it('should accept non-expired future permissions', () => {
    const draft = makeDraft();
    const perms = [makePerm({ expiresAt: Date.now() + 86400000 })];
    expect(checkStandingPermission(draft, perms)).toBe('perm-1');
  });

  it('should reject mismatched person ID', () => {
    const draft = makeDraft({ recipientPersonId: 'person-2' });
    const perms = [makePerm()];
    expect(checkStandingPermission(draft, perms)).toBeNull();
  });

  it('should reject channel not in permission list', () => {
    const draft = makeDraft({ channel: 'telegram' });
    const perms = [makePerm({ channels: ['email', 'slack'] })];
    expect(checkStandingPermission(draft, perms)).toBeNull();
  });

  it('should reject when priority exceeds maxPriority', () => {
    const draft = makeDraft({ priority: 'urgent' });
    const perms = [makePerm({ maxPriority: 'normal' })];
    expect(checkStandingPermission(draft, perms)).toBeNull();
  });

  it('should accept when priority is at or below maxPriority', () => {
    const draft = makeDraft({ priority: 'low' });
    const perms = [makePerm({ maxPriority: 'normal' })];
    expect(checkStandingPermission(draft, perms)).toBe('perm-1');
  });

  it('should accept exact priority match', () => {
    const draft = makeDraft({ priority: 'high' });
    const perms = [makePerm({ maxPriority: 'high' })];
    expect(checkStandingPermission(draft, perms)).toBe('perm-1');
  });

  it('should return first matching permission', () => {
    const draft = makeDraft();
    const perms = [
      makePerm({ id: 'perm-A' }),
      makePerm({ id: 'perm-B' }),
    ];
    expect(checkStandingPermission(draft, perms)).toBe('perm-A');
  });

  it('should skip non-matching and find later match', () => {
    const draft = makeDraft({ channel: 'telegram' });
    const perms = [
      makePerm({ id: 'perm-A', channels: ['email'] }),
      makePerm({ id: 'perm-B', channels: ['telegram', 'slack'] }),
    ];
    expect(checkStandingPermission(draft, perms)).toBe('perm-B');
  });

  it('should return null with empty permissions', () => {
    const draft = makeDraft();
    expect(checkStandingPermission(draft, [])).toBeNull();
  });

  it('should reject draft with null recipientPersonId', () => {
    const draft = makeDraft({ recipientPersonId: null });
    const perms = [makePerm()];
    expect(checkStandingPermission(draft, perms)).toBeNull();
  });
});

// ═══════════════════════════════════════════════════════════════════
// 7-19. OUTBOUND INTELLIGENCE ENGINE (instance tests)
// ═══════════════════════════════════════════════════════════════════

describe('OutboundIntelligence Engine', () => {
  // We import the singleton and work with it directly.
  // Since the mock prevents actual file I/O, we can test in isolation.
  let engine: typeof import('../../src/main/outbound-intelligence').outboundIntelligence;

  beforeEach(async () => {
    // Re-import fresh each time
    vi.resetModules();
    vi.mock('electron', () => ({
      app: { getPath: () => '/tmp/test-outbound' },
    }));
    vi.mock('fs/promises', () => ({
      default: {
        readFile: vi.fn().mockRejectedValue(new Error('no file')),
        writeFile: vi.fn().mockResolvedValue(undefined),
      },
    }));
    const mod = await import('../../src/main/outbound-intelligence');
    engine = mod.outboundIntelligence;
    await engine.initialize();
  });

  afterEach(() => {
    engine.stop();
  });

  // ── Draft Creation ──────────────────────────────────────────────

  describe('Draft Creation', () => {
    it('should create a draft with all fields populated', () => {
      const draft = engine.createDraft({
        recipientName: 'Bob',
        body: 'Hello Bob!',
        context: 'just checking in',
      });
      expect(draft.id).toBeTruthy();
      expect(draft.recipientName).toBe('Bob');
      expect(draft.body).toBe('Hello Bob!');
      expect(draft.status).toBe('pending');
      expect(draft.channel).toBeTruthy();
      expect(draft.tone).toBeTruthy();
      expect(draft.priority).toBe('normal');
      expect(draft.trigger).toBe('user-request');
      expect(draft.createdAt).toBeGreaterThan(0);
      expect(draft.approvedAt).toBeNull();
      expect(draft.sentAt).toBeNull();
      expect(draft.sendError).toBeNull();
      expect(draft.editCount).toBe(0);
    });

    it('should respect explicit channel', () => {
      const draft = engine.createDraft({
        recipientName: 'Bob',
        body: 'test',
        context: 'test',
        channel: 'telegram',
      });
      expect(draft.channel).toBe('telegram');
      expect(draft.channelReason).toBe('user specified');
    });

    it('should respect explicit tone', () => {
      const draft = engine.createDraft({
        recipientName: 'Bob',
        body: 'test',
        context: 'test',
        tone: 'empathetic',
      });
      expect(draft.tone).toBe('empathetic');
    });

    it('should respect explicit priority', () => {
      const draft = engine.createDraft({
        recipientName: 'Bob',
        body: 'test',
        context: 'test',
        priority: 'urgent',
      });
      expect(draft.priority).toBe('urgent');
    });

    it('should detect priority from context when not specified', () => {
      const draft = engine.createDraft({
        recipientName: 'Bob',
        body: 'test',
        context: 'This is urgent - need response ASAP',
      });
      expect(draft.priority).toBe('urgent');
    });

    it('should set trigger correctly', () => {
      const draft = engine.createDraft({
        recipientName: 'Bob',
        body: 'test',
        context: 'test',
        trigger: 'follow-up',
      });
      expect(draft.trigger).toBe('follow-up');
    });

    it('should set commitmentId when provided', () => {
      const draft = engine.createDraft({
        recipientName: 'Bob',
        body: 'test',
        context: 'test',
        commitmentId: 'commit-123',
      });
      expect(draft.commitmentId).toBe('commit-123');
    });

    it('should set subject when provided', () => {
      const draft = engine.createDraft({
        recipientName: 'Bob',
        body: 'test',
        context: 'test',
        subject: 'Important Update',
      });
      expect(draft.subject).toBe('Important Update');
    });
  });

  // ── Draft Retrieval ─────────────────────────────────────────────

  describe('Draft Retrieval', () => {
    it('should get a draft by ID', () => {
      const created = engine.createDraft({
        recipientName: 'Alice',
        body: 'hi',
        context: 'test',
      });
      const retrieved = engine.getDraft(created.id);
      expect(retrieved).not.toBeNull();
      expect(retrieved!.id).toBe(created.id);
    });

    it('should return null for nonexistent draft', () => {
      expect(engine.getDraft('nonexistent')).toBeNull();
    });

    it('should get pending drafts sorted by priority', () => {
      engine.createDraft({ recipientName: 'A', body: 'a', context: 'low priority' });
      engine.createDraft({ recipientName: 'B', body: 'b', context: 'asap urgent' });
      engine.createDraft({ recipientName: 'C', body: 'c', context: 'normal request' });

      const pending = engine.getPendingDrafts();
      expect(pending.length).toBeGreaterThanOrEqual(3);
      // Urgent should be first
      const priorities = pending.map((d) => d.priority);
      const urgentIdx = priorities.indexOf('urgent');
      const lowIdx = priorities.indexOf('low');
      if (urgentIdx >= 0 && lowIdx >= 0) {
        expect(urgentIdx).toBeLessThan(lowIdx);
      }
    });

    it('should filter drafts by status', () => {
      const d = engine.createDraft({ recipientName: 'A', body: 'a', context: 'test' });
      engine.approveDraft(d.id);
      engine.createDraft({ recipientName: 'B', body: 'b', context: 'test' });

      const approved = engine.getAllDrafts({ status: 'approved' });
      expect(approved.every((d) => d.status === 'approved')).toBe(true);
    });

    it('should filter drafts by channel', () => {
      engine.createDraft({ recipientName: 'A', body: 'a', context: 'test', channel: 'email' });
      engine.createDraft({ recipientName: 'B', body: 'b', context: 'test', channel: 'slack' });

      const emails = engine.getAllDrafts({ channel: 'email' });
      expect(emails.every((d) => d.channel === 'email')).toBe(true);
    });

    it('should limit returned drafts', () => {
      for (let i = 0; i < 10; i++) {
        engine.createDraft({ recipientName: `P${i}`, body: 'x', context: 'test' });
      }
      const limited = engine.getAllDrafts({ limit: 3 });
      expect(limited).toHaveLength(3);
    });
  });

  // ── Draft Editing ───────────────────────────────────────────────

  describe('Draft Editing', () => {
    it('should edit body of a pending draft', () => {
      const draft = engine.createDraft({ recipientName: 'A', body: 'original', context: 'test' });
      const edited = engine.editDraft(draft.id, { body: 'updated' });
      expect(edited).not.toBeNull();
      expect(edited!.body).toBe('updated');
      expect(edited!.editCount).toBe(1);
    });

    it('should edit multiple fields at once', () => {
      const draft = engine.createDraft({ recipientName: 'A', body: 'x', context: 'test' });
      const edited = engine.editDraft(draft.id, {
        body: 'new body',
        subject: 'new subject',
        tone: 'formal',
        priority: 'high',
      });
      expect(edited!.body).toBe('new body');
      expect(edited!.subject).toBe('new subject');
      expect(edited!.tone).toBe('formal');
      expect(edited!.priority).toBe('high');
    });

    it('should increment editCount on each edit', () => {
      const draft = engine.createDraft({ recipientName: 'A', body: 'x', context: 'test' });
      engine.editDraft(draft.id, { body: 'v2' });
      engine.editDraft(draft.id, { body: 'v3' });
      const d = engine.getDraft(draft.id);
      expect(d!.editCount).toBe(2);
    });

    it('should not edit an approved draft', () => {
      const draft = engine.createDraft({ recipientName: 'A', body: 'x', context: 'test' });
      engine.approveDraft(draft.id);
      const result = engine.editDraft(draft.id, { body: 'nope' });
      expect(result).toBeNull();
    });

    it('should not edit a rejected draft', () => {
      const draft = engine.createDraft({ recipientName: 'A', body: 'x', context: 'test' });
      engine.rejectDraft(draft.id);
      expect(engine.editDraft(draft.id, { body: 'nope' })).toBeNull();
    });

    it('should return null for nonexistent draft', () => {
      expect(engine.editDraft('fake', { body: 'x' })).toBeNull();
    });
  });

  // ── Draft Deletion ──────────────────────────────────────────────

  describe('Draft Deletion', () => {
    it('should delete an existing draft', () => {
      const draft = engine.createDraft({ recipientName: 'A', body: 'x', context: 'test' });
      expect(engine.deleteDraft(draft.id)).toBe(true);
      expect(engine.getDraft(draft.id)).toBeNull();
    });

    it('should return false for nonexistent draft', () => {
      expect(engine.deleteDraft('fake')).toBe(false);
    });
  });

  // ── Approval Workflow ───────────────────────────────────────────

  describe('Approval Workflow', () => {
    it('should approve a pending draft', () => {
      const draft = engine.createDraft({ recipientName: 'A', body: 'x', context: 'test' });
      const approved = engine.approveDraft(draft.id);
      expect(approved).not.toBeNull();
      expect(approved!.status).toBe('approved');
      expect(approved!.approvedAt).toBeGreaterThan(0);
    });

    it('should not approve an already approved draft', () => {
      const draft = engine.createDraft({ recipientName: 'A', body: 'x', context: 'test' });
      engine.approveDraft(draft.id);
      expect(engine.approveDraft(draft.id)).toBeNull();
    });

    it('should reject a pending draft', () => {
      const draft = engine.createDraft({ recipientName: 'A', body: 'x', context: 'test' });
      const rejected = engine.rejectDraft(draft.id);
      expect(rejected!.status).toBe('rejected');
    });

    it('should not reject a non-pending draft', () => {
      const draft = engine.createDraft({ recipientName: 'A', body: 'x', context: 'test' });
      engine.approveDraft(draft.id);
      expect(engine.rejectDraft(draft.id)).toBeNull();
    });

    it('should approve all pending drafts', () => {
      engine.createDraft({ recipientName: 'A', body: 'a', context: 'test' });
      engine.createDraft({ recipientName: 'B', body: 'b', context: 'test' });
      engine.createDraft({ recipientName: 'C', body: 'c', context: 'test' });
      const count = engine.approveAll();
      expect(count).toBe(3);
      const pending = engine.getPendingDrafts();
      expect(pending).toHaveLength(0);
    });

    it('should return 0 when no pending drafts to approve', () => {
      expect(engine.approveAll()).toBe(0);
    });
  });

  // ── Sending (cLaw Compliance) ──────────────────────────────────

  describe('Sending', () => {
    it('should refuse to send unapproved draft (cLaw)', async () => {
      const draft = engine.createDraft({ recipientName: 'A', body: 'x', context: 'test' });
      const result = await engine.sendDraft(draft.id);
      expect(result.success).toBe(false);
      expect(result.error).toContain("'pending'");
      expect(result.error).toContain("'approved'");
    });

    it('should return error for nonexistent draft', async () => {
      const result = await engine.sendDraft('nonexistent');
      expect(result.success).toBe(false);
      expect(result.error).toContain('not found');
    });

    it('should send an approved in-app draft successfully', async () => {
      const draft = engine.createDraft({
        recipientName: 'A',
        body: 'test',
        context: 'test',
        channel: 'in-app',
      });
      engine.approveDraft(draft.id);
      const result = await engine.sendDraft(draft.id);
      expect(result.success).toBe(true);
      expect(result.channel).toBe('in-app');
      expect(result.sentAt).toBeGreaterThan(0);
    });

    it('should send an approved email draft successfully', async () => {
      const draft = engine.createDraft({
        recipientName: 'A',
        body: 'test',
        context: 'test',
        channel: 'email',
      });
      engine.approveDraft(draft.id);
      const result = await engine.sendDraft(draft.id);
      expect(result.success).toBe(true);
      expect(result.channel).toBe('email');
    });

    it('should update draft status to sent after successful send', async () => {
      const draft = engine.createDraft({
        recipientName: 'A',
        body: 'test',
        context: 'test',
        channel: 'in-app',
      });
      engine.approveDraft(draft.id);
      await engine.sendDraft(draft.id);
      const sent = engine.getDraft(draft.id);
      expect(sent!.status).toBe('sent');
      expect(sent!.sentAt).toBeGreaterThan(0);
    });

    it('should handle send failure gracefully (missing gateway)', async () => {
      const draft = engine.createDraft({
        recipientName: 'A',
        body: 'test',
        context: 'test',
        channel: 'telegram',
        channelAddress: '12345',
      });
      engine.approveDraft(draft.id);
      const result = await engine.sendDraft(draft.id);
      expect(result.success).toBe(false);
      expect(result.error).toBeTruthy();
      const d = engine.getDraft(draft.id);
      expect(d!.status).toBe('failed');
      expect(d!.sendError).toBeTruthy();
    });

    it('should not send a rejected draft', async () => {
      const draft = engine.createDraft({ recipientName: 'A', body: 'x', context: 'test' });
      engine.rejectDraft(draft.id);
      const result = await engine.sendDraft(draft.id);
      expect(result.success).toBe(false);
    });

    it('should not send an already sent draft', async () => {
      const draft = engine.createDraft({
        recipientName: 'A',
        body: 'x',
        context: 'test',
        channel: 'in-app',
      });
      engine.approveDraft(draft.id);
      await engine.sendDraft(draft.id);
      const result = await engine.sendDraft(draft.id);
      expect(result.success).toBe(false);
    });

    it('should approve and send in one step', async () => {
      const draft = engine.createDraft({
        recipientName: 'A',
        body: 'test',
        context: 'test',
        channel: 'in-app',
      });
      const result = await engine.approveAndSend(draft.id);
      expect(result.success).toBe(true);
      expect(engine.getDraft(draft.id)!.status).toBe('sent');
    });

    it('should return error if approveAndSend on nonexistent draft', async () => {
      const result = await engine.approveAndSend('fake');
      expect(result.success).toBe(false);
    });

    it('should send all approved drafts', async () => {
      for (let i = 0; i < 3; i++) {
        const d = engine.createDraft({
          recipientName: `P${i}`,
          body: 'test',
          context: 'test',
          channel: 'in-app',
        });
        engine.approveDraft(d.id);
      }
      const results = await engine.sendAllApproved();
      expect(results).toHaveLength(3);
      expect(results.every((r) => r.success)).toBe(true);
    });
  });

  // ── Batch Review ────────────────────────────────────────────────

  describe('Batch Review', () => {
    it('should return empty array when no pending drafts', () => {
      const review = engine.getBatchReview();
      expect(review).toHaveLength(0);
    });

    it('should return review items with channelScores', () => {
      engine.createDraft({ recipientName: 'A', body: 'x', context: 'test' });
      engine.createDraft({ recipientName: 'B', body: 'y', context: 'test' });
      const review = engine.getBatchReview();
      expect(review).toHaveLength(2);
      for (const item of review) {
        expect(item.draft).toBeTruthy();
        expect(item.channelScores.length).toBeGreaterThan(0);
      }
    });

    it('should not include non-pending drafts in batch review', () => {
      const d1 = engine.createDraft({ recipientName: 'A', body: 'x', context: 'test' });
      engine.createDraft({ recipientName: 'B', body: 'y', context: 'test' });
      engine.approveDraft(d1.id);
      const review = engine.getBatchReview();
      expect(review).toHaveLength(1);
      expect(review[0].draft.recipientName).toBe('B');
    });
  });

  // ── Style Profiles ─────────────────────────────────────────────

  describe('Style Profiles', () => {
    it('should create a new style profile', () => {
      const profile = engine.updateStyleProfile('p1', 'Alice', { preferredTone: 'formal' });
      expect(profile.recipientPersonId).toBe('p1');
      expect(profile.recipientName).toBe('Alice');
      expect(profile.preferredTone).toBe('formal');
      expect(profile.observationCount).toBe(1);
    });

    it('should update an existing style profile', () => {
      engine.updateStyleProfile('p1', 'Alice', { preferredTone: 'formal' });
      const updated = engine.updateStyleProfile('p1', 'Alice', { preferredTone: 'casual', avgLength: 100 });
      expect(updated.preferredTone).toBe('casual');
      expect(updated.avgLength).toBe(100);
      expect(updated.observationCount).toBe(2);
    });

    it('should retrieve a style profile by person ID', () => {
      engine.updateStyleProfile('p1', 'Alice', { preferredTone: 'friendly' });
      const profile = engine.getStyleProfile('p1');
      expect(profile).not.toBeNull();
      expect(profile!.preferredTone).toBe('friendly');
    });

    it('should return null for unknown person ID', () => {
      expect(engine.getStyleProfile('unknown')).toBeNull();
    });

    it('should list all style profiles', () => {
      engine.updateStyleProfile('p1', 'Alice', { preferredTone: 'formal' });
      engine.updateStyleProfile('p2', 'Bob', { preferredTone: 'casual' });
      const all = engine.getAllStyleProfiles();
      expect(all).toHaveLength(2);
    });
  });

  // ── Standing Permissions ────────────────────────────────────────

  describe('Standing Permissions', () => {
    it('should add a standing permission', () => {
      const perm = engine.addStandingPermission({
        recipientPersonId: 'p1',
        recipientName: 'Alice',
        channels: ['email', 'slack'],
        maxPriority: 'normal',
      });
      expect(perm.id).toBeTruthy();
      expect(perm.active).toBe(true);
      expect(perm.channels).toEqual(['email', 'slack']);
    });

    it('should revoke a standing permission', () => {
      const perm = engine.addStandingPermission({
        recipientPersonId: 'p1',
        recipientName: 'Alice',
        channels: ['email'],
        maxPriority: 'normal',
      });
      expect(engine.revokeStandingPermission(perm.id)).toBe(true);
      // Active permissions should not include revoked
      const active = engine.getStandingPermissions();
      expect(active.find((p) => p.id === perm.id)).toBeUndefined();
    });

    it('should return false revoking nonexistent permission', () => {
      expect(engine.revokeStandingPermission('fake')).toBe(false);
    });

    it('should delete a standing permission', () => {
      const perm = engine.addStandingPermission({
        recipientPersonId: 'p1',
        recipientName: 'Alice',
        channels: ['email'],
        maxPriority: 'normal',
      });
      expect(engine.deleteStandingPermission(perm.id)).toBe(true);
      const all = engine.getAllStandingPermissions();
      expect(all.find((p) => p.id === perm.id)).toBeUndefined();
    });

    it('should return false deleting nonexistent permission', () => {
      expect(engine.deleteStandingPermission('fake')).toBe(false);
    });

    it('should filter expired permissions from getStandingPermissions', () => {
      engine.addStandingPermission({
        recipientPersonId: 'p1',
        recipientName: 'Alice',
        channels: ['email'],
        maxPriority: 'normal',
        expiresAt: Date.now() - 1000, // already expired
      });
      engine.addStandingPermission({
        recipientPersonId: 'p2',
        recipientName: 'Bob',
        channels: ['slack'],
        maxPriority: 'high',
      });
      const active = engine.getStandingPermissions();
      expect(active).toHaveLength(1);
      expect(active[0].recipientName).toBe('Bob');
    });

    it('should include all permissions in getAllStandingPermissions', () => {
      engine.addStandingPermission({
        recipientPersonId: 'p1',
        recipientName: 'Alice',
        channels: ['email'],
        maxPriority: 'normal',
        expiresAt: Date.now() - 1000,
      });
      engine.addStandingPermission({
        recipientPersonId: 'p2',
        recipientName: 'Bob',
        channels: ['slack'],
        maxPriority: 'high',
      });
      expect(engine.getAllStandingPermissions()).toHaveLength(2);
    });
  });

  // ── Auto-Approve via Standing Permission ────────────────────────

  describe('Auto-Approve', () => {
    it('should auto-approve when standing permission matches', () => {
      const perm = engine.addStandingPermission({
        recipientPersonId: 'p1',
        recipientName: 'Alice',
        channels: ['email'],
        maxPriority: 'normal',
      });
      const draft = engine.createDraft({
        recipientName: 'Alice',
        body: 'test',
        context: 'test',
        channel: 'email',
      });
      // Manually set the recipientPersonId to match (Trust Graph not available in tests)
      (draft as any).recipientPersonId = 'p1';
      const result = engine.tryAutoApprove(draft.id);
      expect(result).toBe(perm.id);
      expect(engine.getDraft(draft.id)!.status).toBe('approved');
    });

    it('should return null when no standing permission matches', () => {
      const draft = engine.createDraft({
        recipientName: 'Alice',
        body: 'test',
        context: 'test',
        channel: 'email',
      });
      expect(engine.tryAutoApprove(draft.id)).toBeNull();
      expect(engine.getDraft(draft.id)!.status).toBe('pending');
    });

    it('should not auto-approve non-pending draft', () => {
      const draft = engine.createDraft({
        recipientName: 'A',
        body: 'test',
        context: 'test',
      });
      engine.approveDraft(draft.id);
      expect(engine.tryAutoApprove(draft.id)).toBeNull();
    });
  });

  // ── Stats ────────────────────────────────────────────────────────

  describe('Stats', () => {
    it('should report accurate stats', async () => {
      engine.createDraft({ recipientName: 'A', body: 'a', context: 'test' });
      engine.createDraft({ recipientName: 'B', body: 'b', context: 'test' });
      const d3 = engine.createDraft({ recipientName: 'C', body: 'c', context: 'test', channel: 'in-app' });
      const d4 = engine.createDraft({ recipientName: 'D', body: 'd', context: 'test' });
      engine.approveDraft(d3.id);
      engine.rejectDraft(d4.id);
      await engine.sendDraft(d3.id);

      engine.addStandingPermission({
        recipientPersonId: 'p1',
        recipientName: 'X',
        channels: ['email'],
        maxPriority: 'normal',
      });
      engine.updateStyleProfile('p2', 'Y', { preferredTone: 'formal' });

      const stats = engine.getStats();
      expect(stats.totalDrafts).toBe(4);
      expect(stats.pendingDrafts).toBe(2);
      expect(stats.sentMessages).toBe(1);
      expect(stats.rejectedDrafts).toBe(1);
      expect(stats.standingPermissions).toBe(1);
      expect(stats.styleProfiles).toBe(1);
    });

    it('should report zero stats on empty engine', () => {
      const stats = engine.getStats();
      expect(stats.totalDrafts).toBe(0);
      expect(stats.pendingDrafts).toBe(0);
      expect(stats.sentMessages).toBe(0);
    });
  });

  // ── Config ────────────────────────────────────────────────────────

  describe('Config', () => {
    it('should return default config', () => {
      const config = engine.getConfig();
      expect(config.maxDrafts).toBe(100);
      expect(config.draftExpiryHours).toBe(48);
      expect(config.batchReview).toBe(true);
      expect(config.defaultTone).toBe('professional');
    });

    it('should update config partially', () => {
      const updated = engine.updateConfig({ maxDrafts: 50, defaultTone: 'casual' });
      expect(updated.maxDrafts).toBe(50);
      expect(updated.defaultTone).toBe('casual');
      // Unchanged fields preserved
      expect(updated.batchReview).toBe(true);
    });

    it('should persist config across getConfig calls', () => {
      engine.updateConfig({ draftExpiryHours: 24 });
      expect(engine.getConfig().draftExpiryHours).toBe(24);
    });
  });

  // ── Context Generation ──────────────────────────────────────────

  describe('Context Generation', () => {
    it('should return empty string when no drafts', () => {
      expect(engine.getPromptContext()).toBe('');
    });

    it('should include pending draft info', () => {
      engine.createDraft({
        recipientName: 'Alice',
        body: 'Hello Alice, checking in about the project',
        context: 'test',
        channel: 'email',
      });
      const ctx = engine.getPromptContext();
      expect(ctx).toContain('PENDING DRAFTS');
      expect(ctx).toContain('Alice');
      expect(ctx).toContain('email');
    });

    it('should include recently sent info', async () => {
      const d = engine.createDraft({
        recipientName: 'Bob',
        body: 'Done!',
        context: 'test',
        channel: 'in-app',
        subject: 'Completed',
      });
      engine.approveDraft(d.id);
      await engine.sendDraft(d.id);
      const ctx = engine.getPromptContext();
      expect(ctx).toContain('RECENTLY SENT');
      expect(ctx).toContain('Bob');
    });

    it('should truncate long body in context', () => {
      engine.createDraft({
        recipientName: 'Alice',
        body: 'A'.repeat(200),
        context: 'test',
      });
      const ctx = engine.getPromptContext();
      expect(ctx).toContain('...');
      expect(ctx.length).toBeLessThan(500);
    });

    it('should show overflow count for >5 pending drafts', () => {
      for (let i = 0; i < 7; i++) {
        engine.createDraft({ recipientName: `P${i}`, body: 'x', context: 'test' });
      }
      const ctx = engine.getPromptContext();
      expect(ctx).toContain('and 2 more');
    });
  });

  // ── Max Drafts Enforcement ─────────────────────────────────────

  describe('Max Drafts Enforcement', () => {
    it('should enforce maxDrafts limit', () => {
      engine.updateConfig({ maxDrafts: 5 });
      for (let i = 0; i < 8; i++) {
        engine.createDraft({ recipientName: `P${i}`, body: `body ${i}`, context: 'test' });
      }
      const all = engine.getAllDrafts();
      expect(all.length).toBeLessThanOrEqual(5);
    });
  });
});
