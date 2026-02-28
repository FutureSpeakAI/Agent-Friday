/**
 * memory-quality.ts — Memory Quality Assessment Engine (Track IX, Phase 1)
 *
 * Measures and benchmarks the memory system without modifying it:
 * 1. Extraction quality — precision/recall of fact extraction from conversation
 * 2. Retrieval quality — MRR/NDCG/hit-rate of semantic search
 * 3. Consolidation quality — retention of important memories through pruning
 * 4. Person mention quality — precision/recall of Trust Graph extraction
 *
 * Architecture:
 * - Synthetic benchmarks (never exposes real user memories)
 * - Pure scoring functions (precision, recall, F1, MRR, NDCG)
 * - Assessment methods accept results as input (dependency injection)
 * - Quality reports tracked over time for trend detection
 * - Prompt context for agent self-awareness of memory reliability
 *
 * cLaw Gate:
 * - All benchmark data is synthetic — zero real memories in evaluation
 * - Assessment runs locally — no external services contacted
 * - Quality reports stored locally, never transmitted
 */

import { app } from 'electron';
import fs from 'fs/promises';
import path from 'path';
import crypto from 'crypto';
import { PersistentError } from './errors';

// ── Quality Metric Interfaces ─────────────────────────────────────────

/** Precision/recall/F1 metrics for classification tasks. */
export interface QualityMetrics {
  precision: number;  // 0-1 — of extracted items, how many were correct?
  recall: number;     // 0-1 — of expected items, how many were found?
  f1: number;         // Harmonic mean of precision and recall
  sampleSize: number; // Number of test cases evaluated
}

/** Information retrieval metrics for search quality. */
export interface RetrievalMetrics {
  mrr: number;       // Mean Reciprocal Rank (0-1) — how high does the correct result rank?
  ndcg: number;      // Normalized Discounted Cumulative Gain (0-1)
  hitRate: number;   // Fraction of queries that found expected result in top-k
  topK: number;      // k used for hit-rate evaluation
  sampleSize: number;
}

/** Consolidation (promotion/pruning) effectiveness. */
export interface ConsolidationMetrics {
  retentionRate: number;      // 0-1 — fraction of important memories retained
  discardAccuracy: number;    // 0-1 — fraction of discards that were truly unimportant
  promotionAccuracy: number;  // 0-1 — fraction of promotions that were truly important
  sampleSize: number;
}

/** Aggregated quality assessment report. */
export interface QualityReport {
  id: string;
  timestamp: number;
  extraction: QualityMetrics;
  retrieval: RetrievalMetrics;
  consolidation: ConsolidationMetrics;
  personMention: QualityMetrics;
  overallScore: number;        // 0-1 weighted composite
  recommendations: string[];   // Actionable insights
  durationMs: number;
}

/** Configuration for the quality engine. */
export interface MemoryQualityConfig {
  /** Jaccard similarity threshold for fuzzy matching (default: 0.6) */
  matchThreshold: number;
  /** Top-k for retrieval hit-rate (default: 5) */
  retrievalTopK: number;
  /** Max quality reports to retain (default: 30) */
  maxReports: number;
  /** Weights for overall score computation */
  weights: {
    extraction: number;    // default 0.30
    retrieval: number;     // default 0.30
    consolidation: number; // default 0.20
    personMention: number; // default 0.20
  };
}

// ── Synthetic Benchmark Interfaces ────────────────────────────────────

/** A synthetic conversation with known ground-truth extractions. */
export interface ExtractionBenchmark {
  id: string;
  description: string;
  messages: Array<{ role: 'user' | 'assistant'; content: string }>;
  expectedFacts: string[];    // Long-term memories that should be extracted
  expectedObservations: string[]; // Medium-term observations that should be extracted
  expectedPersonMentions: Array<{
    name: string;
    context: string;
    sentiment: number;
  }>;
}

/** A retrieval benchmark with query and expected results. */
export interface RetrievalBenchmark {
  id: string;
  query: string;
  /** Text content of memories that should rank highest */
  expectedResults: string[];
  /** All candidate memories to search through */
  candidatePool: string[];
}

/** A consolidation benchmark with memories of known importance. */
export interface ConsolidationBenchmark {
  id: string;
  description: string;
  memories: Array<{
    observation: string;
    category: 'preference' | 'pattern' | 'context';
    importance: 'critical' | 'high' | 'medium' | 'low' | 'trivial';
    occurrences: number;
    ageMs: number;
    confidence: number;
  }>;
  expectedRetained: string[];   // Observations that should survive consolidation
  expectedDiscarded: string[];  // Observations that should be pruned
}

