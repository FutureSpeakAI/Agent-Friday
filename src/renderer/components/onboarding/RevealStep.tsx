/**
 * RevealStep.tsx — Step 6: Boot sequence + agent reveal.
 *
 * Terminal-style scrolling status lines followed by a cinematic
 * agent name reveal with glow effect. Calls onComplete when done.
 */

import React, { useState, useEffect, useRef } from 'react';

interface RevealStepProps {
  agentName: string;
  onComplete: () => void;
}

const BOOT_LINES = [
  { text: '> Initializing consciousness matrix...', delay: 0 },
  { text: '  ├─ Loading personality vectors', delay: 300 },
  { text: '  ├─ Calibrating emotional resonance', delay: 600 },
  { text: '  ├─ Mapping voice parameters', delay: 900 },
  { text: '  └─ Complete', delay: 1200, color: '#22c55e' },
  { text: '', delay: 1400 },
  { text: '> Binding memory architecture...', delay: 1600 },
  { text: '  ├─ Sovereign vault attached', delay: 1900 },
  { text: '  ├─ Long-term memory initialized', delay: 2200 },
  { text: '  └─ Complete', delay: 2500, color: '#22c55e' },
  { text: '', delay: 2700 },
  { text: '> Applying cLaws directives...', delay: 2900 },
  { text: '  ├─ Law 01: Protection — SIGNED', delay: 3200, color: '#00f0ff' },
  { text: '  ├─ Law 02: Obedience — SIGNED', delay: 3500, color: '#8A2BE2' },
  { text: '  ├─ Law 03: Integrity — SIGNED', delay: 3800, color: '#22c55e' },
  { text: '  └─ Cryptographic verification passed', delay: 4100, color: '#22c55e' },
  { text: '', delay: 4300 },
  { text: '> Awakening...', delay: 4500, color: '#00f0ff' },
];

