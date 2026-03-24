/**
 * ModelsStep.tsx — Onboarding step: Choose local AI models.
 *
 * "Choose Your Models." — Lets the user explicitly pick which local AI models
 * to download/use across four categories: Chat LLM, Whisper STT, TTS engine,
 * and Embeddings. Calculates estimated disk + VRAM usage from selections and
 * checks Ollama connectivity on mount.
 */

import React, { useState, useCallback, useEffect, useMemo } from 'react';
import { Cpu, Download, Check, AlertCircle, ExternalLink } from 'lucide-react';
import NextButton from './shared/NextButton';
import CyberInput from './shared/CyberInput';

// ── Types ──

export type TierName = 'whisper' | 'light' | 'standard' | 'full' | 'sovereign';

export interface ModelSelections {
  chatModel: string | null;
  whisperModel: string | null;
  ttsEngine: string | null;
  embeddingModel: string | null;
}

interface ModelsStepProps {
  detectedTier: TierName | null;
  onComplete: (selections: ModelSelections) => void;
  onBack?: () => void;
}

// ── Size metadata ──

interface ModelOption {
  value: string | null;
  label: string;
  detail: string;
  diskGB: number;
  vramGB: number;
}

const CHAT_MODELS: ModelOption[] = [
  { value: 'llama3.2',    label: 'llama3.2 (3B)',    detail: 'Fast & lightweight',   diskGB: 2.0, vramGB: 2.5 },
  { value: 'llama3.1:8b', label: 'llama3.1:8b (8B)', detail: 'Smarter responses',    diskGB: 4.7, vramGB: 5.5 },
];

const WHISPER_MODELS: ModelOption[] = [
  { value: 'tiny',  label: 'tiny',  detail: 'Good accuracy',  diskGB: 0.039, vramGB: 0.4 },
  { value: 'base',  label: 'base',  detail: 'Better accuracy', diskGB: 0.074, vramGB: 0.5 },
  { value: 'small', label: 'small', detail: 'Best accuracy',  diskGB: 0.244, vramGB: 1.0 },
];

const TTS_OPTIONS: ModelOption[] = [
  { value: 'chatterbox', label: 'Chatterbox Turbo', detail: 'Voice cloning, requires GPU', diskGB: 1.2, vramGB: 2.0 },
  { value: 'kokoro',     label: 'Kokoro',           detail: 'Fast, lightweight, offline',  diskGB: 0.3, vramGB: 0.2 },
  { value: 'cloud',      label: 'Cloud (ElevenLabs)', detail: 'Highest quality, needs API key', diskGB: 0, vramGB: 0 },
];

const EMBEDDING_MODELS: ModelOption[] = [
  { value: 'nomic-embed-text', label: 'nomic-embed-text', detail: 'Semantic search', diskGB: 0.274, vramGB: 0.3 },
];

const TIER_META: Record<TierName, { label: string; color: string }> = {
  whisper:   { label: 'Whisper',   color: '#ef4444' },
  light:     { label: 'Light',     color: '#f97316' },
  standard:  { label: 'Standard',  color: '#eab308' },
  full:      { label: 'Full',      color: '#22c55e' },
  sovereign: { label: 'Sovereign', color: '#00f0ff' },
};

const TIER_ORDER: TierName[] = ['whisper', 'light', 'standard', 'full', 'sovereign'];

function tierAtLeast(tier: TierName | null, min: TierName): boolean {
  if (!tier) return false;
  return TIER_ORDER.indexOf(tier) >= TIER_ORDER.indexOf(min);
}

// ── Defaults per tier ──

function getDefaults(tier: TierName | null): ModelSelections {
  if (!tier || tier === 'whisper') {
    return { chatModel: null, whisperModel: 'tiny', ttsEngine: 'cloud', embeddingModel: null };
  }
  if (tier === 'light') {
    return { chatModel: 'llama3.2', whisperModel: 'tiny', ttsEngine: 'kokoro', embeddingModel: 'nomic-embed-text' };
  }
  if (tier === 'standard') {
    return { chatModel: 'llama3.2', whisperModel: 'tiny', ttsEngine: 'kokoro', embeddingModel: 'nomic-embed-text' };
  }
  // full / sovereign
  return { chatModel: 'llama3.1:8b', whisperModel: 'tiny', ttsEngine: 'chatterbox', embeddingModel: 'nomic-embed-text' };
}

