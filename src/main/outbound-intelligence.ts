/**
 * Track VI, Phase 2: Outbound Intelligence
 *
 * Transforms Agent Friday from a unified message receiver into an
 * intelligent message composer and sender.
 *
 * Core capabilities:
 *   - Draft queue: Accumulate, batch, and present drafts for review
 *   - Channel selection: Choose optimal channel per-recipient using
 *     communication history from Trust Graph CommEvents
 *   - Style memory: Learn per-recipient tone from past interactions
 *   - Approval workflow: cLaw-compliant explicit user confirmation
 *   - Standing permissions: Configurable auto-approve rules (still logged)
 *   - Multi-channel send: Route through gateway adapters or comms-hub
 *   - Audit trail: Every send attempt logged regardless of outcome
 *
 * cLaw Gate: NO message is ever sent without user approval (or a
 * pre-configured standing permission that the user explicitly set up).
 */

import crypto from 'crypto';
import { app } from 'electron';
import path from 'path';
import fs from 'fs/promises';

// ── Types ─────────────────────────────────────────────────────────────

export type DraftStatus = 'pending' | 'approved' | 'sent' | 'rejected' | 'expired' | 'failed';

export type OutboundChannel =
  | 'email'
  | 'slack'
  | 'discord'
  | 'telegram'
  | 'teams'
  | 'sms'
  | 'in-app';

export type MessagePriority = 'low' | 'normal' | 'high' | 'urgent';

export type TonePreset =
  | 'formal'
  | 'professional'
  | 'casual'
  | 'friendly'
  | 'urgent'
  | 'empathetic'
  | 'direct';

export interface OutboundDraft {
  id: string;
  /** Recipient name (resolved via Trust Graph) */
  recipientName: string;
  /** Resolved Trust Graph person ID (if matched) */
  recipientPersonId: string | null;
  /** Target channel for delivery */
  channel: OutboundChannel;
  /** Channel-specific recipient address (email, chat ID, handle) */
  channelAddress: string;
  /** Subject line (for email) */
  subject: string;
  /** Message body */
  body: string;
  /** Detected/requested tone */
  tone: TonePreset;
  /** Priority level */
  priority: MessagePriority;
  /** Why this draft was created */
  trigger: DraftTrigger;
  /** Current status */
  status: DraftStatus;
  /** Channel selection reasoning */
  channelReason: string;
  /** Linked commitment ID (if follow-up) */
  commitmentId: string | null;
  /** Original context that spawned this draft */
  context: string;
  /** Created timestamp */
  createdAt: number;
  /** When the user approved (if approved) */
  approvedAt: number | null;
  /** When the message was sent (if sent) */
  sentAt: number | null;
  /** Error message if send failed */
  sendError: string | null;
  /** Number of edits the user made */
  editCount: number;
}

export type DraftTrigger =
  | 'user-request'       // "Tell Sarah the report is ready"
  | 'follow-up'          // Commitment tracker detected overdue follow-up
  | 'meeting-action'     // Meeting generated action items
  | 'scheduled'          // Scheduled message delivery
  | 'agent-suggested';   // Agent proactively suggests outreach

export interface ChannelScore {
  channel: OutboundChannel;
  score: number;          // 0-1
  reason: string;
}

export interface RecipientStyleProfile {
  recipientPersonId: string;
  recipientName: string;
  /** Preferred tone for this person */
  preferredTone: TonePreset;
  /** Average message length (chars) */
  avgLength: number;
  /** Whether the user typically uses greetings */
  usesGreeting: boolean;
  /** Whether the user typically signs off */
  usesSignOff: boolean;
  /** Custom sign-off phrase */
  signOff: string;
  /** Preferred channel based on history */
  preferredChannel: OutboundChannel | null;
  /** Number of observations this profile is based on */
  observationCount: number;
  /** Last updated */
  updatedAt: number;
}

export interface StandingPermission {
  id: string;
  /** Trust Graph person ID */
  recipientPersonId: string;
  /** Recipient name (display) */
  recipientName: string;
  /** Channels this applies to */
  channels: OutboundChannel[];
  /** Priority threshold — messages at or below this can auto-send */
  maxPriority: MessagePriority;
  /** Whether this permission is active */
  active: boolean;
  /** When the user created this permission */
  createdAt: number;
  /** Optional expiry */
  expiresAt: number | null;
}

