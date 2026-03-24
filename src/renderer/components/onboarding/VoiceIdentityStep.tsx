/**
 * VoiceIdentityStep.tsx — Agent identity + voice engine + voice preview.
 *
 * "Your Agent's Voice." — Combines agent naming, gender/voice-feel selection,
 * voice engine choice, and a live voice preview into a single onboarding step.
 */

import React, { useState, useCallback, useEffect } from 'react';
import { Mic, Volume2 } from 'lucide-react';
import CyberInput from './shared/CyberInput';
import NextButton from './shared/NextButton';
import type { IdentityChoices } from '../OnboardingWizard';

interface VoiceIdentityStepProps {
  choices: IdentityChoices;
  onChange: (choices: IdentityChoices) => void;
  onComplete: () => void;
  onBack?: () => void;
}

type VoiceEngine = 'auto' | 'chatterbox' | 'kokoro' | 'cloud' | 'none';

const GENDERS: { value: IdentityChoices['gender']; label: string }[] = [
  { value: 'male', label: 'Male' },
  { value: 'female', label: 'Female' },
  { value: 'neutral', label: 'Neutral' },
];

const VOICE_FEELS: { value: IdentityChoices['voiceFeel']; label: string; desc: string; color: string }[] = [
  { value: 'warm', label: 'Warm', desc: 'Friendly, approachable', color: '#f59e0b' },
  { value: 'sharp', label: 'Sharp', desc: 'Precise, articulate', color: '#8A2BE2' },
  { value: 'deep', label: 'Deep', desc: 'Rich, resonant', color: '#3b82f6' },
  { value: 'soft', label: 'Soft', desc: 'Calm, gentle', color: '#22c55e' },
  { value: 'bright', label: 'Bright', desc: 'Energetic, clear', color: '#00f0ff' },
];

const VOICE_MAP: Record<IdentityChoices['voiceFeel'], Record<IdentityChoices['gender'], string>> = {
  warm: { male: 'Enceladus', female: 'Aoede', neutral: 'Achird' },
  sharp: { male: 'Puck', female: 'Kore', neutral: 'Zephyr' },
  deep: { male: 'Iapetus', female: 'Despina', neutral: 'Orus' },
  soft: { male: 'Charon', female: 'Achernar', neutral: 'Sulafat' },
  bright: { male: 'Fenrir', female: 'Leda', neutral: 'Zephyr' },
};

const ENGINE_OPTIONS: { value: VoiceEngine; label: string; desc: string }[] = [
  { value: 'auto', label: 'Auto', desc: 'Best available engine per situation' },
  { value: 'chatterbox', label: 'Chatterbox Turbo', desc: 'Voice cloning, GPU required' },
  { value: 'kokoro', label: 'Kokoro (Local)', desc: 'Fast, lightweight, offline' },
  { value: 'cloud', label: 'Cloud (ElevenLabs)', desc: 'Highest quality, needs API key' },
  { value: 'none', label: 'Text Only', desc: 'No speech synthesis' },
];

/** Collect all unique voice names for a given gender, excluding the primary match */
function getAlternativeVoices(gender: IdentityChoices['gender'], currentVoice: string): string[] {
  const allVoices = Object.values(VOICE_MAP).map((genderMap) => genderMap[gender]);
  const unique = Array.from(new Set(allVoices));
  return unique.filter((v) => v !== currentVoice);
}

