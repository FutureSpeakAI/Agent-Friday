/**
 * WelcomeGate.tsx — Full-screen API key entry gate.
 *
 * Shown on first launch before anything else loads.
 * Collects all API keys (required + optional) with clear explanations.
 * Dark, minimal design matching the app's #060B19 palette.
 */

import React, { useState, useCallback } from 'react';

interface WelcomeGateProps {
  onKeysReady: () => void;
}

interface KeyConfig {
  id: 'gemini' | 'anthropic' | 'elevenlabs' | 'firecrawl' | 'perplexity' | 'openai';
  label: string;
  placeholder: string;
  required: boolean;
  description: string;
}

const KEY_CONFIGS: KeyConfig[] = [
  {
    id: 'gemini',
    label: 'Google Gemini',
    placeholder: 'AIza...',
    required: true,
    description: 'Voice interaction, search, and embeddings',
  },
  {
    id: 'anthropic',
    label: 'Anthropic Claude',
    placeholder: 'sk-ant-...',
    required: true,
    description: 'Deep reasoning, memory analysis, and profiling',
  },
  {
    id: 'elevenlabs',
    label: 'ElevenLabs',
    placeholder: 'sk_...',
    required: false,
    description: 'Distinct voices for background agents (Atlas, Nova, Cipher)',
  },
  {
    id: 'firecrawl',
    label: 'Firecrawl',
    placeholder: 'fc-...',
    required: false,
    description: 'Web scraping and deep content extraction',
  },
  {
    id: 'perplexity',
    label: 'Perplexity',
    placeholder: 'pplx-...',
    required: false,
    description: 'Web search with citations and source links',
  },
  {
    id: 'openai',
    label: 'OpenAI',
    placeholder: 'sk-...',
    required: false,
    description: 'DALL-E image generation, GPT models, TTS',
  },
];