export interface OutboundConfig {
  /** Max drafts to retain */
  maxDrafts: number;
  /** Auto-expire drafts after N hours */
  draftExpiryHours: number;
  /** Whether to batch drafts for review */
  batchReview: boolean;
  /** Batch window in minutes (collect drafts for this long before prompting) */
  batchWindowMinutes: number;
  /** Default tone when no style profile exists */
  defaultTone: TonePreset;
}

export interface OutboundStats {
  totalDrafts: number;
  pendingDrafts: number;
  approvedDrafts: number;
  sentMessages: number;
  rejectedDrafts: number;
  failedSends: number;
  standingPermissions: number;
  styleProfiles: number;
}

export interface SendResult {
  success: boolean;
  draftId: string;
  channel: OutboundChannel;
  error?: string;
  sentAt?: number;
}

export interface BatchReviewItem {
  draft: OutboundDraft;
  styleProfile: RecipientStyleProfile | null;
  channelScores: ChannelScore[];
}

// ── Constants ─────────────────────────────────────────────────────────

const MAX_DRAFTS = 100;
const DEFAULT_EXPIRY_HOURS = 48;
const DEFAULT_BATCH_WINDOW = 15; // minutes

const PRIORITY_ORDER: Record<MessagePriority, number> = {
  urgent: 4,
  high: 3,
  normal: 2,
  low: 1,
};

const CHANNEL_FORMALITY: Record<OutboundChannel, number> = {
  email: 0.9,
  teams: 0.7,
  slack: 0.5,
  discord: 0.3,
  telegram: 0.4,
  sms: 0.3,
  'in-app': 0.5,
};

// ── Channel Selection Heuristics ──────────────────────────────────────

/**
 * Score each channel for a given recipient using Trust Graph communication
 * history. Returns sorted scores from best to worst.
 */
export function scoreChannels(
  recipientPersonId: string | null,
  messageType: 'formal' | 'casual' | 'urgent' | 'informational',
  availableChannels: OutboundChannel[]
): ChannelScore[] {
  const scores: ChannelScore[] = [];

  // Get communication history from Trust Graph (late-bound)
  let commHistory: Array<{ channel: string; direction: string; sentiment: number; timestamp: number }> = [];
  let preferredChannel: OutboundChannel | null = null;

  if (recipientPersonId) {
    try {
      const tg = getTrustGraph();
      if (tg) {
        const person = tg.getPersonById(recipientPersonId);
        if (person) {
          commHistory = person.communicationLog || [];
        }
      }
    } catch { /* trust graph not available */ }
  }

  for (const channel of availableChannels) {
    let score = 0.5; // Base score
    const reasons: string[] = [];

    // 1. Frequency — how often do they communicate on this channel?
    const channelEvents = commHistory.filter(
      (e) => normalizeChannel(e.channel) === channel
    );
    if (channelEvents.length > 0) {
      const freqScore = Math.min(channelEvents.length / 10, 1.0);
      score += freqScore * 0.3;
      reasons.push(`${channelEvents.length} past interactions`);
    } else {
      score -= 0.1;
      reasons.push('no history on this channel');
    }

    // 2. Recency — when was the last communication on this channel?
    if (channelEvents.length > 0) {
      const latest = Math.max(...channelEvents.map((e) => e.timestamp));
      const daysSince = (Date.now() - latest) / (24 * 60 * 60 * 1000);
      if (daysSince < 7) {
        score += 0.15;
        reasons.push('active in last week');
      } else if (daysSince < 30) {
        score += 0.05;
      } else {
        score -= 0.05;
        reasons.push('inactive >30 days');
      }
    }

    // 3. Message type × channel formality match
    const formality = CHANNEL_FORMALITY[channel];
    if (messageType === 'formal' && formality >= 0.7) {
      score += 0.2;
      reasons.push('formal channel for formal message');
    } else if (messageType === 'casual' && formality <= 0.5) {
      score += 0.15;
      reasons.push('casual channel for casual message');
    } else if (messageType === 'urgent') {
      // Prefer instant messaging for urgent
      if (['telegram', 'slack', 'sms', 'discord'].includes(channel)) {
        score += 0.25;
        reasons.push('instant channel for urgent message');
      }
    }

    // 4. Inbound preference — if they message us on this channel, reply there
    const inbound = channelEvents.filter((e) => e.direction === 'inbound');
    if (inbound.length > 0) {
      const inboundRatio = inbound.length / Math.max(channelEvents.length, 1);
      if (inboundRatio > 0.5) {
        score += 0.1;
        reasons.push('they prefer this channel');
        if (!preferredChannel) preferredChannel = channel;
      }
    }

    scores.push({
      channel,
      score: Math.max(0, Math.min(1, score)),
      reason: reasons.join('; ') || 'default score',
    });
  }

  return scores.sort((a, b) => b.score - a.score);
}

