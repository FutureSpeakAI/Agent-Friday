/**
 * IdentityStep.tsx — Step 4: Agent identity configuration.
 *
 * Visual UI for naming the agent, selecting voice gender, and voice feel.
 * All choices stored in parent's IdentityChoices state.
 */

import React, { useState, useEffect } from 'react';
import { User, Mic } from 'lucide-react';
import type { IdentityChoices } from '../OnboardingWizard';

interface IdentityStepProps {
  choices: IdentityChoices;
  onChange: (choices: IdentityChoices) => void;
  onComplete: () => void;
  onBack?: () => void;
}

const GENDERS: { value: IdentityChoices['gender']; label: string; desc: string }[] = [
  { value: 'male', label: 'Male', desc: 'Masculine voice' },
  { value: 'female', label: 'Female', desc: 'Feminine voice' },
  { value: 'neutral', label: 'Neutral', desc: 'Androgynous voice' },
];

const VOICE_FEELS: { value: IdentityChoices['voiceFeel']; label: string; desc: string; color: string }[] = [
  { value: 'warm', label: 'Warm', desc: 'Friendly, approachable', color: '#f59e0b' },
  { value: 'sharp', label: 'Sharp', desc: 'Precise, articulate', color: '#8A2BE2' },
  { value: 'deep', label: 'Deep', desc: 'Rich, resonant', color: '#3b82f6' },
  { value: 'soft', label: 'Soft', desc: 'Calm, gentle', color: '#22c55e' },
  { value: 'bright', label: 'Bright', desc: 'Energetic, clear', color: '#00f0ff' },
];

