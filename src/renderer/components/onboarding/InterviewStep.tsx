/**
 * InterviewStep.tsx — Step 4: Voice interview with waveform + transcript + text input.
 *
 * Starts a voice session (local-first or Gemini Live) for the personal intake interview.
 * Displays an animated waveform visualization, a live transcript panel with processing
 * state indicators, and a text input fallback for typing messages.
 * Auto-advances when the backend calls finalize_agent_identity tool, or allows
 * the user to skip with a default personality profile.
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Mic, RefreshCw, Send } from 'lucide-react';
import NextButton from './shared/NextButton';
import type { IdentityChoices } from '../OnboardingWizard';

interface InterviewStepProps {
  identityChoices: IdentityChoices;
  connectVoice?: (identityContext?: string) => Promise<void>;
  sendText?: (text: string) => void;
  onComplete: (finalName?: string) => void;
  onBack?: () => void;
}

interface TranscriptMessage {
  id: string;
  role: 'user' | 'assistant';
  text: string;
}

/**
 * Blanket timeout (ms) — only used as fallback when ConnectionStageMonitor
 * is not available. Increased to 45s to accommodate local-first first-run
 * scenarios where model loading + first inference can take 25s+.
 */
const LEGACY_CONNECTION_TIMEOUT_MS = 45_000;

/**
 * Legacy staged status messages — backend-agnostic. Used only when
 * ConnectionStageMonitor IPC is not available.
 */