const WelcomeGate: React.FC<WelcomeGateProps> = ({ onKeysReady }) => {
  const [keys, setKeys] = useState<Record<string, string>>({
    gemini: '',
    anthropic: '',
    elevenlabs: '',
    firecrawl: '',
    perplexity: '',
    openai: '',
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const requiredFilled = KEY_CONFIGS
    .filter((k) => k.required)
    .every((k) => keys[k.id].trim().length > 0);

  const canProceed = requiredFilled && !saving;

  const updateKey = useCallback((id: string, value: string) => {
    setKeys((prev) => ({ ...prev, [id]: value }));
  }, []);

  const handleBegin = useCallback(async () => {
    if (!canProceed) return;
    setSaving(true);
    setError('');

    try {
      // Save all non-empty keys
      for (const config of KEY_CONFIGS) {
        const value = keys[config.id].trim();
        if (value) {
          await window.eve.settings.setApiKey(config.id, value);
        }
      }
      onKeysReady();
    } catch (err: any) {
      setError(err?.message || 'Failed to save API keys');
      setSaving(false);
    }
  }, [keys, canProceed, onKeysReady]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && canProceed) handleBegin();
    },
    [canProceed, handleBegin],
  );

  const requiredKeys = KEY_CONFIGS.filter((k) => k.required);
  const optionalKeys = KEY_CONFIGS.filter((k) => !k.required);

  return (
    <div style={styles.overlay}>
      <div style={styles.container}>
        {/* Title */}
        <div style={styles.titleBlock}>
          <div style={styles.logo}>◈</div>
          <h1 style={styles.title}>Agent Friday</h1>
          <div style={styles.byLine}>by FutureSpeak.AI</div>
        </div>

        {/* Explanation */}
        <div style={styles.explainer}>
          <p style={styles.explainerText}>
            Agent Friday is a voice-first AI companion that lives on your desktop.
            It needs API keys to connect to the AI services that power its voice,
            reasoning, and capabilities.
          </p>
          <p style={styles.explainerDetail}>
            The two required keys give you the core experience — voice conversation
            and deep reasoning. The optional keys unlock additional capabilities
            like distinct agent voices, web scraping, image generation, and more.
            You can always add or change these later in Settings.
          </p>
        </div>

        {/* Required keys section */}
        <div style={styles.section}>
          <div style={styles.sectionHeader}>
            <span style={styles.sectionLabel}>Required</span>
            <span style={styles.sectionLine} />
          </div>
          <div style={styles.fields}>
            {requiredKeys.map((config, i) => (
              <div key={config.id} style={styles.field}>
                <div style={styles.labelRow}>
                  <label style={styles.label}>{config.label}</label>
                  <span style={styles.required}>Required</span>
                </div>
                <input
                  type="password"
                  value={keys[config.id]}
                  onChange={(e) => updateKey(config.id, e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder={config.placeholder}
                  style={{
                    ...styles.input,
                    borderColor: keys[config.id].trim()
                      ? 'rgba(0, 229, 255, 0.3)'
                      : 'rgba(139, 159, 255, 0.15)',
                  }}
                  autoFocus={i === 0}
                />
                <span style={styles.description}>{config.description}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Optional keys section */}
        <div style={styles.section}>
          <div style={styles.sectionHeader}>
            <span style={{ ...styles.sectionLabel, color: '#6B7A99' }}>Optional</span>
            <span style={styles.sectionLine} />
          </div>
          <div style={styles.fields}>
            {optionalKeys.map((config) => (
              <div key={config.id} style={styles.field}>
                <div style={styles.labelRow}>
                  <label style={{ ...styles.label, color: '#6B7A99' }}>{config.label}</label>
                </div>
                <input
                  type="password"
                  value={keys[config.id]}
                  onChange={(e) => updateKey(config.id, e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder={config.placeholder}
                  style={{
                    ...styles.input,
                    borderColor: keys[config.id].trim()
                      ? 'rgba(0, 229, 255, 0.3)'
                      : 'rgba(255, 255, 255, 0.06)',
                  }}
                />
                <span style={styles.description}>{config.description}</span>
              </div>
            ))}
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
          All keys are stored locally on your machine and never shared with third parties.
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
    alignItems: 'flex-start',
    justifyContent: 'center',
    fontFamily: "'Inter', -apple-system, sans-serif",
    overflowY: 'auto',
    paddingTop: '3vh',
    paddingBottom: '3vh',
  },
  container: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: '1.5rem',
    maxWidth: '480px',
    width: '100%',
    padding: '0 2rem',
  },
  titleBlock: {
    textAlign: 'center',
    marginBottom: '0.25rem',
  },
  logo: {
    fontSize: '2rem',
    color: '#8B9FFF',
    marginBottom: '0.5rem',
    opacity: 0.8,
  },
  title: {
    fontSize: '1.4rem',
    fontWeight: 300,
    color: '#E0E6F0',
    margin: 0,
    letterSpacing: '0.05em',
  },
  byLine: {
    fontSize: '0.65rem',
    fontWeight: 500,
    letterSpacing: '0.06em',
    color: 'rgba(168, 85, 247, 0.5)',
    marginTop: '0.2rem',
  },
  explainer: {
    textAlign: 'center',
    maxWidth: '420px',
  },
  explainerText: {
    fontSize: '0.82rem',
    color: '#9AA5B8',
    lineHeight: 1.6,
    margin: '0 0 0.5rem 0',
  },
  explainerDetail: {
    fontSize: '0.72rem',
    color: '#5A6577',
    lineHeight: 1.5,
    margin: 0,
  },
  section: {
    width: '100%',
  },
  sectionHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: '0.75rem',
    marginBottom: '0.75rem',
  },
  sectionLabel: {
    fontSize: '0.65rem',
    fontWeight: 600,
    color: '#8B9FFF',
    textTransform: 'uppercase' as const,
    letterSpacing: '0.1em',
    whiteSpace: 'nowrap' as const,
  },
  sectionLine: {
    flex: 1,
    height: '1px',
    background: 'rgba(139, 159, 255, 0.1)',
  },
  fields: {
    display: 'flex',
    flexDirection: 'column',
    gap: '0.9rem',
    width: '100%',
  },
  field: {
    display: 'flex',
    flexDirection: 'column',
    gap: '0.3rem',
  },
  labelRow: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  label: {
    fontSize: '0.72rem',
    color: '#8B9FFF',
    textTransform: 'uppercase' as const,
    letterSpacing: '0.08em',
    fontWeight: 500,
  },
  required: {
    fontSize: '0.6rem',
    color: 'rgba(255, 107, 107, 0.6)',
    fontWeight: 500,
    letterSpacing: '0.05em',
  },
  input: {
    background: 'rgba(255, 255, 255, 0.03)',
    border: '1px solid rgba(139, 159, 255, 0.15)',
    borderRadius: '6px',
    padding: '0.6rem 0.85rem',
    fontSize: '0.82rem',
    color: '#E0E6F0',
    outline: 'none',
    fontFamily: "'JetBrains Mono', monospace",
    letterSpacing: '0.02em',
    transition: 'border-color 0.2s',
  },
  description: {
    fontSize: '0.65rem',
    color: '#4A5568',
    lineHeight: 1.4,
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
    padding: '0.7rem 3rem',
    fontSize: '0.9rem',
    color: '#8B9FFF',
    fontWeight: 500,
    letterSpacing: '0.05em',
    transition: 'all 0.2s',
    fontFamily: "'Inter', sans-serif",
    marginTop: '0.5rem',
  },
  hint: {
    fontSize: '0.65rem',
    color: '#3D4759',
    margin: 0,
    textAlign: 'center',
  },
};

export default WelcomeGate;
