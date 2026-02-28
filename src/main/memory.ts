import { app } from 'electron';
import fs from 'fs/promises';
import path from 'path';
import crypto from 'crypto';
import { settingsManager } from './settings';
import {
  ensureVaultStructure,
  writeLongTermNote,
  writeMediumTermNote,
  readLongTermNotes,
  readMediumTermNotes,
  deleteNote,
} from './obsidian-memory';
import { semanticSearch } from './semantic-search';

// Late-bound integrity import to avoid circular dependency
let _integrityManager: any = null;
function getIntegrityManager() {
  if (!_integrityManager) {
    try {
      _integrityManager = require('./integrity').integrityManager;
    } catch {
      // Integrity system not yet initialized — skip signing
    }
  }
  return _integrityManager;
}

// Late-bound trust graph import to avoid circular dependency
let _trustGraph: any = null;
function getTrustGraph() {
  if (!_trustGraph) {
    try {
      _trustGraph = require('./trust-graph').trustGraph;
    } catch {
      // Trust graph not yet initialized — skip person extraction
    }
  }
  return _trustGraph;
}

// Late-bound memory-personality bridge import to avoid circular dependency
let _memoryPersonalityBridge: any = null;
function getMemoryPersonalityBridge() {
  if (!_memoryPersonalityBridge) {
    try {
      _memoryPersonalityBridge = require('./memory-personality-bridge').memoryPersonalityBridge;
    } catch {
      // Bridge not yet initialized — skip extraction guidance
    }
  }
  return _memoryPersonalityBridge;
}

// Late-bound friday-profile import to avoid circular dependency: memory -> friday-profile -> memory
let _appendLearning: any = null;
function getAppendLearning() {
  if (!_appendLearning) {
    try {
      _appendLearning = require('./friday-profile').appendLearning;
    } catch {
      // Friday profile not yet initialized — skip learning append
    }
  }
  return _appendLearning;
}

export interface ShortTermEntry {
  role: 'user' | 'assistant';
  content: string;
  timestamp: number;
}

export interface MediumTermEntry {
  id: string;
  observation: string;
  category: 'preference' | 'pattern' | 'context';
  confidence: number;
  firstObserved: number;
  lastReinforced: number;
  occurrences: number;
  /** How many distinct sessions reinforced this observation (added for scoring) */
  sessionCount?: number;
}

export interface LongTermEntry {
  id: string;
  fact: string;
  category: 'identity' | 'preference' | 'relationship' | 'professional';
  confirmed: boolean;
  createdAt: number;
  source: 'extracted' | 'user-stated' | 'manual-edit';
}

interface MemoryStore {
  shortTerm: ShortTermEntry[];
  mediumTerm: MediumTermEntry[];
  longTerm: LongTermEntry[];
}

const MAX_SHORT_TERM = 20;
const MEDIUM_TERM_MAX_AGE_DAYS = 30;

class MemoryManager {
  private memoryDir: string = '';
  private store: MemoryStore = { shortTerm: [], mediumTerm: [], longTerm: [] };
  private initialized = false;
  private saveQueue: Promise<void> = Promise.resolve(); // Serializes concurrent file writes

  async initialize(): Promise<void> {
    this.memoryDir = path.join(app.getPath('userData'), 'memory');
    await fs.mkdir(this.memoryDir, { recursive: true });
    await this.load();
    this.pruneExpired();
    this.initialized = true;
  }

  getShortTerm(): ShortTermEntry[] {
    return this.store.shortTerm;
  }

  getMediumTerm(): MediumTermEntry[] {
    return this.store.mediumTerm;
  }

  getLongTerm(): LongTermEntry[] {
    return this.store.longTerm;
  }

  async updateShortTerm(messages: Array<{ role: string; content: string }>): Promise<void> {
    this.store.shortTerm = messages.slice(-MAX_SHORT_TERM).map((m) => ({
      role: m.role as 'user' | 'assistant',
      content: m.content,
      timestamp: Date.now(),
    }));
    await this.save('shortTerm');
  }

