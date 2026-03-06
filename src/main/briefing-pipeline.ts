/**
 * Track A, Phase 1: "The Score Reader" — Context-Aware Briefing Triggers
 *
 * The first link in the proactive intelligence chain. Subscribes to the
 * same context stream that feeds the ContextGraph, observes work stream
 * changes (the graph processes events first since it subscribes earlier),
 * and emits BriefingTriggers downstream.
 *
 * Architecture:
 *   ContextStream.on() → [ContextGraph processes event] → BriefingPipeline detects stream change → trigger
 *
 * Observer pattern follows the precedent set by context-stream-bridge.ts:
 * subscribe to an existing system's events, transform, emit downstream.
 *
 * Hermeneutic note: This module understands the *parts* — individual
 * stream transitions. Phase A.2 (scoring) will understand the *whole* —
 * which transitions matter.
 */

import { contextStream } from './context-stream';
import { contextGraph, type EntityRef } from './context-graph';

// ── Types ─────────────────────────────────────────────────────────────

export interface BriefingTrigger {
  /** Unique identifier for this trigger (bt-N) */
  id: string;
  /** The work stream ID that triggered this briefing */
  streamId: string;
  /** Human-readable name of the work stream */
  streamName: string;
  /** Inferred task type (coding, browsing, writing, etc.) */
  task: string;
  /** Top 3 entities from the stream for topic enrichment */
  entities: EntityRef[];
  /** When this trigger was created */
  triggeredAt: number;
}

// ── Constants ─────────────────────────────────────────────────────────

/** Suppress duplicate triggers for the same stream within this window */
const DEDUP_WINDOW_MS = 5 * 60 * 1000; // 5 minutes

/** Maximum number of triggers retained in memory */
const MAX_TRIGGERS = 50;

/** Maximum entities to include per trigger */
const MAX_ENTITIES_PER_TRIGGER = 3;

/** Default limit for getRecentTriggers() */
const DEFAULT_TRIGGER_LIMIT = 10;

// ── BriefingPipeline Class ────────────────────────────────────────────

export class BriefingPipeline {
  private unsubscribe: (() => void) | null = null;
  private lastStreamId: string | null = null;
  private recentTriggers: BriefingTrigger[] = [];
  private triggerCounter = 0;
  private triggerCallbacks: ((trigger: BriefingTrigger) => void)[] = [];

  /**
   * Register a callback to be notified when a briefing trigger fires.
   * Returns an unsubscribe function.
   */
  onTrigger(cb: (trigger: BriefingTrigger) => void): () => void {
    this.triggerCallbacks.push(cb);
    return () => {
      this.triggerCallbacks = this.triggerCallbacks.filter(c => c !== cb);
    };
  }

  /**
   * Start observing context graph work stream changes.
   * Must be called after contextGraph.start() so the graph's
   * listener is registered first (ensuring it processes events
   * before we check getActiveStream).
   */
  start(): void {
    if (this.unsubscribe) return; // Already listening

    // Capture initial active stream to avoid triggering on first event
    const active = contextGraph.getActiveStream();
    this.lastStreamId = active?.id ?? null;

    this.unsubscribe = contextStream.on(() => {
      this.checkForStreamChange();
    });
  }

  /**
   * Stop listening and clean up. Safe to call multiple times.
   */
  stop(): void {
    if (this.unsubscribe) {
      this.unsubscribe();
      this.unsubscribe = null;
    }
    this.lastStreamId = null;
  }

  /**
   * Get recent briefing triggers for debugging and monitoring.
   * Returns triggers in reverse chronological order (most recent first).
   */
  getRecentTriggers(limit: number = DEFAULT_TRIGGER_LIMIT): BriefingTrigger[] {
    return this.recentTriggers.slice(0, limit);
  }

  // ── Private ───────────────────────────────────────────────────────

  /**
   * Called on every context stream event. Compares the current active
   * stream against the last known stream to detect transitions.
   */
  private checkForStreamChange(): void {
    const active = contextGraph.getActiveStream();
    const currentId = active?.id ?? null;

    // No change — same stream or still null
    if (currentId === this.lastStreamId) return;

    // Stream went null (user idle, no focus) — track but don't trigger
    if (!active) {
      this.lastStreamId = null;
      return;
    }

    // Stream changed — check dedup window before firing
    if (this.isDuplicate(active.id)) {
      this.lastStreamId = currentId;
      return;
    }

    // Fire trigger
    const trigger: BriefingTrigger = {
      id: `bt-${++this.triggerCounter}`,
      streamId: active.id,
      streamName: active.name,
      task: active.task,
      entities: active.entities.slice(0, MAX_ENTITIES_PER_TRIGGER),
      triggeredAt: Date.now(),
    };

    this.recentTriggers.unshift(trigger);

    // Prune if over capacity
    if (this.recentTriggers.length > MAX_TRIGGERS) {
      this.recentTriggers = this.recentTriggers.slice(0, MAX_TRIGGERS);
    }

    // Notify downstream subscribers
    for (const cb of this.triggerCallbacks) {
      cb(trigger);
    }

    this.lastStreamId = currentId;
  }

  /**
   * Check if a trigger for the given stream ID was already fired
   * within the dedup window (5 minutes).
   */
  private isDuplicate(streamId: string): boolean {
    const now = Date.now();
    return this.recentTriggers.some(
      t => t.streamId === streamId && now - t.triggeredAt < DEDUP_WINDOW_MS,
    );
  }
}

// ── Singleton ─────────────────────────────────────────────────────────

export const briefingPipeline = new BriefingPipeline();
