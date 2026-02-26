/**
 * AgentCreation.tsx — Cinematic agent materialization sequence.
 *
 * Full-screen animation between onboarding and the agent's first greeting.
 * Warm golden glow builds → NexusCore desktop is revealed for the first time.
 *
 * Timeline (~6s):
 *  0-2s   — Dark screen, subtle pulsing orb, status text
 *  2-3s   — Orb expands, warm golden glow fills screen
 *  3-4.5s — Glow softens, NexusCore fades in through it (onNexusReveal)
 *  4.5-5.5s — Overlay fades out
 *  5.5s   — onComplete fires
 */

import React, { useState, useEffect, useRef } from 'react';

interface AgentCreationProps {
  agentName: string;
  onNexusReveal?: () => void;
  onComplete?: () => void;
}

const STEPS = [
  { label: 'Configuring personality matrix', delay: 200 },
  { label: 'Calibrating voice parameters', delay: 800 },
  { label: 'Loading long-term memory', delay: 1400 },
  { label: 'Initializing consciousness', delay: 2000 },
];

export default function AgentCreation({ agentName, onNexusReveal, onComplete }: AgentCreationProps) {
  const [visibleSteps, setVisibleSteps] = useState(0);
  const [completedSteps, setCompletedSteps] = useState(0);
  const [phase, setPhase] = useState<'building' | 'glow' | 'revealing' | 'fading'>('building');
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const timers: ReturnType<typeof setTimeout>[] = [];

    // Step-by-step status reveals (0-2s)
    STEPS.forEach((step, i) => {
      timers.push(setTimeout(() => setVisibleSteps(i + 1), step.delay));
      timers.push(setTimeout(() => setCompletedSteps(i + 1), step.delay + 400));
    });

    // Phase 2: Warm glow expansion (2s)
    timers.push(setTimeout(() => setPhase('glow'), 2000));

    // Phase 3: NexusCore reveal (3s) — trigger parent to show NexusCore
    timers.push(setTimeout(() => {
      setPhase('revealing');
      onNexusReveal?.();
    }, 3000));

    // Phase 4: Fade overlay out (4.5s)
    timers.push(setTimeout(() => setPhase('fading'), 4500));

    // Done — fire onComplete (5.5s)
    timers.push(setTimeout(() => onComplete?.(), 5500));

    return () => timers.forEach(clearTimeout);
  }, [onNexusReveal, onComplete]);

  // Dynamic glow intensity based on phase
  const glowOpacity = phase === 'building' ? 0.3 : phase === 'glow' ? 1 : phase === 'revealing' ? 0.6 : 0;
  const glowScale = phase === 'building' ? 1 : phase === 'glow' ? 2.5 : phase === 'revealing' ? 3 : 3.5;
  const overlayOpacity = phase === 'fading' ? 0 : 1;

  return (
    <div
      ref={containerRef}
      style={{
        ...styles.overlay,
        opacity: overlayOpacity,
        transition: 'opacity 1s ease-out',
      }}
    >
      {/* Warm glow background — builds then softens */}
      <div
        style={{
          ...styles.warmGlow,
          opacity: glowOpacity,
          transform: `translate(-50%, -50%) scale(${glowScale})`,
          transition: 'opacity 1.2s ease-in-out, transform 1.5s ease-out',
        }}
      />

      {/* Central content — fades once glow takes over */}
      <div
        style={{
          ...styles.content,
          opacity: phase === 'revealing' || phase === 'fading' ? 0 : 1,
          transition: 'opacity 0.8s ease-out',
        }}
      >
        {/* Pulsing orb */}
        <div style={styles.orbContainer}>
          <div
            style={{
              ...styles.orb,
              animation: phase === 'glow'
                ? 'creationOrbExpand 1s ease-out forwards'
                : 'creationOrbPulse 1.5s ease-in-out infinite',
            }}
          />
          <div style={{ ...styles.orbRing, animation: 'creationRingSpin 3s linear infinite' }} />
          <div style={{ ...styles.orbRing2, animation: 'creationRingSpin 4s linear infinite reverse' }} />
        </div>

        {/* Agent name */}
        <div style={styles.nameContainer}>
          <div style={styles.initLabel}>INITIALIZING</div>
          <div style={styles.agentName}>{agentName.toUpperCase()}</div>
        </div>

        {/* Status steps */}
        <div style={styles.stepsContainer}>
          {STEPS.map((step, i) => {
            const isVisible = i < visibleSteps;
            const isComplete = i < completedSteps;
            const isCurrent = isVisible && !isComplete;
            const label = i === STEPS.length - 1
              ? step.label.replace('consciousness', agentName)
              : step.label;

            return (
              <div
                key={i}
                style={{
                  ...styles.stepRow,
                  opacity: isVisible ? 1 : 0,
                  transform: isVisible ? 'translateY(0)' : 'translateY(8px)',
                  transition: 'all 0.35s ease-out',
                }}
              >
                <span
                  style={{
                    ...styles.stepIndicator,
                    color: isComplete ? '#FFD700' : isCurrent ? '#FFA500' : '#444',
                  }}
                >
                  {isComplete ? '\u2713' : isCurrent ? '\u25C9' : '\u25CB'}
                </span>
                <span
                  style={{
                    ...styles.stepLabel,
                    color: isComplete ? '#FFD700' : isCurrent ? '#FFA500' : '#666',
                  }}
                >
                  {label}...
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {/* Keyframe animations */}
      <style>{`
        @keyframes creationOrbPulse {
          0%, 100% {
            transform: scale(1);
            box-shadow: 0 0 40px rgba(255, 215, 0, 0.3), 0 0 80px rgba(255, 165, 0, 0.15);
          }
          50% {
            transform: scale(1.15);
            box-shadow: 0 0 60px rgba(255, 215, 0, 0.5), 0 0 120px rgba(255, 165, 0, 0.3);
          }
        }

        @keyframes creationOrbExpand {
          0% {
            transform: scale(1.15);
            box-shadow: 0 0 60px rgba(255, 215, 0, 0.5), 0 0 120px rgba(255, 165, 0, 0.3);
          }
          100% {
            transform: scale(3);
            box-shadow: 0 0 200px rgba(255, 215, 0, 0.8), 0 0 400px rgba(255, 165, 0, 0.5);
            opacity: 0;
          }
        }

        @keyframes creationRingSpin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }

        @keyframes creationGlowPulse {
          0%, 100% { opacity: 0.3; transform: translate(-50%, -50%) scale(1); }
          50% { opacity: 0.5; transform: translate(-50%, -50%) scale(1.05); }
        }
      `}</style>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  overlay: {
    position: 'fixed',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    zIndex: 100,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    background: 'rgba(6, 11, 25, 0.98)',
    pointerEvents: 'auto',
  },
  warmGlow: {
    position: 'absolute',
    top: '50%',
    left: '50%',
    width: 400,
    height: 400,
    borderRadius: '50%',
    background: 'radial-gradient(circle, rgba(255, 215, 0, 0.25) 0%, rgba(255, 165, 0, 0.12) 30%, rgba(255, 120, 0, 0.04) 60%, transparent 80%)',
    pointerEvents: 'none',
    animation: 'creationGlowPulse 2s ease-in-out infinite',
  },
  content: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 36,
    zIndex: 2,
  },
  orbContainer: {
    position: 'relative',
    width: 120,
    height: 120,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
  },
  orb: {
    width: 60,
    height: 60,
    borderRadius: '50%',
    background: 'radial-gradient(circle at 35% 35%, #FFD700, #FFA500)',
    boxShadow: '0 0 40px rgba(255, 215, 0, 0.3), 0 0 80px rgba(255, 165, 0, 0.15)',
  },
  orbRing: {
    position: 'absolute',
    top: 0,
    left: 0,
    width: 120,
    height: 120,
    borderRadius: '50%',
    border: '1px solid rgba(255, 215, 0, 0.2)',
    borderTopColor: 'rgba(255, 215, 0, 0.6)',
    borderRightColor: 'transparent',
    pointerEvents: 'none',
  },
  orbRing2: {
    position: 'absolute',
    top: 10,
    left: 10,
    width: 100,
    height: 100,
    borderRadius: '50%',
    border: '1px solid rgba(255, 165, 0, 0.15)',
    borderBottomColor: 'rgba(255, 165, 0, 0.5)',
    borderLeftColor: 'transparent',
    pointerEvents: 'none',
  },
  nameContainer: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 8,
  },
  initLabel: {
    fontSize: 11,
    fontWeight: 600,
    letterSpacing: '0.25em',
    color: '#FFD700',
    opacity: 0.7,
    fontFamily: "'JetBrains Mono', monospace",
  },
  agentName: {
    fontSize: 42,
    fontWeight: 300,
    letterSpacing: '0.12em',
    color: '#E0E6F0',
    textShadow: '0 0 30px rgba(255, 215, 0, 0.3), 0 0 60px rgba(255, 165, 0, 0.15)',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  stepsContainer: {
    display: 'flex',
    flexDirection: 'column',
    gap: 12,
    minWidth: 280,
  },
  stepRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
  },
  stepIndicator: {
    fontSize: 14,
    fontFamily: "'JetBrains Mono', monospace",
    width: 18,
    textAlign: 'center',
  },
  stepLabel: {
    fontSize: 13,
    fontFamily: "'JetBrains Mono', monospace",
    letterSpacing: '0.02em',
  },
};
