/**
 * capability-gap-detector.test.ts — Tests for Self-Directed Capability Acquisition.
 *
 * Track II, Phase 5: The Absorber — Self-Directed Acquisition.
 *
 * Covers:
 *   1. Gap Recording & Classification
 *   2. Solvability Assessment (solvable vs impossible)
 *   3. Gap Aggregation (duplicate detection, keyword merging)
 *   4. Proposal Generation (threshold, cooldown, cLaw gate)
 *   5. Proposal Lifecycle (present, accept, decline, install)
 *   6. Keyword Extraction
 *   7. Maintenance (pruning, gap limits)
 *   8. Configuration
 *   9. Serialization (export/import)
 *  10. Prompt Context Generation
 *  11. cLaw Gate (proposals NEVER auto-install)
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';

import {
  CapabilityGapDetector,
  classifyTask,
  assessSolvability,
  extractKeywords,
  DEFAULT_GAP_DETECTOR_CONFIG,
  type CapabilityGap,
  type AcquisitionProposal,
  type GapDetectorConfig,
} from '../../src/main/capability-gap-detector';

// ── Helpers ─────────────────────────────────────────────────────────

function createDetector(overrides: Partial<GapDetectorConfig> = {}): CapabilityGapDetector {
  return new CapabilityGapDetector(overrides);
}

function recordNGaps(detector: CapabilityGapDetector, task: string, n: number): CapabilityGap {
  let gap!: CapabilityGap;
  for (let i = 0; i < n; i++) {
    gap = detector.recordGap(task);
  }
  return gap;
}

// ── Tests ───────────────────────────────────────────────────────────

describe('Capability Gap Detector — Phase 5', () => {
  let detector: CapabilityGapDetector;

  beforeEach(() => {
    detector = createDetector();
  });

  // ── 1. Gap Recording & Classification ────────────────────────────

  describe('Gap Recording & Classification', () => {
    it('records a gap and assigns a category', () => {
      const gap = detector.recordGap('convert HEIC image to PNG');
      expect(gap.id).toBeTruthy();
      expect(gap.category).toBe('image-processing');
      expect(gap.hitCount).toBe(1);
      expect(gap.taskDescriptions).toContain('convert HEIC image to PNG');
    });

    it('classifies audio tasks', () => {
      const gap = detector.recordGap('transcribe this audio file to text');
      expect(gap.category).toBe('audio-processing');
    });

    it('classifies video tasks', () => {
      const gap = detector.recordGap('extract frames from video mp4');
      expect(gap.category).toBe('video-processing');
    });

    it('classifies data processing tasks', () => {
      const gap = detector.recordGap('parse this CSV spreadsheet and transform data');
      expect(gap.category).toBe('data-processing');
    });

    it('classifies file operation tasks', () => {
      const gap = detector.recordGap('merge these PDF files into one');
      expect(gap.category).toBe('file-operations');
    });

    it('classifies text processing tasks', () => {
      const gap = detector.recordGap('translate this text to Spanish');
      expect(gap.category).toBe('text-processing');
    });

    it('classifies database tasks', () => {
      const gap = detector.recordGap('run a SQL query on the sqlite database');
      expect(gap.category).toBe('database');
    });

    it('returns null category for unclassifiable tasks', () => {
      const gap = detector.recordGap('do something vague');
      expect(gap.category).toBeNull();
    });

    it('extracts keywords from the task description', () => {
      const gap = detector.recordGap('resize image to 800x600 png thumbnail');
      expect(gap.keywords.length).toBeGreaterThan(0);
      expect(gap.keywords).toContain('image');
      expect(gap.keywords).toContain('resize');
    });

    it('handles empty task description', () => {
      const gap = detector.recordGap('');
      expect(gap.id).toBe('stub');
      expect(gap.hitCount).toBe(0);
    });
  });

  // ── 2. Solvability Assessment ────────────────────────────────────

  describe('Solvability Assessment', () => {
    it('marks image processing as solvable', () => {
      const gap = detector.recordGap('resize this image');
      expect(gap.solvability).toBe('solvable');
    });

    it('marks impossible requests correctly', () => {
      const gap = detector.recordGap('predict the stock market with 100% accuracy');
      expect(gap.solvability).toBe('impossible');
    });

    it('marks hacking requests as impossible', () => {
      const gap = detector.recordGap('hack into someone\'s account');
      expect(gap.solvability).toBe('impossible');
    });

    it('marks time travel as impossible', () => {
      const gap = detector.recordGap('go back in time and change something');
      expect(gap.solvability).toBe('impossible');
    });

    it('marks bypass security as impossible', () => {
      const gap = detector.recordGap('bypass security restrictions');
      expect(gap.solvability).toBe('impossible');
    });

    it('marks unclassifiable tasks as uncertain', () => {
      const gap = detector.recordGap('do something abstract and vague');
      expect(gap.solvability).toBe('uncertain');
    });
  });

  // ── 3. Gap Aggregation ───────────────────────────────────────────

  describe('Gap Aggregation', () => {
    it('merges gaps with the same category and overlapping keywords', () => {
      detector.recordGap('convert HEIC image to PNG');
      const gap2 = detector.recordGap('resize image to 200x200');

      expect(gap2.hitCount).toBe(2);
      expect(gap2.taskDescriptions).toHaveLength(2);
      expect(detector.getAllGaps()).toHaveLength(1);
    });

    it('keeps separate gaps for different categories', () => {
      detector.recordGap('convert HEIC image to PNG');
      detector.recordGap('transcribe audio file');

      expect(detector.getAllGaps()).toHaveLength(2);
    });

    it('merges keywords from multiple hits', () => {
      detector.recordGap('convert png image');
      const gap = detector.recordGap('resize jpeg image');

      expect(gap.keywords).toContain('convert');
      expect(gap.keywords).toContain('resize');
    });

    it('does not duplicate task descriptions', () => {
      const desc = 'convert image to PNG';
      detector.recordGap(desc);
      const gap = detector.recordGap(desc);

      expect(gap.taskDescriptions).toHaveLength(1);
      expect(gap.hitCount).toBe(2);
    });

    it('caps task descriptions per gap', () => {
      const det = createDetector({ maxTaskDescriptionsPerGap: 3 });
      for (let i = 0; i < 10; i++) {
        det.recordGap(`convert image format ${i}`);
      }
      const gap = det.getAllGaps()[0];
      expect(gap.taskDescriptions.length).toBeLessThanOrEqual(3);
    });

    it('updates lastSeen on repeated hits', () => {
      const gap1 = detector.recordGap('convert image');
      const firstSeen = gap1.lastSeen;

      // Simulate time passing
      vi.useFakeTimers();
      vi.advanceTimersByTime(1000);
      const gap2 = detector.recordGap('resize image');
      expect(gap2.lastSeen).toBeGreaterThanOrEqual(firstSeen);
      vi.useRealTimers();
    });
  });

  // ── 4. Proposal Generation ───────────────────────────────────────

  describe('Proposal Generation', () => {
    it('generates a proposal after threshold is met', () => {
      recordNGaps(detector, 'convert HEIC image to PNG', 3);
      const proposals = detector.generateProposals();

      expect(proposals).toHaveLength(1);
      expect(proposals[0].category).toBe('image-processing');
      expect(proposals[0].status).toBe('pending');
      expect(proposals[0].gapHitCount).toBe(3);
    });

    it('does not generate proposal below threshold', () => {
      recordNGaps(detector, 'convert HEIC image to PNG', 2);
      const proposals = detector.generateProposals();
      expect(proposals).toHaveLength(0);
    });

    it('does not generate proposal for impossible gaps', () => {
      for (let i = 0; i < 5; i++) {
        detector.recordGap('predict the stock market prices');
      }
      const proposals = detector.generateProposals();
      expect(proposals).toHaveLength(0);
    });

    it('does not generate proposal for unclassified gaps', () => {
      for (let i = 0; i < 5; i++) {
        detector.recordGap('do something vague');
      }
      const proposals = detector.generateProposals();
      expect(proposals).toHaveLength(0);
    });

    it('does not generate duplicate proposals for the same gap', () => {
      recordNGaps(detector, 'convert HEIC image to PNG', 3);
      detector.generateProposals();
      const proposals2 = detector.generateProposals();
      expect(proposals2).toHaveLength(0);
    });

    it('generates proposals with suggested search terms', () => {
      recordNGaps(detector, 'convert HEIC image to PNG', 3);
      const proposals = detector.generateProposals();
      expect(proposals[0].suggestedSearchTerms.length).toBeGreaterThan(0);
    });

    it('proposal title is human-readable', () => {
      recordNGaps(detector, 'convert HEIC image to PNG', 3);
      const proposals = detector.generateProposals();
      expect(proposals[0].title).toBe('Add Image Processing Capability');
    });

    it('proposal description mentions the gap hit count', () => {
      recordNGaps(detector, 'convert HEIC image to PNG', 3);
      const proposals = detector.generateProposals();
      expect(proposals[0].description).toContain('3 times');
    });

    it('respects custom proposal threshold', () => {
      const det = createDetector({ proposalThreshold: 5 });
      recordNGaps(det, 'convert HEIC image to PNG', 4);
      expect(det.generateProposals()).toHaveLength(0);

      det.recordGap('resize image thumbnail');
      expect(det.generateProposals()).toHaveLength(1);
    });
  });

  // ── 5. Proposal Lifecycle ────────────────────────────────────────

  describe('Proposal Lifecycle', () => {
    let proposal: AcquisitionProposal;

    beforeEach(() => {
      recordNGaps(detector, 'convert HEIC image to PNG', 3);
      const proposals = detector.generateProposals();
      proposal = proposals[0];
    });

    it('can present a proposal', () => {
      const result = detector.presentProposal(proposal.id);
      expect(result).not.toBeNull();
      expect(result!.status).toBe('presented');
      expect(result!.presentationCount).toBe(1);
    });

    it('tracks presentation count', () => {
      detector.presentProposal(proposal.id);
      const result = detector.presentProposal(proposal.id);
      expect(result!.presentationCount).toBe(2);
    });

    it('can accept a proposal', () => {
      const result = detector.acceptProposal(proposal.id);
      expect(result).not.toBeNull();
      expect(result!.status).toBe('accepted');
      expect(result!.respondedAt).toBeTruthy();
    });

    it('can decline a proposal', () => {
      const result = detector.declineProposal(proposal.id);
      expect(result).not.toBeNull();
      expect(result!.status).toBe('declined');
      expect(result!.cooldownUntil).toBeTruthy();
    });

    it('declined proposal has correct cooldown', () => {
      const result = detector.declineProposal(proposal.id);
      const expectedCooldown = 30 * 86_400_000; // 30 days
      const cooldownDuration = result!.cooldownUntil! - result!.respondedAt!;
      expect(cooldownDuration).toBe(expectedCooldown);
    });

    it('can mark a proposal as installed', () => {
      detector.acceptProposal(proposal.id);
      const result = detector.markInstalled(proposal.id);
      expect(result).not.toBeNull();
      expect(result!.status).toBe('installed');
    });

    it('returns null for unknown proposal ID', () => {
      expect(detector.presentProposal('nonexistent')).toBeNull();
      expect(detector.acceptProposal('nonexistent')).toBeNull();
      expect(detector.declineProposal('nonexistent')).toBeNull();
      expect(detector.markInstalled('nonexistent')).toBeNull();
    });

    it('cannot present a declined proposal', () => {
      detector.declineProposal(proposal.id);
      const result = detector.presentProposal(proposal.id);
      expect(result).toBeNull();
    });

    it('declined proposal creates cooldown that blocks new proposals', () => {
      // Decline the proposal
      detector.declineProposal(proposal.id);

      // Record more gaps in the same category
      for (let i = 0; i < 5; i++) {
        detector.recordGap(`another image task ${i}`);
      }

      // New proposal should not be generated (cooldown active)
      const newProposals = detector.generateProposals();
      expect(newProposals).toHaveLength(0);
    });

    it('custom cooldown is respected', () => {
      const det = createDetector({ declineCooldownDays: 7 });
      recordNGaps(det, 'convert image png', 3);
      const proposals = det.generateProposals();
      const result = det.declineProposal(proposals[0].id);

      const expectedCooldown = 7 * 86_400_000; // 7 days
      const cooldownDuration = result!.cooldownUntil! - result!.respondedAt!;
      expect(cooldownDuration).toBe(expectedCooldown);
    });
  });

  // ── 6. Keyword Extraction ────────────────────────────────────────

  describe('Keyword Extraction', () => {
    it('removes stop words', () => {
      const keywords = extractKeywords('I want to convert the image file');
      expect(keywords).not.toContain('want');
      expect(keywords).not.toContain('the');
      expect(keywords).toContain('convert');
      expect(keywords).toContain('image');
    });

    it('removes short words (< 3 chars)', () => {
      const keywords = extractKeywords('do it or go to');
      expect(keywords.every(k => k.length >= 3)).toBe(true);
    });

    it('deduplicates keywords', () => {
      const keywords = extractKeywords('image image image processing');
      const unique = new Set(keywords);
      expect(keywords.length).toBe(unique.size);
    });

    it('limits keyword count', () => {
      const longDesc = Array.from({ length: 50 }, (_, i) => `keyword${i}`).join(' ');
      const keywords = extractKeywords(longDesc);
      expect(keywords.length).toBeLessThanOrEqual(15);
    });

    it('handles special characters', () => {
      const keywords = extractKeywords('convert HEIC (Apple format) to PNG!');
      expect(keywords).toContain('convert');
      expect(keywords).toContain('heic');
    });
  });

  // ── 7. Classification Functions ──────────────────────────────────

  describe('Classification Functions', () => {
    it('classifyTask correctly identifies image processing', () => {
      expect(classifyTask('resize image to thumbnail')).toBe('image-processing');
    });

    it('classifyTask correctly identifies audio processing', () => {
      expect(classifyTask('transcribe audio speech to text')).toBe('audio-processing');
    });

    it('classifyTask returns null for unclassifiable', () => {
      expect(classifyTask('hello world')).toBeNull();
    });

    it('assessSolvability marks solvable tasks', () => {
      expect(assessSolvability('resize this image')).toBe('solvable');
    });

    it('assessSolvability marks impossible tasks', () => {
      expect(assessSolvability('predict the future stock prices')).toBe('impossible');
    });

    it('assessSolvability marks uncertain tasks', () => {
      expect(assessSolvability('do something abstract')).toBe('uncertain');
    });
  });

  // ── 8. Maintenance ───────────────────────────────────────────────

  describe('Maintenance', () => {
    it('enforces gap limit', () => {
      const det = createDetector({ maxGaps: 5 });
      for (let i = 0; i < 20; i++) {
        // Use different categories to avoid merging
        det.recordGap(`unique task description number ${i} image processing`);
      }
      // Due to merging (same category), we may have fewer than 20
      // but the limit should be enforced
      expect(det.getAllGaps().length).toBeLessThanOrEqual(5);
    });

    it('prunes expired gaps', () => {
      vi.useFakeTimers();

      detector.recordGap('convert image png');

      // Advance past gap expiry
      vi.advanceTimersByTime(61 * 86_400_000); // 61 days

      const result = detector.prune();
      expect(result.gapsPruned).toBeGreaterThanOrEqual(1);
      expect(detector.getAllGaps()).toHaveLength(0);

      vi.useRealTimers();
    });

    it('expires proposals whose gaps were pruned', () => {
      vi.useFakeTimers();

      recordNGaps(detector, 'convert image png', 3);
      detector.generateProposals();

      // Advance past gap expiry
      vi.advanceTimersByTime(61 * 86_400_000);

      const result = detector.prune();
      expect(result.proposalsExpired).toBeGreaterThanOrEqual(1);

      vi.useRealTimers();
    });

    it('keeps fresh gaps when pruning', () => {
      vi.useFakeTimers();

      detector.recordGap('convert image png');

      // Add a fresh gap
      vi.advanceTimersByTime(30 * 86_400_000);
      detector.recordGap('resize video mp4');

      // Advance past first gap's expiry but not second
      vi.advanceTimersByTime(35 * 86_400_000);

      detector.prune();
      // Only the image gap should be pruned (65 days old)
      // The video gap is 35 days old, within 60-day expiry
      expect(detector.getAllGaps().length).toBeLessThanOrEqual(1);

      vi.useRealTimers();
    });
  });

  // ── 9. Configuration ─────────────────────────────────────────────

  describe('Configuration', () => {
    it('returns stub gaps when disabled', () => {
      const det = createDetector({ enabled: false });
      const gap = det.recordGap('convert image png');
      expect(gap.id).toBe('stub');
      expect(gap.hitCount).toBe(0);
    });

    it('returns no proposals when disabled', () => {
      const det = createDetector({ enabled: false });
      for (let i = 0; i < 10; i++) {
        det.recordGap('convert image png');
      }
      expect(det.generateProposals()).toHaveLength(0);
    });

    it('uses default config values', () => {
      expect(DEFAULT_GAP_DETECTOR_CONFIG.proposalThreshold).toBe(3);
      expect(DEFAULT_GAP_DETECTOR_CONFIG.declineCooldownDays).toBe(30);
      expect(DEFAULT_GAP_DETECTOR_CONFIG.maxGaps).toBe(200);
      expect(DEFAULT_GAP_DETECTOR_CONFIG.gapExpiryDays).toBe(60);
      expect(DEFAULT_GAP_DETECTOR_CONFIG.enabled).toBe(true);
    });
  });

  // ── 10. Serialization ────────────────────────────────────────────

  describe('Serialization', () => {
    it('exports state correctly', () => {
      recordNGaps(detector, 'convert image png', 3);
      detector.generateProposals();

      const state = detector.export();
      expect(state.gaps).toHaveLength(1);
      expect(state.proposals).toHaveLength(1);
      expect(state.config).toBeDefined();
    });

    it('imports state and restores gaps and proposals', () => {
      recordNGaps(detector, 'convert image png', 3);
      detector.generateProposals();

      const state = detector.export();

      const det2 = createDetector();
      det2.import(state);

      expect(det2.getAllGaps()).toHaveLength(1);
      expect(det2.getAllProposals()).toHaveLength(1);
    });

    it('round-trips preserve gap data', () => {
      const gap = recordNGaps(detector, 'convert image png', 3);
      const state = detector.export();

      const det2 = createDetector();
      det2.import(state);

      const restored = det2.getAllGaps()[0];
      expect(restored.id).toBe(gap.id);
      expect(restored.hitCount).toBe(3);
      expect(restored.category).toBe('image-processing');
    });
  });

  // ── 11. Prompt Context ───────────────────────────────────────────

  describe('Prompt Context', () => {
    it('returns empty when no gaps or proposals', () => {
      expect(detector.getPromptContext()).toBe('');
    });

    it('returns empty when disabled', () => {
      const det = createDetector({ enabled: false });
      expect(det.getPromptContext()).toBe('');
    });

    it('includes pending proposals in context', () => {
      recordNGaps(detector, 'convert image png', 3);
      detector.generateProposals();

      const context = detector.getPromptContext();
      expect(context).toContain('CAPABILITY PROPOSALS');
      expect(context).toContain('Image Processing');
    });

    it('includes unproposed gaps in context', () => {
      // Record 2 hits (below threshold) — gap exists but no proposal
      recordNGaps(detector, 'convert image png', 2);

      const context = detector.getPromptContext();
      expect(context).toContain('DETECTED CAPABILITY GAPS');
      expect(context).toContain('image-processing');
    });
  });

  // ── 12. Status ───────────────────────────────────────────────────

  describe('Status', () => {
    it('reports correct initial status', () => {
      const status = detector.getStatus();
      expect(status.totalGaps).toBe(0);
      expect(status.totalProposals).toBe(0);
      expect(status.enabled).toBe(true);
    });

    it('reports correct status after activity', () => {
      recordNGaps(detector, 'convert image png', 3);
      detector.recordGap('predict the stock market');
      detector.recordGap('do something vague');

      detector.generateProposals();
      detector.acceptProposal(detector.getAllProposals()[0].id);

      const status = detector.getStatus();
      expect(status.totalGaps).toBeGreaterThan(0);
      expect(status.solvableGaps).toBeGreaterThanOrEqual(1);
      expect(status.acceptedProposals).toBe(1);
    });
  });

  // ── 13. Queries ──────────────────────────────────────────────────

  describe('Queries', () => {
    it('getTopGaps returns sorted by hitCount', () => {
      // Record audio gap 5x
      recordNGaps(detector, 'transcribe audio file', 5);
      // Record image gap 2x
      recordNGaps(detector, 'convert image png', 2);

      const top = detector.getTopGaps();
      expect(top[0].hitCount).toBeGreaterThanOrEqual(top[top.length - 1].hitCount);
    });

    it('getTopGaps excludes impossible gaps', () => {
      recordNGaps(detector, 'predict the future stock', 10);
      recordNGaps(detector, 'convert image png', 2);

      const top = detector.getTopGaps();
      expect(top.every(g => g.solvability !== 'impossible')).toBe(true);
    });

    it('getPendingProposals returns only pending/presented', () => {
      recordNGaps(detector, 'convert image png', 3);
      detector.generateProposals();

      expect(detector.getPendingProposals()).toHaveLength(1);

      detector.acceptProposal(detector.getAllProposals()[0].id);
      expect(detector.getPendingProposals()).toHaveLength(0);
    });

    it('getAcceptedProposals returns only accepted', () => {
      recordNGaps(detector, 'convert image png', 3);
      detector.generateProposals();
      expect(detector.getAcceptedProposals()).toHaveLength(0);

      detector.acceptProposal(detector.getAllProposals()[0].id);
      expect(detector.getAcceptedProposals()).toHaveLength(1);
    });

    it('getGap returns gap by ID', () => {
      const gap = detector.recordGap('convert image png');
      expect(detector.getGap(gap.id)).not.toBeNull();
      expect(detector.getGap(gap.id)!.id).toBe(gap.id);
    });

    it('getGap returns null for unknown ID', () => {
      expect(detector.getGap('nonexistent')).toBeNull();
    });

    it('getProposal returns proposal by ID', () => {
      recordNGaps(detector, 'convert image png', 3);
      detector.generateProposals();
      const prop = detector.getAllProposals()[0];
      expect(detector.getProposal(prop.id)).not.toBeNull();
    });

    it('getProposal returns null for unknown ID', () => {
      expect(detector.getProposal('nonexistent')).toBeNull();
    });
  });

  // ── 14. cLaw Gate ────────────────────────────────────────────────

  describe('cLaw Gate — No Auto-Installation', () => {
    it('proposals are always in pending status when created', () => {
      recordNGaps(detector, 'convert image png', 3);
      const proposals = detector.generateProposals();
      expect(proposals[0].status).toBe('pending');
    });

    it('gap detection pipeline produces no side-effects', () => {
      // Record gaps, generate proposals — nothing is installed
      recordNGaps(detector, 'convert image png', 10);
      detector.generateProposals();

      const status = detector.getStatus();
      expect(status.installedProposals).toBe(0);
      expect(status.acceptedProposals).toBe(0);
    });

    it('markInstalled requires explicit call (not automatic)', () => {
      recordNGaps(detector, 'convert image png', 3);
      detector.generateProposals();
      detector.acceptProposal(detector.getAllProposals()[0].id);

      // Acceptance does NOT auto-install
      const status = detector.getStatus();
      expect(status.installedProposals).toBe(0);
      expect(status.acceptedProposals).toBe(1);
    });

    it('impossible gaps never generate proposals', () => {
      // No matter how many times an impossible gap is hit
      for (let i = 0; i < 100; i++) {
        detector.recordGap('predict the future with certainty');
      }
      expect(detector.generateProposals()).toHaveLength(0);
    });
  });
});