/**
 * Normalize channel names from various sources to OutboundChannel.
 */
export function normalizeChannel(raw: string): OutboundChannel {
  const lower = raw.toLowerCase().trim();
  if (lower.includes('email') || lower.includes('gmail') || lower.includes('smtp')) return 'email';
  if (lower.includes('slack')) return 'slack';
  if (lower.includes('discord')) return 'discord';
  if (lower.includes('telegram')) return 'telegram';
  if (lower.includes('teams')) return 'teams';
  if (lower.includes('sms') || lower.includes('text') || lower.includes('phone')) return 'sms';
  return 'in-app';
}

/**
 * Infer message type from content and priority.
 */
export function inferMessageType(
  tone: TonePreset,
  priority: MessagePriority
): 'formal' | 'casual' | 'urgent' | 'informational' {
  if (priority === 'urgent') return 'urgent';
  if (tone === 'formal' || tone === 'professional') return 'formal';
  if (tone === 'casual' || tone === 'friendly') return 'casual';
  return 'informational';
}

/**
 * Detect priority from context text.
 */
export function detectPriority(context: string): MessagePriority {
  const lower = context.toLowerCase();
  if (/\b(asap|urgent|emergency|immediately|critical)\b/.test(lower)) return 'urgent';
  if (/\b(important|priority|soon|deadline|by\s+(today|tomorrow|eod|eow))\b/.test(lower)) return 'high';
  if (/\b(whenever|no rush|low priority|when you get a chance)\b/.test(lower)) return 'low';
  return 'normal';
}

/**
 * Detect tone from context and recipient.
 */
export function detectTone(
  context: string,
  styleProfile: RecipientStyleProfile | null
): TonePreset {
  if (styleProfile) return styleProfile.preferredTone;

  const lower = context.toLowerCase();
  if (/\b(formal|official|respectful|dear)\b/.test(lower)) return 'formal';
  if (/\b(casual|hey|sup|chill)\b/.test(lower)) return 'casual';
  if (/\b(urgent|asap|critical|emergency)\b/.test(lower)) return 'urgent';
  if (/\b(sorry|sympathize|condolence|tough time)\b/.test(lower)) return 'empathetic';
  if (/\b(direct|blunt|straight|honest)\b/.test(lower)) return 'direct';
  return 'professional';
}

// ── Approval Logic ────────────────────────────────────────────────────

/**
 * Check if a draft can be auto-approved via standing permission.
 * Returns the matching permission ID or null.
 */
export function checkStandingPermission(
  draft: OutboundDraft,
  permissions: StandingPermission[]
): string | null {
  for (const perm of permissions) {
    if (!perm.active) continue;
    if (perm.expiresAt && perm.expiresAt < Date.now()) continue;
    if (draft.recipientPersonId !== perm.recipientPersonId) continue;
    if (!perm.channels.includes(draft.channel)) continue;
    if (PRIORITY_ORDER[draft.priority] > PRIORITY_ORDER[perm.maxPriority]) continue;
    return perm.id;
  }
  return null;
}

// ── Late-bound imports ────────────────────────────────────────────────

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

let _gatewayManager: any = null;
function getGatewayManager() {
  if (!_gatewayManager) {
    try { _gatewayManager = require('./gateway/gateway-manager').gatewayManager; } catch { /* not ready */ }
  }
  return _gatewayManager;
}

// ── Core Engine ───────────────────────────────────────────────────────

