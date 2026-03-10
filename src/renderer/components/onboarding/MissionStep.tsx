/**
 * MissionStep.tsx — Step 1: Trust pillars presentation.
 *
 * "Your AI. Your Terms." — Five trust pillars explaining why this is
 * the most trustworthy AI system in the world. Staggered card reveal.
 */

import React, { useState, useEffect } from 'react';
import { Cpu, Lock, Shield, Eye, Fingerprint } from 'lucide-react';
import NextButton from './shared/NextButton';

interface MissionStepProps {
  onComplete: () => void;
  onBack?: () => void;
}

const PILLARS = [
  {
    number: '01',
    title: 'Local-First Intelligence',
    icon: Cpu,
    description:
      'AI models run directly on your hardware. Your data never leaves your machine unless you explicitly allow it.',
    color: '#00f0ff',
  },
  {
    number: '02',
    title: 'Zero-Knowledge Vault',
    icon: Lock,
    description:
      'Your sensitive data is encrypted with keys only you hold. We cannot access it — even if we wanted to.',
    color: '#8A2BE2',
  },
  {
    number: '03',
    title: 'Privacy Shield',
    icon: Shield,
    description:
      'When cloud AI is used, all personal information is automatically scrubbed before transmission and restored in responses.',
    color: '#22c55e',
  },
  {
    number: '04',
    title: 'Transparent Routing',
    icon: Eye,
    description:
      'You always know which AI is handling your request — local or cloud — and you control the policy.',
    color: '#f59e0b',
  },
  {
    number: '05',
    title: 'Immutable Directives',
    icon: Fingerprint,
    description:
      'Core safety laws are cryptographically bound to the application core. They cannot be overridden, modified, or circumvented.',
    color: '#ef4444',
  },
];

const MissionStep: React.FC<MissionStepProps> = ({ onComplete }) => {
  const [visiblePillars, setVisiblePillars] = useState(0);
  const [showButton, setShowButton] = useState(false);

  useEffect(() => {
    const timers = [
      setTimeout(() => setVisiblePillars(1), 300),
      setTimeout(() => setVisiblePillars(2), 700),
      setTimeout(() => setVisiblePillars(3), 1100),
      setTimeout(() => setVisiblePillars(4), 1500),
      setTimeout(() => setVisiblePillars(5), 1900),
      setTimeout(() => setShowButton(true), 2500),
    ];
    return () => timers.forEach(clearTimeout);
  }, []);

  return (
    <section style={styles.container} aria-label="Mission — Trust pillars">
      {/* Header */}
      <div style={styles.headerBlock}>
        <h2 style={styles.heading}>Your AI. Your Terms.</h2>
        <p style={styles.subtitle}>
          Built from the ground up to be the most trustworthy AI system in the world.
          Here's how we earn that trust.
        </p>
      </div>

      {/* Pillar cards */}
      <div style={styles.pillarList} role="list" aria-label="Five trust pillars">
        {PILLARS.map((pillar, i) => {
          const Icon = pillar.icon;
          const isVisible = i < visiblePillars;
          return (
            <div
              key={pillar.number}
              role="listitem"
              aria-hidden={!isVisible}
              style={{
                ...styles.pillarCard,
                borderTopColor: isVisible ? pillar.color : 'transparent',
                opacity: isVisible ? 1 : 0,
                transform: isVisible ? 'translateY(0)' : 'translateY(16px)',
                transition: 'all 0.5s cubic-bezier(0.16, 1, 0.3, 1)',
              }}
            >
              <div style={styles.pillarHeader}>
                <div
                  aria-hidden="true"
                  style={{
                    ...styles.pillarIconBox,
                    background: `${pillar.color}12`,
                    border: `1px solid ${pillar.color}25`,
                  }}
                >
                  <Icon size={16} color={pillar.color} />
                </div>
                <div>
                  <div style={{ ...styles.pillarNumber, color: pillar.color }}>{pillar.number}</div>
                  <div style={styles.pillarTitle}>{pillar.title}</div>
                </div>
              </div>
              <p style={styles.pillarDescription}>{pillar.description}</p>
            </div>
          );
        })}
      </div>

      {/* Continue button */}
      <div style={{
        opacity: showButton ? 1 : 0,
        transform: showButton ? 'translateY(0)' : 'translateY(10px)',
        transition: 'all 0.5s ease',
        pointerEvents: showButton ? 'auto' : 'none',
      }}>
        <NextButton label="I'm In" onClick={onComplete} />
      </div>
    </section>
  );
};

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 24,
    maxWidth: 600,
    width: '100%',
    padding: '0 24px',
  },
  headerBlock: {
    textAlign: 'center',
    maxWidth: 500,
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
  pillarList: {
    display: 'flex',
    flexDirection: 'column',
    gap: 10,
    width: '100%',
    maxHeight: 380,
    overflowY: 'auto',
    paddingRight: 4,
  },
  pillarCard: {
    background: 'var(--onboarding-card)',
    borderTop: '3px solid transparent',
    border: '1px solid rgba(255, 255, 255, 0.04)',
    borderRadius: 10,
    padding: '14px 18px',
  },
  pillarHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    marginBottom: 6,
  },
  pillarIconBox: {
    width: 34,
    height: 34,
    borderRadius: 8,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
  },
  pillarNumber: {
    fontSize: 9,
    fontWeight: 600,
    letterSpacing: '0.15em',
    fontFamily: "'JetBrains Mono', monospace",
  },
  pillarTitle: {
    fontSize: 14,
    fontWeight: 500,
    color: 'var(--text-primary)',
    fontFamily: "'Space Grotesk', sans-serif",
  },
  pillarDescription: {
    fontSize: 12,
    color: 'var(--text-40)',
    lineHeight: 1.5,
    margin: 0,
    fontFamily: "'Inter', sans-serif",
  },
};

export default MissionStep;