  async addShortTermEntry(entry: ShortTermEntry): Promise<void> {
    this.store.shortTerm.push(entry);
    if (this.store.shortTerm.length > MAX_SHORT_TERM) {
      this.store.shortTerm = this.store.shortTerm.slice(-MAX_SHORT_TERM);
    }
    await this.save('shortTerm');
  }

  async extractMemories(conversationHistory: Array<{ role: string; content: string }>): Promise<void> {
    if (conversationHistory.length < 2) return;

    // Build the extraction prompt
    const existingLongTerm = this.store.longTerm.map((e) => `- ${e.fact}`).join('\n') || 'None yet';
    const existingMediumTerm = this.store.mediumTerm.map((e) => `- ${e.observation}`).join('\n') || 'None yet';

    const conversationText = conversationHistory
      .map((m) => `${m.role}: ${m.content}`)
      .join('\n');

    const extractionPrompt = `Analyse this conversation and extract useful information about the user. Return ONLY valid JSON with no other text.

CONVERSATION:
${conversationText}

ALREADY KNOWN (long-term facts):
${existingLongTerm}

ALREADY KNOWN (medium-term patterns):
${existingMediumTerm}

Return JSON in this exact format (include only genuinely NEW information not already listed above):
{
  "longTerm": [{"fact": "string", "category": "identity|preference|relationship|professional"}],
  "mediumTerm": [{"observation": "string", "category": "preference|pattern|context"}],
  "personMentions": [
    {
      "name": "person's name (as mentioned)",
      "context": "brief description of what was said about or involving them",
      "sentiment": 0.0,
      "domains": ["optional", "expertise", "areas"],
      "evidenceType": "observed"
    }
  ]
}

personMentions: Extract any people mentioned in the conversation (not the user themselves). Include:
- Their name as mentioned
- Brief context of what was discussed about them
- Sentiment from -1 (very negative) to +1 (very positive), 0 for neutral
- Any domains of expertise implied (e.g. "typescript", "cooking", "finance")
- Evidence type: "promise_kept", "promise_broken", "accurate_info", "inaccurate_info", "helpful_action", "unhelpful_action", "emotional_support", "user_stated", "observed", or "inferred"

If nothing new to extract, return: {"longTerm": [], "mediumTerm": [], "personMentions": []}`;

    // Inject personality-informed extraction guidance if available
    const bridge = getMemoryPersonalityBridge();
    let finalPrompt = extractionPrompt;
    if (bridge) {
      const guidance = bridge.getExtractionGuidance();
      if (guidance) {
        finalPrompt = extractionPrompt + guidance;
      }
    }

    try {
      // Use Anthropic SDK directly for extraction (cheapest reliable option)
      const { default: AnthropicSdk } = await import('@anthropic-ai/sdk');
      const anthropic = new AnthropicSdk({ apiKey: process.env.ANTHROPIC_API_KEY });

      const response = await anthropic.messages.create({
        model: 'claude-sonnet-4-20250514',
        max_tokens: 1024,
        messages: [{ role: 'user', content: finalPrompt }],
      });

      const textBlock = response.content.find((b) => b.type === 'text');
      const text = textBlock && 'text' in textBlock ? textBlock.text : '';

      // Extract JSON from response (handle potential markdown wrapping)
      const jsonMatch = text.match(/\{[\s\S]*\}/);
      if (!jsonMatch) return;

      const extracted = JSON.parse(jsonMatch[0]);

      // Merge long-term entries
      if (Array.isArray(extracted.longTerm)) {
        for (const item of extracted.longTerm) {
          if (!item.fact || typeof item.fact !== 'string') continue;
          // Check for duplicates using normalized word overlap (not substring matching)
          const exists = this.isDuplicateFact(item.fact, this.store.longTerm.map((e) => e.fact));
          if (!exists) {
            const newId = crypto.randomUUID();
            this.store.longTerm.push({
              id: newId,
              fact: item.fact,
              category: item.category || 'identity',
              confirmed: false,
              createdAt: Date.now(),
              source: 'extracted',
            });
            // Also append to living intelligence profile
            const appendFn = getAppendLearning();
            if (appendFn) appendFn(item.fact, item.category || 'identity').catch(() => {});
            // Index for semantic search
            semanticSearch.index(newId, item.fact, 'long-term', { category: item.category || 'identity' }).catch(() => {});
          }
        }
        await this.save('longTerm');
      }

      // Merge medium-term entries
      if (Array.isArray(extracted.mediumTerm)) {
        for (const item of extracted.mediumTerm) {
          if (!item.observation || typeof item.observation !== 'string') continue;
          const existing = this.store.mediumTerm.find(
            (e) => this.isDuplicateFact(item.observation, [e.observation])
          );
          if (existing) {
            existing.occurrences++;
            // Detect new session: if >30 min since last reinforcement, count as new session
            const SESSION_GAP_MS = 30 * 60 * 1000; // 30 minutes
            if (Date.now() - existing.lastReinforced > SESSION_GAP_MS) {
              existing.sessionCount = (existing.sessionCount || 1) + 1;
            }
            existing.lastReinforced = Date.now();
            existing.confidence = Math.min(1, existing.confidence + 0.1);
          } else {
            const newId = crypto.randomUUID();
            this.store.mediumTerm.push({
              id: newId,
              observation: item.observation,
              category: item.category || 'pattern',
              confidence: 0.5,
              firstObserved: Date.now(),
              lastReinforced: Date.now(),
              occurrences: 1,
              sessionCount: 1,
            });
            // Index for semantic search
            semanticSearch.index(newId, item.observation, 'medium-term', { category: item.category || 'pattern' }).catch(() => {});
          }
        }
        await this.save('mediumTerm');
      }

      // Route person mentions to Trust Graph
      if (Array.isArray(extracted.personMentions) && extracted.personMentions.length > 0) {
        const tg = getTrustGraph();
        if (tg) {
          tg.processPersonMentions(extracted.personMentions).catch((err: unknown) => {
            console.warn('[Memory] Trust graph person mention processing failed:', err);
          });
        }
      }

      // Sync memory patterns → personality calibration via bridge
      if (bridge) {
        try { bridge.syncMemoryToPersonality(); } catch {
          // Bridge sync is best-effort
        }
      }

      console.log(
        `[Memory] Extracted ${extracted.longTerm?.length || 0} long-term, ${extracted.mediumTerm?.length || 0} medium-term, ${extracted.personMentions?.length || 0} person mentions`
      );
    } catch (err) {
      console.warn('[Memory] Extraction failed:', err);
    }
  }

