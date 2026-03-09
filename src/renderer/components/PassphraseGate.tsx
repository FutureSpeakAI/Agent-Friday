/**
 * PassphraseGate.tsx — Full-screen Sovereign Vault passphrase gate.
 *
 * Sovereign Vault v2: The passphrase is the ONE AND ONLY root of trust.
 * No recovery phrase. No machine binding. No OS credential store.
 *
 * Two modes:
 *   1. First-time — create passphrase (≥8 words), confirm it, acknowledge warnings
 *   2. Returning — enter passphrase to unlock
 *
 * The user must understand: forget the passphrase, lose everything. By design.
 * Dark, minimal design matching the app's #060B19 palette.
 */

import React, { useState, useCallback, useEffect, useRef } from 'react';

interface PassphraseGateProps {
  /** Called when the vault is unlocked and Phase B boot is complete */
  onUnlocked: () => void;
}

type Mode = 'loading' | 'create' | 'unlock';
type CreateStep = 'enter' | 'confirm' | 'warning' | 'initializing';

const MIN_WORDS = 8;

/** Progressive delay after failed passphrase attempts (seconds). */
function getCooldownSeconds(attempts: number): number {
  if (attempts >= 10) return 60;
  if (attempts >= 5) return 15;
  if (attempts >= 3) return 5;
  return 0;
}

