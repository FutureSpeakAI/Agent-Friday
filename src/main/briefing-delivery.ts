/**
 * Track A, Phase 3: "The Performance" — Briefing Delivery to Dashboard
 *
 * The final link in the proactive intelligence chain. Wires together:
 *   BriefingPipeline (triggers) → BriefingScoringEngine (priority) →
 *   IntelligenceEngine (research) → IPC push (renderer)
 *
 * Priority-based delivery:
 *   - urgent/relevant: pushed immediately via webContents.send()
 *   - informational: batched (max 1 push per 10 minutes)
 *
 * Hermeneutic note: This module is the synthesis — the conductor's baton
 * has been passed through trigger → score, and now the audience hears
 * the music. The whole is understood through all parts working together.
 */

import type { BrowserWindow } from 'electron';
import { briefingPipeline, type BriefingTrigger } from './briefing-pipeline';
import { scoreTrigger, type ScoringInput } from './briefing-scoring';
import { intelligenceEngine } from './intelligence';

// ── Types ─────────────────────────────────────────────────────────────

export interface DeliveredBriefing {
  id: string;
  topic: string;
  content: string;
  priority: 'urgent' | 'relevant' | 'informational';
  timestamp: number;
  dismissed: boolean;
}

// ── Constants ─────────────────────────────────────────────────────────

/** Minimum interval between informational briefing pushes */
const BATCH_INTERVAL_MS = 10 * 60 * 1000; // 10 minutes

/** Maximum retained briefings */
const MAX_BRIEFINGS = 50;

// ── Priority mapping ─────────────────────────────────────────────────

const PRIORITY_MAP = {
  urgent: 'high',
  relevant: 'medium',
  informational: 'low',
} as const;

const PRIORITY_ORDER = { urgent: 0, relevant: 1, informational: 2 } as const;

// ── BriefingDelivery Class ───────────────────────────────────────────

export class BriefingDelivery {
  private running = false;
  private unsubPipeline?: () => void;
  private briefings: DeliveredBriefing[] = [];
  private batchQueue: DeliveredBriefing[] = [];
  private batchTimer?: ReturnType<typeof setTimeout>;
  private lastBatchPush = 0;
  private mainWindow?: BrowserWindow;

  /**
   * Start the delivery chain: pipeline triggers → score → research → push.
   */
  start(mainWindow: BrowserWindow): void {
    if (this.running) return;
    this.running = true;
    this.mainWindow = mainWindow;

    this.unsubPipeline = briefingPipeline.onTrigger((trigger) => {
      void this.handleTrigger(trigger);
    });
  }

  /**
   * Stop the delivery chain and clean up all timers.
   */
  stop(): void {
    if (!this.running) return;
    this.running = false;
    this.unsubPipeline?.();
    this.unsubPipeline = undefined;
    if (this.batchTimer) {
      clearTimeout(this.batchTimer);
      this.batchTimer = undefined;
    }
    this.mainWindow = undefined;
  }

  /**
   * Get recent briefings sorted by priority (urgent first) then recency.
   */
  getRecentBriefings(limit = 20): DeliveredBriefing[] {
    return [...this.briefings]
      .sort((a, b) => {
        const pDiff = PRIORITY_ORDER[a.priority] - PRIORITY_ORDER[b.priority];
        if (pDiff !== 0) return pDiff;
        return b.timestamp - a.timestamp;
      })
      .slice(0, limit);
  }

  /**
   * Mark a briefing as dismissed. Returns true if found and updated.
   */
  dismissBriefing(id: string): boolean {
    const b = this.briefings.find(x => x.id === id);
    if (!b || b.dismissed) return false;
    b.dismissed = true;
    return true;
  }

  // ── Private ─────────────────────────────────────────────────────────

  private async handleTrigger(trigger: BriefingTrigger): Promise<void> {
    // 1. Score the trigger
    const input: ScoringInput = {
      trigger,
      history: [],
      currentTimeMs: Date.now(),
      isFirstSessionOfDay: false,
    };
    const result = scoreTrigger(input);

    // 2. Build enriched topic
    const topic = this.buildTopic(trigger);
    const researchPriority = PRIORITY_MAP[result.priority];

    // 3. Run research via intelligence engine
    try {
      await intelligenceEngine.runResearch(topic, researchPriority);
      const newBriefings = await intelligenceEngine.getUndeliveredBriefings();

      for (const b of newBriefings) {
        const delivered: DeliveredBriefing = {
          id: b.id,
          topic: b.topic,
          content: b.content,
          priority: result.priority,
          timestamp: b.createdAt,
          dismissed: false,
        };

        this.briefings.push(delivered);
        if (this.briefings.length > MAX_BRIEFINGS) {
          this.briefings = this.briefings.slice(-MAX_BRIEFINGS);
        }

        // 4. Deliver based on priority
        if (result.priority === 'urgent' || result.priority === 'relevant') {
          this.pushToRenderer(delivered);
        } else {
          this.enqueueBatch(delivered);
        }
      }
    } catch {
      // Research failed — silently skip, don't crash the pipeline
    }
  }

  private buildTopic(trigger: BriefingTrigger): string {
    const entityNames = trigger.entities.map(e => e.value).join(', ');
    return entityNames
      ? `${trigger.streamName}: ${entityNames}`
      : trigger.streamName;
  }

  private pushToRenderer(briefing: DeliveredBriefing): void {
    if (!this.mainWindow?.webContents) return;
    this.mainWindow.webContents.send('briefing:new', {
      id: briefing.id,
      topic: briefing.topic,
      content: briefing.content,
      priority: briefing.priority,
      timestamp: briefing.timestamp,
    });
  }

  private enqueueBatch(briefing: DeliveredBriefing): void {
    const now = Date.now();
    const elapsed = now - this.lastBatchPush;

    if (elapsed >= BATCH_INTERVAL_MS) {
      // Enough time has passed — push immediately
      this.pushToRenderer(briefing);
      this.lastBatchPush = now;
    } else {
      // Too soon — queue for later
      this.batchQueue.push(briefing);
      if (!this.batchTimer) {
        const remaining = BATCH_INTERVAL_MS - elapsed;
        this.batchTimer = setTimeout(() => {
          this.flushBatch();
          this.batchTimer = undefined;
        }, remaining);
      }
    }
  }

  private flushBatch(): void {
    for (const b of this.batchQueue) {
      this.pushToRenderer(b);
    }
    this.batchQueue = [];
    this.lastBatchPush = Date.now();
  }
}

// ── Singleton ─────────────────────────────────────────────────────────

export const briefingDelivery = new BriefingDelivery();