  /** Directly add a long-term fact (used by Gemini's save_memory tool) */
  async addImmediateMemory(fact: string, category: string): Promise<void> {
    const validCategories = ['identity', 'preference', 'relationship', 'professional'];
    const cat = validCategories.includes(category) ? category : 'identity';

    // Duplicate check using word-overlap similarity
    const exists = this.isDuplicateFact(fact, this.store.longTerm.map((e) => e.fact));

    if (!exists) {
      const newId = crypto.randomUUID();
      this.store.longTerm.push({
        id: newId,
        fact,
        category: cat as LongTermEntry['category'],
        confirmed: true,
        createdAt: Date.now(),
        source: 'user-stated',
      });
      await this.save('longTerm');
      // Also append to living intelligence profile
      const appendFn = getAppendLearning();
      if (appendFn) appendFn(fact, cat).catch(() => {});
      // Index for semantic search
      semanticSearch.index(newId, fact, 'long-term', { category: cat, confirmed: true }).catch(() => {});
      console.log(`[Memory] Immediate save: "${fact}" (${cat})`);
    }
  }

  async updateLongTermEntry(id: string, updates: Partial<LongTermEntry>): Promise<void> {
    const entry = this.store.longTerm.find((e) => e.id === id);
    if (entry) {
      Object.assign(entry, updates);
      await this.save('longTerm');
    }
  }

