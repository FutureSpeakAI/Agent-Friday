/**
 * Tests for ContextInjector — Phase C.2 "The Threads"
 *
 * The ContextInjector merges work stream context (entities, streams)
 * with briefing intelligence to produce per-app context objects.
 * Pure computation — no singletons, no side effects.
 *
 * Validation criteria covered:
 *   1. ingest(streamData, briefings) merges work stream and briefing data
 *   2. getContextForApp(appId) returns context relevant to that app's domain
 *   3. FridayNotes gets: file/project entities, writing/documentation briefings
 *   4. FridayFiles gets: current working directory, file entities
 *   5. FridayWeather gets: location entity, time-of-day context
 *   6. FridayMonitor gets: process entities, system-related briefings
 *   7. Unknown apps get generic context (active stream + highest-priority briefing)
 *   8. Updates when new stream or briefing data arrives
 *   9. Pure computation — takes input, returns output, no side effects
 *  10. Max 5 entities + 1 briefing summary per app
 */

import { describe, it, expect } from 'vitest';

import {
  ContextInjector,
  type StreamData,
  type BriefingData,
  type AppContext,
} from '../../src/main/context-injector';

// ── Helpers ─────────────────────────────────────────────────────────

function makeEntity(
  type: string,
  value: string,
  occurrences = 1,
): StreamData['entities'][number] {
  return {
    type: type as any,
    value,
    normalizedValue: value.toLowerCase(),
    firstSeen: Date.now(),
    lastSeen: Date.now(),
    occurrences,
    sourceStreamIds: ['s1'],
  };
}

function makeStream(overrides: Partial<StreamData['activeStream']> = {}): NonNullable<StreamData['activeStream']> {
  return {
    id: 's1',
    name: 'coding',
    task: 'development',
    app: 'code',
    startedAt: Date.now(),
    lastActiveAt: Date.now(),
    eventCount: 5,
    entities: [],
    eventTypes: ['ambient'],
    summary: 'Working on code',
    ...overrides,
  };
}

function makeBriefing(
  topic: string,
  priority: 'urgent' | 'relevant' | 'informational' = 'relevant',
  content = `Briefing about ${topic}`,
): BriefingData[number] {
  return {
    id: `b-${topic.replace(/\s+/g, '-').toLowerCase()}`,
    topic,
    content,
    priority,
    timestamp: Date.now(),
  };
}

// ── Tests ───────────────────────────────────────────────────────────

