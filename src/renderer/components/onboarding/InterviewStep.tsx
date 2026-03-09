/**
 * InterviewStep.tsx — Step 5: Voice interview with waveform.
 *
 * Starts a Gemini Live voice session for the personal intake interview.
 * Displays an animated waveform visualization while the conversation runs.
 * Auto-advances when Gemini calls finalize_agent_identity tool, or allows
 * the user to skip with a default personality profile.
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Mic, SkipForward, RefreshCw } from 'lucide-react';
import type { IdentityChoices } from '../OnboardingWizard';

interface InterviewStepProps {
  identityChoices: IdentityChoices;
  connectToGemini?: (identityContext?: string) => void;
  onComplete: (finalName?: string) => void;
  onBack?: () => void;
}

/** Timeout (ms) before connection is considered failed. */
const CONNECTION_TIMEOUT_MS = 10_000;

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
  onComplete,
  onBack,
}) => {
  const [fadeIn, setFadeIn] = useState(false);
  const [phase, setPhase] = useState<'waiting' | 'connecting' | 'active' | 'failed' | 'done'>('waiting');
  const [statusText, setStatusText] = useState('Preparing voice interview...');
  const [waveformBars, setWaveformBars] = useState<number[]>(new Array(32).fill(0.05));
  const animFrameRef = useRef<number>(0);
  const hasConnectedRef = useRef(false);
  const connectionTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const t = setTimeout(() => setFadeIn(true), 100);
    return () => clearTimeout(t);
  }, []);

  // Attempt to connect to Gemini voice session
  const attemptConnection = useCallback(() => {
    if (!connectToGemini) {
      setPhase('failed');
      setStatusText('Voice session unavailable');
      return;
    }

    setPhase('connecting');
    setStatusText('Connecting to voice session...');

    try {
      const ctx = `Name: ${identityChoices.agentName}, Gender: ${identityChoices.gender}, Voice feel: ${identityChoices.voiceFeel}`;
      connectToGemini(ctx);

      // Fallback timeout: if no audio activity detected within CONNECTION_TIMEOUT_MS, assume failure
      connectionTimerRef.current = setTimeout(() => {
        setPhase((current) => {
          // Only transition to failed if we're still in 'connecting' — if already 'active' or 'done', leave it
          if (current === 'connecting') {
            setStatusText('Voice connection could not be established');
            return 'failed';
          }
          return current;
        });
      }, CONNECTION_TIMEOUT_MS);
    } catch {
      setPhase('failed');
      setStatusText('Voice connection could not be established');
    }
  }, [connectToGemini, identityChoices]);

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
    };
  }, [attemptConnection]);

  // Listen for any audio activity to confirm connection is live
  // The 'gemini-audio-active' event should be dispatched when audio begins streaming
  useEffect(() => {
    const handler = () => {
      // Connection confirmed — clear the fallback timeout and go active
      if (connectionTimerRef.current) {
        clearTimeout(connectionTimerRef.current);
        connectionTimerRef.current = null;
      }
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
  }, []);

  // Retry handler for the failed state
  const handleRetry = useCallback(() => {
    hasConnectedRef.current = false;
    if (connectionTimerRef.current) {
      clearTimeout(connectionTimerRef.current);
      connectionTimerRef.current = null;
    }
    hasConnectedRef.current = true;
    attemptConnection();
  }, [attemptConnection]);

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

  // Listen for the agent finalization event dispatched by App.tsx onAgentFinalized
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
      // Save the default agent config via the finalize endpoint
      await window.eve.onboarding.finalizeAgent({
        agentName: name,
        agentVoice: voiceName,
        gender: gender,
        accent: profile.accent,
        backstory: profile.backstory,
        personalityTraits: profile.traits,
        identityLine: profile.identityLine,
      });
    } catch (err) {
      console.warn('[InterviewStep] Failed to save agent config:', err);
    }

    setTimeout(() => onComplete(name), 600);
  }, [identityChoices, onComplete]);

  return (
    <section style={{
      ...styles.container,
      opacity: fadeIn ? 1 : 0,
      transform: fadeIn ? 'translateY(0)' : 'translateY(16px)',
      transition: 'all 0.7s cubic-bezier(0.16, 1, 0.3, 1)',
    }} aria-label="Voice calibration interview">
      <div style={styles.header} aria-hidden="true">
        <div style={styles.headerLine} />
        <span style={styles.headerLabel}>VOICE CALIBRATION</span>
        <div style={styles.headerLine} />
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

      {/* Explainer */}
      <div style={styles.explainer} aria-live="polite">
        <p style={styles.explainerTitle}>
          {phase === 'waiting' || phase === 'connecting'
            ? 'Setting up your interview...'
            : phase === 'done'
              ? 'Configuration complete!'
              : phase === 'failed'
                ? 'Connection failed'
                : `Speak with your setup assistant`}
        </p>
        <p style={styles.explainerBody}>
          {phase === 'active'
            ? 'Your setup assistant will ask a few personal questions to shape your agent\'s personality. Answer naturally — there are no wrong answers.'
            : phase === 'done'
              ? 'Your agent\'s personality has been configured.'
              : phase === 'failed'
                ? 'The voice session could not be established. You can retry or skip to use a default personality profile.'
                : 'Preparing the voice connection...'}
        </p>
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

      {/* Failed state: Retry + Skip buttons */}
      {phase === 'failed' && (
        <div style={styles.failedButtons}>
          <button onClick={handleRetry} style={styles.retryButton} aria-label="Retry voice connection">
            <RefreshCw size={14} aria-hidden="true" />
            <span>Retry</span>
          </button>
          <button onClick={handleSkip} style={styles.skipButton} aria-label="Skip interview and use default personality">
            <SkipForward size={14} aria-hidden="true" />
            <span>Skip Interview</span>
          </button>
        </div>
      )}

      {/* Skip button (shown in non-failed, non-done states) */}
      {phase !== 'done' && phase !== 'failed' && (
        <button onClick={handleSkip} style={styles.skipButton} aria-label="Skip interview and use default personality">
          <SkipForward size={14} aria-hidden="true" />
          <span>Skip Interview</span>
        </button>
      )}

      <p style={styles.hint}>
        {phase === 'active'
          ? 'The assistant will configure your agent automatically when the interview is complete.'
          : phase === 'failed'
            ? 'Skipping will apply a default personality based on your identity choices.'
            : 'You can skip this step to use a default personality profile.'}
      </p>

      {/* Back button */}
      {onBack && phase !== 'done' && (
        <button onClick={onBack} style={styles.backButton} aria-label="Go back to previous step">
          &#8592; Back
        </button>
      )}
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
  header: {
    display: 'flex',
    alignItems: 'center',
    gap: 16,
    width: '100%',
  },
  headerLine: {
    flex: 1,
    height: 1,
    background: 'linear-gradient(90deg, transparent, var(--accent-cyan-20), transparent)',
  },
  headerLabel: {
    fontSize: 11,
    fontWeight: 600,
    letterSpacing: '0.25em',
    color: 'var(--accent-cyan-70)',
    fontFamily: "'Space Grotesk', sans-serif",
    whiteSpace: 'nowrap',
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
  explainer: {
    textAlign: 'center',
    maxWidth: 380,
  },
  explainerTitle: {
    fontSize: 16,
    fontWeight: 500,
    color: 'var(--text-primary)',
    margin: '0 0 8px 0',
    fontFamily: "'Space Grotesk', sans-serif",
  },
  explainerBody: {
    fontSize: 13,
    color: 'var(--text-40)',
    lineHeight: 1.6,
    margin: 0,
    fontFamily: "'Inter', sans-serif",
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
  skipButton: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '10px 28px',
    background: 'var(--onboarding-card)',
    border: '1px solid var(--onboarding-border)',
    borderRadius: 8,
    color: 'var(--text-50)',
    fontSize: 13,
    fontFamily: "'Space Grotesk', sans-serif",
    cursor: 'pointer',
    transition: 'all 0.2s ease',
  },
  failedButtons: {
    display: 'flex',
    gap: 12,
    alignItems: 'center',
  },
  retryButton: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '10px 28px',
    background: 'var(--accent-cyan-10)',
    border: '1px solid var(--accent-cyan-20)',
    borderRadius: 8,
    color: 'var(--accent-cyan-90)',
    fontSize: 13,
    fontFamily: "'Space Grotesk', sans-serif",
    cursor: 'pointer',
    transition: 'all 0.2s ease',
  },
  hint: {
    fontSize: 10,
    color: 'var(--text-20)',
    margin: 0,
    textAlign: 'center',
    fontFamily: "'Inter', sans-serif",
    maxWidth: 360,
  },
  backButton: {
    background: 'none',
    border: 'none',
    color: 'var(--text-40)',
    fontSize: 13,
    fontFamily: "'Space Grotesk', sans-serif",
    cursor: 'pointer',
    padding: '4px 8px',
    transition: 'color 0.2s ease',
    position: 'absolute' as const,
    bottom: 48,
    left: 48,
  },
};

export default InterviewStep;