  async deleteLongTermEntry(id: string): Promise<void> {
    this.store.longTerm = this.store.longTerm.filter((e) => e.id !== id);
    await this.save('longTerm');
    semanticSearch.remove(id);

    // Also delete from Obsidian vault
    const vaultPath = this.getVaultPath();
    if (vaultPath) {
      deleteNote(vaultPath, 'memories', id).catch((err) =>
        console.warn('[Memory] Obsidian delete failed:', err)
      );
    }
  }

  async deleteMediumTermEntry(id: string): Promise<void> {
    this.store.mediumTerm = this.store.mediumTerm.filter((e) => e.id !== id);
    await this.save('mediumTerm');
    semanticSearch.remove(id);

    // Also delete from Obsidian vault
    const vaultPath = this.getVaultPath();
    if (vaultPath) {
      deleteNote(vaultPath, 'observations', id).catch((err) =>
        console.warn('[Memory] Obsidian delete failed:', err)
      );
    }
  }

  buildMemoryContext(): string {
    const parts: string[] = [];

    if (this.store.longTerm.length > 0) {
      const facts = this.store.longTerm.map((e) => `- ${e.fact}`).join('\n');
      parts.push(`## What You Know About the User\n${facts}`);
    }

    if (this.store.mediumTerm.length > 0) {
      const observations = this.store.mediumTerm
        .sort((a, b) => b.occurrences - a.occurrences)
        .slice(0, 10)
        .map((e) => `- ${e.observation}`)
        .join('\n');
      parts.push(`## Recent Observations\n${observations}`);
    }

    return parts.join('\n\n');
  }

  /**
   * Check if a fact is a duplicate of any existing facts using word-overlap similarity.
   * Requires >= 80% word overlap to be considered a duplicate.
   * Avoids the substring matching bug where "he" matches "she likes cheese".
   */
  private isDuplicateFact(newFact: string, existingFacts: string[]): boolean {
    const stopWords = new Set(['the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
      'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could', 'should',
      'may', 'might', 'can', 'shall', 'to', 'of', 'in', 'for', 'on', 'with', 'at',
      'by', 'from', 'as', 'into', 'through', 'during', 'before', 'after', 'and', 'but',
      'or', 'not', 'no', 'so', 'if', 'than', 'that', 'this', 'it', 'its', 'they', 'them',
      'their', 'he', 'she', 'his', 'her', 'we', 'us', 'our', 'you', 'your', 'i', 'my', 'me']);

    const tokenize = (text: string): Set<string> => {
      const words = text.toLowerCase().replace(/[^a-z0-9\s]/g, '').split(/\s+/).filter(Boolean);
      return new Set(words.filter((w) => !stopWords.has(w) && w.length > 2));
    };

    const newWords = tokenize(newFact);
    if (newWords.size === 0) return false;

    for (const existing of existingFacts) {
      const existingWords = tokenize(existing);
      if (existingWords.size === 0) continue;

      // Compute Jaccard similarity (intersection / union)
      let intersection = 0;
      for (const word of newWords) {
        if (existingWords.has(word)) intersection++;
      }
      const union = new Set([...newWords, ...existingWords]).size;
      const similarity = intersection / union;

      if (similarity >= 0.8) return true;
    }

    return false;
  }

  private pruneExpired(): void {
    const cutoff = Date.now() - MEDIUM_TERM_MAX_AGE_DAYS * 24 * 60 * 60 * 1000;
    const before = this.store.mediumTerm.length;
    this.store.mediumTerm = this.store.mediumTerm.filter(
      (e) => e.lastReinforced > cutoff || e.occurrences >= 5
    );
    if (this.store.mediumTerm.length < before) {
      console.log(`[Memory] Pruned ${before - this.store.mediumTerm.length} expired medium-term entries`);
    }

    if (this.store.shortTerm.length > MAX_SHORT_TERM) {
      this.store.shortTerm = this.store.shortTerm.slice(-MAX_SHORT_TERM);
    }
  }

