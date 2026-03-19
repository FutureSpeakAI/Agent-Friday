/**
 * ollama-lifecycle.ts -- Health monitoring and model awareness for Ollama.
 *
 * The Caretaker. Monitors Ollama health via periodic polling, tracks
 * available and loaded models, provides VRAM awareness, and emits events
 * when the health state changes. Does not make decisions -- just reports
 * facts for the IntelligenceRouter to consume.
 *
 * Polling: every 30s checks /api/tags (available models) and /api/ps
 * (loaded models with VRAM usage). Graceful when Ollama is not installed:
 * reports { running: false } without error spam.
 *
 * cLaw Gate: All requests go to localhost:11434. No cloud traffic.
 *
 * Sprint 3 G.3: "The Caretaker" -- OllamaLifecycle
 */

// -- Constants ---------------------------------------------------------------

/** Default Ollama API base URL */
const OLLAMA_ENDPOINT = 'http://localhost:11434';

/** Polling interval (ms) */
const POLL_INTERVAL_MS = 30_000;

/** Timeout for health/model check requests (ms) */
const CHECK_TIMEOUT_MS = 5_000;

// -- Types -------------------------------------------------------------------

/** Current health status of the Ollama instance */
export interface HealthStatus {
  running: boolean;
  modelsLoaded: number;
  vramUsed: number;
  vramTotal: number;
}

/** Model info from /api/tags */
export interface OllamaModelInfo {
  name: string;
  model: string;
  size: number;
  digest: string;
  modifiedAt: string;
}

/** Currently loaded model from /api/ps */
export interface LoadedModelInfo {
  name: string;
  model: string;
  size: number;
  sizeVram: number;
  expiresAt: string;
}

/** Progress event during model pull */
export interface PullProgress {
  status: string;
  digest?: string;
  total?: number;
  completed?: number;
}

/** Lifecycle event types */
export type OllamaLifecycleEvent =
  | 'healthy'
  | 'unhealthy'
  | 'model-loaded'
  | 'model-unloaded'
  | 'health-change';

/** Event callback signature */
export type LifecycleCallback = (event: OllamaLifecycleEvent, data?: unknown) => void;

// -- Ollama API response shapes -----------------------------------------------

interface OllamaTagsResponse {
  models: Array<{
    name: string;
    model: string;
    size: number;
    digest: string;
    modified_at: string;
  }>;
}

interface OllamaPsResponse {
  models: Array<{
    name: string;
    model: string;
    size: number;
    size_vram: number;
    expires_at: string;
  }>;
}

// -- OllamaLifecycle ----------------------------------------------------------

export class OllamaLifecycle {
  private static instance: OllamaLifecycle | null = null;
  private pollingInterval: ReturnType<typeof setInterval> | null = null;
  private running = false;
  private availableModels: OllamaModelInfo[] = [];
  private loadedModels: LoadedModelInfo[] = [];
  private listeners: Map<OllamaLifecycleEvent, LifecycleCallback[]> = new Map();
  private previouslyRunning = false;
  private previousLoadedNames: Set<string> = new Set();
  /** Resolves after the first health poll completes (prevents race conditions) */
  private firstPollResolve: (() => void) | null = null;
  private firstPollReady: Promise<void>;

  private constructor() {
    this.firstPollReady = new Promise<void>((resolve) => {
      this.firstPollResolve = resolve;
    });
  }

  static getInstance(): OllamaLifecycle {
    if (!OllamaLifecycle.instance) {
      OllamaLifecycle.instance = new OllamaLifecycle();
    }
    return OllamaLifecycle.instance;
  }

  static resetInstance(): void {
    if (OllamaLifecycle.instance) {
      OllamaLifecycle.instance.stop();
    }
    OllamaLifecycle.instance = null;
  }

  async start(): Promise<void> {
    if (this.pollingInterval) return;
    await this.poll();
    // Signal that first poll is done — unblocks getHealthAsync() callers
    if (this.firstPollResolve) {
      this.firstPollResolve();
      this.firstPollResolve = null;
    }
    this.pollingInterval = setInterval(() => {
      void this.poll();
    }, POLL_INTERVAL_MS);
  }

  stop(): void {
    if (this.pollingInterval) {
      clearInterval(this.pollingInterval);
      this.pollingInterval = null;
    }
    this.running = false;
    this.availableModels = [];
    this.loadedModels = [];
    this.previouslyRunning = false;
    this.previousLoadedNames = new Set();
    this.listeners.clear();
    // Reset first-poll gate so restart works correctly
    this.firstPollReady = new Promise<void>((resolve) => {
      this.firstPollResolve = resolve;
    });
  }

