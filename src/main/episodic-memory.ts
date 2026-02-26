/**
 * episodic-memory.ts — Episodic Memory Store for EVE OS.
 *
 * Records timestamped session episodes with Claude-generated summaries,
 * topics, emotional tone, and key decisions. Episodes are created
 * automatically when a Gemini Live session disconnects (sessions >60s).
 *
 * Persisted to episodes.json + Obsidian vault (EVE/episodes/).
 */

import { app } from 'electron';
import fs from 'fs/promises';
import path from 'path';
import crypto from 'crypto';
import { settingsManager } from './settings';
import { semanticSearch } from './semantic-search';
import { relationshipMemory } from './relationship-memory';

export interface Episode {
  id: string;
  startTime: number;
  endTime: number;
  durationSeconds: number;
  summary: string;
  topics: string[];
  emotionalTone: string;
  keyDecisions: string[];
  turnCount: number;
  /** Raw transcript lines — kept for search but not synced to Obsidian */
  transcript?: Array<{ role: string; text: string }>;
}

const MAX_EPISODES = 200;
const MIN_SESSION_SECONDS = 60;

class EpisodicMemoryStore {
  private episodes: Episode[] = [];
  private memoryDir = '';
  private initialized = false;

  async initialize(): Promise<void> {
    this.memoryDir = path.join(app.getPath('userData'), 'memory');
    await fs.mkdir(this.memoryDir, { recursive: true });
    await this.load();
    this.initialized = true;
    console.log(`[EpisodicMemory] Loaded ${this.episodes.length} episodes`);
  }

  getAll(): Episode[] {
    return this.episodes;
  }

  getById(id: string): Episode | undefined {
    return this.episodes.find((e) => e.id === id);
  }

  getRecent(count = 5): Episode[] {
    return this.episodes.slice(-count);
  }

  /**
   * Create an episode from a completed conversation session.
   * Uses Claude Sonnet to generate a summary, extract topics, emotional tone, and key decisions.
   */
  async createFromSession(
    transcript: Array<{ role: string; text: string }>,
    startTime: number,
    endTime: number
  ): Promise<Episode | null> {
    const durationSeconds = Math.round((endTime - startTime) / 1000);

    // Skip very short sessions
    if (durationSeconds < MIN_SESSION_SECONDS) {
      console.log(`[EpisodicMemory] Session too short (${durationSeconds}s), skipping`);
      return null;
    }

    // Skip sessions with too few turns
    if (transcript.length < 2) {
      console.log('[EpisodicMemory] Too few turns, skipping');
      return null;
    }

    const turnCount = transcript.length;

    // Build conversation text for Claude analysis
    const conversationText = transcript
      .map((t) => {
        const label = t.role === 'user'
          ? (settingsManager.getAgentConfig().userName || 'User')
          : (settingsManager.getAgentConfig().agentName || 'Agent');
        return `${label}: ${t.text}`;
      })
      .join('\n');

    // Truncate if extremely long (keep last ~8k chars)
    const maxChars = 8000;
    const trimmedConversation =
      conversationText.length > maxChars
        ? '... [earlier conversation truncated] ...\n' +
          conversationText.slice(-maxChars)
        : conversationText;

    let summary = '';
    let topics: string[] = [];
    let emotionalTone = 'neutral';
    let keyDecisions: string[] = [];

    try {
      const Anthropic = require('@anthropic-ai/sdk');
      const anthropic = new Anthropic.default({
        apiKey: process.env.ANTHROPIC_API_KEY,
      });

      const response = await anthropic.messages.create({
        model: 'claude-sonnet-4-20250514',
        max_tokens: 512,
        messages: [
          {
            role: 'user',
            content: `Analyse this conversation between ${settingsManager.getAgentConfig().userName || 'the user'} and their AI assistant ${settingsManager.getAgentConfig().agentName || 'the agent'}. Return ONLY valid JSON with no other text.

CONVERSATION:
${trimmedConversation}

Return JSON in this exact format:
{
  "summary": "2-3 sentence summary of what was discussed and accomplished",
  "topics": ["topic1", "topic2"],
  "emotionalTone": "one word: positive, neutral, frustrated, excited, focused, stressed, playful, reflective",
  "keyDecisions": ["any decisions made or action items agreed upon"]
}

If no key decisions were made, use an empty array. Keep topics concise (1-3 words each).`,
          },
        ],
      });

      const text =
        response.content.find((b: any) => b.type === 'text')?.text || '';
      const jsonMatch = text.match(/\{[\s\S]*\}/);

      if (jsonMatch) {
        const parsed = JSON.parse(jsonMatch[0]);
        summary = parsed.summary || '';
        topics = Array.isArray(parsed.topics) ? parsed.topics : [];
        emotionalTone = parsed.emotionalTone || 'neutral';
        keyDecisions = Array.isArray(parsed.keyDecisions)
          ? parsed.keyDecisions
          : [];
      }
    } catch (err) {
      console.warn('[EpisodicMemory] Claude analysis failed, using fallback:', err);
      // Fallback: basic summary from first/last turns
      const firstUser = transcript.find((t) => t.role === 'user');
      summary = firstUser
        ? `Session about: ${firstUser.text.slice(0, 100)}...`
        : `${turnCount}-turn conversation session`;
    }

    const episode: Episode = {
      id: crypto.randomUUID(),
      startTime,
      endTime,
      durationSeconds,
      summary,
      topics,
      emotionalTone,
      keyDecisions,
      turnCount,
      transcript,
    };

    this.episodes.push(episode);

    // Cap at max
    if (this.episodes.length > MAX_EPISODES) {
      this.episodes = this.episodes.slice(-MAX_EPISODES);
    }

    await this.save();
    await this.syncEpisodeToVault(episode);

    // Index for semantic search
    const searchText = `${summary} ${topics.join(' ')} ${keyDecisions.join(' ')}`;
    semanticSearch.index(episode.id, searchText, 'episode', {
      summary,
      topics,
      emotionalTone,
      startTime,
    }).catch(() => {});

    // Update relationship memory with this episode
    relationshipMemory.updateFromEpisode(episode).catch((err) => {
      console.warn('[EpisodicMemory] Relationship memory update failed:', err);
    });

    console.log(
      `[EpisodicMemory] Created episode ${episode.id.slice(0, 8)}: "${summary.slice(0, 80)}..."`
    );

    return episode;
  }

