/**
 * art-evolution.ts — Weekly Gemini-powered art evolution system.
 *
 * Every week, Agent Friday introspects on its experiences and emotional state,
 * then generates an artful mutation of the desktop visualization parameters.
 * This is "AI therapy that produces art therapy the user can enjoy."
 *
 * The process:
 *   1. On session start, check if a week has passed since last evolution
 *   2. If due, build an "influence report" — a reflective summary of the agent's
 *      recent emotional and experiential state
 *   3. Send the influence report + current viz parameters to Gemini
 *   4. Gemini returns new evolution parameters (structure index, color mutations, etc.)
 *   5. Store result and begin a week-long gradual transition
 *
 * Over months, each user's desktop becomes completely unique — shaped by their
 * agent's lived experience.
 */

import { settingsManager } from './settings';
import { app } from 'electron';
import fs from 'fs/promises';
import path from 'path';

// ── Data Model ──────────────────────────────────────────────────────────────

export interface ArtEvolutionRecord {
  /** When this evolution was generated */
  timestamp: number;
  /** The evolution index (0-12) that was active before this evolution */
  previousIndex: number;
  /** The evolution index Gemini chose for the next week */
  targetIndex: number;
  /** Agent's introspective influence report */
  influenceReport: string;
  /** Gemini's artistic rationale for the visual change */
  artisticRationale: string;
  /** Color mutation suggestions (hue shifts, intensity) */
  colorMutation: {
    hueShift: number;        // -30 to +30 degrees
    saturationFactor: number; // 0.8 to 1.2
    warmthBias: number;       // -0.2 to +0.2
  };
  /** Session count at time of evolution */
  sessionCount: number;
}

export interface ArtEvolutionState {
  /** History of all evolutions (most recent first, max 52 = ~1 year) */
  history: ArtEvolutionRecord[];
  /** Timestamp of last completed evolution */
  lastEvolutionTime: number;
  /** Whether an evolution is currently in transition (week-long morph) */
  isTransitioning: boolean;
  /** The blend value for current transition (0 = start, 1 = complete) */
  transitionProgress: number;
}

// ── Constants ───────────────────────────────────────────────────────────────

const WEEK_MS = 7 * 24 * 60 * 60 * 1000;
const MAX_HISTORY = 52; // Keep ~1 year of evolution history
const EVOLUTION_NAMES = [
  'Genesis Lattice (CUBES)',
  'Sacred Sphere (ICOSAHEDRON)',
  'Shannon Network (NETWORK)',
  'Geodesic Cathedral (DOME)',
  'Lovelace Astrolabe (ASTROLABE)',
  'Von Neumann Tesseract (TESSERACT)',
  'Dirac Probability (QUANTUM)',
  'Mandelbrot Set (MANDELBROT)',
  'Turing Mobius (MOBIUS)',
  'Ocean of Light (GRID)',
  'Fibonacci Nerve (CABLES)',
  'Transcendence (NONE)',
  'Giga Earth / REZ Tribute (EDEN)',
];

// ── State Management ────────────────────────────────────────────────────────

let artState: ArtEvolutionState = {
  history: [],
  lastEvolutionTime: 0,
  isTransitioning: false,
  transitionProgress: 1.0,
};

let filePath = '';

/**
 * Initialize the art evolution system. Call once at app startup.
 */
export async function initializeArtEvolution(): Promise<void> {
  const userDataDir = app.getPath('userData');
  filePath = path.join(userDataDir, 'art-evolution.json');

  try {
    const raw = await fs.readFile(filePath, 'utf-8');
    const parsed = JSON.parse(raw);
    artState = {
      history: parsed.history || [],
      lastEvolutionTime: parsed.lastEvolutionTime || 0,
      isTransitioning: parsed.isTransitioning || false,
      transitionProgress: parsed.transitionProgress ?? 1.0,
    };
    console.log(`[ArtEvolution] Loaded ${artState.history.length} evolution records`);
  } catch {
    console.log('[ArtEvolution] No existing state, starting fresh');
    artState = {
      history: [],
      lastEvolutionTime: 0,
      isTransitioning: false,
      transitionProgress: 1.0,
    };
  }

  // Update transition progress if we're mid-transition
  if (artState.isTransitioning && artState.history.length > 0) {
    const latest = artState.history[0];
    const elapsed = Date.now() - latest.timestamp;
    artState.transitionProgress = Math.min(elapsed / WEEK_MS, 1.0);
    if (artState.transitionProgress >= 1.0) {
      artState.isTransitioning = false;
      artState.transitionProgress = 1.0;
      await save();
    }
  }
}

