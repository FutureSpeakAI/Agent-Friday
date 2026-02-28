/**
 * relationship-memory.ts — Relationship Memory for Agent Friday.
 *
 * Tracks the evolving relationship between the agent and the user:
 * total sessions, total duration, inside jokes, shared references,
 * trust level, communication style preferences, and emotional patterns.
 *
 * Auto-updated from episodic memory entries via Claude analysis.
 * Exposes getContextString() for injection into personality.ts.
 */

import { app } from 'electron';
import fs from 'fs/promises';
import path from 'path';
import type { Episode } from './episodic-memory';
import { settingsManager } from './settings';

interface SharedReference {
  reference: string;
  context: string;
  firstMentioned: number;
  lastMentioned: number;
  count: number;
}

interface CommunicationPreference {
  trait: string;
  value: string;
  confidence: number;
  observedAt: number;
}

interface RelationshipState {
  totalSessions: number;
  totalDurationMinutes: number;
  firstInteraction: number;
  lastInteraction: number;
  insideJokes: string[];
  sharedReferences: SharedReference[];
  communicationPreferences: CommunicationPreference[];
  trustLevel: number; // 0-1, increases with consistent interaction
  averageMood: string;
  favouriteTopics: Array<{ topic: string; count: number }>;
  peakHours: number[]; // hours of day when the user interacts most
  longestStreak: number; // consecutive days with interaction
  currentStreak: number;
  lastStreakDate: string; // ISO date string
}

const DEFAULTS: RelationshipState = {
  totalSessions: 0,
  totalDurationMinutes: 0,
  firstInteraction: 0,
  lastInteraction: 0,
  insideJokes: [],
  sharedReferences: [],
  communicationPreferences: [],
  trustLevel: 0.3,
  averageMood: 'neutral',
  favouriteTopics: [],
  peakHours: [],
  longestStreak: 0,
  currentStreak: 0,
  lastStreakDate: '',
};

class RelationshipMemory {
  private state: RelationshipState = { ...DEFAULTS };
  private memoryDir = '';
  private initialized = false;

  async initialize(): Promise<void> {
    this.memoryDir = path.join(app.getPath('userData'), 'memory');
    await fs.mkdir(this.memoryDir, { recursive: true });
    await this.load();
    this.initialized = true;
    console.log(
      `[RelationshipMemory] Loaded — ${this.state.totalSessions} sessions, ` +
      `trust: ${this.state.trustLevel.toFixed(2)}, streak: ${this.state.currentStreak}d`
    );
  }

  getState(): RelationshipState {
    return { ...this.state };
  }

  /**
   * Update relationship state from a newly created episode.
   * Extracts relationship-relevant data via Claude analysis + heuristics.
   */
  async updateFromEpisode(episode: Episode): Promise<void> {
    if (!this.initialized) return;

    // Basic stats
    this.state.totalSessions++;
    this.state.totalDurationMinutes += Math.round(episode.durationSeconds / 60);
    if (!this.state.firstInteraction) {
      this.state.firstInteraction = episode.startTime;
    }
    this.state.lastInteraction = episode.endTime;

    // Update streak tracking
    const today = new Date().toISOString().split('T')[0];
    const yesterday = new Date(Date.now() - 86400000).toISOString().split('T')[0];

    if (this.state.lastStreakDate === today) {
      // Already counted today — no change
    } else if (this.state.lastStreakDate === yesterday) {
      this.state.currentStreak++;
      this.state.lastStreakDate = today;
    } else if (this.state.lastStreakDate !== today) {
      this.state.currentStreak = 1;
      this.state.lastStreakDate = today;
    }

    if (this.state.currentStreak > this.state.longestStreak) {
      this.state.longestStreak = this.state.currentStreak;
    }

    // Track peak hours
    const hour = new Date(episode.startTime).getHours();
    this.state.peakHours.push(hour);
    // Keep last 100 for recency weighting
    if (this.state.peakHours.length > 100) {
      this.state.peakHours = this.state.peakHours.slice(-100);
    }

    // Update topic counts
    for (const topic of episode.topics) {
      const existing = this.state.favouriteTopics.find(
        (t) => t.topic.toLowerCase() === topic.toLowerCase()
      );
      if (existing) {
        existing.count++;
      } else {
        this.state.favouriteTopics.push({ topic, count: 1 });
      }
    }
    // Sort by frequency, keep top 20
    this.state.favouriteTopics.sort((a, b) => b.count - a.count);
    this.state.favouriteTopics = this.state.favouriteTopics.slice(0, 20);

    // Trust level — increases logarithmically with session count and streak
    this.state.trustLevel = Math.min(
      1.0,
      0.3 + Math.log10(this.state.totalSessions + 1) * 0.2 +
      Math.min(this.state.currentStreak * 0.02, 0.2)
    );

    // Claude analysis for relationship-specific insights
    await this.analyseEpisodeForRelationship(episode);

    await this.save();
  }

