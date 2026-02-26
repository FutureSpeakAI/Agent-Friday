import { app } from 'electron';
import fs from 'fs/promises';
import path from 'path';
import crypto from 'crypto';
import { appendLearning } from './eve-profile';
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
  "mediumTerm": [{"observation": "string", "category": "preference|pattern|context"}]
}

If nothing new to extract, return: {"longTerm": [], "mediumTerm": []}`;

    try {
      // Use Anthropic SDK directly for extraction (cheapest reliable option)
      const Anthropic = require('@anthropic-ai/sdk');
      const anthropic = new Anthropic.default({ apiKey: process.env.ANTHROPIC_API_KEY });

      const response = await anthropic.messages.create({
        model: 'claude-sonnet-4-20250514',
        max_tokens: 1024,
        messages: [{ role: 'user', content: extractionPrompt }],
      });

      const text = response.content.find((b: any) => b.type === 'text')?.text || '';

      // Extract JSON from response (handle potential markdown wrapping)
      const jsonMatch = text.match(/\{[\s\S]*\}/);
      if (!jsonMatch) return;

      const extracted = JSON.parse(jsonMatch[0]);

      // Merge long-term entries
      if (Array.isArray(extracted.longTerm)) {
        for (const item of extracted.longTerm) {
          if (!item.fact || typeof item.fact !== 'string') continue;
          // Check for duplicates
          const exists = this.store.longTerm.some(
            (e) => e.fact.toLowerCase().includes(item.fact.toLowerCase()) ||
                   item.fact.toLowerCase().includes(e.fact.toLowerCase())
          );
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
            appendLearning(item.fact, item.category || 'identity').catch(() => {});
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
            (e) => e.observation.toLowerCase().includes(item.observation.toLowerCase()) ||
                   item.observation.toLowerCase().includes(e.observation.toLowerCase())
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

      console.log(
        `[Memory] Extracted ${extracted.longTerm?.length || 0} long-term, ${extracted.mediumTerm?.length || 0} medium-term memories`
      );
    } catch (err) {
      console.warn('[Memory] Extraction failed:', err);
    }
  }

  /** Directly add a long-term fact (used by Gemini's save_memory tool) */
  async addImmediateMemory(fact: string, category: string): Promise<void> {
    const validCategories = ['identity', 'preference', 'relationship', 'professional'];
    const cat = validCategories.includes(category) ? category : 'identity';

    // Duplicate check
    const exists = this.store.longTerm.some(
      (e) => e.fact.toLowerCase().includes(fact.toLowerCase()) ||
             fact.toLowerCase().includes(e.fact.toLowerCase())
    );

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
      appendLearning(fact, cat).catch(() => {});
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
    // Always save JSON (canonical source)
    const filePath = path.join(this.memoryDir, `${tier}.json`);
    await fs.writeFile(filePath, JSON.stringify(this.store[tier], null, 2), 'utf-8');

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