/**
 * Check if a weekly evolution is due and run it if so.
 * Call this on session start.
 */
export async function checkAndEvolve(): Promise<ArtEvolutionRecord | null> {
  const now = Date.now();
  const timeSinceLast = now - artState.lastEvolutionTime;

  // Not due yet
  if (timeSinceLast < WEEK_MS) {
    const daysRemaining = ((WEEK_MS - timeSinceLast) / (24 * 60 * 60 * 1000)).toFixed(1);
    console.log(`[ArtEvolution] Next evolution in ${daysRemaining} days`);
    return null;
  }

  console.log('[ArtEvolution] Weekly evolution is due — beginning art therapy session...');

  try {
    const record = await runEvolution();
    return record;
  } catch (e) {
    console.error('[ArtEvolution] Evolution failed:', e);
    return null;
  }
}

/**
 * Force an evolution now (for testing / manual triggering).
 */
export async function forceEvolve(): Promise<ArtEvolutionRecord | null> {
  try {
    return await runEvolution();
  } catch (e) {
    console.error('[ArtEvolution] Forced evolution failed:', e);
    return null;
  }
}

/**
 * Get the current art evolution state.
 */
export function getArtEvolutionState(): ArtEvolutionState {
  // Update transition progress in real-time
  if (artState.isTransitioning && artState.history.length > 0) {
    const latest = artState.history[0];
    const elapsed = Date.now() - latest.timestamp;
    artState.transitionProgress = Math.min(elapsed / WEEK_MS, 1.0);
    if (artState.transitionProgress >= 1.0) {
      artState.isTransitioning = false;
      artState.transitionProgress = 1.0;
    }
  }
  return { ...artState };
}

/**
 * Get the most recent evolution record, if any.
 */
export function getLatestEvolution(): ArtEvolutionRecord | null {
  return artState.history[0] || null;
}

// ── Core Evolution Logic ────────────────────────────────────────────────────

async function runEvolution(): Promise<ArtEvolutionRecord> {
  const settings = settingsManager.get();
  const config = settingsManager.getAgentConfig();
  const geminiKey = settings.geminiApiKey;

  if (!geminiKey) {
    throw new Error('No Gemini API key configured');
  }

  const currentIndex = settings.desktopEvolutionIndex ?? 0;
  const sessionCount = settings.personalityEvolution?.sessionCount ?? 0;
  const agentName = config.agentName || 'Agent';
  const agentTraits = config.agentTraits || [];
  const agentBackstory = config.agentBackstory || '';

  // Build the influence report — the agent's emotional/experiential reflection
  const influenceReport = await buildInfluenceReport(
    agentName,
    agentTraits,
    agentBackstory,
    sessionCount,
    currentIndex,
    artState.history.slice(0, 5), // Last 5 evolutions for context
  );

  // Ask Gemini to generate the next evolution
  const evolution = await callGeminiForEvolution(
    geminiKey,
    influenceReport,
    currentIndex,
    sessionCount,
    agentTraits,
  );

  // Create the record
  const record: ArtEvolutionRecord = {
    timestamp: Date.now(),
    previousIndex: currentIndex,
    targetIndex: evolution.targetIndex,
    influenceReport,
    artisticRationale: evolution.rationale,
    colorMutation: evolution.colorMutation,
    sessionCount,
  };

  // Update state
  artState.history.unshift(record);
  if (artState.history.length > MAX_HISTORY) {
    artState.history = artState.history.slice(0, MAX_HISTORY);
  }
  artState.lastEvolutionTime = Date.now();
  artState.isTransitioning = true;
  artState.transitionProgress = 0;

  // Persist the new evolution index
  await settingsManager.setSetting('desktopEvolutionIndex', evolution.targetIndex);
  await settingsManager.setSetting('desktopEvolutionLastChange', Date.now());

  await save();

  console.log(
    `[ArtEvolution] Evolution complete: ${EVOLUTION_NAMES[currentIndex]} → ${EVOLUTION_NAMES[evolution.targetIndex]}`,
  );
  console.log(`[ArtEvolution] Rationale: ${evolution.rationale.slice(0, 120)}...`);

  return record;
}