  /**
   * Build context string for injection into personality.ts system instruction.
   */
  getContextString(): string {
    if (this.state.totalSessions === 0) return '';

    const parts: string[] = ['## Relationship Context'];

    // Duration and frequency
    const daysSinceFirst = this.state.firstInteraction
      ? Math.max(1, Math.floor((Date.now() - this.state.firstInteraction) / 86400000))
      : 1;
    parts.push(
      `- ${this.state.totalSessions} conversations over ${daysSinceFirst} days ` +
      `(${this.state.totalDurationMinutes} total minutes)`
    );

    // Streak
    if (this.state.currentStreak > 1) {
      parts.push(`- Current streak: ${this.state.currentStreak} consecutive days`);
    }

    // Trust level description
    const userName = settingsManager.getAgentConfig().userName || 'The user';
    if (this.state.trustLevel > 0.8) {
      parts.push(`- Deep trust established — ${userName} treats you as a genuine collaborator`);
    } else if (this.state.trustLevel > 0.6) {
      parts.push(`- Strong working relationship — ${userName} relies on you regularly`);
    } else if (this.state.trustLevel > 0.4) {
      parts.push(`- Growing familiarity — ${userName} is becoming comfortable with your style`);
    }

    // Favourite topics
    const topTopics = this.state.favouriteTopics.slice(0, 5);
    if (topTopics.length > 0) {
      parts.push(
        `- Most discussed topics: ${topTopics.map((t) => t.topic).join(', ')}`
      );
    }

    // Inside jokes
    if (this.state.insideJokes.length > 0) {
      const recent = this.state.insideJokes.slice(-3);
      parts.push(
        `- Inside jokes/references you share: ${recent.join('; ')}`
      );
    }

    // Shared references
    const topRefs = this.state.sharedReferences
      .sort((a, b) => b.count - a.count)
      .slice(0, 3);
    if (topRefs.length > 0) {
      parts.push(
        `- Recurring references: ${topRefs.map((r) => `${r.reference} (${r.context})`).join('; ')}`
      );
    }

    // Communication preferences
    const highConfPrefs = this.state.communicationPreferences
      .filter((p) => p.confidence > 0.6)
      .slice(0, 3);
    if (highConfPrefs.length > 0) {
      parts.push(
        `- Communication notes: ${highConfPrefs.map((p) => `${p.trait}: ${p.value}`).join('; ')}`
      );
    }

    // Peak hours
    if (this.state.peakHours.length >= 10) {
      const hourCounts: Record<number, number> = {};
      for (const h of this.state.peakHours) {
        hourCounts[h] = (hourCounts[h] || 0) + 1;
      }
      const peakHour = Object.entries(hourCounts)
        .sort(([, a], [, b]) => b - a)[0];
      if (peakHour) {
        const h = parseInt(peakHour[0]);
        const period = h < 12 ? 'morning' : h < 17 ? 'afternoon' : h < 21 ? 'evening' : 'night';
        parts.push(`- ${userName} usually chats in the ${period} (peak: ${h}:00)`);
      }
    }

    return parts.join('\n');
  }

