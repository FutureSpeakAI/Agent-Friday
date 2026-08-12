/**
 * Tests for tool-provenance.ts — FR-3 (provenance rules) from
 * dev/friday-orchestrator-integrity-spec.md.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { extractUrls, warnIfUngroundedDataClaim } from '../../src/main/tool-provenance';

describe('tool-provenance', () => {
  describe('extractUrls', () => {
    it('extracts a single URL from a tool result', () => {
      const urls = extractUrls('Found event at https://calendar.google.com/event/abc123');
      expect(urls).toEqual(['https://calendar.google.com/event/abc123']);
    });

    it('extracts multiple URLs', () => {
      const urls = extractUrls('See https://a.example.com/1 and https://b.example.com/2 for details.');
      expect(urls).toHaveLength(2);
      expect(urls).toContain('https://a.example.com/1');
      expect(urls).toContain('https://b.example.com/2');
    });

    it('does not include trailing punctuation or closing parens/quotes', () => {
      const urls = extractUrls('(see https://example.com/page). Also "https://example.com/other".');
      expect(urls[0]).toBe('https://example.com/page');
      expect(urls[1]).toBe('https://example.com/other');
    });

    it('returns an empty array when there are no URLs', () => {
      expect(extractUrls('No links here, just a summary of the meeting.')).toEqual([]);
    });

    it('returns an empty array for empty input', () => {
      expect(extractUrls('')).toEqual([]);
    });
  });

  describe('warnIfUngroundedDataClaim', () => {
    let warnSpy: ReturnType<typeof vi.spyOn>;

    beforeEach(() => {
      warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    });

    afterEach(() => {
      warnSpy.mockRestore();
    });

    it('warns when a calendar claim is made with zero tools executed', () => {
      warnIfUngroundedDataClaim('You have a 10am meeting with Robb DeFilippis.', 0);
      expect(warnSpy).toHaveBeenCalledTimes(1);
      expect(warnSpy.mock.calls[0][0]).toContain('[Provenance]');
    });

    it('warns when an email claim is made with zero tools executed', () => {
      warnIfUngroundedDataClaim('Your inbox has 3 priority emails waiting.', 0);
      expect(warnSpy).toHaveBeenCalledTimes(1);
    });

    it('does not warn when at least one tool executed this turn', () => {
      warnIfUngroundedDataClaim('You have a 10am meeting with Robb DeFilippis.', 1);
      expect(warnSpy).not.toHaveBeenCalled();
    });

    it('does not warn on replies with no data-claim language', () => {
      warnIfUngroundedDataClaim('Sure, happy to help with that.', 0);
      expect(warnSpy).not.toHaveBeenCalled();
    });

    it('does not warn on empty text', () => {
      warnIfUngroundedDataClaim('', 0);
      expect(warnSpy).not.toHaveBeenCalled();
    });
  });
});