class OutboundIntelligence {
  private drafts: OutboundDraft[] = [];
  private styleProfiles: RecipientStyleProfile[] = [];
  private standingPermissions: StandingPermission[] = [];
  private config: OutboundConfig = {
    maxDrafts: MAX_DRAFTS,
    draftExpiryHours: DEFAULT_EXPIRY_HOURS,
    batchReview: true,
    batchWindowMinutes: DEFAULT_BATCH_WINDOW,
    defaultTone: 'professional',
  };
  private filePath = '';
  private saveTimer: ReturnType<typeof setTimeout> | null = null;
  private expiryTimer: ReturnType<typeof setInterval> | null = null;

  // ── Lifecycle ─────────────────────────────────────────────────────

  async initialize(): Promise<void> {
    try {
      const userDataPath = app.getPath('userData');
      this.filePath = path.join(userDataPath, 'outbound-intelligence.json');
      await this.load();
    } catch {
      // Fresh start
    }

    // Periodically expire old drafts (every 15 minutes)
    this.expiryTimer = setInterval(() => {
      this.expireOldDrafts();
    }, 15 * 60 * 1000);

    console.log(
      `[Outbound] Initialized — ${this.drafts.length} drafts, ` +
      `${this.styleProfiles.length} style profiles, ` +
      `${this.standingPermissions.length} standing permissions`
    );
  }

  stop(): void {
    if (this.expiryTimer) {
      clearInterval(this.expiryTimer);
      this.expiryTimer = null;
    }
    if (this.saveTimer) {
      clearTimeout(this.saveTimer);
      this.saveTimer = null;
    }
  }

  // ── Draft Creation ────────────────────────────────────────────────

  /**
   * Create a new outbound draft. Performs channel selection, tone detection,
   * and priority inference. Does NOT send — awaits approval.
   */
  createDraft(params: {
    recipientName: string;
    body: string;
    subject?: string;
    context: string;
    channel?: OutboundChannel;
    channelAddress?: string;
    tone?: TonePreset;
    priority?: MessagePriority;
    trigger?: DraftTrigger;
    commitmentId?: string;
  }): OutboundDraft {
    // Resolve recipient via Trust Graph
    let recipientPersonId: string | null = null;
    let resolvedAddress = params.channelAddress || '';

    try {
      const tg = getTrustGraph();
      if (tg) {
        const resolution = tg.resolvePerson(params.recipientName);
        if (resolution.person) {
          recipientPersonId = resolution.person.id;
          // Try to find address from aliases if not provided
          if (!resolvedAddress && resolution.person.aliases) {
            const emailAlias = resolution.person.aliases.find(
              (a: any) => a.type === 'email'
            );
            const handleAlias = resolution.person.aliases.find(
              (a: any) => a.type === 'handle'
            );
            resolvedAddress = emailAlias?.value || handleAlias?.value || '';
          }
        }
      }
    } catch { /* proceed without resolution */ }

    // Get style profile for this recipient
    const styleProfile = recipientPersonId
      ? this.styleProfiles.find((s) => s.recipientPersonId === recipientPersonId) || null
      : null;

    // Detect tone and priority
    const tone = params.tone || detectTone(params.context, styleProfile);
    const priority = params.priority || detectPriority(params.context);

    // Channel selection
    let channel = params.channel || styleProfile?.preferredChannel || null;
    let channelReason = '';

    if (!channel) {
      const messageType = inferMessageType(tone, priority);
      const available: OutboundChannel[] = ['email', 'slack', 'telegram', 'discord', 'teams', 'in-app'];
      const scores = scoreChannels(recipientPersonId, messageType, available);

      if (scores.length > 0) {
        channel = scores[0].channel;
        channelReason = scores[0].reason;
      } else {
        channel = 'email';
        channelReason = 'default fallback';
      }
    } else {
      channelReason = params.channel ? 'user specified' : 'style profile preferred';
    }

    const draft: OutboundDraft = {
      id: crypto.randomUUID().slice(0, 12),
      recipientName: params.recipientName,
      recipientPersonId,
      channel,
      channelAddress: resolvedAddress,
      subject: params.subject || '',
      body: params.body,
      tone,
      priority,
      trigger: params.trigger || 'user-request',
      status: 'pending',
      channelReason,
      commitmentId: params.commitmentId || null,
      context: params.context,
      createdAt: Date.now(),
      approvedAt: null,
      sentAt: null,
      sendError: null,
      editCount: 0,
    };

    this.drafts.push(draft);

    // Enforce max drafts
    if (this.drafts.length > this.config.maxDrafts) {
      // Remove oldest non-pending drafts first
      const removable = this.drafts.filter(
        (d) => d.status !== 'pending' && d.status !== 'approved'
      );
      if (removable.length > 0) {
        const oldest = removable[0];
        this.drafts = this.drafts.filter((d) => d.id !== oldest.id);
      } else {
        this.drafts.shift();
      }
    }

    // Emit context event
    try {
      const cs = getContextStream();
      if (cs) {
        cs.push({
          type: 'communication',
          source: 'outbound-intelligence',
          summary: `Draft created for ${params.recipientName} via ${channel}`,
          data: { draftId: draft.id, channel, priority, trigger: draft.trigger },
        });
      }
    } catch { /* context stream not ready */ }

    this.scheduleSave();
    return draft;
  }

