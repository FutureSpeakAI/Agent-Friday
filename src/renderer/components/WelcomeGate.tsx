/**
 * WelcomeGate.tsx — Full-screen API key entry gate.
 *
 * Shown on first launch before anything else loads.
 * Requires both Gemini and Anthropic API keys before proceeding.
 * Dark, minimal design matching the app's #060B19 palette.
 */

import React, { useState, useCallback } from 'react';

interface WelcomeGateProps {
  onKeysReady: () => void;
}

const WelcomeGate: React.FC<WelcomeGateProps> = ({ onKeysReady }) => {
  const [geminiKey, setGeminiKey] = useState('');
  const [anthropicKey, setAnthropicKey] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const canProceed = geminiKey.trim().length > 0 && anthropicKey.trim().length > 0 && !saving;

  const handleBegin = useCallback(async () => {
    if (!canProceed) return;
    setSaving(true);
    setError('');

    try {
      await window.eve.settings.setApiKey('gemini', geminiKey.trim());
      await window.eve.settings.setApiKey('anthropic', anthropicKey.trim());
      onKeysReady();
    } catch (err: any) {
      setError(err?.message || 'Failed to save API keys');
      setSaving(false);
    }
  }, [geminiKey, anthropicKey, canProceed, onKeysReady]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && canProceed) handleBegin();
    },
    [canProceed, handleBegin]
  );

  return (
    <div style={styles.overlay}>
      <div style={styles.container}>
        {/* Title */}
        <div style={styles.titleBlock}>
          <div style={styles.logo}>◈</div>
          <h1 style={styles.title}>Agent Friday</h1>
          <div style={styles.byLine}>by FutureSpeak.AI</div>
          <p style={styles.subtitle}>Enter your API keys to begin</p>
        </div>

        {/* Key inputs */}
        <div style={styles.fields}>
          <div style={styles.field}>
            <label style={styles.label}>Gemini API Key</label>
            <input
              type="password"
              value={geminiKey}
              onChange={(e) => setGeminiKey(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="AIza..."
              style={styles.input}
              autoFocus
            />
          </div>

          <div style={styles.field}>
            <label style={styles.label}>Anthropic API Key</label>
            <input
              type="password"
              value={anthropicKey}
              onChange={(e) => setAnthropicKey(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="sk-ant-..."
              style={styles.input}
            />
          </div>
        </div>

        {/* Error */}
        {error && <p style={styles.error}>{error}</p>}

        {/* Begin button */}
        <button
          onClick={handleBegin}
          disabled={!canProceed}
          style={{
            ...styles.button,
            opacity: canProceed ? 1 : 0.3,
            cursor: canProceed ? 'pointer' : 'default',
          }}
        >
          {saving ? 'Initializing...' : 'Begin'}
        </button>

        <p style={styles.hint}>
          Keys are stored locally and never sent to third parties.
        </p>
      </div>
    </div>
  );
};

/* ── Inline styles ─────────────────────────────────────────────────── */

const styles: Record<string, React.CSSProperties> = {
  overlay: {
    position: 'fixed',
    inset: 0,
    zIndex: 200,
    background: '#060B19',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontFamily: "'Inter', -apple-system, sans-serif",
  },
  container: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: '2rem',
    maxWidth: '400px',
    width: '100%',
    padding: '0 2rem',
  },
  titleBlock: {
    textAlign: 'center',
    marginBottom: '0.5rem',
  },
  logo: {
    fontSize: '2.5rem',
    color: '#8B9FFF',
    marginBottom: '0.75rem',
    opacity: 0.8,
  },
  title: {
    fontSize: '1.5rem',
    fontWeight: 300,
    color: '#E0E6F0',
    margin: 0,
    letterSpacing: '0.05em',
  },
  byLine: {
    fontSize: '0.7rem',
    fontWeight: 500,
    letterSpacing: '0.06em',
    color: 'rgba(168, 85, 247, 0.5)',
    marginTop: '0.25rem',
  },
  subtitle: {
    fontSize: '0.85rem',
    color: '#6B7A99',
    marginTop: '0.5rem',
  },
  fields: {
    display: 'flex',
    flexDirection: 'column',
    gap: '1.25rem',
    width: '100%',
  },
  field: {
    display: 'flex',
    flexDirection: 'column',
    gap: '0.4rem',
  },
  label: {
    fontSize: '0.75rem',
    color: '#8B9FFF',
    textTransform: 'uppercase' as const,
    letterSpacing: '0.08em',
    fontWeight: 500,
  },
  input: {
    background: 'rgba(255, 255, 255, 0.04)',
    border: '1px solid rgba(139, 159, 255, 0.15)',
    borderRadius: '8px',
    padding: '0.75rem 1rem',
    fontSize: '0.9rem',
    color: '#E0E6F0',
    outline: 'none',
    fontFamily: "'JetBrains Mono', monospace",
    letterSpacing: '0.02em',
    transition: 'border-color 0.2s',
  },
  error: {
    color: '#FF6B6B',
    fontSize: '0.8rem',
    margin: 0,
  },
  button: {
    background: 'rgba(139, 159, 255, 0.12)',
    border: '1px solid rgba(139, 159, 255, 0.3)',
    borderRadius: '8px',
    padding: '0.75rem 3rem',
    fontSize: '0.9rem',
    color: '#8B9FFF',
    fontWeight: 500,
    letterSpacing: '0.05em',
    transition: 'all 0.2s',
    fontFamily: "'Inter', sans-serif",
  },
  hint: {
    fontSize: '0.7rem',
    color: '#4A5568',
    margin: 0,
  },
};

export default WelcomeGate;
