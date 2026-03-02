/**
 * intelligence.ts — Background intelligence engine.
 * Runs research tasks using Claude, stores briefings, and serves them
 * to the renderer for Friday to speak when the user returns.
 *
 * Flow: Scheduler fires "research" action → intelligence engine runs →
 * Claude produces a briefing → stored in briefings.json →
 * on next user interaction, Friday has the briefing ready.
 */

import { app } from 'electron';
import fs from 'fs/promises';
import path from 'path';
import crypto from 'crypto';
import { memoryManager } from './memory';
import { taskScheduler } from './scheduler';

export interface Briefing {
  id: string;
  topic: string;
  content: string;
  createdAt: number;
  delivered: boolean;
  priority: 'high' | 'medium' | 'low';
}

interface ResearchTopic {
  topic: string;
  schedule: string;
  priority: string;
}

const MAX_BRIEFINGS = 20;
const BRIEFING_MAX_AGE_MS = 24 * 60 * 60 * 1000; // 24 hours

class IntelligenceEngine {
  private briefings: Briefing[] = [];
  private filePath: string = '';

  async initialize(): Promise<void> {
    this.filePath = path.join(app.getPath('userData'), 'briefings.json');

    try {
      const data = await fs.readFile(this.filePath, 'utf-8');
      this.briefings = JSON.parse(data);
    } catch {
      this.briefings = [];
    }

    // Prune old briefings
    this.prune();
    console.log(`[Intelligence] Initialized with ${this.briefings.length} briefings`);
  }

  /**
   * Run a research task — sends prompt to Claude, stores the result as a briefing.
   * Called when the scheduler fires a "research" action.
   */
  async runResearch(topic: string, priority: 'high' | 'medium' | 'low' = 'medium'): Promise<void> {
    console.log(`[Intelligence] Researching: ${topic}`);

    // Build context from user profile
    const longTerm = memoryManager.getLongTerm();
    const userProfile = longTerm.map((e) => `- ${e.fact}`).join('\n') || 'No user profile yet';

    const prompt = `You are a concise research assistant preparing a briefing for someone. Here's what you know about them:

${userProfile}

RESEARCH TOPIC: ${topic}

Prepare a brief, conversational summary (3-5 sentences max) that would be interesting and useful to this specific person. Make it feel personal and relevant to them. Write it as if you're telling a friend — not writing a report.

Current date: ${new Date().toLocaleDateString('en-GB', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })}
Current time: ${new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}

Focus on: actionable insights, interesting developments, things they'd genuinely want to know. Skip generic filler.`;

    try {
      const Anthropic = require('@anthropic-ai/sdk');
      const anthropic = new Anthropic.default({ apiKey: process.env.ANTHROPIC_API_KEY });

      const response = await anthropic.messages.create({
        model: 'claude-sonnet-4-20250514',
        max_tokens: 512,
        messages: [{ role: 'user', content: prompt }],
      });

      const content = response.content.find((b: any) => b.type === 'text')?.text || '';

      if (content.trim()) {
        const briefing: Briefing = {
          id: crypto.randomUUID().slice(0, 8),
          topic,
          content: content.trim(),
          createdAt: Date.now(),
          delivered: false,
          priority,
        };

        this.briefings.push(briefing);
        this.prune();
        await this.save();

        console.log(`[Intelligence] Briefing ready: ${topic} (${briefing.id})`);
      }
    } catch (err) {
      // Crypto Sprint 16: Sanitize — research makes Perplexity/Gemini API calls.
      console.warn(`[Intelligence] Research failed for "${topic}":`, err instanceof Error ? err.message : 'Unknown error');
    }
  }

