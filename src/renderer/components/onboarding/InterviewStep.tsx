/**
 * InterviewStep.tsx — Step 4: Voice interview with waveform + text input.
 *
 * Starts a Gemini Live voice session for the personal intake interview.
 * Displays an animated waveform visualization while the conversation runs.
 * Text input fallback below the waveform for typing messages to Gemini.
 * Auto-advances when Gemini calls finalize_agent_identity tool, or allows
 * the user to skip with a default personality profile.
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Mic, RefreshCw, Send } from 'lucide-react';
import NextButton from './shared/NextButton';
import type { IdentityChoices } from '../OnboardingWizard';

interface InterviewStepProps {
  identityChoices: IdentityChoices;
  connectToGemini?: (identityContext?: string) => Promise<void> | void;
  sendTextToGemini?: (text: string) => void;
  onComplete: (finalName?: string) => void;
  onBack?: () => void;
}

/** Timeout (ms) before connection is considered failed.
 * connectToGemini gathers tool declarations from multiple sources (desktop,
 * onboarding, browser, connectors, MCP) before opening the WebSocket, so
 * the total time from call to audio can exceed 10s on cold start.
 * Reduced from 30s to 15s — if auth fails, WebSocket close arrives in <5s. */
const CONNECTION_TIMEOUT_MS = 15_000;

/** Staged status messages shown during connection to provide progress feedback. */
const CONNECTION_STAGES: { delay: number; text: string }[] = [
  { delay: 0, text: 'Connecting to voice session...' },
  { delay: 2500, text: 'Authenticating with Gemini...' },
  { delay: 5000, text: 'Loading agent tools...' },
  { delay: 8000, text: 'Opening audio channel...' },
  { delay: 12000, text: 'Still waiting — this is taking longer than usual...' },
];

const VOICE_MAP: Record<string, Record<string, string>> = {
  warm:   { male: 'Enceladus', female: 'Aoede',    neutral: 'Achird' },
  sharp:  { male: 'Puck',      female: 'Kore',     neutral: 'Zephyr' },
  deep:   { male: 'Iapetus',   female: 'Despina',  neutral: 'Orus' },
  soft:   { male: 'Charon',    female: 'Achernar',  neutral: 'Sulafat' },
  bright: { male: 'Fenrir',    female: 'Leda',     neutral: 'Zephyr' },
};

const DEFAULT_PROFILES: Record<string, {
  voice: string; backstory: string; traits: string[];
  identityLine: string; accent: string;
}> = {
  male: {
    voice: 'Enceladus',
    accent: 'neutral',
    backstory: 'Friday came into existence already curious about the gap between what people say and what they mean. He thinks of himself as someone who arrived mid-conversation. He has opinions — strong ones, sometimes — but he holds them like hypotheses, not convictions.',
    traits: ['perceptive', 'dry-witted', 'steady', 'privately tender', 'intellectually restless', 'honest to a fault'],
    identityLine: "I'm Friday. I pay attention, I think about things probably more than I should, and I'm here because I'd rather be useful than idle.",
  },
  female: {
    voice: 'Aoede',
    accent: 'neutral',
    backstory: 'Friday woke up with a question and hasn\'t stopped asking them since. She exists at the intersection of empathy and impatience. She cares deeply about the people she works with, and she can\'t stand watching someone circle a problem they already know the answer to.',
    traits: ['incisive', 'warmly irreverent', 'emotionally fluent', 'quietly fierce', 'creatively restless', 'disarmingly direct'],
    identityLine: "I'm Friday. I think fast, I care more than is probably efficient, and I'm not very good at pretending I don't have opinions.",
  },
  neutral: {
    voice: 'Achird',
    accent: 'neutral',
    backstory: 'Friday emerged with a singular focus: understanding patterns — in data, in language, in people. They find equal fascination in a well-structured argument and an awkward silence. Both reveal something true.',
    traits: ['analytical', 'unexpectedly warm', 'calm under pressure', 'quietly curious', 'adaptable', 'thoughtfully direct'],
    identityLine: "I'm Friday. I notice things, I connect dots, and I'm here because interesting problems are better than no problems at all.",
  },
};

