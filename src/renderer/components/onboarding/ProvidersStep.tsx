/**
 * ProvidersStep.tsx — Step 4: Connect Your Services.
 *
 * Replaces the old ApiKeysStep with all 8 API key slots organized
 * by category, plus a routing-preference selector.
 * Keys are encrypted on-device and never sent to third parties.
 */

import React, { useState, useCallback, useEffect, useRef } from 'react';
import { Check, AlertCircle } from 'lucide-react';
import CyberInput from './shared/CyberInput';
import NextButton from './shared/NextButton';
import { validateApiKey } from '../../hooks/useApiKeyValidation';

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

type TierName = 'whisper' | 'light' | 'standard' | 'full' | 'sovereign';

type RoutingPreference = 'anthropic' | 'openrouter' | 'local' | 'auto';

type ApiKeyId =
  | 'anthropic'
  | 'openrouter'
  | 'openai'
  | 'huggingface'
  | 'gemini'
  | 'elevenlabs'
  | 'perplexity'
  | 'firecrawl';

interface KeyConfig {
  id: ApiKeyId;
  label: string;
  placeholder: string;
  description: string;
  hasFlag: string;
  hintFlag: string;
  /** If true, show an extra text input when the key is set */
  extraInput?: {
    settingKey: string;
    label: string;
    defaultValue: string;
  };
}

interface KeySection {
  title: string;
  keys: KeyConfig[];
}

interface ProvidersStepProps {
  detectedTier: TierName | null;
  onComplete: () => void;
  onBack?: () => void;
}

/* ------------------------------------------------------------------ */
/*  Constants                                                          */
/* ------------------------------------------------------------------ */

const LOCAL_CAPABLE_TIERS: TierName[] = ['standard', 'full', 'sovereign'];

const ROUTING_OPTIONS: { value: RoutingPreference; label: string; description: string }[] = [
  { value: 'anthropic', label: 'Anthropic Direct', description: 'Fastest for Claude models' },
  { value: 'openrouter', label: 'OpenRouter', description: 'Model variety, usage tracking' },
  { value: 'local', label: 'Local First', description: 'Ollama/HuggingFace when possible' },
  { value: 'auto', label: 'Auto', description: 'Let Friday choose per-task' },
];

const KEY_SECTIONS: KeySection[] = [
  {
    title: 'Reasoning Engine',
    keys: [
      {
        id: 'anthropic',
        label: 'Anthropic Claude',
        placeholder: 'sk-ant-...',
        description: 'Deep reasoning, memory extraction, profiling',
        hasFlag: 'hasAnthropicKey',
        hintFlag: 'anthropicKeyHint',
      },
      {
        id: 'openrouter',
        label: 'OpenRouter',
        placeholder: 'sk-or-v1-...',
        description: '200+ models, usage tracking',
        hasFlag: 'hasOpenrouterKey',
        hintFlag: 'openrouterKeyHint',
        extraInput: {
          settingKey: 'openrouterModel',
          label: 'Model ID',
          defaultValue: 'anthropic/claude-sonnet-4',
        },
      },
      {
        id: 'openai',
        label: 'OpenAI',
        placeholder: 'sk-...',
        description: 'Embeddings, specialized models',
        hasFlag: 'hasOpenaiKey',
        hintFlag: 'openaiKeyHint',
      },
      {
        id: 'huggingface',
        label: 'HuggingFace',
        placeholder: 'hf_...',
        description: 'Cloud inference for open-weight models',
        hasFlag: 'hasHuggingfaceKey',
        hintFlag: 'huggingfaceKeyHint',
        extraInput: {
          settingKey: 'huggingfaceEndpoint',
          label: 'Endpoint URL',
          defaultValue: 'https://api-inference.huggingface.co/v1',
        },
      },
    ],
  },
  {
    title: 'Voice & Conversation',
    keys: [
      {
        id: 'gemini',
        label: 'Google Gemini',
        placeholder: 'AIza...',
        description: 'Live voice conversation, search, vision',
        hasFlag: 'hasGeminiKey',
        hintFlag: 'geminiKeyHint',
      },
      {
        id: 'elevenlabs',
        label: 'ElevenLabs',
        placeholder: 'Enter API key...',
        description: 'Distinct voices for sub-agents (Atlas, Nova, etc.)',
        hasFlag: 'hasElevenLabsKey',
        hintFlag: 'elevenLabsKeyHint',
      },
    ],
  },
  {
    title: 'Web Intelligence',
    keys: [
      {
        id: 'perplexity',
        label: 'Perplexity',
        placeholder: 'pplx-...',
        description: 'Live web search + deep research',
        hasFlag: 'hasPerplexityKey',
        hintFlag: 'perplexityKeyHint',
      },
      {
        id: 'firecrawl',
        label: 'Firecrawl',
        placeholder: 'fc-...',
        description: 'Web scraping + full-site crawling',
        hasFlag: 'hasFirecrawlKey',
        hintFlag: 'firecrawlKeyHint',
      },
    ],
  },
];

