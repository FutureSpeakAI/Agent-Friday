import React, { useRef, useEffect } from 'react';
import type { MoodPalette } from '../contexts/MoodContext';

interface VoiceOrbProps {
  isListening: boolean;
  isProcessing: boolean;
  isStreaming?: boolean;
  onClick: () => void;
  interimTranscript: string;
  getLevels?: () => { mic: number; output: number };
  /** Mood-derived visual parameters */
  moodPalette?: MoodPalette;
  moodIntensity?: number;
}

export default function VoiceOrb({
  isListening,
  isProcessing,
  isStreaming,
  onClick,
  interimTranscript,
  getLevels,
  moodPalette,
  moodIntensity = 0.4,
}: VoiceOrbProps) {
  const barsRef = useRef<(HTMLDivElement | null)[]>([]);
  const outerGlowRef = useRef<HTMLDivElement | null>(null);
  const rafRef = useRef<number>(0);

  const state = isStreaming
    ? 'streaming'
    : isProcessing
      ? 'processing'
      : isListening
        ? 'listening'
        : 'idle';

  // Helper: convert hex to rgb components
  const hexRgb = (hex: string): [number, number, number] => {
    const h = hex.replace('#', '');
    return [parseInt(h.substring(0, 2), 16), parseInt(h.substring(2, 4), 16), parseInt(h.substring(4, 6), 16)];
  };

  // Mood-tinted color helper — blends base color with mood at 30% weight
  const moodTint = (baseR: number, baseG: number, baseB: number, alpha: number): string => {
    if (!moodPalette) return `rgba(${baseR}, ${baseG}, ${baseB}, ${alpha})`;
    const [mr, mg, mb] = hexRgb(moodPalette.primary);
    const t = 0.3; // mood blend factor
    const r = Math.round(baseR + (mr - baseR) * t);
    const g = Math.round(baseG + (mg - baseG) * t);
    const b = Math.round(baseB + (mb - baseB) * t);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  };

  const orbColors: Record<string, string> = {
    idle: moodTint(0, 240, 255, 0.15),
    listening: moodTint(138, 43, 226, 0.25),
    processing: moodTint(59, 130, 246, 0.2),
    streaming: moodTint(212, 165, 116, 0.25),
  };

  // Glow radius scales with mood intensity
  const glowBoost = 1.0 + moodIntensity * 0.3;
  const glowInner = Math.round(80 * glowBoost);
  const glowOuter = Math.round(160 * glowBoost);

  const glowColors: Record<string, string> = {
    idle: `0 0 ${Math.round(60 * glowBoost)}px ${moodTint(0, 240, 255, 0.2)}, 0 0 ${Math.round(120 * glowBoost)}px ${moodTint(0, 240, 255, 0.1)}`,
    listening: `0 0 ${glowInner}px ${moodTint(138, 43, 226, 0.35)}, 0 0 ${glowOuter}px ${moodTint(138, 43, 226, 0.15)}`,
    processing: `0 0 ${Math.round(60 * glowBoost)}px ${moodTint(59, 130, 246, 0.25)}, 0 0 ${Math.round(120 * glowBoost)}px ${moodTint(59, 130, 246, 0.1)}`,
    streaming: `0 0 ${glowInner}px ${moodTint(212, 165, 116, 0.35)}, 0 0 ${glowOuter}px ${moodTint(212, 165, 116, 0.15)}`,
  };

  const borderColors: Record<string, string> = {
    idle: moodTint(0, 240, 255, 0.3),
    listening: moodTint(138, 43, 226, 0.5),
    processing: moodTint(59, 130, 246, 0.4),
    streaming: moodTint(212, 165, 116, 0.5),
  };

  // Audio-reactive wave bars + outer glow ring — driven by AnalyserNode levels
  useEffect(() => {
    if (state !== 'listening' && state !== 'streaming') {
      cancelAnimationFrame(rafRef.current);
      // Reset outer glow when idle
      if (outerGlowRef.current) {
        outerGlowRef.current.style.opacity = '0';
        outerGlowRef.current.style.transform = 'translateX(-50%) scale(1)';
      }
      return;
    }

    const tick = () => {
      const levels = getLevels?.() ?? { mic: 0, output: 0 };
      const level = state === 'listening' ? levels.mic : levels.output;

      // Each bar gets a different phase offset for organic movement
      barsRef.current.forEach((bar, i) => {
        if (!bar) return;
        const phase = (Date.now() / 300 + i * 0.7) % (Math.PI * 2);
        const ambient = Math.sin(phase) * 0.3 + 0.5; // 0.2–0.8 base
        const h = 6 + (ambient + level * 3.0) * 14; // 6–48px range
        bar.style.height = `${Math.min(h, 48)}px`;
      });

      // Outer reactive glow ring — responds to audio levels
      if (outerGlowRef.current) {
        const glowOpacity = 0.1 + level * 0.5;
        const glowScale = 1 + level * 0.15;
        outerGlowRef.current.style.opacity = `${Math.min(glowOpacity, 0.6)}`;
        outerGlowRef.current.style.transform = `translateX(-50%) scale(${glowScale})`;
      }

      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafRef.current);
  }, [state, getLevels]);

  const barCount = state === 'streaming' ? 7 : 5;
  const barColor =
    state === 'streaming'
      ? 'rgba(212, 165, 116, 0.8)'
      : 'rgba(138, 43, 226, 0.8)';

  const outerGlowColor =
    state === 'streaming'
      ? 'radial-gradient(circle, rgba(212, 165, 116, 0.15) 0%, rgba(212, 165, 116, 0.05) 50%, transparent 70%)'
      : state === 'listening'
        ? 'radial-gradient(circle, rgba(138, 43, 226, 0.12) 0%, rgba(138, 43, 226, 0.04) 50%, transparent 70%)'
        : 'none';

  return (
    <div style={styles.wrapper}>
      {/* Audio-reactive outer glow ring — diffuse, larger */}
      <div
        ref={outerGlowRef}
        style={{
          ...styles.audioGlow,
          background: outerGlowColor,
          opacity: 0,
        }}
      />

      {/* Outer ring animation */}
      <div
        style={{
          ...styles.outerRing,
          borderColor: borderColors[state],
          animation: state !== 'idle' ? 'pulse-ring 2s ease-in-out infinite' : 'none',
          opacity: state === 'idle' ? 0.3 : 0.6,
        }}
      />

      {/* Second ring */}
      <div
        style={{
          ...styles.middleRing,
          borderColor: borderColors[state],
          animation: state !== 'idle' ? 'pulse-ring 2s ease-in-out infinite 0.3s' : 'none',
          opacity: state === 'idle' ? 0.15 : 0.3,
        }}
      />

      {/* The orb itself */}
      <button
        onClick={onClick}
        style={{
          ...styles.orb,
          background: `radial-gradient(circle at 40% 35%, ${orbColors[state]}, transparent 70%)`,
          boxShadow: glowColors[state],
          borderColor: borderColors[state],
          cursor: isProcessing ? 'wait' : 'pointer',
        }}
      >
        <div style={styles.orbInner}>
          {state === 'idle' && (
            <svg
              width="24"
              height="24"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
            >
              <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
              <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
              <line x1="12" y1="19" x2="12" y2="23" />
              <line x1="8" y1="23" x2="16" y2="23" />
            </svg>
          )}
          {(state === 'listening' || state === 'streaming') && (
            <div style={styles.waveContainer}>
              {[...Array(barCount)].map((_, i) => (
                <div
                  key={i}
                  ref={(el) => { barsRef.current[i] = el; }}
                  style={{
                    ...styles.waveBar,
                    background: barColor,
                    height: 6, // initial — RAF updates this
                  }}
                />
              ))}
            </div>
          )}
          {state === 'processing' && (
            <div style={styles.spinnerDots}>
              {[...Array(3)].map((_, i) => (
                <div
                  key={i}
                  style={{
                    ...styles.dot,
                    animationDelay: `${i * 0.2}s`,
                  }}
                />
              ))}
            </div>
          )}
        </div>
      </button>

      {/* Label — shows end of text for long transcripts */}
      <div style={styles.label}>
        {state === 'idle' && 'Press Space or Click to Speak'}
        {state === 'listening' && (
          <span style={styles.transcriptLabel}>
            {interimTranscript || 'Listening...'}
          </span>
        )}
        {state === 'processing' && 'Processing...'}
        {state === 'streaming' && 'Friday is speaking...'}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  wrapper: {
    position: 'relative',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 16,
    zIndex: 10,
  },
  audioGlow: {
    position: 'absolute',
    width: 240,
    height: 240,
    borderRadius: '50%',
    top: -50,
    left: '50%',
    transform: 'translateX(-50%)',
    pointerEvents: 'none',
    transition: 'opacity 0.15s ease-out, transform 0.15s ease-out',
  },
  outerRing: {
    position: 'absolute',
    width: 168,
    height: 168,
    borderRadius: '50%',
    border: '1px solid',
    top: -12,
    left: '50%',
    transform: 'translateX(-50%)',
    pointerEvents: 'none',
  },
  middleRing: {
    position: 'absolute',
    width: 198,
    height: 198,
    borderRadius: '50%',
    border: '1px solid',
    top: -27,
    left: '50%',
    transform: 'translateX(-50%)',
    pointerEvents: 'none',
  },
  orb: {
    width: 130,
    height: 130,
    borderRadius: '50%',
    border: '1.5px solid',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    transition: 'all 0.4s ease',
    outline: 'none',
    position: 'relative',
    backdropFilter: 'blur(10px)',
  },
  orbInner: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    color: '#e0e0e8',
  },
  waveContainer: {
    display: 'flex',
    alignItems: 'center',
    gap: 3,
    height: 48,
  },
  waveBar: {
    width: 3,
    borderRadius: 2,
    transition: 'height 0.06s ease-out',
  },
  spinnerDots: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  dot: {
    width: 8,
    height: 8,
    borderRadius: '50%',
    background: 'rgba(59, 130, 246, 0.8)',
    animation: 'bounce-dot 0.6s ease-in-out infinite',
  },
  label: {
    fontSize: 13,
    color: '#8888a0',
    fontWeight: 400,
    letterSpacing: '0.03em',
    textAlign: 'center',
    maxWidth: 300,
    overflow: 'hidden',
  },
  transcriptLabel: {
    display: 'block',
    direction: 'rtl' as const, // shows end of text when overflow
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
    overflow: 'hidden',
    unicodeBidi: 'plaintext' as const,
  },
};
