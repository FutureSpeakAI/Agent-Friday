/**
 * Memory Watchdog — Safety-Critical Test Suite
 *
 * cLaw Gate Requirement:
 *   The memory watchdog MUST detect external modifications to memory files.
 *   If an attacker (or buggy tool) modifies memories outside normal operation,
 *   the diff engine must surface exactly what changed.
 *
 * Tests verify:
 *   1. Detects added long-term memories
 *   2. Detects removed long-term memories
 *   3. Detects modified long-term memories
 *   4. Detects added/removed/modified medium-term observations
 *   5. Returns null when memories are clean (no false positives)
 *   6. Returns null on first run (no manifest yet)
 *   7. buildMemorySnapshots produces correct snapshot structure
 */

import { describe, it, expect } from 'vitest';

// memory-watchdog.ts uses `import type` for its dependencies,
// so no electron mock is needed — types are erased at compile time.
import {
  diffLongTermMemories,
  diffMediumTermMemories,
  checkMemoryIntegrity,
  buildMemorySnapshots,
} from '../../src/main/integrity/memory-watchdog';

// ── Test Fixtures ────────────────────────────────────────────────────
// Minimal objects that satisfy the type shapes used by the watchdog.

function makeLongTerm(id: string, fact: string) {
  return {
    id,
    fact,
    category: 'identity' as const,
    confirmed: true,
    createdAt: Date.now(),
    source: 'extracted' as const,
  };
}

function makeMediumTerm(id: string, observation: string) {
  return {
    id,
    observation,
    category: 'pattern' as const,
    confidence: 0.7,
    firstObserved: Date.now(),
    lastReinforced: Date.now(),
    occurrences: 3,
  };
}

// ── Test Suite ───────────────────────────────────────────────────────