  getHealth(): HealthStatus {
    const vramUsed = this.loadedModels.reduce(
      (sum, m) => sum + (m.sizeVram || m.size), 0
    );
    return {
      running: this.running,
      modelsLoaded: this.loadedModels.length,
      vramUsed,
      vramTotal: 0,
    };
  }

  /** Wait for the first poll to complete, then return health status. */
  async getHealthAsync(): Promise<HealthStatus> {
    await this.firstPollReady;
    return this.getHealth();
  }

  getAvailableModels(): OllamaModelInfo[] {
    return [...this.availableModels];
  }

  getLoadedModels(): LoadedModelInfo[] {
    return [...this.loadedModels];
  }

  isModelAvailable(name: string): boolean {
    return this.availableModels.some(
      (m) => m.name === name || m.name.startsWith(name)
    );
  }

  async *pullModel(name: string): AsyncGenerator<PullProgress> {
    const res = await fetch(`${OLLAMA_ENDPOINT}/api/pull`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, stream: true }),
    });

    if (!res.ok) {
      throw new Error(`Failed to pull model ${name}: ${res.status} ${res.statusText}`);
    }

    if (!res.body) {
      throw new Error(`No response body for model pull ${name}`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed) continue;
          try {
            yield JSON.parse(trimmed) as PullProgress;
          } catch {
            // skip malformed lines
          }
        }
      }

      if (buffer.trim()) {
        try {
          yield JSON.parse(buffer.trim()) as PullProgress;
        } catch {
          // skip malformed trailing data
        }
      }
    } finally {
      reader.releaseLock();
    }
  }

  on(event: OllamaLifecycleEvent, callback: LifecycleCallback): () => void {
    const existing = this.listeners.get(event) ?? [];
    existing.push(callback);
    this.listeners.set(event, existing);

    return () => {
      const callbacks = this.listeners.get(event);
      if (callbacks) {
        this.listeners.set(
          event,
          callbacks.filter((cb) => cb !== callback)
        );
      }
    };
  }

  private async poll(): Promise<void> {
    const wasRunning = this.previouslyRunning;
    const previousLoaded = this.previousLoadedNames;

    await this.checkHealth();

    const isNowRunning = this.running;
    const currentLoadedNames = new Set(this.loadedModels.map((m) => m.name));

    if (!wasRunning && isNowRunning) {
      this.emitEvent('healthy');
      this.emitEvent('health-change');
    } else if (wasRunning && !isNowRunning) {
      this.emitEvent('unhealthy');
      this.emitEvent('health-change');
    }

    if (isNowRunning) {
      for (const name of currentLoadedNames) {
        if (!previousLoaded.has(name)) {
          this.emitEvent('model-loaded', { name });
        }
      }
      for (const name of previousLoaded) {
        if (!currentLoadedNames.has(name)) {
          this.emitEvent('model-unloaded', { name });
        }
      }
    }

    this.previouslyRunning = isNowRunning;
    this.previousLoadedNames = currentLoadedNames;
  }

  private async checkHealth(): Promise<void> {
    try {
      const tagsRes = await fetch(`${OLLAMA_ENDPOINT}/api/tags`, {
        method: 'GET',
        signal: AbortSignal.timeout(CHECK_TIMEOUT_MS),
      });

      if (!tagsRes.ok) {
        this.running = false;
        this.availableModels = [];
        this.loadedModels = [];
        return;
      }

      const tagsData = (await tagsRes.json()) as OllamaTagsResponse;
      this.running = true;
      this.availableModels = (tagsData.models ?? []).map((m) => ({
        name: m.name,
        model: m.model,
        size: m.size,
        digest: m.digest ?? '',
        modifiedAt: m.modified_at ?? '',
      }));

      try {
        const psRes = await fetch(`${OLLAMA_ENDPOINT}/api/ps`, {
          method: 'GET',
          signal: AbortSignal.timeout(CHECK_TIMEOUT_MS),
        });

        if (psRes.ok) {
          const psData = (await psRes.json()) as OllamaPsResponse;
          this.loadedModels = (psData.models ?? []).map((m) => ({
            name: m.name,
            model: m.model,
            size: m.size,
            sizeVram: m.size_vram ?? m.size,
            expiresAt: m.expires_at ?? '',
          }));
        } else {
          this.loadedModels = [];
        }
      } catch {
        this.loadedModels = [];
      }
    } catch {
      this.running = false;
      this.availableModels = [];
      this.loadedModels = [];
    }
  }

  private emitEvent(event: OllamaLifecycleEvent, data?: unknown): void {
    const callbacks = this.listeners.get(event);
    if (callbacks) {
      for (const cb of callbacks) {
        try {
          cb(event, data);
        } catch {
          // Never let a listener crash the lifecycle
        }
      }
    }
  }
}

// -- Singleton Export ---------------------------------------------------------

export const ollamaLifecycle = OllamaLifecycle.getInstance();
