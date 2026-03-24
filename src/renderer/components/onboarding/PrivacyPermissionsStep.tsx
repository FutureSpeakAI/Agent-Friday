/**
 * PrivacyPermissionsStep.tsx — Privacy & Permissions onboarding step.
 *
 * "Privacy & Permissions." — Combines sovereign vault passphrase setup,
 * privacy toggle controls (PII filtering, telemetry, local processing),
 * and memory depth selection into a single scrollable step.
 */

import React, { useState, useCallback, useEffect } from 'react';
import { Lock, ShieldCheck, Shield, Database } from 'lucide-react';
import CyberInput from './shared/CyberInput';
import NextButton from './shared/NextButton';

interface PrivacyPermissionsStepProps {
  onComplete: () => void;
  onBack?: () => void;
}

/* ── Vault strength ── */

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

/* ── Privacy toggles ── */

interface PrivacyToggle {
  key: string;
  label: string;
  description: string;
  defaultValue: boolean;
}

const PRIVACY_TOGGLES: PrivacyToggle[] = [
  { key: 'piiFiltering', label: 'PII Filtering', description: 'Automatically redact personal information from logs', defaultValue: true },
  { key: 'telemetry', label: 'Anonymous Telemetry', description: 'Help improve Agent Friday with anonymous usage data', defaultValue: false },
  { key: 'localProcessing', label: 'Local Processing Priority', description: 'Process sensitive data locally when possible', defaultValue: true },
];

/* ── Memory depth options ── */

interface MemoryOption {
  value: string;
  label: string;
  description: string;
}

const MEMORY_OPTIONS: MemoryOption[] = [
  { value: 'minimal', label: 'Minimal', description: 'Remember only essential preferences' },
  { value: 'standard', label: 'Standard', description: 'Remember preferences and recent context' },
  { value: 'comprehensive', label: 'Comprehensive', description: 'Deep memory — learns your patterns over time' },
];

/* ── Component ── */

