/**
 * VaultKeyphrase.tsx — Full-screen Sovereign Vault recovery phrase gate.
 *
 * Shown on absolute first launch BEFORE any other setup step.
 * The user must record their 12-word recovery phrase and confirm it
 * by typing it back before they can proceed to API key entry and onboarding.
 *
 * This is the founding act of the user's sovereign relationship to the system.
 * Dark, minimal design matching the app's #060B19 palette.
 */

import React, { useState, useCallback, useEffect, useRef } from 'react';

interface VaultKeyphraseProps {
  /** Called when the user has confirmed they've recorded the phrase */
  onConfirmed: () => void;
}

type Step = 'loading' | 'display' | 'confirm' | 'done';

export default function VaultKeyphrase({ onConfirmed }: VaultKeyphraseProps) {
  const [step, setStep] = useState<Step>('loading');
  const [phrase, setPhrase] = useState('');
  const [confirmInput, setConfirmInput] = useState('');
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Listen for the recovery phrase from the vault
  useEffect(() => {
    let mounted = true;

    // Try to get the recovery phrase — it may already be generated
    const tryGetPhrase = async () => {
      try {
        const p = await window.eve.vault.getRecoveryPhrase();
        if (p && mounted) {
          setPhrase(p);
          setStep('display');
        }
      } catch {
        // Vault not ready yet — wait for the event
      }
    };

    // Listen for the vault:recovery-phrase event (sent during first-time init)
    const cleanup = window.eve.vault.onRecoveryPhrase((p: string) => {
      if (mounted) {
        setPhrase(p);
        setStep('display');
      }
    });

    // Also poll in case the phrase was generated before we mounted
    tryGetPhrase();
    const pollTimer = setInterval(tryGetPhrase, 1000);

    return () => {
      mounted = false;
      cleanup();
      clearInterval(pollTimer);
    };
  }, []);

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(phrase).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }, [phrase]);

  const handleProceedToConfirm = useCallback(() => {
    setStep('confirm');
    setTimeout(() => inputRef.current?.focus(), 100);
  }, []);

  const handleConfirm = useCallback(async () => {
    const normalized = confirmInput.trim().toLowerCase().replace(/\s+/g, ' ');
    const expected = phrase.trim().toLowerCase();

    if (normalized !== expected) {
      setError('Phrase does not match. Please try again — every word matters.');
      return;
    }

    setError('');
    setStep('done');

    // Mark the phrase as shown and clear it from memory
    try {
      await window.eve.vault.markRecoveryPhraseShown();
      await window.eve.vault.clearRecoveryPhrase();
    } catch (e) {
      console.warn('[VaultKeyphrase] Failed to clear phrase:', e);
    }

    // Brief delay for the success animation, then proceed
    setTimeout(() => onConfirmed(), 1200);
  }, [confirmInput, phrase, onConfirmed]);

  const words = phrase.split(' ');

  return (
    <div style={styles.container}>
      <div style={styles.card}>
        {/* Header */}
        <div style={styles.iconRow}>
          <span style={styles.icon}>🔐</span>
        </div>
        <h1 style={styles.title}>Sovereign Vault</h1>
        <p style={styles.subtitle}>Your agent's cryptographic identity</p>

        {/* Loading state */}
        {step === 'loading' && (
          <div style={styles.section}>
            <p style={styles.bodyText}>
              Generating your vault encryption keys using high-strength key derivation.
              This takes 10–30 seconds — your keys are being forged with maximum security.
            </p>
            <div style={styles.spinner} />
            <p style={{ ...styles.bodyText, fontSize: 12, color: '#666680', textAlign: 'center' as const, marginTop: 8 }}>
              scrypt(N=2²⁰) — military-grade key stretching
            </p>
          </div>
        )}

        {/* Display the phrase */}
        {step === 'display' && (
          <div style={styles.section}>
            <p style={styles.bodyText}>
              Your 12-word recovery phrase is the <strong>only way</strong> to recover your
              agent's encrypted data if you move to a new machine. Write it down on paper
              and store it somewhere safe. It will never be shown again.
            </p>

            <div style={styles.phraseGrid}>
              {words.map((word, i) => (
                <div key={i} style={styles.wordCell}>
                  <span style={styles.wordIndex}>{i + 1}</span>
                  <span style={styles.wordText}>{word}</span>
                </div>
              ))}
            </div>

            <div style={styles.buttonRow}>
              <button
                onClick={handleCopy}
                style={{ ...styles.secondaryButton, ...(copied ? styles.copiedButton : {}) }}
              >
                {copied ? '✓ Copied' : 'Copy to clipboard'}
              </button>
              <button onClick={handleProceedToConfirm} style={styles.primaryButton}>
                I've recorded it — continue
              </button>
            </div>

            <p style={styles.warning}>
              ⚠ If you lose this phrase and change machines, your memories, settings,
              and trust data will be permanently unrecoverable.
            </p>
          </div>
        )}

        {/* Confirm the phrase */}
        {step === 'confirm' && (
          <div style={styles.section}>
            <p style={styles.bodyText}>
              Type your 12-word recovery phrase below to confirm you've recorded it.
            </p>

            <textarea
              ref={inputRef}
              value={confirmInput}
              onChange={(e) => {
                setConfirmInput(e.target.value);
                setError('');
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  handleConfirm();
                }
              }}
              placeholder="Enter your 12-word recovery phrase..."
              style={styles.textarea}
              rows={3}
              spellCheck={false}
              autoComplete="off"
            />

            {error && <p style={styles.errorText}>{error}</p>}

            <div style={styles.buttonRow}>
              <button onClick={() => setStep('display')} style={styles.secondaryButton}>
                ← Show phrase again
              </button>
              <button onClick={handleConfirm} style={styles.primaryButton}>
                Confirm phrase
              </button>
            </div>
          </div>
        )}

        {/* Success */}
        {step === 'done' && (
          <div style={styles.section}>
            <div style={styles.successIcon}>✓</div>
            <p style={styles.bodyText}>
              Vault secured. Your recovery phrase has been cleared from memory.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Styles ───────────────────────────────────────────────────────────

const styles: Record<string, React.CSSProperties> = {
  container: {
    position: 'fixed',
    inset: 0,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    background: '#060B19',
    zIndex: 9999,
    fontFamily: "'Inter', 'Segoe UI', system-ui, sans-serif",
  },
  card: {
    width: '100%',
    maxWidth: 560,
    padding: '40px 48px',
    borderRadius: 16,
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.06)',
    textAlign: 'center' as const,
  },
  iconRow: {
    marginBottom: 12,
  },
  icon: {
    fontSize: 48,
  },
  title: {
    color: '#e0e0e8',
    fontSize: 28,
    fontWeight: 700,
    margin: '0 0 6px',
    letterSpacing: '-0.02em',
  },
  subtitle: {
    color: '#a0a0b8',
    fontSize: 14,
    margin: '0 0 32px',
  },
  section: {
    textAlign: 'left' as const,
  },
  bodyText: {
    color: '#c0c0d0',
    fontSize: 14,
    lineHeight: 1.6,
    margin: '0 0 20px',
  },
  phraseGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(4, 1fr)',
    gap: 8,
    marginBottom: 24,
    padding: 16,
    background: 'rgba(0,0,0,0.3)',
    borderRadius: 12,
    border: '1px solid rgba(0,240,255,0.15)',
  },
  wordCell: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    padding: '6px 8px',
    borderRadius: 6,
    background: 'rgba(255,255,255,0.04)',
  },
  wordIndex: {
    color: '#666680',
    fontSize: 11,
    fontFamily: 'monospace',
    minWidth: 18,
  },
  wordText: {
    color: '#00f0ff',
    fontSize: 14,
    fontFamily: "'JetBrains Mono', monospace",
    fontWeight: 600,
  },
  buttonRow: {
    display: 'flex',
    gap: 12,
    justifyContent: 'center',
    marginBottom: 16,
  },
  primaryButton: {
    padding: '10px 24px',
    background: 'rgba(0,240,255,0.15)',
    border: '1px solid rgba(0,240,255,0.3)',
    borderRadius: 8,
    color: '#00f0ff',
    fontSize: 14,
    fontWeight: 600,
    cursor: 'pointer',
    transition: 'all 0.2s',
  },
  secondaryButton: {
    padding: '10px 24px',
    background: 'rgba(255,255,255,0.05)',
    border: '1px solid rgba(255,255,255,0.1)',
    borderRadius: 8,
    color: '#a0a0b8',
    fontSize: 14,
    cursor: 'pointer',
    transition: 'all 0.2s',
  },
  copiedButton: {
    color: '#22c55e',
    borderColor: 'rgba(34,197,94,0.3)',
  },
  warning: {
    color: '#f59e0b',
    fontSize: 12,
    lineHeight: 1.5,
    margin: 0,
    textAlign: 'center' as const,
    opacity: 0.8,
  },
  textarea: {
    width: '100%',
    padding: '12px 16px',
    background: 'rgba(0,0,0,0.3)',
    border: '1px solid rgba(255,255,255,0.1)',
    borderRadius: 8,
    color: '#e0e0e8',
    fontSize: 14,
    fontFamily: "'JetBrains Mono', monospace",
    resize: 'none' as const,
    outline: 'none',
    marginBottom: 12,
    boxSizing: 'border-box' as const,
  },
  errorText: {
    color: '#ef4444',
    fontSize: 13,
    margin: '0 0 12px',
  },
  successIcon: {
    fontSize: 48,
    color: '#22c55e',
    textAlign: 'center' as const,
    marginBottom: 16,
  },
  spinner: {
    width: 24,
    height: 24,
    border: '3px solid rgba(0,240,255,0.15)',
    borderTop: '3px solid #00f0ff',
    borderRadius: '50%',
    animation: 'spin 1s linear infinite',
    margin: '20px auto',
  },
};
