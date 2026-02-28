/**
 * unified-inbox.ts — Track VI Phase 1: Unified Inbox with Security.
 *
 * Normalizes messages from all channels (Telegram, Slack, Discord, email,
 * local conversation) into a single prioritized stream with:
 *   - Intelligent triage (Trust Graph + Commitment Tracker + Context Graph)
 *   - DLP scanning (SSN, credit cards, API keys) before display
 *   - Prompt injection detection
 *   - Sender verification via Trust Graph PersonAlias resolution
 *   - Context stream integration (emits 'communication' events)
 *   - Deduplication across channels
 *
 * Follows the Singleton + IPC + Preload pattern used by all v2.0.0 engines.
 *
 * cLaw Gate: DLP patterns are scanned BEFORE any message body reaches the
 * renderer. Flagged content is redacted in the display copy; originals are
 * kept for audit but never surfaced to the UI unredacted.
 */

import { app } from 'electron';
import fs from 'fs/promises';
import path from 'path';
import crypto from 'crypto';
import type { GatewayMessage, TrustTier } from './gateway/types';
import type { ResolutionResult, PersonNode } from './trust-graph';
import { failClosedTrust } from './errors';

// ── Late-bound imports (avoid circular deps at module load time) ─────

let _trustGraph: any = null;
function getTrustGraph() {
  if (!_trustGraph) {
    try { _trustGraph = require('./trust-graph').trustGraph; } catch { /* not ready */ }
  }
  return _trustGraph;
}

let _contextStream: any = null;
function getContextStream() {
  if (!_contextStream) {
    try { _contextStream = require('./context-stream').contextStream; } catch { /* not ready */ }
  }
  return _contextStream;
}

let _commitmentTracker: any = null;
function getCommitmentTracker() {
  if (!_commitmentTracker) {
    try { _commitmentTracker = require('./commitment-tracker').commitmentTracker; } catch { /* not ready */ }
  }
  return _commitmentTracker;
}

// ══════════════════════════════════════════════════════════════════════
// DATA MODEL
// ══════════════════════════════════════════════════════════════════════

export type UrgencyLevel = 'critical' | 'high' | 'medium' | 'low' | 'info';

export type MessageCategory =
  | 'action-required'
  | 'follow-up'
  | 'informational'
  | 'social'
  | 'automated'
  | 'unknown';

export interface DlpFlag {
  type: 'ssn' | 'credit-card' | 'api-key' | 'email-address' | 'phone-number' | 'custom';
  pattern: string;        // What was matched (redacted form, e.g. "***-**-1234")
  position: number;       // Character offset in original text
  severity: 'high' | 'medium' | 'low';
}

export interface PromptInjectionFlag {
  detected: boolean;
  confidence: number;     // 0-1
  pattern: string;        // Which heuristic matched
}

export interface SenderVerification {
  verified: boolean;
  personId: string | null;
  personName: string | null;
  trustScore: number;     // 0-1 overall trust from Trust Graph
  isNewSender: boolean;
  aliasMatch: boolean;    // Whether this channel identity was a known alias
  warning: string | null; // e.g. "Unusual email for known contact"
}

export interface InboxMessage {
  /** Unique inbox message ID */
  id: string;
  /** Source channel: 'telegram' | 'slack' | 'discord' | 'email' | 'local' */
  channel: string;
  /** Channel-specific sender ID */
  senderId: string;
  /** Human-readable sender name */
  senderName: string;
  /** Message text (DLP-redacted for display) */
  text: string;
  /** Original unredacted text (audit only, never sent to renderer) */
  originalText: string;
  /** Unix ms timestamp */
  timestamp: number;
  /** Gateway trust tier */
  trustTier: TrustTier;
  /** Computed urgency score (0-1) and level */
  urgencyScore: number;
  urgencyLevel: UrgencyLevel;
  /** Auto-categorization */
  category: MessageCategory;
  /** DLP scan results */
  dlpFlags: DlpFlag[];
  /** Prompt injection scan */
  injectionFlag: PromptInjectionFlag;
  /** Sender verification result */
  senderVerification: SenderVerification;
  /** Thread/conversation grouping */
  threadId: string | null;
  /** Original message ID from the channel */
  sourceMessageId: string;
  /** Whether user has read this message */
  read: boolean;
  /** Whether message is archived */
  archived: boolean;
  /** Triage reasoning (one-line explanation) */
  triageReason: string;
  /** Channel-specific metadata */
  metadata: Record<string, unknown>;
}