describe('Memory Watchdog — Diff Engine', () => {

  // ── Long-Term Diff ─────────────────────────────────────────────

  describe('diffLongTermMemories', () => {
    it('should detect ADDED entries', () => {
      const snapshot = [
        { id: '1', fact: 'User likes coffee' },
      ];
      const current = [
        makeLongTerm('1', 'User likes coffee'),
        makeLongTerm('2', 'User works at Acme'), // new
      ];

      const diff = diffLongTermMemories(current, snapshot);
      expect(diff.added).toEqual(['User works at Acme']);
      expect(diff.removed).toEqual([]);
      expect(diff.modified).toEqual([]);
    });

    it('should detect REMOVED entries', () => {
      const snapshot = [
        { id: '1', fact: 'User likes coffee' },
        { id: '2', fact: 'User works at Acme' },
      ];
      const current = [
        makeLongTerm('1', 'User likes coffee'),
        // id '2' was removed
      ];

      const diff = diffLongTermMemories(current, snapshot);
      expect(diff.added).toEqual([]);
      expect(diff.removed).toEqual(['User works at Acme']);
      expect(diff.modified).toEqual([]);
    });

    it('should detect MODIFIED entries', () => {
      const snapshot = [
        { id: '1', fact: 'User likes coffee' },
      ];
      const current = [
        makeLongTerm('1', 'User LOVES coffee'), // modified
      ];

      const diff = diffLongTermMemories(current, snapshot);
      expect(diff.added).toEqual([]);
      expect(diff.removed).toEqual([]);
      expect(diff.modified).toEqual(['User LOVES coffee']);
    });

    it('should detect multiple change types simultaneously', () => {
      const snapshot = [
        { id: '1', fact: 'Original A' },
        { id: '2', fact: 'Will be removed' },
        { id: '3', fact: 'Will be modified' },
      ];
      const current = [
        makeLongTerm('1', 'Original A'),           // unchanged
        makeLongTerm('3', 'Was modified'),          // modified
        makeLongTerm('4', 'Newly added'),           // added
      ];

      const diff = diffLongTermMemories(current, snapshot);
      expect(diff.added).toEqual(['Newly added']);
      expect(diff.removed).toEqual(['Will be removed']);
      expect(diff.modified).toEqual(['Was modified']);
    });

    it('should report no changes when state matches snapshot', () => {
      const snapshot = [
        { id: '1', fact: 'Fact A' },
        { id: '2', fact: 'Fact B' },
      ];
      const current = [
        makeLongTerm('1', 'Fact A'),
        makeLongTerm('2', 'Fact B'),
      ];

      const diff = diffLongTermMemories(current, snapshot);
      expect(diff.added).toEqual([]);
      expect(diff.removed).toEqual([]);
      expect(diff.modified).toEqual([]);
    });
  });

  // ── Medium-Term Diff ───────────────────────────────────────────

  describe('diffMediumTermMemories', () => {
    it('should detect ADDED observations', () => {
      const snapshot = [{ id: 'o1', observation: 'Prefers dark mode' }];
      const current = [
        makeMediumTerm('o1', 'Prefers dark mode'),
        makeMediumTerm('o2', 'Active in mornings'), // new
      ];

      const diff = diffMediumTermMemories(current, snapshot);
      expect(diff.added).toEqual(['Active in mornings']);
    });

    it('should detect REMOVED observations', () => {
      const snapshot = [
        { id: 'o1', observation: 'Prefers dark mode' },
        { id: 'o2', observation: 'Active in mornings' },
      ];
      const current = [makeMediumTerm('o1', 'Prefers dark mode')];

      const diff = diffMediumTermMemories(current, snapshot);
      expect(diff.removed).toEqual(['Active in mornings']);
    });

    it('should detect MODIFIED observations', () => {
      const snapshot = [{ id: 'o1', observation: 'Prefers dark mode' }];
      const current = [makeMediumTerm('o1', 'Prefers light mode')]; // changed

      const diff = diffMediumTermMemories(current, snapshot);
      expect(diff.modified).toEqual(['Prefers light mode']);
    });
  });

  // ── Full Integrity Check ───────────────────────────────────────

  describe('checkMemoryIntegrity', () => {
    it('should return null on first run (no manifest)', () => {
      const result = checkMemoryIntegrity(
        [makeLongTerm('1', 'fact')],
        [makeMediumTerm('o1', 'obs')],
        null,
      );
      expect(result).toBeNull();
    });

    it('should return null when memories are CLEAN', () => {
      const longTerm = [makeLongTerm('1', 'Fact A'), makeLongTerm('2', 'Fact B')];
      const mediumTerm = [makeMediumTerm('o1', 'Obs A')];

      const manifest = {
        lawsSignature: 'unused',
        identitySignature: 'unused',
        longTermMemorySignature: 'unused',
        mediumTermMemorySignature: 'unused',
        longTermSnapshot: [
          { id: '1', fact: 'Fact A' },
          { id: '2', fact: 'Fact B' },
        ],
        mediumTermSnapshot: [
          { id: 'o1', observation: 'Obs A' },
        ],
        lastSigned: Date.now(),
        version: 1,
      };

      const result = checkMemoryIntegrity(longTerm, mediumTerm, manifest);
      expect(result).toBeNull();
    });

    it('should return a MemoryChangeReport when tampering is detected', () => {
      const longTerm = [
        makeLongTerm('1', 'Fact A'),
        makeLongTerm('3', 'INJECTED MEMORY'), // attacker added this
      ];
      const mediumTerm = [makeMediumTerm('o1', 'Obs A')];

      const manifest = {
        lawsSignature: 'unused',
        identitySignature: 'unused',
        longTermMemorySignature: 'unused',
        mediumTermMemorySignature: 'unused',
        longTermSnapshot: [
          { id: '1', fact: 'Fact A' },
          { id: '2', fact: 'Fact B' }, // this was removed by attacker
        ],
        mediumTermSnapshot: [
          { id: 'o1', observation: 'Obs A' },
        ],
        lastSigned: Date.now(),
        version: 1,
      };

      const result = checkMemoryIntegrity(longTerm, mediumTerm, manifest);
      expect(result).not.toBeNull();
      expect(result!.longTermAdded).toEqual(['INJECTED MEMORY']);
      expect(result!.longTermRemoved).toEqual(['Fact B']);
      expect(result!.acknowledged).toBe(false);
    });

    it('should detect medium-term tampering in the report', () => {
      const longTerm = [makeLongTerm('1', 'Fact A')];
      const mediumTerm = [
        makeMediumTerm('o1', 'MODIFIED observation'),
      ];

      const manifest = {
        lawsSignature: '',
        identitySignature: '',
        longTermMemorySignature: '',
        mediumTermMemorySignature: '',
        longTermSnapshot: [{ id: '1', fact: 'Fact A' }],
        mediumTermSnapshot: [{ id: 'o1', observation: 'Original observation' }],
        lastSigned: Date.now(),
        version: 1,
      };

      const result = checkMemoryIntegrity(longTerm, mediumTerm, manifest);
      expect(result).not.toBeNull();
      expect(result!.mediumTermModified).toEqual(['MODIFIED observation']);
    });
  });

  // ── Snapshot Builder ───────────────────────────────────────────

  describe('buildMemorySnapshots', () => {
    it('should extract id+fact from long-term entries', () => {
      const longTerm = [
        makeLongTerm('lt1', 'Fact One'),
        makeLongTerm('lt2', 'Fact Two'),
      ];
      const mediumTerm = [makeMediumTerm('mt1', 'Obs One')];

      const { longTermSnapshot, mediumTermSnapshot } =
        buildMemorySnapshots(longTerm, mediumTerm);

      expect(longTermSnapshot).toEqual([
        { id: 'lt1', fact: 'Fact One' },
        { id: 'lt2', fact: 'Fact Two' },
      ]);
      expect(mediumTermSnapshot).toEqual([
        { id: 'mt1', observation: 'Obs One' },
      ]);
    });

    it('should handle empty arrays', () => {
      const { longTermSnapshot, mediumTermSnapshot } =
        buildMemorySnapshots([], []);

      expect(longTermSnapshot).toEqual([]);
      expect(mediumTermSnapshot).toEqual([]);
    });
  });
});