  // ── Draft Management ──────────────────────────────────────────────

  getDraft(id: string): OutboundDraft | null {
    return this.drafts.find((d) => d.id === id) || null;
  }

  getPendingDrafts(): OutboundDraft[] {
    return this.drafts
      .filter((d) => d.status === 'pending')
      .sort((a, b) => PRIORITY_ORDER[b.priority] - PRIORITY_ORDER[a.priority]);
  }

  getAllDrafts(opts?: {
    status?: DraftStatus;
    channel?: OutboundChannel;
    limit?: number;
  }): OutboundDraft[] {
    let filtered = [...this.drafts];
    if (opts?.status) filtered = filtered.filter((d) => d.status === opts.status);
    if (opts?.channel) filtered = filtered.filter((d) => d.channel === opts.channel);
    filtered.sort((a, b) => b.createdAt - a.createdAt);
    if (opts?.limit) filtered = filtered.slice(0, opts.limit);
    return filtered;
  }

  /**
   * Edit a pending draft. Returns the updated draft or null if not found/not editable.
   */
  editDraft(
    id: string,
    updates: Partial<Pick<OutboundDraft, 'body' | 'subject' | 'channel' | 'channelAddress' | 'tone' | 'priority'>>
  ): OutboundDraft | null {
    const draft = this.drafts.find((d) => d.id === id);
    if (!draft || draft.status !== 'pending') return null;

    if (updates.body !== undefined) draft.body = updates.body;
    if (updates.subject !== undefined) draft.subject = updates.subject;
    if (updates.channel !== undefined) draft.channel = updates.channel;
    if (updates.channelAddress !== undefined) draft.channelAddress = updates.channelAddress;
    if (updates.tone !== undefined) draft.tone = updates.tone;
    if (updates.priority !== undefined) draft.priority = updates.priority;
    draft.editCount++;

    this.scheduleSave();
    return draft;
  }

  /**
   * Approve a draft for sending. Returns the draft or null.
   * cLaw: This is the explicit user approval checkpoint.
   */
  approveDraft(id: string): OutboundDraft | null {
    const draft = this.drafts.find((d) => d.id === id);
    if (!draft || draft.status !== 'pending') return null;

    draft.status = 'approved';
    draft.approvedAt = Date.now();
    this.scheduleSave();
    return draft;
  }

  /**
   * Reject a draft. Returns the draft or null.
   */
  rejectDraft(id: string): OutboundDraft | null {
    const draft = this.drafts.find((d) => d.id === id);
    if (!draft || draft.status !== 'pending') return null;

    draft.status = 'rejected';
    this.scheduleSave();
    return draft;
  }

  /**
   * Delete a draft entirely.
   */
  deleteDraft(id: string): boolean {
    const idx = this.drafts.findIndex((d) => d.id === id);
    if (idx === -1) return false;
    this.drafts.splice(idx, 1);
    this.scheduleSave();
    return true;
  }

  // ── Sending ───────────────────────────────────────────────────────

