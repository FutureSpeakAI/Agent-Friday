/**
 * friday-profile.ts — Dynamic user intelligence dossier.
 * For fresh installs, creates a blank profile that gets populated during onboarding.
 * For returning users, assembles a condensed profile from stored memories and learned insights.
 * The consolidation system compresses raw learnings into narrative paragraphs using Claude Sonnet.
 */

import { app } from 'electron';
import fs from 'fs/promises';
import path from 'path';
import { settingsManager } from './settings';
import { memoryManager } from './memory';
import { llmClient } from './llm-client';

// Late-bound trust graph import to avoid circular dependencies
let _trustGraph: any = null;
function getTrustGraph() {
  if (!_trustGraph) {
    try { _trustGraph = require('./trust-graph').trustGraph; } catch { /* not ready yet */ }
  }
  return _trustGraph;
}

const BLANK_INTELLIGENCE_FILE = `# AGENT INTELLIGENCE FILE
Subject: New User
Classification: Principal — Full Access
Last Updated: ${new Date().toISOString().slice(0, 10)}

## IDENTITY & PERSONAL
- Profile being built during onboarding
- No information gathered yet

## HOW TO BUILD THIS PROFILE
This file will be populated automatically as the agent learns about the user through conversation.
Every fact saved via save_memory contributes to building a comprehensive intelligence dossier.
Periodic consolidation compresses raw observations into structured narrative.`;

const LEARNED_SECTION_HEADER = '\n\n## LEARNED INSIGHTS (auto-updated)\n';
const CONSOLIDATION_THRESHOLD = 30;
let isConsolidating = false; // Lock to prevent concurrent consolidation

/**
 * Writes the intelligence file to userData if it doesn't exist yet,
 * or preserves existing learned insights on subsequent launches.
 */
export async function ensureProfileOnDisk(): Promise<void> {
  const filePath = path.join(app.getPath('userData'), 'friday-intelligence.md');
  const config = settingsManager.getAgentConfig();

  try {
    const existing = await fs.readFile(filePath, 'utf-8').catch(() => '');

    if (!existing) {
      // First time — write a blank profile with an empty learnings section
      const baseProfile = config.onboardingComplete
        ? buildBaseProfileFromMemories()
        : BLANK_INTELLIGENCE_FILE;
      await fs.writeFile(filePath, baseProfile + LEARNED_SECTION_HEADER, 'utf-8');
      console.log('[Profile] Intelligence file created at', filePath);
    } else {
      // File exists — update the base profile but preserve learned insights
      const learnedIdx = existing.indexOf('## LEARNED INSIGHTS');
      if (learnedIdx !== -1) {
        const learnedSection = existing.slice(learnedIdx);
        const baseProfile = config.onboardingComplete
          ? buildBaseProfileFromMemories()
          : BLANK_INTELLIGENCE_FILE;
        await fs.writeFile(filePath, baseProfile + '\n\n' + learnedSection, 'utf-8');
        console.log('[Profile] Intelligence file refreshed (learnings preserved)');
      } else {
        const baseProfile = config.onboardingComplete
          ? buildBaseProfileFromMemories()
          : BLANK_INTELLIGENCE_FILE;
        await fs.writeFile(filePath, baseProfile + LEARNED_SECTION_HEADER, 'utf-8');
        console.log('[Profile] Intelligence file updated (learnings section added)');
      }
    }
  // Crypto Sprint 17: Sanitize error output.
  } catch (err) {
    console.warn('[Profile] Failed to write intelligence file:', err instanceof Error ? err.message : 'Unknown error');
  }
}

/**
 * Build a base profile header from stored long-term memories.
 * This creates a structured intelligence file from whatever the agent has learned.
 */
