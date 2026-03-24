/**
 * PersonalityStep.tsx — Personality calibration path selection.
 *
 * "Shape Your Agent." — Users choose between a voice interview,
 * manual slider calibration, or skipping to use defaults.
 * Manual calibration presents 5 personality trait sliders that
 * save to settings before advancing to first contact.
 */

import React, { useState, useEffect, useCallback } from 'react';
import { Mic, SlidersHorizontal } from 'lucide-react';
import NextButton from './shared/NextButton';

export type PersonalityPath = 'interview' | 'firstContact' | 'skip';

export interface PersonalitySliders {
  communicationStyle: number;
  emotionalTone: number;
  initiativeLevel: number;
  humor: number;
  formality: number;
}

interface PersonalityStepProps {
  onComplete: (path: PersonalityPath, sliders?: PersonalitySliders) => void;
  onBack?: () => void;
}

const DEFAULT_SLIDERS: PersonalitySliders = {
  communicationStyle: 50,
  emotionalTone: 60,
  initiativeLevel: 40,
  humor: 30,
  formality: 50,
};

interface SliderDef {
  key: keyof PersonalitySliders;
  label: string;
  leftLabel: string;
  rightLabel: string;
}

const SLIDER_DEFS: SliderDef[] = [
  { key: 'communicationStyle', label: 'Communication Style', leftLabel: 'Concise', rightLabel: 'Conversational' },
  { key: 'emotionalTone', label: 'Emotional Tone', leftLabel: 'Professional', rightLabel: 'Warm & Personal' },
  { key: 'initiativeLevel', label: 'Initiative Level', leftLabel: 'Always Ask', rightLabel: 'Act Proactively' },
  { key: 'humor', label: 'Humor', leftLabel: 'Serious', rightLabel: 'Playful' },
  { key: 'formality', label: 'Formality', leftLabel: 'Casual', rightLabel: 'Formal' },
];

const PersonalityStep: React.FC<PersonalityStepProps> = ({ onComplete, onBack }) => {
  const [fadeIn, setFadeIn] = useState(false);
  const [phase, setPhase] = useState<'select' | 'sliders'>('select');
  const [sliders, setSliders] = useState<PersonalitySliders>({ ...DEFAULT_SLIDERS });

  useEffect(() => {
    const timer = setTimeout(() => setFadeIn(true), 100);
    return () => clearTimeout(timer);
  }, []);

  const handleSliderChange = useCallback((key: keyof PersonalitySliders, value: number) => {
    setSliders((prev) => ({ ...prev, [key]: value }));
  }, []);

  const handleSlidersComplete = useCallback(async () => {
    try {
      await window.eve.settings.set('personalitySliders', JSON.stringify(sliders));
    } catch {
      // Settings save is best-effort during onboarding
    }
    onComplete('firstContact', sliders);
  }, [sliders, onComplete]);

  return (
    <section style={{
      ...styles.container,
      opacity: fadeIn ? 1 : 0,
      transform: fadeIn ? 'translateY(0)' : 'translateY(16px)',
      transition: 'all 0.7s cubic-bezier(0.16, 1, 0.3, 1)',
    }} aria-label="Personality calibration">
      <div style={styles.headerBlock}>
        <h2 style={styles.heading}>Shape Your Agent.</h2>
        <p style={styles.subtitle}>
          Choose how you'd like to calibrate your agent's personality.
        </p>
      </div>

      {phase === 'select' ? (
        <>
          {/* Path selection cards */}
          <div style={styles.cardRow}>
            {/* Interview card */}
            <button
              style={styles.pathCard}
              onClick={() => onComplete('interview')}
              aria-label="Start voice interview"
            >
              <div style={styles.pathIconBox}>
                <Mic size={24} color="#00f0ff" />
              </div>
              <div style={styles.pathTitle}>Interview Me</div>
              <p style={styles.pathDesc}>
                A short voice or text conversation where your agent learns your
                communication style. The cinematic path.
              </p>
              <div style={styles.pathButtonWrap}>
                <NextButton label="Start Interview" onClick={() => onComplete('interview')} />
              </div>
            </button>

            {/* Manual calibration card */}
            <button
              style={styles.pathCard}
              onClick={() => setPhase('sliders')}
              aria-label="Configure personality manually"
            >
              <div style={{ ...styles.pathIconBox, background: 'rgba(138, 43, 226, 0.08)', border: '1px solid rgba(138, 43, 226, 0.15)' }}>
                <SlidersHorizontal size={24} color="#8A2BE2" />
              </div>
              <div style={styles.pathTitle}>Manual Calibration</div>
              <p style={styles.pathDesc}>
                Set personality traits with sliders. Quick and precise.
                You'll still meet your agent afterward.
              </p>
              <div style={styles.pathButtonWrap}>
                <NextButton label="Configure" onClick={() => setPhase('sliders')} variant="secondary" />
              </div>
            </button>
          </div>

          {/* Skip */}
          <NextButton
            label="Skip — Use Defaults"
            onClick={() => onComplete('skip')}
            variant="skip"
          />
        </>
      ) : (
        <>
          {/* Phase 2: Slider calibration */}
          <div style={styles.slidersCard}>
            {SLIDER_DEFS.map((def) => (
              <div key={def.key} style={styles.sliderRow}>
                <div style={styles.sliderLabelCenter}>{def.label}</div>
                <div style={styles.sliderControl}>
                  <span style={styles.sliderLabelSide}>{def.leftLabel}</span>
                  <input
                    type="range"
                    min={0}
                    max={100}
                    value={sliders[def.key]}
                    onChange={(e) => handleSliderChange(def.key, Number(e.target.value))}
                    className="personality-range"
                    aria-label={def.label}
                    style={styles.rangeInput}
                  />
                  <span style={{ ...styles.sliderLabelSide, textAlign: 'right' }}>{def.rightLabel}</span>
                </div>
              </div>
            ))}
          </div>

          <div style={styles.sliderActions}>
            <NextButton label="Back" onClick={() => setPhase('select')} variant="secondary" />
            <NextButton label="Continue to First Contact" onClick={handleSlidersComplete} />
          </div>
        </>
      )}
    </section>
  );
};

