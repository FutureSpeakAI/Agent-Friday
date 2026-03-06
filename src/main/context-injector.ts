/**
 * Track C, Phase 2: "The Threads" — Cross-App Context Injection
 *
 * Pure computation module that merges work stream context (entities,
 * active stream) with briefing intelligence to produce per-app
 * context objects. Each app gets entities and briefings relevant
 * to its domain, curated to avoid information overload.
 *
 * No singletons, no side effects — this is a computation, not a service.
 *
 * Hermeneutic note: The Threads weave two separate channels of
 * understanding (what the user is doing + what the system thinks
 * is important) into a unified context per app. The whole becomes
 * greater than the sum of its parts.
 */

// ── Types ──────────────────────────────────────────────────────────

interface EntityRef {
  type: 'file' | 'app' | 'url' | 'person' | 'topic' | 'tool' | 'project' | 'channel';
  value: string;
  normalizedValue: string;
  firstSeen: number;
  lastSeen: number;
  occurrences: number;
  sourceStreamIds: string[];
}

interface SerializedStream {
  id: string;
  name: string;
  task: string;
  app: string;
  startedAt: number;
  lastActiveAt: number;
  eventCount: number;
  entities: any[];
  eventTypes: string[];
  summary: string;
}

export interface StreamData {
  activeStream: SerializedStream | null;
  entities: EntityRef[];
}

export interface BriefingData extends Array<{
  id: string;
  topic: string;
  content: string;
  priority: 'urgent' | 'relevant' | 'informational';
  timestamp: number;
}> {}

export interface AppContext {
  activeStream: SerializedStream | null;
  entities: EntityRef[];
  briefingSummary: string | null;
}

// ── App Domain Mappings ────────────────────────────────────────────

/**
 * Maps app IDs to the entity types they care about.
 * Apps not listed here receive all entity types (generic context).
 *
 * This follows the intelligence-router pattern — mapping identifiers
 * to domain-specific filters without importing app code.
 */
const APP_ENTITY_TYPES: Record<string, Set<EntityRef['type']>> = {
  notes:    new Set(['file', 'project', 'topic']),
  docs:     new Set(['file', 'project', 'topic']),
  files:    new Set(['file', 'project']),
  monitor:  new Set(['app']),
  weather:  new Set(['topic']),
  terminal: new Set(['file', 'app']),
  code:     new Set(['file', 'project', 'topic']),
  browser:  new Set(['url', 'topic']),
};

/**
 * Maps app IDs to briefing topic keywords they find relevant.
 * Matching is case-insensitive substring search on briefing topic + content.
 */
const APP_BRIEFING_KEYWORDS: Record<string, string[]> = {
  notes:    ['documentation', 'writing', 'note', 'doc', 'readme', 'text'],
  docs:     ['documentation', 'writing', 'doc', 'text', 'report'],
  files:    ['file', 'storage', 'disk', 'directory', 'folder'],
  monitor:  ['system', 'cpu', 'memory', 'process', 'performance', 'disk', 'network'],
  weather:  ['weather', 'forecast', 'temperature', 'location'],
  terminal: ['command', 'terminal', 'shell', 'process'],
  code:     ['code', 'build', 'test', 'lint', 'compile', 'error', 'bug'],
  browser:  ['web', 'url', 'http', 'api', 'network'],
};

// ── Constants ──────────────────────────────────────────────────────

const MAX_ENTITIES_PER_APP = 5;

const PRIORITY_ORDER: Record<string, number> = {
  urgent: 0,
  relevant: 1,
  informational: 2,
};

// ── ContextInjector ────────────────────────────────────────────────

export class ContextInjector {
  private streamData: StreamData = { activeStream: null, entities: [] };
  private briefings: BriefingData = [];

  /**
   * Ingest new work stream data and briefings.
   * Does not mutate the input arrays.
   */
  ingest(streamData: StreamData, briefings: BriefingData): void {
    this.streamData = {
      activeStream: streamData.activeStream,
      entities: [...streamData.entities],
    };
    this.briefings = [...briefings];
  }

  /**
   * Get curated context for a specific app.
   * Returns entities filtered by the app's domain + the most relevant briefing.
   */
  getContextForApp(appId: string): AppContext {
    const entities = this.filterEntities(appId);
    const briefingSummary = this.selectBriefing(appId);

    return {
      activeStream: this.streamData.activeStream,
      entities,
      briefingSummary,
    };
  }

  // ── Private ────────────────────────────────────────────────────

  private filterEntities(appId: string): EntityRef[] {
    const allowedTypes = APP_ENTITY_TYPES[appId];

    let filtered: EntityRef[];
    if (allowedTypes) {
      filtered = this.streamData.entities.filter((e) =>
        allowedTypes.has(e.type),
      );
    } else {
      // Generic: all entity types
      filtered = [...this.streamData.entities];
    }

    // Sort by occurrences descending, then limit
    return filtered
      .sort((a, b) => b.occurrences - a.occurrences)
      .slice(0, MAX_ENTITIES_PER_APP);
  }

  private selectBriefing(appId: string): string | null {
    if (this.briefings.length === 0) return null;

    const keywords = APP_BRIEFING_KEYWORDS[appId];

    if (keywords) {
      // Find the highest-priority briefing matching this app's domain
      const matched = this.briefings
        .filter((b) => {
          const haystack = `${b.topic} ${b.content}`.toLowerCase();
          return keywords.some((kw) => haystack.includes(kw));
        })
        .sort(
          (a, b) =>
            (PRIORITY_ORDER[a.priority] ?? 2) -
            (PRIORITY_ORDER[b.priority] ?? 2),
        );

      if (matched.length > 0) {
        return matched[0].content;
      }
    }

    // Fallback: highest-priority briefing regardless of domain
    const sorted = [...this.briefings].sort(
      (a, b) =>
        (PRIORITY_ORDER[a.priority] ?? 2) -
        (PRIORITY_ORDER[b.priority] ?? 2),
    );
    return sorted[0].content;
  }
}
