/**
 * Tests for context-graph.ts — Track III Phase 2: Context Graph.
 * Validates work stream creation, entity extraction, entity tracking,
 * stream management, context generation, and graph lifecycle.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ── Mock context stream ──────────────────────────────────────────────
let registeredListener: ((event: any) => void) | null = null;
const mockUnsubscribe = vi.fn();

vi.mock('../../src/main/context-stream', () => ({
  contextStream: {
    on: vi.fn((listener: (event: any) => void) => {
      registeredListener = listener;
      return mockUnsubscribe;
    }),
    getSnapshot: vi.fn().mockReturnValue({
      activeApp: '',
      windowTitle: '',
      inferredTask: '',
      focusStreak: 0,
      currentMood: 'neutral',
      moodConfidence: 0,
      energyLevel: 0.5,
      lastClipboardType: '',
      lastClipboardPreview: '',
      recentToolCalls: [],
      recentNotifications: [],
      activeWorkStream: '',
      lastUpdated: 0,
    }),
  },
}));

import { contextStream } from '../../src/main/context-stream';
import { ContextGraph } from '../../src/main/context-graph';

const mockContextStream = vi.mocked(contextStream);

// ── Helpers ──────────────────────────────────────────────────────────

function makeEvent(overrides: Partial<any> = {}): any {
  return {
    id: `ctx-${Date.now()}-${Math.random()}`,
    type: 'ambient',
    timestamp: Date.now(),
    source: 'test',
    summary: 'test event',
    data: {},
    ...overrides,
  };
}

function createGraph(config?: Partial<any>): ContextGraph {
  return new ContextGraph(config);
}

// We need to access the class directly for testing.
// Export it from the module (the singleton is also exported but we want fresh instances).

describe('Context Graph — Track III Phase 2', () => {
  let graph: ContextGraph;

  beforeEach(() => {
    vi.clearAllMocks();
    registeredListener = null;
    graph = createGraph();
  });

  afterEach(() => {
    graph.stop();
  });

  // ── Lifecycle ────────────────────────────────────────────────────

  describe('Lifecycle', () => {
    it('registers a listener on start', () => {
      graph.start();
      expect(registeredListener).toBeTypeOf('function');
    });

    it('does not double-register on repeated start', () => {
      graph.start();
      graph.start();
      // contextStream.on should only be called once for this graph instance
      // (Note: the mock may have been called by other instances in beforeEach,
      //  but the guard in start() prevents the second call for this instance)
      const callsBefore = mockContextStream.on.mock.calls.length;
      graph.start(); // third call — should still be guarded
      expect(mockContextStream.on.mock.calls.length).toBe(callsBefore);
    });

    it('unsubscribes on stop', () => {
      graph.start();
      graph.stop();
      expect(mockUnsubscribe).toHaveBeenCalled();
    });

    it('clears state on stop', () => {
      graph.start();
      // Simulate some events
      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));
      expect(graph.getStatus().streamCount).toBe(1);

      graph.stop();
      expect(graph.getStatus().streamCount).toBe(0);
      expect(graph.getStatus().entityCount).toBe(0);
      expect(graph.getActiveStream()).toBeNull();
    });

    it('can restart after stop', () => {
      graph.start();
      graph.stop();
      graph.start();
      expect(registeredListener).toBeTypeOf('function');
    });
  });

  // ── Work Stream Creation ────────────────────────────────────────

  describe('Work Stream Creation', () => {
    beforeEach(() => {
      graph.start();
    });

    it('creates a work stream on first ambient event', () => {
      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'index.ts — nexus-os' },
      }));

      const active = graph.getActiveStream();
      expect(active).not.toBeNull();
      expect(active!.app).toBe('VS Code');
      expect(active!.task).toBe('coding');
      expect(active!.name).toContain('Coding');
      expect(active!.name).toContain('VS Code');
    });

    it('includes project name in stream name from window title', () => {
      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts — nexus-os' },
      }));

      const active = graph.getActiveStream();
      expect(active!.name).toContain('nexus-os');
    });

    it('creates new stream when app changes', () => {
      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));

      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'Chrome', inferredTask: 'browsing', windowTitle: 'Google' },
      }));

      const streams = graph.getRecentStreams(10);
      expect(streams.length).toBe(2);
      expect(graph.getActiveStream()!.app).toBe('Chrome');
    });

    it('creates new stream when task changes (same app)', () => {
      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'Chrome', inferredTask: 'browsing', windowTitle: 'Google' },
      }));

      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'Chrome', inferredTask: 'communicating', windowTitle: 'Gmail' },
      }));

      const streams = graph.getRecentStreams(10);
      expect(streams.length).toBe(2);
    });

    it('does NOT create new stream when only title changes', () => {
      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'file1.ts — project' },
      }));

      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'file2.ts — project' },
      }));

      const streams = graph.getRecentStreams(10);
      expect(streams.length).toBe(1);
    });

    it('assigns non-ambient events to active stream', () => {
      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));

      registeredListener!(makeEvent({
        type: 'tool-invoke',
        data: { toolName: 'read_file', success: true },
        summary: 'Tool: read_file (ok)',
      }));

      registeredListener!(makeEvent({
        type: 'clipboard',
        data: { contentType: 'code', preview: 'const x = 1;' },
      }));

      const active = graph.getActiveStream();
      expect(active!.eventCount).toBe(3);
      expect(active!.eventTypes.has('tool-invoke')).toBe(true);
      expect(active!.eventTypes.has('clipboard')).toBe(true);
    });

    it('increments event count on each event', () => {
      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));

      for (let i = 0; i < 5; i++) {
        registeredListener!(makeEvent({
          type: 'tool-invoke',
          data: { toolName: `tool-${i}`, success: true },
        }));
      }

      expect(graph.getActiveStream()!.eventCount).toBe(6); // 1 ambient + 5 tools
    });
  });

  // ── Entity Extraction ───────────────────────────────────────────

  describe('Entity Extraction', () => {
    beforeEach(() => {
      graph.start();
      // Create an active stream first
      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts — nexus-os' },
      }));
    });

    it('extracts app entities from ambient events', () => {
      const entities = graph.getEntitiesByType('app');
      expect(entities.some(e => e.value === 'VS Code')).toBe(true);
    });

    it('extracts tool entities from tool-invoke events', () => {
      registeredListener!(makeEvent({
        type: 'tool-invoke',
        data: { toolName: 'search_web', success: true, durationMs: 100 },
      }));

      const toolEntities = graph.getEntitiesByType('tool');
      expect(toolEntities.some(e => e.value === 'search_web')).toBe(true);
    });

    it('extracts file paths from clipboard content', () => {
      registeredListener!(makeEvent({
        type: 'clipboard',
        data: { contentType: 'text', preview: 'src/main/context-graph.ts' },
        summary: 'Clipboard: text',
      }));

      const fileEntities = graph.getEntitiesByType('file');
      expect(fileEntities.some(e => e.value.includes('context-graph.ts'))).toBe(true);
    });

    it('extracts URLs from text', () => {
      registeredListener!(makeEvent({
        type: 'clipboard',
        data: { contentType: 'url', preview: 'https://github.com/user/repo' },
        summary: 'Clipboard: url',
      }));

      const urlEntities = graph.getEntitiesByType('url');
      expect(urlEntities.some(e => e.value.includes('github.com'))).toBe(true);
    });

    it('extracts project name from window title separator pattern', () => {
      const projectEntities = graph.getEntitiesByType('project');
      expect(projectEntities.some(e => e.value === 'nexus-os')).toBe(true);
    });

    it('extracts app from notification events', () => {
      registeredListener!(makeEvent({
        type: 'notification',
        data: { app: 'Slack', title: 'New message from John', body: 'Hello world' },
        summary: 'Slack: New message from John',
      }));

      const appEntities = graph.getEntitiesByType('app');
      expect(appEntities.some(e => e.value === 'Slack')).toBe(true);
    });

    it('extracts person from communication events', () => {
      registeredListener!(makeEvent({
        type: 'communication',
        data: { channel: 'email', person: 'John Smith', from: 'john@co.com' },
        summary: 'Email from John Smith',
      }));

      const personEntities = graph.getEntitiesByType('person');
      expect(personEntities.some(e => e.value === 'John Smith')).toBe(true);
    });

    it('extracts channel from communication events', () => {
      registeredListener!(makeEvent({
        type: 'communication',
        data: { channel: '#engineering', person: 'Jane' },
        summary: 'Message in #engineering',
      }));

      const channelEntities = graph.getEntitiesByType('channel');
      expect(channelEntities.some(e => e.value === '#engineering')).toBe(true);
    });

    it('extracts topics from user-input events', () => {
      registeredListener!(makeEvent({
        type: 'user-input',
        data: { topic: 'typescript generics' },
        summary: 'User: typescript generics',
      }));

      const topicEntities = graph.getEntitiesByType('topic');
      expect(topicEntities.length).toBeGreaterThan(0);
    });

    it('handles git events with repo and files', () => {
      registeredListener!(makeEvent({
        type: 'git',
        data: {
          repo: 'nexus-os',
          branch: 'main',
          files: ['src/index.ts', 'src/app.ts'],
        },
        summary: 'Git: 2 files committed',
      }));

      const projectEntities = graph.getEntitiesByType('project');
      expect(projectEntities.some(e => e.normalizedValue === 'nexus-os')).toBe(true);

      const fileEntities = graph.getEntitiesByType('file');
      expect(fileEntities.some(e => e.value === 'src/index.ts')).toBe(true);
    });

    it('does not extract entities from empty data', () => {
      const before = graph.getStatus().entityCount;

      registeredListener!(makeEvent({
        type: 'system',
        data: {},
        summary: '',
      }));

      // Might add some entities from the summary or not - mainly check no crash
      expect(graph.getStatus().entityCount).toBeGreaterThanOrEqual(before);
    });
  });

  // ── Entity Tracking ─────────────────────────────────────────────

  describe('Entity Tracking', () => {
    beforeEach(() => {
      graph.start();
      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));
    });

    it('increments occurrence count on repeated entity references', () => {
      for (let i = 0; i < 3; i++) {
        registeredListener!(makeEvent({
          type: 'tool-invoke',
          data: { toolName: 'read_file', success: true },
        }));
      }

      const toolEntities = graph.getEntitiesByType('tool');
      const readFile = toolEntities.find(e => e.value === 'read_file');
      expect(readFile).toBeDefined();
      expect(readFile!.occurrences).toBeGreaterThanOrEqual(3);
    });

    it('tracks entities across multiple streams', () => {
      // Stream 1: VS Code
      registeredListener!(makeEvent({
        type: 'tool-invoke',
        data: { toolName: 'search_web', success: true },
      }));

      // Stream 2: Chrome
      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'Chrome', inferredTask: 'browsing', windowTitle: 'Google' },
      }));
      registeredListener!(makeEvent({
        type: 'tool-invoke',
        data: { toolName: 'search_web', success: true },
      }));

      const entity = graph.getEntity('tool', 'search_web');
      expect(entity).not.toBeNull();
      expect(entity!.sourceStreamIds.length).toBe(2);
    });

    it('getEntity returns null for unknown entity', () => {
      expect(graph.getEntity('tool', 'nonexistent')).toBeNull();
    });

    it('getTopEntities returns entities sorted by relevance', () => {
      // Create several entities with different frequencies
      for (let i = 0; i < 5; i++) {
        registeredListener!(makeEvent({
          type: 'tool-invoke',
          data: { toolName: 'frequent_tool', success: true },
        }));
      }
      registeredListener!(makeEvent({
        type: 'tool-invoke',
        data: { toolName: 'rare_tool', success: true },
      }));

      const top = graph.getTopEntities(20);
      expect(top.length).toBeGreaterThan(0);

      // frequent_tool should appear before rare_tool
      const frequentIdx = top.findIndex(e => e.value === 'frequent_tool');
      const rareIdx = top.findIndex(e => e.value === 'rare_tool');
      if (frequentIdx !== -1 && rareIdx !== -1) {
        expect(frequentIdx).toBeLessThan(rareIdx);
      }
    });

    it('getActiveEntities returns recently seen entities', () => {
      const now = Date.now();

      registeredListener!(makeEvent({
        type: 'tool-invoke',
        timestamp: now,
        data: { toolName: 'recent_tool', success: true },
      }));

      const active = graph.getActiveEntities(10 * 60 * 1000); // 10 min window
      expect(active.some(e => e.value === 'recent_tool')).toBe(true);
    });
  });

  // ── Entity Relationships ────────────────────────────────────────

  describe('Entity Relationships', () => {
    beforeEach(() => {
      graph.start();
    });

    it('finds related entities that co-occur in same stream', () => {
      // Create a stream with multiple entities
      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts — nexus-os' },
      }));
      registeredListener!(makeEvent({
        type: 'tool-invoke',
        data: { toolName: 'edit_block', success: true },
      }));
      registeredListener!(makeEvent({
        type: 'tool-invoke',
        data: { toolName: 'read_file', success: true },
      }));

      const cluster = graph.getRelatedEntities('app', 'VS Code');
      expect(cluster).not.toBeNull();
      expect(cluster!.relatedEntities.length).toBeGreaterThan(0);
    });

    it('returns null for unknown entity', () => {
      expect(graph.getRelatedEntities('app', 'nonexistent')).toBeNull();
    });
  });

  // ── Stream Management ───────────────────────────────────────────

  describe('Stream Management', () => {
    beforeEach(() => {
      graph.start();
    });

    it('getRecentStreams returns streams sorted by recency', () => {
      registeredListener!(makeEvent({
        type: 'ambient',
        timestamp: Date.now() - 5000,
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'a.ts' },
      }));

      registeredListener!(makeEvent({
        type: 'ambient',
        timestamp: Date.now(),
        data: { activeApp: 'Chrome', inferredTask: 'browsing', windowTitle: 'Google' },
      }));

      const streams = graph.getRecentStreams(10);
      expect(streams.length).toBe(2);
      expect(streams[0].app).toBe('Chrome'); // Most recent first
    });

    it('getStreamsByTask filters correctly', () => {
      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'a.ts' },
      }));
      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'Chrome', inferredTask: 'browsing', windowTitle: 'Google' },
      }));
      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'Cursor', inferredTask: 'coding', windowTitle: 'b.ts' },
      }));

      const codingStreams = graph.getStreamsByTask('coding');
      expect(codingStreams.length).toBe(2);
      expect(codingStreams.every(s => s.task === 'coding')).toBe(true);
    });

    it('getStream returns correct stream by ID', () => {
      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));

      const active = graph.getActiveStream();
      expect(active).not.toBeNull();

      const fetched = graph.getStream(active!.id);
      expect(fetched).not.toBeNull();
      expect(fetched!.id).toBe(active!.id);
    });

    it('getStream returns null for unknown ID', () => {
      expect(graph.getStream('nonexistent')).toBeNull();
    });

    it('prunes old streams when max exceeded', () => {
      const smallGraph = createGraph({ maxWorkStreams: 3 });
      smallGraph.start();

      const apps = ['VS Code', 'Chrome', 'Slack', 'Notion', 'Terminal'];
      for (const app of apps) {
        registeredListener!(makeEvent({
          type: 'ambient',
          data: { activeApp: app, inferredTask: 'testing', windowTitle: 'test' },
        }));
      }

      // Should keep max 3 + maybe current (pruning is slightly lenient for active)
      expect(smallGraph.getStatus().streamCount).toBeLessThanOrEqual(4);
      smallGraph.stop();
    });
  });

  // ── Snapshot ────────────────────────────────────────────────────

  describe('Snapshot', () => {
    beforeEach(() => {
      graph.start();
    });

    it('returns complete snapshot', () => {
      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));

      const snap = graph.getSnapshot();
      expect(snap.activeStream).not.toBeNull();
      expect(snap.recentStreams.length).toBeGreaterThan(0);
      expect(snap.streamCount).toBe(1);
      expect(snap.entityCount).toBeGreaterThan(0);
    });

    it('returns null activeStream when no stream', () => {
      const snap = graph.getSnapshot();
      expect(snap.activeStream).toBeNull();
      expect(snap.streamCount).toBe(0);
    });

    it('includes top and active entities', () => {
      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));
      registeredListener!(makeEvent({
        type: 'tool-invoke',
        data: { toolName: 'read_file', success: true },
      }));

      const snap = graph.getSnapshot();
      expect(snap.topEntities.length).toBeGreaterThan(0);
      expect(snap.activeEntities.length).toBeGreaterThan(0);
    });
  });

  // ── Context String Generation ───────────────────────────────────

  describe('Context String Generation', () => {
    beforeEach(() => {
      graph.start();
    });

    it('returns empty when no streams exist', () => {
      expect(graph.getContextString()).toBe('');
    });

    it('includes Work Context header', () => {
      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));

      const ctx = graph.getContextString();
      expect(ctx).toContain('## Work Context');
    });

    it('includes active stream info', () => {
      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));

      const ctx = graph.getContextString();
      expect(ctx).toContain('Active');
      expect(ctx).toContain('VS Code');
    });

    it('includes recent work streams', () => {
      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));
      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'Chrome', inferredTask: 'browsing', windowTitle: 'Google' },
      }));

      const ctx = graph.getContextString();
      expect(ctx).toContain('Recent work');
    });

    it('includes key entities', () => {
      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));
      registeredListener!(makeEvent({
        type: 'tool-invoke',
        data: { toolName: 'read_file', success: true },
      }));

      const ctx = graph.getContextString();
      expect(ctx).toContain('Key entities');
    });
  });

  // ── Prompt Context (Budget-Aware) ──────────────────────────────

  describe('Prompt Context', () => {
    beforeEach(() => {
      graph.start();
    });

    it('returns empty when no active stream', () => {
      expect(graph.getPromptContext()).toBe('');
    });

    it('returns [WORK] prefixed string', () => {
      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));

      const ctx = graph.getPromptContext();
      expect(ctx).toMatch(/^\[WORK\]/);
    });

    it('includes stream name', () => {
      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));

      const ctx = graph.getPromptContext();
      expect(ctx).toContain('VS Code');
    });
  });

  // ── Status ──────────────────────────────────────────────────────

  describe('Status', () => {
    it('returns correct initial status', () => {
      const status = graph.getStatus();
      expect(status.streamCount).toBe(0);
      expect(status.entityCount).toBe(0);
      expect(status.totalEventsProcessed).toBe(0);
      expect(status.activeStreamId).toBeNull();
    });

    it('tracks total events processed', () => {
      graph.start();

      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));
      registeredListener!(makeEvent({
        type: 'tool-invoke',
        data: { toolName: 'test', success: true },
      }));
      registeredListener!(makeEvent({
        type: 'clipboard',
        data: { contentType: 'text', preview: 'hello' },
      }));

      expect(graph.getStatus().totalEventsProcessed).toBe(3);
    });

    it('includes memory estimate', () => {
      graph.start();
      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));

      const status = graph.getStatus();
      expect(status.memoryEstimateKb).toBeGreaterThanOrEqual(0);
    });
  });

  // ── Config ──────────────────────────────────────────────────────

  describe('Config', () => {
    it('returns default config', () => {
      const config = graph.getConfig();
      expect(config.maxWorkStreams).toBe(50);
      expect(config.maxTotalEntities).toBe(500);
      expect(config.streamTimeoutMs).toBe(30 * 60 * 1000);
    });

    it('accepts custom config', () => {
      const custom = createGraph({ maxWorkStreams: 10, maxTotalEntities: 100 });
      const config = custom.getConfig();
      expect(config.maxWorkStreams).toBe(10);
      expect(config.maxTotalEntities).toBe(100);
      // Defaults still present
      expect(config.streamTimeoutMs).toBe(30 * 60 * 1000);
      custom.stop();
    });
  });

  // ── Entity Pruning ──────────────────────────────────────────────

  describe('Entity Pruning', () => {
    it('prunes entities when max exceeded', () => {
      const smallGraph = createGraph({ maxTotalEntities: 10, maxEntitiesPerStream: 10 });
      smallGraph.start();

      // Create active stream
      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));

      // Generate many tool entities to exceed limit
      for (let i = 0; i < 25; i++) {
        registeredListener!(makeEvent({
          type: 'tool-invoke',
          data: { toolName: `tool-${i}`, success: true },
        }));
      }

      // Entity count should be capped
      expect(smallGraph.getStatus().entityCount).toBeLessThanOrEqual(15);
      smallGraph.stop();
    });
  });

  // ── Stream Summary ──────────────────────────────────────────────

  describe('Stream Summary', () => {
    beforeEach(() => {
      graph.start();
    });

    it('generates summary with task and app', () => {
      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));

      const active = graph.getActiveStream();
      expect(active!.summary).toContain('Coding');
      expect(active!.summary).toContain('VS Code');
    });

    it('includes tools in summary when present', () => {
      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));
      registeredListener!(makeEvent({
        type: 'tool-invoke',
        data: { toolName: 'edit_block', success: true },
      }));

      const active = graph.getActiveStream();
      expect(active!.summary).toContain('edit_block');
    });
  });

  // ── Error Resilience ────────────────────────────────────────────

  describe('Error Resilience', () => {
    it('survives null event data gracefully', () => {
      graph.start();
      expect(() => {
        registeredListener!(makeEvent({
          type: 'ambient',
          data: { activeApp: null, inferredTask: undefined, windowTitle: '' },
        }));
      }).not.toThrow();
    });

    it('survives undefined event fields', () => {
      graph.start();
      expect(() => {
        registeredListener!(makeEvent({
          type: 'tool-invoke',
          data: {},
          summary: undefined,
        }));
      }).not.toThrow();
    });

    it('survives very long text without crashing', () => {
      graph.start();
      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));

      expect(() => {
        registeredListener!(makeEvent({
          type: 'user-input',
          data: { topic: 'a'.repeat(10000) },
          summary: 'b'.repeat(5000),
        }));
      }).not.toThrow();
    });

    it('survives malformed event types', () => {
      graph.start();
      expect(() => {
        registeredListener!(makeEvent({
          type: 'unknown-type',
          data: { foo: 'bar' },
        }));
      }).not.toThrow();
    });
  });

  // ── Project Extraction ──────────────────────────────────────────

  describe('Project Extraction from Window Titles', () => {
    beforeEach(() => {
      graph.start();
    });

    it('extracts project from "file — project" pattern', () => {
      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'main.ts — my-project' },
      }));

      const projects = graph.getEntitiesByType('project');
      expect(projects.some(e => e.value === 'my-project')).toBe(true);
    });

    it('extracts project from "file - project - VS Code" pattern', () => {
      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'index.ts - nexus-os - VS Code' },
      }));

      const projects = graph.getEntitiesByType('project');
      expect(projects.some(e => e.normalizedValue === 'nexus-os')).toBe(true);
    });

    it('handles title with no project', () => {
      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'Chrome', inferredTask: 'browsing', windowTitle: 'Google' },
      }));

      // Should not crash; project may or may not be extracted
      expect(graph.getActiveStream()).not.toBeNull();
    });
  });

  // ── Multiple Entity Types Per Event ─────────────────────────────

  describe('Multiple Entity Types Per Event', () => {
    beforeEach(() => {
      graph.start();
      registeredListener!(makeEvent({
        type: 'ambient',
        data: { activeApp: 'VS Code', inferredTask: 'coding', windowTitle: 'test.ts' },
      }));
    });

    it('calendar events extract people and topics', () => {
      registeredListener!(makeEvent({
        type: 'calendar',
        data: {
          title: 'Sprint Planning',
          attendees: ['Alice', 'Bob'],
        },
        summary: 'Calendar: Sprint Planning',
      }));

      const people = graph.getEntitiesByType('person');
      expect(people.some(e => e.value === 'Alice')).toBe(true);
      expect(people.some(e => e.value === 'Bob')).toBe(true);
    });

    it('git events extract project and files', () => {
      registeredListener!(makeEvent({
        type: 'git',
        data: {
          repo: 'nexus-os',
          branch: 'feature/context-graph',
          files: ['src/context-graph.ts', 'tests/context-graph.test.ts'],
        },
        summary: 'Git: committed 2 files',
      }));

      const projects = graph.getEntitiesByType('project');
      const files = graph.getEntitiesByType('file');
      expect(projects.some(e => e.normalizedValue === 'nexus-os')).toBe(true);
      expect(files.some(e => e.value === 'src/context-graph.ts')).toBe(true);
    });
  });
});
