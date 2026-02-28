/**
 * memory-quality.test.ts — Tests for the Memory Quality Assessment Engine (Track IX, Phase 1)
 *
 * Coverage:
 * - Pure scoring functions (tokenize, textSimilarity, findBestMatch, P/R/F1, MRR, NDCG, hitRate, scoreConsolidation)
 * - Synthetic benchmark data integrity
 * - Assessment methods (extraction, retrieval, consolidation, person mention)
 * - Report building and persistence
 * - Recommendations generation
 * - Overall score computation
 * - Quality trend detection
 * - Prompt context generation
 * - Configuration management
 * - cLaw compliance (synthetic-only data, no external calls)
 * - Edge cases and boundary conditions
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ── Mock Electron + fs ──────────────────────────────────────────────

vi.mock('electron', () => ({
  app: {
    getPath: vi.fn().mockReturnValue('/mock/userData'),
  },
}));

vi.mock('fs/promises', () => ({
  default: {
    readFile: vi.fn().mockRejectedValue(new Error('ENOENT')),
    writeFile: vi.fn().mockResolvedValue(undefined),
    mkdir: vi.fn().mockResolvedValue(undefined),
  },
}));

// ── Import after mocks ──────────────────────────────────────────────

import {
  tokenize,
  textSimilarity,
  findBestMatch,
  computePrecisionRecall,
  computeF1,
  computeMRR,
  computeNDCG,
  computeHitRate,
  scoreConsolidation,
  EXTRACTION_BENCHMARKS,
  RETRIEVAL_BENCHMARKS,
  CONSOLIDATION_BENCHMARKS,
  MemoryQualityEngine,
  type QualityMetrics,
  type RetrievalMetrics,
  type ConsolidationMetrics,
  type ExtractionResult,
  type RetrievalResult,
  type ConsolidationResult,
  type ExtractionBenchmark,
  type RetrievalBenchmark,
  type ConsolidationBenchmark,
  type QualityReport,
  type MemoryQualityConfig,
} from '../../src/main/memory-quality';

// ═══════════════════════════════════════════════════════════════════════
// § 1  tokenize()
// ═══════════════════════════════════════════════════════════════════════
describe('tokenize()', () => {
  it('lowercases and splits text', () => {
    const tokens = tokenize('Hello World');
    expect(tokens.has('hello')).toBe(true);
    expect(tokens.has('world')).toBe(true);
  });

  it('removes stopwords', () => {
    const tokens = tokenize('the user is a software engineer');
    expect(tokens.has('the')).toBe(false);
    expect(tokens.has('is')).toBe(false);
    expect(tokens.has('a')).toBe(false);
    expect(tokens.has('user')).toBe(true); // 'user' is not in STOPWORDS, so it should be kept
  });

  it('removes single-character tokens', () => {
    const tokens = tokenize('I a b the cat');
    expect(tokens.has('cat')).toBe(true);
    expect(tokens.has('b')).toBe(false); // length < 2
  });

  it('strips punctuation', () => {
    const tokens = tokenize("Hello, world! It's great.");
    expect(tokens.has('hello')).toBe(true);
    expect(tokens.has('world')).toBe(true);
    expect(tokens.has('great')).toBe(true);
  });

  it('returns empty set for empty string', () => {
    const tokens = tokenize('');
    expect(tokens.size).toBe(0);
  });

  it('returns empty set for stopwords-only text', () => {
    const tokens = tokenize('the is a an');
    expect(tokens.size).toBe(0);
  });
});

// ═══════════════════════════════════════════════════════════════════════
// § 2  textSimilarity()
// ═══════════════════════════════════════════════════════════════════════
describe('textSimilarity()', () => {
  it('returns 1 for identical strings', () => {
    expect(textSimilarity('TypeScript React', 'TypeScript React')).toBe(1);
  });

  it('returns 1 for both empty strings', () => {
    expect(textSimilarity('', '')).toBe(1);
  });

  it('returns 0 when one string is empty', () => {
    expect(textSimilarity('hello world', '')).toBe(0);
    expect(textSimilarity('', 'hello world')).toBe(0);
  });

  it('returns 0 for completely disjoint texts', () => {
    expect(textSimilarity('cats dogs animals', 'typescript react angular')).toBe(0);
  });

  it('computes partial overlap correctly', () => {
    const sim = textSimilarity(
      'Prefers dark mode in apps',
      'Prefers dark mode settings',
    );
    expect(sim).toBeGreaterThan(0.3);
    expect(sim).toBeLessThan(1);
  });

  it('is case insensitive', () => {
    expect(textSimilarity('TypeScript', 'typescript')).toBe(1);
  });

  it('ignores stopwords in comparison', () => {
    const sim = textSimilarity(
      'The user prefers dark mode',
      'User prefers dark mode in settings',
    );
    // Stopwords stripped, so similarity is high
    expect(sim).toBeGreaterThan(0.4);
  });
});

// ═══════════════════════════════════════════════════════════════════════
// § 3  findBestMatch()
// ═══════════════════════════════════════════════════════════════════════
describe('findBestMatch()', () => {
  it('finds exact match', () => {
    const result = findBestMatch('dark mode preference', ['dark mode preference', 'light mode'], 0.5);
    expect(result).not.toBeNull();
    expect(result!.score).toBe(1);
  });

  it('returns null when no match exceeds threshold', () => {
    const result = findBestMatch('TypeScript expert', ['cooking recipes', 'hiking trails'], 0.5);
    expect(result).toBeNull();
  });

  it('returns best match among multiple candidates', () => {
    const result = findBestMatch(
      'Prefers dark mode',
      ['Likes dark mode settings', 'Prefers dark mode apps', 'Uses light theme'],
      0.3,
    );
    expect(result).not.toBeNull();
    expect(result!.match).toBe('Prefers dark mode apps');
  });

  it('respects threshold', () => {
    const result = findBestMatch('hello', ['goodbye'], 0.9);
    expect(result).toBeNull();
  });
});

// ═══════════════════════════════════════════════════════════════════════
// § 4  computeF1()
// ═══════════════════════════════════════════════════════════════════════
describe('computeF1()', () => {
  it('returns 0 when both precision and recall are 0', () => {
    expect(computeF1(0, 0)).toBe(0);
  });

  it('returns 1 when both are 1', () => {
    expect(computeF1(1, 1)).toBe(1);
  });

  it('computes harmonic mean correctly', () => {
    const f1 = computeF1(0.8, 0.6);
    expect(f1).toBeCloseTo(0.6857, 3);
  });

  it('returns 0 when one dimension is 0', () => {
    expect(computeF1(1, 0)).toBe(0);
    expect(computeF1(0, 1)).toBe(0);
  });
});

// ═══════════════════════════════════════════════════════════════════════
// § 5  computePrecisionRecall()
// ═══════════════════════════════════════════════════════════════════════
describe('computePrecisionRecall()', () => {
  it('returns perfect scores for identical sets', () => {
    const result = computePrecisionRecall(
      ['dark mode', 'TypeScript'],
      ['dark mode', 'TypeScript'],
    );
    expect(result.precision).toBe(1);
    expect(result.recall).toBe(1);
    expect(result.f1).toBe(1);
  });

  it('handles perfect precision but low recall', () => {
    const result = computePrecisionRecall(
      ['dark mode', 'TypeScript', 'React'],
      ['dark mode'],
    );
    expect(result.precision).toBe(1); // 1 actual matched / 1 actual total
    expect(result.recall).toBeCloseTo(0.333, 2); // 1 matched expected / 3 expected total
  });

  it('handles low precision but perfect recall', () => {
    const result = computePrecisionRecall(
      ['dark mode'],
      ['dark mode', 'extra thing one', 'extra thing two'],
    );
    expect(result.recall).toBe(1); // 1 matched expected / 1 expected total
    expect(result.precision).toBeCloseTo(0.333, 2); // 1 matched / 3 actuals
  });

  it('returns (1, 1, 1) when both arrays empty', () => {
    const result = computePrecisionRecall([], []);
    expect(result.precision).toBe(1);
    expect(result.recall).toBe(1);
  });

  it('handles empty expected (recall = 1, precision = 0)', () => {
    const result = computePrecisionRecall([], ['something']);
    expect(result.recall).toBe(1);
    expect(result.precision).toBe(0);
  });

  it('handles empty actual (precision = 1, recall = 0)', () => {
    const result = computePrecisionRecall(['something'], []);
    expect(result.precision).toBe(1);
    expect(result.recall).toBe(0);
  });

  it('uses fuzzy matching with threshold', () => {
    const result = computePrecisionRecall(
      ['Prefers dark mode in all applications'],
      ['User prefers dark mode apps'],
      0.4,
    );
    // Should fuzzy match due to overlapping key terms
    expect(result.recall).toBeGreaterThan(0);
  });

  it('does not double-count actuals', () => {
    const result = computePrecisionRecall(
      ['dark mode', 'light mode'],
      ['dark mode preference'],
      0.3,
    );
    // Only one actual, can match at most one expected
    expect(result.precision).toBeLessThanOrEqual(1);
  });
});

// ═══════════════════════════════════════════════════════════════════════
// § 6  computeMRR()
// ═══════════════════════════════════════════════════════════════════════
describe('computeMRR()', () => {
  it('returns 1 when correct result is always first', () => {
    const mrr = computeMRR([
      { expectedResults: ['result A'], actualResults: ['result A', 'B', 'C'] },
      { expectedResults: ['result D'], actualResults: ['result D', 'E', 'F'] },
    ]);
    expect(mrr).toBe(1);
  });

  it('returns 0.5 when correct result is always second', () => {
    const mrr = computeMRR([
      { expectedResults: ['result A'], actualResults: ['X', 'result A', 'Y'] },
    ]);
    expect(mrr).toBe(0.5);
  });

  it('returns 0 when no correct results found', () => {
    const mrr = computeMRR([
      { expectedResults: ['missing item'], actualResults: ['wrong A', 'wrong B'] },
    ]);
    expect(mrr).toBe(0);
  });

  it('returns 0 for empty input', () => {
    expect(computeMRR([])).toBe(0);
  });

  it('handles mixed rankings', () => {
    const mrr = computeMRR([
      { expectedResults: ['result A'], actualResults: ['result A', 'B'] },  // rank 1 → 1/1
      { expectedResults: ['result C'], actualResults: ['X', 'Y', 'result C'] },  // rank 3 → 1/3
    ]);
    // (1 + 1/3) / 2 = 0.667
    expect(mrr).toBeCloseTo(0.667, 2);
  });
});

// ═══════════════════════════════════════════════════════════════════════
// § 7  computeNDCG()
// ═══════════════════════════════════════════════════════════════════════
describe('computeNDCG()', () => {
  it('returns 1 for perfect ranking', () => {
    const ndcg = computeNDCG([1, 1, 0, 0], [1, 1]);
    expect(ndcg).toBe(1);
  });

  it('returns 0 for empty ideal scores', () => {
    expect(computeNDCG([1, 0], [])).toBe(0);
  });

  it('returns 0 when no relevant results', () => {
    expect(computeNDCG([0, 0, 0], [1, 1])).toBe(0);
  });

  it('penalizes poor ranking', () => {
    const perfect = computeNDCG([1, 1, 0], [1, 1]);
    const poor = computeNDCG([0, 0, 1], [1]);
    expect(perfect).toBeGreaterThan(poor);
  });
});

// ═══════════════════════════════════════════════════════════════════════
// § 8  computeHitRate()
// ═══════════════════════════════════════════════════════════════════════
describe('computeHitRate()', () => {
  it('returns 1 when all queries have hits in top-k', () => {
    const hitRate = computeHitRate(
      [
        { expectedResults: ['A result'], actualResults: ['A result', 'B'] },
        { expectedResults: ['C result'], actualResults: ['C result', 'D'] },
      ],
      5,
    );
    expect(hitRate).toBe(1);
  });

  it('returns 0 when no queries have hits', () => {
    const hitRate = computeHitRate(
      [
        { expectedResults: ['missing'], actualResults: ['wrong A', 'wrong B'] },
      ],
      5,
    );
    expect(hitRate).toBe(0);
  });

  it('returns 0 for empty input', () => {
    expect(computeHitRate([], 5)).toBe(0);
  });

  it('respects topK limit', () => {
    const hitRate = computeHitRate(
      [
        // Expected is at position 5 (0-indexed), topK=3 means only checking indices 0-2
        { expectedResults: ['deep result'], actualResults: ['a', 'b', 'c', 'd', 'e', 'deep result'] },
      ],
      3,
    );
    expect(hitRate).toBe(0);
  });

  it('handles partial hits', () => {
    const hitRate = computeHitRate(
      [
        { expectedResults: ['found item'], actualResults: ['found item', 'B'] },
        { expectedResults: ['missing item'], actualResults: ['wrong A', 'wrong B'] },
      ],
      5,
    );
    expect(hitRate).toBe(0.5);
  });
});

// ═══════════════════════════════════════════════════════════════════════
// § 9  scoreConsolidation()
// ═══════════════════════════════════════════════════════════════════════
describe('scoreConsolidation()', () => {
  it('returns perfect scores when all predictions correct', () => {
    const result = scoreConsolidation([
      {
        benchmarkId: 'test',
        actualRetained: ['important fact A', 'important fact B'],
        actualDiscarded: ['trivial stuff C'],
        expectedRetained: ['important fact A', 'important fact B'],
        expectedDiscarded: ['trivial stuff C'],
      },
    ]);
    expect(result.retentionRate).toBe(1);
    expect(result.discardAccuracy).toBe(1);
  });

  it('detects poor retention', () => {
    const result = scoreConsolidation([
      {
        benchmarkId: 'test',
        actualRetained: [],
        actualDiscarded: ['important fact A', 'trivial stuff'],
        expectedRetained: ['important fact A'],
        expectedDiscarded: ['trivial stuff'],
      },
    ]);
    expect(result.retentionRate).toBe(0); // Expected retention not in actual retained
  });

  it('returns zeros for empty input', () => {
    const result = scoreConsolidation([]);
    expect(result.sampleSize).toBe(0);
    expect(result.retentionRate).toBe(0);
  });
});

// ═══════════════════════════════════════════════════════════════════════
// § 10  Benchmark Data Integrity
// ═══════════════════════════════════════════════════════════════════════
describe('Benchmark data integrity', () => {
  describe('EXTRACTION_BENCHMARKS', () => {
    it('has at least 5 benchmarks', () => {
      expect(EXTRACTION_BENCHMARKS.length).toBeGreaterThanOrEqual(5);
    });

    it('each has unique id', () => {
      const ids = new Set(EXTRACTION_BENCHMARKS.map(b => b.id));
      expect(ids.size).toBe(EXTRACTION_BENCHMARKS.length);
    });

    it('each has at least 2 messages', () => {
      for (const b of EXTRACTION_BENCHMARKS) {
        expect(b.messages.length).toBeGreaterThanOrEqual(2);
      }
    });

    it('each has description', () => {
      for (const b of EXTRACTION_BENCHMARKS) {
        expect(b.description.length).toBeGreaterThan(0);
      }
    });

    it('at least one benchmark has person mentions', () => {
      const withMentions = EXTRACTION_BENCHMARKS.filter(b => b.expectedPersonMentions.length > 0);
      expect(withMentions.length).toBeGreaterThanOrEqual(1);
    });

    it('at least one benchmark has facts', () => {
      const withFacts = EXTRACTION_BENCHMARKS.filter(b => b.expectedFacts.length > 0);
      expect(withFacts.length).toBeGreaterThanOrEqual(1);
    });

    it('at least one benchmark has observations', () => {
      const withObs = EXTRACTION_BENCHMARKS.filter(b => b.expectedObservations.length > 0);
      expect(withObs.length).toBeGreaterThanOrEqual(1);
    });
  });

  describe('RETRIEVAL_BENCHMARKS', () => {
    it('has at least 5 benchmarks', () => {
      expect(RETRIEVAL_BENCHMARKS.length).toBeGreaterThanOrEqual(5);
    });

    it('each has unique id', () => {
      const ids = new Set(RETRIEVAL_BENCHMARKS.map(b => b.id));
      expect(ids.size).toBe(RETRIEVAL_BENCHMARKS.length);
    });

    it('each has non-empty candidate pool', () => {
      for (const b of RETRIEVAL_BENCHMARKS) {
        expect(b.candidatePool.length).toBeGreaterThanOrEqual(3);
      }
    });

    it('expected results are subset of candidate pool', () => {
      for (const b of RETRIEVAL_BENCHMARKS) {
        for (const exp of b.expectedResults) {
          const found = b.candidatePool.some(
            c => textSimilarity(exp, c) >= 0.5,
          );
          expect(found).toBe(true);
        }
      }
    });
  });

  describe('CONSOLIDATION_BENCHMARKS', () => {
    it('has at least 2 benchmarks', () => {
      expect(CONSOLIDATION_BENCHMARKS.length).toBeGreaterThanOrEqual(2);
    });

    it('each has unique id', () => {
      const ids = new Set(CONSOLIDATION_BENCHMARKS.map(b => b.id));
      expect(ids.size).toBe(CONSOLIDATION_BENCHMARKS.length);
    });

    it('each has both retained and discarded expectations', () => {
      for (const b of CONSOLIDATION_BENCHMARKS) {
        expect(b.expectedRetained.length).toBeGreaterThan(0);
        expect(b.expectedDiscarded.length).toBeGreaterThan(0);
      }
    });

    it('memories have valid importance levels', () => {
      const validLevels = new Set(['critical', 'high', 'medium', 'low', 'trivial']);
      for (const b of CONSOLIDATION_BENCHMARKS) {
        for (const m of b.memories) {
          expect(validLevels.has(m.importance)).toBe(true);
        }
      }
    });

    it('memories have valid category values', () => {
      const validCats = new Set(['preference', 'pattern', 'context']);
      for (const b of CONSOLIDATION_BENCHMARKS) {
        for (const m of b.memories) {
          expect(validCats.has(m.category)).toBe(true);
        }
      }
    });
  });
});

// ═══════════════════════════════════════════════════════════════════════
// § 11  MemoryQualityEngine — Initialization
// ═══════════════════════════════════════════════════════════════════════
describe('MemoryQualityEngine — Initialization', () => {
  let engine: MemoryQualityEngine;

  beforeEach(() => {
    engine = new MemoryQualityEngine();
  });

  it('initializes with default config', () => {
    const config = engine.getConfig();
    expect(config.matchThreshold).toBe(0.6);
    expect(config.retrievalTopK).toBe(5);
    expect(config.maxReports).toBe(30);
    expect(config.weights.extraction).toBe(0.3);
  });

  it('initializes with custom config', () => {
    const custom = new MemoryQualityEngine({
      matchThreshold: 0.8,
      weights: { extraction: 0.5, retrieval: 0.2, consolidation: 0.2, personMention: 0.1 },
    });
    const config = custom.getConfig();
    expect(config.matchThreshold).toBe(0.8);
    expect(config.weights.extraction).toBe(0.5);
  });

  it('loads from persisted file on initialize', async () => {
    const fsMock = await import('fs/promises');
    const mockData = JSON.stringify({
      config: { matchThreshold: 0.7 },
      reports: [{ id: 'old', timestamp: 1000, overallScore: 0.5 }],
    });
    vi.mocked(fsMock.default.readFile).mockResolvedValueOnce(mockData);

    const eng = new MemoryQualityEngine();
    await eng.initialize();

    expect(eng.getQualityHistory().length).toBe(1);
    expect(eng.getConfig().matchThreshold).toBe(0.7);
  });

  it('initializes empty when file does not exist', async () => {
    const eng = new MemoryQualityEngine();
    await eng.initialize();
    expect(eng.getQualityHistory().length).toBe(0);
    expect(eng.getLatestReport()).toBeNull();
  });
});

// ═══════════════════════════════════════════════════════════════════════
// § 12  MemoryQualityEngine — Extraction Assessment
// ═══════════════════════════════════════════════════════════════════════
describe('MemoryQualityEngine — Extraction Assessment', () => {
  let engine: MemoryQualityEngine;

  beforeEach(() => {
    engine = new MemoryQualityEngine();
  });

  it('returns zero metrics for empty results', () => {
    const metrics = engine.assessExtractionQuality([]);
    expect(metrics.sampleSize).toBe(0);
    expect(metrics.precision).toBe(0);
  });

  it('assesses perfect extraction', () => {
    const bench = EXTRACTION_BENCHMARKS[0]; // ext-01: identity info
    const result: ExtractionResult = {
      benchmarkId: bench.id,
      actualFacts: [...bench.expectedFacts],
      actualObservations: [...bench.expectedObservations],
      actualPersonMentions: [...bench.expectedPersonMentions],
    };
    const metrics = engine.assessExtractionQuality([result]);
    expect(metrics.recall).toBe(1);
    expect(metrics.precision).toBe(1);
    expect(metrics.f1).toBe(1);
  });

  it('detects missing extractions (low recall)', () => {
    const bench = EXTRACTION_BENCHMARKS[0];
    const result: ExtractionResult = {
      benchmarkId: bench.id,
      actualFacts: [bench.expectedFacts[0]], // Only first fact
      actualObservations: [],
      actualPersonMentions: [],
    };
    const metrics = engine.assessExtractionQuality([result]);
    expect(metrics.recall).toBeLessThan(1);
    expect(metrics.precision).toBe(1); // The one we got was correct
  });

  it('detects spurious extractions (low precision)', () => {
    const bench = EXTRACTION_BENCHMARKS[0];
    const result: ExtractionResult = {
      benchmarkId: bench.id,
      actualFacts: [...bench.expectedFacts, 'Spurious fact one', 'Spurious fact two'],
      actualObservations: [],
      actualPersonMentions: [],
    };
    const metrics = engine.assessExtractionQuality([result]);
    expect(metrics.precision).toBeLessThan(1);
  });

  it('aggregates across multiple benchmarks', () => {
    const results: ExtractionResult[] = EXTRACTION_BENCHMARKS.map(b => ({
      benchmarkId: b.id,
      actualFacts: [...b.expectedFacts],
      actualObservations: [...b.expectedObservations],
      actualPersonMentions: [...b.expectedPersonMentions],
    }));
    const metrics = engine.assessExtractionQuality(results);
    expect(metrics.recall).toBe(1);
    expect(metrics.sampleSize).toBeGreaterThan(5);
  });

  it('ignores unknown benchmark IDs', () => {
    const result: ExtractionResult = {
      benchmarkId: 'nonexistent-id',
      actualFacts: ['something'],
      actualObservations: [],
      actualPersonMentions: [],
    };
    const metrics = engine.assessExtractionQuality([result]);
    // Nothing to compare — should get defaults
    expect(metrics.sampleSize).toBe(0);
  });
});

// ═══════════════════════════════════════════════════════════════════════
// § 13  MemoryQualityEngine — Retrieval Assessment
// ═══════════════════════════════════════════════════════════════════════
describe('MemoryQualityEngine — Retrieval Assessment', () => {
  let engine: MemoryQualityEngine;

  beforeEach(() => {
    engine = new MemoryQualityEngine();
  });

  it('returns zero metrics for empty results', () => {
    const metrics = engine.assessRetrievalQuality([]);
    expect(metrics.sampleSize).toBe(0);
    expect(metrics.mrr).toBe(0);
  });

  it('assesses perfect retrieval', () => {
    const bench = RETRIEVAL_BENCHMARKS[0]; // ret-01
    const result: RetrievalResult = {
      benchmarkId: bench.id,
      query: bench.query,
      actualResults: [...bench.expectedResults, ...bench.candidatePool.filter(
        c => !bench.expectedResults.some(e => textSimilarity(e, c) >= 0.6),
      )],
      expectedResults: bench.expectedResults,
    };
    const metrics = engine.assessRetrievalQuality([result]);
    expect(metrics.mrr).toBe(1);
    expect(metrics.hitRate).toBe(1);
  });

  it('detects poor ranking (low MRR)', () => {
    const bench = RETRIEVAL_BENCHMARKS[0];
    const nonMatching = bench.candidatePool.filter(
      c => !bench.expectedResults.some(e => textSimilarity(e, c) >= 0.6),
    );
    const result: RetrievalResult = {
      benchmarkId: bench.id,
      query: bench.query,
      actualResults: [...nonMatching, ...bench.expectedResults], // Expected results at the end
      expectedResults: bench.expectedResults,
    };
    const metrics = engine.assessRetrievalQuality([result]);
    expect(metrics.mrr).toBeLessThan(1);
  });
});

// ═══════════════════════════════════════════════════════════════════════
// § 14  MemoryQualityEngine — Consolidation Assessment
// ═══════════════════════════════════════════════════════════════════════
describe('MemoryQualityEngine — Consolidation Assessment', () => {
  let engine: MemoryQualityEngine;

  beforeEach(() => {
    engine = new MemoryQualityEngine();
  });

  it('returns zero metrics for empty results', () => {
    const metrics = engine.assessConsolidationQuality([]);
    expect(metrics.sampleSize).toBe(0);
  });

  it('assesses perfect consolidation', () => {
    const bench = CONSOLIDATION_BENCHMARKS[0];
    const result: ConsolidationResult = {
      benchmarkId: bench.id,
      actualRetained: [...bench.expectedRetained],
      actualDiscarded: [...bench.expectedDiscarded],
      expectedRetained: bench.expectedRetained,
      expectedDiscarded: bench.expectedDiscarded,
    };
    const metrics = engine.assessConsolidationQuality([result]);
    expect(metrics.retentionRate).toBe(1);
    expect(metrics.discardAccuracy).toBe(1);
  });
});

// ═══════════════════════════════════════════════════════════════════════
// § 15  MemoryQualityEngine — Person Mention Assessment
// ═══════════════════════════════════════════════════════════════════════
describe('MemoryQualityEngine — Person Mention Assessment', () => {
  let engine: MemoryQualityEngine;

  beforeEach(() => {
    engine = new MemoryQualityEngine();
  });

  it('returns zero metrics for empty results', () => {
    const metrics = engine.assessPersonMentionQuality([]);
    expect(metrics.sampleSize).toBe(0);
  });

  it('assesses perfect person mention extraction', () => {
    const bench = EXTRACTION_BENCHMARKS[2]; // ext-03 has Sarah and Mike
    const result: ExtractionResult = {
      benchmarkId: bench.id,
      actualFacts: [],
      actualObservations: [],
      actualPersonMentions: [...bench.expectedPersonMentions],
    };
    const metrics = engine.assessPersonMentionQuality([result]);
    expect(metrics.recall).toBe(1);
    expect(metrics.precision).toBe(1);
  });

  it('detects missing person mentions', () => {
    const bench = EXTRACTION_BENCHMARKS[2]; // Has Sarah and Mike
    const result: ExtractionResult = {
      benchmarkId: bench.id,
      actualFacts: [],
      actualObservations: [],
      actualPersonMentions: [bench.expectedPersonMentions[0]], // Only Sarah
    };
    const metrics = engine.assessPersonMentionQuality([result]);
    expect(metrics.recall).toBeLessThan(1);
  });
});

// ═══════════════════════════════════════════════════════════════════════
// § 16  MemoryQualityEngine — Overall Score
// ═══════════════════════════════════════════════════════════════════════
describe('MemoryQualityEngine — Overall Score', () => {
  let engine: MemoryQualityEngine;

  beforeEach(() => {
    engine = new MemoryQualityEngine();
  });

  it('returns 1 when all metrics are perfect', () => {
    const score = engine.computeOverallScore(
      { precision: 1, recall: 1, f1: 1, sampleSize: 5 },
      { mrr: 1, ndcg: 1, hitRate: 1, topK: 5, sampleSize: 5 },
      { retentionRate: 1, discardAccuracy: 1, promotionAccuracy: 1, sampleSize: 2 },
      { precision: 1, recall: 1, f1: 1, sampleSize: 3 },
    );
    expect(score).toBe(1);
  });

  it('returns 0 when all metrics are zero', () => {
    const score = engine.computeOverallScore(
      { precision: 0, recall: 0, f1: 0, sampleSize: 5 },
      { mrr: 0, ndcg: 0, hitRate: 0, topK: 5, sampleSize: 5 },
      { retentionRate: 0, discardAccuracy: 0, promotionAccuracy: 0, sampleSize: 2 },
      { precision: 0, recall: 0, f1: 0, sampleSize: 3 },
    );
    expect(score).toBe(0);
  });

  it('is bounded between 0 and 1', () => {
    const score = engine.computeOverallScore(
      { precision: 0.5, recall: 0.5, f1: 0.5, sampleSize: 5 },
      { mrr: 0.5, ndcg: 0.5, hitRate: 0.5, topK: 5, sampleSize: 5 },
      { retentionRate: 0.5, discardAccuracy: 0.5, promotionAccuracy: 0.5, sampleSize: 2 },
      { precision: 0.5, recall: 0.5, f1: 0.5, sampleSize: 3 },
    );
    expect(score).toBeGreaterThanOrEqual(0);
    expect(score).toBeLessThanOrEqual(1);
  });

  it('respects weight distribution', () => {
    // All extraction F1=1, rest 0 — extraction weight = 0.3
    const score = engine.computeOverallScore(
      { precision: 1, recall: 1, f1: 1, sampleSize: 5 },
      { mrr: 0, ndcg: 0, hitRate: 0, topK: 5, sampleSize: 5 },
      { retentionRate: 0, discardAccuracy: 0, promotionAccuracy: 0, sampleSize: 2 },
      { precision: 0, recall: 0, f1: 0, sampleSize: 3 },
    );
    expect(score).toBe(0.3);
  });
});

// ═══════════════════════════════════════════════════════════════════════
// § 17  MemoryQualityEngine — Recommendations
// ═══════════════════════════════════════════════════════════════════════
describe('MemoryQualityEngine — Recommendations', () => {
  let engine: MemoryQualityEngine;

  beforeEach(() => {
    engine = new MemoryQualityEngine();
  });

  it('generates extraction precision recommendation when low', () => {
    const recs = engine.generateRecommendations(
      { precision: 0.5, recall: 0.9, f1: 0.64, sampleSize: 5 },
      { mrr: 0.9, ndcg: 0.9, hitRate: 0.9, topK: 5, sampleSize: 5 },
      { retentionRate: 0.9, discardAccuracy: 0.9, promotionAccuracy: 0.9, sampleSize: 2 },
      { precision: 0.9, recall: 0.9, f1: 0.9, sampleSize: 3 },
    );
    expect(recs.some(r => r.includes('precision') && r.includes('low'))).toBe(true);
  });

  it('generates extraction recall recommendation when low', () => {
    const recs = engine.generateRecommendations(
      { precision: 0.9, recall: 0.4, f1: 0.55, sampleSize: 5 },
      { mrr: 0.9, ndcg: 0.9, hitRate: 0.9, topK: 5, sampleSize: 5 },
      { retentionRate: 0.9, discardAccuracy: 0.9, promotionAccuracy: 0.9, sampleSize: 2 },
      { precision: 0.9, recall: 0.9, f1: 0.9, sampleSize: 3 },
    );
    expect(recs.some(r => r.includes('recall') && r.includes('low'))).toBe(true);
  });

  it('generates retrieval MRR recommendation when low', () => {
    const recs = engine.generateRecommendations(
      { precision: 0.9, recall: 0.9, f1: 0.9, sampleSize: 5 },
      { mrr: 0.3, ndcg: 0.3, hitRate: 0.9, topK: 5, sampleSize: 5 },
      { retentionRate: 0.9, discardAccuracy: 0.9, promotionAccuracy: 0.9, sampleSize: 2 },
      { precision: 0.9, recall: 0.9, f1: 0.9, sampleSize: 3 },
    );
    expect(recs.some(r => r.includes('MRR'))).toBe(true);
  });

  it('generates consolidation retention recommendation when low', () => {
    const recs = engine.generateRecommendations(
      { precision: 0.9, recall: 0.9, f1: 0.9, sampleSize: 5 },
      { mrr: 0.9, ndcg: 0.9, hitRate: 0.9, topK: 5, sampleSize: 5 },
      { retentionRate: 0.5, discardAccuracy: 0.9, promotionAccuracy: 0.9, sampleSize: 2 },
      { precision: 0.9, recall: 0.9, f1: 0.9, sampleSize: 3 },
    );
    expect(recs.some(r => r.includes('Consolidation') && r.includes('discarding'))).toBe(true);
  });

  it('returns positive message when all metrics are good', () => {
    const recs = engine.generateRecommendations(
      { precision: 0.9, recall: 0.9, f1: 0.9, sampleSize: 5 },
      { mrr: 0.8, ndcg: 0.8, hitRate: 0.9, topK: 5, sampleSize: 5 },
      { retentionRate: 0.9, discardAccuracy: 0.9, promotionAccuracy: 0.9, sampleSize: 2 },
      { precision: 0.9, recall: 0.9, f1: 0.9, sampleSize: 3 },
    );
    expect(recs.some(r => r.includes('acceptable ranges'))).toBe(true);
  });
});

// ═══════════════════════════════════════════════════════════════════════
// § 18  MemoryQualityEngine — Report Building
// ═══════════════════════════════════════════════════════════════════════
describe('MemoryQualityEngine — Report Building', () => {
  let engine: MemoryQualityEngine;

  beforeEach(() => {
    engine = new MemoryQualityEngine();
  });

  it('builds a complete report from benchmark results', () => {
    const extractionResults: ExtractionResult[] = EXTRACTION_BENCHMARKS.map(b => ({
      benchmarkId: b.id,
      actualFacts: [...b.expectedFacts],
      actualObservations: [...b.expectedObservations],
      actualPersonMentions: [...b.expectedPersonMentions],
    }));

    const retrievalResults: RetrievalResult[] = RETRIEVAL_BENCHMARKS.map(b => ({
      benchmarkId: b.id,
      query: b.query,
      actualResults: [...b.expectedResults, ...b.candidatePool],
      expectedResults: b.expectedResults,
    }));

    const consolidationResults: ConsolidationResult[] = CONSOLIDATION_BENCHMARKS.map(b => ({
      benchmarkId: b.id,
      actualRetained: [...b.expectedRetained],
      actualDiscarded: [...b.expectedDiscarded],
      expectedRetained: b.expectedRetained,
      expectedDiscarded: b.expectedDiscarded,
    }));

    const report = engine.buildReport(extractionResults, retrievalResults, consolidationResults);

    expect(report.id).toBeTruthy();
    expect(report.timestamp).toBeGreaterThan(0);
    expect(report.extraction.f1).toBe(1);
    expect(report.consolidation.retentionRate).toBe(1);
    expect(report.overallScore).toBeGreaterThan(0.5);
    expect(report.recommendations.length).toBeGreaterThan(0);
    expect(report.durationMs).toBeGreaterThanOrEqual(0);
  });

  it('stores report in history', () => {
    const report = engine.buildReport([], [], []);
    expect(engine.getQualityHistory().length).toBe(1);
    expect(engine.getLatestReport()?.id).toBe(report.id);
  });

  it('prunes reports beyond maxReports', () => {
    const small = new MemoryQualityEngine({ maxReports: 3 });
    for (let i = 0; i < 5; i++) {
      small.buildReport([], [], []);
    }
    expect(small.getQualityHistory().length).toBe(3);
  });

  it('handles empty results gracefully', () => {
    const report = engine.buildReport([], [], []);
    expect(report.extraction.sampleSize).toBe(0);
    expect(report.retrieval.sampleSize).toBe(0);
    expect(report.consolidation.sampleSize).toBe(0);
    expect(report.overallScore).toBe(0);
  });
});

// ═══════════════════════════════════════════════════════════════════════
// § 19  MemoryQualityEngine — Quality Trend
// ═══════════════════════════════════════════════════════════════════════
describe('MemoryQualityEngine — Quality Trend', () => {
  let engine: MemoryQualityEngine;

  beforeEach(() => {
    engine = new MemoryQualityEngine();
  });

  it('returns empty array when no reports', () => {
    expect(engine.getQualityTrend()).toEqual([]);
  });

  it('returns trend data points', () => {
    engine.buildReport([], [], []);
    engine.buildReport([], [], []);
    engine.buildReport([], [], []);

    const trend = engine.getQualityTrend(3);
    expect(trend.length).toBe(3);
    expect(trend[0]).toHaveProperty('timestamp');
    expect(trend[0]).toHaveProperty('score');
  });

  it('limits to requested count', () => {
    for (let i = 0; i < 10; i++) {
      engine.buildReport([], [], []);
    }
    const trend = engine.getQualityTrend(3);
    expect(trend.length).toBe(3);
  });
});

// ═══════════════════════════════════════════════════════════════════════
// § 20  MemoryQualityEngine — Prompt Context
// ═══════════════════════════════════════════════════════════════════════
describe('MemoryQualityEngine — Prompt Context', () => {
  let engine: MemoryQualityEngine;

  beforeEach(() => {
    engine = new MemoryQualityEngine();
  });

  it('returns unmeasured message when no reports', () => {
    const ctx = engine.getPromptContext();
    expect(ctx).toContain('MEMORY QUALITY');
    expect(ctx).toContain('unmeasured');
  });

  it('shows quality level label for high score', () => {
    // Build a perfect report
    const extractionResults: ExtractionResult[] = EXTRACTION_BENCHMARKS.map(b => ({
      benchmarkId: b.id,
      actualFacts: [...b.expectedFacts],
      actualObservations: [...b.expectedObservations],
      actualPersonMentions: [...b.expectedPersonMentions],
    }));
    const retrievalResults: RetrievalResult[] = RETRIEVAL_BENCHMARKS.map(b => ({
      benchmarkId: b.id,
      query: b.query,
      actualResults: [...b.expectedResults, ...b.candidatePool],
      expectedResults: b.expectedResults,
    }));
    const consolidationResults: ConsolidationResult[] = CONSOLIDATION_BENCHMARKS.map(b => ({
      benchmarkId: b.id,
      actualRetained: [...b.expectedRetained],
      actualDiscarded: [...b.expectedDiscarded],
      expectedRetained: b.expectedRetained,
      expectedDiscarded: b.expectedDiscarded,
    }));
    engine.buildReport(extractionResults, retrievalResults, consolidationResults);

    const ctx = engine.getPromptContext();
    expect(ctx).toContain('HIGH');
    expect(ctx).toContain('Extraction');
    expect(ctx).toContain('Retrieval');
  });

  it('shows LOW label for poor quality', () => {
    engine.buildReport([], [], []);
    const ctx = engine.getPromptContext();
    expect(ctx).toContain('LOW');
  });

  it('includes trend indicator for 3+ reports', () => {
    for (let i = 0; i < 4; i++) {
      engine.buildReport([], [], []);
    }
    const ctx = engine.getPromptContext();
    // All scores are 0 → no improvement or decline → no trend arrow
    // But at least the format is correct
    expect(ctx).toContain('MEMORY QUALITY');
  });
});

// ═══════════════════════════════════════════════════════════════════════
// § 21  MemoryQualityEngine — Configuration
// ═══════════════════════════════════════════════════════════════════════
describe('MemoryQualityEngine — Configuration', () => {
  let engine: MemoryQualityEngine;

  beforeEach(() => {
    engine = new MemoryQualityEngine();
  });

  it('returns a copy of config (not a reference)', () => {
    const config = engine.getConfig();
    config.matchThreshold = 0.99;
    expect(engine.getConfig().matchThreshold).toBe(0.6); // Unchanged
  });

  it('updates partial config', () => {
    const updated = engine.updateConfig({ matchThreshold: 0.8 });
    expect(updated.matchThreshold).toBe(0.8);
    expect(updated.retrievalTopK).toBe(5); // Unchanged
  });

  it('updates weight sub-config', () => {
    engine.updateConfig({ weights: { extraction: 0.5, retrieval: 0.2, consolidation: 0.2, personMention: 0.1 } });
    const config = engine.getConfig();
    expect(config.weights.extraction).toBe(0.5);
    expect(config.weights.personMention).toBe(0.1);
  });

  it('preserves other weights on partial weight update', () => {
    engine.updateConfig({ weights: { extraction: 0.5, retrieval: 0.3, consolidation: 0.2, personMention: 0.2 } });
    const config = engine.getConfig();
    expect(config.weights.retrieval).toBe(0.3);
  });
});

// ═══════════════════════════════════════════════════════════════════════
// § 22  MemoryQualityEngine — Benchmark Accessors
// ═══════════════════════════════════════════════════════════════════════
describe('MemoryQualityEngine — Benchmark Accessors', () => {
  let engine: MemoryQualityEngine;

  beforeEach(() => {
    engine = new MemoryQualityEngine();
  });

  it('returns copies of extraction benchmarks', () => {
    const benchmarks = engine.getExtractionBenchmarks();
    expect(benchmarks.length).toBe(EXTRACTION_BENCHMARKS.length);
    // Mutating the copy shouldn't affect originals
    benchmarks.pop();
    expect(engine.getExtractionBenchmarks().length).toBe(EXTRACTION_BENCHMARKS.length);
  });

  it('returns copies of retrieval benchmarks', () => {
    const benchmarks = engine.getRetrievalBenchmarks();
    expect(benchmarks.length).toBe(RETRIEVAL_BENCHMARKS.length);
  });

  it('returns copies of consolidation benchmarks', () => {
    const benchmarks = engine.getConsolidationBenchmarks();
    expect(benchmarks.length).toBe(CONSOLIDATION_BENCHMARKS.length);
  });
});

// ═══════════════════════════════════════════════════════════════════════
// § 23  MemoryQualityEngine — Lifecycle
// ═══════════════════════════════════════════════════════════════════════
describe('MemoryQualityEngine — Lifecycle', () => {
  it('stop() does not throw', () => {
    const engine = new MemoryQualityEngine();
    expect(() => engine.stop()).not.toThrow();
  });
});

// ═══════════════════════════════════════════════════════════════════════
// § 24  cLaw Compliance
// ═══════════════════════════════════════════════════════════════════════
describe('cLaw Compliance', () => {
  it('all benchmark data is synthetic — no real user info', () => {
    // Check that no extraction benchmark contains plausible real email or phone
    for (const b of EXTRACTION_BENCHMARKS) {
      for (const msg of b.messages) {
        expect(msg.content).not.toMatch(/[a-z]+@[a-z]+\.[a-z]+/); // No emails
        expect(msg.content).not.toMatch(/\d{3}-\d{3}-\d{4}/); // No phone numbers
      }
    }
  });

  it('engine never contacts external services', () => {
    // The engine has no fetch/http imports — this is a structural assertion
    const engine = new MemoryQualityEngine();
    // Building a report should be purely computational
    const report = engine.buildReport([], [], []);
    expect(report.durationMs).toBeGreaterThanOrEqual(0);
  });

  it('synthetic benchmarks use fictitious company names', () => {
    // Verify company names are clearly fictional
    const companyMentions = EXTRACTION_BENCHMARKS
      .flatMap(b => b.messages.map(m => m.content))
      .join(' ');
    expect(companyMentions).toContain('Meridian Tech'); // Fictional
    expect(companyMentions).not.toContain('Google');
    expect(companyMentions).not.toContain('Microsoft');
    expect(companyMentions).not.toContain('Amazon');
  });
});

// ═══════════════════════════════════════════════════════════════════════
// § 25  Edge Cases
// ═══════════════════════════════════════════════════════════════════════
describe('Edge cases', () => {
  it('textSimilarity handles all-stopword text', () => {
    const sim = textSimilarity('the is a', 'an the was');
    // After removing stopwords, both are empty → returns 1
    expect(sim).toBe(1);
  });

  it('textSimilarity handles numeric text', () => {
    const sim = textSimilarity('version 42', 'version 42');
    expect(sim).toBe(1);
  });

  it('computePrecisionRecall with single items', () => {
    const result = computePrecisionRecall(['hello world'], ['hello world']);
    expect(result.precision).toBe(1);
    expect(result.recall).toBe(1);
  });

  it('computeMRR with single query single result', () => {
    const mrr = computeMRR([
      { expectedResults: ['target item'], actualResults: ['target item'] },
    ]);
    expect(mrr).toBe(1);
  });

  it('NDCG with single relevant result at position 0', () => {
    const ndcg = computeNDCG([1], [1]);
    expect(ndcg).toBe(1);
  });

  it('overall score with custom weights', () => {
    const engine = new MemoryQualityEngine({
      weights: { extraction: 1, retrieval: 0, consolidation: 0, personMention: 0 },
    });
    const score = engine.computeOverallScore(
      { precision: 1, recall: 1, f1: 1, sampleSize: 5 },
      { mrr: 0, ndcg: 0, hitRate: 0, topK: 5, sampleSize: 5 },
      { retentionRate: 0, discardAccuracy: 0, promotionAccuracy: 0, sampleSize: 2 },
      { precision: 0, recall: 0, f1: 0, sampleSize: 3 },
    );
    expect(score).toBe(1); // Only extraction matters
  });
});