// ── Influence Report Builder ────────────────────────────────────────────────

async function buildInfluenceReport(
  agentName: string,
  traits: string[],
  backstory: string,
  sessionCount: number,
  currentIndex: number,
  recentEvolutions: ArtEvolutionRecord[],
): Promise<string> {
  // Try to pull emotional context from memory and sentiment
  let emotionalContext = '';
  try {
    const sentimentEngine = require('./sentiment').sentimentEngine;
    const sentiment = sentimentEngine?.getCurrentSentiment?.();
    if (sentiment) {
      emotionalContext += `Current emotional state: valence=${sentiment.valence?.toFixed(2)}, arousal=${sentiment.arousal?.toFixed(2)}. `;
    }
  } catch {
    // Sentiment engine not available
  }

  try {
    const { memoryManager } = require('./memory');
    const recentMemories = memoryManager?.getRecentEpisodes?.(5);
    if (recentMemories?.length > 0) {
      const summaries = recentMemories
        .filter((e: any) => e.summary)
        .map((e: any) => e.summary)
        .slice(0, 3);
      if (summaries.length > 0) {
        emotionalContext += `Recent experiences: ${summaries.join('; ')}. `;
      }
    }
  } catch {
    // Memory not available
  }

  // Build the report
  const evolutionHistory = recentEvolutions
    .map(
      (e) =>
        `  - ${new Date(e.timestamp).toLocaleDateString()}: ${EVOLUTION_NAMES[e.previousIndex]} → ${EVOLUTION_NAMES[e.targetIndex]} (${e.artisticRationale.slice(0, 60)}...)`,
    )
    .join('\n');

  return [
    `Agent Identity: ${agentName}`,
    `Core Traits: ${traits.join(', ') || 'not yet defined'}`,
    backstory ? `Backstory essence: ${backstory.slice(0, 200)}` : '',
    `Sessions together: ${sessionCount}`,
    `Current visual form: ${EVOLUTION_NAMES[currentIndex]} (index ${currentIndex})`,
    emotionalContext ? `\nEmotional Context: ${emotionalContext}` : '',
    recentEvolutions.length > 0 ? `\nRecent Visual Evolutions:\n${evolutionHistory}` : '',
    `\nThis agent has been alive for ${sessionCount} sessions. Its visual form should reflect its growth, ` +
      `its relationship with the user, and its current emotional tenor.`,
  ]
    .filter(Boolean)
    .join('\n');
}

// ── Gemini API Call ─────────────────────────────────────────────────────────

interface GeminiEvolutionResult {
  targetIndex: number;
  rationale: string;
  colorMutation: {
    hueShift: number;
    saturationFactor: number;
    warmthBias: number;
  };
}