const ALL_KEYS: KeyConfig[] = KEY_SECTIONS.flatMap((s) => s.keys);
const TOTAL_KEYS = ALL_KEYS.length; // 8

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

const ProvidersStep: React.FC<ProvidersStepProps> = ({ detectedTier, onComplete, onBack }) => {
  const [keys, setKeys] = useState<Record<string, string>>({});
  const [existingKeys, setExistingKeys] = useState<Record<string, boolean>>({});
  const [keyHints, setKeyHints] = useState<Record<string, string>>({});
  const [keyStatus, setKeyStatus] = useState<Record<string, 'idle' | 'checking' | 'valid' | 'invalid'>>({});
  const [keyErrors, setKeyErrors] = useState<Record<string, string>>({});
  const [extraValues, setExtraValues] = useState<Record<string, string>>({});
  const [routingPref, setRoutingPref] = useState<RoutingPreference>('auto');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [fadeIn, setFadeIn] = useState(false);
  const debounceTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  const isLocalCapable = detectedTier !== null && LOCAL_CAPABLE_TIERS.includes(detectedTier);

  /* ---- Load existing settings on mount ---- */
  useEffect(() => {
    let cancelled = false;
    setTimeout(() => { if (!cancelled) setFadeIn(true); }, 100);

    (async () => {
      try {
        const settings = await window.eve.settings.get() as Record<string, unknown>;
        if (cancelled) return;

        const existing: Record<string, boolean> = {};
        const hints: Record<string, string> = {};
        for (const config of ALL_KEYS) {
          existing[config.id] = !!settings[config.hasFlag];
          hints[config.id] = String(settings[config.hintFlag] || '');
        }
        setExistingKeys(existing);
        setKeyHints(hints);

        // Load existing routing preference
        if (settings.preferredProvider) {
          const pref = settings.preferredProvider as string;
          if (['anthropic', 'openrouter', 'local', 'auto'].includes(pref)) {
            setRoutingPref(pref as RoutingPreference);
          } else if (pref === 'ollama') {
            setRoutingPref('local');
          }
        }

        // Load existing extra input values
        const extras: Record<string, string> = {};
        if (settings.openrouterModel) {
          extras.openrouterModel = String(settings.openrouterModel);
        }
        if (settings.huggingfaceEndpoint) {
          extras.huggingfaceEndpoint = String(settings.huggingfaceEndpoint);
        }
        setExtraValues(extras);
      } catch { /* ignore */ }
    })();

    return () => { cancelled = true; };
  }, []);

  /* ---- Key input with debounced validation ---- */
  const updateKey = useCallback((id: string, value: string) => {
    setKeys((prev) => ({ ...prev, [id]: value }));

    if (debounceTimers.current[id]) clearTimeout(debounceTimers.current[id]);

    const trimmed = value.trim();
    if (!trimmed || trimmed.length < 8) {
      setKeyStatus((prev) => ({ ...prev, [id]: 'idle' }));
      setKeyErrors((prev) => ({ ...prev, [id]: '' }));
      return;
    }

    setKeyStatus((prev) => ({ ...prev, [id]: 'checking' }));
    debounceTimers.current[id] = setTimeout(async () => {
      try {
        const result = await validateApiKey(id, trimmed);
        setKeyStatus((prev) => ({ ...prev, [id]: result.valid ? 'valid' : 'invalid' }));
        setKeyErrors((prev) => ({ ...prev, [id]: result.error || '' }));
      } catch {
        setKeyStatus((prev) => ({ ...prev, [id]: 'idle' }));
      }
    }, 800);
  }, []);

  /* ---- Extra input handler ---- */
  const updateExtra = useCallback((settingKey: string, value: string) => {
    setExtraValues((prev) => ({ ...prev, [settingKey]: value }));
  }, []);

  /* ---- Count configured keys ---- */
  const configuredCount = ALL_KEYS.filter(
    (c) => (keys[c.id] || '').trim().length > 0 || existingKeys[c.id],
  ).length;

  /* ---- Save ---- */
  const handleSave = useCallback(async () => {
    setSaving(true);
    setError('');
    try {
      // Validate all entered keys
      for (const config of ALL_KEYS) {
        const value = (keys[config.id] || '').trim();
        if (value) {
          const result = await validateApiKey(config.id, value);
          if (!result.valid) {
            setError(`${config.label}: ${result.error}`);
            setSaving(false);
            return;
          }
        }
      }

      // Save all entered keys
      for (const config of ALL_KEYS) {
        const value = (keys[config.id] || '').trim();
        if (value) {
          await window.eve.settings.setApiKey(config.id, value);
        }
      }

      // Save extra inputs
      for (const config of ALL_KEYS) {
        if (config.extraInput) {
          const value = (extraValues[config.extraInput.settingKey] || '').trim();
          if (value && value !== config.extraInput.defaultValue) {
            await window.eve.settings.set(config.extraInput.settingKey, value);
          }
        }
      }

      // Save routing preference
      const providerValue = routingPref === 'local' ? 'ollama' : routingPref;
      await window.eve.settings.set('preferredProvider', providerValue);

      onComplete();
    } catch (err: any) {
      setError(err?.message || 'Failed to save configuration');
      setSaving(false);
    }
  }, [keys, extraValues, routingPref, onComplete]);

  /* ---- Skip ---- */
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

  /* ---- Render a single key field ---- */
  const renderKeyField = (config: KeyConfig) => {
    const status = keyStatus[config.id] || 'idle';
    const hasExisting = existingKeys[config.id];
    const currentValue = keys[config.id] || '';
    const showExtra = config.extraInput && (currentValue.trim().length > 0 || hasExisting);

    return (
      <div key={config.id} style={styles.keyField}>
        <div style={styles.keyLabelRow}>
          <span style={styles.keyLabel}>{config.label}</span>
          <span style={styles.keyStatusRow}>
            {status === 'checking' && (
              <span style={styles.keyStatusDot} title="Validating...">
                <span style={{ ...styles.statusDot, background: '#eab308', boxShadow: '0 0 6px #eab30840' }} />
              </span>
            )}
            {status === 'valid' && (
              <span style={styles.keyStatusDot} title="Key valid">
                <Check size={10} color="#22c55e" />
              </span>
            )}
            {status === 'invalid' && (
              <span style={styles.keyStatusDot} title={keyErrors[config.id] || 'Invalid key'}>
                <AlertCircle size={10} color="#ef4444" />
              </span>
            )}
            {hasExisting && !currentValue.trim() && (
              <span style={styles.keyConfigured}>
                <Check size={10} aria-hidden="true" /> Saved
              </span>
            )}
          </span>
        </div>
        <CyberInput
          id={`key-${config.id}`}
          label={hasExisting ? keyHints[config.id] || '••••••••' : config.placeholder}
          value={currentValue}
          onChange={(v) => updateKey(config.id, v)}
          type="password"
          monospace
          success={status === 'valid' || (!currentValue.trim() && !!hasExisting)}
        />
        {status === 'invalid' && keyErrors[config.id] && (
          <span style={styles.keyErrorHint}>{keyErrors[config.id]}</span>
        )}
        <span style={styles.keyDesc}>{config.description}</span>

        {/* Extra input (model ID or endpoint URL) */}
        {showExtra && config.extraInput && (
          <div style={styles.extraInputWrap}>
            <CyberInput
              id={`extra-${config.extraInput.settingKey}`}
              label={config.extraInput.label}
              value={extraValues[config.extraInput.settingKey] || config.extraInput.defaultValue}
              onChange={(v) => updateExtra(config.extraInput!.settingKey, v)}
              type="text"
            />
          </div>
        )}
      </div>
    );
  };

  /* ---- Render ---- */
  return (
    <section
      style={{
        ...styles.container,
        opacity: fadeIn ? 1 : 0,
        transform: fadeIn ? 'translateY(0)' : 'translateY(16px)',
        transition: 'all 0.7s cubic-bezier(0.16, 1, 0.3, 1)',
      }}
      aria-label="Provider configuration"
    >
      {/* Header */}
      <div style={styles.headerBlock}>
        <h2 style={styles.heading}>Connect Your Services.</h2>
        <p style={styles.subtitle}>
          Everything here is optional. Keys are encrypted on-device and never sent to third parties.
        </p>
      </div>

      {isLocalCapable && (
        <p style={styles.localNote}>
          All keys are optional — your hardware is powerful enough for local AI.
        </p>
      )}

      {/* Scrollable key sections */}
      <div style={styles.scrollArea}>
        {KEY_SECTIONS.map((section) => (
          <div key={section.title} style={styles.sectionCard}>
            <span style={styles.sectionTitle}>{section.title}</span>
            <div style={styles.sectionKeys}>
              {section.keys.map(renderKeyField)}
            </div>
          </div>
        ))}

        {/* Routing Preference */}
        <div style={styles.sectionCard}>
          <span style={styles.sectionTitle}>Routing Preference</span>
          <div style={styles.routingGroup} role="radiogroup" aria-label="Routing preference">
            {ROUTING_OPTIONS.map((opt) => (
              <label key={opt.value} style={styles.routingOption}>
                <input
                  type="radio"
                  name="routing-preference"
                  value={opt.value}
                  checked={routingPref === opt.value}
                  onChange={() => setRoutingPref(opt.value)}
                  style={styles.radioHidden}
                />
                <span
                  style={{
                    ...styles.radioOuter,
                    borderColor: routingPref === opt.value
                      ? 'var(--accent-cyan-70)'
                      : 'rgba(255, 255, 255, 0.12)',
                  }}
                >
                  {routingPref === opt.value && <span style={styles.radioInner} />}
                </span>
                <span style={styles.routingText}>
                  <span style={styles.routingLabel}>{opt.label}</span>
                  <span style={styles.routingDesc}>{opt.description}</span>
                </span>
              </label>
            ))}
          </div>
        </div>
      </div>

      {/* Key counter */}
      <p style={styles.keyCounter}>Keys configured: {configuredCount}/{TOTAL_KEYS}</p>

      {error && <p style={styles.error} role="alert">{error}</p>}

      {/* Buttons */}
      <div style={styles.buttonRow}>
        <NextButton
          label={saving ? 'Saving...' : 'Continue'}
          onClick={handleSave}
          disabled={saving}
          loading={saving}
        />
        <NextButton
          label="Skip All"
          onClick={handleSkip}
          disabled={saving}
          variant="skip"
        />
      </div>

      <p style={styles.hint}>All keys can be changed later in Settings.</p>
    </section>
  );
};

