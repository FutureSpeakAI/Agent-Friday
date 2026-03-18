/**
 * HardwareStep.tsx — Step 2: Hardware detection + model downloading.
 *
 * "Your Hardware, Your AI." — Detects GPU/VRAM/RAM, determines tier,
 * then auto-downloads local AI models via SetupWizard IPC.
 * Three phases: detecting → recommending → downloading.
 */

import React, { useState, useCallback, useEffect, useRef } from 'react';
import { Cpu, Zap, Database, Download, Check, AlertCircle, X, Cloud, ExternalLink, RefreshCw } from 'lucide-react';
import NextButton from './shared/NextButton';

/** Hardware tiers mirroring src/main/hardware/tier-recommender.ts */
type TierName = 'whisper' | 'light' | 'standard' | 'full' | 'sovereign';

const TIER_META: Record<TierName, { label: string; color: string; segments: number; desc: string }> = {
  whisper:   { label: 'Whisper',   color: '#ef4444', segments: 1, desc: 'Cloud-dependent — limited local capability' },
  light:     { label: 'Light',     color: '#f97316', segments: 2, desc: 'Basic local models — cloud recommended' },
  standard:  { label: 'Standard',  color: '#eab308', segments: 3, desc: 'Solid local AI — cloud optional' },
  full:      { label: 'Full',      color: '#22c55e', segments: 4, desc: 'Full local suite — cloud rarely needed' },
  sovereign: { label: 'Sovereign', color: '#00f0ff', segments: 5, desc: 'Maximum local power — fully autonomous' },
};

type Phase = 'detecting' | 'checking-ollama' | 'recommending' | 'downloading' | 'complete';

interface HardwareProfile {
  gpuName?: string;
  vramMB?: number;
  ramMB?: number;
}

interface DownloadProgress {
  modelName: string;
  status: 'pending' | 'downloading' | 'complete' | 'failed';
  bytesDownloaded: number;
  bytesTotal: number;
  percentComplete: number;
}

interface HardwareStepProps {
  onComplete: (tier: TierName) => void;
  onBack?: () => void;
}