  /**
   * Search episodes by text query. Matches against summary, topics, key decisions, and transcript.
   * Returns episodes sorted by relevance (most recent matches first).
   */
  search(query: string, maxResults = 10): Episode[] {
    const q = query.toLowerCase();

    const scored = this.episodes
      .map((ep) => {
        let score = 0;

        // Summary match (highest weight)
        if (ep.summary.toLowerCase().includes(q)) score += 10;

        // Topics match
        for (const topic of ep.topics) {
          if (topic.toLowerCase().includes(q)) score += 5;
        }

        // Key decisions match
        for (const decision of ep.keyDecisions) {
          if (decision.toLowerCase().includes(q)) score += 4;
        }

        // Transcript match (lower weight, broader coverage)
        if (ep.transcript) {
          for (const turn of ep.transcript) {
            if (turn.text.toLowerCase().includes(q)) {
              score += 1;
              break; // Count once per episode
            }
          }
        }

        // Recency bonus — more recent episodes get a slight boost
        const ageHours = (Date.now() - ep.endTime) / (1000 * 60 * 60);
        if (ageHours < 24) score += 3;
        else if (ageHours < 168) score += 1; // within a week

        return { episode: ep, score };
      })
      .filter((item) => item.score > 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, maxResults);

    return scored.map((s) => s.episode);
  }

  async deleteEpisode(id: string): Promise<boolean> {
    const before = this.episodes.length;
    this.episodes = this.episodes.filter((e) => e.id !== id);

    if (this.episodes.length < before) {
      await this.save();
      semanticSearch.remove(id);

      // Delete from Obsidian vault
      const vaultPath = this.getVaultPath();
      if (vaultPath) {
        try {
          const dir = path.join(vaultPath, 'EVE', 'episodes');
          const files = await fs.readdir(dir);
          for (const file of files) {
            if (file.includes(id.slice(0, 8))) {
              await fs.unlink(path.join(dir, file));
              break;
            }
          }
        } catch {
          // Vault may not exist
        }
      }

      return true;
    }
    return false;
  }