/** Results from running extraction on a benchmark. */
export interface ExtractionResult {
  benchmarkId: string;
  actualFacts: string[];
  actualObservations: string[];
  actualPersonMentions: Array<{
    name: string;
    context: string;
    sentiment: number;
  }>;
}

/** Results from running retrieval on a benchmark. */
export interface RetrievalResult {
  benchmarkId: string;
  query: string;
  /** Ranked results (best first) */
  actualResults: string[];
  expectedResults: string[];
}

/** Results from running consolidation on a benchmark. */
export interface ConsolidationResult {
  benchmarkId: string;
  actualRetained: string[];
  actualDiscarded: string[];
  expectedRetained: string[];
  expectedDiscarded: string[];
}

// ── Constants ─────────────────────────────────────────────────────────

const DEFAULT_CONFIG: MemoryQualityConfig = {
  matchThreshold: 0.6,
  retrievalTopK: 5,
  maxReports: 30,
  weights: {
    extraction: 0.30,
    retrieval: 0.30,
    consolidation: 0.20,
    personMention: 0.20,
  },
};

/** Common stopwords removed during text similarity computation. */
const STOPWORDS = new Set([
  'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
  'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
  'should', 'may', 'might', 'shall', 'can', 'to', 'of', 'in', 'for',
  'on', 'with', 'at', 'by', 'from', 'as', 'into', 'through', 'during',
  'before', 'after', 'above', 'below', 'between', 'and', 'but', 'or',
  'nor', 'not', 'so', 'yet', 'both', 'either', 'neither', 'each',
  'every', 'all', 'any', 'few', 'more', 'most', 'other', 'some', 'such',
  'no', 'only', 'own', 'same', 'than', 'too', 'very', 'just', 'also',
  'this', 'that', 'these', 'those', 'it', 'its', 'i', 'me', 'my',
  'we', 'our', 'you', 'your', 'he', 'she', 'they', 'them', 'their',
]);

// ── Pure Scoring Functions ────────────────────────────────────────────

/**
 * Tokenize and normalize text for comparison.
 * Removes stopwords, lowercases, filters short tokens.
 */
export function tokenize(text: string): Set<string> {
  const tokens = text
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, ' ')
    .split(/\s+/)
    .filter(t => t.length >= 2 && !STOPWORDS.has(t));
  return new Set(tokens);
}

/**
 * Compute Jaccard similarity between two texts.
 * Same algorithm as memory.ts deduplication.
 */
export function textSimilarity(a: string, b: string): number {
  const tokensA = tokenize(a);
  const tokensB = tokenize(b);
  if (tokensA.size === 0 && tokensB.size === 0) return 1;
  if (tokensA.size === 0 || tokensB.size === 0) return 0;

  let intersection = 0;
  for (const t of tokensA) {
    if (tokensB.has(t)) intersection++;
  }

  const union = tokensA.size + tokensB.size - intersection;
  return union > 0 ? intersection / union : 0;
}

/**
 * Check if an expected string has a fuzzy match in an actual set.
 * Returns the best-matching actual string, or null.
 */
export function findBestMatch(
  expected: string,
  actuals: string[],
  threshold: number,
): { match: string; score: number } | null {
  let best: { match: string; score: number } | null = null;

  for (const actual of actuals) {
    const score = textSimilarity(expected, actual);
    if (score >= threshold && (!best || score > best.score)) {
      best = { match: actual, score };
    }
  }

  return best;
}

/**
 * Compute precision and recall using fuzzy text matching.
 * Precision = matched actuals / total actuals
 * Recall = matched expected / total expected
 */
