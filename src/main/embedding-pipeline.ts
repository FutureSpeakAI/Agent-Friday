/**
 * embedding-pipeline.ts — Local embedding generation via Ollama's /api/embed.
 *
 * The Inner Voice. Provides vector embeddings for text using a locally-running
 * Ollama instance with an embedding model (nomic-embed-text preferred,
 * all-minilm as fallback). This is Tier 1 — always-local, silent, no user
 * interaction or consent dialogs.
 *
 * Lifecycle follows the singleton start()/stop() pattern established by
 * contextGraph. The pipeline checks Ollama availability and model presence
 * during start(), then exposes embed() and embedBatch() for downstream
 * consumers (context graph, semantic search, etc.).
 *
 * cLaw Gate: All inference is local via Ollama. Never triggers cloud
 * requests. Never shows consent dialogs. Pure computation on user's machine.
 *
 * Sprint 3 G.2: "The Inner Voice" — EmbeddingPipeline
 */

// ── Constants ─────────────────────────────────────────────────────────

/** Default Ollama API base URL */
const OLLAMA_ENDPOINT = 'http://localhost:11434';

/** Preferred embedding model (~275MB, 768 dimensions) */
const PREFERRED_MODEL = 'nomic-embed-text';

/** Fallback embedding model (~45MB, smaller but lighter) */
const FALLBACK_MODEL = 'all-minilm';

/** Timeout for health/model checks (ms) */
const CHECK_TIMEOUT_MS = 5_000;

/** Timeout for embedding requests (ms) */
const EMBED_TIMEOUT_MS = 30_000;

// ── Types ─────────────────────────────────────────────────────────────

/** Ollama /api/embed response */
interface OllamaEmbedResponse {
  embeddings: number[][];
}

/** Ollama /api/tags response */
interface OllamaTagsResponse {
  models: Array<{
    name: string;
    model: string;
    size: number;
  }>;
}

// ── Pipeline ──────────────────────────────────────────────────────────

/**
 * Local embedding generation pipeline using Ollama.
 *
 * Singleton with start()/stop() lifecycle. Call start() during app
 * initialization to probe Ollama and select an embedding model.
 * Once ready, embed() and embedBatch() generate vector embeddings
 * for any text input.
 *
 * Cosine similarity is a pure static utility — no Ollama needed.
 */
export class EmbeddingPipeline {
  /** The resolved embedding model name (set during start) */
  private model: string | null = null;

  /** Whether the pipeline is ready to generate embeddings */
  private ready = false;

  // ── Lifecycle ─────────────────────────────────────────────────────

  /**
   * Start the embedding pipeline.
   *
   * Probes Ollama for availability, then checks whether the preferred
   * or fallback embedding model is installed. If Ollama is down or no
   * embedding model is found, the pipeline stays in not-ready state
   * without crashing.
   */
  async start(): Promise<void> {
    this.ready = false;
    this.model = null;

    try {
      // Check if Ollama is reachable
      const available = await this.checkOllamaAvailable();
      if (!available) {
        console.warn('[EmbeddingPipeline] Ollama is not available — pipeline disabled');
        return;
      }

      // Find an embedding model
      const model = await this.findEmbeddingModel();
      if (!model) {
        console.warn('[EmbeddingPipeline] No embedding model found — pipeline disabled');
        return;
      }

      this.model = model;
      this.ready = true;
      console.log(`[EmbeddingPipeline] Ready with model: ${model}`);
    } catch (err) {
      console.warn('[EmbeddingPipeline] Failed to start:', (err as Error).message);
      this.ready = false;
      this.model = null;
    }
  }

  /**
   * Stop the pipeline and reset state.
   */
  stop(): void {
    this.ready = false;
    this.model = null;
  }

  /**
   * Returns true if the pipeline is ready to generate embeddings.
   */
  isReady(): boolean {
    return this.ready;
  }

  // ── Embedding Generation ──────────────────────────────────────────