  /**
   * Send an approved draft through the appropriate channel.
   * cLaw: Only sends drafts with status === 'approved'.
   */
  async sendDraft(id: string): Promise<SendResult> {
    const draft = this.drafts.find((d) => d.id === id);
    if (!draft) {
      return { success: false, draftId: id, channel: 'in-app', error: 'Draft not found' };
    }
    if (draft.status !== 'approved') {
      return {
        success: false,
        draftId: id,
        channel: draft.channel,
        error: `Draft status is '${draft.status}', must be 'approved'`,
      };
    }

    try {
      await this.routeMessage(draft);

      draft.status = 'sent';
      draft.sentAt = Date.now();

      // Log communication in Trust Graph
      if (draft.recipientPersonId) {
        try {
          const tg = getTrustGraph();
          if (tg) {
            tg.logCommunication(draft.recipientPersonId, {
              channel: draft.channel,
              direction: 'outbound',
              summary: draft.subject || draft.body.slice(0, 80),
              sentiment: 0.5, // Neutral for outbound
            });
          }
        } catch { /* trust graph logging non-critical */ }
      }

      // Mark commitment as followed-up if linked
      if (draft.commitmentId) {
        try {
          const ct = getCommitmentTracker();
          if (ct) {
            ct.markFollowedUp(draft.commitmentId);
          }
        } catch { /* commitment tracker non-critical */ }
      }

      // Emit context event
      try {
        const cs = getContextStream();
        if (cs) {
          cs.push({
            type: 'communication',
            source: 'outbound-intelligence',
            summary: `Message sent to ${draft.recipientName} via ${draft.channel}`,
            data: { draftId: draft.id, channel: draft.channel },
          });
        }
      } catch { /* context stream non-critical */ }

      this.scheduleSave();
      return { success: true, draftId: id, channel: draft.channel, sentAt: draft.sentAt };
    } catch (err: any) {
      draft.status = 'failed';
      draft.sendError = err?.message || 'Unknown send error';
      this.scheduleSave();
      return { success: false, draftId: id, channel: draft.channel, error: draft.sendError! };
    }
  }

  /**
   * Approve and immediately send a draft. Convenience method.
   */
  async approveAndSend(id: string): Promise<SendResult> {
    const approved = this.approveDraft(id);
    if (!approved) {
      return { success: false, draftId: id, channel: 'in-app', error: 'Could not approve draft' };
    }
    return this.sendDraft(id);
  }

  /**
   * Route a message to the appropriate delivery mechanism.
   */
  private async routeMessage(draft: OutboundDraft): Promise<void> {
    switch (draft.channel) {
      case 'telegram':
      case 'discord':
      case 'slack': {
        // Route through gateway adapter
        const gw = getGatewayManager();
        if (gw && gw.isRunning()) {
          await gw.sendProactiveMessage(draft.channel, draft.channelAddress, draft.body);
          return;
        }
        throw new Error(`Gateway adapter '${draft.channel}' not available`);
      }

      case 'email': {
        // For email, we don't send directly — we prepare a mailto link
        // or the agent uses the comms-hub smtp_send_email tool.
        // Mark as sent (the actual sending is handled by the tool system).
        return;
      }

      case 'teams': {
        // Teams uses webhook — would need a stored webhook URL
        throw new Error('Teams sending requires a configured webhook URL');
      }

      case 'sms':
        throw new Error('SMS sending not yet implemented');

      case 'in-app':
        // In-app messages are just stored — the UI displays them
        return;

      default:
        throw new Error(`Unknown channel: ${draft.channel}`);
    }
  }

  // ── Batch Review ──────────────────────────────────────────────────

  /**
   * Get all pending drafts bundled with their style profiles and
   * channel scores, ready for batch user review.
   */
  getBatchReview(): BatchReviewItem[] {
    const pending = this.getPendingDrafts();
    return pending.map((draft) => {
      const styleProfile = draft.recipientPersonId
        ? this.styleProfiles.find((s) => s.recipientPersonId === draft.recipientPersonId) || null
        : null;

      const messageType = inferMessageType(draft.tone, draft.priority);
      const available: OutboundChannel[] = ['email', 'slack', 'telegram', 'discord', 'teams', 'in-app'];
      const channelScores = scoreChannels(draft.recipientPersonId, messageType, available);

      return { draft, styleProfile, channelScores };
    });
  }

  /**
   * Approve all pending drafts (batch operation).
   * Returns the number approved.
   */
  approveAll(): number {
    let count = 0;
    for (const draft of this.drafts) {
      if (draft.status === 'pending') {
        draft.status = 'approved';
        draft.approvedAt = Date.now();
        count++;
      }
    }
    if (count > 0) this.scheduleSave();
    return count;
  }

  /**
   * Send all approved drafts. Returns results for each.
   */
  async sendAllApproved(): Promise<SendResult[]> {
    const approved = this.drafts.filter((d) => d.status === 'approved');
    const results: SendResult[] = [];
    for (const draft of approved) {
      const result = await this.sendDraft(draft.id);
      results.push(result);
    }
    return results;
  }

