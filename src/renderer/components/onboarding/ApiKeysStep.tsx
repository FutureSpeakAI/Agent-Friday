/**
 * ApiKeysStep.tsx — Step 4: Optional cloud API credentials.
 *
 * "Cloud Engines." — Presents API keys as optional additions.
 * Local models handle most tasks; cloud keys add frontier capabilities.
 */

import React, { useState, useCallback, useEffect } from 'react';
import { Check } from 'lucide-react';
import CyberInput from './shared/CyberInput';
import NextButton from './shared/NextButton';

type TierName = 'whisper' | 'light' | 'standard' | 'full' | 'sovereign';

const LOCAL_CAPABLE_TIERS: TierName[] = ['standard', 'full', 'sovereign'];

interface KeyConfig {
  id: 'gemini' | 'anthropic' | 'openrouter';
  label: string;
  placeholder: string;
  description: string;
  localDescription: string;
  hasFlag: string;
  hintFlag: string;
}

const KEY_CONFIGS: KeyConfig[] = [
  {
    id: 'gemini',
    label: 'Google Gemini',
    placeholder: 'AIza...',
    description: 'Voice interaction, search, and embeddings',
    localDescription: 'Enables voice mode and cloud search — text mode works without it',
    hasFlag: 'hasGeminiKey',
    hintFlag: 'geminiKeyHint',
  },
  {
    id: 'anthropic',
    label: 'Anthropic Claude',
    placeholder: 'sk-ant-...',
    description: 'Deep reasoning, memory analysis, and profiling',
    localDescription: 'Enhances reasoning quality — local models handle this when absent',
    hasFlag: 'hasAnthropicKey',
    hintFlag: 'anthropicKeyHint',
  },
  {
    id: 'openrouter',
    label: 'OpenRouter',
    placeholder: 'sk-or-v1-...',
    description: 'Access 200+ AI models as alternative reasoning engine',
    localDescription: 'Optional — provides additional model variety',
    hasFlag: 'hasOpenrouterKey',
    hintFlag: 'openrouterKeyHint',
  },
];

interface ApiKeysStepProps {
  detectedTier: TierName | null;
  onComplete: () => void;
  onBack?: () => void;
}