const LEGACY_CONNECTION_STAGES: { delay: number; text: string }[] = [
  { delay: 0, text: 'Connecting to voice session...' },
  { delay: 2500, text: 'Loading voice models...' },
  { delay: 5000, text: 'Initializing speech pipeline...' },
  { delay: 10000, text: 'Preparing language model...' },
  { delay: 20000, text: 'First load can take a moment — almost ready...' },
  { delay: 35000, text: 'Still waiting — this is taking longer than usual...' },
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

const PROCESSING_STATUS: Record<string, string> = {
  listening: 'Listening — speak naturally...',
  thinking: 'Processing your response...',
  speaking: 'Speaking...',
};

const InterviewStep: React.FC<InterviewStepProps> = ({
  identityChoices,
  connectVoice,
  sendText: sendTextProp,
  onComplete,
  onBack,
}) => {
  const [fadeIn, setFadeIn] = useState(false);
  const [phase, setPhase] = useState<'waiting' | 'connecting' | 'active' | 'failed' | 'done'>('waiting');
  const [statusText, setStatusText] = useState('Preparing voice interview...');
  const [waveformBars, setWaveformBars] = useState<number[]>(new Array(32).fill(0.05));
  const [textInput, setTextInput] = useState('');
  const [failureDetail, setFailureDetail] = useState<string | null>(null);
  const [failureAction, setFailureAction] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<TranscriptMessage[]>([]);
  const [processingState, setProcessingState] = useState<'idle' | 'listening' | 'thinking' | 'speaking'>('idle');
  const animFrameRef = useRef<number>(0);
  const hasConnectedRef = useRef(false);
  const connectionTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stageTimersRef = useRef<ReturnType<typeof setTimeout>[]>([]);
  const stageCleanupRef = useRef<Array<() => void>>([]);
  const hasStageMonitorRef = useRef(false);
  const transcriptEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const t = setTimeout(() => setFadeIn(true), 100);
    return () => clearTimeout(t);
  }, []);

  // Auto-scroll transcript to bottom
  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [transcript, processingState]);

  // Clear any staged progress timers
  const clearStageTimers = useCallback(() => {
    stageTimersRef.current.forEach(clearTimeout);
    stageTimersRef.current = [];
  }, []);

  // Clean up ConnectionStageMonitor listeners
  const clearStageMonitorListeners = useCallback(() => {
    for (const cleanup of stageCleanupRef.current) cleanup();
    stageCleanupRef.current = [];
  }, []);

  // ── Interview event listeners ─────────────────────────────────────────
  useEffect(() => {
    const onUserTranscript = (e: Event) => {
      const { text } = (e as CustomEvent).detail;
      setTranscript((prev) => {
        // Deduplicate: if the last user message matches, skip
        const last = prev[prev.length - 1];
        if (last && last.role === 'user' && last.text === text) return prev;
        return [...prev, { id: crypto.randomUUID(), role: 'user', text }];
      });
      setProcessingState('thinking');
    };

    const onAiResponse = (e: Event) => {
      const { text, streaming } = (e as CustomEvent).detail;
      setTranscript((prev) => {
        if (streaming) {
          // Gemini streaming: append to last assistant message
          const last = prev[prev.length - 1];
          if (last && last.role === 'assistant') {
            return [...prev.slice(0, -1), { ...last, text: last.text + text }];
          }
        }
        return [...prev, { id: crypto.randomUUID(), role: 'assistant', text }];
      });
    };

    const onProcessingState = (e: Event) => {
      const { state } = (e as CustomEvent).detail;
      setProcessingState(state);
    };

    const onConnectionFailed = (e: Event) => {
      const { message } = (e as CustomEvent).detail;
      // Immediately fail — no need to wait for the timeout
      if (connectionTimerRef.current) {
        clearTimeout(connectionTimerRef.current);
        connectionTimerRef.current = null;
      }
      clearStageTimers();
      clearStageMonitorListeners();
      setPhase('failed');
      setStatusText('No voice backend available');
      setFailureDetail(message);
      setFailureAction('Install Ollama for local voice or add a Gemini API key in Settings.');
    };

    window.addEventListener('interview-user-transcript', onUserTranscript);
    window.addEventListener('interview-ai-response', onAiResponse);
    window.addEventListener('interview-processing-state', onProcessingState);
    window.addEventListener('interview-connection-failed', onConnectionFailed);
    return () => {
      window.removeEventListener('interview-user-transcript', onUserTranscript);
      window.removeEventListener('interview-ai-response', onAiResponse);
      window.removeEventListener('interview-processing-state', onProcessingState);
      window.removeEventListener('interview-connection-failed', onConnectionFailed);
    };
  }, [clearStageTimers, clearStageMonitorListeners]);

  // Attempt to connect to voice session
  const attemptConnection = useCallback(() => {
    if (!connectVoice) {
      setPhase('failed');
      setStatusText('Voice session unavailable');
      return;
    }

    setPhase('connecting');
    setFailureDetail(null);
    setFailureAction(null);
    clearStageTimers();
    clearStageMonitorListeners();

    // ── Track 6: Subscribe to ConnectionStageMonitor for real progress ──
    let usingStageMonitor = false;
    try {
      if (window.eve.connectionStage) {
        usingStageMonitor = true;
        hasStageMonitorRef.current = true;

        const cleanups: Array<() => void> = [];

        cleanups.push(
          window.eve.connectionStage.onStageEnter((payload) => {
            setPhase((current) => {
              if (current === 'connecting') {
                setStatusText(payload.userMessage);
              }
              return current;
            });
          }),
        );

        cleanups.push(
          window.eve.connectionStage.onStageTimeout((payload) => {
            setPhase((current) => {
              if (current === 'connecting') {
                setStatusText(payload.failureMessage);
                setFailureDetail(payload.failureMessage);
                setFailureAction(payload.failureAction || null);
                return 'failed';
              }
              return current;
            });
            if (connectionTimerRef.current) {
              clearTimeout(connectionTimerRef.current);
              connectionTimerRef.current = null;
            }
          }),
        );

        cleanups.push(
          window.eve.connectionStage.onAllComplete(() => {
            if (connectionTimerRef.current) {
              clearTimeout(connectionTimerRef.current);
              connectionTimerRef.current = null;
            }
          }),
        );

        stageCleanupRef.current = cleanups;
      }
    } catch {
      usingStageMonitor = false;
    }

    // Legacy fallback: schedule timed status messages if monitor unavailable
    if (!usingStageMonitor) {
      for (const stage of LEGACY_CONNECTION_STAGES) {
        const timer = setTimeout(() => {
          setPhase((current) => {
            if (current === 'connecting') setStatusText(stage.text);
            return current;
          });
        }, stage.delay);
        stageTimersRef.current.push(timer);
      }
    }

    connectVoice().catch((err: any) => {
      if (connectionTimerRef.current) {
        clearTimeout(connectionTimerRef.current);
        connectionTimerRef.current = null;
      }
      clearStageTimers();
      clearStageMonitorListeners();
      setPhase('failed');

      const msg = String(err?.message || '').toLowerCase();
      if (msg.includes('api key') || msg.includes('401') || msg.includes('1008')) {
        setStatusText('Authentication failed — check your API key in Settings');
        setFailureDetail('The API key may be invalid or expired.');
        setFailureAction('Open Settings to update your API key.');
      } else if (msg.includes('network') || msg.includes('failed to fetch')) {
        setStatusText('Network error — check your internet connection');
        setFailureDetail('Could not reach the voice backend.');
        setFailureAction('Check your internet connection and try again.');
      } else if (msg.includes('no voice backend')) {
        setStatusText('No voice backend available');
        setFailureDetail('Neither Ollama (local) nor Gemini (cloud) is accessible.');
        setFailureAction('Install Ollama for local voice or add a Gemini API key in Settings.');
      } else {
        setStatusText('Voice connection could not be established');
        setFailureDetail(String(err?.message || 'Unknown error'));
      }
    });

    // Legacy blanket timeout
    connectionTimerRef.current = setTimeout(() => {
      clearStageTimers();
      clearStageMonitorListeners();
      setPhase((current) => {
        if (current === 'connecting') {
          setStatusText('Connection timed out — check your voice settings');
          setFailureDetail('The connection attempt exceeded the maximum wait time.');
          return 'failed';
        }
        return current;
      });
    }, LEGACY_CONNECTION_TIMEOUT_MS);
  }, [connectVoice, identityChoices, clearStageTimers, clearStageMonitorListeners]);

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
      clearStageMonitorListeners();
    };
  }, [attemptConnection, clearStageTimers, clearStageMonitorListeners]);

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
          setProcessingState('listening');
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
    clearStageMonitorListeners();
    setFailureDetail(null);
    setFailureAction(null);
    setTranscript([]);
    setProcessingState('idle');
    hasConnectedRef.current = true;
    attemptConnection();
  }, [attemptConnection, clearStageTimers, clearStageMonitorListeners]);

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
    if (!trimmed || !sendTextProp) return;
    // Add to transcript immediately (dedup against bridge event by checking last msg)
    setTranscript((prev) => {
      const last = prev[prev.length - 1];
      if (last && last.role === 'user' && last.text === trimmed) return prev;
      return [...prev, { id: crypto.randomUUID(), role: 'user', text: trimmed }];
    });
    setProcessingState('thinking');
    sendTextProp(trimmed);
    setTextInput('');
  }, [textInput, sendTextProp]);

  const handleTextKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendText();
    }
  }, [handleSendText]);

  // Dynamic status text when active
  const activeStatusText = phase === 'active' && processingState !== 'idle'
    ? PROCESSING_STATUS[processingState] || statusText
    : statusText;

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

      {/* Transcript panel — visible when active and has messages */}
      {phase === 'active' && transcript.length > 0 && (
        <div style={styles.transcriptPanel} aria-label="Interview transcript" role="log">
          {transcript.map((msg) => (
            <div
              key={msg.id}
              style={{
                ...styles.transcriptMessage,
                alignSelf: msg.role === 'user' ? 'flex-end' : 'flex-start',
                background: msg.role === 'user'
                  ? 'rgba(0, 240, 255, 0.08)'
                  : 'var(--onboarding-card)',
                borderColor: msg.role === 'user'
                  ? 'rgba(0, 240, 255, 0.15)'
                  : 'rgba(255, 255, 255, 0.06)',
              }}
            >
              <span style={{
                ...styles.transcriptRole,
                color: msg.role === 'user' ? 'var(--accent-cyan-70)' : 'var(--text-30)',
              }}>
                {msg.role === 'user' ? 'You' : 'Assistant'}
              </span>
              <span style={styles.transcriptText}>{msg.text}</span>
            </div>
          ))}
          {processingState === 'thinking' && (
            <div style={{ ...styles.transcriptMessage, alignSelf: 'flex-start', background: 'var(--onboarding-card)', borderColor: 'rgba(255, 255, 255, 0.06)' }}>
              <span style={{ ...styles.transcriptRole, color: 'var(--text-30)' }}>Assistant</span>
              <span style={styles.thinkingDots}>
                <span style={styles.dot} />
                <span style={{ ...styles.dot, animationDelay: '0.2s' }} />
                <span style={{ ...styles.dot, animationDelay: '0.4s' }} />
              </span>
            </div>
          )}
          <div ref={transcriptEndRef} />
        </div>
      )}

      {/* Status */}
      <p role="status" aria-live="polite" style={{
        ...styles.statusText,
        color: phase === 'failed' ? 'var(--accent-red)' : 'var(--accent-cyan-50)',
      }}>{activeStatusText}</p>

      {/* Text input fallback — visible when active */}
      {phase === 'active' && sendTextProp && (
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

      {/* Failed state: error detail + recovery + Retry/Skip buttons */}
      {phase === 'failed' && (
        <>
          {(failureDetail || failureAction) && (
            <div style={styles.failureDetailBlock}>
              {failureDetail && (
                <p style={styles.failureDetailText}>{failureDetail}</p>
              )}
              {failureAction && (
                <p style={styles.failureActionText}>{failureAction}</p>
              )}
            </div>
          )}
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
        </>
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
            ? 'Check your voice settings, or skip to use a default personality.'
            : 'You can skip this step to use a default personality profile.'}
      </p>

      {/* Inline keyframes for thinking dots animation */}
      <style>{`
        @keyframes interviewDotPulse {
          0%, 80%, 100% { opacity: 0.2; transform: scale(0.8); }
          40% { opacity: 1; transform: scale(1); }
        }
      `}</style>
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
  transcriptPanel: {
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
    width: '100%',
    maxWidth: 420,
    maxHeight: 240,
    overflowY: 'auto',
    padding: '8px 4px',
    scrollBehavior: 'smooth',
  },
  transcriptMessage: {
    display: 'flex',
    flexDirection: 'column',
    gap: 2,
    maxWidth: '85%',
    padding: '8px 12px',
    borderRadius: 10,
    border: '1px solid',
  },
  transcriptRole: {
    fontSize: 9,
    fontWeight: 600,
    letterSpacing: '0.1em',
    textTransform: 'uppercase' as const,
    fontFamily: "'Space Grotesk', sans-serif",
  },
  transcriptText: {
    fontSize: 13,
    color: 'var(--text-primary)',
    lineHeight: 1.5,
    fontFamily: "'Inter', sans-serif",
  },
  thinkingDots: {
    display: 'flex',
    gap: 4,
    padding: '4px 0',
  },
  dot: {
    width: 6,
    height: 6,
    borderRadius: '50%',
    background: 'var(--accent-cyan-50)',
    animation: 'interviewDotPulse 1.4s ease-in-out infinite',
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
  failureDetailBlock: {
    maxWidth: 400,
    width: '100%',
    padding: '10px 14px',
    background: 'rgba(239, 68, 68, 0.06)',
    border: '1px solid rgba(239, 68, 68, 0.15)',
    borderRadius: 8,
    textAlign: 'center' as const,
  },
  failureDetailText: {
    fontSize: 12,
    color: 'rgba(239, 68, 68, 0.8)',
    margin: '0 0 4px 0',
    lineHeight: 1.5,
    fontFamily: "'Inter', sans-serif",
  },
  failureActionText: {
    fontSize: 11,
    color: 'var(--text-30)',
    margin: 0,
    lineHeight: 1.5,
    fontFamily: "'Inter', sans-serif",
    fontStyle: 'italic' as const,
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