export default function PassphraseGate({ onUnlocked }: PassphraseGateProps) {
  const [mode, setMode] = useState<Mode>('loading');
  const [createStep, setCreateStep] = useState<CreateStep>('enter');
  const [passphrase, setPassphrase] = useState('');
  const [confirmPassphrase, setConfirmPassphrase] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [warningAcknowledged, setWarningAcknowledged] = useState(false);
  const [failedAttempts, setFailedAttempts] = useState(0);
  const [cooldownRemaining, setCooldownRemaining] = useState(0);
  const [showResetConfirm, setShowResetConfirm] = useState(false);
  const [resetting, setResetting] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const cooldownRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Determine mode on mount — first-time or returning user
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const initialized = await window.eve.vault.isInitialized();
        if (cancelled) return;
        if (initialized) {
          // Returning user — check if already unlocked (edge case)
          const unlocked = await window.eve.vault.isUnlocked();
          if (cancelled) return;
          if (unlocked) {
            onUnlocked();
            return;
          }
          setMode('unlock');
        } else {
          setMode('create');
        }
      } catch (err) {
        console.warn('[PassphraseGate] Vault status check failed:', err);
        if (!cancelled) setMode('create'); // fallback to create
      }
    })();
    return () => { cancelled = true; };
  }, [onUnlocked]);

  // Listen for boot-complete signal from main process
  useEffect(() => {
    const cleanup = window.eve.vault.onBootComplete(() => {
      onUnlocked();
    });
    return cleanup;
  }, [onUnlocked]);

  // Clean up cooldown timer on unmount
  useEffect(() => {
    return () => {
      if (cooldownRef.current) clearInterval(cooldownRef.current);
    };
  }, []);

  // Focus input when mode/step changes
  useEffect(() => {
    setTimeout(() => inputRef.current?.focus(), 100);
  }, [mode, createStep]);

  const wordCount = passphrase.trim().split(/\s+/).filter(Boolean).length;
  const isValidLength = wordCount >= MIN_WORDS;

  // ── First-time: proceed from entry to confirmation ──
  const handleCreateNext = useCallback(() => {
    setError('');
    if (!isValidLength) {
      setError(`Passphrase must be at least ${MIN_WORDS} words (currently ${wordCount})`);
      return;
    }
    setCreateStep('confirm');
  }, [isValidLength, wordCount]);

  // ── First-time: confirm passphrase matches ──
  const handleConfirmNext = useCallback(() => {
    setError('');
    if (confirmPassphrase.trim() !== passphrase.trim()) {
      setError('Passphrases do not match. Please try again.');
      return;
    }
    setCreateStep('warning');
  }, [confirmPassphrase, passphrase]);

  // ── First-time: initialize vault after warning acknowledged ──
  const handleInitialize = useCallback(async () => {
    if (!warningAcknowledged) return;
    setBusy(true);
    setError('');
    setCreateStep('initializing');
    try {
      const result = await window.eve.vault.initializeNew(passphrase.trim());
      if (!result.ok) {
        setError(result.error || 'Vault initialization failed');
        setCreateStep('warning');
        setBusy(false);
        return;
      }
      // Boot-complete event will trigger onUnlocked via the listener above
    } catch (err: any) {
      setError(err?.message || 'Vault initialization failed');
      setCreateStep('warning');
      setBusy(false);
    }
  }, [warningAcknowledged, passphrase]);

  // ── Start cooldown timer after failed attempts ──
  const startCooldown = useCallback((seconds: number) => {
    if (cooldownRef.current) clearInterval(cooldownRef.current);
    setCooldownRemaining(seconds);
    cooldownRef.current = setInterval(() => {
      setCooldownRemaining((prev) => {
        if (prev <= 1) {
          if (cooldownRef.current) clearInterval(cooldownRef.current);
          cooldownRef.current = null;
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
  }, []);

  // ── Returning: unlock vault ──
  const handleUnlock = useCallback(async () => {
    if (!passphrase.trim() || cooldownRemaining > 0) return;
    setBusy(true);
    setError('');
    try {
      const result = await window.eve.vault.unlock(passphrase.trim());
      if (!result.ok) {
        const newAttempts = failedAttempts + 1;
        setFailedAttempts(newAttempts);
        const cooldown = getCooldownSeconds(newAttempts);
        if (cooldown > 0) {
          setError(`Incorrect passphrase (attempt ${newAttempts}). Please wait ${cooldown}s before trying again.`);
          startCooldown(cooldown);
        } else {
          setError(result.error || 'Incorrect passphrase');
        }
        setBusy(false);
        return;
      }
      // Boot-complete event will trigger onUnlocked via the listener above
    } catch (err: any) {
      setError(err?.message || 'Unlock failed');
      setBusy(false);
    }
  }, [passphrase, failedAttempts, cooldownRemaining, startCooldown]);

  // ── Start Fresh: wipe vault and relaunch ──
  const handleStartFresh = useCallback(async () => {
    setResetting(true);
    try {
      await window.eve.vault.resetAll();
      // App will relaunch — this code won't continue
    } catch (err: any) {
      console.error('[PassphraseGate] Reset failed:', err);
      setError(err?.message || 'Reset failed');
      setResetting(false);
      setShowResetConfirm(false);
    }
  }, []);

  // ── Key handler for Enter ──
  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (mode === 'unlock') handleUnlock();
      else if (createStep === 'enter') handleCreateNext();
      else if (createStep === 'confirm') handleConfirmNext();
    }
  }, [mode, createStep, handleUnlock, handleCreateNext, handleConfirmNext]);

  // ── Render ──

  if (mode === 'loading') {
    return (
      <div style={styles.container}>
        <div style={styles.card}>
          <div style={styles.spinner} />
          <p style={styles.subtitle}>Checking vault status…</p>
        </div>
      </div>
    );
  }

  // ────────────────────────────────────────────────────
  //  UNLOCK MODE (returning user)
  // ────────────────────────────────────────────────────

  if (mode === 'unlock') {
    return (
      <div style={styles.container}>
        <div style={styles.card}>
          <div style={styles.lockIcon}>🔒</div>
          <h1 style={styles.title}>Sovereign Vault</h1>
          <p style={styles.subtitle}>Enter your passphrase to unlock</p>

          <textarea
            ref={inputRef}
            value={passphrase}
            onChange={(e) => { setPassphrase(e.target.value); setError(''); }}
            onKeyDown={handleKeyDown}
            placeholder="Enter your passphrase…"
            rows={2}
            style={styles.textarea}
            disabled={busy}
            autoFocus
          />

          {error && <p style={styles.errorText}>{error}</p>}

          {busy ? (
            <div style={styles.busyRow}>
              <div style={styles.spinner} />
              <p style={styles.busyText}>Unlocking vault — deriving keys &amp; restoring state…</p>
              <p style={{ ...styles.busyText, fontSize: 11, opacity: 0.6 }}>This takes a few seconds (by design)</p>
            </div>
          ) : cooldownRemaining > 0 ? (
            <button style={{ ...styles.primaryButton, opacity: 0.3, cursor: 'not-allowed' }} disabled>
              Wait {cooldownRemaining}s…
            </button>
          ) : (
            <button style={styles.primaryButton} onClick={handleUnlock} disabled={!passphrase.trim()}>
              Unlock
            </button>
          )}

          {/* Start Fresh — for users who forgot passphrase or reinstalled */}
          <div style={styles.resetSection}>
            <button
              style={styles.resetLink}
              onClick={() => setShowResetConfirm(true)}
              disabled={busy}
            >
              Forgot passphrase? Start fresh →
            </button>
          </div>
        </div>

        {/* ── Reset confirmation overlay ── */}
        {showResetConfirm && (
          <div style={styles.warningOverlay}>
            <div style={styles.warningCard}>
              <div style={styles.warningIcon}>💀</div>
              <h1 style={styles.warningTitle}>ERASE EVERYTHING?</h1>

              <div style={styles.warningBody}>
                <p style={styles.warningLine}>This will permanently destroy:</p>
                <p style={styles.warningLineEmphasis}>
                  Your agent's identity, memories, personality,
                  all encrypted data, and every secret stored in the vault.
                </p>
                <p style={styles.warningLine}>The app will relaunch with a blank slate.</p>
                <p style={styles.warningLineDesign}>There is no undo.</p>
              </div>

              {resetting ? (
                <div style={styles.busyRow}>
                  <div style={styles.spinner} />
                  <p style={styles.busyText}>Erasing vault and relaunching…</p>
                </div>
              ) : (
                <div style={styles.buttonRow}>
                  <button style={styles.secondaryButton} onClick={() => setShowResetConfirm(false)}>
                    ← Cancel
                  </button>
                  <button style={styles.dangerButton} onClick={handleStartFresh}>
                    Erase &amp; Start Fresh
                  </button>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    );
  }

  // ────────────────────────────────────────────────────
  //  CREATE MODE (first-time user)
  // ────────────────────────────────────────────────────

  // Step 1: Enter passphrase
  if (createStep === 'enter') {
    return (
      <div style={styles.container}>
        <div style={styles.card}>
          <div style={styles.lockIcon}>🛡️</div>
          <h1 style={styles.title}>Create Your Passphrase</h1>
          <p style={styles.subtitle}>
            Your passphrase is the <strong>only key</strong> to your agent's identity, memories, and secrets.
            Choose a memorable sentence of at least {MIN_WORDS} words.
          </p>

          <textarea
            ref={inputRef}
            value={passphrase}
            onChange={(e) => { setPassphrase(e.target.value); setError(''); }}
            onKeyDown={handleKeyDown}
            placeholder="e.g. the old lighthouse keeper plays chess with seagulls every morning"
            rows={3}
            style={styles.textarea}
            autoFocus
          />

          <div style={styles.wordCounter}>
            <span style={{ color: isValidLength ? '#22c55e' : '#f59e0b' }}>
              {wordCount} / {MIN_WORDS} words {isValidLength ? '✓' : ''}
            </span>
          </div>

          {error && <p style={styles.errorText}>{error}</p>}

          <p style={styles.warningInline}>
            ⚠ If you forget this passphrase, all your agent's data is permanently and irreversibly lost.
            There is no recovery mechanism.
          </p>

          <button
            style={{ ...styles.primaryButton, opacity: isValidLength ? 1 : 0.4 }}
            onClick={handleCreateNext}
            disabled={!isValidLength}
          >
            Continue
          </button>
        </div>
      </div>
    );
  }

  // Step 2: Confirm passphrase
  if (createStep === 'confirm') {
    return (
      <div style={styles.container}>
        <div style={styles.card}>
          <div style={styles.lockIcon}>🔑</div>
          <h1 style={styles.title}>Confirm Your Passphrase</h1>
          <p style={styles.subtitle}>
            Type your passphrase again to confirm you've memorized it.
          </p>

          <textarea
            ref={inputRef}
            value={confirmPassphrase}
            onChange={(e) => { setConfirmPassphrase(e.target.value); setError(''); }}
            onKeyDown={handleKeyDown}
            placeholder="Re-enter your passphrase…"
            rows={3}
            style={styles.textarea}
            autoFocus
          />

          {error && <p style={styles.errorText}>{error}</p>}

          <div style={styles.buttonRow}>
            <button style={styles.secondaryButton} onClick={() => { setCreateStep('enter'); setError(''); }}>
              ← Back
            </button>
            <button style={styles.primaryButton} onClick={handleConfirmNext}>
              Confirm
            </button>
          </div>
        </div>
      </div>
    );
  }

  // Step 3: Final warning (the "founding act" of sovereignty)
  if (createStep === 'warning') {
    return (
      <div style={styles.container}>
        <div style={styles.warningOverlay}>
          <div style={styles.warningCard}>
            <div style={styles.warningIcon}>⚠️</div>
            <h1 style={styles.warningTitle}>THIS IS YOUR ONLY KEY</h1>

            <div style={styles.warningBody}>
              <p style={styles.warningLine}>There is no "forgot password."</p>
              <p style={styles.warningLine}>There is no support team.</p>
              <p style={styles.warningLine}>There is no backdoor.</p>
              <p style={styles.warningLineEmphasis}>
                If you lose this passphrase, your agent's identity, memories,
                and all private data are gone forever.
              </p>
              <p style={styles.warningLineDesign}>This is by design.</p>
            </div>

            <label style={styles.checkboxLabel}>
              <input
                type="checkbox"
                checked={warningAcknowledged}
                onChange={(e) => setWarningAcknowledged(e.target.checked)}
                style={styles.checkbox}
              />
              <span>I understand that forgetting my passphrase means permanent, total data loss</span>
            </label>

            {error && <p style={styles.errorText}>{error}</p>}

            <div style={styles.buttonRow}>
              <button style={styles.secondaryButton} onClick={() => { setCreateStep('confirm'); setError(''); }}>
                ← Back
              </button>
              <button
                style={{
                  ...styles.dangerButton,
                  opacity: warningAcknowledged ? 1 : 0.3,
                  cursor: warningAcknowledged ? 'pointer' : 'not-allowed',
                }}
                onClick={handleInitialize}
                disabled={!warningAcknowledged}
              >
                Initialize Vault
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // Step 4: Initializing (Argon2id derivation in progress)
  if (createStep === 'initializing') {
    return (
      <div style={styles.container}>
        <div style={styles.card}>
          <div style={styles.spinner} />
          <h1 style={styles.title}>Initializing Sovereign Vault</h1>
          <p style={styles.subtitle}>
            Deriving cryptographic keys from your passphrase…
          </p>
          <p style={styles.busyText}>This takes a few seconds (by design — it makes brute-force attacks infeasible).</p>
          {error && <p style={styles.errorText}>{error}</p>}
        </div>
      </div>
    );
  }

  return null;
}

// ── Styles ─────────────────────────────────────────────────────────────

const styles: Record<string, React.CSSProperties> = {
  container: {
    position: 'fixed',
    inset: 0,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    background: '#060B19',
    zIndex: 10000,
    padding: 24,
  },
  card: {
    maxWidth: 520,
    width: '100%',
    background: 'rgba(10, 18, 40, 0.95)',
    border: '1px solid rgba(0, 240, 255, 0.08)',
    borderRadius: 16,
    padding: '40px 36px',
    textAlign: 'center' as const,
    boxShadow: '0 0 60px rgba(0, 240, 255, 0.04)',
  },
  lockIcon: {
    fontSize: 48,
    marginBottom: 16,
    textAlign: 'center' as const,
  },
  title: {
    color: '#e0e0e8',
    fontSize: 22,
    fontWeight: 700,
    margin: '0 0 8px',
    letterSpacing: '0.01em',
  },
  subtitle: {
    color: '#a0a0b8',
    fontSize: 14,
    lineHeight: 1.6,
    margin: '0 0 24px',
  },
  textarea: {
    width: '100%',
    padding: '12px 16px',
    background: 'rgba(0, 0, 0, 0.3)',
    border: '1px solid rgba(255, 255, 255, 0.1)',
    borderRadius: 8,
    color: '#e0e0e8',
    fontSize: 14,
    fontFamily: "'JetBrains Mono', monospace",
    resize: 'none' as const,
    outline: 'none',
    marginBottom: 12,
    boxSizing: 'border-box' as const,
  },
  wordCounter: {
    fontSize: 13,
    fontFamily: "'JetBrains Mono', monospace",
    marginBottom: 16,
    textAlign: 'right' as const,
  },
  warningInline: {
    color: '#f59e0b',
    fontSize: 12,
    lineHeight: 1.5,
    margin: '0 0 20px',
    textAlign: 'center' as const,
    opacity: 0.85,
  },
  primaryButton: {
    padding: '10px 28px',
    background: 'rgba(0, 240, 255, 0.15)',
    border: '1px solid rgba(0, 240, 255, 0.3)',
    borderRadius: 8,
    color: '#00f0ff',
    fontSize: 14,
    fontWeight: 600,
    cursor: 'pointer',
    transition: 'all 0.2s',
  },
  secondaryButton: {
    padding: '10px 24px',
    background: 'rgba(255, 255, 255, 0.05)',
    border: '1px solid rgba(255, 255, 255, 0.1)',
    borderRadius: 8,
    color: '#a0a0b8',
    fontSize: 14,
    cursor: 'pointer',
    transition: 'all 0.2s',
  },
  dangerButton: {
    padding: '10px 28px',
    background: 'rgba(239, 68, 68, 0.15)',
    border: '1px solid rgba(239, 68, 68, 0.3)',
    borderRadius: 8,
    color: '#ef4444',
    fontSize: 14,
    fontWeight: 600,
    cursor: 'pointer',
    transition: 'all 0.2s',
  },
  buttonRow: {
    display: 'flex',
    gap: 12,
    justifyContent: 'center',
    marginTop: 8,
  },
  errorText: {
    color: '#ef4444',
    fontSize: 13,
    margin: '0 0 12px',
  },
  busyRow: {
    display: 'flex',
    flexDirection: 'column' as const,
    alignItems: 'center',
    gap: 8,
  },
  busyText: {
    color: '#a0a0b8',
    fontSize: 13,
    margin: 0,
  },
  spinner: {
    width: 24,
    height: 24,
    border: '3px solid rgba(0, 240, 255, 0.15)',
    borderTop: '3px solid #00f0ff',
    borderRadius: '50%',
    animation: 'spin 1s linear infinite',
    margin: '12px auto',
  },
  // ── Warning overlay (the "founding act") ──
  warningOverlay: {
    position: 'fixed' as const,
    inset: 0,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    background: 'rgba(6, 11, 25, 0.97)',
    zIndex: 10001,
    padding: 24,
  },
  warningCard: {
    maxWidth: 560,
    width: '100%',
    background: 'rgba(20, 10, 10, 0.95)',
    border: '1px solid rgba(239, 68, 68, 0.2)',
    borderRadius: 16,
    padding: '48px 40px',
    textAlign: 'center' as const,
    boxShadow: '0 0 80px rgba(239, 68, 68, 0.06)',
  },
  warningIcon: {
    fontSize: 56,
    marginBottom: 16,
  },
  warningTitle: {
    color: '#ef4444',
    fontSize: 26,
    fontWeight: 800,
    letterSpacing: '0.04em',
    margin: '0 0 28px',
  },
  warningBody: {
    marginBottom: 28,
  },
  warningLine: {
    color: '#e0e0e8',
    fontSize: 16,
    lineHeight: 2,
    margin: 0,
  },
  warningLineEmphasis: {
    color: '#f59e0b',
    fontSize: 15,
    lineHeight: 1.7,
    margin: '16px 0',
    fontWeight: 600,
  },
  warningLineDesign: {
    color: '#a0a0b8',
    fontSize: 14,
    fontStyle: 'italic',
    margin: '12px 0 0',
  },
  checkboxLabel: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 10,
    color: '#e0e0e8',
    fontSize: 14,
    lineHeight: 1.5,
    textAlign: 'left' as const,
    marginBottom: 24,
    cursor: 'pointer',
  },
  checkbox: {
    marginTop: 3,
    accentColor: '#ef4444',
    width: 16,
    height: 16,
    flexShrink: 0,
  },
  // ── Start Fresh link ──
  resetSection: {
    marginTop: 24,
    paddingTop: 16,
    borderTop: '1px solid rgba(255, 255, 255, 0.05)',
  },
  resetLink: {
    background: 'none',
    border: 'none',
    color: '#6b7280',
    fontSize: 12,
    cursor: 'pointer',
    textDecoration: 'none',
    transition: 'color 0.2s',
    padding: 0,
  },
};
