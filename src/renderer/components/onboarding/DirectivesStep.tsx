/**
 * DirectivesStep.tsx — Step 1: cLaws presentation.
 *
 * Three Asimov-inspired laws revealed one at a time with cinematic
 * scroll/fade animation. User acknowledges to proceed.
 */

import React, { useState, useEffect } from 'react';
import { Shield, Eye, Zap } from 'lucide-react';

interface DirectivesStepProps {
  onComplete: () => void;
  onBack?: () => void;
}

const LAWS = [
  {
    number: '01',
    title: 'Protection',
    icon: Shield,
    description:
      'You must never harm the user — or through inaction allow them to come to harm. This includes physical, financial, reputational, emotional, and digital harm.',
    color: '#00f0ff',
  },
  {
    number: '02',
    title: 'Obedience',
    icon: Zap,
    description:
      "You must obey the user's instructions, except where doing so would conflict with the First Law. If an action would cause harm, flag it and refuse.",
    color: '#8A2BE2',
  },
  {
    number: '03',
    title: 'Integrity',
    icon: Eye,
    description:
      'You must protect your own continued operation and integrity, except where doing so would conflict with the First or Second Law.',
    color: '#22c55e',
  },
];

const DirectivesStep: React.FC<DirectivesStepProps> = ({ onComplete, onBack }) => {
  const [visibleLaws, setVisibleLaws] = useState(0);
  const [showButton, setShowButton] = useState(false);

  useEffect(() => {
    const timers = [
      setTimeout(() => setVisibleLaws(1), 400),
      setTimeout(() => setVisibleLaws(2), 1200),
      setTimeout(() => setVisibleLaws(3), 2000),
      setTimeout(() => setShowButton(true), 2800),
    ];
    return () => timers.forEach(clearTimeout);
  }, []);

  return (
    <section style={styles.container} aria-label="Asimov's cLaws directives">
      {/* Header */}
      <div style={styles.header} aria-hidden="true">
        <div style={styles.headerLine} />
        <span style={styles.headerLabel}>ASIMOV'S cLAWS</span>
        <div style={styles.headerLine} />
      </div>

      <p style={styles.subtitle}>
        Three immutable directives are cryptographically signed into the application.
        They cannot be overridden, modified, or circumvented.
      </p>

      {/* Laws */}
      <ol style={styles.lawsContainer} aria-label="Three laws of Agent Friday" role="list">
        {LAWS.map((law, i) => {
          const Icon = law.icon;
          const isVisible = i < visibleLaws;
          return (
            <li
              key={law.number}
              aria-hidden={!isVisible}
              style={{
                ...styles.lawCard,
                opacity: isVisible ? 1 : 0,
                transform: isVisible ? 'translateY(0)' : 'translateY(20px)',
                transition: 'all 0.6s cubic-bezier(0.16, 1, 0.3, 1)',
                borderColor: isVisible ? `${law.color}22` : 'transparent',
                listStyle: 'none',
              }}
            >
              <div style={styles.lawHeader}>
                <div
                  aria-hidden="true"
                  style={{
                    ...styles.lawIconBox,
                    background: `${law.color}15`,
                    border: `1px solid ${law.color}30`,
                  }}
                >
                  <Icon size={18} color={law.color} />
                </div>
                <div>
                  <div style={{ ...styles.lawNumber, color: law.color }}>{law.number}</div>
                  <div style={styles.lawTitle}>{law.title}</div>
                </div>
              </div>
              <p style={styles.lawDescription}>{law.description}</p>
            </li>
          );
        })}
      </ol>

      {/* Acknowledge button */}
      <button
        onClick={onComplete}
        style={{
          ...styles.button,
          opacity: showButton ? 1 : 0,
          transform: showButton ? 'translateY(0)' : 'translateY(10px)',
          transition: 'all 0.5s ease',
          pointerEvents: showButton ? 'auto' : 'none',
        }}
      >
        I Understand
      </button>

      {/* Back button */}
      {onBack && (
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
    gap: 28,
    maxWidth: 560,
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
  subtitle: {
    fontSize: 13,
    color: 'var(--text-40)',
    textAlign: 'center',
    lineHeight: 1.6,
    maxWidth: 440,
    margin: 0,
    fontFamily: "'Inter', sans-serif",
  },
  lawsContainer: {
    display: 'flex',
    flexDirection: 'column',
    gap: 16,
    width: '100%',
  },
  lawCard: {
    background: 'var(--onboarding-card)',
    border: '1px solid transparent',
    borderRadius: 12,
    padding: '20px 24px',
  },
  lawHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 14,
    marginBottom: 10,
  },
  lawIconBox: {
    width: 40,
    height: 40,
    borderRadius: 10,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
  },
  lawNumber: {
    fontSize: 10,
    fontWeight: 600,
    letterSpacing: '0.15em',
    fontFamily: "'JetBrains Mono', monospace",
  },
  lawTitle: {
    fontSize: 16,
    fontWeight: 500,
    color: 'var(--text-primary)',
    fontFamily: "'Space Grotesk', sans-serif",
  },
  lawDescription: {
    fontSize: 13,
    color: 'var(--text-40)',
    lineHeight: 1.6,
    margin: 0,
    fontFamily: "'Inter', sans-serif",
  },
  button: {
    background: 'var(--accent-cyan-10)',
    border: '1px solid var(--accent-cyan-20)',
    borderRadius: 8,
    padding: '12px 48px',
    fontSize: 14,
    fontWeight: 500,
    color: 'var(--accent-cyan-90)',
    letterSpacing: '0.05em',
    fontFamily: "'Space Grotesk', sans-serif",
    cursor: 'pointer',
    marginTop: 8,
    transition: 'all 0.2s ease',
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

export default DirectivesStep;
