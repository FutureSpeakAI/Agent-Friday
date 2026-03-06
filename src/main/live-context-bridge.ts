/**
 * Track C, Phase 3: "The Tapestry" — Live Context Feed
 *
 * Subscribes to context stream and briefing updates, runs the
 * ContextInjector, and pushes enriched per-app context to the
 * renderer via IPC. Includes debouncing and a circuit breaker
 * for the execution feedback loop.
 *
 * The hermeneutic circle closes: OS events → context graph →
 * briefings → injector → apps → tool execution → back to graph.
 */

import { contextStream } from './context-stream';
import { contextGraph } from './context-graph';
import { briefingDelivery } from './briefing-delivery';
import { ContextInjector, type AppContext } from './context-injector';

// ── Constants ──────────────────────────────────────────────────────

const DEBOUNCE_MS = 2000;
const FEEDBACK_COOLDOWN_MS = 5000;

// ── LiveContextBridge ──────────────────────────────────────────────

export class LiveContextBridge {
  private running = false;
  private mainWindow: any = null;
  private unsubStream: (() => void) | null = null;
  private injector = new ContextInjector();
  private debounceTimer: ReturnType<typeof setTimeout> | null = null;
  private lastFeedbackAt = 0;

  /**
   * Start the live context feed.
   * Subscribes to context stream events and pushes enriched
   * context to the renderer on each update (debounced).
   */
  start(mainWindow: any): void {
    if (this.running) return;
    this.running = true;
    this.mainWindow = mainWindow;

    this.unsubStream = contextStream.on(() => {
      this.scheduleUpdate();
    });
  }

  /**
   * Stop the live context feed and clean up all subscriptions.
   */
  stop(): void {
    if (!this.running) return;
    this.running = false;

    if (this.unsubStream) {
      this.unsubStream();
      this.unsubStream = null;
    }

    if (this.debounceTimer) {
      clearTimeout(this.debounceTimer);
      this.debounceTimer = null;
    }

    this.mainWindow = null;
  }

  /**
   * Get current enriched context for a specific app.
   * Used by the app-context:get IPC handler.
   */
  getContextForApp(appId: string): AppContext {
    return this.injector.getContextForApp(appId);
  }

  /**
   * Feed an execution result back into the context graph.
   * Includes a cooldown circuit breaker to prevent runaway loops.
   */
  feedExecutionResult(result: {
    tool_use_id: string;
    content: string | any[];
    is_error?: boolean;
  }): void {
    const now = Date.now();
    if (now - this.lastFeedbackAt < FEEDBACK_COOLDOWN_MS) return;
    this.lastFeedbackAt = now;

    contextStream.push({
      type: 'tool-invoke',
      source: 'execution-delegate',
      summary: typeof result.content === 'string'
        ? result.content.slice(0, 100)
        : 'Tool execution completed',
      data: {
        toolUseId: result.tool_use_id,
        isError: result.is_error ?? false,
      },
    });
  }

  // ── Private ────────────────────────────────────────────────────

  private scheduleUpdate(): void {
    // Update injector eagerly (synchronous) so getContextForApp is always current
    this.refreshInjector();

    // Debounce the IPC push to renderer
    if (this.debounceTimer) {
      clearTimeout(this.debounceTimer);
    }

    this.debounceTimer = setTimeout(() => {
      this.debounceTimer = null;
      this.pushToRenderer();
    }, DEBOUNCE_MS);
  }

  private refreshInjector(): void {
    const raw = contextGraph.getActiveStream();
    const entities = contextGraph.getTopEntities(20);
    const briefings = briefingDelivery.getRecentBriefings(10);

    // Serialize WorkStream → SerializedStream at the bridge boundary
    const activeStream = raw
      ? {
          id: raw.id,
          name: raw.name,
          task: raw.task,
          app: raw.app,
          startedAt: raw.startedAt,
          lastActiveAt: raw.lastActiveAt,
          eventCount: raw.eventCount,
          entities: raw.entities,
          eventTypes: [...raw.eventTypes],
          summary: raw.summary,
        }
      : null;

    this.injector.ingest(
      { activeStream, entities },
      briefings.map((b) => ({
        id: b.id,
        topic: b.topic,
        content: b.content,
        priority: b.priority,
        timestamp: b.timestamp,
      })),
    );
  }

  private pushToRenderer(): void {
    if (!this.running || !this.mainWindow) return;
    if (this.mainWindow.webContents.isDestroyed()) return;

    const ctx = this.injector.getContextForApp('dashboard');
    this.mainWindow.webContents.send('app-context:update', ctx);
  }
}

export const liveContextBridge = new LiveContextBridge();
