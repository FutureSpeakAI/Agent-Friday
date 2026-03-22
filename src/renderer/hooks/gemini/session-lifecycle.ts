/**
 * Session lifecycle effects for Gemini Live.
 *
 * Contains the logic for:
 * - System sleep/resume detection
 * - Tab focus recovery (AudioContext resume)
 * - Mic + AudioContext health monitoring
 * - Periodic memory extraction
 * - Proactive agent result surfacing
 * - Idle behavior start/stop
 * - Ambient context polling
 */

import type { GeminiRefs, GeminiLiveState } from './types';

// ── Sleep/resume detection ──

/**
 * Set up a heartbeat timer that detects system sleep/wake by measuring
 * timer gaps. When a gap > 15s is detected, trigger reconnection.
 * Returns a cleanup function.
 */
export function setupSleepResumeDetection(refs: GeminiRefs): () => void {
  let lastHeartbeat = Date.now();
  const HEARTBEAT_INTERVAL = 5000;
  const SLEEP_THRESHOLD = 15000;

  const heartbeat = setInterval(() => {
    const now = Date.now();
    const gap = now - lastHeartbeat;
    lastHeartbeat = now;

    if (gap > SLEEP_THRESHOLD) {
      console.warn(`[GeminiLive] Detected system wake-up (${Math.round(gap / 1000)}s gap) — checking connection health`);

      const ws = refs.wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        console.warn('[GeminiLive] WebSocket dead after wake — triggering reconnect');
        const sm = refs.sessionManagerRef.current;
        if (sm && !refs.smReconnectingRef.current && !refs.isAutoReconnectingRef.current) {
          refs.intentionalDisconnectRef.current = true;
          sm.requestReconnect();
        }
      } else {
        // WS reports open but might be stale — send keepalive to test
        try {
          const silentPcm = new ArrayBuffer(320);
          const silentB64 = btoa(String.fromCharCode(...new Uint8Array(silentPcm)));
          ws.send(JSON.stringify({
            realtime_input: {
              media_chunks: [{ data: silentB64, mime_type: 'audio/pcm;rate=16000' }],
            },
          }));
        } catch {
          console.warn('[GeminiLive] Post-wake keepalive failed — triggering reconnect');
          const sm = refs.sessionManagerRef.current;
          if (sm && !refs.smReconnectingRef.current) {
            refs.intentionalDisconnectRef.current = true;
            sm.requestReconnect();
          }
        }
      }
    }
  }, HEARTBEAT_INTERVAL);

  return () => clearInterval(heartbeat);
}

// ── Tab focus recovery ──

/**
 * Resume AudioContexts when the browser tab regains focus.
 * Returns a cleanup function.
 */
export function setupTabFocusRecovery(refs: GeminiRefs): () => void {
  const onVisibilityChange = async () => {
    if (document.visibilityState === 'visible') {
      const mic = refs.audioContextRef.current;
      if (mic && mic.state === 'suspended') {
        console.log('[GeminiLive] Window regained focus — resuming mic AudioContext');
        try { await mic.resume(); } catch { /* ignored */ }
      }
      const playback = refs.playbackEngineRef.current;
      if (playback) {
        try { await playback.resumeIfSuspended(); } catch { /* ignored */ }
      }
    }
  };

  document.addEventListener('visibilitychange', onVisibilityChange);
  return () => document.removeEventListener('visibilitychange', onVisibilityChange);
}

// ── Mic + AudioContext health monitor ──

/**
 * Periodic health check for mic tracks and AudioContexts.
 * Detects dead tracks, suspended contexts, and triggers restarts.
 * Returns a cleanup function.
 */
