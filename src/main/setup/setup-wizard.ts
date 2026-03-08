/**
 * setup-wizard.ts -- Singleton that orchestrates the first-run setup flow.
 *
 * State machine: idle -> detecting -> recommending -> confirming
 *                -> downloading -> loading -> complete
 *
 * Detects hardware via HardwareProfiler, recommends a tier via
 * TierRecommender, downloads models via OllamaLifecycle.pullModel(),
 * and loads them via ModelOrchestrator.loadTierModels().
 *
 * Persistence: file-based marker in userData/setup-state.json.
 *
 * Sprint 6 P.1: "The Birth" -- SetupWizard
 */

import { app } from 'electron';
import fs from 'node:fs';
import path from 'node:path';
import { HardwareProfiler } from '../hardware/hardware-profiler';
import { recommend, getModelList } from '../hardware/tier-recommender';
import type { TierName, TierRecommendation } from '../hardware/tier-recommender';
import type { HardwareProfile } from '../hardware/hardware-profiler';
import { OllamaLifecycle } from '../ollama-lifecycle';
import { ModelOrchestrator } from '../hardware/model-orchestrator';

// -- Contract Types ----------------------------------------------------------

export type SetupStep =
  | 'idle'
  | 'detecting'
  | 'recommending'
  | 'confirming'
  | 'downloading'
  | 'loading'
  | 'complete';

export interface SetupState {
  step: SetupStep;
  profile: HardwareProfile | null;
  recommendation: TierRecommendation | null;
  confirmedTier: TierName | null;
  downloads: DownloadProgress[];
  error: string | null;
}

export interface DownloadProgress {
  modelName: string;
  status: 'pending' | 'downloading' | 'complete' | 'failed';
  bytesDownloaded: number;
  bytesTotal: number;
  percentComplete: number;
}

// -- Event Types -------------------------------------------------------------

export type SetupEvent =
  | 'setup-state-changed'
  | 'download-progress'
  | 'setup-complete'
  | 'setup-error';

type SetupStateCallback = (state: SetupState) => void;
type DownloadProgressCallback = (downloads: DownloadProgress[]) => void;
type SetupCompleteCallback = (data: { tier: TierName }) => void;
type SetupErrorCallback = (data: { error: string; step: SetupStep }) => void;

type EventCallback =
  | SetupStateCallback
  | DownloadProgressCallback
  | SetupCompleteCallback
  | SetupErrorCallback;

// -- Persistence helpers -----------------------------------------------------

interface PersistedSetup {
  completed: boolean;
  tier: TierName | null;
  completedAt: number | null;
}

function getSetupFilePath(): string {
  return path.join(app.getPath('userData'), 'setup-state.json');
}

function readPersistedSetup(): PersistedSetup | null {
  try {
    const filePath = getSetupFilePath();
    if (!fs.existsSync(filePath)) return null;
    const raw = fs.readFileSync(filePath, 'utf-8');
    return JSON.parse(raw) as PersistedSetup;
  } catch {
    return null;
  }
}

function writePersistedSetup(data: PersistedSetup): void {
  const filePath = getSetupFilePath();
  const dir = path.dirname(filePath);
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
  fs.writeFileSync(filePath, JSON.stringify(data));
}

// -- SetupWizard -------------------------------------------------------------

export class SetupWizard {
  private static instance: SetupWizard | null = null;

  private state: SetupState = {
    step: 'idle',
    profile: null,
    recommendation: null,
    confirmedTier: null,
    downloads: [],
    error: null,
  };

  private listeners = new Map<SetupEvent, EventCallback[]>();

  private constructor() {
    // Singleton -- use getInstance()
  }

  static getInstance(): SetupWizard {
    if (!SetupWizard.instance) {
      SetupWizard.instance = new SetupWizard();
    }
    return SetupWizard.instance;
  }

  static resetInstance(): void {
    SetupWizard.instance = null;
  }

  // -- Public API -----------------------------------------------------------

  /** Check if this is the first run (no setup completion marker). */
  isFirstRun(): boolean {
    const persisted = readPersistedSetup();
    if (!persisted) return true;
    return !persisted.completed;
  }

  /** Return the current wizard state snapshot. */
  getSetupState(): SetupState {
    return { ...this.state, downloads: [...this.state.downloads] };
  }

  /**
   * Begin the setup flow: detect hardware, then recommend a tier.
   * Transitions: idle -> detecting -> recommending -> confirming
   */
  async startSetup(): Promise<void> {
    try {
      // Detect hardware
      this.updateStep('detecting');
      const profiler = HardwareProfiler.getInstance();
      const profile = await profiler.detect();
      this.state.profile = profile;

      // Recommend tier
      this.updateStep('recommending');
      const recommendation = recommend(profile);
      this.state.recommendation = recommendation;

      // Ready for user confirmation
      this.updateStep('confirming');
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Unknown error during setup';
      this.state.error = message;
      this.emit('setup-error', { error: message, step: this.state.step });
    }
  }