// ── Component ──

const ModelsStep: React.FC<ModelsStepProps> = ({ detectedTier, onComplete, onBack }) => {
  const defaults = useMemo(() => getDefaults(detectedTier), [detectedTier]);

  const [chatModel, setChatModel] = useState<string | null>(defaults.chatModel);
  const [customChatModel, setCustomChatModel] = useState('');
  const [isCustomChat, setIsCustomChat] = useState(false);
  const [whisperModel, setWhisperModel] = useState<string | null>(defaults.whisperModel);
  const [ttsEngine, setTtsEngine] = useState<string | null>(defaults.ttsEngine);
  const [embeddingModel, setEmbeddingModel] = useState<string | null>(defaults.embeddingModel);

  const [ollamaStatus, setOllamaStatus] = useState<'checking' | 'connected' | 'disconnected'>('checking');
  const [fadeIn, setFadeIn] = useState(false);
  const [saving, setSaving] = useState(false);

  // Fade in on mount + check Ollama health
  useEffect(() => {
    let cancelled = false;
    setTimeout(() => { if (!cancelled) setFadeIn(true); }, 100);

    (async () => {
      try {
        const health = await window.eve.ollama.getHealth() as any;
        if (!cancelled) setOllamaStatus(health?.running ? 'connected' : 'disconnected');
      } catch {
        if (!cancelled) setOllamaStatus('disconnected');
      }
    })();

    return () => { cancelled = true; };
  }, []);

  // Calculate totals
  const totals = useMemo(() => {
    let disk = 0;
    let vram = 0;

    // Chat model
    if (isCustomChat && customChatModel.trim()) {
      // Estimate ~3 GB for unknown custom models
      disk += 3.0;
      vram += 3.0;
    } else if (chatModel) {
      const opt = CHAT_MODELS.find((m) => m.value === chatModel);
      if (opt) { disk += opt.diskGB; vram += opt.vramGB; }
    }

    // Whisper
    if (whisperModel) {
      const opt = WHISPER_MODELS.find((m) => m.value === whisperModel);
      if (opt) { disk += opt.diskGB; vram += opt.vramGB; }
    }

    // TTS
    if (ttsEngine) {
      const opt = TTS_OPTIONS.find((m) => m.value === ttsEngine);
      if (opt) { disk += opt.diskGB; vram += opt.vramGB; }
    }

    // Embeddings
    if (embeddingModel) {
      const opt = EMBEDDING_MODELS.find((m) => m.value === embeddingModel);
      if (opt) { disk += opt.diskGB; vram += opt.vramGB; }
    }

    return { disk, vram };
  }, [chatModel, customChatModel, isCustomChat, whisperModel, ttsEngine, embeddingModel]);

  const handleDownloadAndContinue = useCallback(async () => {
    setSaving(true);
    try {
      // Save voice engine preference
      if (ttsEngine && ttsEngine !== 'cloud') {
        await window.eve.settings.set('voiceEngine', ttsEngine);
      } else if (ttsEngine === 'cloud') {
        await window.eve.settings.set('voiceEngine', 'elevenlabs');
      }

      const selections: ModelSelections = {
        chatModel: isCustomChat ? (customChatModel.trim() || null) : chatModel,
        whisperModel,
        ttsEngine,
        embeddingModel,
      };

      // Save model selections to settings
      if (selections.chatModel) {
        await window.eve.settings.set('localModelId', selections.chatModel);
        await window.eve.settings.set('localModelEnabled', true);
        // Ensure the chat model is available in Ollama (pull if missing)
        try {
          const available = await window.eve.ollama.isModelAvailable(selections.chatModel);
          if (!available) {
            await window.eve.ollama.pullModel(selections.chatModel);
          }
        } catch {
          // Pull failed — user can still download later via settings
        }
      }
      // Save whisper model preference
      if (selections.whisperModel) {
        await window.eve.settings.set('whisperModel', selections.whisperModel);
      }
      // Save embedding model preference and pull if missing
      if (selections.embeddingModel) {
        await window.eve.settings.set('embeddingModel', selections.embeddingModel);
        try {
          const available = await window.eve.ollama.isModelAvailable(selections.embeddingModel);
          if (!available) {
            await window.eve.ollama.pullModel(selections.embeddingModel);
          }
        } catch {
          // Pull failed — embeddings will degrade gracefully
        }
      }

      onComplete(selections);
    } catch {
      // Best effort — continue anyway
      const selections: ModelSelections = {
        chatModel: isCustomChat ? (customChatModel.trim() || null) : chatModel,
        whisperModel,
        ttsEngine,
        embeddingModel,
      };
      onComplete(selections);
    }
  }, [chatModel, customChatModel, isCustomChat, whisperModel, ttsEngine, embeddingModel, onComplete]);

  const handleSkip = useCallback(async () => {
    try {
      await window.eve.settings.set('localModelEnabled', false);
    } catch {
      // Best effort — continue even if setting fails
    }
    onComplete({ chatModel: null, whisperModel: null, ttsEngine: null, embeddingModel: null });
  }, [onComplete]);

  const tierInfo = detectedTier ? TIER_META[detectedTier] : null;

  return (
    <section
      style={{
        ...styles.container,
        opacity: fadeIn ? 1 : 0,
        transform: fadeIn ? 'translateY(0)' : 'translateY(16px)',
        transition: 'all 0.7s cubic-bezier(0.16, 1, 0.3, 1)',
      }}
      aria-label="Model selection"
    >
      {/* Header */}
      <div style={styles.headerBlock}>
        <div style={styles.headingRow}>
          <h2 style={styles.heading}>Choose Your Models.</h2>
          {tierInfo && (
            <span
              style={{
                ...styles.tierBadge,
                background: `${tierInfo.color}18`,
                color: tierInfo.color,
                borderColor: `${tierInfo.color}30`,
              }}
            >
              {tierInfo.label}
            </span>
          )}
        </div>
        <p style={styles.subtitle}>
          Select which AI models to run locally. Higher quality models need more disk space and VRAM.
        </p>
      </div>

      {/* Scrollable sections */}
      <div style={styles.scrollArea}>

        {/* Section 1: Chat Model */}
        <div style={styles.sectionCard}>
          <div style={styles.sectionHeader}>
            <Cpu size={14} color="var(--accent-cyan)" aria-hidden="true" />
            <span style={styles.sectionTitle}>Chat Model (Local LLM)</span>
          </div>

          {CHAT_MODELS.map((model) => (
            <label
              key={model.value}
              style={{
                ...styles.radioCard,
                ...(chatModel === model.value && !isCustomChat ? styles.radioCardSelected : {}),
              }}
            >
              <input
                type="radio"
                name="chatModel"
                checked={chatModel === model.value && !isCustomChat}
                onChange={() => { setChatModel(model.value); setIsCustomChat(false); }}
                style={styles.radioInput}
              />
              <div style={styles.radioContent}>
                <div style={styles.radioLabelRow}>
                  <span style={styles.radioLabel}>{model.label}</span>
                  {model.value === 'llama3.2' && tierAtLeast(detectedTier, 'standard') && (
                    <span style={styles.recBadge}>REC</span>
                  )}
                  {model.value === 'llama3.1:8b' && tierAtLeast(detectedTier, 'full') && (
                    <span style={styles.recBadge}>REC</span>
                  )}
                </div>
                <span style={styles.radioDetail}>{model.detail}</span>
                <span style={styles.sizeInfo}>{model.diskGB} GB</span>
              </div>
            </label>
          ))}

          {/* Custom model */}
          <label
            style={{
              ...styles.radioCard,
              ...(isCustomChat ? styles.radioCardSelected : {}),
            }}
          >
            <input
              type="radio"
              name="chatModel"
              checked={isCustomChat}
              onChange={() => setIsCustomChat(true)}
              style={styles.radioInput}
            />
            <div style={{ ...styles.radioContent, flex: 1 }}>
              <span style={styles.radioLabel}>Custom</span>
              {isCustomChat && (
                <div style={styles.customInputRow}>
                  <div style={{ flex: 1 }}>
                    <CyberInput
                      id="custom-chat-model"
                      label="Model name (e.g. mistral:7b)"
                      value={customChatModel}
                      onChange={setCustomChatModel}
                      monospace
                    />
                  </div>
                  <NextButton
                    label="Pull from Ollama"
                    onClick={async () => {
                      const name = customChatModel.trim();
                      if (!name) return;
                      try {
                        await (window.eve.ollama as any).pullModel?.(name);
                      } catch { /* user will see download status elsewhere */ }
                    }}
                    variant="secondary"
                    disabled={!customChatModel.trim()}
                  />
                </div>
              )}
            </div>
          </label>

          {/* None */}
          <label
            style={{
              ...styles.radioCard,
              ...(chatModel === null && !isCustomChat ? styles.radioCardSelected : {}),
            }}
          >
            <input
              type="radio"
              name="chatModel"
              checked={chatModel === null && !isCustomChat}
              onChange={() => { setChatModel(null); setIsCustomChat(false); }}
              style={styles.radioInput}
            />
            <div style={styles.radioContent}>
              <span style={styles.radioLabel}>None</span>
              <span style={styles.radioDetail}>Cloud only (requires API key)</span>
            </div>
          </label>
        </div>

        {/* Section 2: Whisper STT */}
        <div style={styles.sectionCard}>
          <div style={styles.sectionHeader}>
            <Cpu size={14} color="var(--accent-cyan)" aria-hidden="true" />
            <span style={styles.sectionTitle}>Speech-to-Text (Whisper)</span>
          </div>

          {WHISPER_MODELS.map((model) => (
            <label
              key={model.value}
              style={{
                ...styles.radioCard,
                ...(whisperModel === model.value ? styles.radioCardSelected : {}),
              }}
            >
              <input
                type="radio"
                name="whisperModel"
                checked={whisperModel === model.value}
                onChange={() => setWhisperModel(model.value)}
                style={styles.radioInput}
              />
              <div style={styles.radioContent}>
                <div style={styles.radioLabelRow}>
                  <span style={styles.radioLabel}>{model.label}</span>
                  {model.value === 'tiny' && <span style={styles.recBadge}>REC</span>}
                </div>
                <span style={styles.radioDetail}>{model.detail}</span>
                <span style={styles.sizeInfo}>{model.diskGB >= 1 ? `${model.diskGB} GB` : `${Math.round(model.diskGB * 1000)} MB`}</span>
              </div>
            </label>
          ))}

          <label
            style={{
              ...styles.radioCard,
              ...(whisperModel === null ? styles.radioCardSelected : {}),
            }}
          >
            <input
              type="radio"
              name="whisperModel"
              checked={whisperModel === null}
              onChange={() => setWhisperModel(null)}
              style={styles.radioInput}
            />
            <div style={styles.radioContent}>
              <span style={styles.radioLabel}>None</span>
              <span style={styles.radioDetail}>Cloud STT or text-only</span>
            </div>
          </label>
        </div>

        {/* Section 3: TTS */}
        <div style={styles.sectionCard}>
          <div style={styles.sectionHeader}>
            <Cpu size={14} color="var(--accent-cyan)" aria-hidden="true" />
            <span style={styles.sectionTitle}>Text-to-Speech</span>
          </div>

          {TTS_OPTIONS.map((opt) => (
            <label
              key={opt.value}
              style={{
                ...styles.radioCard,
                ...(ttsEngine === opt.value ? styles.radioCardSelected : {}),
              }}
            >
              <input
                type="radio"
                name="ttsEngine"
                checked={ttsEngine === opt.value}
                onChange={() => setTtsEngine(opt.value)}
                style={styles.radioInput}
              />
              <div style={styles.radioContent}>
                <div style={styles.radioLabelRow}>
                  <span style={styles.radioLabel}>{opt.label}</span>
                  {opt.value === 'kokoro' && tierAtLeast(detectedTier, 'standard') && (
                    <span style={styles.recBadge}>REC</span>
                  )}
                </div>
                <span style={styles.radioDetail}>{opt.detail}</span>
                {opt.diskGB > 0 && (
                  <span style={styles.sizeInfo}>{opt.diskGB >= 1 ? `${opt.diskGB} GB` : `${Math.round(opt.diskGB * 1000)} MB`}</span>
                )}
              </div>
            </label>
          ))}

          <label
            style={{
              ...styles.radioCard,
              ...(ttsEngine === null ? styles.radioCardSelected : {}),
            }}
          >
            <input
              type="radio"
              name="ttsEngine"
              checked={ttsEngine === null}
              onChange={() => setTtsEngine(null)}
              style={styles.radioInput}
            />
            <div style={styles.radioContent}>
              <span style={styles.radioLabel}>None</span>
              <span style={styles.radioDetail}>Text-only responses</span>
            </div>
          </label>
        </div>

        {/* Section 4: Embeddings */}
        <div style={styles.sectionCard}>
          <div style={styles.sectionHeader}>
            <Cpu size={14} color="var(--accent-cyan)" aria-hidden="true" />
            <span style={styles.sectionTitle}>Embeddings (Semantic Memory)</span>
          </div>

          {EMBEDDING_MODELS.map((model) => (
            <label
              key={model.value}
              style={{
                ...styles.radioCard,
                ...(embeddingModel === model.value ? styles.radioCardSelected : {}),
              }}
            >
              <input
                type="radio"
                name="embeddingModel"
                checked={embeddingModel === model.value}
                onChange={() => setEmbeddingModel(model.value)}
                style={styles.radioInput}
              />
              <div style={styles.radioContent}>
                <div style={styles.radioLabelRow}>
                  <span style={styles.radioLabel}>{model.label}</span>
                  <span style={styles.recBadge}>REC</span>
                </div>
                <span style={styles.radioDetail}>{model.detail}</span>
                <span style={styles.sizeInfo}>{Math.round(model.diskGB * 1000)} MB</span>
              </div>
            </label>
          ))}

          <label
            style={{
              ...styles.radioCard,
              ...(embeddingModel === null ? styles.radioCardSelected : {}),
            }}
          >
            <input
              type="radio"
              name="embeddingModel"
              checked={embeddingModel === null}
              onChange={() => setEmbeddingModel(null)}
              style={styles.radioInput}
            />
            <div style={styles.radioContent}>
              <span style={styles.radioLabel}>None</span>
              <span style={styles.radioDetail}>Keyword search only</span>
            </div>
          </label>
        </div>
      </div>

      {/* Footer: totals + Ollama status */}
      <div style={styles.footer}>
        <span style={styles.totalLine}>
          Total download: ~{totals.disk.toFixed(1)} GB &middot; VRAM needed: ~{totals.vram.toFixed(1)} GB
        </span>

        {ollamaStatus === 'checking' && (
          <div style={styles.ollamaRow}>
            <Cpu size={12} color="var(--text-30)" aria-hidden="true" />
            <span style={styles.ollamaText}>Checking Ollama...</span>
          </div>
        )}
        {ollamaStatus === 'connected' && (
          <div style={styles.ollamaRow}>
            <Check size={12} color="#22c55e" aria-hidden="true" />
            <span style={{ ...styles.ollamaText, color: 'rgba(34, 197, 94, 0.9)' }}>
              Ollama is running
            </span>
          </div>
        )}
        {ollamaStatus === 'disconnected' && (
          <div style={styles.ollamaRow}>
            <AlertCircle size={12} color="#ef4444" aria-hidden="true" />
            <span style={{ ...styles.ollamaText, color: 'rgba(239, 68, 68, 0.9)' }}>
              Ollama not detected &mdash;{' '}
              <span
                style={styles.ollamaLink}
                role="link"
                tabIndex={0}
                onClick={() => window.eve?.shell?.openPath?.('https://ollama.com/download')}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    window.eve?.shell?.openPath?.('https://ollama.com/download');
                  }
                }}
              >
                Install Ollama <ExternalLink size={9} style={{ verticalAlign: 'middle' }} />
              </span>
            </span>
          </div>
        )}
      </div>

      {/* Action buttons */}
      <div style={styles.buttonRow}>
        <NextButton
          label={saving ? 'Saving...' : 'Download & Continue'}
          onClick={handleDownloadAndContinue}
          disabled={saving}
          loading={saving}
          icon={<Download size={14} />}
        />
        <NextButton
          label="Skip Downloads"
          onClick={handleSkip}
          variant="skip"
        />
      </div>
    </section>
  );
};

