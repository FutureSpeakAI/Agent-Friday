/**
 * SovereigntyStep.tsx — Step 3: Vault passphrase setup.
 *
 * Explains data sovereignty, collects a passphrase (with confirmation),
 * and initializes the encrypted vault via window.eve.vault.initializeNew().
 */

import React, { useState, useCallback, useEffect, useRef } from 'react';
import { Lock, ShieldCheck } from 'lucide-react';

interface SovereigntyStepProps {
  onComplete: () => void;
}

const SovereigntyStep: React.FC<SovereigntyStepProps> = ({ onComplete }) => {
  const [passphrase, setPassphrase] = useState('');
  const [confirm, setConfirm] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [vaultReady, setVaultReady] = useState(false);
  const [fadeIn, setFadeIn] = useState(false);
  const skippedRef = useRef(false);

  // Check if vault is already initialized — auto-skip if so
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const initialized = await window.eve.vault.isInitialized();
        if (initialized && !cancelled) {
          setVaultReady(true);
          // Auto-advance after a brief flash so user sees "secured" state
          if (!skippedRef.current) {
            skippedRef.current = true;
            setTimeout(() => { if (!cancelled) onComplete(); }, 800);
          }
          return;
        }
      } catch { /* not initialized */ }
      setTimeout(() => { if (!cancelled) setFadeIn(true); }, 100);
    })();
    return () => { cancelled = true; };
  }, [onComplete]);

  const mismatch = confirm.length > 0 && passphrase !== confirm;
  const tooShort = passphrase.length > 0 && passphrase.length < 8;
  const canSubmit = passphrase.length >= 8 && passphrase === confirm && !saving;

  const handleSubmit = useCallback(async () => {
    if (!canSubmit) return;
    setSaving(true);
    setError('');
    try {
      await window.eve.vault.initializeNew(passphrase);
      setVaultReady(true);
      setTimeout(() => onComplete(), 600);
    } catch (err: any) {
      setError(err?.message || 'Failed to initialize vault');
      setSaving(false);
    }
  }, [passphrase, canSubmit, onComplete]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && canSubmit) handleSubmit();
  }, [canSubmit, handleSubmit]);

  return (
    <div style={{
      ...styles.container,
      opacity: fadeIn ? 1 : 0,
      transform: fadeIn ? 'translateY(0)' : 'translateY(16px)',
      transition: 'all 0.7s cubic-bezier(0.16, 1, 0.3, 1)',
    }}>
      <div style={styles.header}>
        <div style={styles.headerLine} />
        <span style={styles.headerLabel}>DATA SOVEREIGNTY</span>
        <div style={styles.headerLine} />
      </div>

      {/* Icon */}
      <div style={styles.iconWrap}>
        {vaultReady ? (
          <ShieldCheck size={36} color="#22c55e" />
        ) : (
          <Lock size={36} color="#00f0ff" />
        )}
      </div>

      {/* Explanation */}
      <div style={styles.explainer}>
        <p style={styles.explainerTitle}>Your data never leaves your machine.</p>
        <p style={styles.explainerBody}>
          Agent Friday uses an encrypted vault to store your memories, preferences,
          and conversations. Only you can unlock it with your passphrase.
        </p>
      </div>

      {vaultReady ? (
        /* Already initialized */
        <div style={styles.readyBox}>
          <ShieldCheck size={18} color="#22c55e" />
          <span style={styles.readyText}>Vault initialized and secured</span>
        </div>
      ) : (
        /* Passphrase form */
        <>
          <div style={styles.fieldGroup}>
            <label style={styles.fieldLabel}>Create Passphrase</label>
            <input
              type="password"
              value={passphrase}
              onChange={(e) => setPassphrase(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Minimum 8 characters"
              autoFocus
              style={{
                ...styles.input,
                borderColor: tooShort ? 'rgba(239, 68, 68, 0.3)' : passphrase.length >= 8 ? 'rgba(0, 240, 255, 0.25)' : 'rgba(255,255,255,0.06)',
              }}
            />
            {tooShort && <span style={styles.fieldError}>Must be at least 8 characters</span>}
          </div>

          <div style={styles.fieldGroup}>
            <label style={styles.fieldLabel}>Confirm Passphrase</label>
            <input
              type="password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Re-enter passphrase"
              style={{
                ...styles.input,
                borderColor: mismatch ? 'rgba(239, 68, 68, 0.3)' : (confirm && !mismatch) ? 'rgba(0, 240, 255, 0.25)' : 'rgba(255,255,255,0.06)',
              }}
            />
            {mismatch && <span style={styles.fieldError}>Passphrases do not match</span>}
          </div>
        </>
      )}

      {error && <p style={styles.error}>{error}</p>}

      <button
        onClick={vaultReady ? onComplete : handleSubmit}
        disabled={!vaultReady && !canSubmit}
        style={{
          ...styles.button,
          opacity: (vaultReady || canSubmit) ? 1 : 0.35,
        }}
      >
        {saving ? 'Initializing Vault...' : vaultReady ? 'Continue' : 'Initialize Vault'}
      </button>

      <p style={styles.hint}>
        If you forget this passphrase, your vault data cannot be recovered.
      </p>
    </div>
  );
};

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 24,
    maxWidth: 460,
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
  explainer: {
    textAlign: 'center',
    maxWidth: 380,
  },
  explainerTitle: {
    fontSize: 16,
    fontWeight: 500,
    color: '#F8FAFC',
    margin: '0 0 8px 0',
    fontFamily: "'Space Grotesk', sans-serif",
  },
  explainerBody: {
    fontSize: 13,
    color: 'rgba(255, 255, 255, 0.4)',
    lineHeight: 1.6,
    margin: 0,
    fontFamily: "'Inter', sans-serif",
  },
  readyBox: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    padding: '14px 28px',
    background: 'rgba(34, 197, 94, 0.06)',
    border: '1px solid rgba(34, 197, 94, 0.2)',
    borderRadius: 10,
  },
  readyText: {
    fontSize: 13,
    color: 'rgba(34, 197, 94, 0.8)',
    fontFamily: "'Space Grotesk', sans-serif",
    fontWeight: 500,
  },
  fieldGroup: {
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
    width: '100%',
    maxWidth: 360,
  },
  fieldLabel: {
    fontSize: 11,
    fontWeight: 500,
    color: 'rgba(255, 255, 255, 0.5)',
    letterSpacing: '0.05em',
    fontFamily: "'Space Grotesk', sans-serif",
  },
  input: {
    background: 'rgba(255, 255, 255, 0.03)',
    border: '1px solid rgba(255, 255, 255, 0.06)',
    borderRadius: 8,
    padding: '10px 14px',
    fontSize: 14,
    color: '#F8FAFC',
    outline: 'none',
    fontFamily: "'JetBrains Mono', monospace",
    letterSpacing: '0.05em',
    transition: 'border-color 0.2s',
  },
  fieldError: {
    fontSize: 10,
    color: 'rgba(239, 68, 68, 0.7)',
    fontFamily: "'Inter', sans-serif",
  },
  error: {
    color: '#ef4444',
    fontSize: 12,
    margin: 0,
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
  },
  hint: {
    fontSize: 10,
    color: 'rgba(255, 255, 255, 0.2)',
    margin: 0,
    textAlign: 'center',
    fontFamily: "'Inter', sans-serif",
    maxWidth: 340,
  },
};

export default SovereigntyStep;
