/**
 * Track III, Phase 1: Activity Ingestion — Unified Context Stream
 *
 * The Nervous System. Aggregates events from all context sources
 * (ambient, clipboard, sentiment, notifications, tool invocations)
 * into a single ordered stream with deduplication, throttling, and
 * a rolling memory-bounded buffer.
 *
 * Provides both a "latest state" snapshot view and a time-windowed
 * event stream view for downstream consumers (personality.ts,
 * prompt-budget.ts, context graph in Phase 2).
 *
 * cLaw Gate: Event stream is in-memory only, never persisted to disk.
 * Screen capture frames are NOT stored in the stream (too large);
 * only OCR-extracted text summaries if available.
 */

// ── Event Types ──────────────────────────────────────────────────────

export type ContextEventType =
  | 'ambient'        // App focus, window title, inferred task
  | 'clipboard'      // Clipboard content change
  | 'sentiment'      // User mood shift
  | 'notification'   // OS notification captured
  | 'tool-invoke'    // Agent tool call (records what the agent did)
  | 'calendar'       // Calendar event approaching or starting
  | 'communication'  // Email/message sent or received
  | 'git'            // Git commit, branch change, etc.
  | 'screen-text'    // OCR-extracted text from screen capture
  | 'user-input'     // User typed something (topic/intent only, not content)
  | 'system';        // System events (session start, error, etc.)

export interface ContextEvent {
  id: string;                          // Unique event ID
  type: ContextEventType;
  timestamp: number;                   // Date.now()
  source: string;                      // e.g. 'ambient-engine', 'clipboard-intelligence'
  summary: string;                     // One-line human-readable summary
  data: Record<string, unknown>;       // Type-specific payload (no sensitive content)
  dedupeKey?: string;                  // If set, newer event with same key replaces older
  ttlMs?: number;                      // Custom TTL (default: buffer window)
}

export interface ContextSnapshot {
  activeApp: string;
  windowTitle: string;
  inferredTask: string;
  focusStreak: number;                 // seconds
  currentMood: string;
  moodConfidence: number;
  energyLevel: number;
  lastClipboardType: string;
  lastClipboardPreview: string;
  recentToolCalls: string[];           // last 5 tool names
  recentNotifications: string[];       // last 3 notification summaries
  activeWorkStream: string;            // placeholder for Phase 2
  lastUpdated: number;
}

export interface ContextStreamConfig {
  maxBufferSize: number;               // Max events in buffer (default: 2000)
  maxBufferAgeMs: number;              // Max age of events (default: 4 hours)
  dedupeWindowMs: number;              // Window for dedup key matching (default: 30s)
  throttleMs: Record<ContextEventType, number>;  // Per-type minimum interval
  enabled: boolean;
}

export interface ContextStreamStatus {
  enabled: boolean;
  bufferSize: number;
  maxBufferSize: number;
  oldestEventAge: number;              // ms since oldest event
  eventCounts: Record<string, number>; // count per type
  eventsPerMinute: number;             // throughput in last 60s
  memoryEstimateKb: number;
}

// ── Constants ────────────────────────────────────────────────────────

const DEFAULT_CONFIG: ContextStreamConfig = {
  maxBufferSize: 2000,
  maxBufferAgeMs: 4 * 60 * 60 * 1000,       // 4 hours
  dedupeWindowMs: 30_000,                     // 30 seconds
  throttleMs: {
    'ambient': 8_000,                         // At most every 8s (ambient polls at 10s)
    'clipboard': 1_500,                       // At most every 1.5s (polls at 2s)
    'sentiment': 5_000,                       // At most every 5s
    'notification': 500,                      // Near-realtime
    'tool-invoke': 0,                         // Every invocation
    'calendar': 60_000,                       // At most once per minute
    'communication': 2_000,                   // At most every 2s
    'git': 5_000,                             // At most every 5s
    'screen-text': 10_000,                    // At most every 10s
    'user-input': 1_000,                      // At most every 1s
    'system': 0,                              // Every event
  },
  enabled: true,
};

const MAX_SUMMARY_LENGTH = 200;
const MAX_DATA_KEYS = 20;
const CONTEXT_STRING_MAX_EVENTS = 15;
const SNAPSHOT_TOOL_HISTORY = 5;
const SNAPSHOT_NOTIF_HISTORY = 3;

let idCounter = 0;
function nextId(): string {
  return `ctx-${Date.now().toString(36)}-${(idCounter++).toString(36)}`;
}

// ── Context Stream Engine ────────────────────────────────────────────

