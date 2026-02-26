/**
 * memory-consolidation.ts — Memory Consolidation Engine for EVE OS.
 *
 * Runs every 6 hours (or on demand):
 * 1. Promotes high-confidence medium-term observations → long-term facts
 * 2. Merges duplicate/overlapping long-term entries via Claude
 * 3. Extracts cross-episode insights from recent episodes
 *
 * Like the brain's sleep consolidation — strengthening important memories
 * and pruning redundancy.
 */

import { memoryManager, MediumTermEntry, LongTermEntry } from './memory';
import { episodicMemory } from './episodic-memory';
import { semanticSearch } from './semantic-search';
import { appendLearning } from './eve-profile';
import crypto from 'crypto';

const CONSOLIDATION_INTERVAL_MS = 6 * 60 * 60 * 1000; // 6 hours
const MERGE_SIMILARITY_THRESHOLD = 0.85;

// ── Weighted Promotion Scoring ──────────────────────────────────────
// Inspired by Claude History MCP's learnings synthesis model.
// Instead of a binary threshold (confidence + occurrences), we compute a
// weighted score across multiple signals. This avoids promoting one-off
// session bursts while surfacing observations that persist across time.
const PROMOTION_SCORE_THRESHOLD = 10;
const PROMOTION_MIN_OCCURRENCES = 3; // Hard floor — must be observed 3+ times

/**
 * Compute a weighted promotion score for a medium-term observation.
 *
 * Signals and weights:
 *   FREQUENCY:      min(occurrences, 10) × 2     — max 20
 *   CROSS-SESSION:  min(sessionCount, 5)  × 2     — max 10
 *   TIME-SPAN:      +5 if spans ≥7 days, +3 if ≥3 days
 *   CONFIDENCE:     +3 if confidence ≥ 0.9
 *   STALENESS:      −5 if not reinforced in 14+ days, −2 if 7+ days
 */
function computePromotionScore(entry: MediumTermEntry): number {
  const frequency = Math.min(entry.occurrences, 10) * 2;
  const sessions  = Math.min(entry.sessionCount || 1, 5) * 2;

  const daySpan = (entry.lastReinforced - entry.firstObserved) / (24 * 60 * 60 * 1000);
  const timeSpan = daySpan >= 7 ? 5 : daySpan >= 3 ? 3 : 0;

  const confidenceBonus = entry.confidence >= 0.9 ? 3 : 0;

  const daysSinceReinforced = (Date.now() - entry.lastReinforced) / (24 * 60 * 60 * 1000);
  const stalenessPenalty = daysSinceReinforced > 14 ? -5 : daysSinceReinforced > 7 ? -2 : 0;

  return frequency + sessions + timeSpan + confidenceBonus + stalenessPenalty;
}

class MemoryConsolidation {
  private timer: ReturnType<typeof setInterval> | null = null;
  private running = false;

  initialize(): void {
    // Run initial consolidation after a short delay (let other systems boot)
    setTimeout(() => {
      this.run().catch((err) => {
        console.warn('[Consolidation] Initial run failed:', err);
      });
    }, 30_000);

    // Schedule periodic consolidation
    this.timer = setInterval(() => {
      this.run().catch((err) => {
        console.warn('[Consolidation] Periodic run failed:', err);
      });
    }, CONSOLIDATION_INTERVAL_MS);

    console.log('[Consolidation] Initialized — running every 6 hours');
  }

  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  /**
   * Run the full consolidation cycle.
   */
  async run(): Promise<{ promoted: number; merged: number; insights: number }> {
    if (this.running) {
      console.log('[Consolidation] Already running, skipping');
      return { promoted: 0, merged: 0, insights: 0 };
    }

    this.running = true;
    console.log('[Consolidation] Starting consolidation cycle...');

    try {
      const promoted = await this.promoteHighConfidence();
      const merged = await this.mergeDuplicates();
      const insights = await this.extractCrossEpisodeInsights();

      console.log(
        `[Consolidation] Complete — promoted: ${promoted}, merged: ${merged}, insights: ${insights}`
      );

      return { promoted, merged, insights };
    } finally {
      this.running = false;
    }
  }