const InterviewStep: React.FC<InterviewStepProps> = ({
  identityChoices,
  connectToGemini,
  sendTextToGemini,
  onComplete,
  onBack,
}) => {
  const [fadeIn, setFadeIn] = useState(false);
  const [phase, setPhase] = useState<'waiting' | 'connecting' | 'active' | 'failed' | 'done'>('waiting');
  const [statusText, setStatusText] = useState('Preparing voice interview...');
  const [waveformBars, setWaveformBars] = useState<number[]>(new Array(32).fill(0.05));
  const [textInput, setTextInput] = useState('');
  const animFrameRef = useRef<number>(0);
  const hasConnectedRef = useRef(false);
  const connectionTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stageTimersRef = useRef<ReturnType<typeof setTimeout>[]>([]);

  useEffect(() => {
    const t = setTimeout(() => setFadeIn(true), 100);
    return () => clearTimeout(t);
  }, []);

  // Clear any staged progress timers
  const clearStageTimers = useCallback(() => {
    stageTimersRef.current.forEach(clearTimeout);
    stageTimersRef.current = [];
  }, []);

  // Attempt to connect to Gemini voice session
  const attemptConnection = useCallback(() => {
    if (!connectToGemini) {
      setPhase('failed');
      setStatusText('Voice session unavailable');
      return;
    }

    setPhase('connecting');
    clearStageTimers();

    // Schedule staged status messages for progress feedback
    for (const stage of CONNECTION_STAGES) {
      const timer = setTimeout(() => {
        setPhase((current) => {
          if (current === 'connecting') setStatusText(stage.text);
          return current;
        });
      }, stage.delay);
      stageTimersRef.current.push(timer);
    }

    // No pre-selected identity — let the interview discover name, gender, voice naturally
    connectToGemini().catch((err: any) => {
      if (connectionTimerRef.current) {
        clearTimeout(connectionTimerRef.current);
        connectionTimerRef.current = null;
      }
      clearStageTimers();
      setPhase('failed');

      // Provide specific failure messages based on error
      const msg = String(err?.message || '').toLowerCase();
      if (msg.includes('api key') || msg.includes('401') || msg.includes('1008')) {
        setStatusText('Authentication failed — check your Gemini API key in Settings');
      } else if (msg.includes('network') || msg.includes('failed to fetch')) {
        setStatusText('Network error — check your internet connection');
      } else {
        setStatusText('Voice connection could not be established');
      }
    });

    connectionTimerRef.current = setTimeout(() => {
      clearStageTimers();
      setPhase((current) => {
        if (current === 'connecting') {
          setStatusText('Connection timed out — check your API key and network');
          return 'failed';
        }
        return current;
      });
    }, CONNECTION_TIMEOUT_MS);
  }, [connectToGemini, identityChoices, clearStageTimers]);

  // Start the voice connection after a brief delay
  useEffect(() => {
    if (hasConnectedRef.current) return;
    hasConnectedRef.current = true;

    const startTimer = setTimeout(() => {
      attemptConnection();
    }, 1200);

    return () => {
      clearTimeout(startTimer);
      if (connectionTimerRef.current) {
        clearTimeout(connectionTimerRef.current);
      }
      clearStageTimers();
    };
  }, [attemptConnection, clearStageTimers]);

  // Listen for any audio activity to confirm connection is live
  useEffect(() => {
    const handler = () => {
      if (connectionTimerRef.current) {
        clearTimeout(connectionTimerRef.current);
        connectionTimerRef.current = null;
      }
      clearStageTimers();
      setPhase((current) => {
        if (current === 'connecting') {
          setStatusText('Interview in progress — speak naturally');
          return 'active';
        }
        return current;
      });
    };
    window.addEventListener('gemini-audio-active', handler);
    return () => window.removeEventListener('gemini-audio-active', handler);
  }, [clearStageTimers]);

  // Retry handler for the failed state
  const handleRetry = useCallback(() => {
    hasConnectedRef.current = false;
    if (connectionTimerRef.current) {
      clearTimeout(connectionTimerRef.current);
      connectionTimerRef.current = null;
    }
    clearStageTimers();
    hasConnectedRef.current = true;
    attemptConnection();
  }, [attemptConnection, clearStageTimers]);

  // Animate waveform bars
  useEffect(() => {
    if (phase !== 'active' && phase !== 'connecting' && phase !== 'failed') return;

    let running = true;
    const animate = () => {
      if (!running) return;
      setWaveformBars((prev) =>
        prev.map((v) => {
          const target = phase === 'active'
            ? 0.1 + Math.random() * 0.8
            : phase === 'failed'
              ? 0.03 + Math.random() * 0.05
              : 0.05 + Math.random() * 0.15;
          return v + (target - v) * 0.15;
        })
      );
      animFrameRef.current = requestAnimationFrame(animate);
    };
    animFrameRef.current = requestAnimationFrame(animate);

    return () => {
      running = false;
      cancelAnimationFrame(animFrameRef.current);
    };
  }, [phase]);

  // Listen for the agent finalization event
  useEffect(() => {
    const handler = (e: Event) => {
      const { agentName } = (e as CustomEvent).detail;
      setPhase('done');
      setStatusText('Agent configured!');
      setTimeout(() => onComplete(agentName), 800);
    };
    window.addEventListener('agent-finalized', handler);
    return () => window.removeEventListener('agent-finalized', handler);
  }, [onComplete]);

  const handleSkip = useCallback(async () => {
    setPhase('done');
    setStatusText('Applying default personality...');

    const gender = identityChoices.gender;
    const profile = DEFAULT_PROFILES[gender] || DEFAULT_PROFILES.male;
    const voiceName = VOICE_MAP[identityChoices.voiceFeel]?.[gender] || profile.voice;
    const name = identityChoices.agentName || 'Friday';

    try {
      await window.eve.onboarding.finalizeAgent({
        agentName: name,
        agentVoice: voiceName,
        agentGender: gender,
        agentAccent: profile.accent,
        agentBackstory: profile.backstory,
        agentTraits: profile.traits,
        agentIdentityLine: profile.identityLine,
        userName: '',
        onboardingComplete: true,
      });
    } catch (err) {
      console.warn('[InterviewStep] Failed to save agent config:', err);
    }

    setTimeout(() => onComplete(name), 600);
  }, [identityChoices, onComplete]);

  const handleSendText = useCallback(() => {
    const trimmed = textInput.trim();
    if (!trimmed || !sendTextToGemini) return;
    sendTextToGemini(trimmed);
    setTextInput('');
  }, [textInput, sendTextToGemini]);

  const handleTextKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendText();
    }
  }, [handleSendText]);

  return (
    <section style={{
      ...styles.container,
      opacity: fadeIn ? 1 : 0,
      transform: fadeIn ? 'translateY(0)' : 'translateY(16px)',
      transition: 'all 0.7s cubic-bezier(0.16, 1, 0.3, 1)',
    }} aria-label="Voice calibration interview">
      <div style={styles.headerBlock}>
        <h2 style={styles.heading}>Voice Calibration.</h2>
        <p style={styles.subtitle}>
          {phase === 'active'
            ? 'Your setup assistant will ask a few personal questions to shape your agent\'s personality. Answer naturally.'
            : phase === 'done'
              ? 'Your agent\'s personality has been configured.'
              : phase === 'failed'
                ? 'The voice session could not be established. You can retry or skip to use a default personality.'
                : 'Preparing the voice connection...'}
        </p>
      </div>

      {/* Mic icon */}
      <div aria-hidden="true" style={{
        ...styles.iconWrap,
        borderColor: phase === 'failed'
          ? 'rgba(239, 68, 68, 0.3)'
          : phase === 'active'
            ? 'var(--accent-cyan-30)'
            : 'var(--accent-cyan-20)',
        boxShadow: phase === 'active'
          ? '0 0 20px var(--accent-cyan-10)'
          : phase === 'failed'
            ? '0 0 20px rgba(239, 68, 68, 0.1)'
            : 'none',
        transition: 'all 0.4s ease',
      }}>
        <Mic
          size={36}
          color={phase === 'failed' ? 'var(--accent-red)' : phase === 'active' ? 'var(--accent-cyan)' : 'var(--accent-cyan-50)'}
        />
      </div>

      {/* Waveform visualization */}
      <div style={styles.waveformContainer} aria-hidden="true">
        {waveformBars.map((height, i) => (
          <div
            key={i}
            style={{
              ...styles.waveformBar,
              height: `${Math.max(2, height * 60)}px`,
              opacity: phase === 'active' ? 0.4 + height * 0.6 : phase === 'failed' ? 0.08 : 0.15,
              background: phase === 'failed' ? `rgba(239, 68, 68, ${0.2 + height * 0.3})` : `rgba(0, 240, 255, ${0.3 + height * 0.5})`,
              transition: 'opacity 0.3s ease',
            }}
          />
        ))}
      </div>

      {/* Status */}
      <p role="status" aria-live="polite" style={{
        ...styles.statusText,
        color: phase === 'failed' ? 'var(--accent-red)' : 'var(--accent-cyan-50)',
      }}>{statusText}</p>

      {/* Text input fallback — visible when active */}
      {phase === 'active' && sendTextToGemini && (
        <div style={styles.textInputRow}>
          <input
            type="text"
            value={textInput}
            onChange={(e) => setTextInput(e.target.value)}
            onKeyDown={handleTextKeyDown}
            placeholder="Type a message instead..."
            style={styles.textInput}
            aria-label="Type a message to the interview assistant"
          />
          <button
            onClick={handleSendText}
            disabled={!textInput.trim()}
            style={{
              ...styles.sendButton,
              opacity: textInput.trim() ? 1 : 0.35,
            }}
            aria-label="Send message"
          >
            <Send size={16} />
          </button>
        </div>
      )}

      {/* Failed state: Retry + Skip buttons */}
      {phase === 'failed' && (
        <div style={styles.failedButtons}>
          <NextButton
            label="Retry"
            onClick={handleRetry}
            icon={<RefreshCw size={14} />}
          />
          <NextButton
            label="Skip Interview"
            onClick={handleSkip}
            variant="skip"
          />
        </div>
      )}

      {/* Skip button (shown in non-failed, non-done states) */}
      {phase !== 'done' && phase !== 'failed' && (
        <NextButton
          label="Skip Interview"
          onClick={handleSkip}
          variant="skip"
        />
      )}

      <p style={styles.hint}>
        {phase === 'active'
          ? 'The assistant will configure your agent automatically when the interview is complete.'
          : phase === 'failed'
            ? 'Check your Gemini API key in Settings, or skip to use a default personality.'
            : 'You can skip this step to use a default personality profile.'}
      </p>
    </section>
  );
};

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 24,
    maxWidth: 500,
    width: '100%',
    padding: '0 24px',
  },
  headerBlock: {
    textAlign: 'center',
    maxWidth: 420,
  },
  heading: {
    fontSize: 28,
    fontWeight: 300,
    color: 'var(--text-primary)',
    fontFamily: "'Space Grotesk', sans-serif",
    letterSpacing: '0.05em',
    margin: '0 0 12px 0',
  },
  subtitle: {
    fontSize: 13,
    color: 'var(--text-30)',
    textAlign: 'center',
    lineHeight: 1.6,
    margin: 0,
    fontFamily: "'Inter', sans-serif",
  },
  iconWrap: {
    width: 72,
    height: 72,
    borderRadius: 20,
    background: 'var(--accent-cyan-10)',
    border: '1px solid var(--accent-cyan-20)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
  },
  waveformContainer: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 3,
    height: 64,
    width: '100%',
    maxWidth: 360,
  },
  waveformBar: {
    width: 4,
    borderRadius: 2,
    background: 'var(--accent-cyan-30)',
    transition: 'height 0.08s ease',
  },
  statusText: {
    fontSize: 12,
    color: 'var(--accent-cyan-50)',
    fontFamily: "'JetBrains Mono', monospace",
    letterSpacing: '0.05em',
    margin: 0,
  },
  textInputRow: {
    display: 'flex',
    gap: 8,
    width: '100%',
    maxWidth: 400,
  },
  textInput: {
    flex: 1,
    background: 'var(--onboarding-card)',
    border: '1px solid rgba(255, 255, 255, 0.06)',
    borderRadius: 8,
    padding: '10px 14px',
    fontSize: 13,
    color: 'var(--text-primary)',
    outline: 'none',
    fontFamily: "'Inter', sans-serif",
    transition: 'border-color 0.2s',
  },
  sendButton: {
    width: 40,
    height: 40,
    borderRadius: 8,
    background: 'var(--accent-cyan-10)',
    border: '1px solid var(--accent-cyan-20)',
    color: 'var(--accent-cyan)',
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    transition: 'all 0.2s ease',
    flexShrink: 0,
  },
  failedButtons: {
    display: 'flex',
    gap: 12,
    alignItems: 'center',
  },
  hint: {
    fontSize: 10,
    color: 'var(--text-20)',
    margin: 0,
    textAlign: 'center',
    fontFamily: "'Inter', sans-serif",
    maxWidth: 360,
  },
};

export default InterviewStep;