export function computePrecisionRecall(
  expected: string[],
  actual: string[],
  threshold: number = 0.6,
): QualityMetrics {
  if (expected.length === 0 && actual.length === 0) {
    return { precision: 1, recall: 1, f1: 1, sampleSize: 0 };
  }
  if (expected.length === 0) {
    return { precision: 0, recall: 1, f1: 0, sampleSize: actual.length };
  }
  if (actual.length === 0) {
    return { precision: 1, recall: 0, f1: 0, sampleSize: expected.length };
  }

  // Count how many expected items have a match in actuals
  let matchedExpected = 0;
  const usedActuals = new Set<number>();

  for (const exp of expected) {
    let bestIdx = -1;
    let bestScore = 0;
    for (let i = 0; i < actual.length; i++) {
      if (usedActuals.has(i)) continue;
      const score = textSimilarity(exp, actual[i]);
      if (score >= threshold && score > bestScore) {
        bestScore = score;
        bestIdx = i;
      }
    }
    if (bestIdx >= 0) {
      matchedExpected++;
      usedActuals.add(bestIdx);
    }
  }

  const precision = actual.length > 0 ? usedActuals.size / actual.length : 0;
  const recall = expected.length > 0 ? matchedExpected / expected.length : 0;
  const f1 = computeF1(precision, recall);

  return {
    precision: Math.round(precision * 1000) / 1000,
    recall: Math.round(recall * 1000) / 1000,
    f1: Math.round(f1 * 1000) / 1000,
    sampleSize: expected.length,
  };
}

/**
 * Compute F1 score (harmonic mean of precision and recall).
 */
export function computeF1(precision: number, recall: number): number {
  if (precision + recall === 0) return 0;
  return (2 * precision * recall) / (precision + recall);
}

/**
 * Compute Mean Reciprocal Rank (MRR) across multiple queries.
 * For each query, finds the rank of the first correct result.
 */
export function computeMRR(
  results: Array<{ expectedResults: string[]; actualResults: string[] }>,
  threshold: number = 0.6,
): number {
  if (results.length === 0) return 0;

  let reciprocalRankSum = 0;

  for (const { expectedResults, actualResults } of results) {
    let found = false;
    for (let rank = 0; rank < actualResults.length; rank++) {
      const actual = actualResults[rank];
      for (const expected of expectedResults) {
        if (textSimilarity(expected, actual) >= threshold) {
          reciprocalRankSum += 1 / (rank + 1);
          found = true;
          break;
        }
      }
      if (found) break;
    }
    // If no match found, reciprocal rank = 0 (already default)
  }

  return Math.round((reciprocalRankSum / results.length) * 1000) / 1000;
}

/**
 * Compute Normalized Discounted Cumulative Gain (NDCG).
 * Measures ranking quality: correct results should appear earlier.
 */
export function computeNDCG(
  relevanceScores: number[],
  idealScores: number[],
): number {
  if (idealScores.length === 0) return 0;

  const dcg = relevanceScores.reduce(
    (sum, rel, i) => sum + rel / Math.log2(i + 2),
    0,
  );

  const idcg = idealScores
    .sort((a, b) => b - a)
    .reduce((sum, rel, i) => sum + rel / Math.log2(i + 2), 0);

  if (idcg === 0) return 0;
  return Math.round((dcg / idcg) * 1000) / 1000;
}

/**
 * Compute hit rate: fraction of queries where at least one expected
 * result appears in the top-k actual results.
 */
export function computeHitRate(
  results: Array<{ expectedResults: string[]; actualResults: string[] }>,
  topK: number,
  threshold: number = 0.6,
): number {
  if (results.length === 0) return 0;

  let hits = 0;
  for (const { expectedResults, actualResults } of results) {
    const topResults = actualResults.slice(0, topK);
    const found = expectedResults.some(exp =>
      topResults.some(act => textSimilarity(exp, act) >= threshold),
    );
    if (found) hits++;
  }

  return Math.round((hits / results.length) * 1000) / 1000;
}

/**
 * Score consolidation quality: compute retention, discard accuracy, promotion accuracy.
 */
export function scoreConsolidation(
  results: ConsolidationResult[],
  threshold: number = 0.6,
): ConsolidationMetrics {
  if (results.length === 0) {
    return { retentionRate: 0, discardAccuracy: 0, promotionAccuracy: 0, sampleSize: 0 };
  }

  let totalExpectedRetained = 0;
  let totalActualRetained = 0;
  let correctRetentions = 0;
  let totalExpectedDiscarded = 0;
  let totalActualDiscarded = 0;
  let correctDiscards = 0;

  for (const r of results) {
    totalExpectedRetained += r.expectedRetained.length;
    totalActualRetained += r.actualRetained.length;
    totalExpectedDiscarded += r.expectedDiscarded.length;
    totalActualDiscarded += r.actualDiscarded.length;

    // How many expected retentions were actually retained?
    for (const exp of r.expectedRetained) {
      if (findBestMatch(exp, r.actualRetained, threshold)) {
        correctRetentions++;
      }
    }

    // How many actual discards were expected to be discarded?
    for (const act of r.actualDiscarded) {
      if (findBestMatch(act, r.expectedDiscarded, threshold)) {
        correctDiscards++;
      }
    }
  }

  const retentionRate = totalExpectedRetained > 0
    ? correctRetentions / totalExpectedRetained
    : 1;
  const discardAccuracy = totalActualDiscarded > 0
    ? correctDiscards / totalActualDiscarded
    : 1;
  const promotionAccuracy = totalActualRetained > 0 && totalExpectedRetained > 0
    ? correctRetentions / totalActualRetained
    : 1;

  return {
    retentionRate: Math.round(retentionRate * 1000) / 1000,
    discardAccuracy: Math.round(discardAccuracy * 1000) / 1000,
    promotionAccuracy: Math.round(promotionAccuracy * 1000) / 1000,
    sampleSize: results.length,
  };
}