  /**
   * Use Claude to extract relationship-specific insights from an episode.
   */
  private async analyseEpisodeForRelationship(episode: Episode): Promise<void> {
    // Only run Claude analysis on longer or emotionally rich episodes
    if (episode.durationSeconds < 120 && episode.turnCount < 6) return;

    try {
      const Anthropic = require('@anthropic-ai/sdk');
      const anthropic = new Anthropic.default({
        apiKey: process.env.ANTHROPIC_API_KEY,
      });

      const config = settingsManager.getAgentConfig();
      const uName = config.userName || 'User';
      const aName = config.agentName || 'Agent';
      const transcript = episode.transcript
        ? episode.transcript
            .map((t) => `${t.role === 'user' ? uName : aName}: ${t.text}`)
            .join('\n')
            .slice(-4000)
        : episode.summary;

      const response = await anthropic.messages.create({
        model: 'claude-sonnet-4-20250514',
        max_tokens: 300,
        messages: [
          {
            role: 'user',
            content: `Analyse this conversation for relationship dynamics. Return ONLY valid JSON.

CONVERSATION:
${transcript}

EXISTING INSIDE JOKES: ${JSON.stringify(this.state.insideJokes.slice(-5))}

Return JSON:
{
  "newInsideJokes": ["any new inside jokes, running gags, or callback humour"],
  "sharedReferences": ["any shared cultural references, books, films, people, or concepts they both reference"],
  "communicationNotes": [{"trait": "trait name", "value": "observation"}],
  "moodSummary": "one word for ${uName}'s overall mood this session"
}

Rules:
- Only include GENUINELY new inside jokes, not generic conversation
- Communication notes should be about ${uName}'s PREFERENCES (likes directness, enjoys banter, etc.)
- If nothing notable, use empty arrays
- Be selective — only genuinely relationship-relevant observations`,
          },
        ],
      });

      const text =
        response.content.find((b: any) => b.type === 'text')?.text || '';
      const jsonMatch = text.match(/\{[\s\S]*\}/);
      if (!jsonMatch) return;

      const parsed = JSON.parse(jsonMatch[0]);

      // Merge inside jokes (deduplicate)
      if (Array.isArray(parsed.newInsideJokes)) {
        for (const joke of parsed.newInsideJokes) {
          if (joke && !this.state.insideJokes.includes(joke)) {
            this.state.insideJokes.push(joke);
          }
        }
        // Cap at 20
        if (this.state.insideJokes.length > 20) {
          this.state.insideJokes = this.state.insideJokes.slice(-20);
        }
      }

      // Merge shared references
      if (Array.isArray(parsed.sharedReferences)) {
        for (const ref of parsed.sharedReferences) {
          if (!ref) continue;
          const existing = this.state.sharedReferences.find(
            (r) => r.reference.toLowerCase() === ref.toLowerCase()
          );
          if (existing) {
            existing.count++;
            existing.lastMentioned = Date.now();
          } else {
            this.state.sharedReferences.push({
              reference: ref,
              context: episode.summary.slice(0, 80),
              firstMentioned: Date.now(),
              lastMentioned: Date.now(),
              count: 1,
            });
          }
        }
        // Cap at 30
        if (this.state.sharedReferences.length > 30) {
          this.state.sharedReferences.sort((a, b) => b.count - a.count);
          this.state.sharedReferences = this.state.sharedReferences.slice(0, 30);
        }
      }

      // Merge communication preferences
      if (Array.isArray(parsed.communicationNotes)) {
        for (const note of parsed.communicationNotes) {
          if (!note?.trait || !note?.value) continue;
          const existing = this.state.communicationPreferences.find(
            (p) => p.trait.toLowerCase() === note.trait.toLowerCase()
          );
          if (existing) {
            existing.value = note.value;
            existing.confidence = Math.min(1, existing.confidence + 0.1);
            existing.observedAt = Date.now();
          } else {
            this.state.communicationPreferences.push({
              trait: note.trait,
              value: note.value,
              confidence: 0.5,
              observedAt: Date.now(),
            });
          }
        }
        // Cap at 15
        if (this.state.communicationPreferences.length > 15) {
          this.state.communicationPreferences.sort(
            (a, b) => b.confidence - a.confidence
          );
          this.state.communicationPreferences =
            this.state.communicationPreferences.slice(0, 15);
        }
      }

      // Average mood tracking
      if (parsed.moodSummary) {
        this.state.averageMood = parsed.moodSummary;
      }
    } catch (err) {
      console.warn('[RelationshipMemory] Claude analysis failed:', err);
    }
  }

  private async save(): Promise<void> {
    const filePath = path.join(this.memoryDir, 'relationship.json');
    await fs.writeFile(filePath, JSON.stringify(this.state, null, 2), 'utf-8');
  }

  private async load(): Promise<void> {
    const filePath = path.join(this.memoryDir, 'relationship.json');
    try {
      const data = await fs.readFile(filePath, 'utf-8');
      const loaded = JSON.parse(data);
      this.state = { ...DEFAULTS, ...loaded };
    } catch {
      // File doesn't exist yet — keep defaults
    }
  }
}

export const relationshipMemory = new RelationshipMemory();