// ── Styles ──

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
  headingRow: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 12,
    marginBottom: 12,
  },
  heading: {
    fontSize: 28,
    fontWeight: 300,
    color: 'var(--text-primary)',
    fontFamily: "'Space Grotesk', sans-serif",
    letterSpacing: '0.05em',
    margin: 0,
  },
  tierBadge: {
    fontSize: 10,
    fontWeight: 600,
    letterSpacing: '0.1em',
    padding: '3px 10px',
    borderRadius: 20,
    border: '1px solid',
    fontFamily: "'Space Grotesk', sans-serif",
    flexShrink: 0,
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
    gap: 16,
    paddingRight: 4,
  },
  sectionCard: {
    width: '100%',
    background: 'var(--onboarding-card)',
    border: '1px solid rgba(255, 255, 255, 0.04)',
    borderRadius: 12,
    padding: '20px 24px',
    display: 'flex',
    flexDirection: 'column',
    gap: 10,
  },
  sectionHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    marginBottom: 4,
  },
  sectionTitle: {
    fontSize: 12,
    fontWeight: 600,
    color: 'var(--text-60)',
    letterSpacing: '0.05em',
    fontFamily: "'Space Grotesk', sans-serif",
  },
  radioCard: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 10,
    padding: '10px 14px',
    borderRadius: 8,
    border: '1px solid rgba(255, 255, 255, 0.04)',
    background: 'transparent',
    cursor: 'pointer',
    transition: 'all 0.15s ease',
  },
  radioCardSelected: {
    borderColor: 'var(--accent-cyan-30)',
    background: 'var(--accent-cyan-10)',
  },
  radioInput: {
    appearance: 'none' as any,
    WebkitAppearance: 'none' as any,
    width: 14,
    height: 14,
    borderRadius: '50%',
    border: '2px solid var(--text-20)',
    flexShrink: 0,
    marginTop: 2,
    cursor: 'pointer',
    transition: 'all 0.15s ease',
    background: 'transparent',
    position: 'relative' as const,
  },
  radioContent: {
    display: 'flex',
    flexDirection: 'column',
    gap: 2,
    minWidth: 0,
  },
  radioLabelRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  radioLabel: {
    fontSize: 12,
    fontWeight: 500,
    color: 'var(--text-primary)',
    fontFamily: "'Space Grotesk', sans-serif",
  },
  radioDetail: {
    fontSize: 11,
    color: 'var(--text-30)',
    fontFamily: "'Inter', sans-serif",
    lineHeight: 1.4,
  },
  sizeInfo: {
    fontSize: 10,
    color: 'var(--text-30)',
    fontFamily: "'JetBrains Mono', monospace",
  },
  recBadge: {
    fontSize: 9,
    fontWeight: 700,
    color: '#22c55e',
    background: 'rgba(34, 197, 94, 0.1)',
    border: '1px solid rgba(34, 197, 94, 0.2)',
    borderRadius: 4,
    padding: '2px 6px',
    letterSpacing: '0.05em',
    fontFamily: "'JetBrains Mono', monospace",
    flexShrink: 0,
  },
  customInputRow: {
    display: 'flex',
    alignItems: 'flex-end',
    gap: 8,
    marginTop: 6,
    width: '100%',
  },
  footer: {
    width: '100%',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 8,
  },
  totalLine: {
    fontSize: 11,
    color: 'var(--text-40)',
    fontFamily: "'JetBrains Mono', monospace",
    textAlign: 'center',
  },
  ollamaRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
  },
  ollamaText: {
    fontSize: 11,
    color: 'var(--text-40)',
    fontFamily: "'Space Grotesk', sans-serif",
  },
  ollamaLink: {
    color: 'var(--accent-cyan)',
    cursor: 'pointer',
    textDecoration: 'underline',
    textUnderlineOffset: '2px',
  },
  buttonRow: {
    display: 'flex',
    gap: 12,
    alignItems: 'center',
  },
};

export default ModelsStep;