export class ContextStream {
  private buffer: ContextEvent[] = [];
  private config: ContextStreamConfig;
  private lastEmitTime: Map<ContextEventType, number> = new Map();
  private dedupeIndex: Map<string, number> = new Map(); // dedupeKey → buffer index
  private snapshot: ContextSnapshot;
  private listeners: Array<(event: ContextEvent) => void> = [];

  constructor(config: Partial<ContextStreamConfig> = {}) {
    this.config = { ...DEFAULT_CONFIG, ...config };
    if (config.throttleMs) {
      this.config.throttleMs = { ...DEFAULT_CONFIG.throttleMs, ...config.throttleMs };
    }
    this.snapshot = this.emptySnapshot();
  }

  // ── Core: Push Event ─────────────────────────────────────────────

  /**
   * Push a new event into the context stream.
   * Returns the event if accepted, null if throttled/disabled.
   */
  push(event: Omit<ContextEvent, 'id' | 'timestamp'>): ContextEvent | null {
    if (!this.config.enabled) return null;

    // Throttle check
    const now = Date.now();
    const lastEmit = this.lastEmitTime.get(event.type) ?? 0;
    const minInterval = this.config.throttleMs[event.type] ?? 0;
    if (minInterval > 0 && (now - lastEmit) < minInterval) {
      return null; // Throttled
    }

    // Sanitize
    const sanitized: ContextEvent = {
      id: nextId(),
      timestamp: now,
      type: event.type,
      source: event.source,
      summary: (event.summary || '').slice(0, MAX_SUMMARY_LENGTH),
      data: this.sanitizeData(event.data || {}),
      dedupeKey: event.dedupeKey,
      ttlMs: event.ttlMs,
    };

    // Dedup: replace existing event with same dedupeKey within window
    if (sanitized.dedupeKey) {
      const existingIdx = this.dedupeIndex.get(sanitized.dedupeKey);
      if (existingIdx !== undefined && existingIdx < this.buffer.length) {
        const existing = this.buffer[existingIdx];
        if (existing && (now - existing.timestamp) < this.config.dedupeWindowMs) {
          // Replace in-place
          this.buffer[existingIdx] = sanitized;
          this.lastEmitTime.set(event.type, now);
          this.updateSnapshot(sanitized);
          this.notifyListeners(sanitized);
          return sanitized;
        }
      }
      // Track new dedup key position
      this.dedupeIndex.set(sanitized.dedupeKey, this.buffer.length);
    }

    // Append
    this.buffer.push(sanitized);
    this.lastEmitTime.set(event.type, now);

    // Evict if over size limit
    if (this.buffer.length > this.config.maxBufferSize) {
      this.evictOldest(this.buffer.length - this.config.maxBufferSize);
    }

    this.updateSnapshot(sanitized);
    this.notifyListeners(sanitized);
    return sanitized;
  }

  // ── Query: Latest Snapshot ───────────────────────────────────────

  getSnapshot(): ContextSnapshot {
    return { ...this.snapshot };
  }

  // ── Query: Recent Events ─────────────────────────────────────────

  /**
   * Get recent events, optionally filtered by type and time window.
   */
  getRecent(opts?: {
    limit?: number;
    types?: ContextEventType[];
    sinceMs?: number;
  }): ContextEvent[] {
    const now = Date.now();
    const limit = opts?.limit ?? 50;
    const sinceMs = opts?.sinceMs ?? this.config.maxBufferAgeMs;

    let events = this.buffer.filter(e => (now - e.timestamp) <= sinceMs);

    if (opts?.types && opts.types.length > 0) {
      const typeSet = new Set(opts.types);
      events = events.filter(e => typeSet.has(e.type));
    }

    // Return most recent first
    return events.slice(-limit).reverse();
  }

  // ── Query: Events by Type ────────────────────────────────────────

  getByType(type: ContextEventType, limit = 10): ContextEvent[] {
    return this.buffer
      .filter(e => e.type === type)
      .slice(-limit)
      .reverse();
  }

  // ── Query: Latest of Each Type ───────────────────────────────────

  getLatestByType(): Map<ContextEventType, ContextEvent> {
    const latest = new Map<ContextEventType, ContextEvent>();
    // Walk backward for efficiency
    for (let i = this.buffer.length - 1; i >= 0; i--) {
      const e = this.buffer[i];
      if (!latest.has(e.type)) {
        latest.set(e.type, e);
      }
    }
    return latest;
  }

  // ── Context String Generation ────────────────────────────────────