  /**
   * Phase 1: Promote high-scoring medium-term observations to long-term facts.
   *
   * Uses a weighted scoring formula that considers frequency, cross-session
   * reinforcement, time-span persistence, confidence, and staleness decay.
   * This replaces the old binary threshold approach (confidence + occurrences).
   */
  private async promoteHighConfidence(): Promise<number> {
    const mediumTerm = memoryManager.getMediumTerm();
    const longTerm = memoryManager.getLongTerm();
    let promotedCount = 0;

    // Score all candidates and filter by threshold
    const scored = mediumTerm
      .map((m) => ({ entry: m, score: computePromotionScore(m) }))
      .filter((s) => s.score >= PROMOTION_SCORE_THRESHOLD && s.entry.occurrences >= PROMOTION_MIN_OCCURRENCES)
      .sort((a, b) => b.score - a.score); // Promote highest-scoring first

    if (scored.length > 0) {
      console.log(
        `[Consolidation] ${scored.length} candidate(s) meet promotion threshold (>=${PROMOTION_SCORE_THRESHOLD}):`,
        scored.map((s) => `"${s.entry.observation.slice(0, 40)}…" score=${s.score}`).join(', ')
      );
    }

    for (const { entry: candidate, score } of scored) {
      // Check if this observation is already captured in long-term
      const alreadyExists = longTerm.some(
        (lt) =>
          lt.fact.toLowerCase().includes(candidate.observation.toLowerCase()) ||
          candidate.observation.toLowerCase().includes(lt.fact.toLowerCase())
      );

      if (alreadyExists) {
        // Remove from medium-term since it's redundant
        await memoryManager.deleteMediumTermEntry(candidate.id);
        continue;
      }

      // Promote: add to long-term via the proper path
      const category = this.mapMediumToLongCategory(candidate.category);
      await memoryManager.addImmediateMemory(candidate.observation, category);

      // Remove from medium-term
      await memoryManager.deleteMediumTermEntry(candidate.id);

      promotedCount++;
      console.log(
        `[Consolidation] Promoted (score=${score}): "${candidate.observation.slice(0, 60)}…" → long-term (${category})`
      );
    }

    return promotedCount;
  }

  /**
   * Phase 2: Merge duplicate/overlapping long-term entries using Claude.
   * Finds semantically similar entries and merges them into a single, cleaner fact.
   */
  private async mergeDuplicates(): Promise<number> {
    const longTerm = memoryManager.getLongTerm();
    if (longTerm.length < 3) return 0;

    // Find potential duplicates using semantic search
    const mergeGroups: Array<{ entries: LongTermEntry[]; merged: string }> = [];
    const processed = new Set<string>();

    for (const entry of longTerm) {
      if (processed.has(entry.id)) continue;

      // Search for similar entries
      const similar = await semanticSearch.search(entry.fact, {
        maxResults: 5,
        minScore: MERGE_SIMILARITY_THRESHOLD,
        types: ['long-term'],
      });

      const siblings = similar
        .filter((s) => s.id !== entry.id && !processed.has(s.id))
        .map((s) => longTerm.find((lt) => lt.id === s.id))
        .filter((lt): lt is LongTermEntry => lt !== undefined);

      if (siblings.length === 0) continue;

      // Mark all as processed
      processed.add(entry.id);
      for (const sib of siblings) {
        processed.add(sib.id);
      }

      // Use Claude to merge into a single fact
      const allEntries = [entry, ...siblings];
      const merged = await this.claudeMerge(allEntries);

      if (merged) {
        mergeGroups.push({ entries: allEntries, merged });
      }
    }

    // Apply merges
    let mergedCount = 0;
    for (const group of mergeGroups) {
      // Delete all old entries except the first (which we'll update)
      const [keep, ...remove] = group.entries;

      for (const entry of remove) {
        await memoryManager.deleteLongTermEntry(entry.id);
      }

      // Update the kept entry with the merged text
      await memoryManager.updateLongTermEntry(keep.id, {
        fact: group.merged,
        confirmed: true,
      });

      // Re-index
      semanticSearch
        .index(keep.id, group.merged, 'long-term', { category: keep.category })
        .catch(() => {});

      mergedCount++;
      console.log(
        `[Consolidation] Merged ${group.entries.length} entries → "${group.merged.slice(0, 60)}..."`
      );
    }

    return mergedCount;
  }

