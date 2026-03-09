/**
 * EnginesStep.tsx — Step 2: Hardware profiler + API key entry.
 *
 * Detects GPU/VRAM/RAM, determines tier, then presents API key form.
 * Tier-aware: standard+ hardware makes all keys optional with "Run Locally"
 * skip. Reuses KEY_CONFIGS pattern from WelcomeGate.
 */

import React, { useState, useCallback, useEffect } from 'react';
import { Cpu, Zap, Database, ChevronRight, Check } from 'lucide-react';

/** Hardware tiers mirroring src/main/hardware/tier-recommender.ts */
type TierName = 'whisper' | 'light' | 'standard' | 'full' | 'sovereign';

const LOCAL_CAPABLE_TIERS: TierName[] = ['standard', 'full', 'sovereign'];

const TIER_META: Record<TierName, { label: string; color: string; dots: number }> = {
  whisper:   { label: 'Whisper',   color: '#ef4444', dots: 1 },
  light:     { label: 'Light',     color: '#f97316', dots: 2 },
  standard:  { label: 'Standard',  color: '#eab308', dots: 3 },
  full:      { label: 'Full',      color: '#22c55e', dots: 4 },
  sovereign: { label: 'Sovereign', color: '#00f0ff', dots: 5 },
};

interface KeyConfig {
  id: 'gemini' | 'anthropic' | 'elevenlabs' | 'firecrawl' | 'perplexity' | 'openai' | 'openrouter';
  label: string;
  placeholder: string;
  required: boolean;
  description: string;
  localDescription?: string;
  hasFlag: string;
  hintFlag: string;
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
    description: 'Distinct voices for background agents',
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
    description: 'o3 reasoning, Whisper transcription, embeddings',
    hasFlag: 'hasOpenaiKey',
    hintFlag: 'openaiKeyHint',
  },
  {
    id: 'openrouter',
    label: 'OpenRouter',
    placeholder: 'sk-or-v1-...',
    required: false,
    description: 'Access 200+ AI models as alternative reasoning engine',
    hasFlag: 'hasOpenrouterKey',
    hintFlag: 'openrouterKeyHint',
  },
];

interface EnginesStepProps {
  onComplete: (didSkip?: boolean) => void;
  onBack?: () => void;
}

interface HardwareProfile {
  gpuName?: string;
  vramMB?: number;
  ramMB?: number;
}