const ApiKeysStep: React.FC<ApiKeysStepProps> = ({ detectedTier, onComplete, onBack }) => {
  const [keys, setKeys] = useState<Record<string, string>>({});
  const [existingKeys, setExistingKeys] = useState<Record<string, boolean>>({});
  const [keyHints, setKeyHints] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [fadeIn, setFadeIn] = useState(false);

  const isLocalCapable = detectedTier !== null && LOCAL_CAPABLE_TIERS.includes(detectedTier);

  // Load existing key state on mount
  useEffect(() => {
    let cancelled = false;
    setTimeout(() => { if (!cancelled) setFadeIn(true); }, 100);

    (async () => {
      try {
        const settings = await window.eve.settings.get() as Record<string, unknown>;
        if (cancelled) return;

        const existing: Record<string, boolean> = {};
        const hints: Record<string, string> = {};
        for (const config of KEY_CONFIGS) {
          existing[config.id] = !!settings[config.hasFlag];
          hints[config.id] = String(settings[config.hintFlag] || '');
        }
        setExistingKeys(existing);
        setKeyHints(hints);
      } catch { /* ignore */ }
    })();

    return () => { cancelled = true; };
  }, []);

  const updateKey = useCallback((id: string, value: string) => {
    setKeys((prev) => ({ ...prev, [id]: value }));
  }, []);

  const handleSave = useCallback(async () => {
    setSaving(true);
    setError('');
    try {
      let anyCloudKey = false;
      for (const config of KEY_CONFIGS) {
        const value = (keys[config.id] || '').trim();
        if (value) {
          await window.eve.settings.setApiKey(config.id, value);
          anyCloudKey = true;
        }
      }
      if (isLocalCapable && !anyCloudKey) {
        const hasExistingCloud = existingKeys.gemini || existingKeys.anthropic || existingKeys.openrouter;
        if (!hasExistingCloud) {
          await window.eve.settings.set('preferredProvider', 'ollama');
        }
      }
      onComplete();
    } catch (err: any) {
      setError(err?.message || 'Failed to save API keys');
      setSaving(false);
    }
  }, [keys, onComplete, isLocalCapable, existingKeys]);

  const handleSkip = useCallback(async () => {
    setSaving(true);
    try {
      if (isLocalCapable) {
        await window.eve.settings.set('preferredProvider', 'ollama');
      }
      onComplete();
    } catch {
      onComplete();
    }
  }, [onComplete, isLocalCapable]);

  const hasAnyKey = KEY_CONFIGS.some((c) =>
    (keys[c.id] || '').trim().length > 0 || existingKeys[c.id],
  );

  return (
    <section style={{
      ...styles.container,
      opacity: fadeIn ? 1 : 0,
      transform: fadeIn ? 'translateY(0)' : 'translateY(16px)',
      transition: 'all 0.7s cubic-bezier(0.16, 1, 0.3, 1)',
    }} aria-label="Cloud API key configuration">
      <div style={styles.headerBlock}>
        <h2 style={styles.heading}>Cloud Engines.</h2>
        <p style={styles.subtitle}>
          {isLocalCapable
            ? 'Your local models handle most tasks. Add cloud API keys for frontier capabilities — or skip this entirely.'
            : 'Connect cloud AI engines to power your agent. At least one key is recommended for your hardware tier.'}
        </p>
      </div>

      {isLocalCapable && (
        <p style={styles.localNote}>
          All keys are optional — your hardware is powerful enough for local AI.
        </p>
      )}

      {/* Key inputs */}
      <div style={styles.keyForm} role="form" aria-label="API key configuration">
        {KEY_CONFIGS.map((config) => (
          <div key={config.id} style={styles.keyField}>
            <div style={styles.keyLabelRow}>
              <span style={styles.keyLabel}>{config.label}</span>
              {existingKeys[config.id] && (
                <span style={styles.keyConfigured}>
                  <Check size={10} aria-hidden="true" /> Saved
                </span>
              )}
            </div>
            <CyberInput
              id={`key-${config.id}`}
              label={existingKeys[config.id] ? keyHints[config.id] || '••••••••' : config.placeholder}
              value={keys[config.id] || ''}
              onChange={(v) => updateKey(config.id, v)}
              type="password"
              monospace
              success={!!(keys[config.id] || '').trim() || existingKeys[config.id]}
            />
            <span style={styles.keyDesc}>
              {isLocalCapable ? config.localDescription : config.description}
            </span>
          </div>
        ))}
      </div>

      <p style={styles.settingsHint}>
        Configure additional providers (ElevenLabs, Firecrawl, Perplexity, OpenAI) in Settings.
      </p>

      {error && <p style={styles.error} role="alert">{error}</p>}

      <div style={styles.buttonRow}>
        <NextButton
          label={saving ? 'Saving...' : 'Continue'}
          onClick={handleSave}
          disabled={saving}
          loading={saving}
        />
        <NextButton
          label={isLocalCapable ? 'Skip — Use Local Models' : 'Skip for Now'}
          onClick={handleSkip}
          disabled={saving}
          variant="skip"
        />
      </div>

      <p style={styles.hint}>
        All keys stored locally and encrypted. You can change these anytime in Settings.
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
  localNote: {
    fontSize: 12,
    color: 'rgba(34, 197, 94, 0.7)',
    textAlign: 'center',
    lineHeight: 1.5,
    margin: 0,
    fontFamily: "'Inter', sans-serif",
    maxWidth: 420,
  },
  keyForm: {
    width: '100%',
    display: 'flex',
    flexDirection: 'column',
    gap: 20,
  },
  keyField: {
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
  },
  keyLabelRow: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  keyLabel: {
    fontSize: 11,
    fontWeight: 500,
    color: 'var(--text-60)',
    letterSpacing: '0.05em',
    fontFamily: "'Space Grotesk', sans-serif",
  },
  keyConfigured: {
    fontSize: 10,
    color: 'var(--accent-cyan-50)',
    display: 'flex',
    alignItems: 'center',
    gap: 4,
  },
  keyDesc: {
    fontSize: 10,
    color: 'var(--text-20)',
    lineHeight: 1.4,
    fontFamily: "'Inter', sans-serif",
  },
  settingsHint: {
    fontSize: 11,
    color: 'var(--text-30)',
    textAlign: 'center',
    margin: 0,
    fontFamily: "'Inter', sans-serif",
    fontStyle: 'italic',
  },
  error: {
    color: 'var(--accent-red)',
    fontSize: 12,
    margin: 0,
  },
  buttonRow: {
    display: 'flex',
    gap: 12,
    alignItems: 'center',
  },
  hint: {
    fontSize: 10,
    color: 'var(--text-20)',
    margin: 0,
    textAlign: 'center',
    fontFamily: "'Inter', sans-serif",
  },
};

export default ApiKeysStep;