function buildBaseProfileFromMemories(): string {
  const config = settingsManager.getAgentConfig();
  const longTerm = memoryManager.getLongTerm();
  const userName = config.userName || 'User';

  // Categorize memories
  const identity = longTerm.filter((e) => e.category === 'identity').map((e) => `- ${e.fact}`);
  const professional = longTerm.filter((e) => e.category === 'professional').map((e) => `- ${e.fact}`);
  const preferences = longTerm.filter((e) => e.category === 'preference').map((e) => `- ${e.fact}`);
  const relationships = longTerm.filter((e) => e.category === 'relationship').map((e) => `- ${e.fact}`);
  const other = longTerm
    .filter((e) => !['identity', 'professional', 'preference', 'relationship'].includes(e.category))
    .map((e) => `- [${e.category}] ${e.fact}`);

  const sections: string[] = [
    `# AGENT INTELLIGENCE FILE`,
    `Subject: ${userName}`,
    `Classification: Principal — Full Access`,
    `Agent: ${config.agentName || 'Unconfigured'}`,
    `Last Updated: ${new Date().toISOString().slice(0, 10)}`,
  ];

  if (identity.length > 0) {
    sections.push(`\n## IDENTITY & PERSONAL\n${identity.join('\n')}`);
  }

  if (professional.length > 0) {
    sections.push(`\n## CAREER & PROFESSIONAL\n${professional.join('\n')}`);
  }

  if (preferences.length > 0) {
    sections.push(`\n## PREFERENCES & STYLE\n${preferences.join('\n')}`);
  }

  if (relationships.length > 0) {
    sections.push(`\n## RELATIONSHIPS\n${relationships.join('\n')}`);
  }

  // Enrich with structured people intelligence from Trust Graph
  try {
    const tg = getTrustGraph();
    if (tg) {
      const topPeople = tg.getMostTrusted(10);
      if (topPeople.length > 0) {
        const peopleLines = topPeople.map((p: any) => {
          const trustPct = Math.round((p.trust?.overall ?? 0.5) * 100);
          const domains = p.domains?.length > 0 ? p.domains.join(', ') : 'general';
          return `- **${p.primaryName}**: Trust ${trustPct}% | ${domains} | ${p.interactionCount || 0} interactions`;
        });
        sections.push(`\n## KEY PEOPLE\n${peopleLines.join('\n')}`);
      }
    }
  } catch {
    // Trust Graph not ready yet — skip enrichment
  }

  if (other.length > 0) {
    sections.push(`\n## OTHER KNOWLEDGE\n${other.join('\n')}`);
  }

  return sections.join('\n');
}

/**
 * Parse the LEARNED INSIGHTS section into its constituent parts:
 * consolidated narrative paragraphs and recent raw entries.
 */
function parseLearnedSection(content: string): {
  baseContent: string;
  consolidatedBlocks: string[];
  recentEntries: string[];
} {
  const lines = content.split('\n');
  const sectionStart = lines.findIndex((l) => l.startsWith('## LEARNED INSIGHTS'));

  if (sectionStart === -1) {
    return { baseContent: content, consolidatedBlocks: [], recentEntries: [] };
  }

  const baseContent = lines.slice(0, sectionStart).join('\n');
  const consolidatedBlocks: string[] = [];
  const recentEntries: string[] = [];

  let currentBlock: string[] = [];
  let inConsolidated = false;
  let inRecent = false;

  for (let i = sectionStart + 1; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();

    if (trimmed.startsWith('### Consolidated')) {
      // Save any previous consolidated block
      if (currentBlock.length > 0) {
        consolidatedBlocks.push(currentBlock.join('\n'));
        currentBlock = [];
      }
      inConsolidated = true;
      inRecent = false;
      currentBlock.push(line);
    } else if (trimmed.startsWith('### Recent')) {
      // Save any previous consolidated block
      if (inConsolidated && currentBlock.length > 0) {
        consolidatedBlocks.push(currentBlock.join('\n'));
        currentBlock = [];
      }
      inConsolidated = false;
      inRecent = true;
    } else if (inConsolidated) {
      currentBlock.push(line);
    } else if (inRecent || trimmed.startsWith('- ')) {
      // Raw entries — either under ### Recent or bare (legacy format)
      if (trimmed.startsWith('- ')) {
        recentEntries.push(trimmed);
      }
    }
  }

  // Flush final consolidated block
  if (inConsolidated && currentBlock.length > 0) {
    consolidatedBlocks.push(currentBlock.join('\n'));
  }

  return { baseContent, consolidatedBlocks, recentEntries };
}

/**
 * Reconstruct the full intelligence file from its parts.
 */
function reconstructFile(
  baseContent: string,
  consolidatedBlocks: string[],
  recentEntries: string[]
): string {
  let learned = LEARNED_SECTION_HEADER;

  if (consolidatedBlocks.length > 0) {
    learned += consolidatedBlocks.join('\n\n') + '\n\n';
  }

  learned += '### Recent\n';
  if (recentEntries.length > 0) {
    learned += recentEntries.join('\n') + '\n';
  }

  return baseContent.trimEnd() + learned;
}