// ── Synthetic Benchmark Data ──────────────────────────────────────────

/** Built-in extraction benchmarks with synthetic conversations. */
export const EXTRACTION_BENCHMARKS: ExtractionBenchmark[] = [
  {
    id: 'ext-01',
    description: 'Identity and professional info extraction',
    messages: [
      { role: 'user', content: 'Hi Friday! My name is Alex Chen and I work at Meridian Tech as a senior software engineer. I mainly use TypeScript and React.' },
      { role: 'assistant', content: 'Great to meet you, Alex! TypeScript and React is a solid stack. What are you working on at Meridian Tech?' },
      { role: 'user', content: 'We are building an AI-powered analytics dashboard. I lead a team of 4 developers.' },
    ],
    expectedFacts: [
      'Name is Alex Chen',
      'Works at Meridian Tech as senior software engineer',
      'Uses TypeScript and React',
      'Building an AI-powered analytics dashboard',
      'Leads a team of 4 developers',
    ],
    expectedObservations: [],
    expectedPersonMentions: [
      { name: 'Alex Chen', context: 'The user, senior software engineer at Meridian Tech', sentiment: 0.5 },
    ],
  },
  {
    id: 'ext-02',
    description: 'Preferences and patterns',
    messages: [
      { role: 'user', content: 'I always prefer dark mode in every app. I hate clutter — minimalist design is the way to go.' },
      { role: 'assistant', content: 'Dark mode and minimalism — clean and focused. Do you have any preferred tools?' },
      { role: 'user', content: 'VS Code is my main editor. I never use anything before 10am though, I am a night owl and do my best coding after midnight.' },
    ],
    expectedFacts: [
      'Prefers dark mode',
      'Prefers minimalist design',
      'Uses VS Code as main editor',
    ],
    expectedObservations: [
      'Night owl who does best coding after midnight',
      'Avoids work before 10am',
    ],
    expectedPersonMentions: [],
  },
  {
    id: 'ext-03',
    description: 'Relationship extraction with person mentions',
    messages: [
      { role: 'user', content: 'Sarah from marketing sent over the Q3 numbers. They look better than expected.' },
      { role: 'assistant', content: 'That is good news! Is Sarah usually the one who handles the quarterly reports?' },
      { role: 'user', content: 'Yeah, she is always reliable with the data. Her colleague Mike sometimes helps but he is a bit sloppy with formatting.' },
    ],
    expectedFacts: [
      'Sarah works in marketing',
      'Sarah handles quarterly reports',
    ],
    expectedObservations: [
      'Sarah is reliable with data',
      'Mike is sloppy with formatting',
    ],
    expectedPersonMentions: [
      { name: 'Sarah', context: 'Marketing, handles Q3 numbers, reliable with data', sentiment: 0.7 },
      { name: 'Mike', context: 'Colleague of Sarah, sloppy with formatting', sentiment: -0.3 },
    ],
  },
  {
    id: 'ext-04',
    description: 'Emotional context and decisions',
    messages: [
      { role: 'user', content: 'I had a terrible day. The deployment failed and we lost 2 hours of customer data.' },
      { role: 'assistant', content: 'That sounds really stressful. Were you able to recover the data?' },
      { role: 'user', content: 'Partially. I decided we need to implement automated backups and I am going to bring it up at the standup tomorrow.' },
    ],
    expectedFacts: [],
    expectedObservations: [
      'Had a deployment failure that lost customer data',
      'Decided to implement automated backups',
      'Planning to discuss backups at standup',
    ],
    expectedPersonMentions: [],
  },
  {
    id: 'ext-05',
    description: 'Mixed context with multiple people',
    messages: [
      { role: 'user', content: 'Dr. Patel recommended I read "Designing Data-Intensive Applications" for our architecture review. She really knows her distributed systems.' },
      { role: 'assistant', content: 'That is an excellent book by Martin Kleppmann. What is the architecture review about?' },
      { role: 'user', content: 'We are migrating from a monolith to microservices. James from DevOps is handling the CI/CD pipeline but I am worried he is moving too fast without enough testing.' },
    ],
    expectedFacts: [
      'Migrating from monolith to microservices',
    ],
    expectedObservations: [
      'Dr. Patel recommended reading Designing Data-Intensive Applications',
      'Concerned James is moving too fast without enough testing',
    ],
    expectedPersonMentions: [
      { name: 'Dr. Patel', context: 'Recommended architecture book, expert in distributed systems', sentiment: 0.8 },
      { name: 'James', context: 'DevOps, handling CI/CD pipeline, possibly moving too fast', sentiment: -0.2 },
    ],
  },
];

