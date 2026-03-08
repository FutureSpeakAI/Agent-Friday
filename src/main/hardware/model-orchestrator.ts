/**
 * model-orchestrator.ts -- Singleton that coordinates model loading/unloading
 * within a VRAM budget.
 *
 * The Conductor. Manages the fleet of local AI models, loading core models
 * eagerly at startup and vision models lazily on first request. Tracks
 * estimated VRAM consumption and evicts least-recently-used models when
 * the budget is exceeded.
 *
 * Uses Ollama REST API (POST /api/generate with keep_alive) for warm-up
 * and unloading. Does NOT download models -- assumes they are already
 * available locally.
 *
 * cLaw Gate: All requests go to localhost:11434. No cloud traffic.
 *
 * Sprint 6 O.3: "The Conductor" -- ModelOrchestrator
 */

import { HardwareProfiler } from './hardware-profiler';
import { getModelList } from './tier-recommender';
import type { TierName, ModelRequirement } from './tier-recommender';

// -- Constants ---------------------------------------------------------------

/** Default Ollama API base URL */
const OLLAMA_ENDPOINT = 'http://localhost:11434';

/** How long to keep a model loaded after warm-up (Ollama keep_alive) */
const KEEP_ALIVE_DURATION = '24h';

/** Vision model names that should be loaded lazily, not eagerly */
const LAZY_MODELS = new Set(['moondream:latest']);

// -- Contract Types ----------------------------------------------------------

export interface LoadedModel {
  name: string;
  vramBytes: number;
  loadedAt: number;     // timestamp
  lastUsedAt: number;   // timestamp, updated on inference
  purpose: string;
}

export interface OrchestratorState {
  tier: TierName;
  loadedModels: LoadedModel[];
  estimatedVRAMUsage: number;   // bytes
  actualVRAMUsage: number | null; // from nvidia-smi, may be null
  vramBudget: number;            // effective VRAM from profile
  vramHeadroom: number;          // budget minus estimated usage
}

// -- Event Types -------------------------------------------------------------

type OrchestratorEvent = 'model-loaded' | 'model-unloaded' | 'vram-warning';
type OrchestratorCallback = (data: unknown) => void;

// -- ModelOrchestrator -------------------------------------------------------

export class ModelOrchestrator {
  private static instance: ModelOrchestrator | null = null;

  private loaded: LoadedModel[] = [];
  private currentTier: TierName = 'whisper';
  private listeners = new Map<OrchestratorEvent, OrchestratorCallback[]>();

  /** Full model registry from tier-recommender, keyed by name. */
  private modelRegistry = new Map<string, ModelRequirement>();

  private constructor() {
    // Singleton -- use getInstance()
  }

  static getInstance(): ModelOrchestrator {
    if (!ModelOrchestrator.instance) {
      ModelOrchestrator.instance = new ModelOrchestrator();
    }
    return ModelOrchestrator.instance;
  }

  static resetInstance(): void {
    ModelOrchestrator.instance = null;
  }

  // -- Public API -----------------------------------------------------------

  /**
   * Load all core models for a tier. Vision models are excluded from eager
   * loading (they load lazily via loadModel on first vision request).
   */
  async loadTierModels(tier: TierName): Promise<LoadedModel[]> {
    this.currentTier = tier;
    const models = getModelList(tier);

    // Populate the model registry for later on-demand loads
    for (const model of models) {
      this.modelRegistry.set(model.name, model);
    }

    // Filter out lazy models (e.g., vision) -- they load on demand
    const eagerModels = models.filter((m) => !LAZY_MODELS.has(m.name));

    const results: LoadedModel[] = [];
    for (const model of eagerModels) {
      const loadedModel = await this.loadModelInternal(model);
      results.push(loadedModel);
    }

    return results;
  }

  /** Return currently loaded models. */
  getLoadedModels(): LoadedModel[] {
    return [...this.loaded];
  }

  /** Get estimated VRAM consumption in bytes. */
  getVRAMUsage(): number {
    return this.loaded.reduce((sum, m) => sum + m.vramBytes, 0);
  }

  /** Check if a model fits within the remaining VRAM budget. */
  canLoadModel(model: ModelRequirement): boolean {
    const budget = this.getVRAMBudget();
    const currentUsage = this.getVRAMUsage();
    return currentUsage + model.vramBytes <= budget;
  }