  private getVaultPath(): string {
    try {
      return settingsManager.getObsidianVaultPath();
    } catch {
      return '';
    }
  }

  private async save(tier: 'shortTerm' | 'mediumTerm' | 'longTerm'): Promise<void> {
    // Serialize writes to prevent concurrent file corruption (same pattern as settings.ts)
    this.saveQueue = this.saveQueue.then(async () => {
      await this._doSave(tier);
    }).catch((err) => {
      console.error(`[Memory] Save failed for ${tier}:`, err);
    });
    return this.saveQueue;
  }

  private async _doSave(tier: 'shortTerm' | 'mediumTerm' | 'longTerm'): Promise<void> {
    // Always save JSON (canonical source)
    const filePath = path.join(this.memoryDir, `${tier}.json`);
    await fs.writeFile(filePath, JSON.stringify(this.store[tier], null, 2), 'utf-8');

    // Sign memory stores after save (integrity protection)
    if (tier === 'longTerm' || tier === 'mediumTerm') {
      const im = getIntegrityManager();
      if (im) {
        try {
          const ltJson = JSON.stringify(this.store.longTerm, null, 2);
          const mtJson = JSON.stringify(this.store.mediumTerm, null, 2);
          const ltSnap = this.store.longTerm.map((e) => ({ id: e.id, fact: e.fact }));
          const mtSnap = this.store.mediumTerm.map((e) => ({ id: e.id, observation: e.observation }));
          await im.signMemories(ltSnap, mtSnap, ltJson, mtJson);
        } catch (err) {
          console.warn('[Memory] Integrity signing failed:', err);
        }
      }
    }

    // Mirror to Obsidian vault if configured (long-term and medium-term only)
    const vaultPath = this.getVaultPath();
    if (vaultPath) {
      try {
        await ensureVaultStructure(vaultPath);
        if (tier === 'longTerm') {
          for (const entry of this.store.longTerm) {
            await writeLongTermNote(vaultPath, entry);
          }
        } else if (tier === 'mediumTerm') {
          for (const entry of this.store.mediumTerm) {
            await writeMediumTermNote(vaultPath, entry);
          }
        }
      } catch (err) {
        console.warn(`[Memory] Obsidian sync failed for ${tier}:`, err);
      }
    }
  }

  private async load(): Promise<void> {
    // Load from JSON first (always available)
    for (const tier of ['shortTerm', 'mediumTerm', 'longTerm'] as const) {
      const filePath = path.join(this.memoryDir, `${tier}.json`);
      try {
        const data = await fs.readFile(filePath, 'utf-8');
        (this.store as any)[tier] = JSON.parse(data);
      } catch {
        // File doesn't exist yet, keep defaults
      }
    }

    // Merge in any Obsidian-only entries (e.g. user manually added notes)
    const vaultPath = this.getVaultPath();
    if (vaultPath) {
      try {
        const vaultLongTerm = await readLongTermNotes(vaultPath);
        for (const vEntry of vaultLongTerm) {
          if (!this.store.longTerm.some((e) => e.id === vEntry.id)) {
            this.store.longTerm.push(vEntry);
          }
        }

        const vaultMediumTerm = await readMediumTermNotes(vaultPath);
        for (const vEntry of vaultMediumTerm) {
          if (!this.store.mediumTerm.some((e) => e.id === vEntry.id)) {
            this.store.mediumTerm.push(vEntry);
          }
        }

        console.log('[Memory] Merged Obsidian vault entries');
      } catch (err) {
        console.warn('[Memory] Obsidian vault read failed:', err);
      }
    }
  }
}

export const memoryManager = new MemoryManager();
