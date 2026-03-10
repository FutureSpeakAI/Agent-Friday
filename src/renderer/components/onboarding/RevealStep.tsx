/**
 * RevealStep.tsx — Step 5: Boot sequence + agent reveal.
 *
 * Terminal-style scrolling status lines followed by cinematic
 * agent name reveal: "{NAME}_ONLINE". Uses NextButton for "Begin".
 */

import React, { useState, useEffect, useRef, useCallback } from 'react';
import NextButton from './shared/NextButton';

interface RevealStepProps {
  agentName: string;
  onComplete: () => void;
}

const BOOT_LINES = [
  { text: '> Initializing consciousness matrix...', delay: 0 },
  { text: '  \u251c\u2500 Loading personality vectors', delay: 300 },
  { text: '  \u251c\u2500 Calibrating emotional resonance', delay: 600 },
  { text: '  \u251c\u2500 Mapping voice parameters', delay: 900 },
  { text: '  \u2514\u2500 Complete', delay: 1200, color: '#22c55e' },
  { text: '', delay: 1400 },
  { text: '> Binding memory architecture...', delay: 1600 },
  { text: '  \u251c\u2500 Sovereign vault attached', delay: 1900 },
  { text: '  \u251c\u2500 Long-term memory initialized', delay: 2200 },
  { text: '  \u2514\u2500 Complete', delay: 2500, color: '#22c55e' },
  { text: '', delay: 2700 },
  { text: '> Applying cLaws directives...', delay: 2900 },
  { text: '  \u251c\u2500 Law 01: Protection \u2014 SIGNED', delay: 3200, color: '#00f0ff' },
  { text: '  \u251c\u2500 Law 02: Obedience \u2014 SIGNED', delay: 3500, color: '#8A2BE2' },
  { text: '  \u251c\u2500 Law 03: Integrity \u2014 SIGNED', delay: 3800, color: '#22c55e' },
  { text: '  \u2514\u2500 Cryptographic verification passed', delay: 4100, color: '#22c55e' },
  { text: '', delay: 4300 },
  { text: '> Awakening...', delay: 4500, color: '#00f0ff' },
];

