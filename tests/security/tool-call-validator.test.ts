/**
 * Tests for tool-call-validator.ts — FR-2 (response validator) from
 * dev/friday-orchestrator-integrity-spec.md. Detects narrated ("pseudo")
 * tool calls: bracket syntax imitating a function call that never executed.
 */
import { describe, it, expect } from 'vitest';
import {
  buildPseudoCallPattern,
  findPseudoToolCalls,
  buildCorrectiveMessage,
  HONEST_FAILURE_MESSAGE,
} from '../../src/main/tool-call-validator';

describe('tool-call-validator', () => {
  describe('findPseudoToolCalls', () => {
    it('detects a named registered tool narrated as bracket syntax', () => {
      const text = 'Let me check that. [query_calendar] You have a 10am meeting.';
      const matches = findPseudoToolCalls(text, ['query_calendar', 'search_email']);
      expect(matches).toHaveLength(1);
      expect(matches[0].raw).toBe('[query_calendar]');
    });

    it('detects a named tool with narrated arguments', () => {
      const text = '[query_trust_graph(robb defilippis)] found a match.';
      const matches = findPseudoToolCalls(text, ['query_trust_graph']);
      expect(matches).toHaveLength(1);
      expect(matches[0].raw).toBe('[query_trust_graph(robb defilippis)]');
    });

    it('catches fabricated tool names via the generic fallback even when not in the registry', () => {
      // This is the actual 2026-08-12 incident shape: the model invented tool
      // names that were never registered anywhere (see V1 findings) — the
      // fallback must not depend on the registry containing the exact name.
      const text = 'Searching now. [search_web(query: "google calendar outage")] Found it.';
      const matches = findPseudoToolCalls(text, ['file_search', 'list_directory']);
      expect(matches).toHaveLength(1);
      expect(matches[0].raw).toContain('search_web');
    });

    it('finds multiple pseudo-calls in one reply', () => {
      const text = '[query_calendar] then [draft_email(to:robb@example.com)] done.';
      const matches = findPseudoToolCalls(text, ['query_calendar', 'draft_email']);
      expect(matches).toHaveLength(2);
    });

    it('does not flag ordinary markdown links', () => {
      const text = 'See [the docs](https://example.com/docs) for more.';
      const matches = findPseudoToolCalls(text, ['query_calendar']);
      expect(matches).toHaveLength(0);
    });

    it('does not flag plain bracketed prose with no parens and not a registered name', () => {
      const text = 'Status: [pending review]';
      const matches = findPseudoToolCalls(text, ['query_calendar']);
      expect(matches).toHaveLength(0);
    });

    it('ignores bracket syntax inside fenced code blocks (risk mitigation from spec section 8)', () => {
      const text = 'Example usage:\n```\n[query_calendar]\n```\nThat is just documentation.';
      const matches = findPseudoToolCalls(text, ['query_calendar']);
      expect(matches).toHaveLength(0);
    });

    it('ignores bracket syntax inside inline code spans', () => {
      const text = 'The tool call looks like `[query_calendar]` in the transcript.';
      const matches = findPseudoToolCalls(text, ['query_calendar']);
      expect(matches).toHaveLength(0);
    });

    it('still flags real narration outside a code block even when the same reply has code elsewhere', () => {
      const text = 'Run `npm test` first. Then [query_calendar] gives you today.';
      const matches = findPseudoToolCalls(text, ['query_calendar']);
      expect(matches).toHaveLength(1);
      expect(matches[0].raw).toBe('[query_calendar]');
    });

    it('returns an empty array for empty or clean text', () => {
      expect(findPseudoToolCalls('', ['query_calendar'])).toEqual([]);
      expect(findPseudoToolCalls('Sure, happy to help with that.', ['query_calendar'])).toEqual([]);
    });

    it('handles an empty registry — generic fallback still applies', () => {
      const matches = findPseudoToolCalls('[anything(at all)]', []);
      expect(matches).toHaveLength(1);
    });
  });

  describe('buildPseudoCallPattern', () => {
    it('escapes regex-special characters in tool names', () => {
      // A tool name containing regex metacharacters must not break the pattern
      // or match unintended text. Use a fresh pattern per test() call since
      // the returned regex carries the 'g' flag and mutates lastIndex.
      expect(() => buildPseudoCallPattern(['weird.tool+name']).test('[weird.tool+name]')).not.toThrow();
      expect(buildPseudoCallPattern(['weird.tool+name']).test('[weird.tool+name]')).toBe(true);
    });

    it('does not let one regex-special tool name match an unrelated bracket', () => {
      // If '.' or '+' were left unescaped, this would incorrectly match.
      const matches = findPseudoToolCalls('[weirdXtoolXname]', ['weird.tool+name']);
      expect(matches).toHaveLength(0);
    });
  });

  describe('buildCorrectiveMessage', () => {
    it('includes the offending example and instructs against narration', () => {
      const msg = buildCorrectiveMessage('[query_calendar]');
      expect(msg).toContain('[query_calendar]');
      expect(msg.toLowerCase()).toContain('never');
    });
  });

  describe('HONEST_FAILURE_MESSAGE', () => {
    it('contains no bracket tool-call syntax itself', () => {
      const matches = findPseudoToolCalls(HONEST_FAILURE_MESSAGE, ['query_calendar']);
      expect(matches).toHaveLength(0);
    });
  });
});
