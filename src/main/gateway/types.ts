/**
 * gateway/types.ts — Core types for the multi-channel messaging gateway.
 *
 * Defines the interfaces that all gateway components share: messages,
 * responses, channel adapters, trust tiers, and capability policies.
 */

// ── Trust Tiers ──────────────────────────────────────────────────────
// Ordered from most-trusted to least-trusted.
// Each tier maps to a capability policy that gates tool access,
// memory permissions, and iteration limits.

export type TrustTier = 'local' | 'owner-dm' | 'approved-dm' | 'group' | 'public';

// ── Messages ─────────────────────────────────────────────────────────

export interface GatewayMessage {
  /** Unique message ID (from the channel) */
  id: string;
  /** Channel identifier: 'telegram' | 'discord' | 'slack' | etc. */
  channel: string;
  /** Channel-specific sender ID (e.g. Telegram user ID) */
  senderId: string;
  /** Human-readable sender name */
  senderName: string;
  /** Message text content */
  text: string;
  /** Unix ms timestamp */
  timestamp: number;
  /** Trust tier — initially 'public', resolved by trust engine */
  trustTier: TrustTier;
  /** Thread/conversation ID for group threading */
  threadId?: string;
  /** If replying to a specific message */
  replyToId?: string;
  /** Channel-specific extras (e.g. chat type, attachments metadata) */
  metadata?: Record<string, unknown>;
}

export interface GatewayResponse {
  /** Response text */
  text: string;
  /** Target channel */
  channel: string;
  /** Target recipient ID (chat ID for Telegram, channel ID for Discord, etc.) */
  recipientId: string;
  /** Thread ID for threaded replies */
  threadId?: string;
  /** Channel-specific extras (e.g. parse_mode for Telegram) */
  metadata?: Record<string, unknown>;
}

// ── Channel Adapters ─────────────────────────────────────────────────

export interface ChannelAdapter {
  /** Unique adapter ID (e.g. 'telegram', 'discord') */
  id: string;
  /** Human-readable label */
  label: string;
  /** Start the adapter (connect, begin polling/listening) */
  start(): Promise<void>;
  /** Stop the adapter gracefully */
  stop(): Promise<void>;
  /** Send a response message through this channel */
  sendMessage(response: GatewayResponse): Promise<void>;
  /** Whether the adapter is currently running */
  isRunning(): boolean;
  /**
   * Message callback — set by GatewayManager.
   * Adapters call this when an inbound message arrives.
   */
  onMessage: ((msg: GatewayMessage) => void) | null;
}

// ── Trust Policies ───────────────────────────────────────────────────

export interface TrustPolicy {
  tier: TrustTier;
  /** Max Claude tool-use iterations for this tier */
  maxIterations: number;
  /** Glob-like patterns for allowed tool names (e.g. ['firecrawl_*', 'web_search']) */
  toolAllowPatterns: string[];
  /** Glob-like patterns for blocked tool names (e.g. ['powershell_*', 'run_command']) */
  toolBlockPatterns: string[];
  /** Whether this tier can read the user's memory */
  memoryRead: boolean;
  /** Whether this tier can write/extract new memories */
  memoryWrite: boolean;
  /** Whether this tier can create scheduled tasks */
  canTriggerScheduler: boolean;
  /** Whether this tier can access desktop automation tools */
  canAccessDesktop: boolean;
  /** Max messages per minute from a single sender */
  rateLimitPerMinute: number;
}

// ── Paired Identities ────────────────────────────────────────────────

export interface PairedIdentity {
  /** Unique pairing ID */
  id: string;
  /** Channel where this identity is paired */
  channel: string;
  /** Channel-specific sender ID */
  senderId: string;
  /** Human-readable name */
  name: string;
  /** Assigned trust tier */
  tier: TrustTier;
  /** When the pairing was established */
  pairedAt: number;
  /** Optional notes about this contact */
  notes?: string;
}

// ── Pending Pairings ─────────────────────────────────────────────────

export interface PendingPairing {
  /** 6-character pairing code (e.g. 'A7X-9K2') */
  code: string;
  /** Channel the pairing request came from */
  channel: string;
  /** Sender ID requesting pairing */
  senderId: string;
  /** Sender's display name */
  senderName: string;
  /** When the pairing code was generated */
  createdAt: number;
  /** Expiry time (15 minutes from creation) */
  expiresAt: number;
}

// ── Audit Log Entry ──────────────────────────────────────────────────

/**
 * Action ledger entry type discriminator.
 * Current types are messaging-related; Track X will extend with
 * 'credit_transfer', 'escrow_lock', 'attestation', etc.
 */
export type AuditEntryType = 'message' | 'tool_use' | 'claw_check' | string;

export interface AuditEntry {
  /** Unix ms timestamp */
  ts: number;
  /** Entry type discriminator — extensible for future ledger entry types (Track X foundation) */
  type?: AuditEntryType;
  /** Direction: inbound or outbound */
  dir: 'in' | 'out';
  /** Channel identifier */
  channel: string;
  /** Sender ID (for inbound) or recipient ID (for outbound) */
  sender?: string;
  recipient?: string;
  /** Resolved trust tier */
  trust?: TrustTier;
  /** Message text (truncated to 500 chars for storage) */
  text: string;
  /** Original message ID */
  msgId?: string;
  /** Number of tool calls made processing this message */
  toolCalls?: number;
  /** Processing time in ms */
  durationMs?: number;
}

// ── Gateway Status ───────────────────────────────────────────────────

export interface GatewayStatus {
  enabled: boolean;
  channels: Array<{
    id: string;
    label: string;
    running: boolean;
    error?: string;
  }>;
  pairedIdentities: number;
  totalMessagesHandled: number;
}