export function setupMicHealthMonitor(
  refs: GeminiRefs,
  startListening: () => Promise<void>
): () => void {
  const HEALTH_CHECK_INTERVAL = 10_000;

  const healthCheck = async () => {
    // 1. Check if mic stream tracks are still alive
    const stream = refs.streamRef.current;
    if (stream) {
      const tracks = stream.getAudioTracks();
      const hasLiveTrack = tracks.some((t) => t.readyState === 'live' && t.enabled);
      if (!hasLiveTrack && tracks.length > 0) {
        console.warn('[GeminiLive] Mic track died (sleep/unplug?) — restarting mic pipeline');
        try {
          await startListening();
        } catch (err) {
          console.error('[GeminiLive] Mic restart failed:', err);
        }
        return;
      }
    }

    // 2. Check if mic AudioContext got suspended (browser tab background, screen lock)
    const ctx = refs.audioContextRef.current;
    if (ctx && ctx.state === 'suspended') {
      console.warn('[GeminiLive] Mic AudioContext suspended — resuming');
      try {
        await ctx.resume();
        console.log('[GeminiLive] Mic AudioContext resumed successfully');
      } catch (err) {
        console.warn('[GeminiLive] Mic AudioContext resume failed — restarting pipeline:', err);
        await startListening();
      }
    }

    // 3. Check if AudioContext was closed unexpectedly
    if (ctx && ctx.state === 'closed') {
      console.warn('[GeminiLive] Mic AudioContext closed unexpectedly — restarting pipeline');
      await startListening();
    }

    // 4. Ensure playback AudioContext is also alive
    const playback = refs.playbackEngineRef.current;
    if (playback) {
      try {
        await playback.resumeIfSuspended();
      } catch {
        // Non-critical — playback will auto-resume when next chunk arrives
      }
    }
  };

  const interval = setInterval(healthCheck, HEALTH_CHECK_INTERVAL);
  healthCheck(); // Run once immediately
  return () => clearInterval(interval);
}

// ── Periodic memory extraction ──

/**
 * Every 5 minutes during connected sessions, extract facts from conversation
 * history and store them in long-term memory.
 * Returns a cleanup function.
 */
export function setupPeriodicMemoryExtraction(refs: GeminiRefs): () => void {
  const EXTRACT_INTERVAL = 5 * 60 * 1000;
  const timer = setInterval(() => {
    const sm = refs.sessionManagerRef.current;
    if (!sm) return;

    const history = sm.getConversationHistory();
    if (history.length >= 4) {
      console.log('[GeminiLive] Running periodic memory extraction...');
      window.eve.memory.extract(history).catch((err: unknown) => {
        console.warn('[GeminiLive] Memory extraction failed:', err);
      });
    }
  }, EXTRACT_INTERVAL);

  return () => clearInterval(timer);
}

// ── Proactive agent result surfacing ──

/**
 * Poll for completed background agents and inject their results
 * into the Gemini conversation.
 * Returns a cleanup function.
 */
export function setupAgentResultSurfacing(
  refs: GeminiRefs,
  sendTextToGemini: (text: string) => void
): () => void {
  const surfacedAgentsRef = new Set<string>();
  const AGENT_POLL_INTERVAL = 15_000;

  const poll = async () => {
    try {
      const tasks = await window.eve.agents.list('completed');
      if (!Array.isArray(tasks)) return;

      for (const task of tasks) {
        if (surfacedAgentsRef.has(task.id)) continue;
        surfacedAgentsRef.add(task.id);

        // Only surface recent results (completed within last 2 minutes)
        const completedAt = task.completedAt || 0;
        if (Date.now() - completedAt > 120_000) continue;

        // Don't interrupt if Friday is currently speaking
        if (refs.stateRef.current.isSpeaking) {
          surfacedAgentsRef.delete(task.id);
          continue;
        }

        const resultPreview = task.result
          ? String(task.result).slice(0, 1500)
          : 'No result returned.';

        const injection = `[SYSTEM: Background agent "${task.description}" (type: ${task.agentType}) has completed. Here is the result — share it with the user naturally when appropriate, don't interrupt if they're mid-thought:\n\n${resultPreview}${String(task.result || '').length > 1500 ? '\n\n(Result truncated — full result available via check_agent tool)' : ''}]`;

        console.log(`[GeminiLive] Surfacing agent result: ${task.id.slice(0, 8)} — ${task.description}`);
        sendTextToGemini(injection);
      }
    } catch (err) {
      // Non-critical — silent fail
    }
  };

  const interval = setInterval(poll, AGENT_POLL_INTERVAL);
  poll(); // Run once immediately
  return () => clearInterval(interval);
}

// ── Ambient context polling ──

/**
 * Poll the main process for ambient context (active app, window title, etc.)
 * and cache it for synchronous access by idle behavior.
 * Returns a cleanup function.
 */
export function setupAmbientContextPolling(refs: GeminiRefs): () => void {
  const poll = async () => {
    try {
      refs.ambientContextCacheRef.current = await window.eve.ambient.getContextString();
    } catch {
      // Non-critical — keep last cached value
    }
  };
  poll();
  const interval = setInterval(poll, 15_000);
  return () => clearInterval(interval);
}