  /**
   * Get undelivered briefings, sorted by priority then recency.
   * Marks them as delivered after retrieval.
   */
  async getUndeliveredBriefings(): Promise<Briefing[]> {
    const undelivered = this.briefings
      .filter((b) => !b.delivered)
      .sort((a, b) => {
        const priorityOrder = { high: 0, medium: 1, low: 2 };
        const pDiff = priorityOrder[a.priority] - priorityOrder[b.priority];
        if (pDiff !== 0) return pDiff;
        return b.createdAt - a.createdAt;
      });

    // Mark as delivered
    for (const b of undelivered) {
      b.delivered = true;
    }

    if (undelivered.length > 0) {
      await this.save();
    }

    return undelivered;
  }

  /**
   * Get a summary string of all pending briefings for injection into Gemini.
   * Returns empty string if no briefings pending.
   */
  async getBriefingSummary(): Promise<string> {
    const briefings = await this.getUndeliveredBriefings();
    if (briefings.length === 0) return '';

    const lines = briefings.map(
      (b) => `**${b.topic}**: ${b.content}`
    );

    return `[INTELLIGENCE BRIEFING — share these naturally when appropriate, don't dump them all at once]\n\n${lines.join('\n\n')}`;
  }

  /**
   * Set up intelligence research tasks from the onboarding conversation.
   * Called via the setup_intelligence Gemini tool.
   */
  async setupFromOnboarding(topics: ResearchTopic[]): Promise<string> {
    const created: string[] = [];

    for (const topic of topics) {
      let cronPattern: string;
      const taskType: 'once' | 'recurring' = 'recurring';

      switch (topic.schedule) {
        case 'daily_morning':
          cronPattern = '0 8 * * *'; // 8am daily
          break;
        case 'daily_evening':
          cronPattern = '0 18 * * *'; // 6pm daily
          break;
        case 'weekly_monday':
          cronPattern = '0 9 * * 1'; // Monday 9am
          break;
        case 'weekly_friday':
          cronPattern = '0 17 * * 5'; // Friday 5pm
          break;
        case 'hourly':
          cronPattern = '0 * * * *'; // top of every hour
          break;
        case 'twice_daily':
          cronPattern = '0 8,17 * * *'; // 8am and 5pm
          break;
        default:
          cronPattern = '0 9 * * *'; // default: daily 9am
      }

      try {
        await taskScheduler.createTask({
          description: `Research: ${topic.topic}`,
          type: taskType,
          cron_pattern: cronPattern,
          action: 'research',
          payload: topic.topic,
        });

        created.push(`"${topic.topic}" (${topic.schedule})`);
      } catch (err) {
        // Crypto Sprint 16: Sanitize — scheduler errors could contain API data.
        console.warn(`[Intelligence] Failed to create task for "${topic.topic}":`, err instanceof Error ? err.message : 'Unknown error');
      }
    }

    // Also run an immediate research burst for high-priority topics
    const highPriority = topics.filter((t) => t.priority === 'high');
    for (const topic of highPriority.slice(0, 3)) {
      // Don't await — let these run in background
      this.runResearch(topic.topic, 'high').catch(() => {});
    }

    const summary = created.length > 0
      ? `Set up ${created.length} intelligence tasks: ${created.join(', ')}. High-priority topics are being researched right now.`
      : 'No intelligence tasks created.';

    console.log(`[Intelligence] ${summary}`);
    return summary;
  }

  /**
   * Get ALL briefings (both delivered and undelivered), sorted newest first.
   * Used by the Research Panel in the renderer.
   */
  getAllBriefings(): Briefing[] {
    return [...this.briefings].sort((a, b) => b.createdAt - a.createdAt);
  }

  private prune(): void {
    const cutoff = Date.now() - BRIEFING_MAX_AGE_MS;

    // Remove old delivered briefings
    this.briefings = this.briefings.filter(
      (b) => !b.delivered || b.createdAt > cutoff
    );

    // Cap total briefings
    if (this.briefings.length > MAX_BRIEFINGS) {
      this.briefings = this.briefings.slice(-MAX_BRIEFINGS);
    }
  }

  private async save(): Promise<void> {
    await fs.writeFile(this.filePath, JSON.stringify(this.briefings, null, 2), 'utf-8');
  }
}

export const intelligenceEngine = new IntelligenceEngine();
