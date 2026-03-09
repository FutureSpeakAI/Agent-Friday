/**
 * semantic-search.ts — Semantic Search Engine for Agent Friday.
 *
 * Generates vector embeddings for all memory tiers (long-term facts,
 * medium-term observations, episodic summaries) and provides cosine
 * similarity search across the full knowledge base.
 *
 * Embedding strategy: local-first via Ollama (nomic-embed-text, 768-dim),
 * falls back to Gemini cloud API (text-embedding-004, 768-dim) when
 * Ollama is unavailable. Both models produce compatible 768-dim vectors.
 *
 * Embeddings are cached in-memory and persisted to embeddings.json.
 * New memories are auto-indexed on save; bulk re-index runs on init.
 */

import { app } from 'electron';
import fs from 'fs/promises';
import path from 'path';
import { settingsManager } from './settings';
import { embeddingPipeline } from './embedding-pipeline';
import { privacyShield } from './privacy-shield';

export interface EmbeddingEntry {
  id: string;
  text: string;
  type: 'long-term' | 'medium-term' | 'episode' | 'document';
  /** Source metadata — varies by type */
  meta: Record<string, unknown>;
  embedding: number[];
  indexedAt: number;
}

export interface SearchResult {
  id: string;
  text: string;
  type: EmbeddingEntry['type'];
  meta: Record<string, unknown>;
  score: number;
}

const EMBEDDING_MODEL = 'text-embedding-004';
const EMBEDDING_DIMENSION = 768;
const MAX_BATCH_SIZE = 100; // Gemini embedding batch limit
const CACHE_FILE = 'embeddings.json';

class SemanticSearchEngine {
  private entries: Map<string, EmbeddingEntry> = new Map();
  private memoryDir = '';
  private initialized = false;
  private pendingBatch: Array<{ id: string; text: string; type: EmbeddingEntry['type']; meta: Record<string, unknown> }> = [];
  private batchTimer: ReturnType<typeof setTimeout> | null = null;

  async initialize(): Promise<void> {
    this.memoryDir = path.join(app.getPath('userData'), 'memory');
    await fs.mkdir(this.memoryDir, { recursive: true });
    await this.load();

    // Start local embedding pipeline (Ollama). If unavailable, falls back to Gemini cloud.
    await embeddingPipeline.start();

    this.initialized = true;
    console.log(`[SemanticSearch] Loaded ${this.entries.size} embeddings (local embeddings: ${embeddingPipeline.isReady() ? 'ready' : 'unavailable, using Gemini cloud'})`);
  }

  /**
   * Index a single memory entry. Batches requests for efficiency.
   * If the entry already exists with the same text, skips re-embedding.
   */
  async index(
    id: string,
    text: string,
    type: EmbeddingEntry['type'],
    meta: Record<string, unknown> = {}
  ): Promise<void> {
    // Skip if already indexed with same text
    const existing = this.entries.get(id);
    if (existing && existing.text === text) return;

    this.pendingBatch.push({ id, text, type, meta });

    // Flush immediately if batch is large enough
    if (this.pendingBatch.length >= MAX_BATCH_SIZE) {
      await this.flushBatch();
    } else {
      // Otherwise debounce — flush after 2s of quiet
      if (this.batchTimer) clearTimeout(this.batchTimer);
      this.batchTimer = setTimeout(() => this.flushBatch(), 2000);
    }
  }

  /**
   * Index multiple entries at once. Used for bulk re-indexing on init.
   */
  async indexBulk(
    items: Array<{ id: string; text: string; type: EmbeddingEntry['type']; meta?: Record<string, unknown> }>
  ): Promise<void> {
    // Filter out already-indexed entries with same text
    const toIndex = items.filter((item) => {
      const existing = this.entries.get(item.id);
      return !existing || existing.text !== item.text;
    });

    if (toIndex.length === 0) return;

    console.log(`[SemanticSearch] Bulk indexing ${toIndex.length} entries...`);

    // Process in batches
    for (let i = 0; i < toIndex.length; i += MAX_BATCH_SIZE) {
      const batch = toIndex.slice(i, i + MAX_BATCH_SIZE);
      const texts = batch.map((b) => b.text);

      try {
        const embeddings = await this.getEmbeddings(texts);

        for (let j = 0; j < batch.length; j++) {
          const item = batch[j];
          this.entries.set(item.id, {
            id: item.id,
            text: item.text,
            type: item.type,
            meta: item.meta || {},
            embedding: embeddings[j] || [],
            indexedAt: Date.now(),
          });
        }
      } catch (err) {
        // Crypto Sprint 16: Sanitize — Gemini API errors may contain URL with API key.
        console.warn(`[SemanticSearch] Bulk embedding batch failed:`, err instanceof Error ? err.message : 'Unknown error');
      }
    }

    await this.save();
  }

  /**
   * Remove an entry from the index.
   */
  remove(id: string): void {
    this.entries.delete(id);
    // Save async — don't block
    this.save().catch(() => {});
  }