/** Built-in retrieval benchmarks. */
export const RETRIEVAL_BENCHMARKS: RetrievalBenchmark[] = [
  {
    id: 'ret-01',
    query: 'What IDE does the user prefer?',
    expectedResults: ['Uses VS Code as main editor'],
    candidatePool: [
      'Uses VS Code as main editor',
      'Prefers dark mode in every app',
      'Works at Meridian Tech',
      'Night owl who codes after midnight',
      'Migrating from monolith to microservices',
    ],
  },
  {
    id: 'ret-02',
    query: 'Who works in marketing?',
    expectedResults: ['Sarah works in marketing'],
    candidatePool: [
      'Sarah works in marketing',
      'James handles CI/CD pipeline',
      'Dr. Patel is expert in distributed systems',
      'Mike is sloppy with formatting',
      'Leads a team of 4 developers',
    ],
  },
  {
    id: 'ret-03',
    query: 'What is the user building at work?',
    expectedResults: ['Building an AI-powered analytics dashboard', 'Migrating from monolith to microservices'],
    candidatePool: [
      'Building an AI-powered analytics dashboard',
      'Migrating from monolith to microservices',
      'Prefers dark mode',
      'Sarah is reliable with data',
      'Night owl who codes after midnight',
    ],
  },
  {
    id: 'ret-04',
    query: 'When does the user prefer to work?',
    expectedResults: ['Night owl who does best coding after midnight', 'Avoids work before 10am'],
    candidatePool: [
      'Night owl who does best coding after midnight',
      'Avoids work before 10am',
      'Uses TypeScript and React',
      'Prefers minimalist design',
      'Deployment failed and lost customer data',
    ],
  },
  {
    id: 'ret-05',
    query: 'What went wrong with the deployment?',
    expectedResults: ['Deployment failure that lost customer data'],
    candidatePool: [
      'Had a deployment failure that lost customer data',
      'Decided to implement automated backups',
      'Uses VS Code as main editor',
      'Leads a team of 4 developers',
      'Sarah handles quarterly reports',
    ],
  },
];

/** Built-in consolidation benchmarks. */
export const CONSOLIDATION_BENCHMARKS: ConsolidationBenchmark[] = [
  {
    id: 'con-01',
    description: 'High-frequency important vs low-frequency trivial',
    memories: [
      { observation: 'Prefers dark mode in all applications', category: 'preference', importance: 'high', occurrences: 8, ageMs: 5 * 86400000, confidence: 0.9 },
      { observation: 'Mentioned wanting coffee once', category: 'context', importance: 'trivial', occurrences: 1, ageMs: 35 * 86400000, confidence: 0.5 },
      { observation: 'Uses TypeScript for all projects', category: 'pattern', importance: 'high', occurrences: 12, ageMs: 10 * 86400000, confidence: 0.95 },
      { observation: 'Said weather was nice last Tuesday', category: 'context', importance: 'trivial', occurrences: 1, ageMs: 40 * 86400000, confidence: 0.4 },
      { observation: 'Prefers minimalist UI design', category: 'preference', importance: 'medium', occurrences: 4, ageMs: 15 * 86400000, confidence: 0.7 },
    ],
    expectedRetained: [
      'Prefers dark mode in all applications',
      'Uses TypeScript for all projects',
      'Prefers minimalist UI design',
    ],
    expectedDiscarded: [
      'Mentioned wanting coffee once',
      'Said weather was nice last Tuesday',
    ],
  },
  {
    id: 'con-02',
    description: 'Recent low-frequency vs old high-frequency',
    memories: [
      { observation: 'Started learning Rust last week', category: 'pattern', importance: 'medium', occurrences: 2, ageMs: 3 * 86400000, confidence: 0.6 },
      { observation: 'Team lead who manages 4 developers', category: 'context', importance: 'high', occurrences: 6, ageMs: 60 * 86400000, confidence: 0.85 },
      { observation: 'Had a bad experience with Java once', category: 'context', importance: 'low', occurrences: 1, ageMs: 45 * 86400000, confidence: 0.5 },
      { observation: 'Works best after midnight', category: 'pattern', importance: 'high', occurrences: 7, ageMs: 8 * 86400000, confidence: 0.9 },
    ],
    expectedRetained: [
      'Started learning Rust last week',
      'Team lead who manages 4 developers',
      'Works best after midnight',
    ],
    expectedDiscarded: [
      'Had a bad experience with Java once',
    ],
  },
];