const HardwareStep: React.FC<HardwareStepProps> = ({ onComplete, onBack }) => {
  const [phase, setPhase] = useState<Phase>('detecting');
  const [hwProfile, setHwProfile] = useState<HardwareProfile | null>(null);
  const [tier, setTier] = useState<TierName | null>(null);
  const [modelList, setModelList] = useState<string[]>([]);
  const [downloads, setDownloads] = useState<DownloadProgress[]>([]);
  const [error, setError] = useState('');
  const [fadeIn, setFadeIn] = useState(false);
  const [ollamaRunning, setOllamaRunning] = useState<boolean | null>(null);
  const [checkingOllama, setCheckingOllama] = useState(false);
  const [whisperStatus, setWhisperStatus] = useState<'unchecked' | 'downloading' | 'ready' | 'failed'>('unchecked');
  const [whisperProgress, setWhisperProgress] = useState(0);
  const cleanupRef = useRef<Array<() => void>>([]);

  // Detect hardware on mount
  useEffect(() => {
    let cancelled = false;
    setTimeout(() => { if (!cancelled) setFadeIn(true); }, 100);

    (async () => {
      try {
        const profile = await window.eve.hardware.detect().catch(() => null);
        if (cancelled) return;

        let detectedTier: string = 'whisper';

        if (profile) {
          const p = profile as any;
          setHwProfile({
            gpuName: String(p?.gpu?.name || 'Unknown GPU'),
            vramMB: Math.round((p?.vram?.available || 0) / (1024 * 1024)),
            ramMB: Math.round((p?.ram?.total || 0) / (1024 * 1024)),
          });
          try {
            const t = await window.eve.hardware.getTier(p);
            detectedTier = t as string;
            if (!cancelled) setTier(t as TierName);
          } catch {
            if (!cancelled) setTier('whisper');
          }
        } else {
          setTier('whisper');
        }

        // Get model list for the detected tier
        try {
          const models = await window.eve.hardware.getModelList(detectedTier);
          if (!cancelled) setModelList((models as any[]).map((m) => typeof m === 'string' ? m : m.name));
        } catch { /* no models */ }

        if (!cancelled) {
          setTimeout(async () => {
            if (cancelled) return;
            // Check if Ollama is needed and running before showing model recommendations
            if (detectedTier !== 'whisper') {
              setPhase('checking-ollama');
              try {
                const health = await window.eve.ollama.getHealth() as any;
                if (!cancelled) setOllamaRunning(!!health?.running);
              } catch {
                if (!cancelled) setOllamaRunning(false);
              }
            } else {
              setPhase('recommending');
            }
          }, 2000);
        }
      } catch {
        if (!cancelled) {
          setTier('whisper');
          setPhase('recommending');
        }
      }
    })();

    return () => { cancelled = true; };
  }, []);

  // Start model downloads
  const startDownloads = useCallback(async () => {
    if (!tier) return;
    setPhase('downloading');
    setError('');

    try {
      // Start the setup wizard
      await window.eve.setup.start();
      await window.eve.setup.confirmTier(tier);

      // Subscribe to progress events
      // Note: preload bridge strips the IPC event — callbacks receive data only
      const unsub1 = window.eve.setup.onDownloadProgress((progressData: any) => {
        if (Array.isArray(progressData)) {
          setDownloads(progressData);
        }
      });
      const unsub2 = window.eve.setup.onComplete(() => {
        setPhase('complete');
      });
      const unsub3 = window.eve.setup.onError((errorData: any) => {
        setError(errorData?.error || 'Download failed');
      });

      cleanupRef.current = [unsub1, unsub2, unsub3];

      // Begin downloading
      await window.eve.setup.startDownload();
    } catch (err: any) {
      setError(err?.message || 'Failed to start model downloads');
    }
  }, [tier]);

  // Cleanup event listeners on unmount
  useEffect(() => {
    return () => {
      cleanupRef.current.forEach((fn) => fn());
    };
  }, []);

  // Auto-download Whisper tiny model when Ollama is confirmed running
  const ensureWhisperModel = useCallback(async () => {
    try {
      const downloaded = await window.eve.voice.whisper.isModelDownloaded('tiny');
      if (downloaded) {
        setWhisperStatus('ready');
        return;
      }

      setWhisperStatus('downloading');

      // Subscribe to download progress
      const unsub = window.eve.voice.whisper.onDownloadProgress(
        (progress: { downloaded: number; total: number }) => {
          if (progress.total > 0) {
            setWhisperProgress(Math.round((progress.downloaded / progress.total) * 100));
          }
        }
      );
      cleanupRef.current.push(unsub);

      await window.eve.voice.whisper.downloadModel('tiny');
      setWhisperStatus('ready');
    } catch (err: any) {
      console.warn('[HardwareStep] Whisper download failed (non-fatal):', err?.message);
      setWhisperStatus('failed');
      // Non-fatal — local conversation works in text-only mode without Whisper
    }
  }, []);

  // Trigger Whisper download when Ollama is confirmed running
  useEffect(() => {
    if (ollamaRunning === true && whisperStatus === 'unchecked') {
      ensureWhisperModel();
    }
  }, [ollamaRunning, whisperStatus, ensureWhisperModel]);

  // Re-check Ollama connectivity
  const recheckOllama = useCallback(async () => {
    setCheckingOllama(true);
    try {
      const health = await window.eve.ollama.getHealth() as any;
      setOllamaRunning(!!health?.running);
    } catch {
      setOllamaRunning(false);
    }
    setCheckingOllama(false);
  }, []);

  const handleSkipDownloads = useCallback(async () => {
    try {
      await window.eve.setup.skip();
      onComplete(tier || 'whisper');
    } catch {
      onComplete(tier || 'whisper');
    }
  }, [tier, onComplete]);

  const handleContinue = useCallback(async () => {
    try {
      await window.eve.setup.complete();
    } catch { /* best effort */ }
    onComplete(tier || 'whisper');
  }, [tier, onComplete]);

  const tierInfo = tier ? TIER_META[tier] : null;

  // ── Phase: Detecting ──
  if (phase === 'detecting') {
    return (
      <section style={{
        ...styles.container,
        opacity: fadeIn ? 1 : 0,
        transform: fadeIn ? 'translateY(0)' : 'translateY(16px)',
        transition: 'all 0.7s cubic-bezier(0.16, 1, 0.3, 1)',
      }} aria-label="Hardware detection">
        <div style={styles.headerBlock}>
          <h2 style={styles.heading}>Your Hardware, Your AI.</h2>
          <p style={styles.subtitle}>
            We run AI models directly on your machine. Let's see what your hardware can handle.
          </p>
        </div>

        <div style={styles.hwCard} role="status" aria-live="polite">
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
            <div style={styles.tierScale} aria-hidden="true">
              {[1, 2, 3, 4, 5].map((n) => (
                <div
                  key={n}
                  style={{
                    ...styles.tierSegment,
                    background: n <= tierInfo.segments ? tierInfo.color : 'rgba(255,255,255,0.06)',
                    boxShadow: n <= tierInfo.segments ? `0 0 8px ${tierInfo.color}30` : 'none',
                  }}
                />
              ))}
            </div>
          </div>
        )}
      </section>
    );
  }

  // ── Phase: Checking Ollama ──
  if (phase === 'checking-ollama') {
    return (
      <section style={styles.container} aria-label="Ollama dependency check">
        <div style={styles.headerBlock}>
          <h2 style={styles.heading}>Local AI Engine.</h2>
          <p style={styles.subtitle}>
            Agent Friday uses Ollama to run AI models directly on your machine.
            Ollama is free, open-source, and requires no account.
          </p>
        </div>

        {ollamaRunning === null ? (
          <div style={styles.ollamaStatusCard}>
            <RefreshCw size={16} color="var(--accent-cyan)" style={{ animation: 'spin 1s linear infinite' }} />
            <span style={styles.ollamaStatusText}>Checking for Ollama...</span>
          </div>
        ) : ollamaRunning ? (
          <>
            <div style={{ ...styles.ollamaStatusCard, borderColor: 'rgba(34, 197, 94, 0.2)' }}>
              <Check size={16} color="#22c55e" />
              <span style={{ ...styles.ollamaStatusText, color: 'rgba(34, 197, 94, 0.9)' }}>
                Ollama is running
              </span>
            </div>

            {/* Whisper model download status */}
            {whisperStatus === 'downloading' && (
              <div style={styles.ollamaStatusCard}>
                <Download size={14} color="var(--accent-cyan)" />
                <span style={styles.ollamaStatusText}>
                  Downloading voice model... {whisperProgress > 0 ? `${whisperProgress}%` : ''}
                </span>
              </div>
            )}
            {whisperStatus === 'ready' && (
              <div style={{ ...styles.ollamaStatusCard, borderColor: 'rgba(34, 197, 94, 0.1)' }}>
                <Check size={14} color="#22c55e" />
                <span style={{ ...styles.ollamaStatusText, color: 'rgba(34, 197, 94, 0.7)', fontSize: 12 }}>
                  Voice model ready
                </span>
              </div>
            )}
            {whisperStatus === 'failed' && (
              <div style={{ ...styles.ollamaStatusCard, borderColor: 'rgba(239, 68, 68, 0.1)' }}>
                <AlertCircle size={14} color="#ef4444" />
                <span style={{ ...styles.ollamaStatusText, color: 'rgba(239, 68, 68, 0.7)', fontSize: 11 }}>
                  Voice model download failed — text input will still work
                </span>
              </div>
            )}

            <NextButton
              label="Continue"
              onClick={() => setPhase('recommending')}
              disabled={whisperStatus === 'downloading'}
            />
          </>
        ) : (
          <>
            <div style={{ ...styles.ollamaStatusCard, borderColor: 'rgba(239, 68, 68, 0.2)' }}>
              <AlertCircle size={16} color="#ef4444" />
              <span style={{ ...styles.ollamaStatusText, color: 'rgba(239, 68, 68, 0.9)' }}>
                Ollama not detected
              </span>
            </div>

            <div style={styles.ollamaInstructionsCard}>
              <p style={styles.ollamaInstructionsTitle}>Quick Setup (2 minutes)</p>
              <div style={styles.ollamaStep}>
                <span style={styles.ollamaStepNum}>1</span>
                <span style={styles.ollamaStepText}>
                  Download Ollama from{' '}
                  <span
                    style={styles.ollamaLink}
                    onClick={() => window.eve?.shell?.openPath?.('https://ollama.com/download')}
                    role="link"
                    tabIndex={0}
                  >
                    ollama.com/download <ExternalLink size={10} style={{ verticalAlign: 'middle' }} />
                  </span>
                </span>
              </div>
              <div style={styles.ollamaStep}>
                <span style={styles.ollamaStepNum}>2</span>
                <span style={styles.ollamaStepText}>Run the installer (no account needed)</span>
              </div>
              <div style={styles.ollamaStep}>
                <span style={styles.ollamaStepNum}>3</span>
                <span style={styles.ollamaStepText}>
                  Ollama starts automatically — click "Check Again" below
                </span>
              </div>
            </div>

            <div style={styles.buttonRow}>
              <NextButton
                label={checkingOllama ? 'Checking...' : 'Check Again'}
                onClick={recheckOllama}
                disabled={checkingOllama}
                loading={checkingOllama}
              />
              <NextButton
                label="Skip — Use Cloud Only"
                onClick={() => {
                  setTier('whisper');
                  setModelList([]);
                  setPhase('recommending');
                }}
                variant="skip"
              />
            </div>

            <p style={styles.hint}>
              Without Ollama, Agent Friday works in cloud-only mode using API keys.
            </p>
          </>
        )}
      </section>
    );
  }

  // ── Phase: Recommending ──
  if (phase === 'recommending') {
    return (
      <section style={styles.container} aria-label="Model recommendation">
        <div style={styles.headerBlock}>
          <h2 style={styles.heading}>Your Hardware, Your AI.</h2>
          <p style={styles.subtitle}>
            Based on your hardware, we'll install local AI models so your data stays on your machine.
          </p>
        </div>

        {/* Hardware summary */}
        {tierInfo && (
          <div style={styles.hwSummary}>
            <span style={styles.hwSummaryText}>
              {hwProfile?.gpuName || 'GPU'} — {Math.round((hwProfile?.vramMB || 0) / 1024)} GB VRAM
            </span>
            <span style={{
              ...styles.tierBadge,
              background: `${tierInfo.color}18`,
              color: tierInfo.color,
              borderColor: `${tierInfo.color}30`,
            }}>
              {tierInfo.label}
            </span>
          </div>
        )}

        {tierInfo && (
          <p style={styles.tierDesc}>{tierInfo.desc}</p>
        )}

        {/* Model list — or cloud-only explanation for Whisper tier */}
        {modelList.length > 0 ? (
          <div style={styles.modelListCard}>
            <div style={styles.modelListHeader}>
              <Download size={14} color="var(--accent-cyan)" aria-hidden="true" />
              <span style={styles.modelListTitle}>Models to Install</span>
              <span style={styles.modelCount}>{modelList.length}</span>
            </div>
            <div style={styles.modelNames}>
              {modelList.map((name) => (
                <span key={name} style={styles.modelChip}>{name}</span>
              ))}
            </div>
          </div>
        ) : tier === 'whisper' ? (
          <div style={styles.cloudOnlyCard}>
            <div style={styles.cloudOnlyHeader}>
              <Cloud size={16} color="#8A2BE2" aria-hidden="true" />
              <span style={styles.cloudOnlyTitle}>Cloud Mode</span>
            </div>
            <p style={styles.cloudOnlyDesc}>
              Your device doesn't have dedicated GPU memory, so Friday will use cloud AI services
              instead of local models. This works great — you just need an API key.
            </p>
            <div style={styles.cloudOnlyFeatures}>
              <span style={styles.cloudFeature}>Voice via Gemini Live</span>
              <span style={styles.cloudFeature}>Reasoning via Claude</span>
              <span style={styles.cloudFeature}>Text chat always works</span>
            </div>
            <p style={styles.cloudOnlyNote}>
              No downloads needed. You'll set up API keys in the next step.
            </p>
          </div>
        ) : null}

        <div style={styles.buttonRow}>
          {modelList.length > 0 ? (
            <>
              <NextButton label="Install Models" onClick={startDownloads} />
              <NextButton
                label="Skip Downloads"
                onClick={handleSkipDownloads}
                variant="skip"
              />
            </>
          ) : (
            <NextButton label="Continue" onClick={handleSkipDownloads} />
          )}
        </div>

        {modelList.length > 0 && (
          <p style={styles.hint}>
            Downloads can be large. You can skip and install models later from Settings.
          </p>
        )}
      </section>
    );
  }

  // ── Phase: Downloading ──
  if (phase === 'downloading') {
    const totalModels = downloads.length;
    const completedModels = downloads.filter((d) => d.status === 'complete').length;
    const failedModels = downloads.filter((d) => d.status === 'failed').length;
    const overallPercent = totalModels > 0
      ? Math.round(downloads.reduce((sum, d) => sum + d.percentComplete, 0) / totalModels)
      : 0;

    return (
      <section style={styles.container} aria-label="Model downloads">
        <div style={styles.headerBlock}>
          <h2 style={styles.heading}>Installing Local AI.</h2>
          <p style={styles.subtitle}>
            Downloading models to your machine. This may take a few minutes.
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
            {completedModels}/{totalModels} models — {overallPercent}%
          </span>
        </div>

        {/* Per-model progress */}
        <div style={styles.downloadList}>
          {downloads.map((dl) => (
            <div key={dl.modelName} style={styles.downloadRow}>
              <div style={styles.downloadIcon}>
                {dl.status === 'complete' && <Check size={12} color="#22c55e" />}
                {dl.status === 'failed' && <X size={12} color="#ef4444" />}
                {dl.status === 'downloading' && <Download size={12} color="#00f0ff" />}
                {dl.status === 'pending' && <span style={styles.pendingDot} />}
              </div>
              <span style={styles.downloadName}>{dl.modelName}</span>
              <div style={styles.downloadBarWrap}>
                <div style={{
                  ...styles.downloadBar,
                  width: `${dl.percentComplete}%`,
                  background: dl.status === 'failed' ? '#ef4444' : 'var(--accent-cyan)',
                }} />
              </div>
              <span style={styles.downloadPercent}>
                {dl.status === 'failed' ? 'Failed' : `${dl.percentComplete}%`}
              </span>
            </div>
          ))}
        </div>

        {error && (
          <div style={styles.errorRow} role="alert">
            <AlertCircle size={14} color="#ef4444" />
            <span style={styles.errorText}>{error}</span>
          </div>
        )}

        {failedModels > 0 && failedModels + completedModels === totalModels && (
          <div style={styles.buttonRow}>
            <NextButton label="Continue Anyway" onClick={handleContinue} />
          </div>
        )}

        <NextButton
          label="Skip Remaining"
          onClick={handleSkipDownloads}
          variant="skip"
        />
      </section>
    );
  }

  // ── Phase: Complete ──
  return (
    <section style={styles.container} aria-label="Models installed">
      <div style={styles.headerBlock}>
        <h2 style={styles.heading}>Local AI Ready.</h2>
        <p style={styles.subtitle}>
          Your AI models are installed and ready. Your data stays on your machine.
        </p>
      </div>

      <div style={styles.completeBadge}>
        <Check size={20} color="#22c55e" />
        <span style={styles.completeText}>
          {downloads.filter((d) => d.status === 'complete').length} models installed
        </span>
      </div>

      <NextButton label="Continue" onClick={handleContinue} />
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
  hwCard: {
    width: '100%',
    background: 'var(--onboarding-card)',
    border: '1px solid rgba(255, 255, 255, 0.04)',
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
  tierScale: {
    display: 'flex',
    gap: 3,
  },
  tierSegment: {
    width: 28,
    height: 6,
    borderRadius: 3,
    transition: 'all 0.4s ease',
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
  tierDesc: {
    fontSize: 12,
    color: 'var(--text-40)',
    textAlign: 'center',
    margin: 0,
    fontFamily: "'Inter', sans-serif",
  },
  modelListCard: {
    width: '100%',
    background: 'var(--onboarding-card)',
    border: '1px solid rgba(255, 255, 255, 0.04)',
    borderRadius: 12,
    padding: '16px 20px',
  },
  modelListHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    marginBottom: 12,
  },
  modelListTitle: {
    fontSize: 12,
    fontWeight: 500,
    color: 'var(--text-60)',
    fontFamily: "'Space Grotesk', sans-serif",
    flex: 1,
  },
  modelCount: {
    fontSize: 10,
    fontWeight: 600,
    color: 'var(--accent-cyan)',
    fontFamily: "'JetBrains Mono', monospace",
  },
  modelNames: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: 6,
  },
  modelChip: {
    fontSize: 10,
    fontWeight: 500,
    color: 'var(--text-40)',
    padding: '3px 10px',
    borderRadius: 4,
    background: 'rgba(255, 255, 255, 0.03)',
    border: '1px solid rgba(255, 255, 255, 0.05)',
    fontFamily: "'JetBrains Mono', monospace",
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
  cloudOnlyCard: {
    width: '100%',
    background: 'var(--onboarding-card)',
    border: '1px solid rgba(138, 43, 226, 0.15)',
    borderRadius: 12,
    padding: '20px 24px',
    display: 'flex',
    flexDirection: 'column',
    gap: 12,
  },
  cloudOnlyHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
  },
  cloudOnlyTitle: {
    fontSize: 13,
    fontWeight: 600,
    color: '#8A2BE2',
    letterSpacing: '0.05em',
    fontFamily: "'Space Grotesk', sans-serif",
  },
  cloudOnlyDesc: {
    fontSize: 12,
    color: 'var(--text-40)',
    lineHeight: 1.6,
    margin: 0,
    fontFamily: "'Inter', sans-serif",
  },
  cloudOnlyFeatures: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: 6,
  },
  cloudFeature: {
    fontSize: 10,
    fontWeight: 500,
    color: 'rgba(138, 43, 226, 0.7)',
    padding: '3px 10px',
    borderRadius: 4,
    background: 'rgba(138, 43, 226, 0.06)',
    border: '1px solid rgba(138, 43, 226, 0.12)',
    fontFamily: "'JetBrains Mono', monospace",
  },
  cloudOnlyNote: {
    fontSize: 10,
    color: 'var(--text-20)',
    margin: 0,
    fontStyle: 'italic',
    fontFamily: "'Inter', sans-serif",
  },
  ollamaStatusCard: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    padding: '14px 20px',
    background: 'var(--onboarding-card)',
    border: '1px solid rgba(255, 255, 255, 0.04)',
    borderRadius: 10,
    width: '100%',
    justifyContent: 'center',
  },
  ollamaStatusText: {
    fontSize: 13,
    fontWeight: 500,
    color: 'var(--text-60)',
    fontFamily: "'Space Grotesk', sans-serif",
  },
  ollamaInstructionsCard: {
    width: '100%',
    background: 'var(--onboarding-card)',
    border: '1px solid rgba(255, 255, 255, 0.04)',
    borderRadius: 12,
    padding: '20px 24px',
    display: 'flex',
    flexDirection: 'column' as const,
    gap: 14,
  },
  ollamaInstructionsTitle: {
    fontSize: 12,
    fontWeight: 600,
    color: 'var(--text-60)',
    letterSpacing: '0.05em',
    fontFamily: "'Space Grotesk', sans-serif",
    margin: 0,
  },
  ollamaStep: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 12,
  },
  ollamaStepNum: {
    width: 20,
    height: 20,
    borderRadius: '50%',
    background: 'rgba(0, 240, 255, 0.08)',
    border: '1px solid rgba(0, 240, 255, 0.2)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: 10,
    fontWeight: 600,
    color: 'var(--accent-cyan)',
    fontFamily: "'JetBrains Mono', monospace",
    flexShrink: 0,
  },
  ollamaStepText: {
    fontSize: 12,
    color: 'var(--text-40)',
    lineHeight: 1.6,
    fontFamily: "'Inter', sans-serif",
  },
  ollamaLink: {
    color: 'var(--accent-cyan)',
    cursor: 'pointer',
    textDecoration: 'underline',
    textUnderlineOffset: '2px',
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

export default HardwareStep;
