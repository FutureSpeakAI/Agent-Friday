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

const IdentityStep: React.FC<IdentityStepProps> = ({ choices, onChange, onComplete }) => {
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
    <div style={{
      ...styles.container,
      opacity: fadeIn ? 1 : 0,
      transform: fadeIn ? 'translateY(0)' : 'translateY(16px)',
      transition: 'all 0.7s cubic-bezier(0.16, 1, 0.3, 1)',
    }}>
      <div style={styles.header}>
        <div style={styles.headerLine} />
        <span style={styles.headerLabel}>AGENT IDENTITY</span>
        <div style={styles.headerLine} />
      </div>

      {/* Icon */}
      <div style={styles.iconWrap}>
        <User size={36} color="#00f0ff" />
      </div>

      {/* Agent Name */}
      <div style={styles.section}>
        <label style={styles.sectionLabel}>Agent Name</label>
        <p style={styles.sectionHint}>What should your AI companion be called?</p>
        <input
          type="text"
          value={choices.agentName}
          onChange={(e) => updateField('agentName', e.target.value)}
          placeholder="Friday"
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
                borderColor: choices.agentName === name ? 'rgba(0, 240, 255, 0.4)' : 'rgba(255,255,255,0.08)',
                color: choices.agentName === name ? 'rgba(0, 240, 255, 0.9)' : 'rgba(255,255,255,0.4)',
                background: choices.agentName === name ? 'rgba(0, 240, 255, 0.08)' : 'transparent',
              }}
            >
              {name}
            </button>
          ))}
        </div>
      </div>

      {/* Voice Gender */}
      <div style={styles.section}>
        <label style={styles.sectionLabel}>Voice Gender</label>
        <div style={styles.optionRow}>
          {GENDERS.map((g) => (
            <button
              key={g.value}
              onClick={() => updateField('gender', g.value)}
              style={{
                ...styles.optionButton,
                borderColor: choices.gender === g.value ? 'rgba(0, 240, 255, 0.4)' : 'rgba(255,255,255,0.06)',
                background: choices.gender === g.value ? 'rgba(0, 240, 255, 0.08)' : 'rgba(255,255,255,0.02)',
              }}
            >
              <span style={{
                ...styles.optionLabel,
                color: choices.gender === g.value ? 'rgba(0, 240, 255, 0.9)' : 'rgba(255,255,255,0.6)',
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
          <Mic size={14} color="rgba(255,255,255,0.4)" />
          <label style={styles.sectionLabel}>Voice Feel</label>
        </div>
        <div style={styles.feelGrid}>
          {VOICE_FEELS.map((vf) => (
            <button
              key={vf.value}
              onClick={() => updateField('voiceFeel', vf.value)}
              style={{
                ...styles.feelButton,
                borderColor: choices.voiceFeel === vf.value ? `${vf.color}66` : 'rgba(255,255,255,0.06)',
                background: choices.voiceFeel === vf.value ? `${vf.color}15` : 'rgba(255,255,255,0.02)',
              }}
            >
              <div style={{
                ...styles.feelDot,
                background: vf.color,
                boxShadow: choices.voiceFeel === vf.value ? `0 0 8px ${vf.color}80` : 'none',
              }} />
              <div>
                <span style={{
                  ...styles.feelLabel,
                  color: choices.voiceFeel === vf.value ? '#F8FAFC' : 'rgba(255,255,255,0.6)',
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
    </div>
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
    background: 'linear-gradient(90deg, transparent, rgba(0, 240, 255, 0.2), transparent)',
  },
  headerLabel: {
    fontSize: 11,
    fontWeight: 600,
    letterSpacing: '0.25em',
    color: 'rgba(0, 240, 255, 0.7)',
    fontFamily: "'Space Grotesk', sans-serif",
    whiteSpace: 'nowrap',
  },
  iconWrap: {
    width: 72,
    height: 72,
    borderRadius: 20,
    background: 'rgba(0, 240, 255, 0.06)',
    border: '1px solid rgba(0, 240, 255, 0.15)',
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
    color: 'rgba(255, 255, 255, 0.5)',
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
    color: 'rgba(255, 255, 255, 0.35)',
    margin: 0,
    fontFamily: "'Inter', sans-serif",
  },
  nameInput: {
    background: 'rgba(255, 255, 255, 0.03)',
    border: '1px solid rgba(0, 240, 255, 0.15)',
    borderRadius: 8,
    padding: '10px 14px',
    fontSize: 18,
    color: '#F8FAFC',
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
    border: '1px solid rgba(255,255,255,0.08)',
    borderRadius: 16,
    padding: '4px 14px',
    fontSize: 12,
    color: 'rgba(255,255,255,0.4)',
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
    border: '1px solid rgba(255,255,255,0.06)',
    background: 'rgba(255,255,255,0.02)',
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
    color: 'rgba(255,255,255,0.3)',
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
    border: '1px solid rgba(255,255,255,0.06)',
    background: 'rgba(255,255,255,0.02)',
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
    color: 'rgba(255,255,255,0.3)',
    fontFamily: "'Inter', sans-serif",
    display: 'block',
  },
  button: {
    background: 'rgba(0, 240, 255, 0.08)',
    border: '1px solid rgba(0, 240, 255, 0.25)',
    borderRadius: 8,
    padding: '12px 48px',
    fontSize: 14,
    fontWeight: 500,
    color: 'rgba(0, 240, 255, 0.9)',
    letterSpacing: '0.05em',
    fontFamily: "'Space Grotesk', sans-serif",
    cursor: 'pointer',
    transition: 'all 0.2s ease',
    marginTop: 8,
  },
};

export default IdentityStep;
