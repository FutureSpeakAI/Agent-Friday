/**
 * AwakeningStep.tsx — Step 0: Cinematic splash screen.
 *
 * "FRIDAY." with cyan accent dot, particle ambiance, tagline.
 * Auto-advances after 4.5s or on click/keypress.
 */

import React, { useEffect, useState, useCallback, useRef } from 'react';

interface AwakeningStepProps {
  onComplete: () => void;
}

const AwakeningStep: React.FC<AwakeningStepProps> = ({ onComplete }) => {
  const [phase, setPhase] = useState<'logo' | 'title' | 'tagline' | 'ready'>('logo');
  const doneRef = useRef(false);

  const advance = useCallback(() => {
    if (doneRef.current) return;
    doneRef.current = true;
    onComplete();
  }, [onComplete]);

  useEffect(() => {
    const timers = [
      setTimeout(() => setPhase('title'), 800),
      setTimeout(() => setPhase('tagline'), 1800),
      setTimeout(() => setPhase('ready'), 2800),
      setTimeout(() => advance(), 4500),
    ];
    return () => timers.forEach(clearTimeout);
  }, [advance]);

  const handleClick = useCallback(() => {
    if (phase === 'tagline' || phase === 'ready') advance();
  }, [phase, advance]);

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Enter' || e.key === ' ') advance();
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [advance]);

  return (
    <section style={styles.container} onClick={handleClick} aria-label="Agent Friday — Welcome" role="region">
      {/* Particle dots */}
      <div style={styles.particles} aria-hidden="true">
        {Array.from({ length: 20 }).map((_, i) => (
          <div
            key={i}
            style={{
              ...styles.particle,
              left: `${Math.random() * 100}%`,
              top: `${Math.random() * 100}%`,
              animationDelay: `${Math.random() * 5}s`,
              animationDuration: `${3 + Math.random() * 4}s`,
              width: Math.random() > 0.7 ? 2 : 1,
              height: Math.random() > 0.7 ? 2 : 1,
            }}
          />
        ))}
      </div>

      {/* Title */}
      <h1
        style={{
          ...styles.title,
          opacity: phase === 'title' || phase === 'tagline' || phase === 'ready' ? 1 : 0,
          transform:
            phase === 'title' || phase === 'tagline' || phase === 'ready'
              ? 'translateY(0)'
              : 'translateY(16px)',
          transition: 'all 1s cubic-bezier(0.16, 1, 0.3, 1)',
        }}
      >
        FRIDAY<span style={styles.titleDot}>.</span>
      </h1>

      {/* Tagline */}
      <p
        aria-live="polite"
        style={{
          ...styles.tagline,
          opacity: phase === 'tagline' || phase === 'ready' ? 1 : 0,
          transition: 'opacity 1s ease',
        }}
      >
        FutureSpeak Intelligence Systems
      </p>

      {/* Skip hint */}
      <div
        style={{
          ...styles.skipHint,
          opacity: phase === 'ready' ? 0.4 : 0,
          transition: 'opacity 0.6s ease',
        }}
      >
        Press any key or click to continue
      </div>
    </section>
  );
};

const styles: Record<string, React.CSSProperties> = {
  container: {
    position: 'relative',
    width: '100%',
    height: '100%',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 16,
    cursor: 'pointer',
    userSelect: 'none',
  },
  particles: {
    position: 'absolute',
    inset: 0,
    overflow: 'hidden',
    pointerEvents: 'none',
  },
  particle: {
    position: 'absolute',
    borderRadius: '50%',
    background: 'var(--accent-cyan-30)',
    animation: 'onb-particle-float 5s ease-in-out infinite',
  },
  title: {
    fontSize: 56,
    fontWeight: 200,
    letterSpacing: '0.2em',
    color: 'var(--text-primary)',
    fontFamily: "'Space Grotesk', sans-serif",
    zIndex: 2,
    margin: 0,
  },
  titleDot: {
    color: 'var(--accent-cyan)',
  },
  tagline: {
    fontSize: 13,
    fontWeight: 400,
    letterSpacing: '0.2em',
    color: 'var(--text-30)',
    fontFamily: "'Space Grotesk', sans-serif",
    textTransform: 'uppercase',
    zIndex: 2,
  },
  skipHint: {
    position: 'absolute',
    bottom: 48,
    fontSize: 11,
    letterSpacing: '0.1em',
    color: 'var(--text-40)',
    fontFamily: "'Space Grotesk', sans-serif",
    zIndex: 2,
  },
};

export default AwakeningStep;