  /**
   * Generate an embedding vector for a single text input.
   *
   * Returns a number[] of fixed dimension (determined by the model),
   * or null if the pipeline is not ready.
   */
  async embed(text: string): Promise<number[] | null> {
    if (!this.ready || !this.model) {
      return null;
    }

    try {
      const response = await fetch(`${OLLAMA_ENDPOINT}/api/embed`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: this.model,
          input: text,
        }),
        signal: AbortSignal.timeout(EMBED_TIMEOUT_MS),
      });

      if (!response.ok) {
        console.warn(`[EmbeddingPipeline] Embed failed (${response.status})`);
        return null;
      }

      const data = (await response.json()) as OllamaEmbedResponse;

      if (!data.embeddings || data.embeddings.length === 0) {
        return null;
      }

      return data.embeddings[0];
    } catch (err) {
      console.warn('[EmbeddingPipeline] Embed error:', (err as Error).message);
      return null;
    }
  }

  /**
   * Generate embedding vectors for multiple texts in a single request.
   *
   * Returns an array of number[] vectors matching the input length,
   * or null if the pipeline is not ready.
   */
  async embedBatch(texts: string[]): Promise<number[][] | null> {
    if (!this.ready || !this.model) {
      return null;
    }

    if (texts.length === 0) {
      return [];
    }

    try {
      const response = await fetch(`${OLLAMA_ENDPOINT}/api/embed`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: this.model,
          input: texts,
        }),
        signal: AbortSignal.timeout(EMBED_TIMEOUT_MS),
      });

      if (!response.ok) {
        console.warn(`[EmbeddingPipeline] EmbedBatch failed (${response.status})`);
        return null;
      }

      const data = (await response.json()) as OllamaEmbedResponse;

      if (!data.embeddings) {
        return null;
      }

      return data.embeddings;
    } catch (err) {
      console.warn('[EmbeddingPipeline] EmbedBatch error:', (err as Error).message);
      return null;
    }
  }

  // ── Similarity ────────────────────────────────────────────────────

  /**
   * Compute cosine similarity between two vectors.
   *
   * Returns a value in [-1, 1] where:
   *   1  = identical direction
   *   0  = orthogonal (unrelated)
   *  -1  = opposite direction
   *
   * This is a pure math function — no Ollama needed.
   */
  static similarity(vecA: number[], vecB: number[]): number {
    if (vecA.length !== vecB.length || vecA.length === 0) {
      return 0;
    }

    let dotProduct = 0;
    let normA = 0;
    let normB = 0;

    for (let i = 0; i < vecA.length; i++) {
      dotProduct += vecA[i] * vecB[i];
      normA += vecA[i] * vecA[i];
      normB += vecB[i] * vecB[i];
    }

    const denominator = Math.sqrt(normA) * Math.sqrt(normB);

    if (denominator === 0) {
      return 0;
    }

    return dotProduct / denominator;
  }

  // ── Private: Ollama Probing ───────────────────────────────────────

  /**
   * Check if Ollama is reachable by hitting /api/tags.
   */
  private async checkOllamaAvailable(): Promise<boolean> {
    try {
      const res = await fetch(`${OLLAMA_ENDPOINT}/api/tags`, {
        method: 'GET',
        signal: AbortSignal.timeout(CHECK_TIMEOUT_MS),
      });
      return res.ok;
    } catch {
      return false;
    }
  }

  /**
   * Find an available embedding model.
   * Prefers nomic-embed-text, falls back to all-minilm.
   * Returns the model name if found, null otherwise.
   */
  private async findEmbeddingModel(): Promise<string | null> {
    try {
      const res = await fetch(`${OLLAMA_ENDPOINT}/api/tags`, {
        method: 'GET',
        signal: AbortSignal.timeout(CHECK_TIMEOUT_MS),
      });

      if (!res.ok) return null;

      const data = (await res.json()) as OllamaTagsResponse;

      if (!Array.isArray(data.models)) return null;

      const modelNames = data.models.map((m) => m.name);

      // Check preferred model first
      if (modelNames.some((n) => n.startsWith(PREFERRED_MODEL))) {
        return PREFERRED_MODEL;
      }

      // Fall back to lighter model
      if (modelNames.some((n) => n.startsWith(FALLBACK_MODEL))) {
        return FALLBACK_MODEL;
      }

      // Use the first available model if any exist (for testing/flexibility)
      // In practice, users should have an embedding model installed
      if (modelNames.length > 0) {
        return modelNames[0];
      }

      return null;
    } catch {
      return null;
    }
  }
}

// ── Singleton Export ──────────────────────────────────────────────────

export const embeddingPipeline = new EmbeddingPipeline();
