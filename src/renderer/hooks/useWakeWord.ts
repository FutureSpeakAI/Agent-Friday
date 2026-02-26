/**
 * useWakeWord.ts — Wake Word Detection Hook.
 * Uses Web Speech API for continuous "Hey Friday" / "Friday" detection.
 * Active only when Gemini is NOT connected (idle mode).
 * On detection → triggers the provided callback (e.g., auto-connect).
 */

import { useEffect, useRef, useCallback } from 'react';

interface UseWakeWordOptions {
  /** Whether wake word detection is enabled */
  enabled: boolean;
  /** Whether Gemini is currently connected (detection pauses when true) */
  isConnected: boolean;
  /** Callback when wake word is detected */
  onWake: () => void;
}

const WAKE_PHRASES = [
  'hey friday',
  'hey fry',    // common misrecognition
  'hey fry day',
  'ok friday',
  'friday',
];

// Minimum confidence to trigger (Web Speech API sometimes returns low-confidence noise)
const MIN_CONFIDENCE = 0.5;
// Cooldown after triggering to prevent rapid re-fires (ms)
const TRIGGER_COOLDOWN = 5000;

export function useWakeWord({ enabled, isConnected, onWake }: UseWakeWordOptions): void {
  const recognitionRef = useRef<SpeechRecognition | null>(null);
  const lastTriggerRef = useRef(0);
  const restartTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onWakeRef = useRef(onWake);

  // Keep callback ref fresh
  onWakeRef.current = onWake;

  const startRecognition = useCallback(() => {
    // Clean up existing
    if (recognitionRef.current) {
      try {
        recognitionRef.current.abort();
      } catch { /* ignore */ }
      recognitionRef.current = null;
    }

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      console.warn('[WakeWord] Web Speech API not supported');
      return;
    }

    const recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = 'en-GB';
    recognition.maxAlternatives = 3;

    recognition.onresult = (event: SpeechRecognitionEvent) => {
      const now = Date.now();
      if (now - lastTriggerRef.current < TRIGGER_COOLDOWN) return;

      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        for (let j = 0; j < result.length; j++) {
          const alt = result[j];
          const transcript = alt.transcript.toLowerCase().trim();
          const confidence = alt.confidence;

          // Check if any wake phrase matches
          const isWake = WAKE_PHRASES.some((phrase) => {
            // Exact match or ends with the phrase (handles "hey eve" after other words)
            return transcript === phrase || transcript.endsWith(phrase);
          });

          if (isWake && (confidence >= MIN_CONFIDENCE || confidence === 0)) {
            // confidence === 0 happens with interim results where confidence isn't computed
            lastTriggerRef.current = now;
            console.log(`[WakeWord] Detected: "${transcript}" (confidence: ${confidence})`);

            // Stop listening before triggering (connection will take over mic)
            try {
              recognition.abort();
            } catch { /* ignore */ }

            onWakeRef.current();
            return;
          }
        }
      }
    };

    recognition.onerror = (event: SpeechRecognitionErrorEvent) => {
      // 'no-speech' and 'aborted' are expected — just restart
      if (event.error === 'no-speech' || event.error === 'aborted') return;
      console.warn(`[WakeWord] Error: ${event.error}`);
    };

    recognition.onend = () => {
      // Auto-restart if still enabled and not connected
      // Small delay to prevent tight restart loops
      restartTimerRef.current = setTimeout(() => {
        if (enabled && !isConnected && recognitionRef.current === recognition) {
          try {
            recognition.start();
          } catch {
            // May fail if recognition was aborted — create fresh instance
            startRecognition();
          }
        }
      }, 500);
    };

    recognitionRef.current = recognition;

    try {
      recognition.start();
      console.log('[WakeWord] Listening for wake word...');
    } catch (err) {
      console.warn('[WakeWord] Failed to start:', err);
    }
  }, [enabled, isConnected]);

  useEffect(() => {
    if (enabled && !isConnected) {
      startRecognition();
    } else {
      // Stop recognition
      if (recognitionRef.current) {
        try {
          recognitionRef.current.abort();
        } catch { /* ignore */ }
        recognitionRef.current = null;
      }
      if (restartTimerRef.current) {
        clearTimeout(restartTimerRef.current);
        restartTimerRef.current = null;
      }
    }

    return () => {
      if (recognitionRef.current) {
        try {
          recognitionRef.current.abort();
        } catch { /* ignore */ }
        recognitionRef.current = null;
      }
      if (restartTimerRef.current) {
        clearTimeout(restartTimerRef.current);
        restartTimerRef.current = null;
      }
    };
  }, [enabled, isConnected, startRecognition]);
}

// Augment Window for webkitSpeechRecognition
declare global {
  interface Window {
    SpeechRecognition: typeof SpeechRecognition;
    webkitSpeechRecognition: typeof SpeechRecognition;
  }
}