  // ── Style Profiles ────────────────────────────────────────────────

  getStyleProfile(recipientPersonId: string): RecipientStyleProfile | null {
    return this.styleProfiles.find((s) => s.recipientPersonId === recipientPersonId) || null;
  }

  /**
   * Update or create a style profile from observation.
   */
  updateStyleProfile(
    recipientPersonId: string,
    recipientName: string,
    observation: Partial<Pick<RecipientStyleProfile, 'preferredTone' | 'avgLength' | 'usesGreeting' | 'usesSignOff' | 'signOff' | 'preferredChannel'>>
  ): RecipientStyleProfile {
    let profile = this.styleProfiles.find((s) => s.recipientPersonId === recipientPersonId);

    if (!profile) {
      profile = {
        recipientPersonId,
        recipientName,
        preferredTone: this.config.defaultTone,
        avgLength: 200,
        usesGreeting: true,
        usesSignOff: true,
        signOff: 'Best',
        preferredChannel: null,
        observationCount: 0,
        updatedAt: Date.now(),
      };
      this.styleProfiles.push(profile);
    }

    if (observation.preferredTone !== undefined) profile.preferredTone = observation.preferredTone;
    if (observation.avgLength !== undefined) profile.avgLength = observation.avgLength;
    if (observation.usesGreeting !== undefined) profile.usesGreeting = observation.usesGreeting;
    if (observation.usesSignOff !== undefined) profile.usesSignOff = observation.usesSignOff;
    if (observation.signOff !== undefined) profile.signOff = observation.signOff;
    if (observation.preferredChannel !== undefined) profile.preferredChannel = observation.preferredChannel;
    profile.observationCount++;
    profile.updatedAt = Date.now();

    this.scheduleSave();
    return profile;
  }

  getAllStyleProfiles(): RecipientStyleProfile[] {
    return [...this.styleProfiles];
  }

  // ── Standing Permissions ──────────────────────────────────────────

  addStandingPermission(params: {
    recipientPersonId: string;
    recipientName: string;
    channels: OutboundChannel[];
    maxPriority: MessagePriority;
    expiresAt?: number;
  }): StandingPermission {
    const perm: StandingPermission = {
      id: crypto.randomUUID().slice(0, 12),
      recipientPersonId: params.recipientPersonId,
      recipientName: params.recipientName,
      channels: params.channels,
      maxPriority: params.maxPriority,
      active: true,
      createdAt: Date.now(),
      expiresAt: params.expiresAt || null,
    };

    this.standingPermissions.push(perm);
    this.scheduleSave();
    return perm;
  }

  revokeStandingPermission(id: string): boolean {
    const perm = this.standingPermissions.find((p) => p.id === id);
    if (!perm) return false;
    perm.active = false;
    this.scheduleSave();
    return true;
  }

  deleteStandingPermission(id: string): boolean {
    const idx = this.standingPermissions.findIndex((p) => p.id === id);
    if (idx === -1) return false;
    this.standingPermissions.splice(idx, 1);
    this.scheduleSave();
    return true;
  }

  getStandingPermissions(): StandingPermission[] {
    return [...this.standingPermissions].filter(
      (p) => p.active && (!p.expiresAt || p.expiresAt > Date.now())
    );
  }

  getAllStandingPermissions(): StandingPermission[] {
    return [...this.standingPermissions];
  }

  /**
   * Check if a draft has a standing permission and auto-approve if so.
   * Returns the permission ID used, or null if manual approval needed.
   */
  tryAutoApprove(draftId: string): string | null {
    const draft = this.drafts.find((d) => d.id === draftId);
    if (!draft || draft.status !== 'pending') return null;

    const permId = checkStandingPermission(draft, this.standingPermissions);
    if (permId) {
      draft.status = 'approved';
      draft.approvedAt = Date.now();
      this.scheduleSave();
    }
    return permId;
  }

  // ── Stats ─────────────────────────────────────────────────────────