const IdentityStep: React.FC<IdentityStepProps> = ({ choices, onChange, onComplete, onBack }) => {
  const [fadeIn, setFadeIn] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => setFadeIn(true), 100);
    return () => clearTimeout(t);
  }, []);

  const updateField = <K extends keyof IdentityChoices>(key: K, value: IdentityChoices[K]) => {
    onChange({ ...choices, [key]: value });
  };

  const canContinue = choices.agentName.trim().length > 0;

  return (
    <section style={{
      ...styles.container,
      opacity: fadeIn ? 1 : 0,
      transform: fadeIn ? 'translateY(0)' : 'translateY(16px)',
      transition: 'all 0.7s cubic-bezier(0.16, 1, 0.3, 1)',
    }} aria-label="Agent identity configuration">
      <div style={styles.header} aria-hidden="true">
        <div style={styles.headerLine} />
        <span style={styles.headerLabel}>AGENT IDENTITY</span>
        <div style={styles.headerLine} />
      </div>

      {/* Icon */}
      <div style={styles.iconWrap} aria-hidden="true">
        <User size={36} color="#00f0ff" />
      </div>

      {/* Agent Name */}
      <div style={styles.section}>
        <label htmlFor="agent-name" style={styles.sectionLabel}>Agent Name</label>
        <p id="agent-name-hint" style={styles.sectionHint}>What should your AI companion be called?</p>
        <input
          id="agent-name"
          type="text"
          value={choices.agentName}
          onChange={(e) => updateField('agentName', e.target.value)}
          placeholder="Friday"
          aria-describedby="agent-name-hint"
          aria-required
          autoFocus
          maxLength={24}
          style={styles.nameInput}
        />
        <div style={styles.nameSuggestions}>
          {['Friday', 'Atlas', 'Nova', 'Echo'].map((name) => (
            <button
              key={name}
              onClick={() => updateField('agentName', name)}
              style={{
                ...styles.suggestionChip,
                borderColor: choices.agentName === name ? 'var(--accent-cyan-30)' : 'var(--onboarding-border)',
                color: choices.agentName === name ? 'var(--accent-cyan-90)' : 'var(--text-40)',
                background: choices.agentName === name ? 'var(--accent-cyan-10)' : 'transparent',
              }}
            >
              {name}
            </button>
          ))}
        </div>
      </div>

      {/* Voice Gender */}
      <div style={styles.section}>
        <label id="voice-gender-label" style={styles.sectionLabel}>Voice Gender</label>
        <div style={styles.optionRow} role="radiogroup" aria-labelledby="voice-gender-label">
          {GENDERS.map((g) => (
            <button
              key={g.value}
              onClick={() => updateField('gender', g.value)}
              role="radio"
              aria-checked={choices.gender === g.value}
              style={{
                ...styles.optionButton,
                borderColor: choices.gender === g.value ? 'var(--accent-cyan-30)' : 'var(--onboarding-border)',
                background: choices.gender === g.value ? 'var(--accent-cyan-10)' : 'var(--bg-surface)',
              }}
            >
              <span style={{
                ...styles.optionLabel,
                color: choices.gender === g.value ? 'var(--accent-cyan-90)' : 'var(--text-60)',
              }}>
                {g.label}
              </span>
              <span style={styles.optionDesc}>{g.desc}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Voice Feel */}
      <div style={styles.section}>
        <div style={styles.sectionLabelRow}>
          <Mic size={14} color="var(--text-40)" aria-hidden="true" />
          <label id="voice-feel-label" style={styles.sectionLabel}>Voice Feel</label>
        </div>
        <div style={styles.feelGrid} role="radiogroup" aria-labelledby="voice-feel-label">
          {VOICE_FEELS.map((vf) => (
            <button
              key={vf.value}
              onClick={() => updateField('voiceFeel', vf.value)}
              role="radio"
              aria-checked={choices.voiceFeel === vf.value}
              style={{
                ...styles.feelButton,
                borderColor: choices.voiceFeel === vf.value ? `${vf.color}66` : 'rgba(255,255,255,0.06)',
                background: choices.voiceFeel === vf.value ? `${vf.color}15` : 'rgba(255,255,255,0.02)',
              }}
            >
              <div aria-hidden="true" style={{
                ...styles.feelDot,
                background: vf.color,
                boxShadow: choices.voiceFeel === vf.value ? `0 0 8px ${vf.color}80` : 'none',
              }} />
              <div>
                <span style={{
                  ...styles.feelLabel,
                  color: choices.voiceFeel === vf.value ? 'var(--text-primary)' : 'var(--text-60)',
                }}>
                  {vf.label}
                </span>
                <span style={styles.feelDesc}>{vf.desc}</span>
              </div>
            </button>
          ))}
        </div>
      </div>

      <button
        onClick={onComplete}
        disabled={!canContinue}
        style={{
          ...styles.button,
          opacity: canContinue ? 1 : 0.35,
        }}
      >
        Continue
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
    gap: 24,
    maxWidth: 480,
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
  section: {
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
    width: '100%',
  },
  sectionLabel: {
    fontSize: 11,
    fontWeight: 500,
    color: 'var(--text-50)',
    letterSpacing: '0.05em',
    fontFamily: "'Space Grotesk', sans-serif",
    margin: 0,
  },
  sectionLabelRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
  },
  sectionHint: {
    fontSize: 13,
    color: 'var(--text-30)',
    margin: 0,
    fontFamily: "'Inter', sans-serif",
  },
  nameInput: {
    background: 'var(--onboarding-card)',
    border: '1px solid var(--accent-cyan-20)',
    borderRadius: 8,
    padding: '10px 14px',
    fontSize: 18,
    color: 'var(--text-primary)',
    outline: 'none',
    fontFamily: "'Space Grotesk', sans-serif",
    fontWeight: 500,
    letterSpacing: '0.05em',
    textAlign: 'center',
    transition: 'border-color 0.2s',
  },
  nameSuggestions: {
    display: 'flex',
    justifyContent: 'center',
    gap: 8,
    flexWrap: 'wrap',
  },
  suggestionChip: {
    border: '1px solid var(--onboarding-border)',
    borderRadius: 16,
    padding: '4px 14px',
    fontSize: 12,
    color: 'var(--text-40)',
    background: 'transparent',
    cursor: 'pointer',
    fontFamily: "'Space Grotesk', sans-serif",
    transition: 'all 0.2s ease',
  },
  optionRow: {
    display: 'flex',
    gap: 8,
  },
  optionButton: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 4,
    padding: '12px 8px',
    borderRadius: 10,
    border: '1px solid var(--onboarding-border)',
    background: 'var(--bg-surface)',
    cursor: 'pointer',
    transition: 'all 0.2s ease',
  },
  optionLabel: {
    fontSize: 13,
    fontWeight: 500,
    fontFamily: "'Space Grotesk', sans-serif",
  },
  optionDesc: {
    fontSize: 10,
    color: 'var(--text-30)',
    fontFamily: "'Inter', sans-serif",
  },
  feelGrid: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: 8,
  },
  feelButton: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    padding: '10px 16px',
    borderRadius: 10,
    border: '1px solid var(--onboarding-border)',
    background: 'var(--bg-surface)',
    cursor: 'pointer',
    transition: 'all 0.2s ease',
    minWidth: 140,
    flex: '1 1 calc(50% - 4px)',
  },
  feelDot: {
    width: 10,
    height: 10,
    borderRadius: '50%',
    flexShrink: 0,
    transition: 'box-shadow 0.2s ease',
  },
  feelLabel: {
    fontSize: 13,
    fontWeight: 500,
    fontFamily: "'Space Grotesk', sans-serif",
    display: 'block',
  },
  feelDesc: {
    fontSize: 10,
    color: 'var(--text-30)',
    fontFamily: "'Inter', sans-serif",
    display: 'block',
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
    transition: 'all 0.2s ease',
    marginTop: 8,
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

export default IdentityStep;