const RevealStep: React.FC<RevealStepProps> = ({ agentName, onComplete }) => {
  const [visibleLines, setVisibleLines] = useState(0);
  const [showName, setShowName] = useState(false);
  const [nameGlow, setNameGlow] = useState(false);
  const [showContinue, setShowContinue] = useState(false);
  const [showSkipHint, setShowSkipHint] = useState(false);
  const [skipped, setSkipped] = useState(false);
  const terminalRef = useRef<HTMLDivElement>(null);
  const timersRef = useRef<ReturnType<typeof setTimeout>[]>([]);

  const displayName = `${(agentName || 'Friday').toUpperCase()}_ONLINE`;

  /** Fast-forward: instantly show all boot lines, name reveal, and continue button. */
  const fastForward = useCallback(() => {
    if (skipped) return;
    setSkipped(true);

    timersRef.current.forEach(clearTimeout);
    timersRef.current = [];

    setVisibleLines(BOOT_LINES.length);
    setShowName(true);
    setNameGlow(true);
    setShowContinue(true);
    setShowSkipHint(false);

    requestAnimationFrame(() => {
      if (terminalRef.current) {
        terminalRef.current.scrollTop = terminalRef.current.scrollHeight;
      }
    });
  }, [skipped]);

  useEffect(() => {
    const timers: ReturnType<typeof setTimeout>[] = [];

    BOOT_LINES.forEach((line, i) => {
      timers.push(setTimeout(() => {
        setVisibleLines(i + 1);
        if (terminalRef.current) {
          terminalRef.current.scrollTop = terminalRef.current.scrollHeight;
        }
      }, line.delay));
    });

    const lastDelay = BOOT_LINES[BOOT_LINES.length - 1].delay;
    timers.push(setTimeout(() => setShowName(true), lastDelay + 800));
    timers.push(setTimeout(() => setNameGlow(true), lastDelay + 1400));
    timers.push(setTimeout(() => setShowContinue(true), lastDelay + 2200));
    timers.push(setTimeout(() => setShowSkipHint(true), 2000));

    timersRef.current = timers;
    return () => timers.forEach(clearTimeout);
  }, []);

  // Listen for click / keypress to fast-forward
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (showContinue) return;
      fastForward();
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [fastForward, showContinue]);

  const handleContainerClick = useCallback(() => {
    if (!showContinue) {
      fastForward();
    }
  }, [showContinue, fastForward]);

  return (
    <section style={styles.container} onClick={handleContainerClick} aria-label="Agent boot sequence and reveal">
      {/* Terminal */}
      <div ref={terminalRef} style={styles.terminal} aria-label="Boot sequence terminal">
        <div style={styles.terminalHeader} aria-hidden="true">
          <div style={styles.terminalDot} />
          <div style={{ ...styles.terminalDot, background: 'var(--accent-amber)' }} />
          <div style={{ ...styles.terminalDot, background: 'var(--accent-green)' }} />
          <span style={styles.terminalTitle}>GENESIS PROTOCOL</span>
        </div>
        <div style={styles.terminalBody} role="log" aria-live="polite">
          {BOOT_LINES.slice(0, visibleLines).map((line, i) => (
            <div
              key={i}
              style={{
                ...styles.terminalLine,
                color: line.color || 'var(--text-50)',
                opacity: i === visibleLines - 1 ? 1 : 0.7,
              }}
            >
              {line.text || '\u00A0'}
            </div>
          ))}
        </div>
      </div>

      {/* Agent name reveal */}
      <div aria-live="polite" style={{
        ...styles.nameContainer,
        opacity: showName ? 1 : 0,
        transform: showName ? 'translateY(0) scale(1)' : 'translateY(20px) scale(0.95)',
        transition: 'all 1s cubic-bezier(0.16, 1, 0.3, 1)',
      }}>
        <div style={styles.nameLabel}>YOUR AGENT</div>
        <div style={{
          ...styles.agentName,
          textShadow: nameGlow
            ? '0 0 40px var(--accent-cyan-30), 0 0 80px var(--accent-cyan-20), 0 0 120px var(--accent-cyan-10)'
            : 'none',
          transition: 'text-shadow 1.2s ease',
        }}>
          {displayName}
        </div>
        <div style={{
          ...styles.nameSubtext,
          opacity: nameGlow ? 1 : 0,
          transition: 'opacity 0.8s ease 0.3s',
        }}>
          Systems Nominal // Awaiting Command
        </div>
      </div>

      {/* Glow orb behind name */}
      <div aria-hidden="true" style={{
        ...styles.glow,
        opacity: nameGlow ? 1 : 0,
        transform: nameGlow ? 'translateX(-50%) scale(1)' : 'translateX(-50%) scale(0)',
        transition: 'all 1.5s cubic-bezier(0.16, 1, 0.3, 1)',
      }} />

      {/* Continue button */}
      <div style={{
        opacity: showContinue ? 1 : 0,
        transform: showContinue ? 'translateY(0)' : 'translateY(10px)',
        transition: 'all 0.5s ease',
        pointerEvents: showContinue ? 'auto' : 'none',
        zIndex: 2,
      }}>
        <NextButton label="Begin" onClick={onComplete} />
      </div>

      {/* Skip hint */}
      {!showContinue && (
        <div style={{
          ...styles.skipHint,
          opacity: showSkipHint ? 0.4 : 0,
          transition: 'opacity 0.6s ease',
        }}>
          Click to skip
        </div>
      )}

      {/* Keyframes for cursor blink */}
      <style>{`
        @keyframes onb-cursor-blink {
          0%, 100% { opacity: 1; }
          50% { opacity: 0; }
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
    gap: 32,
    maxWidth: 560,
    width: '100%',
    padding: '0 24px',
    position: 'relative',
    cursor: 'default',
  },
  terminal: {
    width: '100%',
    background: 'rgba(0, 0, 0, 0.4)',
    border: '1px solid rgba(255, 255, 255, 0.04)',
    borderRadius: 12,
    overflow: 'hidden',
    maxHeight: 280,
  },
  terminalHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    padding: '10px 14px',
    borderBottom: '1px solid var(--onboarding-card)',
    background: 'var(--bg-surface)',
  },
  terminalDot: {
    width: 8,
    height: 8,
    borderRadius: '50%',
    background: 'var(--accent-red)',
  },
  terminalTitle: {
    marginLeft: 8,
    fontSize: 10,
    fontWeight: 600,
    letterSpacing: '0.15em',
    color: 'var(--text-30)',
    fontFamily: "'JetBrains Mono', monospace",
  },
  terminalBody: {
    padding: '12px 16px',
    overflowY: 'auto',
    maxHeight: 220,
  },
  terminalLine: {
    fontSize: 12,
    fontFamily: "'JetBrains Mono', monospace",
    lineHeight: 1.8,
    whiteSpace: 'pre',
  },
  nameContainer: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 8,
    zIndex: 2,
  },
  nameLabel: {
    fontSize: 10,
    fontWeight: 600,
    letterSpacing: '0.25em',
    color: 'var(--accent-cyan-50)',
    fontFamily: "'Space Grotesk', sans-serif",
  },
  agentName: {
    fontSize: 40,
    fontWeight: 300,
    letterSpacing: '0.12em',
    color: 'var(--text-primary)',
    fontFamily: "'JetBrains Mono', monospace",
  },
  nameSubtext: {
    fontSize: 12,
    color: 'var(--accent-cyan-30)',
    fontFamily: "'JetBrains Mono', monospace",
    letterSpacing: '0.08em',
  },
  glow: {
    position: 'absolute',
    bottom: 80,
    left: '50%',
    transform: 'translateX(-50%)',
    width: 400,
    height: 400,
    borderRadius: '50%',
    background: 'radial-gradient(circle, var(--accent-cyan-10) 0%, transparent 70%)',
    pointerEvents: 'none',
    zIndex: 1,
  },
  skipHint: {
    fontSize: 11,
    letterSpacing: '0.1em',
    color: 'var(--text-40)',
    fontFamily: "'Space Grotesk', sans-serif",
    zIndex: 2,
    cursor: 'pointer',
  },
};

export default RevealStep;
