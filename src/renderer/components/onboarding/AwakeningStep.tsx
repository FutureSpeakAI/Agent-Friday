/**
 * AwakeningStep.tsx — Step 0: Cinematic splash screen.
 *
 * Animated logo, particle dots, tagline. Auto-advances after 4s or on click.
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
      setTimeout(() => advance(), 4200),
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
        {Array.from({ length: 40 }).map((_, i) => (
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

      {/* Central glow */}
      <div style={styles.glow} aria-hidden="true" />

      {/* Logo symbol */}
      <div
        aria-hidden="true"
        style={{
          ...styles.logo,
          opacity: phase !== 'logo' ? 1 : 0,
          transform: phase !== 'logo' ? 'scale(1)' : 'scale(0.8)',
          transition: 'all 1s cubic-bezier(0.16, 1, 0.3, 1)',
        }}
      >
        ◈
      </div>

      {/* Title */}
      <h1
        style={{
          ...styles.title,
          opacity: phase === 'title' || phase === 'tagline' || phase === 'ready' ? 1 : 0,
          transform:
            phase === 'title' || phase === 'tagline' || phase === 'ready'
              ? 'translateY(0)'
              : 'translateY(12px)',
          transition: 'all 0.8s cubic-bezier(0.16, 1, 0.3, 1)',
        }}
      >
        AGENT FRIDAY
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
        Your sovereign AI companion
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

      {/* Keyframes */}
      <style>{`
        @keyframes onb-particle-float {
          0%, 100% { opacity: 0; transform: translateY(0) scale(0.5); }
          20% { opacity: 0.6; }
          80% { opacity: 0.4; }
          50% { transform: translateY(-30px) scale(1); }
        }
        @keyframes onb-glow-pulse {
          0%, 100% { opacity: 0.15; transform: translate(-50%, -50%) scale(1); }
          50% { opacity: 0.25; transform: translate(-50%, -50%) scale(1.1); }
        }
      `}</style>
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
  glow: {
    position: 'absolute',
    top: '50%',
    left: '50%',
    width: 500,
    height: 500,
    borderRadius: '50%',
    background: 'radial-gradient(circle, var(--accent-cyan-10) 0%, transparent 70%)',
    animation: 'onb-glow-pulse 4s ease-in-out infinite',
    pointerEvents: 'none',
  },
  logo: {
    fontSize: 64,
    color: 'var(--accent-cyan)',
    textShadow: '0 0 40px var(--accent-cyan-30), 0 0 80px var(--accent-cyan-10)',
    zIndex: 2,
  },
  title: {
    fontSize: 32,
    fontWeight: 300,
    letterSpacing: '0.3em',
    color: 'var(--text-primary)',
    fontFamily: "'Space Grotesk', sans-serif",
    zIndex: 2,
  },
  tagline: {
    fontSize: 14,
    fontWeight: 400,
    letterSpacing: '0.15em',
    color: 'var(--accent-cyan-50)',
    fontFamily: "'Space Grotesk', sans-serif",
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