const EnginesStep: React.FC<EnginesStepProps> = ({ onComplete, onBack }) => {
  const [phase, setPhase] = useState<'detecting' | 'form'>('detecting');
  const [hwProfile, setHwProfile] = useState<HardwareProfile | null>(null);
  const [tier, setTier] = useState<TierName | null>(null);
  const [keys, setKeys] = useState<Record<string, string>>({});
  const [existingKeys, setExistingKeys] = useState<Record<string, boolean>>({});
  const [keyHints, setKeyHints] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const isLocalCapable = tier !== null && LOCAL_CAPABLE_TIERS.includes(tier);

  const effectiveConfigs = KEY_CONFIGS.map((c) => ({
    ...c,
    required: isLocalCapable ? false : c.required,
  }));

  // Detect hardware on mount
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [settings, profile] = await Promise.all([
          window.eve.settings.get() as Promise<Record<string, unknown>>,
          window.eve.hardware.detect().catch(() => null),
        ]);

        if (cancelled) return;

        // Load existing keys
        const existing: Record<string, boolean> = {};
        const hints: Record<string, string> = {};
        for (const config of KEY_CONFIGS) {
          existing[config.id] = !!settings[config.hasFlag];
          hints[config.id] = String(settings[config.hintFlag] || '');
        }
        setExistingKeys(existing);
        setKeyHints(hints);

        // Detect tier
        if (profile) {
          const p = profile as Record<string, unknown>;
          setHwProfile({
            gpuName: String(p.gpuName || p.gpu || 'Unknown GPU'),
            vramMB: Number(p.vramMB || p.vram || 0),
            ramMB: Number(p.ramMB || p.ram || 0),
          });
          try {
            const t = await window.eve.hardware.getTier(p);
            if (!cancelled) setTier(t as TierName);
          } catch {
            if (!cancelled) setTier('whisper');
          }
        } else {
          setTier('whisper');
        }
      } catch {
        setTier('whisper');
      }

      if (!cancelled) {
        setTimeout(() => {
          if (!cancelled) setPhase('form');
        }, 1800);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const updateKey = useCallback((id: string, value: string) => {
    setKeys((prev) => ({ ...prev, [id]: value }));
  }, []);

  const requiredFilled = effectiveConfigs
    .filter((k) => k.required)
    .every((k) => (keys[k.id] || '').trim().length > 0 || existingKeys[k.id]);

  const canProceed = requiredFilled && !saving;

  const handleSave = useCallback(async () => {
    if (!canProceed) return;
    setSaving(true);
    setError('');
    try {
      let anyCloudKey = false;
      for (const config of KEY_CONFIGS) {
        const value = (keys[config.id] || '').trim();
        if (value) {
          await window.eve.settings.setApiKey(config.id, value);
          if (['gemini', 'anthropic', 'openai', 'openrouter'].includes(config.id)) {
            anyCloudKey = true;
          }
        }
      }
      if (isLocalCapable && !anyCloudKey) {
        const hasExistingCloud = existingKeys.gemini || existingKeys.anthropic || existingKeys.openrouter;
        if (!hasExistingCloud) {
          await window.eve.settings.set('preferredProvider', 'ollama');
        }
      }
      onComplete(false);
    } catch (err: any) {
      setError(err?.message || 'Failed to save API keys');
      setSaving(false);
    }
  }, [keys, canProceed, onComplete, isLocalCapable, existingKeys]);

  const handleSkipLocal = useCallback(async () => {
    setSaving(true);
    try {
      await window.eve.settings.set('preferredProvider', 'ollama');
      onComplete(true);
    } catch (err: any) {
      setError(err?.message || 'Failed to configure local mode');
      setSaving(false);
    }
  }, [onComplete]);

  // Detecting phase — hardware HUD
  if (phase === 'detecting') {
    const tierInfo = tier ? TIER_META[tier] : null;
    return (
      <section style={styles.container} aria-label="System profile detection">
        <div style={styles.header} aria-hidden="true">
          <div style={styles.headerLine} />
          <span style={styles.headerLabel}>SYSTEM PROFILE</span>
          <div style={styles.headerLine} />
        </div>

        <div style={styles.hwCard} role="status" aria-live="polite" aria-label="Hardware detection results">
          <div style={styles.hwRow}>
            <Cpu size={16} color="#00f0ff" aria-hidden="true" />
            <span style={styles.hwLabel}>GPU</span>
            <span style={styles.hwValue}>{hwProfile?.gpuName || 'Scanning...'}</span>
          </div>
          <div style={styles.hwRow}>
            <Zap size={16} color="#8A2BE2" aria-hidden="true" />
            <span style={styles.hwLabel}>VRAM</span>
            <span style={styles.hwValue}>
              {hwProfile ? `${Math.round((hwProfile.vramMB || 0) / 1024)} GB` : 'Scanning...'}
            </span>
          </div>
          <div style={styles.hwRow}>
            <Database size={16} color="#22c55e" aria-hidden="true" />
            <span style={styles.hwLabel}>RAM</span>
            <span style={styles.hwValue}>
              {hwProfile ? `${Math.round((hwProfile.ramMB || 0) / 1024)} GB` : 'Scanning...'}
            </span>
          </div>
        </div>

        {tierInfo && (
          <div style={styles.tierDisplay} role="status" aria-label={`Hardware tier: ${tierInfo.label}`}>
            <span style={{ ...styles.tierLabel, color: tierInfo.color }}>{tierInfo.label}</span>
            <div style={styles.tierDots} aria-hidden="true">
              {[1, 2, 3, 4, 5].map((n) => (
                <div
                  key={n}
                  style={{
                    ...styles.tierDot,
                    background: n <= tierInfo.dots ? tierInfo.color : 'rgba(255,255,255,0.08)',
                    boxShadow: n <= tierInfo.dots ? `0 0 6px ${tierInfo.color}40` : 'none',
                  }}
                />
              ))}
            </div>
          </div>
        )}

        <p style={styles.detectingText} aria-live="polite">Profiling hardware capabilities...</p>

        {/* Back button */}
        {onBack && (
          <button onClick={onBack} style={styles.backButton} aria-label="Go back to previous step">
            &#8592; Back
          </button>
        )}
      </section>
    );
  }

  // Form phase — API key entry
  const requiredKeys = effectiveConfigs.filter((k) => k.required);
  const optionalKeys = effectiveConfigs.filter((k) => !k.required);
  const tierInfo = tier ? TIER_META[tier] : null;

  return (
    <section style={styles.container} aria-label="Engine configuration">
      <div style={styles.header} aria-hidden="true">
        <div style={styles.headerLine} />
        <span style={styles.headerLabel}>ENGINE CONFIGURATION</span>
        <div style={styles.headerLine} />
      </div>

      {/* Compact hardware summary */}
      {tierInfo && (
        <div style={styles.hwSummary} role="status" aria-label={`Hardware: ${hwProfile?.gpuName || 'GPU'}, ${Math.round((hwProfile?.vramMB || 0) / 1024)} GB VRAM, tier ${tierInfo.label}`}>
          <span style={styles.hwSummaryText}>
            {hwProfile?.gpuName || 'GPU'} — {Math.round((hwProfile?.vramMB || 0) / 1024)} GB VRAM
          </span>
          <span style={{ ...styles.tierBadge, background: `${tierInfo.color}18`, color: tierInfo.color, borderColor: `${tierInfo.color}30` }}>
            {tierInfo.label}
          </span>
        </div>
      )}

      {isLocalCapable && (
        <p style={styles.localNote}>
          Your hardware supports local AI models. All API keys are optional — you can run entirely on-device.
        </p>
      )}

      {/* Scrollable key form */}
      <div style={styles.scrollArea} role="form" aria-label="API key configuration">
        {/* Required section */}
        {requiredKeys.length > 0 && (
          <fieldset style={{ ...styles.section, border: 'none', margin: 0, padding: 0 }}>
            <legend style={styles.sectionHead}>
              <span style={styles.sectionLabel}>Required</span>
              <div style={styles.sectionLine} />
            </legend>
            {requiredKeys.map((config) => (
              <div key={config.id} style={styles.keyField}>
                <div style={styles.keyLabelRow}>
                  <label htmlFor={`key-${config.id}`} style={styles.keyLabel}>{config.label}</label>
                  {existingKeys[config.id] && <span style={styles.keyConfigured}><Check size={10} aria-hidden="true" /> Saved</span>}
                </div>
                <input
                  id={`key-${config.id}`}
                  type="password"
                  value={keys[config.id] || ''}
                  onChange={(e) => updateKey(config.id, e.target.value)}
                  placeholder={existingKeys[config.id] ? keyHints[config.id] || '••••••••' : config.placeholder}
                  aria-describedby={`key-desc-${config.id}`}
                  aria-required={config.required}
                  style={{
                    ...styles.keyInput,
                    borderColor: ((keys[config.id] || '').trim() || existingKeys[config.id])
                      ? 'var(--accent-cyan-20)' : 'var(--onboarding-card-hover)',
                  }}
                />
                <span id={`key-desc-${config.id}`} style={styles.keyDesc}>{config.description}</span>
              </div>
            ))}
          </fieldset>
        )}

        {/* Optional section */}
        <fieldset style={{ ...styles.section, border: 'none', margin: 0, padding: 0 }}>
          <legend style={styles.sectionHead}>
            <span style={{ ...styles.sectionLabel, color: 'var(--text-30)' }}>
              {isLocalCapable ? 'Cloud Enhancements' : 'Optional'}
            </span>
            <div style={styles.sectionLine} />
          </legend>
          {optionalKeys.map((config) => (
            <div key={config.id} style={styles.keyField}>
              <div style={styles.keyLabelRow}>
                <label htmlFor={`key-${config.id}`} style={{ ...styles.keyLabel, color: 'var(--text-40)' }}>{config.label}</label>
                {existingKeys[config.id] && <span style={styles.keyConfigured}><Check size={10} aria-hidden="true" /> Saved</span>}
              </div>
              <input
                id={`key-${config.id}`}
                type="password"
                value={keys[config.id] || ''}
                onChange={(e) => updateKey(config.id, e.target.value)}
                placeholder={existingKeys[config.id] ? keyHints[config.id] || '••••••••' : config.placeholder}
                aria-describedby={`key-desc-${config.id}`}
                style={{
                  ...styles.keyInput,
                  borderColor: ((keys[config.id] || '').trim() || existingKeys[config.id])
                    ? 'var(--accent-cyan-20)' : 'var(--onboarding-card-hover)',
                }}
              />
              <span id={`key-desc-${config.id}`} style={styles.keyDesc}>
                {isLocalCapable && config.localDescription ? config.localDescription : config.description}
              </span>
            </div>
          ))}
        </fieldset>
      </div>

      {error && <p style={styles.error} role="alert">{error}</p>}

      <div style={styles.buttonRow}>
        <button
          onClick={handleSave}
          disabled={!canProceed}
          style={{
            ...styles.button,
            opacity: canProceed ? 1 : 0.35,
          }}
        >
          {saving ? 'Saving...' : 'Continue'}
          {!saving && <ChevronRight size={14} style={{ marginLeft: 4 }} aria-hidden="true" />}
        </button>

        {isLocalCapable && (
          <button
            onClick={handleSkipLocal}
            disabled={saving}
            style={styles.skipButton}
          >
            Run Locally
          </button>
        )}
      </div>

      <p style={styles.hint}>
        All keys stored locally. You can change these anytime in Settings.
      </p>

      {/* Back button */}
      {onBack && (
        <button onClick={onBack} style={styles.backButton} aria-label="Go back to previous step">
          &#8592; Back
        </button>
      )}
    </section>
  );
};

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 20,
    maxWidth: 520,
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
    background: 'linear-gradient(90deg, transparent, var(--accent-cyan-20), transparent)',
  },
  headerLabel: {
    fontSize: 11,
    fontWeight: 600,
    letterSpacing: '0.25em',
    color: 'var(--accent-cyan-70)',
    fontFamily: "'Space Grotesk', sans-serif",
    whiteSpace: 'nowrap',
  },
  hwCard: {
    width: '100%',
    background: 'var(--onboarding-card)',
    border: '1px solid var(--accent-cyan-10)',
    borderRadius: 12,
    padding: '20px 24px',
    display: 'flex',
    flexDirection: 'column',
    gap: 14,
  },
  hwRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
  },
  hwLabel: {
    fontSize: 10,
    fontWeight: 600,
    letterSpacing: '0.15em',
    color: 'var(--text-40)',
    fontFamily: "'JetBrains Mono', monospace",
    width: 40,
  },
  hwValue: {
    fontSize: 13,
    color: 'var(--text-primary)',
    fontFamily: "'Space Grotesk', sans-serif",
    flex: 1,
  },
  tierDisplay: {
    display: 'flex',
    alignItems: 'center',
    gap: 14,
  },
  tierLabel: {
    fontSize: 14,
    fontWeight: 600,
    letterSpacing: '0.1em',
    fontFamily: "'Space Grotesk', sans-serif",
  },
  tierDots: {
    display: 'flex',
    gap: 6,
  },
  tierDot: {
    width: 8,
    height: 8,
    borderRadius: '50%',
    transition: 'all 0.4s ease',
  },
  detectingText: {
    fontSize: 12,
    color: 'var(--text-30)',
    fontFamily: "'Space Grotesk', sans-serif",
    margin: 0,
  },
  hwSummary: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    width: '100%',
    justifyContent: 'center',
  },
  hwSummaryText: {
    fontSize: 12,
    color: 'var(--text-40)',
    fontFamily: "'JetBrains Mono', monospace",
  },
  tierBadge: {
    fontSize: 10,
    fontWeight: 600,
    letterSpacing: '0.1em',
    padding: '3px 10px',
    borderRadius: 20,
    border: '1px solid',
    fontFamily: "'Space Grotesk', sans-serif",
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
  scrollArea: {
    width: '100%',
    maxHeight: 340,
    overflowY: 'auto',
    display: 'flex',
    flexDirection: 'column',
    gap: 20,
    paddingRight: 4,
  },
  section: {
    display: 'flex',
    flexDirection: 'column',
    gap: 12,
  },
  sectionHead: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
  },
  sectionLabel: {
    fontSize: 10,
    fontWeight: 600,
    letterSpacing: '0.15em',
    color: 'var(--accent-cyan-50)',
    fontFamily: "'Space Grotesk', sans-serif",
    whiteSpace: 'nowrap',
    textTransform: 'uppercase',
  },
  sectionLine: {
    flex: 1,
    height: 1,
    background: 'var(--onboarding-card)',
  },
  keyField: {
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
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
  keyInput: {
    background: 'var(--onboarding-card)',
    border: '1px solid var(--onboarding-card-hover)',
    borderRadius: 6,
    padding: '8px 12px',
    fontSize: 12,
    color: 'var(--text-primary)',
    outline: 'none',
    fontFamily: "'JetBrains Mono', monospace",
    letterSpacing: '0.02em',
    transition: 'border-color 0.2s',
  },
  keyDesc: {
    fontSize: 10,
    color: 'var(--text-20)',
    lineHeight: 1.4,
    fontFamily: "'Inter', sans-serif",
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
  button: {
    background: 'var(--accent-cyan-10)',
    border: '1px solid var(--accent-cyan-20)',
    borderRadius: 8,
    padding: '10px 36px',
    fontSize: 13,
    fontWeight: 500,
    color: 'var(--accent-cyan-90)',
    letterSpacing: '0.05em',
    fontFamily: "'Space Grotesk', sans-serif",
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    transition: 'all 0.2s ease',
  },
  skipButton: {
    background: 'rgba(34, 197, 94, 0.08)',
    border: '1px solid rgba(34, 197, 94, 0.25)',
    borderRadius: 8,
    padding: '10px 24px',
    fontSize: 13,
    fontWeight: 500,
    color: 'rgba(34, 197, 94, 0.9)',
    letterSpacing: '0.05em',
    fontFamily: "'Space Grotesk', sans-serif",
    cursor: 'pointer',
    transition: 'all 0.2s ease',
  },
  hint: {
    fontSize: 10,
    color: 'var(--text-20)',
    margin: 0,
    textAlign: 'center',
    fontFamily: "'Inter', sans-serif",
  },
  backButton: {
    background: 'none',
    border: 'none',
    color: 'var(--text-40)',
    fontSize: 13,
    fontFamily: "'Space Grotesk', sans-serif",
    cursor: 'pointer',
    padding: '4px 8px',
    transition: 'color 0.2s ease',
    position: 'absolute' as const,
    bottom: 48,
    left: 48,
  },
};

export default EnginesStep;