/* ── Inline <style> injected once for range input customization ── */
const RANGE_CSS = `
input[type="range"].personality-range {
  -webkit-appearance: none;
  appearance: none;
  width: 100%;
  height: 3px;
  background: rgba(255, 255, 255, 0.1);
  border-radius: 2px;
  outline: none;
  cursor: pointer;
}
input[type="range"].personality-range::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: #00f0ff;
  border: none;
  cursor: pointer;
  box-shadow: 0 0 6px rgba(0, 240, 255, 0.4);
}
input[type="range"].personality-range::-moz-range-thumb {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: #00f0ff;
  border: none;
  cursor: pointer;
  box-shadow: 0 0 6px rgba(0, 240, 255, 0.4);
}
input[type="range"].personality-range::-moz-range-track {
  height: 3px;
  background: rgba(255, 255, 255, 0.1);
  border-radius: 2px;
  border: none;
}
`;

// Inject style tag once
if (typeof document !== 'undefined') {
  const id = 'personality-range-css';
  if (!document.getElementById(id)) {
    const style = document.createElement('style');
    style.id = id;
    style.textContent = RANGE_CSS;
    document.head.appendChild(style);
  }
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 24,
    maxWidth: 560,
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
  cardRow: {
    display: 'flex',
    gap: 16,
    width: '100%',
  },
  pathCard: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column' as const,
    alignItems: 'center',
    gap: 14,
    background: 'var(--onboarding-card)',
    border: '1px solid rgba(255, 255, 255, 0.04)',
    borderRadius: 12,
    padding: '28px 20px 20px',
    cursor: 'pointer',
    transition: 'all 0.2s ease',
    textAlign: 'center' as const,
  },
  pathIconBox: {
    width: 52,
    height: 52,
    borderRadius: 14,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    background: 'rgba(0, 240, 255, 0.08)',
    border: '1px solid rgba(0, 240, 255, 0.15)',
    flexShrink: 0,
  },
  pathTitle: {
    fontSize: 15,
    fontWeight: 500,
    color: 'var(--text-primary)',
    fontFamily: "'Space Grotesk', sans-serif",
  },
  pathDesc: {
    fontSize: 11,
    color: 'var(--text-30)',
    lineHeight: 1.6,
    margin: 0,
    fontFamily: "'Inter', sans-serif",
    flex: 1,
  },
  pathButtonWrap: {
    marginTop: 4,
    pointerEvents: 'none' as const,
  },
  slidersCard: {
    width: '100%',
    background: 'var(--onboarding-card)',
    border: '1px solid rgba(255, 255, 255, 0.04)',
    borderRadius: 12,
    padding: '20px 24px',
    display: 'flex',
    flexDirection: 'column',
    gap: 20,
  },
  sliderRow: {
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
  },
  sliderLabelCenter: {
    fontSize: 12,
    fontWeight: 500,
    color: 'var(--text-60)',
    fontFamily: "'Space Grotesk', sans-serif",
    textAlign: 'center',
  },
  sliderControl: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
  },
  sliderLabelSide: {
    fontSize: 10,
    color: 'var(--text-30)',
    fontFamily: "'Inter', sans-serif",
    width: 100,
    flexShrink: 0,
  },
  rangeInput: {
    flex: 1,
    accentColor: '#00f0ff',
    width: '100%',
  },
  sliderActions: {
    display: 'flex',
    gap: 12,
    alignItems: 'center',
  },
};

export default PersonalityStep;