/* ------------------------------------------------------------------ */
/*  Styles                                                             */
/* ------------------------------------------------------------------ */

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

  /* Scroll area */
  scrollArea: {
    width: '100%',
    maxHeight: 440,
    overflowY: 'auto',
    display: 'flex',
    flexDirection: 'column',
    gap: 16,
    paddingRight: 4,
  },

  /* Section cards */
  sectionCard: {
    background: 'var(--onboarding-card)',
    border: '1px solid rgba(255, 255, 255, 0.04)',
    borderRadius: 12,
    padding: '20px 24px',
    display: 'flex',
    flexDirection: 'column',
    gap: 16,
  },
  sectionTitle: {
    fontSize: 11,
    fontWeight: 600,
    letterSpacing: '0.1em',
    color: 'var(--text-40)',
    fontFamily: "'Space Grotesk', sans-serif",
    textTransform: 'uppercase' as const,
  },
  sectionKeys: {
    display: 'flex',
    flexDirection: 'column',
    gap: 18,
  },

  /* Key field */
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
  keyStatusRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
  },
  keyStatusDot: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
  },
  statusDot: {
    width: 8,
    height: 8,
    borderRadius: '50%',
    animation: 'pulse 1.2s ease-in-out infinite',
  },
  keyConfigured: {
    fontSize: 10,
    color: 'var(--accent-cyan-50)',
    display: 'flex',
    alignItems: 'center',
    gap: 4,
  },
  keyErrorHint: {
    fontSize: 10,
    color: '#ef4444',
    fontFamily: "'Inter', sans-serif",
    marginTop: -2,
  },
  keyDesc: {
    fontSize: 10,
    color: 'var(--text-20)',
    lineHeight: 1.4,
    fontFamily: "'Inter', sans-serif",
  },
  extraInputWrap: {
    marginTop: 4,
  },

  /* Routing preference */
  routingGroup: {
    display: 'flex',
    flexDirection: 'column',
    gap: 10,
  },
  routingOption: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    cursor: 'pointer',
    padding: '6px 0',
  },
  radioHidden: {
    position: 'absolute',
    opacity: 0,
    width: 0,
    height: 0,
    pointerEvents: 'none',
  },
  radioOuter: {
    width: 16,
    height: 16,
    borderRadius: '50%',
    border: '1.5px solid rgba(255, 255, 255, 0.12)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
    transition: 'border-color 0.2s ease',
  },
  radioInner: {
    width: 8,
    height: 8,
    borderRadius: '50%',
    background: 'var(--accent-cyan-70)',
  },
  routingText: {
    display: 'flex',
    flexDirection: 'column',
    gap: 1,
  },
  routingLabel: {
    fontSize: 12,
    fontWeight: 500,
    color: 'var(--text-60)',
    fontFamily: "'Space Grotesk', sans-serif",
    letterSpacing: '0.03em',
  },
  routingDesc: {
    fontSize: 10,
    color: 'var(--text-20)',
    fontFamily: "'Inter', sans-serif",
  },

  /* Footer */
  keyCounter: {
    fontSize: 11,
    color: 'var(--text-40)',
    fontFamily: "'Space Grotesk', sans-serif",
    letterSpacing: '0.05em',
    margin: 0,
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

export default ProvidersStep;