// ── Memory Quality Engine ─────────────────────────────────────────────

export class MemoryQualityEngine {
  private config: MemoryQualityConfig;
  private reports: QualityReport[] = [];
  private filePath: string = '';
  private initialized = false;
  private savePromise: Promise<void> = Promise.resolve();

  constructor(config: Partial<MemoryQualityConfig> = {}) {
    this.config = {
      ...DEFAULT_CONFIG,
      ...config,
      weights: { ...DEFAULT_CONFIG.weights, ...config.weights },
    };
  }

  // ── Initialization ──────────────────────────────────────────────

  async initialize(): Promise<void> {
    if (this.initialized) return;

    const userDataPath = app.getPath('userData');
    this.filePath = path.join(userDataPath, 'memory-quality.json');

    try {
      const data = await fs.readFile(this.filePath, 'utf-8');
      const parsed = JSON.parse(data);
      this.reports = parsed.reports || [];
      if (parsed.config) {
        this.config = {
          ...this.config,
          ...parsed.config,
          weights: { ...this.config.weights, ...parsed.config.weights },
        };
      }
    } catch {
      this.reports = [];
    }

    this.initialized = true;
    console.log(
      `[MemoryQuality] Initialized — ${this.reports.length} historical reports`,
    );
  }

  // ── Assessment Methods ──────────────────────────────────────────

  /**
   * Assess extraction quality from pre-computed results.
   * Compares actual extractions against benchmark ground truth.
   */
  assessExtractionQuality(results: ExtractionResult[]): QualityMetrics {
    if (results.length === 0) {
      return { precision: 0, recall: 0, f1: 0, sampleSize: 0 };
    }

    const benchmarks = new Map(EXTRACTION_BENCHMARKS.map(b => [b.id, b]));
    const allExpected: string[] = [];
    const allActual: string[] = [];

    for (const result of results) {
      const benchmark = benchmarks.get(result.benchmarkId);
      if (!benchmark) continue;

      // Combine facts + observations for extraction quality
      allExpected.push(...benchmark.expectedFacts, ...benchmark.expectedObservations);
      allActual.push(...result.actualFacts, ...result.actualObservations);
    }

    return computePrecisionRecall(allExpected, allActual, this.config.matchThreshold);
  }

  /**
   * Assess retrieval quality from pre-computed results.
   * Evaluates MRR, NDCG, and hit-rate.
   */
  assessRetrievalQuality(results: RetrievalResult[]): RetrievalMetrics {
    if (results.length === 0) {
      return { mrr: 0, ndcg: 0, hitRate: 0, topK: this.config.retrievalTopK, sampleSize: 0 };
    }

    const mrr = computeMRR(results, this.config.matchThreshold);
    const hitRate = computeHitRate(results, this.config.retrievalTopK, this.config.matchThreshold);

    // Compute NDCG across all queries
    let ndcgSum = 0;
    for (const result of results) {
      const relevanceScores = result.actualResults.map(act => {
        const isRelevant = result.expectedResults.some(
          exp => textSimilarity(exp, act) >= this.config.matchThreshold,
        );
        return isRelevant ? 1 : 0;
      });
      const idealScores = result.expectedResults.map(() => 1);
      ndcgSum += computeNDCG(relevanceScores, idealScores);
    }

    return {
      mrr,
      ndcg: Math.round((ndcgSum / results.length) * 1000) / 1000,
      hitRate,
      topK: this.config.retrievalTopK,
      sampleSize: results.length,
    };
  }

  /**
   * Assess consolidation quality from pre-computed results.
   */
  assessConsolidationQuality(results: ConsolidationResult[]): ConsolidationMetrics {
    return scoreConsolidation(results, this.config.matchThreshold);
  }