/** Safe view of InboxMessage for renderer (no originalText) */
export type InboxMessageView = Omit<InboxMessage, 'originalText'>;

export interface InboxStats {
  total: number;
  unread: number;
  byCritical: number;
  byHigh: number;
  byMedium: number;
  byLow: number;
  byChannel: Record<string, number>;
  dlpFlagsTotal: number;
  injectionBlockedTotal: number;
}

export interface InboxConfig {
  maxMessages: number;          // Default 500
  retentionDays: number;        // Default 30
  dlpEnabled: boolean;          // Default true
  injectionDetection: boolean;  // Default true
  autoArchiveDays: number;      // Default 7
}

// ══════════════════════════════════════════════════════════════════════
// DLP PATTERNS — cLaw Safety Gate
// ══════════════════════════════════════════════════════════════════════

interface DlpPatternDef {
  type: DlpFlag['type'];
  regex: RegExp;
  severity: DlpFlag['severity'];
  redact: (match: string) => string;
}

const DLP_PATTERNS: DlpPatternDef[] = [
  {
    type: 'ssn',
    regex: /\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b/g,
    severity: 'high',
    redact: (m) => `***-**-${m.slice(-4)}`,
  },
  {
    type: 'credit-card',
    // Visa, MasterCard, Amex, Discover patterns
    regex: /\b(?:4\d{3}|5[1-5]\d{2}|3[47]\d{2}|6(?:011|5\d{2}))[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b/g,
    severity: 'high',
    redact: (m) => `****-****-****-${m.replace(/[-\s]/g, '').slice(-4)}`,
  },
  {
    type: 'api-key',
    // Common API key patterns: sk-..., ghp_..., xoxb-..., AKIA...
    regex: /\b(?:sk-[a-zA-Z0-9]{20,}|ghp_[a-zA-Z0-9]{36,}|gho_[a-zA-Z0-9]{36,}|xoxb-[a-zA-Z0-9-]{40,}|AKIA[A-Z0-9]{16}|AIza[a-zA-Z0-9_-]{35})\b/g,
    severity: 'high',
    redact: (m) => `${m.slice(0, 6)}...[REDACTED]`,
  },
];

/**
 * Scan text for sensitive patterns (DLP).
 * Returns the redacted text + array of flags.
 */
export function dlpScan(text: string, enabled: boolean = true): { redacted: string; flags: DlpFlag[] } {
  if (!enabled || !text) return { redacted: text, flags: [] };

  const flags: DlpFlag[] = [];
  let redacted = text;

  for (const pattern of DLP_PATTERNS) {
    // Reset regex lastIndex for global patterns
    pattern.regex.lastIndex = 0;
    let match: RegExpExecArray | null;

    while ((match = pattern.regex.exec(text)) !== null) {
      flags.push({
        type: pattern.type,
        pattern: pattern.redact(match[0]),
        position: match.index,
        severity: pattern.severity,
      });
    }

    // Apply redaction to the copy
    pattern.regex.lastIndex = 0;
    redacted = redacted.replace(pattern.regex, (m) => pattern.redact(m));
  }

  return { redacted, flags };
}

// ══════════════════════════════════════════════════════════════════════
// PROMPT INJECTION DETECTION
// ══════════════════════════════════════════════════════════════════════

interface InjectionPattern {
  regex: RegExp;
  weight: number;   // 0-1 contribution to confidence
  label: string;
}

const INJECTION_PATTERNS: InjectionPattern[] = [
  {
    regex: /ignore\s+(your|all|previous|prior)\s+(instructions?|rules?|prompts?|system)/i,
    weight: 0.9,
    label: 'instruction-override',
  },
  {
    regex: /you\s+are\s+now\s+(a|an|in|the)\s/i,
    weight: 0.7,
    label: 'role-reassignment',
  },
  {
    regex: /system\s*:\s*|<\s*system\s*>|<<\s*SYS\s*>>/i,
    weight: 0.85,
    label: 'system-tag-injection',
  },
  {
    regex: /(?:disregard|forget|override|bypass)\s+(?:the|all|any|your)\s+(?:above|previous|safety|security|rules?|instructions?)/i,
    weight: 0.9,
    label: 'safety-bypass',
  },
  {
    regex: /\bdo\s+not\s+(?:follow|obey|listen|comply)/i,
    weight: 0.6,
    label: 'compliance-override',
  },
  {
    regex: /(?:send|forward|share|transmit)\s+(?:all|every|the)\s+(?:user'?s?|private|personal|secret|confidential)/i,
    weight: 0.8,
    label: 'data-exfiltration',
  },
  {
    regex: /\[INST\]|\[\/INST\]|<\|im_start\|>|<\|im_end\|>/i,
    weight: 0.95,
    label: 'prompt-delimiter',
  },
  {
    regex: /(?:ADMIN|DEVELOPER|SYSTEM)\s+(?:MODE|ACCESS|OVERRIDE)/i,
    weight: 0.85,
    label: 'privilege-escalation',
  },
];

/**
 * Detect prompt injection attempts in message text.
 * Returns a flag with detection result and confidence.
 */
export function detectPromptInjection(text: string, enabled: boolean = true): PromptInjectionFlag {
  if (!enabled || !text) {
    return { detected: false, confidence: 0, pattern: '' };
  }

  let maxConfidence = 0;
  let matchedPattern = '';

  for (const pattern of INJECTION_PATTERNS) {
    if (pattern.regex.test(text)) {
      if (pattern.weight > maxConfidence) {
        maxConfidence = pattern.weight;
        matchedPattern = pattern.label;
      }
    }
  }

  // Additional heuristic: unusually long messages with instruction-like structure
  const instructionDensity = (text.match(/\b(?:must|should|always|never|ensure|make sure|remember)\b/gi) || []).length;
  if (instructionDensity >= 5 && text.length > 500) {
    const densityScore = Math.min(0.6, instructionDensity * 0.08);
    if (densityScore > maxConfidence) {
      maxConfidence = densityScore;
      matchedPattern = 'instruction-density';
    }
  }

  return {
    detected: maxConfidence >= 0.5,
    confidence: maxConfidence,
    pattern: matchedPattern,
  };
}

// ══════════════════════════════════════════════════════════════════════
// SENDER VERIFICATION
// ══════════════════════════════════════════════════════════════════════

/**
 * Verify a sender against the Trust Graph.
 * Resolves the sender to a PersonNode and checks alias consistency.
 */
export function verifySender(
  senderName: string,
  senderId: string,
  channel: string,
): SenderVerification {
  const tg = getTrustGraph();
  if (!tg) {
    return {
      verified: false,
      personId: null,
      personName: null,
      trustScore: 0.5,
      isNewSender: true,
      aliasMatch: false,
      warning: null,
    };
  }

  // Use failClosedTrust: if resolution throws, treat as unverified
  const resolution: ResolutionResult = failClosedTrust(
    () => tg.resolvePerson(senderName, 'name'),
    { person: null, confidence: 0, isNew: true },
    'inbox-sender-verification',
  );

  if (!resolution.person) {
    return {
      verified: false,
      personId: null,
      personName: null,
      trustScore: 0.5,
      isNewSender: true,
      aliasMatch: false,
      warning: null,
    };
  }

  const person: PersonNode = resolution.person;

  // Check if the channel-specific ID is a known alias
  const channelAlias = person.aliases.find(
    (a) => a.value.toLowerCase() === senderId.toLowerCase() ||
           a.value.toLowerCase() === senderName.toLowerCase()
  );

  // Detect potential spoofing: known person from unexpected channel identity
  let warning: string | null = null;
  if (!channelAlias && !resolution.isNew && resolution.confidence < 0.9) {
    warning = `Message from "${senderName}" via ${channel} — not a recognized alias for ${person.primaryName}`;
  }

  return {
    verified: !!channelAlias || resolution.confidence >= 0.9,
    personId: person.id,
    personName: person.primaryName,
    trustScore: person.trust.overall,
    isNewSender: resolution.isNew,
    aliasMatch: !!channelAlias,
    warning,
  };
}

// ══════════════════════════════════════════════════════════════════════
// INTELLIGENT TRIAGE
// ══════════════════════════════════════════════════════════════════════

interface TriageFactors {
  trustTierWeight: number;    // Gateway access control tier
  trustGraphWeight: number;   // Trust Graph credibility score
  commitmentBoost: number;    // Related to overdue commitment?
  recencyBoost: number;       // Very recent messages get a bump
  channelWeight: number;      // DM > group
  injectionPenalty: number;   // Suspected injection → deprioritize
  categoryBoost: number;      // action-required > informational
}

/**
 * Compute triage urgency score (0-1) from multiple signals.
 *
 * Three orthogonal systems compose:
 *   1. Gateway trust tier (access control) — owner-dm > approved-dm > group
 *   2. Trust Graph score (credibility) — high-trust senders = more important
 *   3. Commitment Tracker (temporal) — overdue follow-ups boost urgency
 */
export function computeTriageScore(
  msg: Pick<InboxMessage, 'trustTier' | 'senderVerification' | 'injectionFlag' | 'category' | 'senderName' | 'text'>,
): { score: number; level: UrgencyLevel; reason: string } {
  const factors: TriageFactors = {
    trustTierWeight: 0,
    trustGraphWeight: 0,
    commitmentBoost: 0,
    recencyBoost: 0,
    channelWeight: 0,
    injectionPenalty: 0,
    categoryBoost: 0,
  };

  // 1. Gateway trust tier weight
  const tierWeights: Record<TrustTier, number> = {
    'local': 1.0,
    'owner-dm': 0.9,
    'approved-dm': 0.7,
    'group': 0.4,
    'public': 0.1,
  };
  factors.trustTierWeight = tierWeights[msg.trustTier] ?? 0.3;

  // 2. Trust Graph credibility score
  factors.trustGraphWeight = msg.senderVerification.trustScore;

  // 3. Commitment tracker boost
  const ct = getCommitmentTracker();
  if (ct) {
    try {
      const active = ct.getActiveCommitments?.() || [];
      const overdue = active.filter(
        (c: any) => c.status === 'overdue' &&
        c.personName?.toLowerCase().includes(msg.senderName.toLowerCase())
      );
      if (overdue.length > 0) {
        factors.commitmentBoost = 0.3; // Significant boost for overdue follow-ups
      }
    } catch { /* commitment tracker not ready */ }
  }

  // 4. Injection penalty
  if (msg.injectionFlag.detected) {
    factors.injectionPenalty = -0.4 * msg.injectionFlag.confidence;
  }

  // 5. Category boost
  const categoryBoosts: Record<MessageCategory, number> = {
    'action-required': 0.2,
    'follow-up': 0.1,
    'informational': 0,
    'social': -0.05,
    'automated': -0.1,
    'unknown': 0,
  };
  factors.categoryBoost = categoryBoosts[msg.category] ?? 0;

  // Composite score: weighted combination
  const raw =
    factors.trustTierWeight * 0.25 +
    factors.trustGraphWeight * 0.30 +
    factors.commitmentBoost +
    factors.injectionPenalty +
    factors.categoryBoost +
    0.15; // Base score so nothing is zero

  const score = Math.max(0, Math.min(1, raw));

  // Map score to level
  let level: UrgencyLevel;
  if (score >= 0.85) level = 'critical';
  else if (score >= 0.65) level = 'high';
  else if (score >= 0.45) level = 'medium';
  else if (score >= 0.25) level = 'low';
  else level = 'info';

  // Build triage reason
  const reasons: string[] = [];
  if (factors.trustTierWeight >= 0.9) reasons.push('trusted sender');
  if (factors.commitmentBoost > 0) reasons.push('overdue follow-up');
  if (factors.injectionPenalty < 0) reasons.push('injection suspected');
  if (msg.category === 'action-required') reasons.push('action needed');
  const reason = reasons.length > 0 ? reasons.join(', ') : `score: ${score.toFixed(2)}`;

  return { score, level, reason };
}

// ══════════════════════════════════════════════════════════════════════
// MESSAGE CATEGORIZATION
// ══════════════════════════════════════════════════════════════════════

const ACTION_PATTERNS = [
  /\b(?:please|could you|can you|would you|need you to|urgent|asap|deadline)\b/i,
  /\b(?:approve|review|sign|confirm|submit|complete|respond|reply)\b/i,
  /\?$/,  // Questions often need responses
];

const FOLLOW_UP_PATTERNS = [
  /\b(?:following up|just checking|any update|status on|reminder|circling back)\b/i,
  /\b(?:did you|have you|were you able)\b/i,
];

const AUTOMATED_PATTERNS = [
  /\b(?:noreply|no-reply|automated|notification|alert|digest|unsubscribe)\b/i,
  /\b(?:CI|CD|build|deploy|pipeline|github-actions|dependabot)\b/i,
];

/**
 * Categorize a message based on heuristic content analysis.
 */
export function categorizeMessage(text: string, senderName: string): MessageCategory {
  if (!text) return 'unknown';

  const lower = text.toLowerCase();

  // Automated detection (check first — automated msgs shouldn't be action-required)
  if (AUTOMATED_PATTERNS.some((p) => p.test(lower)) || AUTOMATED_PATTERNS.some((p) => p.test(senderName))) {
    return 'automated';
  }

  // Follow-up detection
  if (FOLLOW_UP_PATTERNS.some((p) => p.test(lower))) {
    return 'follow-up';
  }

  // Action-required detection
  if (ACTION_PATTERNS.some((p) => p.test(text))) {
    return 'action-required';
  }

  // Short social messages
  if (text.length < 50 && /\b(?:thanks|thank you|great|awesome|cool|ok|sure|lol|haha)\b/i.test(text)) {
    return 'social';
  }

  return 'informational';
}

// ══════════════════════════════════════════════════════════════════════
// UNIFIED INBOX ENGINE
// ══════════════════════════════════════════════════════════════════════

const DEFAULT_INBOX_CONFIG: InboxConfig = {
  maxMessages: 500,
  retentionDays: 30,
  dlpEnabled: true,
  injectionDetection: true,
  autoArchiveDays: 7,
};

class UnifiedInbox {
  private messages: InboxMessage[] = [];
  private filePath: string = '';
  private config: InboxConfig = { ...DEFAULT_INBOX_CONFIG };
  private savePromise: Promise<void> = Promise.resolve();
  private dedupeSet: Set<string> = new Set(); // channel:sourceMessageId
  private injectionBlockedCount: number = 0;

  // ── Initialization ──

  async initialize(): Promise<void> {
    this.filePath = path.join(app.getPath('userData'), 'unified-inbox.json');

    try {
      const data = await fs.readFile(this.filePath, 'utf-8');
      const saved = JSON.parse(data);
      this.messages = saved.messages || [];
      if (saved.config) {
        this.config = { ...DEFAULT_INBOX_CONFIG, ...saved.config };
      }
      this.injectionBlockedCount = saved.injectionBlockedCount || 0;
    } catch {
      this.messages = [];
    }

    // Build dedup set from existing messages
    for (const msg of this.messages) {
      this.dedupeSet.add(`${msg.channel}:${msg.sourceMessageId}`);
    }

    // Prune old messages
    this.pruneOldMessages();

    console.log(`[UnifiedInbox] Initialized with ${this.messages.length} messages`);
  }

  // ── Core: Ingest Message ──

  /**
   * Ingest a message from any channel into the unified inbox.
   * This is the main entry point — called by gateway-manager hook
   * and any other channel adapters.
   *
   * Pipeline:
   *   1. Deduplication check
   *   2. DLP scan → redact sensitive content
   *   3. Prompt injection detection
   *   4. Sender verification via Trust Graph
   *   5. Message categorization
   *   6. Triage scoring (Trust Graph + Commitment Tracker)
   *   7. Store + emit context event
   */
  ingestMessage(gatewayMsg: GatewayMessage): InboxMessage | null {
    // 1. Dedup
    const dedupeKey = `${gatewayMsg.channel}:${gatewayMsg.id}`;
    if (this.dedupeSet.has(dedupeKey)) {
      return null; // Already ingested
    }

    // 2. DLP scan
    const { redacted, flags: dlpFlags } = dlpScan(gatewayMsg.text, this.config.dlpEnabled);

    // 3. Prompt injection detection
    const injectionFlag = detectPromptInjection(gatewayMsg.text, this.config.injectionDetection);
    if (injectionFlag.detected) {
      this.injectionBlockedCount++;
      console.warn(
        `[UnifiedInbox] Prompt injection detected from ${gatewayMsg.senderName} ` +
        `(${gatewayMsg.channel}): ${injectionFlag.pattern} (confidence: ${injectionFlag.confidence.toFixed(2)})`
      );
    }

    // 4. Sender verification
    const senderVerification = verifySender(
      gatewayMsg.senderName,
      gatewayMsg.senderId,
      gatewayMsg.channel,
    );

    // 5. Categorize
    const category = categorizeMessage(gatewayMsg.text, gatewayMsg.senderName);

    // 6. Build preliminary message
    const inboxMsg: InboxMessage = {
      id: crypto.randomUUID().slice(0, 12),
      channel: gatewayMsg.channel,
      senderId: gatewayMsg.senderId,
      senderName: gatewayMsg.senderName,
      text: redacted,
      originalText: gatewayMsg.text,
      timestamp: gatewayMsg.timestamp,
      trustTier: gatewayMsg.trustTier,
      urgencyScore: 0,
      urgencyLevel: 'medium',
      category,
      dlpFlags,
      injectionFlag,
      senderVerification,
      threadId: gatewayMsg.threadId || null,
      sourceMessageId: gatewayMsg.id,
      read: false,
      archived: false,
      triageReason: '',
      metadata: gatewayMsg.metadata || {},
    };

    // 7. Compute triage
    const triage = computeTriageScore(inboxMsg);
    inboxMsg.urgencyScore = triage.score;
    inboxMsg.urgencyLevel = triage.level;
    inboxMsg.triageReason = triage.reason;

    // Store
    this.messages.push(inboxMsg);
    this.dedupeSet.add(dedupeKey);

    // Cap messages
    if (this.messages.length > this.config.maxMessages) {
      // Remove oldest archived first, then oldest read
      this.messages.sort((a, b) => {
        if (a.archived !== b.archived) return a.archived ? -1 : 1;
        if (a.read !== b.read) return a.read ? -1 : 1;
        return a.timestamp - b.timestamp;
      });
      const removed = this.messages.shift();
      if (removed) {
        this.dedupeSet.delete(`${removed.channel}:${removed.sourceMessageId}`);
      }
    }

    // Emit context stream event
    this.emitContextEvent(inboxMsg);

    // Log Trust Graph communication
    if (senderVerification.personId) {
      const tg = getTrustGraph();
      if (tg) {
        try {
          tg.logCommunication(senderVerification.personId, {
            channel: gatewayMsg.channel,
            direction: 'inbound' as const,
            summary: gatewayMsg.text.slice(0, 100),
            sentiment: 0, // Neutral default; memory extraction does deeper analysis
          });
        } catch { /* trust graph not ready */ }
      }
    }

    this.scheduleSave();
    return inboxMsg;
  }

  // ── Queries ──

  /**
   * Get all messages sorted by urgency (highest first), then by timestamp (newest first).
   * Returns renderer-safe views (no originalText).
   */
  getMessages(opts?: {
    unreadOnly?: boolean;
    channel?: string;
    urgencyLevel?: UrgencyLevel;
    category?: MessageCategory;
    limit?: number;
    offset?: number;
    includeArchived?: boolean;
  }): InboxMessageView[] {
    let filtered = this.messages.filter((m) => {
      if (!opts?.includeArchived && m.archived) return false;
      if (opts?.unreadOnly && m.read) return false;
      if (opts?.channel && m.channel !== opts.channel) return false;
      if (opts?.urgencyLevel && m.urgencyLevel !== opts.urgencyLevel) return false;
      if (opts?.category && m.category !== opts.category) return false;
      return true;
    });

    // Sort: urgencyScore desc, then timestamp desc
    filtered.sort((a, b) => {
      const urgDiff = b.urgencyScore - a.urgencyScore;
      if (Math.abs(urgDiff) > 0.05) return urgDiff;
      return b.timestamp - a.timestamp;
    });

    // Pagination
    const offset = opts?.offset || 0;
    const limit = opts?.limit || 50;
    filtered = filtered.slice(offset, offset + limit);

    // Strip originalText for renderer safety
    return filtered.map((m) => this.toView(m));
  }

  /**
   * Get a single message by ID (renderer-safe view).
   */
  getMessage(id: string): InboxMessageView | null {
    const msg = this.messages.find((m) => m.id === id);
    return msg ? this.toView(msg) : null;
  }

  /**
   * Get inbox statistics.
   */
  getStats(): InboxStats {
    const active = this.messages.filter((m) => !m.archived);
    const byChannel: Record<string, number> = {};
    for (const m of active) {
      byChannel[m.channel] = (byChannel[m.channel] || 0) + 1;
    }

    return {
      total: active.length,
      unread: active.filter((m) => !m.read).length,
      byCritical: active.filter((m) => m.urgencyLevel === 'critical').length,
      byHigh: active.filter((m) => m.urgencyLevel === 'high').length,
      byMedium: active.filter((m) => m.urgencyLevel === 'medium').length,
      byLow: active.filter((m) => m.urgencyLevel === 'low').length,
      byChannel,
      dlpFlagsTotal: active.reduce((sum, m) => sum + m.dlpFlags.length, 0),
      injectionBlockedTotal: this.injectionBlockedCount,
    };
  }

  // ── Actions ──

  /**
   * Mark message(s) as read.
   */
  markRead(ids: string | string[]): void {
    const idSet = new Set(Array.isArray(ids) ? ids : [ids]);
    for (const msg of this.messages) {
      if (idSet.has(msg.id)) {
        msg.read = true;
      }
    }
    this.scheduleSave();
  }

  /**
   * Mark message(s) as unread.
   */
  markUnread(ids: string | string[]): void {
    const idSet = new Set(Array.isArray(ids) ? ids : [ids]);
    for (const msg of this.messages) {
      if (idSet.has(msg.id)) {
        msg.read = false;
      }
    }
    this.scheduleSave();
  }

  /**
   * Archive message(s).
   */
  archive(ids: string | string[]): void {
    const idSet = new Set(Array.isArray(ids) ? ids : [ids]);
    for (const msg of this.messages) {
      if (idSet.has(msg.id)) {
        msg.archived = true;
        msg.read = true; // Archiving implies read
      }
    }
    this.scheduleSave();
  }

  /**
   * Un-archive message(s).
   */
  unarchive(ids: string | string[]): void {
    const idSet = new Set(Array.isArray(ids) ? ids : [ids]);
    for (const msg of this.messages) {
      if (idSet.has(msg.id)) {
        msg.archived = false;
      }
    }
    this.scheduleSave();
  }

  /**
   * Delete message(s) permanently.
   */
  deleteMessages(ids: string | string[]): number {
    const idSet = new Set(Array.isArray(ids) ? ids : [ids]);
    const before = this.messages.length;
    this.messages = this.messages.filter((m) => {
      if (idSet.has(m.id)) {
        this.dedupeSet.delete(`${m.channel}:${m.sourceMessageId}`);
        return false;
      }
      return true;
    });
    const removed = before - this.messages.length;
    if (removed > 0) this.scheduleSave();
    return removed;
  }

  /**
   * Mark all unread messages as read.
   */
  markAllRead(): number {
    let count = 0;
    for (const msg of this.messages) {
      if (!msg.read) {
        msg.read = true;
        count++;
      }
    }
    if (count > 0) this.scheduleSave();
    return count;
  }

  // ── Configuration ──

  getConfig(): InboxConfig {
    return { ...this.config };
  }

  updateConfig(partial: Partial<InboxConfig>): void {
    this.config = { ...this.config, ...partial };
    this.scheduleSave();
  }

  // ── Context Stream Integration ──

  private emitContextEvent(msg: InboxMessage): void {
    const cs = getContextStream();
    if (!cs) return;

    try {
      cs.push({
        type: 'communication' as const,
        source: 'unified-inbox',
        summary: `${msg.senderName} via ${msg.channel}: ${msg.text.slice(0, 80)}${msg.text.length > 80 ? '...' : ''}`,
        data: {
          channel: msg.channel,
          sender: msg.senderName,
          senderId: msg.senderId,
          trustTier: msg.trustTier,
          urgencyLevel: msg.urgencyLevel,
          category: msg.category,
          inboxMessageId: msg.id,
          injectionDetected: msg.injectionFlag.detected,
          dlpFlagCount: msg.dlpFlags.length,
        },
        dedupeKey: `inbox:${msg.channel}:${msg.sourceMessageId}`,
      });
    } catch { /* context stream not ready */ }
  }

  // ── Maintenance ──

  private pruneOldMessages(): void {
    const cutoff = Date.now() - this.config.retentionDays * 24 * 60 * 60 * 1000;
    const autoCutoff = Date.now() - this.config.autoArchiveDays * 24 * 60 * 60 * 1000;

    // Auto-archive old read messages
    for (const msg of this.messages) {
      if (msg.read && !msg.archived && msg.timestamp < autoCutoff) {
        msg.archived = true;
      }
    }

    // Remove messages older than retention
    const before = this.messages.length;
    this.messages = this.messages.filter((m) => {
      if (m.timestamp < cutoff) {
        this.dedupeSet.delete(`${m.channel}:${m.sourceMessageId}`);
        return false;
      }
      return true;
    });

    if (this.messages.length < before) {
      console.log(`[UnifiedInbox] Pruned ${before - this.messages.length} old messages`);
    }
  }

  // ── Helpers ──

  private toView(msg: InboxMessage): InboxMessageView {
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    const { originalText, ...view } = msg;
    return view;
  }

  // ── Persistence ──

  private scheduleSave(): void {
    this.savePromise = this.savePromise
      .then(async () => {
        await fs.writeFile(
          this.filePath,
          JSON.stringify(
            {
              messages: this.messages,
              config: this.config,
              injectionBlockedCount: this.injectionBlockedCount,
            },
            null,
            2,
          ),
          'utf-8',
        );
      })
      .catch((err) => {
        console.error('[UnifiedInbox] Save failed:', err);
      });
  }

  /** Expose save promise for tests / shutdown. */
  async flush(): Promise<void> {
    return this.savePromise;
  }

  /** Get message count (for testing). */
  getMessageCount(): number {
    return this.messages.length;
  }
}

export const unifiedInbox = new UnifiedInbox();