/**
 * Consolidate recent learnings into a narrative paragraph using Claude Sonnet.
 * Fires when recent entries exceed CONSOLIDATION_THRESHOLD.
 * Nothing is ever deleted — raw entries become distilled narrative.
 */
async function consolidateLearnings(): Promise<void> {
  if (isConsolidating) return;
  isConsolidating = true;

  const filePath = path.join(app.getPath('userData'), 'friday-intelligence.md');
  const config = settingsManager.getAgentConfig();
  const userName = config.userName || 'the user';

  try {
    const content = await fs.readFile(filePath, 'utf-8').catch(() => '');
    if (!content) return;

    const { baseContent, consolidatedBlocks, recentEntries } = parseLearnedSection(content);

    if (recentEntries.length < CONSOLIDATION_THRESHOLD) return;

    console.log(`[Profile] Consolidating ${recentEntries.length} learnings...`);

    // Build the consolidation prompt
    const existingNarratives = consolidatedBlocks.length > 0
      ? `\n\nALREADY CONSOLIDATED (do NOT repeat these — only synthesize NEW insights):\n${consolidatedBlocks.join('\n')}`
      : '';

    const prompt = `You are compressing raw learning entries about a user into a tight narrative paragraph. These are observations an AI assistant has accumulated about ${userName}.

RAW ENTRIES TO CONSOLIDATE:
${recentEntries.join('\n')}
${existingNarratives}

Write a single dense paragraph (4-8 sentences) that captures every meaningful insight from the raw entries. Rules:
- Preserve ALL specific facts, names, preferences, and details — nothing gets lost
- Merge related items naturally (e.g. multiple work preferences into one sentence)
- Write in third person about ${userName}
- If an entry contradicts a previous consolidation, the newer entry wins — note the update
- Do NOT include timestamps or category tags — just clean narrative
- Do NOT include any preamble or explanation — return ONLY the paragraph`;

    const narrative = (await llmClient.text(prompt, { maxTokens: 1024 })).trim();

    if (!narrative) {
      console.warn('[Profile] Consolidation returned empty — keeping raw entries');
      return;
    }

    // Build the new consolidated block
    const today = new Date().toISOString().slice(0, 10);
    const newBlock = `### Consolidated (${today})\n${narrative}`;

    // All recent entries become consolidated — recent section resets to empty
    const updatedBlocks = [...consolidatedBlocks, newBlock];
    const updatedContent = reconstructFile(baseContent, updatedBlocks, []);
    await fs.writeFile(filePath, updatedContent, 'utf-8');

    console.log(`[Profile] Consolidated ${recentEntries.length} entries into narrative (${narrative.length} chars)`);
  } catch (err) {
    console.warn('[Profile] Consolidation failed:', err instanceof Error ? err.message : 'Unknown error');
  } finally {
    isConsolidating = false;
  }
}

/**
 * Append a learning insight to the intelligence profile.
 * Learnings are never evicted. When recent entries exceed the consolidation
 * threshold, Claude Sonnet compresses them into a narrative paragraph.
 */
export async function appendLearning(insight: string, category: string): Promise<void> {
  const filePath = path.join(app.getPath('userData'), 'friday-intelligence.md');
  try {
    let content = await fs.readFile(filePath, 'utf-8').catch(() => '');

    if (!content) {
      content = BLANK_INTELLIGENCE_FILE + LEARNED_SECTION_HEADER;
    }

    const { baseContent, consolidatedBlocks, recentEntries } = parseLearnedSection(content);

    // Duplicate check — against both recent entries AND consolidated narratives
    const lowerInsight = insight.toLowerCase();
    const isDuplicateRecent = recentEntries.some(
      (l) => l.toLowerCase().includes(lowerInsight) || lowerInsight.includes(l.slice(2).toLowerCase())
    );
    const isDuplicateConsolidated = consolidatedBlocks.some(
      (block) => block.toLowerCase().includes(lowerInsight)
    );
    if (isDuplicateRecent || isDuplicateConsolidated) return;

    // Build the new entry with timestamp
    const timestamp = new Date().toISOString().slice(0, 10);
    const entry = `- [${timestamp}] [${category}] ${insight}`;
    const updatedRecent = [...recentEntries, entry];

    // Write the updated file
    const updatedContent = reconstructFile(baseContent, consolidatedBlocks, updatedRecent);
    await fs.writeFile(filePath, updatedContent, 'utf-8');

    console.log(`[Profile] Learning appended: "${insight}" (${category})`);

    // Trigger consolidation if threshold crossed (non-blocking)
    if (updatedRecent.length >= CONSOLIDATION_THRESHOLD) {
      consolidateLearnings().catch((err) =>
        console.warn('[Profile] Background consolidation failed:', err instanceof Error ? err.message : 'Unknown error')
      );
    }
  } catch (err) {
    console.warn('[Profile] Failed to append learning:', err instanceof Error ? err.message : 'Unknown error');
  }
}