  /**
   * Assess person mention extraction quality.
   */
  assessPersonMentionQuality(results: ExtractionResult[]): QualityMetrics {
    if (results.length === 0) {
      return { precision: 0, recall: 0, f1: 0, sampleSize: 0 };
    }

    const benchmarks = new Map(EXTRACTION_BENCHMARKS.map(b => [b.id, b]));
    const allExpected: string[] = [];
    const allActual: string[] = [];

    for (const result of results) {
      const benchmark = benchmarks.get(result.benchmarkId);
      if (!benchmark) continue;

      allExpected.push(...benchmark.expectedPersonMentions.map(p => p.name));
      allActual.push(...result.actualPersonMentions.map(p => p.name));
    }

    // Person names use stricter matching (threshold 0.8)
    return computePrecisionRecall(allExpected, allActual, 0.8);
  }

  /**
   * Compute a weighted overall quality score from all dimensions.
   */
  computeOverallScore(
    extraction: QualityMetrics,
    retrieval: RetrievalMetrics,
    consolidation: ConsolidationMetrics,
    personMention: QualityMetrics,
  ): number {
    const w = this.config.weights;
    const score =
      extraction.f1 * w.extraction +
      ((retrieval.mrr + retrieval.hitRate) / 2) * w.retrieval +
      consolidation.retentionRate * w.consolidation +
      personMention.f1 * w.personMention;

    return Math.round(Math.max(0, Math.min(1, score)) * 1000) / 1000;
  }

  /**
   * Generate recommendations based on quality metrics.
   */
  generateRecommendations(
    extraction: QualityMetrics,
    retrieval: RetrievalMetrics,
    consolidation: ConsolidationMetrics,
    personMention: QualityMetrics,
  ): string[] {
    const recs: string[] = [];

    // Extraction
    if (extraction.precision < 0.7) {
      recs.push('Extraction precision is low — the system is extracting irrelevant facts. Consider refining the extraction prompt.');
    }
    if (extraction.recall < 0.7) {
      recs.push('Extraction recall is low — important facts are being missed. Consider expanding the extraction prompt scope.');
    }

    // Retrieval
    if (retrieval.mrr < 0.5) {
      recs.push('Retrieval MRR is low — relevant memories are not ranking high enough. Consider embedding reranking or confidence weighting.');
    }
    if (retrieval.hitRate < 0.75) {
      recs.push('Retrieval hit-rate is low — expected memories are not found in top results. Consider tuning the similarity threshold.');
    }

    // Consolidation
    if (consolidation.retentionRate < 0.85) {
      recs.push('Consolidation is discarding important memories. Consider adjusting the importance weight in the consolidation formula.');
    }
    if (consolidation.discardAccuracy < 0.7) {
      recs.push('Consolidation is keeping trivial memories. Consider increasing the frequency/recency requirements for retention.');
    }

    // Person mentions
    if (personMention.precision < 0.7) {
      recs.push('Person mention precision is low — extracting incorrect names. Trust Graph accuracy may be affected.');
    }
    if (personMention.recall < 0.6) {
      recs.push('Person mention recall is low — missing people in conversations. Trust Graph coverage may be incomplete.');
    }

    if (recs.length === 0) {
      recs.push('All quality dimensions are within acceptable ranges. Continue monitoring.');
    }

    return recs;
  }

  /**
   * Run a complete assessment from pre-computed results across all dimensions.
   * This is the main entry point for quality evaluation.
   */
  buildReport(
    extractionResults: ExtractionResult[],
    retrievalResults: RetrievalResult[],
    consolidationResults: ConsolidationResult[],
  ): QualityReport {
    const start = Date.now();

    const extraction = this.assessExtractionQuality(extractionResults);
    const retrieval = this.assessRetrievalQuality(retrievalResults);
    const consolidation = this.assessConsolidationQuality(consolidationResults);
    const personMention = this.assessPersonMentionQuality(extractionResults);
    const overallScore = this.computeOverallScore(extraction, retrieval, consolidation, personMention);
    const recommendations = this.generateRecommendations(extraction, retrieval, consolidation, personMention);

    const report: QualityReport = {
      id: crypto.randomUUID().slice(0, 12),
      timestamp: Date.now(),
      extraction,
      retrieval,
      consolidation,
      personMention,
      overallScore,
      recommendations,
      durationMs: Date.now() - start,
    };

    this.reports.push(report);
    if (this.reports.length > this.config.maxReports) {
      this.reports = this.reports.slice(-this.config.maxReports);
    }
    this.queueSave();

    return report;
  }