  /**
   * Generate a markdown context string for system prompt injection.
   * Replaces individual getContextString() calls from ambient, clipboard, etc.
   */
  getContextString(): string {
    if (!this.config.enabled || this.buffer.length === 0) return '';

    const snap = this.snapshot;
    const lines: string[] = ['## Activity Stream'];

    // Current focus
    if (snap.activeApp) {
      lines.push(`- Currently ${snap.inferredTask || 'using'} **${snap.activeApp}**`);
      if (snap.windowTitle) {
        lines.push(`  Window: "${snap.windowTitle.slice(0, 80)}"`);
      }
      if (snap.focusStreak > 60) {
        lines.push(`  Focus: ${Math.round(snap.focusStreak / 60)} min streak`);
      }
    }

    // Mood
    if (snap.currentMood && snap.currentMood !== 'neutral') {
      lines.push(`- Mood: ${snap.currentMood} (${Math.round(snap.moodConfidence * 100)}% confidence)`);
    }

    // Clipboard
    if (snap.lastClipboardType && snap.lastClipboardType !== 'empty') {
      lines.push(`- Clipboard: ${snap.lastClipboardType} — "${snap.lastClipboardPreview.slice(0, 60)}"`);
    }

    // Recent tool activity
    if (snap.recentToolCalls.length > 0) {
      lines.push(`- Recent tools: ${snap.recentToolCalls.join(', ')}`);
    }

    // Recent notifications
    if (snap.recentNotifications.length > 0) {
      lines.push(`- Notifications: ${snap.recentNotifications.join(' | ')}`);
    }

    // Recent notable events (last 5 non-ambient, non-sentiment)
    const notable = this.buffer
      .filter(e => e.type !== 'ambient' && e.type !== 'sentiment' && e.type !== 'system')
      .slice(-5)
      .reverse();

    if (notable.length > 0) {
      lines.push('- Recent activity:');
      for (const e of notable) {
        const ago = Math.round((Date.now() - e.timestamp) / 1000);
        const agoStr = ago < 60 ? `${ago}s ago` : `${Math.round(ago / 60)}m ago`;
        lines.push(`  • [${agoStr}] ${e.summary}`);
      }
    }

    return lines.join('\n');
  }

  // ── Prompt Context (shorter, budget-aware) ───────────────────────

  /**
   * Shorter context for high-priority prompt section.
   * Designed to fit within 'medium' priority budget (~2000 chars).
   */
  getPromptContext(): string {
    if (!this.config.enabled || this.buffer.length === 0) return '';

    const snap = this.snapshot;
    const parts: string[] = [];

    if (snap.activeApp) {
      parts.push(`${snap.inferredTask || 'using'} ${snap.activeApp}`);
    }
    if (snap.currentMood && snap.currentMood !== 'neutral') {
      parts.push(`mood: ${snap.currentMood}`);
    }
    if (snap.recentToolCalls.length > 0) {
      parts.push(`recent tools: ${snap.recentToolCalls.slice(0, 3).join(', ')}`);
    }

    return parts.length > 0 ? `[CONTEXT] ${parts.join(' | ')}` : '';
  }

  // ── Listener Registration ────────────────────────────────────────

  /**
   * Register a listener for new events. Returns unsubscribe function.
   */
  on(listener: (event: ContextEvent) => void): () => void {
    this.listeners.push(listener);
    return () => {
      this.listeners = this.listeners.filter(l => l !== listener);
    };
  }

  // ── Maintenance ──────────────────────────────────────────────────

  /**
   * Prune expired events from the buffer.
   * Called automatically on push, but can be called manually.
   */
  prune(): number {
    const now = Date.now();
    const before = this.buffer.length;

    this.buffer = this.buffer.filter(e => {
      const ttl = e.ttlMs ?? this.config.maxBufferAgeMs;
      return (now - e.timestamp) <= ttl;
    });

    // Rebuild dedup index
    this.rebuildDedupeIndex();

    return before - this.buffer.length;
  }

  /**
   * Clear the entire buffer and reset snapshot.
   */
  clear(): void {
    this.buffer = [];
    this.dedupeIndex.clear();
    this.lastEmitTime.clear();
    this.snapshot = this.emptySnapshot();
  }

  // ── Status ───────────────────────────────────────────────────────

