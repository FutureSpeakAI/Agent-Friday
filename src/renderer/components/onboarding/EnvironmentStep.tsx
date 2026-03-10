/**
 * EnvironmentStep.tsx — Step 3: Merged Sovereignty + Identity.
 *
 * "Data Sovereignty." — Vault passphrase setup + agent identity
 * configuration in a single screen. Auto-skips vault section if
 * already initialized. Both sections visible simultaneously.
 */

import React, { useState, useCallback, useEffect, useRef } from 'react';
import { Lock, ShieldCheck, Mic } from 'lucide-react';
import CyberInput from './shared/CyberInput';
import NextButton from './shared/NextButton';
import type { IdentityChoices } from '../OnboardingWizard';

interface EnvironmentStepProps {
  choices: IdentityChoices;
  onChange: (choices: IdentityChoices) => void;
  onComplete: () => void;
  onBack?: () => void;
}

const MIN_PASSPHRASE_LENGTH = 8;
type StrengthLevel = 'weak' | 'fair' | 'good' | 'strong';

const STRENGTH_META: Record<StrengthLevel, { label: string; color: string; percent: number }> = {
  weak:   { label: 'Weak',   color: '#ef4444', percent: 25 },
  fair:   { label: 'Fair',   color: '#f59e0b', percent: 50 },
  good:   { label: 'Good',   color: '#eab308', percent: 75 },
  strong: { label: 'Strong', color: '#22c55e', percent: 100 },
};

function calcStrength(pass: string): StrengthLevel {
  if (pass.length < MIN_PASSPHRASE_LENGTH) return 'weak';
  let score = 0;
  if (pass.length >= 20) score += 3;
  else if (pass.length >= 16) score += 2;
  else if (pass.length >= 12) score += 1;
  if (/[a-z]/.test(pass) && /[A-Z]/.test(pass)) score += 1;
  if (/\d/.test(pass)) score += 1;
  if (/[^a-zA-Z0-9]/.test(pass)) score += 1;
  if (score >= 5) return 'strong';
  if (score >= 3) return 'good';
  if (score >= 1) return 'fair';
  return 'weak';
}

const ZERO_KNOWLEDGE_BADGES = [
  'AES-256-GCM',
  'Argon2id KDF',
  'HMAC-SHA256',
  'Zero-Knowledge',
];

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