  /**
   * Phase 3: Extract cross-episode insights from recent episodes.
   * Looks for patterns across conversations that aren't captured in individual memories.
   */
  private async extractCrossEpisodeInsights(): Promise<number> {
    const episodes = episodicMemory.getRecent(10);
    if (episodes.length < 3) return 0;

    const existingFacts = memoryManager.getLongTerm().map((e) => e.fact);

    try {
      const Anthropic = require('@anthropic-ai/sdk');
      const anthropic = new Anthropic.default({
        apiKey: process.env.ANTHROPIC_API_KEY,
      });

      const episodeSummaries = episodes
        .map(
          (ep) =>
            `[${new Date(ep.startTime).toLocaleDateString()}] ${ep.summary} ` +
            `(Topics: ${ep.topics.join(', ')}; Mood: ${ep.emotionalTone})`
        )
        .join('\n');

      const response = await anthropic.messages.create({
        model: 'claude-sonnet-4-20250514',
        max_tokens: 400,
        messages: [
          {
            role: 'user',
            content: `Analyse these recent conversation summaries for cross-conversation patterns and insights. Return ONLY valid JSON.

RECENT CONVERSATIONS:
${episodeSummaries}

ALREADY KNOWN FACTS:
${existingFacts.slice(0, 20).map((f) => `- ${f}`).join('\n')}

Return JSON:
{
  "insights": [
    {"fact": "cross-conversation observation", "category": "preference|pattern|professional|identity"}
  ]
}

Rules:
- Only include genuinely NEW insights that emerge from PATTERNS across multiple conversations
- Don't repeat what's already known
- Focus on meta-patterns: recurring interests, work rhythms, communication evolution
- Max 3 insights per cycle
- If nothing new, return {"insights": []}`,
          },
        ],
      });

      const text =
        response.content.find((b: any) => b.type === 'text')?.text || '';
      const jsonMatch = text.match(/\{[\s\S]*\}/);
      if (!jsonMatch) return 0;

      const parsed = JSON.parse(jsonMatch[0]);
      let insightCount = 0;

      if (Array.isArray(parsed.insights)) {
        for (const insight of parsed.insights.slice(0, 3)) {
          if (!insight?.fact) continue;

          // Duplicate check
          const exists = existingFacts.some(
            (f) =>
              f.toLowerCase().includes(insight.fact.toLowerCase()) ||
              insight.fact.toLowerCase().includes(f.toLowerCase())
          );

          if (!exists) {
            await memoryManager.addImmediateMemory(
              insight.fact,
              insight.category || 'identity'
            );
            appendLearning(
              `[Cross-episode insight] ${insight.fact}`,
              insight.category || 'identity'
            ).catch(() => {});
            insightCount++;
            console.log(
              `[Consolidation] New insight: "${insight.fact.slice(0, 60)}..."`
            );
          }
        }
      }

      return insightCount;
    } catch (err) {
      console.warn('[Consolidation] Cross-episode analysis failed:', err);
      return 0;
    }
  }

  /**
   * Use Claude to merge multiple similar long-term entries into one clean fact.
   */
  private async claudeMerge(entries: LongTermEntry[]): Promise<string | null> {
    try {
      const Anthropic = require('@anthropic-ai/sdk');
      const anthropic = new Anthropic.default({
        apiKey: process.env.ANTHROPIC_API_KEY,
      });

      const facts = entries.map((e) => `- ${e.fact}`).join('\n');

      const response = await anthropic.messages.create({
        model: 'claude-sonnet-4-20250514',
        max_tokens: 100,
        messages: [
          {
            role: 'user',
            content: `These memory entries overlap or say the same thing. Merge them into ONE clear, concise fact. Return ONLY the merged text, nothing else.

ENTRIES:
${facts}

MERGED FACT:`,
          },
        ],
      });

      const text =
        response.content.find((b: any) => b.type === 'text')?.text || '';
      const cleaned = text.trim().replace(/^["']|["']$/g, '');

      return cleaned.length > 5 ? cleaned : null;
    } catch (err) {
      console.warn('[Consolidation] Claude merge failed:', err);
      return null;
    }
  }

  private mapMediumToLongCategory(
    mediumCat: string
  ): 'identity' | 'preference' | 'relationship' | 'professional' {
    switch (mediumCat) {
      case 'preference':
        return 'preference';
      case 'context':
        return 'professional';
      case 'pattern':
        return 'preference';
      default:
        return 'identity';
    }
  }
}

export const memoryConsolidation = new MemoryConsolidation();