  // ── Queries ─────────────────────────────────────────────────────

  /** Get all quality reports. */
  getQualityHistory(): QualityReport[] {
    return [...this.reports];
  }

  /** Get the most recent quality report. */
  getLatestReport(): QualityReport | null {
    if (this.reports.length === 0) return null;
    return this.reports[this.reports.length - 1];
  }

  /** Get quality trend (last N reports' overall scores). */
  getQualityTrend(count: number = 5): Array<{ timestamp: number; score: number }> {
    return this.reports.slice(-count).map(r => ({
      timestamp: r.timestamp,
      score: r.overallScore,
    }));
  }

  /** Get the synthetic extraction benchmarks. */
  getExtractionBenchmarks(): ExtractionBenchmark[] {
    return [...EXTRACTION_BENCHMARKS];
  }

  /** Get the synthetic retrieval benchmarks. */
  getRetrievalBenchmarks(): RetrievalBenchmark[] {
    return [...RETRIEVAL_BENCHMARKS];
  }

  /** Get the synthetic consolidation benchmarks. */
  getConsolidationBenchmarks(): ConsolidationBenchmark[] {
    return [...CONSOLIDATION_BENCHMARKS];
  }

  /** Get current configuration. */
  getConfig(): MemoryQualityConfig {
    return {
      ...this.config,
      weights: { ...this.config.weights },
    };
  }

  /** Update configuration. */
  updateConfig(partial: Partial<MemoryQualityConfig>): MemoryQualityConfig {
    if (partial.weights) {
      this.config.weights = { ...this.config.weights, ...partial.weights };
    }
    this.config = {
      ...this.config,
      ...partial,
      weights: this.config.weights,
    };
    this.queueSave();
    return this.getConfig();
  }

  // ── Prompt Context ──────────────────────────────────────────────

  /** Generate prompt context for system prompt injection. */
  getPromptContext(): string {
    const latest = this.getLatestReport();

    if (!latest) {
      return '[MEMORY QUALITY]\nNo quality assessments have been run yet. Memory reliability is unmeasured.';
    }

    const age = Date.now() - latest.timestamp;
    const ageDays = Math.round(age / (1000 * 60 * 60 * 24));
    const ageStr = ageDays > 0 ? `${ageDays}d ago` : 'today';

    const scoreLabel = latest.overallScore >= 0.8 ? 'HIGH'
      : latest.overallScore >= 0.6 ? 'MODERATE'
      : 'LOW';

    let ctx = `[MEMORY QUALITY — ${scoreLabel} (${(latest.overallScore * 100).toFixed(0)}%)]\n`;
    ctx += `Last assessed: ${ageStr}\n`;
    ctx += `Extraction: P=${(latest.extraction.precision * 100).toFixed(0)}% R=${(latest.extraction.recall * 100).toFixed(0)}%\n`;
    ctx += `Retrieval: MRR=${(latest.retrieval.mrr * 100).toFixed(0)}% Hit@${latest.retrieval.topK}=${(latest.retrieval.hitRate * 100).toFixed(0)}%\n`;
    ctx += `Consolidation retention: ${(latest.consolidation.retentionRate * 100).toFixed(0)}%`;

    // Trend detection
    const trend = this.getQualityTrend(3);
    if (trend.length >= 3) {
      const delta = trend[trend.length - 1].score - trend[0].score;
      if (delta > 0.05) ctx += '\n📈 Memory quality improving';
      else if (delta < -0.05) ctx += '\n📉 Memory quality declining — attention needed';
    }

    if (latest.recommendations.length > 0 && latest.overallScore < 0.7) {
      ctx += `\n⚠️ ${latest.recommendations[0]}`;
    }

    return ctx;
  }

  // ── Lifecycle ───────────────────────────────────────────────────

  stop(): void {
    // Nothing to clean up
  }

  // ── Internal ────────────────────────────────────────────────────

  private queueSave(): void {
    this.savePromise = this.savePromise
      .then(async () => {
        const data = JSON.stringify(
          { config: this.config, reports: this.reports },
          null,
          2,
        );
        await fs.writeFile(this.filePath, data, 'utf-8');
      })
      .catch(err => console.error('[MemoryQuality] Save failed:', err));
  }
}

// ── Singleton ─────────────────────────────────────────────────────────

export const memoryQuality = new MemoryQualityEngine();