const VoiceIdentityStep: React.FC<VoiceIdentityStepProps> = ({ choices, onChange, onComplete }) => {
  const [engine, setEngine] = useState<VoiceEngine>('auto');
  const [fadeIn, setFadeIn] = useState(false);
  const [previewing, setPreviewing] = useState(false);

  // Load current voice engine setting on mount
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const current = await window.eve.settings.getVoiceEngine();
        if (!cancelled && current) {
          const mapped = current as VoiceEngine;
          if (ENGINE_OPTIONS.some((o) => o.value === mapped)) {
            setEngine(mapped);
          }
        }
      } catch {
        // Use default 'auto'
      }
    })();
    setTimeout(() => { if (!cancelled) setFadeIn(true); }, 100);
    return () => { cancelled = true; };
  }, []);

  const matchedVoice = VOICE_MAP[choices.voiceFeel]?.[choices.gender] ?? 'Zephyr';
  const alternatives = getAlternativeVoices(choices.gender, matchedVoice);

  const updateField = <K extends keyof IdentityChoices>(key: K, value: IdentityChoices[K]) => {
    onChange({ ...choices, [key]: value });
  };

  const handlePreview = useCallback(async (voiceName: string) => {
    setPreviewing(true);
    try {
      await window.eve.voice?.profiles?.preview?.(voiceName);
    } catch {
      // Graceful failure if voice preview is unavailable
    }
    setPreviewing(false);
  }, []);

  const handleContinue = useCallback(async () => {
    try {
      await window.eve.settings.setVoiceEngine(engine);
    } catch {
      // Best effort — continue anyway
    }
    onComplete();
  }, [engine, onComplete]);

  const canContinue = choices.agentName.trim().length > 0;

  return (
    <section style={{
      ...styles.container,
      opacity: fadeIn ? 1 : 0,
      transform: fadeIn ? 'translateY(0)' : 'translateY(16px)',
      transition: 'all 0.7s cubic-bezier(0.16, 1, 0.3, 1)',
    }} aria-label="Agent voice and identity configuration">
      <div style={styles.headerBlock}>
        <h2 style={styles.heading}>Your Agent's Voice.</h2>
        <p style={styles.subtitle}>
          Name your agent and choose how it sounds.
        </p>
      </div>

      <div style={styles.scrollArea}>
        {/* ─── Section 1: Name & Character ─── */}
        <div style={styles.sectionCard}>
          <div style={styles.sectionHeader}>
            <div style={{ ...styles.sectionIconBox, background: 'rgba(138, 43, 226, 0.08)', border: '1px solid rgba(138, 43, 226, 0.15)' }}>
              <Mic size={18} color="#8A2BE2" />
            </div>
            <div>
              <div style={styles.sectionTitle}>Name & Character</div>
              <div style={styles.sectionDesc}>Identity and voice personality</div>
            </div>
          </div>

          <div style={styles.field}>
            <CyberInput
              id="agent-name"
              label="Agent Name"
              value={choices.agentName}
              onChange={(v) => updateField('agentName', v)}
              maxLength={24}
            />
            <span style={styles.nameHint}>Default: Friday — or choose your own</span>
          </div>

          <div style={styles.field}>
            <label id="voice-gender-label" style={styles.fieldLabel}>Gender</label>
            <div style={styles.genderRow} role="radiogroup" aria-labelledby="voice-gender-label">
              {GENDERS.map((g) => (
                <button
                  key={g.value}
                  onClick={() => updateField('gender', g.value)}
                  role="radio"
                  aria-checked={choices.gender === g.value}
                  style={{
                    ...styles.genderButton,
                    borderColor: choices.gender === g.value ? 'var(--accent-cyan-30)' : 'rgba(255,255,255,0.06)',
                    background: choices.gender === g.value ? 'var(--accent-cyan-10)' : 'transparent',
                    color: choices.gender === g.value ? 'var(--accent-cyan-90)' : 'var(--text-50)',
                  }}
                >
                  {g.label}
                </button>
              ))}
            </div>
          </div>

          <div style={styles.field}>
            <label id="voice-feel-label" style={styles.fieldLabel}>Voice Feel</label>
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
        </div>

        {/* ─── Section 2: Voice Engine ─── */}
        <div style={styles.sectionCard}>
          <div style={styles.sectionHeader}>
            <div style={styles.sectionIconBox}>
              <Volume2 size={18} color="#00f0ff" />
            </div>
            <div>
              <div style={styles.sectionTitle}>Voice Engine</div>
              <div style={styles.sectionDesc}>How your agent speaks</div>
            </div>
          </div>

          <div style={styles.engineList} role="radiogroup" aria-label="Voice engine selection">
            {ENGINE_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                onClick={() => setEngine(opt.value)}
                role="radio"
                aria-checked={engine === opt.value}
                style={{
                  ...styles.engineOption,
                  borderColor: engine === opt.value ? 'var(--accent-cyan-30)' : 'rgba(255,255,255,0.06)',
                  background: engine === opt.value ? 'var(--accent-cyan-10)' : 'transparent',
                }}
              >
                <div style={{
                  ...styles.engineRadio,
                  borderColor: engine === opt.value ? 'var(--accent-cyan)' : 'rgba(255,255,255,0.15)',
                }}>
                  {engine === opt.value && <div style={styles.engineRadioDot} />}
                </div>
                <div style={styles.engineText}>
                  <span style={{
                    ...styles.engineLabel,
                    color: engine === opt.value ? 'var(--text-primary)' : 'var(--text-60)',
                  }}>
                    {opt.label}
                  </span>
                  <span style={styles.engineDesc}>{opt.desc}</span>
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* ─── Section 3: Voice Preview ─── */}
        <div style={styles.sectionCard}>
          <div style={styles.sectionHeader}>
            <div style={{ ...styles.sectionIconBox, background: 'rgba(245, 158, 11, 0.08)', border: '1px solid rgba(245, 158, 11, 0.15)' }}>
              <Volume2 size={18} color="#f59e0b" />
            </div>
            <div>
              <div style={styles.sectionTitle}>Voice Preview</div>
              <div style={styles.sectionDesc}>
                Matched voice: <span style={styles.voiceNameHighlight}>{matchedVoice}</span>
              </div>
            </div>
          </div>

          <button
            onClick={() => handlePreview(matchedVoice)}
            disabled={previewing}
            style={{
              ...styles.previewButton,
              opacity: previewing ? 0.5 : 1,
            }}
          >
            <span style={styles.previewPlayIcon} aria-hidden="true">{previewing ? '...' : '\u25B6'}</span>
            <span style={styles.previewText}>&quot;Good morning. How can I help you today?&quot;</span>
          </button>

          {alternatives.length > 0 && (
            <div style={styles.altVoicesRow}>
              <span style={styles.altVoicesLabel}>Try other voices:</span>
              <div style={styles.altVoicesButtons}>
                {alternatives.map((name) => (
                  <button
                    key={name}
                    onClick={() => handlePreview(name)}
                    disabled={previewing}
                    style={styles.altVoiceButton}
                  >
                    {name}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      <NextButton
        label="Continue"
        onClick={handleContinue}
        disabled={!canContinue}
      />

      <p style={styles.hint}>
        Your agent's name, voice, and engine can be changed later in Settings.
      </p>
    </section>
  );
};

/* ── Styles ─────────────────────────────────────────────────── */

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
  scrollArea: {
    width: '100%',
    maxHeight: 440,
    overflowY: 'auto',
    display: 'flex',
    flexDirection: 'column',
    gap: 20,
    paddingRight: 4,
  },
  sectionCard: {
    background: 'var(--onboarding-card)',
    border: '1px solid rgba(255, 255, 255, 0.04)',
    borderRadius: 12,
    padding: '20px 24px',
    display: 'flex',
    flexDirection: 'column',
    gap: 16,
  },
  sectionHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 14,
  },
  sectionIconBox: {
    width: 40,
    height: 40,
    borderRadius: 10,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    background: 'rgba(0, 240, 255, 0.08)',
    border: '1px solid rgba(0, 240, 255, 0.15)',
    flexShrink: 0,
  },
  sectionTitle: {
    fontSize: 15,
    fontWeight: 500,
    color: 'var(--text-primary)',
    fontFamily: "'Space Grotesk', sans-serif",
  },
  sectionDesc: {
    fontSize: 11,
    color: 'var(--text-30)',
    fontFamily: "'Inter', sans-serif",
  },
  field: {
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  fieldLabel: {
    fontSize: 11,
    fontWeight: 500,
    color: 'var(--text-50)',
    letterSpacing: '0.05em',
    fontFamily: "'Space Grotesk', sans-serif",
    margin: 0,
  },
  nameHint: {
    fontSize: 10,
    color: 'var(--text-20)',
    fontFamily: "'Inter', sans-serif",
    marginTop: -4,
  },
  genderRow: {
    display: 'flex',
    gap: 8,
  },
  genderButton: {
    flex: 1,
    padding: '10px 8px',
    borderRadius: 8,
    border: '1px solid rgba(255,255,255,0.06)',
    background: 'transparent',
    cursor: 'pointer',
    fontSize: 13,
    fontWeight: 500,
    fontFamily: "'Space Grotesk', sans-serif",
    transition: 'all 0.2s ease',
    textAlign: 'center',
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
    padding: '10px 14px',
    borderRadius: 8,
    border: '1px solid rgba(255,255,255,0.06)',
    background: 'rgba(255,255,255,0.02)',
    cursor: 'pointer',
    transition: 'all 0.2s ease',
    minWidth: 130,
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

  /* Voice Engine */
  engineList: {
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  engineOption: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    padding: '10px 14px',
    borderRadius: 8,
    border: '1px solid rgba(255,255,255,0.06)',
    background: 'transparent',
    cursor: 'pointer',
    transition: 'all 0.2s ease',
    textAlign: 'left',
  },
  engineRadio: {
    width: 16,
    height: 16,
    borderRadius: '50%',
    border: '2px solid rgba(255,255,255,0.15)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
    transition: 'border-color 0.2s ease',
  },
  engineRadioDot: {
    width: 8,
    height: 8,
    borderRadius: '50%',
    background: 'var(--accent-cyan)',
    boxShadow: '0 0 6px var(--accent-cyan-30)',
  },
  engineText: {
    display: 'flex',
    flexDirection: 'column',
    gap: 2,
  },
  engineLabel: {
    fontSize: 13,
    fontWeight: 500,
    fontFamily: "'Space Grotesk', sans-serif",
  },
  engineDesc: {
    fontSize: 10,
    color: 'var(--text-30)',
    fontFamily: "'Inter', sans-serif",
  },

  /* Voice Preview */
  voiceNameHighlight: {
    color: 'var(--accent-cyan-70)',
    fontWeight: 500,
    fontFamily: "'Space Grotesk', sans-serif",
  },
  previewButton: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    padding: '12px 16px',
    borderRadius: 8,
    border: '1px solid rgba(0, 240, 255, 0.15)',
    background: 'rgba(0, 240, 255, 0.04)',
    cursor: 'pointer',
    transition: 'all 0.2s ease',
    textAlign: 'left',
  },
  previewPlayIcon: {
    fontSize: 12,
    color: 'var(--accent-cyan)',
    flexShrink: 0,
    width: 20,
    textAlign: 'center',
  },
  previewText: {
    fontSize: 12,
    color: 'var(--text-50)',
    fontFamily: "'Inter', sans-serif",
    fontStyle: 'italic',
    lineHeight: 1.4,
  },
  altVoicesRow: {
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  altVoicesLabel: {
    fontSize: 10,
    color: 'var(--text-30)',
    fontFamily: "'Inter', sans-serif",
  },
  altVoicesButtons: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: 6,
  },
  altVoiceButton: {
    fontSize: 10,
    fontWeight: 500,
    color: 'var(--text-50)',
    padding: '4px 12px',
    borderRadius: 6,
    background: 'rgba(255, 255, 255, 0.03)',
    border: '1px solid rgba(255, 255, 255, 0.06)',
    cursor: 'pointer',
    fontFamily: "'Space Grotesk', sans-serif",
    transition: 'all 0.2s ease',
  },

  hint: {
    fontSize: 10,
    color: 'var(--text-20)',
    margin: 0,
    textAlign: 'center',
    fontFamily: "'Inter', sans-serif",
    maxWidth: 400,
  },
};

export default VoiceIdentityStep;