  getStatus(): ContextStreamStatus {
    const now = Date.now();
    const eventCounts: Record<string, number> = {};
    let recentCount = 0;

    for (const e of this.buffer) {
      eventCounts[e.type] = (eventCounts[e.type] || 0) + 1;
      if (now - e.timestamp <= 60_000) recentCount++;
    }

    const oldest = this.buffer.length > 0 ? this.buffer[0] : null;

    // Rough memory estimate: ~300 bytes per event average
    const memoryEstimateKb = Math.round((this.buffer.length * 300) / 1024);

    return {
      enabled: this.config.enabled,
      bufferSize: this.buffer.length,
      maxBufferSize: this.config.maxBufferSize,
      oldestEventAge: oldest ? (now - oldest.timestamp) : 0,
      eventCounts,
      eventsPerMinute: recentCount,
      memoryEstimateKb,
    };
  }

  // ── Configuration ────────────────────────────────────────────────

  getConfig(): ContextStreamConfig {
    return { ...this.config };
  }

  setEnabled(enabled: boolean): void {
    this.config.enabled = enabled;
  }

  getBufferSize(): number {
    return this.buffer.length;
  }

  // ── Private Helpers ──────────────────────────────────────────────

  private updateSnapshot(event: ContextEvent): void {
    this.snapshot.lastUpdated = event.timestamp;

    switch (event.type) {
      case 'ambient':
        if (event.data.activeApp) this.snapshot.activeApp = String(event.data.activeApp);
        if (event.data.windowTitle) this.snapshot.windowTitle = String(event.data.windowTitle);
        if (event.data.inferredTask) this.snapshot.inferredTask = String(event.data.inferredTask);
        if (typeof event.data.focusStreak === 'number') {
          this.snapshot.focusStreak = event.data.focusStreak;
        }
        break;

      case 'sentiment':
        if (event.data.mood) this.snapshot.currentMood = String(event.data.mood);
        if (typeof event.data.confidence === 'number') {
          this.snapshot.moodConfidence = event.data.confidence;
        }
        if (typeof event.data.energyLevel === 'number') {
          this.snapshot.energyLevel = event.data.energyLevel;
        }
        break;

      case 'clipboard':
        if (event.data.contentType) {
          this.snapshot.lastClipboardType = String(event.data.contentType);
        }
        if (event.data.preview) {
          this.snapshot.lastClipboardPreview = String(event.data.preview);
        }
        break;

      case 'tool-invoke':
        if (event.data.toolName) {
          this.snapshot.recentToolCalls.unshift(String(event.data.toolName));
          if (this.snapshot.recentToolCalls.length > SNAPSHOT_TOOL_HISTORY) {
            this.snapshot.recentToolCalls.pop();
          }
        }
        break;

      case 'notification':
        if (event.summary) {
          this.snapshot.recentNotifications.unshift(event.summary);
          if (this.snapshot.recentNotifications.length > SNAPSHOT_NOTIF_HISTORY) {
            this.snapshot.recentNotifications.pop();
          }
        }
        break;
    }
  }

  private sanitizeData(data: Record<string, unknown>): Record<string, unknown> {
    const result: Record<string, unknown> = {};
    let count = 0;

    for (const [key, value] of Object.entries(data)) {
      if (count >= MAX_DATA_KEYS) break;

      // Skip sensitive-looking keys
      if (/password|token|secret|key|auth|credential/i.test(key)) continue;

      // Skip large values (base64 frames, full message bodies)
      if (typeof value === 'string' && value.length > 2000) {
        result[key] = value.slice(0, 200) + '… [truncated]';
      } else {
        result[key] = value;
      }
      count++;
    }

    return result;
  }

  private evictOldest(count: number): void {
    this.buffer.splice(0, count);
    this.rebuildDedupeIndex();
  }

  private rebuildDedupeIndex(): void {
    this.dedupeIndex.clear();
    for (let i = 0; i < this.buffer.length; i++) {
      const e = this.buffer[i];
      if (e.dedupeKey) {
        this.dedupeIndex.set(e.dedupeKey, i);
      }
    }
  }

  private emptySnapshot(): ContextSnapshot {
    return {
      activeApp: '',
      windowTitle: '',
      inferredTask: '',
      focusStreak: 0,
      currentMood: 'neutral',
      moodConfidence: 0,
      energyLevel: 0.5,
      lastClipboardType: '',
      lastClipboardPreview: '',
      recentToolCalls: [],
      recentNotifications: [],
      activeWorkStream: '',
      lastUpdated: 0,
    };
  }

  private notifyListeners(event: ContextEvent): void {
    for (const listener of this.listeners) {
      try {
        listener(event);
      } catch {
        // Don't let listener errors break the stream
      }
    }
  }
}

// ── Singleton ────────────────────────────────────────────────────────

export const contextStream = new ContextStream();