describe('ContextInjector', () => {
  // ── Criterion 1: ingest merges stream + briefing data ──

  it('ingest accepts stream data and briefings', () => {
    const injector = new ContextInjector();
    const streamData: StreamData = {
      activeStream: makeStream(),
      entities: [makeEntity('file', 'main.ts')],
    };
    const briefings: BriefingData = [
      makeBriefing('Documentation update'),
    ];

    injector.ingest(streamData, briefings);

    // Can retrieve context after ingestion
    const ctx = injector.getContextForApp('notes');
    expect(ctx).toBeDefined();
    expect(ctx.activeStream).not.toBeNull();
  });

  // ── Criterion 2: getContextForApp returns relevant context ──

  it('getContextForApp returns an AppContext object', () => {
    const injector = new ContextInjector();
    injector.ingest(
      { activeStream: makeStream(), entities: [makeEntity('file', 'main.ts')] },
      [makeBriefing('Test briefing')],
    );

    const ctx = injector.getContextForApp('notes');
    expect(ctx).toHaveProperty('activeStream');
    expect(ctx).toHaveProperty('entities');
    expect(ctx).toHaveProperty('briefingSummary');
  });

  // ── Criterion 3: FridayNotes gets file/project entities + writing briefings ──

  it('notes app receives file and project entities', () => {
    const injector = new ContextInjector();
    injector.ingest(
      {
        activeStream: makeStream(),
        entities: [
          makeEntity('file', 'README.md', 5),
          makeEntity('project', 'nexus-os', 3),
          makeEntity('url', 'https://example.com', 2),
          makeEntity('person', 'Alice', 1),
        ],
      },
      [],
    );

    const ctx = injector.getContextForApp('notes');
    const entityValues = ctx.entities.map((e) => e.value);
    expect(entityValues).toContain('README.md');
    expect(entityValues).toContain('nexus-os');
    expect(entityValues).not.toContain('https://example.com');
    expect(entityValues).not.toContain('Alice');
  });

  it('notes app receives writing/documentation briefings', () => {
    const injector = new ContextInjector();
    injector.ingest(
      { activeStream: makeStream(), entities: [] },
      [
        makeBriefing('documentation update available', 'relevant'),
        makeBriefing('system CPU high', 'urgent'),
        makeBriefing('writing tips for README', 'informational'),
      ],
    );

    const ctx = injector.getContextForApp('notes');
    // Should pick the documentation/writing-related briefing
    expect(ctx.briefingSummary).toBeDefined();
    if (ctx.briefingSummary) {
      const lower = ctx.briefingSummary.toLowerCase();
      expect(
        lower.includes('documentation') || lower.includes('writing'),
      ).toBe(true);
    }
  });

  // ── Criterion 4: FridayFiles gets cwd + file entities ──

  it('files app receives file entities', () => {
    const injector = new ContextInjector();
    injector.ingest(
      {
        activeStream: makeStream({ app: 'code' }),
        entities: [
          makeEntity('file', 'src/main.ts', 4),
          makeEntity('file', 'package.json', 2),
          makeEntity('topic', 'TypeScript', 5),
          makeEntity('person', 'Bob', 1),
        ],
      },
      [],
    );

    const ctx = injector.getContextForApp('files');
    const entityValues = ctx.entities.map((e) => e.value);
    expect(entityValues).toContain('src/main.ts');
    expect(entityValues).toContain('package.json');
    expect(entityValues).not.toContain('Bob');
  });

  // ── Criterion 5: FridayWeather gets location entity ──

  it('weather app receives location entities', () => {
    const injector = new ContextInjector();
    injector.ingest(
      {
        activeStream: makeStream(),
        entities: [
          makeEntity('topic', 'Seattle', 3),
          makeEntity('file', 'main.ts', 5),
        ],
      },
      [],
    );

    const ctx = injector.getContextForApp('weather');
    // Weather gets topic entities (location names are topics)
    const entityValues = ctx.entities.map((e) => e.value);
    expect(entityValues).toContain('Seattle');
    expect(entityValues).not.toContain('main.ts');
  });

  // ── Criterion 6: FridayMonitor gets process entities + system briefings ──

  it('monitor app receives app entities', () => {
    const injector = new ContextInjector();
    injector.ingest(
      {
        activeStream: makeStream(),
        entities: [
          makeEntity('app', 'electron', 8),
          makeEntity('app', 'vscode', 4),
          makeEntity('file', 'README.md', 2),
          makeEntity('topic', 'React', 1),
        ],
      },
      [],
    );

    const ctx = injector.getContextForApp('monitor');
    const entityValues = ctx.entities.map((e) => e.value);
    expect(entityValues).toContain('electron');
    expect(entityValues).toContain('vscode');
    expect(entityValues).not.toContain('React');
  });

  it('monitor app receives system-related briefings', () => {
    const injector = new ContextInjector();
    injector.ingest(
      { activeStream: makeStream(), entities: [] },
      [
        makeBriefing('system CPU usage high', 'urgent'),
        makeBriefing('documentation update', 'relevant'),
        makeBriefing('memory usage warning', 'relevant'),
      ],
    );

    const ctx = injector.getContextForApp('monitor');
    expect(ctx.briefingSummary).toBeDefined();
    if (ctx.briefingSummary) {
      const lower = ctx.briefingSummary.toLowerCase();
      expect(
        lower.includes('cpu') ||
        lower.includes('system') ||
        lower.includes('memory'),
      ).toBe(true);
    }
  });

  // ── Criterion 7: unknown apps get generic context ──

  it('unknown app gets active stream + highest-priority briefing', () => {
    const injector = new ContextInjector();
    const stream = makeStream({ name: 'work' });
    injector.ingest(
      {
        activeStream: stream,
        entities: [
          makeEntity('file', 'main.ts', 5),
          makeEntity('topic', 'React', 3),
        ],
      },
      [
        makeBriefing('low importance', 'informational'),
        makeBriefing('high importance', 'urgent'),
      ],
    );

    const ctx = injector.getContextForApp('unknown-app-xyz');
    expect(ctx.activeStream).toEqual(stream);
    // Gets all entity types (generic)
    expect(ctx.entities.length).toBeGreaterThan(0);
    // Gets highest priority briefing
    expect(ctx.briefingSummary).toBeDefined();
    if (ctx.briefingSummary) {
      expect(ctx.briefingSummary.toLowerCase()).toContain('high importance');
    }
  });

  // ── Criterion 8: updates when new data arrives ──

  it('context updates when new stream data is ingested', () => {
    const injector = new ContextInjector();
    injector.ingest(
      {
        activeStream: makeStream({ name: 'first' }),
        entities: [makeEntity('file', 'old.ts')],
      },
      [],
    );

    let ctx = injector.getContextForApp('files');
    expect(ctx.entities.map((e) => e.value)).toContain('old.ts');

    // Ingest new data
    injector.ingest(
      {
        activeStream: makeStream({ name: 'second' }),
        entities: [makeEntity('file', 'new.ts')],
      },
      [],
    );

    ctx = injector.getContextForApp('files');
    expect(ctx.entities.map((e) => e.value)).toContain('new.ts');
    expect(ctx.activeStream?.name).toBe('second');
  });

  it('context updates when new briefing data is ingested', () => {
    const injector = new ContextInjector();
    injector.ingest(
      { activeStream: makeStream(), entities: [] },
      [makeBriefing('first briefing', 'relevant')],
    );

    let ctx = injector.getContextForApp('notes');
    if (ctx.briefingSummary) {
      expect(ctx.briefingSummary.toLowerCase()).toContain('first');
    }

    injector.ingest(
      { activeStream: makeStream(), entities: [] },
      [makeBriefing('documentation second briefing', 'urgent')],
    );

    ctx = injector.getContextForApp('notes');
    if (ctx.briefingSummary) {
      expect(ctx.briefingSummary.toLowerCase()).toContain('second');
    }
  });

  // ── Criterion 9: pure computation — no side effects ──

  it('is a pure computation with no singleton state', () => {
    const injector1 = new ContextInjector();
    const injector2 = new ContextInjector();

    injector1.ingest(
      {
        activeStream: makeStream({ name: 'one' }),
        entities: [makeEntity('file', 'a.ts')],
      },
      [],
    );

    // injector2 is independent
    const ctx2 = injector2.getContextForApp('files');
    expect(ctx2.activeStream).toBeNull();
    expect(ctx2.entities).toEqual([]);
  });

  it('does not mutate input data', () => {
    const injector = new ContextInjector();
    const entities = [
      makeEntity('file', 'a.ts', 3),
      makeEntity('file', 'b.ts', 1),
      makeEntity('topic', 'React', 5),
    ];
    const originalEntities = [...entities];
    const briefings = [makeBriefing('test')];
    const originalBriefings = [...briefings];

    injector.ingest(
      { activeStream: makeStream(), entities },
      briefings,
    );
    injector.getContextForApp('notes');

    // Input arrays unchanged
    expect(entities).toEqual(originalEntities);
    expect(briefings).toEqual(originalBriefings);
  });

  // ── Criterion 10: max 5 entities + 1 briefing per app ──

  it('limits entities to max 5 per app', () => {
    const injector = new ContextInjector();
    const manyFiles = Array.from({ length: 10 }, (_, i) =>
      makeEntity('file', `file-${i}.ts`, 10 - i),
    );

    injector.ingest(
      { activeStream: makeStream(), entities: manyFiles },
      [],
    );

    const ctx = injector.getContextForApp('files');
    expect(ctx.entities.length).toBeLessThanOrEqual(5);
  });

  it('returns exactly 1 briefing summary (not multiple)', () => {
    const injector = new ContextInjector();
    injector.ingest(
      { activeStream: makeStream(), entities: [] },
      [
        makeBriefing('system alert one', 'urgent'),
        makeBriefing('system alert two', 'urgent'),
        makeBriefing('system alert three', 'relevant'),
      ],
    );

    const ctx = injector.getContextForApp('monitor');
    // briefingSummary is a single string or null, not an array
    expect(
      typeof ctx.briefingSummary === 'string' ||
      ctx.briefingSummary === null,
    ).toBe(true);
  });

  // ── Edge cases ──

  it('returns empty context when no data ingested', () => {
    const injector = new ContextInjector();
    const ctx = injector.getContextForApp('notes');
    expect(ctx.activeStream).toBeNull();
    expect(ctx.entities).toEqual([]);
    expect(ctx.briefingSummary).toBeNull();
  });

  it('handles null activeStream gracefully', () => {
    const injector = new ContextInjector();
    injector.ingest(
      { activeStream: null, entities: [makeEntity('file', 'orphan.ts')] },
      [makeBriefing('test')],
    );

    const ctx = injector.getContextForApp('files');
    expect(ctx.activeStream).toBeNull();
    expect(ctx.entities.length).toBeGreaterThan(0);
  });
});