const PrivacyPermissionsStep: React.FC<PrivacyPermissionsStepProps> = ({ onComplete, onBack }) => {
  const [fadeIn, setFadeIn] = useState(false);

  // Vault state
  const [vaultReady, setVaultReady] = useState(false);
  const [passphrase, setPassphrase] = useState('');
  const [confirm, setConfirm] = useState('');
  const [saving, setSaving] = useState(false);
  const [vaultError, setVaultError] = useState('');

  // Privacy toggles
  const [toggles, setToggles] = useState<Record<string, boolean>>(() => {
    const init: Record<string, boolean> = {};
    PRIVACY_TOGGLES.forEach((t) => { init[t.key] = t.defaultValue; });
    return init;
  });

  // Memory depth
  const [memoryDepth, setMemoryDepth] = useState('standard');

  // Load current settings on mount
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const initialized = await window.eve.vault.isInitialized();
        if (initialized && !cancelled) setVaultReady(true);
      } catch (err) {
        console.warn('[PrivacyPermissionsStep] Vault status check failed:', err);
        // Vault not initialized — user will need to create it
      }

      // Load toggle settings from full settings object
      try {
        const all = await window.eve.settings.get();
        if (!cancelled) {
          const loaded: Record<string, boolean> = {};
          for (const t of PRIVACY_TOGGLES) {
            const val = (all as Record<string, unknown>)[t.key];
            loaded[t.key] = val !== undefined && val !== null ? Boolean(val) : t.defaultValue;
          }
          setToggles(loaded);

          // Load memory depth
          const depth = (all as Record<string, unknown>)['memoryDepth'];
          if (depth) setMemoryDepth(String(depth));
        }
      } catch { /* use defaults */ }

      setTimeout(() => { if (!cancelled) setFadeIn(true); }, 100);
    })();
    return () => { cancelled = true; };
  }, []);

  // Vault strength
  const strength = calcStrength(passphrase);
  const strengthMeta = STRENGTH_META[strength];
  const mismatch = confirm.length > 0 && passphrase !== confirm;
  const tooShort = passphrase.length > 0 && passphrase.length < MIN_PASSPHRASE_LENGTH;
  const strongEnough = strength !== 'weak';
  const canInitVault = passphrase.length >= MIN_PASSPHRASE_LENGTH && strongEnough && passphrase === confirm && !saving;

  const handleInitVault = useCallback(async () => {
    if (!canInitVault) return;
    setSaving(true);
    setVaultError('');
    try {
      const result = await window.eve.vault.initializeNew(passphrase);
      if (result && !result.ok) {
        setVaultError(result.error || 'Failed to initialize vault');
        setSaving(false);
        return;
      }
      setVaultReady(true);
      setSaving(false);
    } catch (err: any) {
      setVaultError(err?.message || 'Failed to initialize vault');
      setSaving(false);
    }
  }, [passphrase, canInitVault]);

  const handleToggle = useCallback(async (key: string) => {
    const newValue = !toggles[key];
    setToggles((prev) => ({ ...prev, [key]: newValue }));
    try {
      await window.eve.settings.set(key, newValue);
    } catch { /* best-effort */ }
  }, [toggles]);

  const handleMemoryDepth = useCallback(async (value: string) => {
    setMemoryDepth(value);
    try {
      await window.eve.settings.set('memoryDepth', value);
    } catch { /* best-effort */ }
  }, []);

  return (
    <section style={{
      ...styles.container,
      opacity: fadeIn ? 1 : 0,
      transform: fadeIn ? 'translateY(0)' : 'translateY(16px)',
      transition: 'all 0.7s cubic-bezier(0.16, 1, 0.3, 1)',
    }} aria-label="Privacy and permissions">
      <div style={styles.headerBlock}>
        <h2 style={styles.heading}>Privacy &amp; Permissions.</h2>
        <p style={styles.subtitle}>
          Your data sovereignty starts here. Everything stays on your machine.
        </p>
      </div>

      {/* Scrollable sections */}
      <div style={styles.scrollArea}>

        {/* ─── Section 1: Sovereign Vault ─── */}
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

          {vaultError && <p role="alert" style={styles.error}>{vaultError}</p>}

          <div style={styles.badges} aria-hidden="true">
            {ZERO_KNOWLEDGE_BADGES.map((badge) => (
              <span key={badge} style={styles.badge}>{badge}</span>
            ))}
          </div>
        </div>

        {/* ─── Section 2: Privacy Controls ─── */}
        <div style={styles.sectionCard}>
          <div style={styles.sectionHeader}>
            <div style={{ ...styles.sectionIconBox, background: 'rgba(138, 43, 226, 0.08)', border: '1px solid rgba(138, 43, 226, 0.15)' }}>
              <Shield size={18} color="#8A2BE2" />
            </div>
            <div>
              <div style={styles.sectionTitle}>Privacy Controls</div>
              <div style={styles.sectionDesc}>Manage data handling preferences</div>
            </div>
          </div>

          {PRIVACY_TOGGLES.map((toggle) => {
            const checked = toggles[toggle.key] ?? toggle.defaultValue;
            return (
              <div
                key={toggle.key}
                style={styles.toggleRow}
                role="switch"
                aria-checked={checked}
                aria-label={toggle.label}
                tabIndex={0}
                onClick={() => handleToggle(toggle.key)}
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleToggle(toggle.key); } }}
              >
                <div style={styles.toggleText}>
                  <span style={styles.toggleLabel}>{toggle.label}</span>
                  <span style={styles.toggleDesc}>{toggle.description}</span>
                </div>
                <div style={{
                  ...styles.switchTrack,
                  background: checked ? 'var(--accent-cyan)' : 'rgba(255, 255, 255, 0.1)',
                  borderColor: checked ? 'var(--accent-cyan)' : 'rgba(255, 255, 255, 0.15)',
                }}>
                  <div style={{
                    ...styles.switchThumb,
                    transform: checked ? 'translateX(12px)' : 'translateX(0)',
                    background: checked ? '#000' : 'rgba(255, 255, 255, 0.4)',
                  }} />
                </div>
              </div>
            );
          })}
        </div>

        {/* ─── Section 3: Memory Depth ─── */}
        <div style={styles.sectionCard}>
          <div style={styles.sectionHeader}>
            <div style={{ ...styles.sectionIconBox, background: 'rgba(245, 158, 11, 0.08)', border: '1px solid rgba(245, 158, 11, 0.15)' }}>
              <Database size={18} color="#f59e0b" />
            </div>
            <div>
              <div style={styles.sectionTitle}>Memory Depth</div>
              <div style={styles.sectionDesc}>How much your agent remembers</div>
            </div>
          </div>

          <div style={styles.radioGroup} role="radiogroup" aria-label="Memory depth">
            {MEMORY_OPTIONS.map((opt) => {
              const selected = memoryDepth === opt.value;
              return (
                <div
                  key={opt.value}
                  role="radio"
                  aria-checked={selected}
                  tabIndex={0}
                  onClick={() => handleMemoryDepth(opt.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleMemoryDepth(opt.value); } }}
                  style={{
                    ...styles.radioOption,
                    borderColor: selected ? 'var(--accent-cyan-30)' : 'rgba(255, 255, 255, 0.06)',
                    background: selected ? 'var(--accent-cyan-10)' : 'transparent',
                  }}
                >
                  <div style={{
                    ...styles.radioCircle,
                    borderColor: selected ? 'var(--accent-cyan)' : 'rgba(255, 255, 255, 0.2)',
                  }}>
                    {selected && <div style={styles.radioDot} />}
                  </div>
                  <div>
                    <div style={{
                      ...styles.radioLabel,
                      color: selected ? 'var(--text-primary)' : 'var(--text-60)',
                    }}>{opt.label}</div>
                    <div style={styles.radioDesc}>{opt.description}</div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* Continue */}
      <NextButton
        label="Continue"
        onClick={onComplete}
        disabled={!vaultReady}
      />

      <p style={styles.hint}>
        {!vaultReady
          ? 'Initialize your vault before continuing.'
          : 'All settings can be changed later in Preferences.'}
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

  /* Vault */
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
  error: {
    color: 'var(--accent-red)',
    fontSize: 12,
    margin: 0,
  },

  /* Privacy toggle switches */
  toggleRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    padding: '10px 0',
    cursor: 'pointer',
    userSelect: 'none',
  },
  toggleText: {
    display: 'flex',
    flexDirection: 'column',
    gap: 2,
    flex: 1,
  },
  toggleLabel: {
    fontSize: 13,
    fontWeight: 500,
    color: 'var(--text-primary)',
    fontFamily: "'Space Grotesk', sans-serif",
  },
  toggleDesc: {
    fontSize: 11,
    color: 'var(--text-30)',
    fontFamily: "'Inter', sans-serif",
  },
  switchTrack: {
    width: 28,
    height: 16,
    borderRadius: 8,
    border: '1px solid rgba(255, 255, 255, 0.15)',
    display: 'flex',
    alignItems: 'center',
    padding: 2,
    flexShrink: 0,
    transition: 'all 0.15s ease',
    cursor: 'pointer',
  },
  switchThumb: {
    width: 10,
    height: 10,
    borderRadius: '50%',
    transition: 'all 0.15s ease',
  },

  /* Memory depth */
  radioGroup: {
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  radioOption: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    padding: '12px 16px',
    borderRadius: 8,
    border: '1px solid rgba(255, 255, 255, 0.06)',
    cursor: 'pointer',
    transition: 'all 0.15s ease',
    userSelect: 'none',
  },
  radioCircle: {
    width: 18,
    height: 18,
    borderRadius: '50%',
    border: '2px solid rgba(255, 255, 255, 0.2)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
    transition: 'border-color 0.15s ease',
  },
  radioDot: {
    width: 8,
    height: 8,
    borderRadius: '50%',
    background: 'var(--accent-cyan)',
  },
  radioLabel: {
    fontSize: 13,
    fontWeight: 500,
    fontFamily: "'Space Grotesk', sans-serif",
  },
  radioDesc: {
    fontSize: 11,
    color: 'var(--text-30)',
    fontFamily: "'Inter', sans-serif",
  },

  /* Bottom */
  hint: {
    fontSize: 10,
    color: 'var(--text-20)',
    margin: 0,
    textAlign: 'center',
    fontFamily: "'Inter', sans-serif",
    maxWidth: 400,
  },
};

export default PrivacyPermissionsStep;
