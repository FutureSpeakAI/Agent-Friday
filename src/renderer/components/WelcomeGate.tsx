/**
 * WelcomeGate.tsx — Full-screen API key entry gate.
 *
 * Shown on first launch before anything else loads.
 * Collects all API keys with clear explanations.
 * Dark, minimal design matching the app's #060B19 palette.
 *
 * TIER-AWARE: Detects hardware capabilities on mount. If the user has
 * "standard" tier or above (6 GB+ VRAM), all API keys become optional
 * and a "Run Locally" skip button appears. For whisper/light tiers,
 * Gemini + Claude remain required since local models won't fit.
 *
 * AUTO-SKIP: If both required keys already exist in the persisted settings
 * file (e.g. from a previous install), the gate auto-skips immediately.
 * Existing keys are shown as "configured" with masked hints.
 */

import React, { useState, useCallback, useEffect, useRef } from 'react';

/** Hardware tiers mirroring src/main/hardware/tier-recommender.ts */
type TierName = 'whisper' | 'light' | 'standard' | 'full' | 'sovereign';

/** Tiers with enough VRAM to run local models (6 GB+) */
const LOCAL_CAPABLE_TIERS: TierName[] = ['standard', 'full', 'sovereign'];

interface WelcomeGateProps {
  onKeysReady: () => void;
}

interface KeyConfig {
  id: 'gemini' | 'anthropic' | 'elevenlabs' | 'firecrawl' | 'perplexity' | 'openai' | 'openrouter';
  label: string;
  placeholder: string;
  /** Whether this key is required — dynamically overridden based on hardware tier */
  required: boolean;
  description: string;
  /** Description shown when hardware can run locally */
  localDescription?: string;
  hasFlag: string;    // settings key for boolean check (e.g. 'hasGeminiKey')
  hintFlag: string;   // settings key for masked hint (e.g. 'geminiKeyHint')
}