  /**
   * Build context string for injection into personality.ts system instruction.
   * Shows recent episode summaries so EVE can reference past conversations naturally.
   */
  getContextString(): string {
    const recent = this.getRecent(5);
    if (recent.length === 0) return '';

    const lines = recent.map((ep) => {
      const when = this.formatTimeAgo(ep.endTime);
      const topics = ep.topics.length > 0 ? ` [${ep.topics.join(', ')}]` : '';
      return `- ${when}: ${ep.summary}${topics}`;
    });

    return `## Recent Conversations\n${lines.join('\n')}`;
  }

  private formatTimeAgo(timestamp: number): string {
    const diff = Date.now() - timestamp;
    const minutes = Math.floor(diff / 60000);
    const hours = Math.floor(diff / 3600000);
    const days = Math.floor(diff / 86400000);

    if (minutes < 60) return `${minutes}m ago`;
    if (hours < 24) return `${hours}h ago`;
    if (days === 1) return 'yesterday';
    if (days < 7) return `${days} days ago`;
    return new Date(timestamp).toLocaleDateString('en-GB', {
      day: 'numeric',
      month: 'short',
    });
  }

  private getVaultPath(): string {
    try {
      return settingsManager.getObsidianVaultPath();
    } catch {
      return '';
    }
  }

  private async syncEpisodeToVault(episode: Episode): Promise<void> {
    const vaultPath = this.getVaultPath();
    if (!vaultPath) return;

    try {
      const dir = path.join(vaultPath, 'EVE', 'episodes');
      await fs.mkdir(dir, { recursive: true });

      const date = new Date(episode.startTime);
      const dateStr = date.toISOString().split('T')[0];
      const slug = episode.summary
        .toLowerCase()
        .replace(/[^a-z0-9\s]/g, '')
        .replace(/\s+/g, '-')
        .slice(0, 40);
      const filename = `${dateStr}-${slug}-${episode.id.slice(0, 8)}.md`;

      const frontmatter = [
        '---',
        `id: ${episode.id}`,
        `date: ${date.toISOString()}`,
        `duration: ${episode.durationSeconds}s`,
        `tone: ${episode.emotionalTone}`,
        `turns: ${episode.turnCount}`,
        `topics: [${episode.topics.join(', ')}]`,
        `tags: [eve-episode, ${episode.emotionalTone}]`,
        '---',
      ].join('\n');

      const body = [
        `# ${episode.summary}`,
        '',
        `**Duration**: ${Math.round(episode.durationSeconds / 60)} minutes | **Turns**: ${episode.turnCount} | **Tone**: ${episode.emotionalTone}`,
        '',
        episode.topics.length > 0
          ? `**Topics**: ${episode.topics.join(', ')}`
          : '',
        '',
        episode.keyDecisions.length > 0
          ? `## Key Decisions\n${episode.keyDecisions.map((d) => `- ${d}`).join('\n')}`
          : '',
      ]
        .filter(Boolean)
        .join('\n');

      await fs.writeFile(path.join(dir, filename), `${frontmatter}\n${body}\n`, 'utf-8');
    } catch (err) {
      console.warn('[EpisodicMemory] Vault sync failed:', err);
    }
  }

  private async save(): Promise<void> {
    const filePath = path.join(this.memoryDir, 'episodes.json');
    // Strip transcripts for storage efficiency — they can be large
    const stripped = this.episodes.map((ep) => ({
      ...ep,
      transcript: undefined,
    }));
    await fs.writeFile(filePath, JSON.stringify(stripped, null, 2), 'utf-8');
  }

  private async load(): Promise<void> {
    const filePath = path.join(this.memoryDir, 'episodes.json');
    try {
      const data = await fs.readFile(filePath, 'utf-8');
      this.episodes = JSON.parse(data);
    } catch {
      // File doesn't exist yet
    }
  }
}

export const episodicMemory = new EpisodicMemoryStore();