  /**
   * Skip the full setup flow -- default to whisper tier and mark complete.
   */
  skipSetup(): void {
    this.state.confirmedTier = 'whisper';
    this.state.step = 'complete';
    this.persist();
    this.emitStateChanged();
    this.emit('setup-complete', { tier: 'whisper' as TierName });
  }

  /**
   * User confirms a tier selection (recommended or a lower tier).
   * Must be called after startSetup() reaches 'confirming' step.
   */
  confirmTier(tier: TierName): void {
    this.state.confirmedTier = tier;
    this.emitStateChanged();
  }

  /**
   * Download models for the confirmed tier via OllamaLifecycle.pullModel().
   * Transitions: confirming -> downloading -> loading -> (ready for complete)
   */
  async startModelDownload(): Promise<void> {
    if (!this.state.confirmedTier) {
      throw new Error('No tier confirmed. Call confirmTier() first.');
    }

    const models = getModelList(this.state.confirmedTier);
    this.updateStep('downloading');

    // Initialize download progress for all models
    this.state.downloads = models.map((m) => ({
      modelName: m.name,
      status: 'pending' as const,
      bytesDownloaded: 0,
      bytesTotal: m.diskBytes,
      percentComplete: 0,
    }));
    this.emitDownloadProgress();

    const lifecycle = OllamaLifecycle.getInstance();

    for (let i = 0; i < models.length; i++) {
      const model = models[i];
      this.state.downloads[i].status = 'downloading';
      this.emitDownloadProgress();

      try {
        for await (const progress of lifecycle.pullModel(model.name)) {
          if (progress.total && progress.completed !== undefined) {
            this.state.downloads[i].bytesDownloaded = progress.completed;
            this.state.downloads[i].bytesTotal = progress.total;
            this.state.downloads[i].percentComplete =
              progress.total > 0
                ? Math.round((progress.completed / progress.total) * 100)
                : 0;
          }
          this.emitDownloadProgress();
        }

        // Mark model as complete
        this.state.downloads[i].status = 'complete';
        this.state.downloads[i].percentComplete = 100;
        this.emitDownloadProgress();
      } catch {
        // Mark failed, continue with next model
        this.state.downloads[i].status = 'failed';
        this.emitDownloadProgress();
      }
    }

    // Load models into VRAM
    this.updateStep('loading');
    try {
      const orchestrator = ModelOrchestrator.getInstance();
      await orchestrator.loadTierModels(this.state.confirmedTier);
    } catch {
      // Loading may fail if Ollama is not running; non-fatal
    }
  }

  /** Return the current per-model download progress. */
  getDownloadProgress(): DownloadProgress[] {
    return [...this.state.downloads];
  }

  /** Mark setup as complete and persist the tier selection. */
  completeSetup(): void {
    this.state.step = 'complete';
    this.persist();
    this.emitStateChanged();
    this.emit('setup-complete', { tier: this.state.confirmedTier! });
  }

  /** Clear the setup marker so the next launch triggers the wizard again. */
  resetSetup(): void {
    writePersistedSetup({ completed: false, tier: null, completedAt: null });
    this.state = {
      step: 'idle',
      profile: null,
      recommendation: null,
      confirmedTier: null,
      downloads: [],
      error: null,
    };
    this.emitStateChanged();
  }

  /** Subscribe to wizard events. Returns an unsubscribe function. */
  on(event: 'setup-state-changed', callback: SetupStateCallback): () => void;
  on(event: 'download-progress', callback: DownloadProgressCallback): () => void;
  on(event: 'setup-complete', callback: SetupCompleteCallback): () => void;
  on(event: 'setup-error', callback: SetupErrorCallback): () => void;
  on(event: SetupEvent, callback: EventCallback): () => void {
    const existing = this.listeners.get(event) ?? [];
    existing.push(callback);
    this.listeners.set(event, existing);

    return () => {
      const callbacks = this.listeners.get(event);
      if (callbacks) {
        this.listeners.set(
          event,
          callbacks.filter((cb) => cb !== callback),
        );
      }
    };
  }

  // -- Private helpers ------------------------------------------------------

  /** Update step and emit state change. */
  private updateStep(step: SetupStep): void {
    this.state.step = step;
    this.emitStateChanged();
  }

  /** Emit state-changed event. */
  private emitStateChanged(): void {
    this.emit('setup-state-changed', this.getSetupState());
  }

  /** Emit download-progress event. */
  private emitDownloadProgress(): void {
    this.emit('download-progress', this.getDownloadProgress());
  }

  /** Persist setup completion to disk. */
  private persist(): void {
    writePersistedSetup({
      completed: true,
      tier: this.state.confirmedTier,
      completedAt: Date.now(),
    });
  }

  /** Emit an event to all registered listeners. */
  private emit(event: SetupEvent, data: unknown): void {
    const callbacks = this.listeners.get(event) ?? [];
    for (const cb of callbacks) {
      try {
        (cb as (data: unknown) => void)(data);
      } catch {
        // Never let a listener crash the wizard
      }
    }
  }
}
