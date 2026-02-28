/**
 * Context Stream Bridge — connects existing context sources (ambient,
 * clipboard, sentiment, notifications) to the unified context stream.
 *
 * Track III, Phase 1: Activity Ingestion — Source Integration.
 *
 * This bridge observes changes in each engine and pushes normalized
 * ContextEvents into the contextStream singleton. It does NOT modify
 * the source engines; instead it polls or wraps their outputs.
 *
 * cLaw Gate: No new data is generated — only existing data is
 * reformatted and routed. Sensitive keys stripped at stream level.
 */
import { contextStream } from './context-stream';
import { ambientEngine } from './ambient';
import { clipboardIntelligence } from './clipboard-intelligence';
import { sentimentEngine } from './sentiment';
import { notificationEngine } from './notifications';

let ambientInterval: ReturnType<typeof setInterval> | null = null;
let clipboardInterval: ReturnType<typeof setInterval> | null = null;
let notificationInterval: ReturnType<typeof setInterval> | null = null;

let lastAmbientApp = '';
let lastAmbientTitle = '';
let lastClipboardPreview = '';
let lastNotificationCount = 0;

/**
 * Start bridging all context sources into the unified stream.
 * Should be called after all engines have been initialized.
 */
export function startContextStreamBridge(): void {
  // Push a system event to mark bridge startup
  contextStream.push({
    type: 'system',
    source: 'context-stream-bridge',
    summary: 'Context stream bridge started',
    data: { event: 'bridge-start' },
  });

  // ── Ambient Engine Bridge ──────────────────────────────────────────
  // Poll ambient state every 10s (matches ambient engine's own poll rate)
  // Only push when something actually changed
  ambientInterval = setInterval(() => {
    try {
      const state = ambientEngine.getState();
      if (!state) return;

      const appChanged = state.activeApp !== lastAmbientApp;
      const titleChanged = state.windowTitle !== lastAmbientTitle;

      if (appChanged || titleChanged) {
        lastAmbientApp = state.activeApp || '';
        lastAmbientTitle = state.windowTitle || '';

        contextStream.push({
          type: 'ambient',
          source: 'ambient-engine',
          summary: state.inferredTask
            ? `${state.inferredTask} in ${state.activeApp}`
            : `Using ${state.activeApp || 'unknown'}`,
          data: {
            activeApp: state.activeApp,
            windowTitle: state.windowTitle,
            inferredTask: state.inferredTask,
            focusStreak: state.focusStreak ?? 0,
          },
          dedupeKey: 'ambient-focus',
        });
      }
    } catch {
      // Ambient engine may not be initialized yet
    }
  }, 10_000);

  // ── Clipboard Intelligence Bridge ──────────────────────────────────
  // Poll clipboard state every 3s (slightly longer than clipboard's 2s poll)
  // Only push when clipboard content actually changed
  clipboardInterval = setInterval(() => {
    try {
      const current = clipboardIntelligence.getCurrent();
      if (!current || current.type === 'empty') return;

      if (current.preview !== lastClipboardPreview) {
        lastClipboardPreview = current.preview || '';

        contextStream.push({
          type: 'clipboard',
          source: 'clipboard-intelligence',
          summary: `Clipboard: ${current.type} — "${(current.preview || '').slice(0, 60)}"`,
          data: {
            contentType: current.type,
            preview: (current.preview || '').slice(0, 200),
          },
          dedupeKey: 'clipboard-content',
        });
      }
    } catch {
      // Clipboard engine may not be initialized yet
    }
  }, 3_000);

  // ── Notification Engine Bridge ─────────────────────────────────────
  // Poll notifications every 15s (matches notification engine's poll rate)
  // Push any new notifications since last check
  notificationInterval = setInterval(() => {
    try {
      const recent = notificationEngine.getRecent();
      if (!recent || recent.length === 0) return;

      // Only push if we have new notifications
      if (recent.length > lastNotificationCount) {
        const newNotifs = recent.slice(0, recent.length - lastNotificationCount);
        lastNotificationCount = recent.length;

        for (const notif of newNotifs) {
          contextStream.push({
            type: 'notification',
            source: 'notification-engine',
            summary: `${notif.app}: ${notif.title}`,
            data: {
              app: notif.app,
              title: notif.title,
              body: (notif.body || '').slice(0, 200),
            },
          });
        }
      }
    } catch {
      // Notification engine may not be initialized yet
    }
  }, 15_000);
}

/**
 * Push a sentiment update into the context stream.
 * Call this after sentimentEngine.analyse() completes.
 */
export function bridgeSentimentUpdate(): void {
  try {
    const state = sentimentEngine.getState();
    if (!state || !state.currentMood) return;

    contextStream.push({
      type: 'sentiment',
      source: 'sentiment-engine',
      summary: `Mood: ${state.currentMood} (${Math.round((state.confidence ?? 0) * 100)}% confidence)`,
      data: {
        mood: state.currentMood,
        confidence: state.confidence,
        energyLevel: state.energyLevel,
        moodStreak: state.moodStreak,
      },
      dedupeKey: 'sentiment-mood',
    });
  } catch {
    // Sentiment engine may not be initialized yet
  }
}

/**
 * Push a tool invocation event into the context stream.
 * Call this when the agent invokes a tool.
 */
export function bridgeToolInvocation(
  toolName: string,
  success: boolean,
  durationMs?: number,
): void {
  contextStream.push({
    type: 'tool-invoke',
    source: 'tool-router',
    summary: `Tool: ${toolName} (${success ? 'ok' : 'failed'}${durationMs ? `, ${durationMs}ms` : ''})`,
    data: {
      toolName,
      success,
      durationMs: durationMs ?? 0,
    },
  });
}

/**
 * Push a user input event into the context stream.
 * Call this when the user sends a message (topic only, not content).
 */
export function bridgeUserInput(topic: string): void {
  contextStream.push({
    type: 'user-input',
    source: 'chat-handler',
    summary: `User: ${topic.slice(0, 100)}`,
    data: { topic: topic.slice(0, 200) },
    dedupeKey: 'user-input-latest',
  });
}

/**
 * Stop all bridge polling intervals.
 */
export function stopContextStreamBridge(): void {
  if (ambientInterval) {
    clearInterval(ambientInterval);
    ambientInterval = null;
  }
  if (clipboardInterval) {
    clearInterval(clipboardInterval);
    clipboardInterval = null;
  }
  if (notificationInterval) {
    clearInterval(notificationInterval);
    notificationInterval = null;
  }

  lastAmbientApp = '';
  lastAmbientTitle = '';
  lastClipboardPreview = '';
  lastNotificationCount = 0;
}
