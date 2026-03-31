/**
 * ModelsStep.tsx — Onboarding step: Choose local AI models.
 *
 * "Choose Your Models." — Lets the user explicitly pick which local AI models
 * to download/use across four categories: Chat LLM, Whisper STT, TTS engine,
 * and Embeddings. Calculates estimated disk + VRAM usage from selections and
 * checks Ollama connectivity on mount.
 *
 * After selection, orchestrates sequential downloads of all required binaries
 * and models with per-item progress UI (whisper binary, whisper model, ollama
 * models, TTS binaries, chatterbox setup).
 */

import React, { useState, useCallback, useEffect, useMemo, useRef } from 'react';
import { Cpu, Download, Check, AlertCircle, ExternalLink, X } from 'lucide-react';
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

type Phase = 'selecting' | 'downloading' | 'complete';

interface DownloadItem {
  name: string;
  type: 'whisper-binary' | 'whisper-model' | 'ollama-pull' | 'tts-binary' | 'tts-model' | 'chatterbox';
  status: 'pending' | 'downloading' | 'complete' | 'failed';
  percent: number;
  error?: string;
  /** For ollama-pull: the model name to filter progress events */
  modelName?: string;
  /** For whisper-model: the size to download */
  whisperSize?: string;
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

// ── Manifest builder ──

function buildManifest(selections: ModelSelections): DownloadItem[] {
  const tasks: DownloadItem[] = [];

  // Whisper binary + model
  if (selections.whisperModel) {
    tasks.push({
      name: 'Whisper STT Binary',
      type: 'whisper-binary',
      status: 'pending',
      percent: 0,
    });
    tasks.push({
      name: `Whisper Model (${selections.whisperModel})`,
      type: 'whisper-model',
      status: 'pending',
      percent: 0,
      whisperSize: selections.whisperModel,
    });
  }

  // Chat model via Ollama
  if (selections.chatModel) {
    tasks.push({
      name: `Chat Model (${selections.chatModel})`,
      type: 'ollama-pull',
      status: 'pending',
      percent: 0,
      modelName: selections.chatModel,
    });
  }

  // Embedding model via Ollama
  if (selections.embeddingModel) {
    tasks.push({
      name: `Embedding Model (${selections.embeddingModel})`,
      type: 'ollama-pull',
      status: 'pending',
      percent: 0,
      modelName: selections.embeddingModel,
    });
  }

  // TTS
  if (selections.ttsEngine === 'kokoro') {
    tasks.push({
      name: 'TTS Binary (Kokoro)',
      type: 'tts-binary',
      status: 'pending',
      percent: 0,
    });
    tasks.push({
      name: 'TTS Voice Model',
      type: 'tts-model',
      status: 'pending',
      percent: 0,
    });
  } else if (selections.ttsEngine === 'chatterbox') {
    tasks.push({
      name: 'Chatterbox TTS',
      type: 'chatterbox',
      status: 'pending',
      percent: 0,
    });
  }

  return tasks;
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

  // Download phase state
  const [phase, setPhase] = useState<Phase>('selecting');
  const [downloads, setDownloads] = useState<DownloadItem[]>([]);
  const [downloadError, setDownloadError] = useState('');
  const cleanupRef = useRef<Array<() => void>>([]);
  const skipRef = useRef(false);

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

  // Cleanup event listeners on unmount
  useEffect(() => {
    return () => {
      cleanupRef.current.forEach((fn) => fn());
    };
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

  // ── Sequential download executor ──

  const executeManifest = useCallback(async (tasks: DownloadItem[], selections: ModelSelections) => {
    setDownloads([...tasks]);
    setDownloadError('');

    for (let i = 0; i < tasks.length; i++) {
      if (skipRef.current) break;

      // Mark current as downloading
      tasks[i].status = 'downloading';
      setDownloads([...tasks]);

      try {
        await executeTask(tasks[i], (percent) => {
          tasks[i].percent = percent;
          setDownloads([...tasks]);
        });

        tasks[i].status = 'complete';
        tasks[i].percent = 100;
      } catch (err: any) {
        tasks[i].status = 'failed';
        tasks[i].error = err?.message || 'Download failed';
        setDownloadError(err?.message || 'Download failed');
      }

      setDownloads([...tasks]);
    }

    // All done — check results
    const anySucceeded = tasks.some((t) => t.status === 'complete');
    const allFailed = tasks.every((t) => t.status === 'failed');

    if (!allFailed && !skipRef.current) {
      setPhase('complete');
    }
    // If all failed, stay on downloading phase — user sees "Continue Anyway"
  }, []);

  const executeTask = useCallback(async (task: DownloadItem, onProgress: (p: number) => void): Promise<void> => {
    // Cleanup any previous listeners for this task
    const taskCleanups: Array<() => void> = [];

    try {
      switch (task.type) {
        case 'whisper-binary': {
          const unsub = window.eve.voice.binaries.onDownloadProgress((data) => {
            if (data.binary === 'whisper' && data.total > 0) {
              onProgress(Math.round((data.downloaded / data.total) * 100));
            }
          });
          taskCleanups.push(unsub);
          cleanupRef.current.push(unsub);
          await window.eve.voice.binaries.ensureWhisper();
          break;
        }

        case 'whisper-model': {
          const unsub = window.eve.voice.whisper.onDownloadProgress((data) => {
            if (data.total > 0) {
              onProgress(Math.round((data.downloaded / data.total) * 100));
            }
          });
          taskCleanups.push(unsub);
          cleanupRef.current.push(unsub);
          await window.eve.voice.whisper.downloadModel(task.whisperSize);
          break;
        }

        case 'ollama-pull': {
          const targetModel = task.modelName!;
          // First check if already available
          try {
            const available = await window.eve.ollama.isModelAvailable(targetModel);
            if (available) {
              onProgress(100);
              return;
            }
          } catch { /* proceed with pull */ }

          await new Promise<void>((resolve, reject) => {
            const unsub = window.eve.ollama.onPullProgress((data: any) => {
              if (data.modelName !== targetModel) return;

              if (data.status === 'success') {
                onProgress(100);
                resolve();
                return;
              }
              if (data.status === 'error') {
                reject(new Error(data.error || `Failed to pull ${targetModel}`));
                return;
              }
              if (data.completed && data.total && data.total > 0) {
                onProgress(Math.round((data.completed / data.total) * 100));
              }
            });
            taskCleanups.push(unsub);
            cleanupRef.current.push(unsub);

            window.eve.ollama.pullModel(targetModel).then(() => {
              // pullModel resolves when complete — if onPullProgress hasn't
              // already resolved us, do it now
              onProgress(100);
              resolve();
            }).catch(reject);
          });
          break;
        }

        case 'tts-binary': {
          const unsub = window.eve.voice.binaries.onDownloadProgress((data) => {
            if (data.binary === 'tts' && data.total > 0) {
              onProgress(Math.round((data.downloaded / data.total) * 100));
            }
          });
          taskCleanups.push(unsub);
          cleanupRef.current.push(unsub);
          await window.eve.voice.binaries.ensureTTS();
          break;
        }

        case 'tts-model': {
          const unsub = window.eve.voice.binaries.onDownloadProgress((data) => {
            if (data.binary === 'tts-model' && data.total > 0) {
              onProgress(Math.round((data.downloaded / data.total) * 100));
            }
          });
          taskCleanups.push(unsub);
          cleanupRef.current.push(unsub);
          await window.eve.voice.binaries.ensureTTSModel();
          break;
        }

        case 'chatterbox': {
          const unsub = window.eve.voice.chatterbox.onSetupProgress((data) => {
            onProgress(data.percent);
          });
          taskCleanups.push(unsub);
          cleanupRef.current.push(unsub);
          await window.eve.voice.chatterbox.setup();
          break;
        }
      }
    } finally {
      // Unsubscribe task-specific listeners
      taskCleanups.forEach((fn) => fn());
    }
  }, []);

  // ── Save settings and start downloads ──

  const handleDownloadAndContinue = useCallback(async () => {
    const selections: ModelSelections = {
      chatModel: isCustomChat ? (customChatModel.trim() || null) : chatModel,
      whisperModel,
      ttsEngine,
      embeddingModel,
    };

    try {
      // Save voice engine preference
      if (ttsEngine && ttsEngine !== 'cloud') {
        await window.eve.settings.set('voiceEngine', ttsEngine);
      } else if (ttsEngine === 'cloud') {
        await window.eve.settings.set('voiceEngine', 'elevenlabs');
      }

      // Save model selections to settings
      if (selections.chatModel) {
        await window.eve.settings.set('localModelId', selections.chatModel);
        await window.eve.settings.set('localModelEnabled', true);
      }
      if (selections.whisperModel) {
        await window.eve.settings.set('whisperModel', selections.whisperModel);
      }
      if (selections.embeddingModel) {
        await window.eve.settings.set('embeddingModel', selections.embeddingModel);
      }
    } catch {
      // Best effort — continue anyway
    }

    // Build download manifest
    const manifest = buildManifest(selections);

    if (manifest.length === 0) {
      // Nothing to download — skip straight to completion
      onComplete(selections);
      return;
    }

    // Switch to download phase
    skipRef.current = false;
    setPhase('downloading');
    // Store selections for use in completion
    selectionsRef.current = selections;
    executeManifest(manifest, selections);
  }, [chatModel, customChatModel, isCustomChat, whisperModel, ttsEngine, embeddingModel, onComplete, executeManifest]);

  const selectionsRef = useRef<ModelSelections>({
    chatModel: null, whisperModel: null, ttsEngine: null, embeddingModel: null,
  });

  const handleSkip = useCallback(async () => {
    try {
      await window.eve.settings.set('localModelEnabled', false);
    } catch {
      // Best effort — continue even if setting fails
    }
    onComplete({ chatModel: null, whisperModel: null, ttsEngine: null, embeddingModel: null });
  }, [onComplete]);

  const handleSkipRemaining = useCallback(() => {
    skipRef.current = true;
    // Clean up listeners
    cleanupRef.current.forEach((fn) => fn());
    cleanupRef.current = [];
    onComplete(selectionsRef.current);
  }, [onComplete]);

  const handleContinueAnyway = useCallback(() => {
    onComplete(selectionsRef.current);
  }, [onComplete]);

  const handleComplete = useCallback(() => {
    onComplete(selectionsRef.current);
  }, [onComplete]);

  const tierInfo = detectedTier ? TIER_META[detectedTier] : null;

  // ── Phase: Downloading ──
  if (phase === 'downloading') {
    const totalItems = downloads.length;
    const completedItems = downloads.filter((d) => d.status === 'complete').length;
    const failedItems = downloads.filter((d) => d.status === 'failed').length;
    const finishedItems = completedItems + failedItems;
    const overallPercent = totalItems > 0
      ? Math.round(downloads.reduce((sum, d) => sum + d.percent, 0) / totalItems)
      : 0;
    const allFinished = finishedItems === totalItems;

    return (
      <section
        style={{
          ...styles.container,
          opacity: 1,
          transition: 'all 0.7s cubic-bezier(0.16, 1, 0.3, 1)',
        }}
        aria-label="Model downloads"
      >
        <div style={styles.headerBlock}>
          <h2 style={styles.heading}>Installing Voice & Models.</h2>
          <p style={styles.subtitle}>
            Downloading selected models and voice components to your machine.
          </p>
        </div>

        {/* Overall progress */}
        <div style={styles.overallProgress}>
          <div style={styles.overallBar}>
            <div style={{
              ...styles.overallFill,
              width: `${overallPercent}%`,
            }} />
          </div>
          <span style={styles.overallText}>
            {completedItems}/{totalItems} items &mdash; {overallPercent}%
          </span>
        </div>

        {/* Per-item progress */}
        <div style={styles.downloadList}>
          {downloads.map((dl, idx) => (
            <div key={idx} style={styles.downloadRow}>
              <div style={styles.downloadIcon}>
                {dl.status === 'complete' && <Check size={12} color="#22c55e" />}
                {dl.status === 'failed' && <X size={12} color="#ef4444" />}
                {dl.status === 'downloading' && <Download size={12} color="#00f0ff" />}
                {dl.status === 'pending' && <span style={styles.pendingDot} />}
              </div>
              <span style={styles.downloadName}>{dl.name}</span>
              <div style={styles.downloadBarWrap}>
                <div style={{
                  ...styles.downloadBar,
                  width: `${dl.percent}%`,
                  background: dl.status === 'failed' ? '#ef4444' : 'var(--accent-cyan)',
                }} />
              </div>
              <span style={styles.downloadPercent}>
                {dl.status === 'failed' ? 'Failed' : `${dl.percent}%`}
              </span>
            </div>
          ))}
        </div>

        {downloadError && (
          <div style={styles.errorRow} role="alert">
            <AlertCircle size={14} color="#ef4444" />
            <span style={styles.errorText}>{downloadError}</span>
          </div>
        )}

        {allFinished && failedItems > 0 && (
          <div style={styles.buttonRow}>
            <NextButton label="Continue Anyway" onClick={handleContinueAnyway} />
          </div>
        )}

        <NextButton
          label="Skip Remaining"
          onClick={handleSkipRemaining}
          variant="skip"
        />
      </section>
    );
  }

  // ── Phase: Complete ──
  if (phase === 'complete') {
    const completedCount = downloads.filter((d) => d.status === 'complete').length;

    return (
      <section style={styles.container} aria-label="Models installed">
        <div style={styles.headerBlock}>
          <h2 style={styles.heading}>Models & Voice Ready.</h2>
          <p style={styles.subtitle}>
            Your selected models and voice components are installed and ready.
          </p>
        </div>

        <div style={styles.completeBadge}>
          <Check size={20} color="#22c55e" />
          <span style={styles.completeText}>
            {completedCount} item{completedCount !== 1 ? 's' : ''} installed
          </span>
        </div>

        <NextButton label="Continue" onClick={handleComplete} />
      </section>
    );
  }

  // ── Phase: Selecting (default) ──
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
          label="Download & Continue"
          onClick={handleDownloadAndContinue}
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

  // ── Download phase styles (matching HardwareStep) ──
  overallProgress: {
    width: '100%',
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
    alignItems: 'center',
  },
  overallBar: {
    width: '100%',
    height: 6,
    borderRadius: 3,
    background: 'rgba(255, 255, 255, 0.06)',
    overflow: 'hidden',
  },
  overallFill: {
    height: '100%',
    borderRadius: 3,
    background: 'var(--accent-cyan)',
    transition: 'width 0.3s ease',
    boxShadow: '0 0 8px var(--accent-cyan-30)',
  },
  overallText: {
    fontSize: 12,
    color: 'var(--text-50)',
    fontFamily: "'JetBrains Mono', monospace",
  },
  downloadList: {
    width: '100%',
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
    maxHeight: 280,
    overflowY: 'auto',
  },
  downloadRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    padding: '8px 12px',
    background: 'var(--onboarding-card)',
    border: '1px solid rgba(255, 255, 255, 0.04)',
    borderRadius: 8,
  },
  downloadIcon: {
    width: 16,
    height: 16,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
  },
  downloadName: {
    fontSize: 11,
    color: 'var(--text-60)',
    fontFamily: "'JetBrains Mono', monospace",
    flex: 1,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  downloadBarWrap: {
    width: 80,
    height: 4,
    borderRadius: 2,
    background: 'rgba(255, 255, 255, 0.06)',
    overflow: 'hidden',
    flexShrink: 0,
  },
  downloadBar: {
    height: '100%',
    borderRadius: 2,
    transition: 'width 0.3s ease',
  },
  downloadPercent: {
    fontSize: 10,
    color: 'var(--text-40)',
    fontFamily: "'JetBrains Mono', monospace",
    width: 40,
    textAlign: 'right',
    flexShrink: 0,
  },
  pendingDot: {
    width: 6,
    height: 6,
    borderRadius: '50%',
    background: 'rgba(255, 255, 255, 0.15)',
  },
  errorRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '8px 16px',
    background: 'rgba(239, 68, 68, 0.06)',
    border: '1px solid rgba(239, 68, 68, 0.2)',
    borderRadius: 8,
  },
  errorText: {
    fontSize: 12,
    color: '#ef4444',
    fontFamily: "'Inter', sans-serif",
  },
  completeBadge: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    padding: '16px 28px',
    background: 'rgba(34, 197, 94, 0.06)',
    border: '1px solid rgba(34, 197, 94, 0.2)',
    borderRadius: 10,
  },
  completeText: {
    fontSize: 14,
    color: 'rgba(34, 197, 94, 0.9)',
    fontFamily: "'Space Grotesk', sans-serif",
    fontWeight: 500,
  },
};

export default ModelsStep;