  /**
   * Semantic search across all indexed entries.
   * Returns results sorted by cosine similarity score (descending).
   */
  async search(
    query: string,
    options: {
      maxResults?: number;
      minScore?: number;
      types?: EmbeddingEntry['type'][];
    } = {}
  ): Promise<SearchResult[]> {
    const { maxResults = 10, minScore = 0.3, types } = options;

    if (this.entries.size === 0) return [];

    let queryEmbedding: number[];
    try {
      const embeddings = await this.getEmbeddings([query]);
      queryEmbedding = embeddings[0];
      if (!queryEmbedding || queryEmbedding.length === 0) return [];
    } catch (err) {
      // Crypto Sprint 16: Sanitize — Gemini API errors may contain URL with API key.
      console.warn('[SemanticSearch] Query embedding failed:', err instanceof Error ? err.message : 'Unknown error');
      return [];
    }

    const results: SearchResult[] = [];

    for (const entry of this.entries.values()) {
      // Type filter
      if (types && !types.includes(entry.type)) continue;

      // Skip entries without valid embeddings
      if (!entry.embedding || entry.embedding.length === 0) continue;

      const score = this.cosineSimilarity(queryEmbedding, entry.embedding);

      if (score >= minScore) {
        results.push({
          id: entry.id,
          text: entry.text,
          type: entry.type,
          meta: entry.meta,
          score,
        });
      }
    }

    return results
      .sort((a, b) => b.score - a.score)
      .slice(0, maxResults);
  }

  /**
   * Get total indexed entry count.
   */
  getCount(): number {
    return this.entries.size;
  }

  /**
   * Get counts by type.
   */
  getStats(): Record<string, number> {
    const stats: Record<string, number> = {};
    for (const entry of this.entries.values()) {
      stats[entry.type] = (stats[entry.type] || 0) + 1;
    }
    return stats;
  }

  // --- Private methods ---

  private async flushBatch(): Promise<void> {
    if (this.pendingBatch.length === 0) return;

    const batch = [...this.pendingBatch];
    this.pendingBatch = [];
    if (this.batchTimer) {
      clearTimeout(this.batchTimer);
      this.batchTimer = null;
    }

    const texts = batch.map((b) => b.text);

    try {
      const embeddings = await this.getEmbeddings(texts);

      for (let i = 0; i < batch.length; i++) {
        const item = batch[i];
        this.entries.set(item.id, {
          id: item.id,
          text: item.text,
          type: item.type,
          meta: item.meta,
          embedding: embeddings[i] || [],
          indexedAt: Date.now(),
        });
      }

      await this.save();
      console.log(`[SemanticSearch] Indexed ${batch.length} entries`);
    } catch (err) {
      // Crypto Sprint 16: Sanitize — Gemini API errors may contain URL with API key.
      console.warn('[SemanticSearch] Batch embedding failed:', err instanceof Error ? err.message : 'Unknown error');
      // Put items back for retry
      this.pendingBatch.push(...batch);
    }
  }

  /**
   * Generate embeddings for texts. Tries local Ollama pipeline first (no cloud,
   * no API key needed), then falls back to Gemini cloud if local is unavailable.
   */
  private async getEmbeddings(texts: string[]): Promise<number[][]> {
    // ── Try local Ollama first (sovereign / local-first) ──────────────
    if (embeddingPipeline.isReady()) {
      const localResult = await embeddingPipeline.embedBatch(texts);
      if (localResult && localResult.length === texts.length) {
        return localResult;
      }
      // Local failed mid-request — fall through to cloud
      console.warn('[SemanticSearch] Local embedding failed, falling back to Gemini cloud');
    }

    // ── Fall back to Gemini cloud API ─────────────────────────────────
    const apiKey = settingsManager.getGeminiApiKey();
    if (!apiKey) {
      throw new Error('No embedding source available (Ollama not running, no Gemini API key)');
    }

    // Privacy Shield: scrub document text before sending to Google cloud.
    // Uses session-scoped deterministic hashing so the same PII produces
    // the same placeholder — embeddings remain internally consistent.
    const shieldEnabled = privacyShield.isEnabled();
    const cleanTexts = shieldEnabled
      ? texts.map((t) => privacyShield.scrub(t).text)
      : texts;

    const url = `https://generativelanguage.googleapis.com/v1beta/models/${EMBEDDING_MODEL}:batchEmbedContents`;

    const requests = cleanTexts.map((text) => ({
      model: `models/${EMBEDDING_MODEL}`,
      content: {
        parts: [{ text: text.slice(0, 2048) }], // Truncate to model max
      },
      taskType: 'RETRIEVAL_DOCUMENT',
    }));

    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-goog-api-key': apiKey,
      },
      body: JSON.stringify({ requests }),
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`Embedding API error ${response.status}: ${errorText}`);
    }

    const data = await response.json();
    const embeddings: number[][] = (data.embeddings || []).map(
      (e: { values: number[] }) => e.values || []
    );

    return embeddings;
  }

  /**
   * Cosine similarity between two vectors.
   */
  private cosineSimilarity(a: number[], b: number[]): number {
    if (a.length !== b.length || a.length === 0) return 0;

    let dotProduct = 0;
    let normA = 0;
    let normB = 0;

    for (let i = 0; i < a.length; i++) {
      dotProduct += a[i] * b[i];
      normA += a[i] * a[i];
      normB += b[i] * b[i];
    }

    const denominator = Math.sqrt(normA) * Math.sqrt(normB);
    return denominator === 0 ? 0 : dotProduct / denominator;
  }

  private async save(): Promise<void> {
    const filePath = path.join(this.memoryDir, CACHE_FILE);
    const data = Array.from(this.entries.values());
    await fs.writeFile(filePath, JSON.stringify(data), 'utf-8');
  }

  private async load(): Promise<void> {
    const filePath = path.join(this.memoryDir, CACHE_FILE);
    try {
      const raw = await fs.readFile(filePath, 'utf-8');
      const data: EmbeddingEntry[] = JSON.parse(raw);
      for (const entry of data) {
        this.entries.set(entry.id, entry);
      }
    } catch {
      // File doesn't exist yet
    }
  }
}

export const semanticSearch = new SemanticSearchEngine();