const EnvironmentStep: React.FC<EnvironmentStepProps> = ({ choices, onChange, onComplete, onBack }) => {
  const [passphrase, setPassphrase] = useState('');
  const [confirm, setConfirm] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [vaultReady, setVaultReady] = useState(false);
  const [fadeIn, setFadeIn] = useState(false);

  // Check if vault is already initialized
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const initialized = await window.eve.vault.isInitialized();
        if (initialized && !cancelled) {
          setVaultReady(true);
        }
      } catch { /* not initialized */ }
      setTimeout(() => { if (!cancelled) setFadeIn(true); }, 100);
    })();
    return () => { cancelled = true; };
  }, []);

  const strength = calcStrength(passphrase);
  const strengthMeta = STRENGTH_META[strength];
  const mismatch = confirm.length > 0 && passphrase !== confirm;
  const tooShort = passphrase.length > 0 && passphrase.length < MIN_PASSPHRASE_LENGTH;
  const strongEnough = strength !== 'weak';
  const canInitVault = passphrase.length >= MIN_PASSPHRASE_LENGTH && strongEnough && passphrase === confirm && !saving;

  const canContinue = vaultReady && choices.agentName.trim().length > 0;

  const handleInitVault = useCallback(async () => {
    if (!canInitVault) return;
    setSaving(true);
    setError('');
    try {
      await window.eve.vault.initializeNew(passphrase);
      setVaultReady(true);
      setSaving(false);
    } catch (err: any) {
      setError(err?.message || 'Failed to initialize vault');
      setSaving(false);
    }
  }, [passphrase, canInitVault]);

  const updateField = <K extends keyof IdentityChoices>(key: K, value: IdentityChoices[K]) => {
    onChange({ ...choices, [key]: value });
  };

  return (
    <section style={{
      ...styles.container,
      opacity: fadeIn ? 1 : 0,
      transform: fadeIn ? 'translateY(0)' : 'translateY(16px)',
      transition: 'all 0.7s cubic-bezier(0.16, 1, 0.3, 1)',
    }} aria-label="Data sovereignty and agent identity">
      <div style={styles.headerBlock}>
        <h2 style={styles.heading}>Data Sovereignty.</h2>
        <p style={styles.subtitle}>
          Your vault is encrypted on-device. No data ever leaves your machine.
        </p>
      </div>

      {/* Scrollable content */}
      <div style={styles.scrollArea}>
        {/* ─── Section 1: Vault ─── */}
        <div style={styles.sectionCard}>
          <div style={styles.sectionHeader}>
            <div style={styles.sectionIconBox}>
              {vaultReady ? (
                <ShieldCheck size={18} color="#22c55e" />
              ) : (
                <Lock size={18} color="#00f0ff" />
              )}
            </div>
            <div>
              <div style={styles.sectionTitle}>Sovereign Vault</div>
              <div style={styles.sectionDesc}>Encrypted local storage</div>
            </div>
          </div>

          {vaultReady ? (
            <div style={styles.readyBox} role="status" aria-live="polite">
              <ShieldCheck size={14} color="#22c55e" aria-hidden="true" />
              <span style={styles.readyText}>Vault initialized and secured</span>
            </div>
          ) : (
            <div style={styles.vaultForm}>
              <CyberInput
                id="vault-passphrase"
                label="Create Passphrase"
                value={passphrase}
                onChange={setPassphrase}
                type="password"
                monospace
                autoFocus
                error={tooShort ? `Minimum ${MIN_PASSPHRASE_LENGTH} characters` : undefined}
              />

              {/* Strength indicator */}
              {passphrase.length >= MIN_PASSPHRASE_LENGTH && (
                <div style={styles.strengthContainer} role="status" aria-live="polite">
                  <div style={styles.strengthTrack}>
                    <div style={{
                      ...styles.strengthFill,
                      width: `${strengthMeta.percent}%`,
                      background: strengthMeta.color,
                      boxShadow: `0 0 6px ${strengthMeta.color}40`,
                    }} />
                  </div>
                  <span style={{ ...styles.strengthLabel, color: strengthMeta.color }}>
                    {strengthMeta.label}
                  </span>
                </div>
              )}

              <CyberInput
                id="vault-passphrase-confirm"
                label="Confirm Passphrase"
                value={confirm}
                onChange={setConfirm}
                type="password"
                monospace
                error={mismatch ? 'Passphrases do not match' : undefined}
                success={!!(confirm && !mismatch && passphrase === confirm)}
                onKeyDown={(e) => { if (e.key === 'Enter' && canInitVault) handleInitVault(); }}
              />

              <NextButton
                label={saving ? 'Initializing...' : 'Initialize Vault'}
                onClick={handleInitVault}
                disabled={!canInitVault}
                loading={saving}
                variant="secondary"
              />
            </div>
          )}

          {/* Zero-knowledge badges */}
          <div style={styles.badges} aria-hidden="true">
            {ZERO_KNOWLEDGE_BADGES.map((badge) => (
              <span key={badge} style={styles.badge}>{badge}</span>
            ))}
          </div>
        </div>

        {/* ─── Section 2: Identity ─── */}
        <div style={styles.sectionCard}>
          <div style={styles.sectionHeader}>
            <div style={{ ...styles.sectionIconBox, background: 'rgba(138, 43, 226, 0.08)', border: '1px solid rgba(138, 43, 226, 0.15)' }}>
              <Mic size={18} color="#8A2BE2" />
            </div>
            <div>
              <div style={styles.sectionTitle}>Identity Wallet</div>
              <div style={styles.sectionDesc}>Name and voice configuration</div>
            </div>
          </div>

          {/* Agent name */}
          <div style={styles.identityField}>
            <CyberInput
              id="agent-name"
              label="Agent Name"
              value={choices.agentName}
              onChange={(v) => updateField('agentName', v)}
              maxLength={24}
            />
            <span style={styles.nameHint}>Default: Friday — or choose your own</span>
          </div>

          {/* Voice gender */}
          <div style={styles.identityField}>
            <label id="voice-gender-label" style={styles.fieldLabel}>Voice Gender</label>
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

          {/* Voice feel grid */}
          <div style={styles.identityField}>
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
      </div>

      {error && <p role="alert" style={styles.error}>{error}</p>}

      <NextButton
        label="Continue"
        onClick={onComplete}
        disabled={!canContinue}
      />

      <p style={styles.hint}>
        {!vaultReady
          ? 'Initialize your vault before continuing. If you forget the passphrase, data cannot be recovered.'
          : 'Your agent\'s name and voice can be changed later in Settings.'}
      </p>
    </section>
  );
};

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
    maxHeight: 420,
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
  readyBox: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    padding: '12px 20px',
    background: 'rgba(34, 197, 94, 0.06)',
    border: '1px solid rgba(34, 197, 94, 0.2)',
    borderRadius: 8,
  },
  readyText: {
    fontSize: 12,
    color: 'rgba(34, 197, 94, 0.8)',
    fontFamily: "'Space Grotesk', sans-serif",
    fontWeight: 500,
  },
  vaultForm: {
    display: 'flex',
    flexDirection: 'column',
    gap: 14,
  },
  strengthContainer: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    marginTop: -6,
  },
  strengthTrack: {
    flex: 1,
    height: 3,
    borderRadius: 2,
    background: 'var(--onboarding-card-hover)',
    overflow: 'hidden',
  },
  strengthFill: {
    height: '100%',
    borderRadius: 2,
    transition: 'width 0.3s ease, background 0.3s ease',
  },
  strengthLabel: {
    fontSize: 10,
    fontWeight: 600,
    letterSpacing: '0.05em',
    fontFamily: "'Space Grotesk', sans-serif",
    flexShrink: 0,
  },
  badges: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: 6,
  },
  badge: {
    fontSize: 9,
    fontWeight: 600,
    letterSpacing: '0.08em',
    color: 'var(--text-30)',
    padding: '3px 8px',
    borderRadius: 4,
    background: 'rgba(255, 255, 255, 0.03)',
    border: '1px solid rgba(255, 255, 255, 0.05)',
    fontFamily: "'JetBrains Mono', monospace",
  },
  identityField: {
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
  error: {
    color: 'var(--accent-red)',
    fontSize: 12,
    margin: 0,
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

export default EnvironmentStep;
