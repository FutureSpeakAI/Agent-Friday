/**
 * PrivacyStep.tsx — Step 3: Privacy Shield explainer.
 *
 * "Privacy Shield." — Explains how cloud API requests are filtered
 * to protect user privacy without reducing functionality.
 */

import React, { useState, useEffect } from 'react';
import { Shield, ArrowRight, Cloud, User, Lock } from 'lucide-react';
import NextButton from './shared/NextButton';

interface PrivacyStepProps {
  onComplete: () => void;
  onBack?: () => void;
}

const PII_CATEGORIES = [
  'Names',
  'Emails',
  'Phone Numbers',
  'API Keys & Secrets',
  'Credit Cards',
  'SSN / ID Numbers',
  'IP Addresses',
  'File Paths',
];

const PrivacyStep: React.FC<PrivacyStepProps> = ({ onComplete }) => {
  const [fadeIn, setFadeIn] = useState(false);
  const [showFlow, setShowFlow] = useState(false);
  const [showBadges, setShowBadges] = useState(false);
  const [showButton, setShowButton] = useState(false);

  useEffect(() => {
    const timers = [
      setTimeout(() => setFadeIn(true), 100),
      setTimeout(() => setShowFlow(true), 600),
      setTimeout(() => setShowBadges(true), 1200),
      setTimeout(() => setShowButton(true), 1800),
    ];
    return () => timers.forEach(clearTimeout);
  }, []);

  return (
    <section style={{
      ...styles.container,
      opacity: fadeIn ? 1 : 0,
      transform: fadeIn ? 'translateY(0)' : 'translateY(16px)',
      transition: 'all 0.7s cubic-bezier(0.16, 1, 0.3, 1)',
    }} aria-label="Privacy Shield explanation">
      {/* Header */}
      <div style={styles.headerBlock}>
        <h2 style={styles.heading}>Privacy Shield.</h2>
        <p style={styles.subtitle}>
          When your request needs cloud AI, we protect you automatically.
          Your personal information never reaches external servers.
        </p>
      </div>

      {/* Flow diagram */}
      <div style={{
        ...styles.flowCard,
        opacity: showFlow ? 1 : 0,
        transform: showFlow ? 'translateY(0)' : 'translateY(12px)',
        transition: 'all 0.6s cubic-bezier(0.16, 1, 0.3, 1)',
      }}>
        <div style={styles.flowRow}>
          {/* Your Message */}
          <div style={styles.flowNode}>
            <div style={{ ...styles.flowIcon, background: 'rgba(0, 240, 255, 0.08)', border: '1px solid rgba(0, 240, 255, 0.15)' }}>
              <User size={16} color="#00f0ff" />
            </div>
            <span style={styles.flowLabel}>Your Message</span>
          </div>

          <ArrowRight size={14} color="var(--text-20)" style={{ flexShrink: 0 }} />

          {/* Privacy Shield - Scrub */}
          <div style={styles.flowNode}>
            <div style={{ ...styles.flowIcon, background: 'rgba(34, 197, 94, 0.08)', border: '1px solid rgba(34, 197, 94, 0.15)' }}>
              <Shield size={16} color="#22c55e" />
            </div>
            <span style={styles.flowLabel}>Scrub PII</span>
          </div>

          <ArrowRight size={14} color="var(--text-20)" style={{ flexShrink: 0 }} />

          {/* Cloud AI */}
          <div style={styles.flowNode}>
            <div style={{ ...styles.flowIcon, background: 'rgba(138, 43, 226, 0.08)', border: '1px solid rgba(138, 43, 226, 0.15)' }}>
              <Cloud size={16} color="#8A2BE2" />
            </div>
            <span style={styles.flowLabel}>Cloud AI</span>
          </div>

          <ArrowRight size={14} color="var(--text-20)" style={{ flexShrink: 0 }} />

          {/* Privacy Shield - Restore */}
          <div style={styles.flowNode}>
            <div style={{ ...styles.flowIcon, background: 'rgba(34, 197, 94, 0.08)', border: '1px solid rgba(34, 197, 94, 0.15)' }}>
              <Lock size={16} color="#22c55e" />
            </div>
            <span style={styles.flowLabel}>Restore</span>
          </div>
        </div>
      </div>

      {/* PII categories */}
      <div style={{
        ...styles.badgeSection,
        opacity: showBadges ? 1 : 0,
        transform: showBadges ? 'translateY(0)' : 'translateY(12px)',
        transition: 'all 0.6s cubic-bezier(0.16, 1, 0.3, 1)',
      }}>
        <span style={styles.badgeTitle}>What gets filtered</span>
        <div style={styles.badgeGrid}>
          {PII_CATEGORIES.map((cat) => (
            <span key={cat} style={styles.piiBadge}>{cat}</span>
          ))}
        </div>
      </div>

      {/* Reassurance */}
      <div style={{
        ...styles.reassurance,
        opacity: showBadges ? 1 : 0,
        transition: 'opacity 0.5s ease 0.3s',
      }}>
        <p style={styles.reassuranceText}>
          This doesn't reduce functionality — your AI gets the full context it needs,
          with personal details replaced by safe placeholders that are restored in the response.
        </p>
        <p style={styles.localNote}>
          Local models bypass this entirely — no filtering needed when data never leaves your machine.
        </p>
      </div>

      {/* Continue */}
      <div style={{
        opacity: showButton ? 1 : 0,
        transform: showButton ? 'translateY(0)' : 'translateY(10px)',
        transition: 'all 0.5s ease',
        pointerEvents: showButton ? 'auto' : 'none',
      }}>
        <NextButton label="Continue" onClick={onComplete} />
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
  flowCard: {
    width: '100%',
    background: 'var(--onboarding-card)',
    border: '1px solid rgba(255, 255, 255, 0.04)',
    borderRadius: 12,
    padding: '24px 20px',
  },
  flowRow: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 10,
    flexWrap: 'wrap',
  },
  flowNode: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 6,
  },
  flowIcon: {
    width: 36,
    height: 36,
    borderRadius: 8,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
  },
  flowLabel: {
    fontSize: 10,
    fontWeight: 500,
    color: 'var(--text-40)',
    fontFamily: "'Space Grotesk', sans-serif",
    letterSpacing: '0.05em',
  },
  badgeSection: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 10,
  },
  badgeTitle: {
    fontSize: 10,
    fontWeight: 600,
    letterSpacing: '0.15em',
    color: 'var(--text-30)',
    fontFamily: "'Space Grotesk', sans-serif",
    textTransform: 'uppercase',
  },
  badgeGrid: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: 6,
    justifyContent: 'center',
    maxWidth: 420,
  },
  piiBadge: {
    fontSize: 10,
    fontWeight: 500,
    color: 'var(--accent-cyan-70)',
    padding: '4px 12px',
    borderRadius: 16,
    background: 'var(--accent-cyan-10)',
    border: '1px solid var(--accent-cyan-20)',
    fontFamily: "'Space Grotesk', sans-serif",
  },
  reassurance: {
    textAlign: 'center',
    maxWidth: 460,
  },
  reassuranceText: {
    fontSize: 12,
    color: 'var(--text-40)',
    lineHeight: 1.6,
    margin: '0 0 8px 0',
    fontFamily: "'Inter', sans-serif",
  },
  localNote: {
    fontSize: 11,
    color: 'rgba(34, 197, 94, 0.6)',
    lineHeight: 1.5,
    margin: 0,
    fontFamily: "'Inter', sans-serif",
    fontStyle: 'italic',
  },
};

export default PrivacyStep;
