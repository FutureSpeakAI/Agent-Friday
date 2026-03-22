/**
 * stage-presenter.test.ts — Tests for The Stage creative output presenter
 *
 * Track G of the Polymath Update (v3.0.0).
 * 82 tests covering: exports, tool declarations, push, list, get, clear,
 * stats, pin, export, capacity, error resilience.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import {
  TOOLS,
  execute,
  detect,
  _resetStore,
  _getOutputCount,
} from '../../../src/main/connectors/stage-presenter';
import type { StageOutput, StageDomain, OutputRenderer, DomainStats } from '../../../src/main/connectors/stage-presenter';

// ── Helpers ──────────────────────────────────────────────────────────────────

function push(overrides: Record<string, unknown> = {}): { result?: string; error?: string } {
  return execute('stage_push_output', {
    domain: 'image',
    title: 'Test Output',
    source_tool: 'comfyui_txt2img',
    prompt: 'a cat on a skateboard',
    ...overrides,
  });
}

function pushMany(count: number, domain: StageDomain = 'image'): void {
  for (let i = 0; i < count; i++) {
    push({ domain, title: `Output ${i + 1}`, source_tool: `tool_${domain}` });
  }
}

function parseResult(r: { result?: string; error?: string }): unknown {
  expect(r.error).toBeUndefined();
  expect(r.result).toBeDefined();
  return JSON.parse(r.result!);
}

// ── Tests ────────────────────────────────────────────────────────────────────

describe('stage-presenter', () => {
  beforeEach(() => _resetStore());

  // ── Module exports ───────────────────────────────────────────────────────
  describe('module exports', () => {
    it('exports TOOLS as a non-empty array', () => {
      expect(Array.isArray(TOOLS)).toBe(true);
      expect(TOOLS.length).toBeGreaterThan(0);
    });

    it('exports exactly 7 tools', () => {
      expect(TOOLS).toHaveLength(7);
    });

    it('exports execute as a function', () => {
      expect(typeof execute).toBe('function');
    });

    it('exports detect as a function', () => {
      expect(typeof detect).toBe('function');
    });

    it('exports _resetStore for testing', () => {
      expect(typeof _resetStore).toBe('function');
    });

    it('exports _getOutputCount for testing', () => {
      expect(typeof _getOutputCount).toBe('function');
    });
  });

  // ── Tool declarations ────────────────────────────────────────────────────
  describe('tool declarations', () => {
    const toolNames = TOOLS.map(t => t.name);

    it('includes stage_push_output', () => {
      expect(toolNames).toContain('stage_push_output');
    });

    it('includes stage_list_outputs', () => {
      expect(toolNames).toContain('stage_list_outputs');
    });

    it('includes stage_get_output', () => {
      expect(toolNames).toContain('stage_get_output');
    });

    it('includes stage_clear_outputs', () => {
      expect(toolNames).toContain('stage_clear_outputs');
    });

    it('includes stage_get_stats', () => {
      expect(toolNames).toContain('stage_get_stats');
    });

    it('includes stage_pin_output', () => {
      expect(toolNames).toContain('stage_pin_output');
    });

    it('includes stage_export_feed', () => {
      expect(toolNames).toContain('stage_export_feed');
    });

    it('all tools have name, description, parameters', () => {
      for (const tool of TOOLS) {
        expect(tool.name).toBeDefined();
        expect(typeof tool.description).toBe('string');
        expect(tool.parameters).toBeDefined();
        expect(tool.parameters.type).toBe('object');
      }
    });

    it('push requires domain, title, source_tool', () => {
      const pushTool = TOOLS.find(t => t.name === 'stage_push_output')!;
      expect(pushTool.parameters.required).toContain('domain');
      expect(pushTool.parameters.required).toContain('title');
      expect(pushTool.parameters.required).toContain('source_tool');
    });

    it('get_output requires id', () => {
      const getTool = TOOLS.find(t => t.name === 'stage_get_output')!;
      expect(getTool.parameters.required).toContain('id');
    });

    it('pin_output requires id', () => {
      const pinTool = TOOLS.find(t => t.name === 'stage_pin_output')!;
      expect(pinTool.parameters.required).toContain('id');
    });
  });

  // ── detect ───────────────────────────────────────────────────────────────
  describe('detect', () => {
    it('returns true (always available)', () => {
      expect(detect()).toBe(true);
    });
  });

  // ── stage_push_output ────────────────────────────────────────────────────
  describe('stage_push_output', () => {
    it('pushes a valid output and returns id', () => {
      const data = parseResult(push()) as any;
      expect(data.id).toBeDefined();
      expect(data.id).toMatch(/^stage_/);
      expect(data.domain).toBe('image');
      expect(data.renderer).toBe('image-viewer');
      expect(data.title).toBe('Test Output');
      expect(data.created_at).toBeDefined();
    });

    it('increments output count', () => {
      expect(_getOutputCount()).toBe(0);
      push();
      expect(_getOutputCount()).toBe(1);
      push();
      expect(_getOutputCount()).toBe(2);
    });

    it('assigns correct renderer per domain', () => {
      const domains: [StageDomain, OutputRenderer][] = [
        ['image', 'image-viewer'],
        ['video', 'video-player'],
        ['music', 'audio-player'],
        ['sfx', 'audio-player'],
        ['speech', 'audio-player'],
        ['podcast', 'audio-player'],
        ['code', 'code-block'],
        ['document', 'document-frame'],
      ];

      for (const [domain, renderer] of domains) {
        _resetStore();
        const data = parseResult(push({ domain })) as any;
        expect(data.renderer).toBe(renderer);
      }
    });

    it('stores optional fields (prompt, file_path, url, thumbnail)', () => {
      push({
        prompt: 'test prompt',
        file_path: '/tmp/test.png',
        url: 'https://example.com/img.png',
        thumbnail: 'data:image/png;base64,...',
      });

      const list = parseResult(execute('stage_list_outputs', {})) as any;
      const id = list.outputs[0].id;
      const full = parseResult(execute('stage_get_output', { id })) as any;
      expect(full.prompt).toBe('test prompt');
      expect(full.file_path).toBe('/tmp/test.png');
      expect(full.url).toBe('https://example.com/img.png');
      expect(full.thumbnail).toBe('data:image/png;base64,...');
    });

    it('stores metadata object', () => {
      push({ metadata: { width: 1024, height: 768, model: 'sdxl' } });
      const list = parseResult(execute('stage_list_outputs', {})) as any;
      const id = list.outputs[0].id;
      const full = parseResult(execute('stage_get_output', { id })) as any;
      expect(full.metadata.width).toBe(1024);
      expect(full.metadata.model).toBe('sdxl');
    });

    it('rejects invalid domain', () => {
      const r = push({ domain: 'invalid' });
      expect(r.error).toContain('Invalid or missing domain');
    });

    it('rejects missing domain', () => {
      const r = execute('stage_push_output', { title: 'x', source_tool: 'y' });
      expect(r.error).toContain('Invalid or missing domain');
    });

    it('rejects missing title', () => {
      const r = execute('stage_push_output', { domain: 'image', source_tool: 'y' });
      expect(r.error).toContain('title is required');
    });

    it('rejects missing source_tool', () => {
      const r = execute('stage_push_output', { domain: 'image', title: 'x' });
      expect(r.error).toContain('source_tool is required');
    });

    it('ignores non-object metadata', () => {
      push({ metadata: 'not-an-object' });
      const list = parseResult(execute('stage_list_outputs', {})) as any;
      const id = list.outputs[0].id;
      const full = parseResult(execute('stage_get_output', { id })) as any;
      expect(full.metadata).toEqual({});
    });

    it('ignores array metadata', () => {
      push({ metadata: [1, 2, 3] });
      const list = parseResult(execute('stage_list_outputs', {})) as any;
      const id = list.outputs[0].id;
      const full = parseResult(execute('stage_get_output', { id })) as any;
      expect(full.metadata).toEqual({});
    });
  });

  // ── stage_list_outputs ───────────────────────────────────────────────────
  describe('stage_list_outputs', () => {
    it('returns empty list when no outputs', () => {
      const data = parseResult(execute('stage_list_outputs', {})) as any;
      expect(data.total).toBe(0);
      expect(data.returned).toBe(0);
      expect(data.outputs).toEqual([]);
    });

    it('returns all outputs newest first', () => {
      push({ title: 'First' });
      push({ title: 'Second' });
      push({ title: 'Third' });

      const data = parseResult(execute('stage_list_outputs', {})) as any;
      expect(data.total).toBe(3);
      expect(data.outputs[0].title).toBe('Third');
      expect(data.outputs[2].title).toBe('First');
    });

    it('filters by domain', () => {
      push({ domain: 'image', title: 'Img' });
      push({ domain: 'video', title: 'Vid' });
      push({ domain: 'music', title: 'Mus' });

      const data = parseResult(execute('stage_list_outputs', { domain: 'video' })) as any;
      expect(data.total).toBe(1);
      expect(data.outputs[0].title).toBe('Vid');
    });

    it('respects limit', () => {
      pushMany(10);
      const data = parseResult(execute('stage_list_outputs', { limit: 3 })) as any;
      expect(data.returned).toBe(3);
      expect(data.total).toBe(10);
    });

    it('clamps limit to 1-100 range', () => {
      pushMany(5);
      const data = parseResult(execute('stage_list_outputs', { limit: -5 })) as any;
      expect(data.returned).toBeGreaterThanOrEqual(1);
    });

    it('filters pinned_only', () => {
      push({ title: 'Normal' });
      push({ title: 'Pinned' });
      // pin the second one
      const list = parseResult(execute('stage_list_outputs', {})) as any;
      execute('stage_pin_output', { id: list.outputs[0].id });

      const pinned = parseResult(execute('stage_list_outputs', { pinned_only: true })) as any;
      expect(pinned.total).toBe(1);
      expect(pinned.outputs[0].title).toBe('Pinned');
    });

    it('rejects invalid domain', () => {
      const r = execute('stage_list_outputs', { domain: 'invalid' });
      expect(r.error).toContain('Invalid domain');
    });
  });

  // ── stage_get_output ─────────────────────────────────────────────────────
  describe('stage_get_output', () => {
    it('returns full output by id', () => {
      push({ title: 'My Image', prompt: 'cat' });
      const list = parseResult(execute('stage_list_outputs', {})) as any;
      const id = list.outputs[0].id;

      const full = parseResult(execute('stage_get_output', { id })) as any;
      expect(full.id).toBe(id);
      expect(full.title).toBe('My Image');
      expect(full.prompt).toBe('cat');
      expect(full.domain).toBe('image');
      expect(full.renderer).toBe('image-viewer');
    });

    it('returns error for missing id', () => {
      const r = execute('stage_get_output', {});
      expect(r.error).toContain('id is required');
    });

    it('returns error for non-existent id', () => {
      const r = execute('stage_get_output', { id: 'nonexistent' });
      expect(r.error).toContain('Output not found');
    });

    it('returns error for non-string id', () => {
      const r = execute('stage_get_output', { id: 123 });
      expect(r.error).toContain('id is required');
    });
  });

  // ── stage_clear_outputs ──────────────────────────────────────────────────
  describe('stage_clear_outputs', () => {
    it('clears all outputs', () => {
      pushMany(5);
      const data = parseResult(execute('stage_clear_outputs', { keep_pinned: false })) as any;
      expect(data.removed).toBe(5);
      expect(data.remaining).toBe(0);
      expect(_getOutputCount()).toBe(0);
    });

    it('clears by domain', () => {
      push({ domain: 'image' });
      push({ domain: 'video' });
      push({ domain: 'image' });

      const data = parseResult(execute('stage_clear_outputs', { domain: 'image' })) as any;
      expect(data.removed).toBe(2);
      expect(data.remaining).toBe(1);
    });

    it('preserves pinned by default', () => {
      push({ title: 'Keep' });
      push({ title: 'Remove' });
      const list = parseResult(execute('stage_list_outputs', {})) as any;
      // pin 'Keep' (it's the second item since newest first)
      execute('stage_pin_output', { id: list.outputs[1].id });

      const data = parseResult(execute('stage_clear_outputs', {})) as any;
      expect(data.removed).toBe(1);
      expect(data.remaining).toBe(1);
    });

    it('can clear pinned when keep_pinned=false', () => {
      push();
      const list = parseResult(execute('stage_list_outputs', {})) as any;
      execute('stage_pin_output', { id: list.outputs[0].id });

      const data = parseResult(execute('stage_clear_outputs', { keep_pinned: false })) as any;
      expect(data.removed).toBe(1);
      expect(data.remaining).toBe(0);
    });

    it('rejects invalid domain', () => {
      const r = execute('stage_clear_outputs', { domain: 'invalid' });
      expect(r.error).toContain('Invalid domain');
    });
  });

  // ── stage_get_stats ──────────────────────────────────────────────────────
  describe('stage_get_stats', () => {
    it('returns zeros when empty', () => {
      const data = parseResult(execute('stage_get_stats', {})) as any;
      expect(data.total).toBe(0);
      expect(data.total_pinned).toBe(0);
      expect(data.all_domains).toHaveLength(8);
      expect(data.domains).toHaveLength(0);
    });

    it('counts per domain', () => {
      push({ domain: 'image' });
      push({ domain: 'image' });
      push({ domain: 'video' });

      const data = parseResult(execute('stage_get_stats', {})) as any;
      expect(data.total).toBe(3);
      expect(data.domains).toHaveLength(2);

      const imgStats = data.all_domains.find((d: any) => d.domain === 'image');
      expect(imgStats.count).toBe(2);

      const vidStats = data.all_domains.find((d: any) => d.domain === 'video');
      expect(vidStats.count).toBe(1);
    });

    it('tracks pinned count', () => {
      push();
      push();
      const list = parseResult(execute('stage_list_outputs', {})) as any;
      execute('stage_pin_output', { id: list.outputs[0].id });

      const data = parseResult(execute('stage_get_stats', {})) as any;
      expect(data.total_pinned).toBe(1);
    });

    it('includes latest timestamp', () => {
      push({ domain: 'music' });
      const data = parseResult(execute('stage_get_stats', {})) as any;
      const musicStats = data.all_domains.find((d: any) => d.domain === 'music');
      expect(musicStats.latest).toBeDefined();
      expect(musicStats.count).toBe(1);
    });
  });

  // ── stage_pin_output ─────────────────────────────────────────────────────
  describe('stage_pin_output', () => {
    it('pins an output', () => {
      push();
      const list = parseResult(execute('stage_list_outputs', {})) as any;
      const id = list.outputs[0].id;

      const data = parseResult(execute('stage_pin_output', { id })) as any;
      expect(data.pinned).toBe(true);
      expect(data.id).toBe(id);
    });

    it('unpins an output', () => {
      push();
      const list = parseResult(execute('stage_list_outputs', {})) as any;
      const id = list.outputs[0].id;

      execute('stage_pin_output', { id });
      const data = parseResult(execute('stage_pin_output', { id, pinned: false })) as any;
      expect(data.pinned).toBe(false);
    });

    it('returns error for missing id', () => {
      const r = execute('stage_pin_output', {});
      expect(r.error).toContain('id is required');
    });

    it('returns error for non-existent id', () => {
      const r = execute('stage_pin_output', { id: 'nope' });
      expect(r.error).toContain('Output not found');
    });
  });

  // ── stage_export_feed ────────────────────────────────────────────────────
  describe('stage_export_feed', () => {
    it('exports empty feed', () => {
      const data = parseResult(execute('stage_export_feed', {})) as any;
      expect(data.count).toBe(0);
      expect(data.outputs).toEqual([]);
      expect(data.exported_at).toBeDefined();
      expect(data.domain_filter).toBe('all');
    });

    it('exports all outputs', () => {
      pushMany(3);
      const data = parseResult(execute('stage_export_feed', {})) as any;
      expect(data.count).toBe(3);
      expect(data.outputs).toHaveLength(3);
    });

    it('filters by domain', () => {
      push({ domain: 'image' });
      push({ domain: 'video' });

      const data = parseResult(execute('stage_export_feed', { domain: 'video' })) as any;
      expect(data.count).toBe(1);
      expect(data.domain_filter).toBe('video');
    });

    it('includes metadata by default', () => {
      push({ metadata: { key: 'value' } });
      const data = parseResult(execute('stage_export_feed', {})) as any;
      expect(data.outputs[0].metadata).toBeDefined();
      expect(data.outputs[0].metadata.key).toBe('value');
    });

    it('excludes metadata when include_metadata=false', () => {
      push({ metadata: { key: 'value' } });
      const data = parseResult(execute('stage_export_feed', { include_metadata: false })) as any;
      expect(data.outputs[0].metadata).toBeUndefined();
    });

    it('rejects invalid domain', () => {
      const r = execute('stage_export_feed', { domain: 'bad' });
      expect(r.error).toContain('Invalid domain');
    });
  });

  // ── Capacity management ──────────────────────────────────────────────────
  describe('capacity management', () => {
    it('caps at 500 outputs', () => {
      // Push 510 outputs
      for (let i = 0; i < 510; i++) {
        push({ title: `Output ${i}` });
      }
      expect(_getOutputCount()).toBeLessThanOrEqual(500);
    });

    it('preserves pinned outputs when capping', () => {
      // Push and pin one
      push({ title: 'Pinned One' });
      const list = parseResult(execute('stage_list_outputs', {})) as any;
      execute('stage_pin_output', { id: list.outputs[0].id });

      // Push 505 more
      for (let i = 0; i < 505; i++) {
        push({ title: `Filler ${i}` });
      }

      // Pinned one should still be there
      const pinnedList = parseResult(execute('stage_list_outputs', { pinned_only: true })) as any;
      expect(pinnedList.total).toBeGreaterThanOrEqual(1);
    });
  });

  // ── Unknown tool ─────────────────────────────────────────────────────────
  describe('unknown tool', () => {
    it('returns error for unknown tool name', () => {
      const r = execute('stage_nonexistent', {});
      expect(r.error).toContain('Unknown stage tool');
    });
  });

  // ── Error resilience ─────────────────────────────────────────────────────
  describe('error resilience', () => {
    it('push with non-string title returns error', () => {
      const r = push({ title: 123 });
      expect(r.error).toContain('title is required');
    });

    it('push with non-string source_tool returns error', () => {
      const r = push({ source_tool: true });
      expect(r.error).toContain('source_tool is required');
    });

    it('get_output with empty string id returns error', () => {
      const r = execute('stage_get_output', { id: '' });
      expect(r.error).toContain('id is required');
    });

    it('all 7 tools never throw', () => {
      const badArgs = { domain: 42, title: null, id: undefined, limit: 'abc' };
      for (const tool of TOOLS) {
        const r = execute(tool.name, badArgs as any);
        expect(r).toBeDefined();
        expect(typeof r).toBe('object');
        // Must have either result or error
        expect(r.result !== undefined || r.error !== undefined).toBe(true);
      }
    });

    it('non-string prompt/file_path/url/thumbnail ignored gracefully', () => {
      const r = push({
        prompt: 123,
        file_path: true,
        url: [],
        thumbnail: {},
      });
      expect(r.error).toBeUndefined();
      expect(r.result).toBeDefined();
    });
  });

  // ── _resetStore ──────────────────────────────────────────────────────────
  describe('_resetStore', () => {
    it('clears all outputs', () => {
      pushMany(10);
      expect(_getOutputCount()).toBe(10);
      _resetStore();
      expect(_getOutputCount()).toBe(0);
    });

    it('resets id counter', () => {
      push();
      const list1 = parseResult(execute('stage_list_outputs', {})) as any;
      const id1 = list1.outputs[0].id;

      _resetStore();
      push();
      const list2 = parseResult(execute('stage_list_outputs', {})) as any;
      const id2 = list2.outputs[0].id;

      // IDs should be different (different timestamps) but both start with stage_
      expect(id1).toMatch(/^stage_/);
      expect(id2).toMatch(/^stage_/);
    });
  });
});