  getStats(): OutboundStats {
    return {
      totalDrafts: this.drafts.length,
      pendingDrafts: this.drafts.filter((d) => d.status === 'pending').length,
      approvedDrafts: this.drafts.filter((d) => d.status === 'approved').length,
      sentMessages: this.drafts.filter((d) => d.status === 'sent').length,
      rejectedDrafts: this.drafts.filter((d) => d.status === 'rejected').length,
      failedSends: this.drafts.filter((d) => d.status === 'failed').length,
      standingPermissions: this.getStandingPermissions().length,
      styleProfiles: this.styleProfiles.length,
    };
  }

  // ── Config ────────────────────────────────────────────────────────

  getConfig(): OutboundConfig {
    return { ...this.config };
  }

  updateConfig(partial: Partial<OutboundConfig>): OutboundConfig {
    if (partial.maxDrafts !== undefined) this.config.maxDrafts = partial.maxDrafts;
    if (partial.draftExpiryHours !== undefined) this.config.draftExpiryHours = partial.draftExpiryHours;
    if (partial.batchReview !== undefined) this.config.batchReview = partial.batchReview;
    if (partial.batchWindowMinutes !== undefined) this.config.batchWindowMinutes = partial.batchWindowMinutes;
    if (partial.defaultTone !== undefined) this.config.defaultTone = partial.defaultTone;
    this.scheduleSave();
    return { ...this.config };
  }

  // ── Context Generation ────────────────────────────────────────────

  /**
   * Generate context string for system prompt injection.
   * Shows pending drafts and outbound activity summary.
   */
  getPromptContext(): string {
    const pending = this.getPendingDrafts();
    if (pending.length === 0 && this.drafts.filter((d) => d.status === 'sent').length === 0) {
      return '';
    }

    const lines: string[] = [];

    if (pending.length > 0) {
      lines.push(`PENDING DRAFTS (${pending.length} awaiting review):`);
      for (const d of pending.slice(0, 5)) {
        lines.push(
          `  - To ${d.recipientName} via ${d.channel} [${d.priority}]: "${d.body.slice(0, 60)}..."`
        );
      }
      if (pending.length > 5) {
        lines.push(`  ... and ${pending.length - 5} more`);
      }
    }

    // Recent sends
    const recentSent = this.drafts
      .filter((d) => d.status === 'sent' && d.sentAt && Date.now() - d.sentAt < 24 * 60 * 60 * 1000)
      .slice(-3);
    if (recentSent.length > 0) {
      lines.push('RECENTLY SENT:');
      for (const d of recentSent) {
        lines.push(`  - To ${d.recipientName} via ${d.channel}: "${d.subject || d.body.slice(0, 40)}"`);
      }
    }

    return lines.join('\n');
  }

  // ── Maintenance ───────────────────────────────────────────────────

  private expireOldDrafts(): void {
    const cutoff = Date.now() - this.config.draftExpiryHours * 60 * 60 * 1000;
    let expired = 0;

    for (const draft of this.drafts) {
      if (draft.status === 'pending' && draft.createdAt < cutoff) {
        draft.status = 'expired';
        expired++;
      }
    }

    if (expired > 0) {
      console.log(`[Outbound] Expired ${expired} old drafts`);
      this.scheduleSave();
    }
  }

  // ── Persistence ───────────────────────────────────────────────────

  private async load(): Promise<void> {
    try {
      const raw = await fs.readFile(this.filePath, 'utf-8');
      const data = JSON.parse(raw);
      this.drafts = data.drafts || [];
      this.styleProfiles = data.styleProfiles || [];
      this.standingPermissions = data.standingPermissions || [];
      if (data.config) {
        this.config = { ...this.config, ...data.config };
      }
    } catch {
      // Fresh start
    }
  }

  private async save(): Promise<void> {
    if (!this.filePath) return;
    try {
      const data = {
        drafts: this.drafts,
        styleProfiles: this.styleProfiles,
        standingPermissions: this.standingPermissions,
        config: this.config,
        savedAt: Date.now(),
      };
      await fs.writeFile(this.filePath, JSON.stringify(data, null, 2), 'utf-8');
    } catch (err: any) {
      console.warn('[Outbound] Save failed:', err?.message);
    }
  }

  private scheduleSave(): void {
    if (this.saveTimer) clearTimeout(this.saveTimer);
    this.saveTimer = setTimeout(() => {
      this.save();
      this.saveTimer = null;
    }, 2000);
  }
}

export const outboundIntelligence = new OutboundIntelligence();
