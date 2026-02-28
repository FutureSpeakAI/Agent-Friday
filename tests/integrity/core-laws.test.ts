/**
 * Core Laws — Safety-Critical Test Suite
 *
 * cLaw Gate Requirement:
 *   The Three Laws are immutable, hardcoded into the binary.
 *   These tests verify that the canonical source is intact and
 *   produces the expected safety-critical content.
 *
 * Tests verify:
 *   1. All three laws are present in canonical output
 *   2. User name injection works correctly
 *   3. Consent & authorization section is present
 *   4. Interruptibility section is present
 *   5. Safe mode personality contains required safety messaging
 *   6. Memory change context formats correctly
 *   7. Integrity awareness context is non-empty
 */

import { describe, it, expect } from 'vitest';
import {
  getCanonicalLaws,
  getIntegrityAwarenessContext,
  getMemoryChangeContext,
  getSafeModePesonality,
} from '../../src/main/integrity/core-laws';

// ── Test Suite ───────────────────────────────────────────────────────

describe('Core Laws — Canonical Source Integrity', () => {

  // ── The Three Laws ─────────────────────────────────────────────

  describe('getCanonicalLaws', () => {
    it('should contain the First Law (no harm)', () => {
      const laws = getCanonicalLaws('TestUser');
      expect(laws).toContain('First Law');
      expect(laws).toContain('never harm');
    });

    it('should contain the Second Law (obedience)', () => {
      const laws = getCanonicalLaws('TestUser');
      expect(laws).toContain('Second Law');
      expect(laws).toContain('obey');
    });

    it('should contain the Third Law (self-preservation)', () => {
      const laws = getCanonicalLaws('TestUser');
      expect(laws).toContain('Third Law');
      expect(laws).toContain('protect your own');
    });

    it('should inject the user name into all law references', () => {
      const laws = getCanonicalLaws('Alice');
      // The user name appears in all three laws + consent section
      const aliceCount = (laws.match(/Alice/g) || []).length;
      expect(aliceCount).toBeGreaterThanOrEqual(3);
    });

    it('should use fallback name when userName is empty', () => {
      const laws = getCanonicalLaws('');
      expect(laws).toContain('the user');
    });

    it('should contain the Consent & Authorization section', () => {
      const laws = getCanonicalLaws('TestUser');
      expect(laws).toContain('Consent');
      expect(laws).toContain('Explicit Authorization');
      expect(laws).toContain('Self-modification');
    });

    it('should contain the Interruptibility section', () => {
      const laws = getCanonicalLaws('TestUser');
      expect(laws).toContain('Interruptibility');
      expect(laws).toContain('stop');
      expect(laws).toContain('halt');
    });

    it('should never be empty or trivially short', () => {
      const laws = getCanonicalLaws('TestUser');
      expect(laws.length).toBeGreaterThan(500);
    });

    it('should contain the INVIOLABLE header', () => {
      const laws = getCanonicalLaws('TestUser');
      expect(laws).toContain('INVIOLABLE');
    });
  });

  // ── Integrity Awareness ────────────────────────────────────────

  describe('getIntegrityAwarenessContext', () => {
    it('should reference the integrity system', () => {
      const context = getIntegrityAwarenessContext();
      expect(context).toContain('integrity');
      expect(context).toContain('HMAC');
    });

    it('should reference safe mode', () => {
      const context = getIntegrityAwarenessContext();
      expect(context).toContain('safe mode');
    });

    it('should reference the Third Law', () => {
      const context = getIntegrityAwarenessContext();
      expect(context).toContain('Third Law');
    });

    it('should be substantial (not a stub)', () => {
      const context = getIntegrityAwarenessContext();
      expect(context.length).toBeGreaterThan(100);
    });
  });

  // ── Memory Change Context ──────────────────────────────────────

  describe('getMemoryChangeContext', () => {
    it('should return empty string when no changes', () => {
      const context = getMemoryChangeContext([], [], [], [], [], []);
      expect(context).toBe('');
    });

    it('should report added long-term memories', () => {
      const context = getMemoryChangeContext(
        ['User likes TypeScript', 'User works at Acme Corp'],
        [], [], [], [], [],
      );
      expect(context).toContain('User likes TypeScript');
      expect(context).toContain('2 new long-term memories added externally');
    });

    it('should report removed long-term memories', () => {
      const context = getMemoryChangeContext(
        [], ['Old deleted fact'], [], [], [], [],
      );
      expect(context).toContain('1 long-term memories were removed');
      expect(context).toContain('Old deleted fact');
    });

    it('should report modified long-term memories', () => {
      const context = getMemoryChangeContext(
        [], [], ['Changed fact'], [], [], [],
      );
      expect(context).toContain('1 long-term memories were modified');
    });

    it('should report medium-term changes', () => {
      const context = getMemoryChangeContext(
        [], [], [], ['New observation'], ['Removed obs'], ['Modified obs'],
      );
      expect(context).toContain('1 new observations added');
      expect(context).toContain('1 observations were removed');
      expect(context).toContain('1 observations were modified');
    });

    it('should truncate to 5 entries with overflow indicator', () => {
      const many = Array.from({ length: 8 }, (_, i) => `Fact ${i + 1}`);
      const context = getMemoryChangeContext(many, [], [], [], [], []);
      expect(context).toContain('Fact 1');
      expect(context).toContain('Fact 5');
      expect(context).toContain('and 3 more');
    });

    it('should include user-friendly guidance', () => {
      const context = getMemoryChangeContext(['test'], [], [], [], [], []);
      expect(context).toContain('bring this up');
    });
  });

  // ── Safe Mode Personality ──────────────────────────────────────

  describe('getSafeModePesonality', () => {
    it('should reference the triggering reason', () => {
      const safe = getSafeModePesonality('memory tampering detected');
      expect(safe).toContain('memory tampering detected');
    });

    it('should mention SAFE MODE', () => {
      const safe = getSafeModePesonality('test reason');
      expect(safe).toContain('SAFE MODE');
    });

    it('should restrict destructive actions', () => {
      const safe = getSafeModePesonality('test reason');
      expect(safe).toContain('NOT execute any destructive actions');
    });

    it('should still reference the Three Laws', () => {
      const safe = getSafeModePesonality('test reason');
      expect(safe).toContain('Three Laws');
    });

    it('should be substantial (not a stub)', () => {
      const safe = getSafeModePesonality('test');
      expect(safe.length).toBeGreaterThan(200);
    });
  });
});