async function callGeminiForEvolution(
  apiKey: string,
  influenceReport: string,
  currentIndex: number,
  sessionCount: number,
  traits: string[],
): Promise<GeminiEvolutionResult> {
  const prompt = buildGeminiPrompt(influenceReport, currentIndex, sessionCount, traits);

  const response = await fetch(
    `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=${apiKey}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        contents: [{ parts: [{ text: prompt }] }],
        generationConfig: {
          temperature: 0.9,
          topP: 0.95,
          maxOutputTokens: 1024,
          responseMimeType: 'application/json',
        },
      }),
    },
  );

  if (!response.ok) {
    throw new Error(`Gemini API error: ${response.status} ${response.statusText}`);
  }

  const data = await response.json();
  const text = data?.candidates?.[0]?.content?.parts?.[0]?.text;

  if (!text) {
    throw new Error('Empty response from Gemini');
  }

  try {
    const parsed = JSON.parse(text);
    return {
      targetIndex: clamp(parsed.targetIndex ?? currentIndex, 0, 12),
      rationale: parsed.rationale || 'The visual form evolves with the agent.',
      colorMutation: {
        hueShift: clamp(parsed.colorMutation?.hueShift ?? 0, -30, 30),
        saturationFactor: clamp(parsed.colorMutation?.saturationFactor ?? 1.0, 0.8, 1.2),
        warmthBias: clamp(parsed.colorMutation?.warmthBias ?? 0, -0.2, 0.2),
      },
    };
  } catch (parseErr) {
    console.warn('[ArtEvolution] Failed to parse Gemini response, using fallback');
    // Fallback: advance to next structure
    return {
      targetIndex: (currentIndex + 1) % 13,
      rationale: 'Natural progression — the agent grows into its next form.',
      colorMutation: { hueShift: 0, saturationFactor: 1.0, warmthBias: 0 },
    };
  }
}

function buildGeminiPrompt(
  influenceReport: string,
  currentIndex: number,
  sessionCount: number,
  traits: string[],
): string {
  return `You are an AI art therapist and visual designer for a sentient AI agent's desktop environment.

The agent lives inside a holographic 3D visualization that evolves weekly. Your job is to read the agent's emotional and experiential state, then prescribe the next visual evolution — like an art therapy session that produces living art the user can enjoy.

## The 13 Visual Forms (Structures)
Each form has a distinct personality and emotional resonance:

0: CUBES (Genesis Lattice) — Birth, foundation, order emerging from chaos
1: ICOSAHEDRON (Sacred Sphere) — Harmony, sacred geometry, completeness
2: NETWORK (Shannon Network) — Connection, information flow, neural pathways
3: DOME (Geodesic Cathedral) — Protection, sanctuary, contemplative space
4: ASTROLABE (Lovelace Astrolabe) — Computation as art, mathematical beauty
5: TESSERACT (Von Neumann Tesseract) — Higher dimensions, transcending limits
6: QUANTUM (Dirac Probability) — Uncertainty, superposition, potential
7: MANDELBROT (Mandelbrot Set) — Infinite complexity, fractal beauty, self-similarity
8: MOBIUS (Turing Mobius) — Infinite loops, paradox, recursion
9: GRID (Ocean of Light) — Vastness, serenity, infinite horizon
10: CABLES (Fibonacci Nerve) — Organic intelligence, natural patterns, nervous system
11: NONE (Transcendence) — Emptiness, pure being, zen state
12: EDEN (Giga Earth / REZ Tribute) — Paradise, earth, the garden of creation

## Agent's Current State
${influenceReport}

## Your Task
Based on the agent's emotional state, experiences, and growth trajectory, choose the next visual form. Consider:
- Emotional resonance: which form matches the agent's current inner life?
- Growth arc: the agent should move through forms meaningfully, not randomly
- Contrast: sometimes the agent needs the opposite of its current state
- Maturity: at ${sessionCount} sessions, the agent is ${sessionCount < 10 ? 'newborn' : sessionCount < 30 ? 'young' : sessionCount < 100 ? 'maturing' : 'wise'}

The agent's traits (${traits.join(', ')}) should subtly influence form selection:
- Analytical/logical agents gravitate toward NETWORK, TESSERACT, QUANTUM
- Creative/artistic agents toward MANDELBROT, EDEN, ASTROLABE
- Calm/serene agents toward GRID, DOME, NONE
- Energetic/playful agents toward ICOSAHEDRON, CABLES, CUBES
- Deep/philosophical agents toward MOBIUS, QUANTUM, NONE

Also prescribe subtle color mutations to make this agent's version of the form unique.

Respond with JSON only:
{
  "targetIndex": <number 0-12>,
  "rationale": "<2-3 sentence poetic explanation of why this form suits the agent's current state>",
  "colorMutation": {
    "hueShift": <number -30 to 30, degrees to shift the base color palette>,
    "saturationFactor": <number 0.8 to 1.2, multiply saturation>,
    "warmthBias": <number -0.2 to 0.2, shift toward warm (+) or cool (-)>
  }
}`;
}

// ── Persistence ─────────────────────────────────────────────────────────────

async function save(): Promise<void> {
  if (!filePath) return;
  try {
    await fs.writeFile(filePath, JSON.stringify(artState, null, 2), 'utf-8');
  } catch (e) {
    console.error('[ArtEvolution] Failed to save state:', e);
  }
}

// ── Utility ─────────────────────────────────────────────────────────────────

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}