/**
 * Returns a condensed version of the intelligence profile for
 * inclusion in the Gemini Live system instruction (voice context).
 * For fresh installs, returns a minimal "profile being built" message.
 * For returning users, assembles from memories + learned insights.
 */
export async function getCondensedProfile(): Promise<string> {
  const config = settingsManager.getAgentConfig();
  const userName = config.userName || 'the user';
  const agentName = config.agentName || 'the agent';

  // If onboarding hasn't completed, return minimal profile
  if (!config.onboardingComplete || !config.userName) {
    return `## About the User
- Profile is being built during onboarding
- No information gathered yet — this is a fresh install
- Listen carefully and save every detail shared during the onboarding conversation`;
  }

  // Build condensed profile from long-term memories
  const longTerm = memoryManager.getLongTerm();
  const profileLines: string[] = [];

  // Group by category for readability
  const categories: Record<string, string[]> = {};
  for (const entry of longTerm) {
    const cat = entry.category || 'general';
    if (!categories[cat]) categories[cat] = [];
    categories[cat].push(entry.fact);
  }

  // Identity first
  if (categories.identity) {
    profileLines.push(...categories.identity.map((f) => `- ${f}`));
    delete categories.identity;
  }

  // Professional second
  if (categories.professional) {
    profileLines.push(...categories.professional.map((f) => `- ${f}`));
    delete categories.professional;
  }

  // Everything else
  for (const [, facts] of Object.entries(categories)) {
    profileLines.push(...facts.map((f) => `- ${f}`));
  }

  let result = `## What ${agentName} Knows About ${userName} (Intelligence Briefing)\n`;
  if (profileLines.length > 0) {
    result += profileLines.join('\n');
  } else {
    result += '- Basic profile established during onboarding — memories will accumulate over time';
  }

  // Append key people from Trust Graph
  try {
    const tg = getTrustGraph();
    if (tg) {
      const topPeople = tg.getMostTrusted(8);
      if (topPeople.length > 0) {
        result += `\n\n## Key People in ${userName}'s World\n`;
        for (const person of topPeople) {
          const trustPct = Math.round((person.trust?.overall ?? 0.5) * 100);
          const domains = person.domains?.length > 0 ? person.domains.join(', ') : 'general';
          const lastSeen = person.lastSeen
            ? `last seen ${Math.round((Date.now() - person.lastSeen) / 86400000)}d ago`
            : 'no recent interaction';
          result += `- **${person.primaryName}** (trust: ${trustPct}%, ${domains}, ${person.interactionCount || 0} interactions, ${lastSeen})\n`;
        }
      }
    }
  } catch {
    // Trust Graph not available — skip
  }

  // Append learned insights from the intelligence file
  try {
    const filePath = path.join(app.getPath('userData'), 'friday-intelligence.md');
    const content = await fs.readFile(filePath, 'utf-8').catch(() => '');
    if (content) {
      const { consolidatedBlocks, recentEntries } = parseLearnedSection(content);

      const insightLines: string[] = [];
      // Include consolidated narratives (most valuable)
      for (const block of consolidatedBlocks) {
        const lines = block.split('\n').filter((l) => l.trim() && !l.startsWith('###'));
        insightLines.push(...lines);
      }
      // Include last 10 recent entries
      const recent = recentEntries.slice(-10);
      for (const entry of recent) {
        // Strip timestamp/category tags for compactness
        const clean = entry.replace(/^- \[\d{4}-\d{2}-\d{2}\] \[\w+\] /, '- ');
        insightLines.push(clean);
      }

      if (insightLines.length > 0) {
        result += `\n\n## Learned About ${userName} (from conversations)\n` + insightLines.join('\n');
      }
    }
  } catch {
    // Non-critical — fall back to base profile
  }

  return result;
}
