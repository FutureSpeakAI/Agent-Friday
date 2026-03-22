/**
 * sentiment.ts — Sentiment Tracking Engine.
 * Analyses user messages for emotional tone using keyword heuristics.
 * Tracks mood over time, energy level, and emotional patterns.
 * Feeds into personality.ts for adaptive response behaviour.
 */

import { EventEmitter } from 'node:events';
import { app } from 'electron';
import fs from 'fs/promises';
import path from 'path';
import { settingsManager } from './settings';

export type Mood = 'positive' | 'neutral' | 'frustrated' | 'tired' | 'excited' | 'stressed' | 'curious' | 'focused';

export interface SentimentState {
  currentMood: Mood;
  confidence: number;
  energyLevel: number; // 0-1 scale
  moodStreak: number;  // consecutive messages with same mood
  lastAnalysed: number;
}

export interface MoodLogEntry {
  mood: Mood;
  confidence: number;
  energy: number;
  timestamp: number;
  trigger?: string; // brief snippet that triggered the classification
}

// --- Keyword patterns (ordered by specificity) ---

interface MoodPattern {
  mood: Mood;
  energy: number;
  keywords: RegExp[];
  weight: number;
}

const MOOD_PATTERNS: MoodPattern[] = [
  // Frustrated / annoyed
  {
    mood: 'frustrated',
    energy: 0.6,
    weight: 0.8,
    keywords: [
      /\b(frustrated|annoyed|irritated|ugh|damn|shit|fuck|bloody|ffs|wtf|broken|stupid|hate this|sick of|fed up|for god'?s? sake)\b/i,
      /\b(doesn'?t work|not working|still broken|keeps? (failing|crashing)|won'?t|can'?t believe)\b/i,
      /!{2,}/,
    ],
  },
  // Stressed / overwhelmed
  {
    mood: 'stressed',
    energy: 0.4,
    weight: 0.75,
    keywords: [
      /\b(stressed|overwhelmed|too much|swamped|drowning|deadline|behind|pressure|panic|anxiety|anxious|worried)\b/i,
      /\b(running out of time|not enough time|so much to do|can'?t keep up)\b/i,
    ],
  },
  // Tired / low energy
  {
    mood: 'tired',
    energy: 0.2,
    weight: 0.7,
    keywords: [
      /\b(tired|exhausted|knackered|shattered|drained|sleepy|wiped|burned? out|burnout|long day|need (a |some )?sleep|need (a |some )?rest|zonked)\b/i,
      /\b(barely awake|can'?t think|brain is fried|running on fumes)\b/i,
    ],
  },
  // Excited / high energy positive
  {
    mood: 'excited',
    energy: 0.95,
    weight: 0.8,
    keywords: [
      /\b(excited|amazing|incredible|brilliant|love it|perfect|yes!|nailed it|awesome|fantastic|can'?t wait|let'?s go|holy shit)\b/i,
      /\b(this is (great|huge|massive)|blew my mind|game.?changer)\b/i,
      /!{2,}.*(!|\?)/,
    ],
  },
  // Positive / warm
  {
    mood: 'positive',
    energy: 0.7,
    weight: 0.6,
    keywords: [
      /\b(thanks?|thank you|great|good|nice|happy|pleased|glad|cool|sweet|lovely|cheers|appreciate|helpful|working|works)\b/i,
      /\b(well done|good job|looks? good|that'?s right|exactly|perfect)\b/i,
      /(?:^|\s)[;:]-?\)/, // smiley
    ],
  },
  // Curious / exploratory
  {
    mood: 'curious',
    energy: 0.65,
    weight: 0.5,
    keywords: [
      /\b(wondering|curious|what if|how (would|could|does|do)|why (does|do|is|are)|interesting|tell me (more|about)|explore|investigate|dig into)\b/i,
      /\b(could we|what about|have you (thought|considered)|I'?m thinking)\b/i,
    ],
  },
  // Focused / deep work
  {
    mood: 'focused',
    energy: 0.75,
    weight: 0.45,
    keywords: [
      /\b(ok (so|let'?s|now)|right(,| so)|next|continue|moving on|let'?s (do|get|start|move)|focus on|back to|anyway)\b/i,
      /\b(implement|build|create|write|code|fix|refactor|deploy|ship)\b/i,
    ],
  },
];

const MAX_LOG_SIZE = 500;
const LOG_FILE = 'mood-log.json';

class SentimentEngine extends EventEmitter {
  private state: SentimentState = {
    currentMood: 'neutral',
    confidence: 0,
    energyLevel: 0.5,
    moodStreak: 0,
    lastAnalysed: 0,
  };

  constructor() {
    super();
  }

  private moodLog: MoodLogEntry[] = [];
  private logPath = '';

  async initialize(): Promise<void> {
    try {
      this.logPath = path.join(app.getPath('userData'), LOG_FILE);
      const data = await fs.readFile(this.logPath, 'utf-8');
      this.moodLog = JSON.parse(data);
      // Cap log size
      if (this.moodLog.length > MAX_LOG_SIZE) {
        this.moodLog = this.moodLog.slice(-MAX_LOG_SIZE);
      }
      console.log(`[Sentiment] Loaded ${this.moodLog.length} mood log entries`);
    } catch {
      // First run — empty log
    }
  }

  /**
   * Analyse a user message and update sentiment state.
   * Returns the detected mood.
   */
  analyse(text: string): Mood {
    if (!text || text.trim().length < 2) return this.state.currentMood;

    const previousMood = this.state.currentMood;
    const previousEnergy = this.state.energyLevel;

    let bestMood: Mood = 'neutral';
    let bestScore = 0;
    let bestEnergy = 0.5;
    let trigger = '';

    for (const pattern of MOOD_PATTERNS) {
      let matches = 0;
      for (const kw of pattern.keywords) {
        const match = text.match(kw);
        if (match) {
          matches++;
          if (!trigger && match[0]) {
            trigger = match[0].slice(0, 30);
          }
        }
      }

      if (matches > 0) {
        // Score = weight * (matches / total patterns), capped at weight
        const score = Math.min(pattern.weight, pattern.weight * (matches / pattern.keywords.length) + 0.2);
        if (score > bestScore) {
          bestScore = score;
          bestMood = pattern.mood;
          bestEnergy = pattern.energy;
        }
      }
    }

    // Time-of-day energy modulation
    const hour = new Date().getHours();
    if (hour >= 23 || hour < 6) {
      bestEnergy = Math.min(bestEnergy, 0.35); // Late night cap
    } else if (hour >= 6 && hour < 9) {
      bestEnergy *= 0.85; // Early morning slight reduction
    }

    // Update streak
    if (bestMood === this.state.currentMood) {
      this.state.moodStreak++;
    } else {
      this.state.moodStreak = 1;
    }

    // Smooth energy transitions (exponential moving average)
    this.state.energyLevel = this.state.energyLevel * 0.6 + bestEnergy * 0.4;
    this.state.currentMood = bestMood;
    this.state.confidence = bestScore;
    this.state.lastAnalysed = Date.now();

    // Log mood change
    const entry: MoodLogEntry = {
      mood: bestMood,
      confidence: bestScore,
      energy: this.state.energyLevel,
      timestamp: Date.now(),
      trigger: trigger || undefined,
    };
    this.moodLog.push(entry);

    // Prune and save periodically
    if (this.moodLog.length > MAX_LOG_SIZE) {
      this.moodLog = this.moodLog.slice(-MAX_LOG_SIZE);
    }
    this.saveMoodLog();

    // Push mood change to renderer — only when mood actually changed or energy shifted significantly
    if (bestMood !== previousMood || Math.abs(this.state.energyLevel - previousEnergy) > 0.05) {
      this.emit('mood-change', this.getState());
    }

    return bestMood;
  }

  getState(): SentimentState {
    return { ...this.state };
  }

  getMoodLog(): MoodLogEntry[] {
    return [...this.moodLog];
  }

  /**
   * Build a context string for injection into the system prompt.
   */
  getContextString(): string {
    if (!this.state.lastAnalysed) return '';

    const parts: string[] = ['## Emotional Context'];

    const moodDescriptions: Record<Mood, string> = {
      positive: 'in a good mood',
      neutral: 'in a neutral state',
      frustrated: 'frustrated or annoyed',
      tired: 'tired or low energy',
      excited: 'excited and high energy',
      stressed: 'stressed or under pressure',
      curious: 'in an exploratory, curious mood',
      focused: 'in deep focus mode',
    };

    const userName = settingsManager.getAgentConfig().userName || 'The user';
    parts.push(`- ${userName} seems ${moodDescriptions[this.state.currentMood]}`);

    if (this.state.moodStreak > 3) {
      parts.push(`- This mood has been consistent for ${this.state.moodStreak} messages`);
    }

    // Energy level description
    if (this.state.energyLevel < 0.3) {
      parts.push('- Energy level: low');
    } else if (this.state.energyLevel > 0.8) {
      parts.push('- Energy level: high');
    }

    // Recent mood trajectory (last 5 entries)
    const recent = this.moodLog.slice(-5);
    if (recent.length >= 3) {
      const moods = recent.map((e) => e.mood);
      const uniqueMoods = new Set(moods);
      if (uniqueMoods.size === 1 && moods[0] !== 'neutral') {
        parts.push(`- Mood has been consistently ${moods[0]} recently`);
      } else if (
        recent.length >= 3 &&
        recent[recent.length - 1].energy < recent[0].energy - 0.2
      ) {
        parts.push('- Energy has been declining over recent messages');
      }
    }

    return parts.join('\n');
  }

  private async saveMoodLog(): Promise<void> {
    try {
      if (this.logPath) {
        await fs.writeFile(this.logPath, JSON.stringify(this.moodLog, null, 2), 'utf-8');
      }
    } catch {
      // Non-critical
    }
  }
}

export const sentimentEngine = new SentimentEngine();
