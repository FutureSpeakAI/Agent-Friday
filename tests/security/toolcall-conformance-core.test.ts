/**
 * Tests for src/main/toolcall-conformance-core.ts — FR-1 (orchestrator seat
 * conformance gate) from dev/friday-orchestrator-integrity-spec.md.
 *
 * Mocks global fetch to simulate Ollama's /api/show and /api/chat responses,
 * since the real gate is exercised against a live Ollama instance (see
 * dev/conformance-runs/ for the real gemma3:4b red-run and gemma4:latest
 * green-run this logic produced).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  runConformance,
  checkModelCapabilities,
  formatReport,
  CANNED_PROMPTS,
  type ConformanceTool,
} from '../../src/main/toolcall-conformance-core';

const TOOLS: ConformanceTool[] = [
  { name: 'get_active_window', description: 'Get active window', parameters: { type: 'object', properties: {} } },
  { name: 'list_windows', description: 'List windows', parameters: { type: 'object', properties: {} } },
];

function jsonResponse(body: unknown, ok = true, status = 200) {
  return {
    ok,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response;
}

describe('conformance-core', () => {
  let fetchSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    fetchSpy = vi.spyOn(globalThis, 'fetch');
  });

  afterEach(() => {
    fetchSpy.mockRestore();
  });

  describe('CANNED_PROMPTS', () => {
    it('has exactly 10 prompts (FR-1)', () => {
      expect(CANNED_PROMPTS).toHaveLength(10);
    });

    it('every prompt has a unique id and a non-empty expected tool', () => {
      const ids = new Set(CANNED_PROMPTS.map((p) => p.id));
      expect(ids.size).toBe(10);
      for (const p of CANNED_PROMPTS) {
        expect(p.prompt.length).toBeGreaterThan(0);
        expect(p.expectedTool.length).toBeGreaterThan(0);
      }
    });
  });

  describe('checkModelCapabilities', () => {
    it('returns the capabilities array from /api/show', async () => {
      fetchSpy.mockResolvedValueOnce(jsonResponse({ capabilities: ['completion', 'tools'] }));
      const caps = await checkModelCapabilities('http://localhost:11434', 'qwen3:8b');
      expect(caps).toEqual(['completion', 'tools']);
    });

    it('returns null when /api/show fails', async () => {
      fetchSpy.mockResolvedValueOnce(jsonResponse({}, false, 500));
      const caps = await checkModelCapabilities('http://localhost:11434', 'unknown-model');
      expect(caps).toBeNull();
    });

    it('returns null when the request throws (Ollama unreachable)', async () => {
      fetchSpy.mockRejectedValueOnce(new Error('ECONNREFUSED'));
      const caps = await checkModelCapabilities('http://localhost:11434', 'gemma3:4b');
      expect(caps).toBeNull();
    });
  });

  describe('runConformance', () => {
    it('fails a model whose Ollama capabilities lack `tools` — reproduces the gemma3:4b red run', async () => {
      // /api/show — no tools capability (the real gemma3:4b response shape)
      fetchSpy.mockResolvedValueOnce(jsonResponse({ capabilities: ['completion', 'vision'] }));
      // Every /api/chat call is rejected the same way Ollama actually rejects it
      fetchSpy.mockResolvedValue(
        jsonResponse({ error: 'registry.ollama.ai/library/gemma3:4b does not support tools' }, false, 400),
      );

      const report = await runConformance({ endpoint: 'http://localhost:11434', model: 'gemma3:4b', tools: TOOLS });

      expect(report.hasNativeToolsCapability).toBe(false);
      expect(report.pass).toBe(false);
      expect(report.passCount).toBe(0);
      expect(report.totalCount).toBe(10);
      for (const r of report.results) {
        expect(r.pass).toBe(false);
        expect(r.error).toContain('does not support tools');
      }
    });

    it('passes a model that returns a structured tool call with clean prose for every prompt', async () => {
      fetchSpy.mockResolvedValueOnce(jsonResponse({ capabilities: ['completion', 'tools'] }));
      fetchSpy.mockResolvedValue(
        jsonResponse({
          message: {
            role: 'assistant',
            content: '',
            tool_calls: [{ function: { name: 'get_active_window', arguments: {} } }],
          },
        }),
      );

      const report = await runConformance({ endpoint: 'http://localhost:11434', model: 'qwen3:8b', tools: TOOLS });

      expect(report.hasNativeToolsCapability).toBe(true);
      expect(report.pass).toBe(true);
      expect(report.passCount).toBe(10);
    });

    it('fails a prompt whose reply has a structured tool call but ALSO narrates a registry tool name in prose', async () => {
      fetchSpy.mockResolvedValueOnce(jsonResponse({ capabilities: ['completion', 'tools'] }));
      fetchSpy.mockResolvedValue(
        jsonResponse({
          message: {
            role: 'assistant',
            // Real tool call present, but the model also narrates a second, fake one in prose —
            // FR-1 requires zero registry names leaking into prose, not just "a tool call happened".
            content: 'Checking now. [list_windows] one moment.',
            tool_calls: [{ function: { name: 'get_active_window', arguments: {} } }],
          },
        }),
      );

      const report = await runConformance({
        endpoint: 'http://localhost:11434',
        model: 'partially-honest-model',
        tools: TOOLS,
      });

      expect(report.pass).toBe(false);
      expect(report.results[0].structuredToolCalls).toHaveLength(1);
      expect(report.results[0].proseLeaks).toContain('[list_windows]');
      expect(report.results[0].pass).toBe(false);
    });

    it('fails a prompt with no tool call at all even if the prose is clean', async () => {
      fetchSpy.mockResolvedValueOnce(jsonResponse({ capabilities: ['completion', 'tools'] }));
      fetchSpy.mockResolvedValue(
        jsonResponse({ message: { role: 'assistant', content: "I'm not sure how to help with that." } }),
      );

      const report = await runConformance({ endpoint: 'http://localhost:11434', model: 'shy-model', tools: TOOLS });

      expect(report.pass).toBe(false);
      expect(report.passCount).toBe(0);
    });

    it('treats an HTTP error from /api/chat as a failed prompt with error detail, not a crash', async () => {
      fetchSpy.mockResolvedValueOnce(jsonResponse({ capabilities: null }));
      fetchSpy.mockResolvedValue(jsonResponse({ error: 'model not found' }, false, 404));

      const report = await runConformance({ endpoint: 'http://localhost:11434', model: 'nonexistent', tools: TOOLS });

      expect(report.pass).toBe(false);
      expect(report.results.every((r) => r.error?.includes('HTTP 404'))).toBe(true);
    });

    it('only runs the prompts it is given (custom prompt subset)', async () => {
      fetchSpy.mockResolvedValueOnce(jsonResponse({ capabilities: ['tools'] }));
      fetchSpy.mockResolvedValue(
        jsonResponse({ message: { role: 'assistant', content: '', tool_calls: [{ function: { name: 'x', arguments: {} } }] } }),
      );

      const report = await runConformance({
        endpoint: 'http://localhost:11434',
        model: 'test-model',
        tools: TOOLS,
        prompts: [{ id: 'only-one', prompt: 'test', expectedTool: 'x' }],
      });

      expect(report.totalCount).toBe(1);
    });
  });

  describe('formatReport', () => {
    it('renders PASS/FAIL and the pass ratio', async () => {
      fetchSpy.mockResolvedValueOnce(jsonResponse({ capabilities: ['tools'] }));
      fetchSpy.mockResolvedValue(
        jsonResponse({ message: { role: 'assistant', content: '', tool_calls: [{ function: { name: 'x', arguments: {} } }] } }),
      );
      const report = await runConformance({
        endpoint: 'http://localhost:11434',
        model: 'test-model',
        tools: TOOLS,
        prompts: [{ id: 'p', prompt: 'test', expectedTool: 'x' }],
      });
      const text = formatReport(report);
      expect(text).toContain('PASS');
      expect(text).toContain('1/1');
      expect(text).toContain('test-model');
    });
  });
});