const KEY_CONFIGS: KeyConfig[] = [
  {
    id: 'gemini',
    label: 'Google Gemini',
    placeholder: 'AIza...',
    required: true,
    description: 'Voice interaction, search, and embeddings',
    localDescription: 'Enables voice mode and cloud search — text mode works without it',
    hasFlag: 'hasGeminiKey',
    hintFlag: 'geminiKeyHint',
  },
  {
    id: 'anthropic',
    label: 'Anthropic Claude',
    placeholder: 'sk-ant-...',
    required: true,
    description: 'Deep reasoning, memory analysis, and profiling',
    localDescription: 'Enhances reasoning quality — local models handle this when absent',
    hasFlag: 'hasAnthropicKey',
    hintFlag: 'anthropicKeyHint',
  },
  {
    id: 'elevenlabs',
    label: 'ElevenLabs',
    placeholder: 'sk_...',
    required: false,
    description: 'Distinct voices for background agents (Atlas, Nova, Cipher)',
    hasFlag: 'hasElevenLabsKey',
    hintFlag: 'elevenLabsKeyHint',
  },
  {
    id: 'firecrawl',
    label: 'Firecrawl',
    placeholder: 'fc-...',
    required: false,
    description: 'Web scraping and deep content extraction',
    hasFlag: 'hasFirecrawlKey',
    hintFlag: 'firecrawlKeyHint',
  },
  {
    id: 'perplexity',
    label: 'Perplexity',
    placeholder: 'pplx-...',
    required: false,
    description: 'Web search with citations and source links',
    hasFlag: 'hasPerplexityKey',
    hintFlag: 'perplexityKeyHint',
  },
  {
    id: 'openai',
    label: 'OpenAI',
    placeholder: 'sk-...',
    required: false,
    description: 'o3 reasoning, Whisper transcription, embeddings (images use Nano Banana 2 via Gemini)',
    hasFlag: 'hasOpenaiKey',
    hintFlag: 'openaiKeyHint',
  },
  {
    id: 'openrouter',
    label: 'OpenRouter',
    placeholder: 'sk-or-v1-...',
    required: false,
    description: 'Access 200+ AI models — can replace Claude as the agent reasoning engine',
    hasFlag: 'hasOpenrouterKey',
    hintFlag: 'openrouterKeyHint',
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
    openrouter: '',
  });
  // Track which keys already exist in persisted settings (from previous install)
  const [existingKeys, setExistingKeys] = useState<Record<string, boolean>>({});
  const [keyHints, setKeyHints] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const autoSkippedRef = useRef(false);
  /** Detected hardware tier — null until detection completes */
  const [hardwareTier, setHardwareTier] = useState<TierName | null>(null);

  /** Whether this hardware can run local models (standard+ = 6 GB+ VRAM) */
  const isLocalCapable = hardwareTier !== null && LOCAL_CAPABLE_TIERS.includes(hardwareTier);

  /**
   * Build the effective key configs based on hardware tier.
   * For local-capable hardware, all keys become optional.
   */
  const effectiveConfigs = KEY_CONFIGS.map((config) => ({
    ...config,
    required: isLocalCapable ? false : config.required,
  }));

  // On mount: detect hardware tier AND load existing keys
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // Detect hardware tier in parallel with settings load
        const [settings, hwProfile] = await Promise.all([
          window.eve.settings.get() as Promise<Record<string, unknown>>,
          window.eve.hardware.detect().catch(() => null),
        ]);

        if (cancelled) return;

        // Determine tier from hardware profile
        if (hwProfile) {
          try {
            const tier = await window.eve.hardware.getTier(hwProfile as Record<string, unknown>);
            if (!cancelled) {
              setHardwareTier(tier as TierName);
              console.log(`[WelcomeGate] Hardware tier detected: ${tier}`);
            }
          } catch (e) {
            console.warn('[WelcomeGate] Tier detection failed, assuming whisper:', e);
            if (!cancelled) setHardwareTier('whisper');
          }
        } else {
          if (!cancelled) setHardwareTier('whisper');
        }

        // Build maps of which keys exist and their hints
        const existing: Record<string, boolean> = {};
        const hints: Record<string, string> = {};
        for (const config of KEY_CONFIGS) {
          existing[config.id] = !!settings[config.hasFlag];
          hints[config.id] = String(settings[config.hintFlag] || '');
        }

        setExistingKeys(existing);
        setKeyHints(hints);

        // AUTO-SKIP: If all required keys already exist, skip the gate entirely
        // (Uses base KEY_CONFIGS.required since tier hasn't settled yet for skip logic —
        //  if both cloud keys exist, skip regardless of tier)
        const requiredKeysExist = KEY_CONFIGS
          .filter((k) => k.required)
          .every((k) => existing[k.id]);

        if (requiredKeysExist && !autoSkippedRef.current) {
          autoSkippedRef.current = true;
          console.log('[WelcomeGate] Required API keys detected from existing settings — auto-skipping gate');
          onKeysReady();
          return;
        }
      } catch (err) {
        console.warn('[WelcomeGate] Failed to load existing settings:', err);
      }

      if (!cancelled) setLoading(false);
    })();

    return () => { cancelled = true; };
  }, [onKeysReady]);

  // A key counts as "filled" if the user typed a new value OR it already exists in settings
  const requiredFilled = effectiveConfigs
    .filter((k) => k.required)
    .every((k) => keys[k.id].trim().length > 0 || existingKeys[k.id]);

  const canProceed = requiredFilled && !saving && !loading;

  const updateKey = useCallback((id: string, value: string) => {
    setKeys((prev) => ({ ...prev, [id]: value }));
  }, []);

  /** Skip API keys entirely — run in local-only mode */
  const handleSkipLocal = useCallback(async () => {
    setSaving(true);
    setError('');
    try {
      // Auto-configure for local-only operation
      await window.eve.settings.set('preferredProvider', 'ollama');
      console.log('[WelcomeGate] Skipped API keys — configured for local-only operation');
      onKeysReady();
    } catch (err: any) {
      setError(err?.message || 'Failed to configure local mode');
      setSaving(false);
    }
  }, [onKeysReady]);

  const handleBegin = useCallback(async () => {
    if (!canProceed) return;
    setSaving(true);
    setError('');

    try {
      // Save only NEW keys (non-empty inputs that override or add to existing)
      let anyCloudKeyProvided = false;
      for (const config of KEY_CONFIGS) {
        const value = keys[config.id].trim();
        if (value) {
          await window.eve.settings.setApiKey(config.id, value);
          if (['gemini', 'anthropic', 'openai', 'openrouter'].includes(config.id)) {
            anyCloudKeyProvided = true;
          }
        }
      }

      // If local-capable hardware but no cloud keys were entered, default to Ollama
      if (isLocalCapable && !anyCloudKeyProvided) {
        const hasExistingCloud = existingKeys.gemini || existingKeys.anthropic || existingKeys.openrouter;
        if (!hasExistingCloud) {
          await window.eve.settings.set('preferredProvider', 'ollama');
          console.log('[WelcomeGate] No cloud keys — auto-configured preferredProvider to ollama');
        }
      }

      onKeysReady();
    } catch (err: any) {
      setError(err?.message || 'Failed to save API keys');
      setSaving(false);
    }
  }, [keys, canProceed, onKeysReady, isLocalCapable, existingKeys]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && canProceed) handleBegin();
    },
    [canProceed, handleBegin],
  );

  // Don't render anything while loading or if auto-skipping
  if (loading) {
    return (
      <div style={styles.overlay}>
        <div style={{ ...styles.container, justifyContent: 'center', minHeight: '100vh' }}>
          <div style={styles.logo}>◈</div>
          <div style={{ color: '#5A6577', fontSize: '0.8rem', letterSpacing: '0.1em' }}>
            Checking configuration...
          </div>
        </div>
      </div>
    );
  }

  // Split configs into required vs optional based on effective (tier-aware) config
  const requiredKeyConfigs = effectiveConfigs.filter((k) => k.required);
  const optionalKeyConfigs = effectiveConfigs.filter((k) => !k.required);

  // Tier badge for the explainer
  const tierBadge = hardwareTier
    ? `${hardwareTier.charAt(0).toUpperCase() + hardwareTier.slice(1)} tier`
    : 'Detecting...';

  return (
    <div style={styles.overlay}>
      <div style={styles.container}>
        {/* Title */}
        <div style={styles.titleBlock}>
          <div style={styles.logo}>◈</div>
          <h1 style={styles.title}>Agent Friday</h1>
          <div style={styles.byLine}>by FutureSpeak.AI</div>
        </div>

        {/* Explanation — tier-aware */}
        <div style={styles.explainer}>
          {isLocalCapable ? (
            <>
              <p style={styles.explainerText}>
                Your hardware supports local AI models ({tierBadge}).
                Agent Friday can run entirely on your machine — no cloud keys needed.
              </p>
              <p style={styles.explainerDetail}>
                Adding API keys unlocks voice mode, frontier reasoning, and additional
                capabilities. But text conversation, memory, screen awareness, and more
                all work locally. You can always add keys later in Settings.
              </p>
            </>
          ) : (
            <>
              <p style={styles.explainerText}>
                Agent Friday is a voice-first AI companion that lives on your desktop.
                It needs API keys to connect to the AI services that power its voice,
                reasoning, and capabilities.
              </p>
              <p style={styles.explainerDetail}>
                {hardwareTier ? `Your hardware (${tierBadge}) needs cloud models for the best experience. ` : ''}
                The two required keys give you the core experience — voice conversation
                and deep reasoning. The optional keys unlock additional capabilities.
                You can always add or change these later in Settings.
              </p>
            </>
          )}
        </div>

        {/* Required keys section — only shown if there are required keys (whisper/light tiers) */}
        {requiredKeyConfigs.length > 0 && (
          <div style={styles.section}>
            <div style={styles.sectionHeader}>
              <span style={styles.sectionLabel}>Required</span>
              <span style={styles.sectionLine} />
            </div>
            <div style={styles.fields}>
              {requiredKeyConfigs.map((config, i) => (
                <div key={config.id} style={styles.field}>
                  <div style={styles.labelRow}>
                    <label style={styles.label}>{config.label}</label>
                    {existingKeys[config.id] ? (
                      <span style={styles.configured}>✓ Configured</span>
                    ) : (
                      <span style={styles.required}>Required</span>
                    )}
                  </div>
                  <input
                    type="password"
                    value={keys[config.id]}
                    onChange={(e) => updateKey(config.id, e.target.value)}
                    onKeyDown={handleKeyDown}
                    placeholder={existingKeys[config.id] ? keyHints[config.id] || '••••••••' : config.placeholder}
                    style={{
                      ...styles.input,
                      borderColor: (keys[config.id].trim() || existingKeys[config.id])
                        ? 'rgba(0, 229, 255, 0.3)'
                        : 'rgba(139, 159, 255, 0.15)',
                      ...(existingKeys[config.id] && !keys[config.id].trim() ? {
                        background: 'rgba(0, 229, 255, 0.04)',
                      } : {}),
                    }}
                    autoFocus={!existingKeys[config.id] && i === 0}
                  />
                  <span style={styles.description}>
                    {existingKeys[config.id] && !keys[config.id].trim()
                      ? `${config.description} — key already saved, leave blank to keep`
                      : config.description}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Optional / Cloud Enhancement keys section */}
        <div style={styles.section}>
          <div style={styles.sectionHeader}>
            <span style={{ ...styles.sectionLabel, color: '#6B7A99' }}>
              {isLocalCapable ? 'Cloud Enhancements' : 'Optional'}
            </span>
            <span style={styles.sectionLine} />
          </div>
          <div style={styles.fields}>
            {optionalKeyConfigs.map((config, i) => (
              <div key={config.id} style={styles.field}>
                <div style={styles.labelRow}>
                  <label style={{ ...styles.label, color: '#6B7A99' }}>{config.label}</label>
                  {existingKeys[config.id] && (
                    <span style={styles.configured}>✓ Configured</span>
                  )}
                </div>
                <input
                  type="password"
                  value={keys[config.id]}
                  onChange={(e) => updateKey(config.id, e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder={existingKeys[config.id] ? keyHints[config.id] || '••••••••' : config.placeholder}
                  style={{
                    ...styles.input,
                    borderColor: (keys[config.id].trim() || existingKeys[config.id])
                      ? 'rgba(0, 229, 255, 0.3)'
                      : 'rgba(255, 255, 255, 0.06)',
                    ...(existingKeys[config.id] && !keys[config.id].trim() ? {
                      background: 'rgba(0, 229, 255, 0.04)',
                    } : {}),
                  }}
                  autoFocus={isLocalCapable && !existingKeys[config.id] && i === 0}
                />
                <span style={styles.description}>
                  {existingKeys[config.id] && !keys[config.id].trim()
                    ? `${config.description} — key already saved, leave blank to keep`
                    : (isLocalCapable && config.localDescription) ? config.localDescription : config.description}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* Error */}
        {error && <p style={styles.error}>{error}</p>}

        {/* Action buttons */}
        <div style={{ display: 'flex', gap: '1rem', alignItems: 'center', flexWrap: 'wrap', justifyContent: 'center' }}>
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

          {/* "Run Locally" skip button — only for local-capable hardware */}
          {isLocalCapable && (
            <button
              onClick={handleSkipLocal}
              disabled={saving}
              style={{
                ...styles.button,
                background: 'rgba(0, 229, 255, 0.08)',
                borderColor: 'rgba(0, 229, 255, 0.25)',
                color: 'rgba(0, 229, 255, 0.8)',
                opacity: saving ? 0.3 : 1,
                cursor: saving ? 'default' : 'pointer',
              }}
            >
              {saving ? 'Initializing...' : 'Run Locally'}
            </button>
          )}
        </div>

        <p style={styles.hint}>
          All keys are stored locally on your machine and never shared with third parties.
          {isLocalCapable && ' You can add cloud keys anytime in Settings.'}
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
    // Override body-level -webkit-app-region:drag so scrollbar + content are interactive
    WebkitAppRegion: 'no-drag',
  } as React.CSSProperties,
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
  configured: {
    fontSize: '0.6rem',
    color: 'rgba(0, 229, 255, 0.7)',
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