const RevealStep: React.FC<RevealStepProps> = ({ agentName, onComplete }) => {
  const [visibleLines, setVisibleLines] = useState(0);
  const [showName, setShowName] = useState(false);
  const [nameGlow, setNameGlow] = useState(false);
  const [showContinue, setShowContinue] = useState(false);
  const terminalRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const timers: ReturnType<typeof setTimeout>[] = [];

    // Reveal boot lines one by one
    BOOT_LINES.forEach((line, i) => {
      timers.push(setTimeout(() => {
        setVisibleLines(i + 1);
        // Auto-scroll terminal
        if (terminalRef.current) {
          terminalRef.current.scrollTop = terminalRef.current.scrollHeight;
        }
      }, line.delay));
    });

    // After last boot line, show the name
    const lastDelay = BOOT_LINES[BOOT_LINES.length - 1].delay;
    timers.push(setTimeout(() => setShowName(true), lastDelay + 800));
    timers.push(setTimeout(() => setNameGlow(true), lastDelay + 1400));
    timers.push(setTimeout(() => setShowContinue(true), lastDelay + 2200));

    return () => timers.forEach(clearTimeout);
  }, []);

  return (
    <div style={styles.container}>
      {/* Terminal */}
      <div ref={terminalRef} style={styles.terminal}>
        <div style={styles.terminalHeader}>
          <div style={styles.terminalDot} />
          <div style={{ ...styles.terminalDot, background: '#f59e0b' }} />
          <div style={{ ...styles.terminalDot, background: '#22c55e' }} />
          <span style={styles.terminalTitle}>GENESIS PROTOCOL</span>
        </div>
        <div style={styles.terminalBody}>
          {BOOT_LINES.slice(0, visibleLines).map((line, i) => (
            <div
              key={i}
              style={{
                ...styles.terminalLine,
                color: line.color || 'rgba(255, 255, 255, 0.5)',
                opacity: i === visibleLines - 1 ? 1 : 0.7,
              }}
            >
              {line.text || '\u00A0'}
            </div>
          ))}
        </div>
      </div>

      {/* Agent name reveal */}
      <div style={{
        ...styles.nameContainer,
        opacity: showName ? 1 : 0,
        transform: showName ? 'translateY(0) scale(1)' : 'translateY(20px) scale(0.95)',
        transition: 'all 1s cubic-bezier(0.16, 1, 0.3, 1)',
      }}>
        <div style={styles.nameLabel}>YOUR AGENT</div>
        <div style={{
          ...styles.agentName,
          textShadow: nameGlow
            ? '0 0 40px rgba(0, 240, 255, 0.4), 0 0 80px rgba(0, 240, 255, 0.15), 0 0 120px rgba(0, 240, 255, 0.05)'
            : 'none',
          transition: 'text-shadow 1.2s ease',
        }}>
          {agentName || 'Friday'}
        </div>
        <div style={{
          ...styles.nameSubtext,
          opacity: nameGlow ? 1 : 0,
          transition: 'opacity 0.8s ease 0.3s',
        }}>
          Ready for first contact
        </div>
      </div>

      {/* Glow orb behind name */}
      <div style={{
        ...styles.glow,
        opacity: nameGlow ? 1 : 0,
        transition: 'opacity 1.5s ease',
      }} />

      {/* Continue button */}
      <button
        onClick={onComplete}
        style={{
          ...styles.button,
          opacity: showContinue ? 1 : 0,
          transform: showContinue ? 'translateY(0)' : 'translateY(10px)',
          transition: 'all 0.5s ease',
          pointerEvents: showContinue ? 'auto' : 'none',
        }}
      >
        Begin
      </button>

      {/* Keyframes for cursor blink */}
      <style>{`
        @keyframes onb-cursor-blink {
          0%, 100% { opacity: 1; }
          50% { opacity: 0; }
        }
      `}</style>
    </div>
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
  },
  terminal: {
    width: '100%',
    background: 'rgba(0, 0, 0, 0.4)',
    border: '1px solid rgba(255, 255, 255, 0.06)',
    borderRadius: 12,
    overflow: 'hidden',
    maxHeight: 280,
  },
  terminalHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    padding: '10px 14px',
    borderBottom: '1px solid rgba(255, 255, 255, 0.04)',
    background: 'rgba(255, 255, 255, 0.02)',
  },
  terminalDot: {
    width: 8,
    height: 8,
    borderRadius: '50%',
    background: '#ef4444',
  },
  terminalTitle: {
    marginLeft: 8,
    fontSize: 10,
    fontWeight: 600,
    letterSpacing: '0.15em',
    color: 'rgba(255, 255, 255, 0.3)',
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
    color: 'rgba(0, 240, 255, 0.5)',
    fontFamily: "'Space Grotesk', sans-serif",
  },
  agentName: {
    fontSize: 48,
    fontWeight: 300,
    letterSpacing: '0.15em',
    color: '#F8FAFC',
    fontFamily: "'Space Grotesk', sans-serif",
  },
  nameSubtext: {
    fontSize: 13,
    color: 'rgba(0, 240, 255, 0.4)',
    fontFamily: "'Space Grotesk', sans-serif",
    letterSpacing: '0.1em',
  },
  glow: {
    position: 'absolute',
    bottom: 80,
    left: '50%',
    transform: 'translateX(-50%)',
    width: 400,
    height: 400,
    borderRadius: '50%',
    background: 'radial-gradient(circle, rgba(0, 240, 255, 0.08) 0%, transparent 70%)',
    pointerEvents: 'none',
    zIndex: 1,
  },
  button: {
    background: 'rgba(0, 240, 255, 0.08)',
    border: '1px solid rgba(0, 240, 255, 0.25)',
    borderRadius: 8,
    padding: '14px 64px',
    fontSize: 16,
    fontWeight: 500,
    color: 'rgba(0, 240, 255, 0.9)',
    letterSpacing: '0.1em',
    fontFamily: "'Space Grotesk', sans-serif",
    cursor: 'pointer',
    transition: 'all 0.2s ease',
    zIndex: 2,
  },
};

export default RevealStep;