  /**
   * Load a single model by name. If the model does not fit within the
   * VRAM budget, evicts least-recently-used models until it fits.
   */
  async loadModel(name: string): Promise<LoadedModel> {
    // Check if already loaded
    const existing = this.loaded.find((m) => m.name === name);
    if (existing) {
      existing.lastUsedAt = Date.now();
      return existing;
    }

    // Look up the model in the registry
    let modelReq = this.modelRegistry.get(name);
    if (!modelReq) {
      // Try to find it from a broader tier search
      const allTiers: TierName[] = ['sovereign', 'full', 'standard', 'light', 'whisper'];
      for (const tier of allTiers) {
        const tierModels = getModelList(tier);
        const found = tierModels.find((m) => m.name === name);
        if (found) {
          modelReq = found;
          this.modelRegistry.set(name, found);
          break;
        }
      }
    }

    if (!modelReq) {
      throw new Error(`Unknown model: ${name}`);
    }

    // Evict until the model fits
    while (!this.canLoadModel(modelReq) && this.loaded.length > 0) {
      await this.evictLeastRecent();
    }

    return this.loadModelInternal(modelReq);
  }

  /** Unload a specific model by name. */
  async unloadModel(name: string): Promise<void> {
    const idx = this.loaded.findIndex((m) => m.name === name);
    if (idx === -1) return;

    // Send unload request to Ollama
    await this.ollamaUnload(name);

    this.loaded.splice(idx, 1);
    this.emit('model-unloaded', { name });
  }

  /**
   * Evict the least recently used model. Returns the name of the evicted
   * model, or null if no models are loaded.
   */
  async evictLeastRecent(): Promise<string | null> {
    if (this.loaded.length === 0) return null;

    // Find the model with the oldest lastUsedAt
    let lru = this.loaded[0];
    for (const model of this.loaded) {
      if (model.lastUsedAt < lru.lastUsedAt) {
        lru = model;
      }
    }

    await this.unloadModel(lru.name);
    return lru.name;
  }

  /** Full state snapshot for the UI. */
  getOrchestratorState(): OrchestratorState {
    const budget = this.getVRAMBudget();
    const estimated = this.getVRAMUsage();

    return {
      tier: this.currentTier,
      loadedModels: [...this.loaded],
      estimatedVRAMUsage: estimated,
      actualVRAMUsage: null, // nvidia-smi integration deferred
      vramBudget: budget,
      vramHeadroom: budget - estimated,
    };
  }

  /** Update the lastUsedAt timestamp for a loaded model. */
  markUsed(name: string): void {
    const model = this.loaded.find((m) => m.name === name);
    if (model) {
      model.lastUsedAt = Date.now();
    }
  }

  /** Subscribe to orchestrator events. Returns an unsubscribe function. */
  on(event: OrchestratorEvent, callback: OrchestratorCallback): () => void {
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

  /** Get the effective VRAM budget from the HardwareProfiler. */
  private getVRAMBudget(): number {
    const profiler = HardwareProfiler.getInstance();
    return profiler.getEffectiveVRAM();
  }

  /** Load a model via Ollama REST API and add it to the loaded list. */
  private async loadModelInternal(model: ModelRequirement): Promise<LoadedModel> {
    // Check if already loaded
    const existing = this.loaded.find((m) => m.name === model.name);
    if (existing) {
      existing.lastUsedAt = Date.now();
      return existing;
    }

    // Send warm-up request to Ollama
    await this.ollamaWarmUp(model.name);

    const now = Date.now();
    const loadedModel: LoadedModel = {
      name: model.name,
      vramBytes: model.vramBytes,
      loadedAt: now,
      lastUsedAt: now,
      purpose: model.purpose,
    };

    this.loaded.push(loadedModel);
    this.emit('model-loaded', loadedModel);

    // Check for VRAM warning
    const budget = this.getVRAMBudget();
    const usage = this.getVRAMUsage();
    if (usage > budget * 0.9) {
      this.emit('vram-warning', { usage, budget });
    }

    return loadedModel;
  }

  /** Warm up a model in Ollama by sending a dummy generate request. */
  private async ollamaWarmUp(name: string): Promise<void> {
    try {
      await fetch(`${OLLAMA_ENDPOINT}/api/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: name,
          prompt: '',
          keep_alive: KEEP_ALIVE_DURATION,
        }),
      });
    } catch {
      // Ollama may not be running in tests or during initial setup
    }
  }

  /** Unload a model from Ollama by setting keep_alive to 0. */
  private async ollamaUnload(name: string): Promise<void> {
    try {
      await fetch(`${OLLAMA_ENDPOINT}/api/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: name,
          prompt: '',
          keep_alive: '0',
        }),
      });
    } catch {
      // Ollama may not be running
    }
  }

  /** Emit an event to all registered listeners. */
  private emit(event: OrchestratorEvent, data: unknown): void {
    const callbacks = this.listeners.get(event) ?? [];
    for (const cb of callbacks) {
      try {
        cb(data);
      } catch {
        // Never let a listener crash the orchestrator
      }
    }
  }
}
