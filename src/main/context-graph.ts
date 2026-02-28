/**
 * Track III, Phase 2: Context Graph — Work Stream Intelligence
 *
 * The Prefrontal Cortex. Clusters raw context events from the unified
 * stream into meaningful work streams, tracks entities across events,
 * and provides structured work context for downstream consumers.
 *
 * Architecture:
 *   ContextStream (Phase 1) → ContextGraph (Phase 2) → Tool Router (Phase 3)
 *
 * Work Streams are automatically created when the user shifts focus
 * to a new app/task combination. Events are assigned to the active
 * work stream and entities (files, apps, people, topics) are extracted
 * and cross-referenced.
 *
 * cLaw Gate: In-memory only — no persistence. Entity extraction is
 * purely structural (pattern matching), not AI-based. No new data
 * is generated; only existing event data is organized.
 */

import { contextStream, type ContextEvent, type ContextEventType } from './context-stream';

// ── Types ────────────────────────────────────────────────────────────

export interface WorkStream {
  id: string;
  name: string;                        // e.g. "Coding in VS Code — nexus-os"
  task: string;                        // Inferred task: "coding", "browsing", etc.
  app: string;                         // Primary app
  startedAt: number;
  lastActiveAt: number;
  eventCount: number;
  entities: EntityRef[];               // Entities discovered in this stream
  eventTypes: Set<ContextEventType>;   // Event types that appeared
  summary: string;                     // Auto-generated summary
}

export interface EntityRef {
  type: EntityType;
  value: string;                       // The entity itself
  normalizedValue: string;             // Lowercased, trimmed for dedup
  firstSeen: number;
  lastSeen: number;
  occurrences: number;
  sourceStreamIds: string[];           // Which work streams reference this entity
}

export type EntityType =
  | 'file'           // File path or filename
  | 'app'            // Application name
  | 'person'         // Person mention
  | 'topic'          // Inferred topic or keyword
  | 'url'            // URL or domain
  | 'tool'           // Tool name (from tool-invoke events)
  | 'project'        // Project name (from window title, git, etc.)
  | 'channel';       // Communication channel (slack channel, email thread)

export interface EntityCluster {
  entity: EntityRef;
  relatedEntities: EntityRef[];        // Entities that co-occur with this one
  relatedStreams: string[];            // Stream IDs where this entity appears
  strength: number;                    // 0-1 how central this entity is
}

export interface ContextGraphSnapshot {
  activeStream: WorkStream | null;
  recentStreams: WorkStream[];         // Last 10 streams (most recent first)
  topEntities: EntityRef[];            // Top 15 entities by recency × frequency
  activeEntities: EntityRef[];         // Entities seen in last 5 minutes
  streamCount: number;
  entityCount: number;
}

export interface ContextGraphConfig {
  maxWorkStreams: number;              // Max streams to track (default: 50)
  maxEntitiesPerStream: number;       // Max entities per stream (default: 100)
  maxTotalEntities: number;           // Max total entities (default: 500)
  streamTimeoutMs: number;            // Inactivity before stream auto-closes (default: 30 min)
  entityDecayMs: number;              // Entity relevance decay (default: 2 hours)
}

export interface ContextGraphStatus {
  activeStreamId: string | null;
  streamCount: number;
  entityCount: number;
  totalEventsProcessed: number;
  memoryEstimateKb: number;
}

// ── Constants ────────────────────────────────────────────────────────

const DEFAULT_CONFIG: ContextGraphConfig = {
  maxWorkStreams: 50,
  maxEntitiesPerStream: 100,
  maxTotalEntities: 500,
  streamTimeoutMs: 30 * 60 * 1000,     // 30 minutes
  entityDecayMs: 2 * 60 * 60 * 1000,   // 2 hours
};

const STREAM_ID_PREFIX = 'ws';

// Entity extraction patterns
const FILE_PATTERN = /(?:^|\s|["'`(])([A-Za-z]:\\[^\s"'`),]+|\/(?:Users|home|tmp|var|etc|src|lib|app)[^\s"'`),]+|[a-zA-Z0-9_.-]+(?:\/[a-zA-Z0-9_.-]+)+\.(?:ts|tsx|js|jsx|py|rs|go|java|cpp|c|h|css|html|json|yaml|yml|md|txt|toml|sql|sh|bat|ps1)|[a-zA-Z0-9_-]+\.(?:ts|tsx|js|jsx|py|rs|go|java|cpp|c|h|css|html|json|yaml|yml|md|txt|toml|sql|sh|bat|ps1))/g;
const URL_PATTERN = /https?:\/\/[^\s"'`),]+/g;
const PROJECT_PATTERN = /(?:^|\s|[—–-])\s*([a-zA-Z][a-zA-Z0-9_-]{1,40}(?:\/[a-zA-Z][a-zA-Z0-9_-]{1,40})?)\s*(?:$|[—–\s])/;

// ── ContextGraph Class ───────────────────────────────────────────────

export class ContextGraph {
  private config: ContextGraphConfig;
  private streams: Map<string, WorkStream> = new Map();
  private entities: Map<string, EntityRef> = new Map(); // key = type:normalizedValue
  private activeStreamId: string | null = null;
  private totalEventsProcessed = 0;
  private streamCounter = 0;
  private unsubscribe: (() => void) | null = null;

  constructor(config?: Partial<ContextGraphConfig>) {
    this.config = { ...DEFAULT_CONFIG, ...config };
  }

  // ── Lifecycle ────────────────────────────────────────────────────

  /**
   * Start listening to the context stream.
   * Call after context stream and bridge are initialized.
   */
  start(): void {
    if (this.unsubscribe) return; // Already started

    this.unsubscribe = contextStream.on((event) => {
      try {
        this.processEvent(event);
      } catch {
        // Don't let graph errors break the stream pipeline
      }
    });
  }

  /**
   * Stop listening and clear all graph state.
   */
  stop(): void {
    if (this.unsubscribe) {
      this.unsubscribe();
      this.unsubscribe = null;
    }
    this.streams.clear();
    this.entities.clear();
    this.activeStreamId = null;
    this.totalEventsProcessed = 0;
    this.streamCounter = 0;
  }

  // ── Event Processing ─────────────────────────────────────────────

  private processEvent(event: ContextEvent): void {
    this.totalEventsProcessed++;

    // 1. Determine if we need a new work stream
    let newStreamCreated = false;
    if (event.type === 'ambient') {
      newStreamCreated = this.handleAmbientEvent(event);
    }

    // 2. Assign event to active stream
    //    Skip increment when handleAmbientEvent already created the stream
    //    with eventCount=1 (avoids double-counting the ambient event).
    const stream = this.getActiveStream();
    if (stream && !newStreamCreated) {
      stream.eventCount++;
      stream.lastActiveAt = event.timestamp;
      stream.eventTypes.add(event.type);
    }

    // 3. Extract entities from event
    const extracted = this.extractEntities(event);
    for (const entity of extracted) {
      this.trackEntity(entity, stream?.id);
      if (stream && stream.entities.length < this.config.maxEntitiesPerStream) {
        const existing = stream.entities.find(
          e => e.type === entity.type && e.normalizedValue === entity.normalizedValue,
        );
        if (existing) {
          existing.occurrences++;
          existing.lastSeen = event.timestamp;
        } else {
          stream.entities.push({ ...entity });
        }
      }
    }

    // 4. Update active stream summary
    if (stream) {
      this.updateStreamSummary(stream);
    }

    // 5. Prune if needed
    this.pruneIfNeeded();

    // 6. Update context stream snapshot with active work stream
    this.updateContextSnapshot();
  }

  private handleAmbientEvent(event: ContextEvent): boolean {
    const app = String(event.data.activeApp || '');
    const task = String(event.data.inferredTask || '');
    const title = String(event.data.windowTitle || '');

    if (!app) return false;

    const current = this.getActiveStream();

    // Decide if this represents a new work stream
    const isNewStream =
      !current ||
      current.app !== app ||
      (current.task !== task && task !== '');

    if (isNewStream) {
      // Create new work stream
      const streamId = `${STREAM_ID_PREFIX}-${++this.streamCounter}-${Date.now()}`;
      const projectHint = this.extractProjectFromTitle(title);

      const name = projectHint
        ? `${task || 'Using'} ${app} — ${projectHint}`
        : `${task || 'Using'} ${app}`;

      const stream: WorkStream = {
        id: streamId,
        name: this.capitalize(name),
        task: task || 'unknown',
        app,
        startedAt: event.timestamp,
        lastActiveAt: event.timestamp,
        eventCount: 1,
        entities: [],
        eventTypes: new Set<ContextEventType>(['ambient']),
        summary: `Started ${task || 'using'} ${app}`,
      };

      this.streams.set(streamId, stream);
      this.activeStreamId = streamId;

      // Track app entity
      this.trackEntity({
        type: 'app',
        value: app,
        normalizedValue: app.toLowerCase(),
        firstSeen: event.timestamp,
        lastSeen: event.timestamp,
        occurrences: 1,
        sourceStreamIds: [streamId],
      }, streamId);

      // Track project entity if found
      if (projectHint) {
        this.trackEntity({
          type: 'project',
          value: projectHint,
          normalizedValue: projectHint.toLowerCase(),
          firstSeen: event.timestamp,
          lastSeen: event.timestamp,
          occurrences: 1,
          sourceStreamIds: [streamId],
        }, streamId);
      }

      return true; // New stream was created
    } else if (current) {
      // Same stream, just update
      current.lastActiveAt = event.timestamp;
    }

    return false; // No new stream created
  }

  // ── Entity Extraction ────────────────────────────────────────────

  private extractEntities(event: ContextEvent): EntityRef[] {
    const now = event.timestamp;
    const refs: EntityRef[] = [];
    const seen = new Set<string>();

    const addEntity = (type: EntityType, value: string) => {
      const normalized = value.toLowerCase().trim();
      const key = `${type}:${normalized}`;
      if (seen.has(key) || !normalized || normalized.length < 2) return;
      seen.add(key);

      refs.push({
        type,
        value: value.trim(),
        normalizedValue: normalized,
        firstSeen: now,
        lastSeen: now,
        occurrences: 1,
        sourceStreamIds: [],
      });
    };

    // Extract from event data based on type
    switch (event.type) {
      case 'ambient':
        if (event.data.activeApp) addEntity('app', String(event.data.activeApp));
        if (event.data.windowTitle) {
          this.extractFromText(String(event.data.windowTitle), addEntity);
          const project = this.extractProjectFromTitle(String(event.data.windowTitle));
          if (project) addEntity('project', project);
        }
        break;

      case 'clipboard':
        if (event.data.preview) {
          this.extractFromText(String(event.data.preview), addEntity);
        }
        break;

      case 'notification':
        if (event.data.app) addEntity('app', String(event.data.app));
        if (event.data.title) {
          this.extractFromText(String(event.data.title), addEntity);
        }
        break;

      case 'tool-invoke':
        if (event.data.toolName) addEntity('tool', String(event.data.toolName));
        break;

      case 'user-input':
        if (event.data.topic) {
          this.extractFromText(String(event.data.topic), addEntity);
        }
        break;

      case 'communication':
        if (event.data.channel) addEntity('channel', String(event.data.channel));
        if (event.data.person) addEntity('person', String(event.data.person));
        if (event.data.from) addEntity('person', String(event.data.from));
        if (event.data.to) addEntity('person', String(event.data.to));
        break;

      case 'git':
        if (event.data.branch) addEntity('project', String(event.data.branch));
        if (event.data.repo) addEntity('project', String(event.data.repo));
        if (event.data.files && Array.isArray(event.data.files)) {
          for (const f of event.data.files.slice(0, 10)) {
            addEntity('file', String(f));
          }
        }
        break;

      case 'calendar':
        if (event.data.title) {
          this.extractFromText(String(event.data.title), addEntity);
        }
        if (event.data.attendees && Array.isArray(event.data.attendees)) {
          for (const a of event.data.attendees.slice(0, 10)) {
            addEntity('person', String(a));
          }
        }
        break;

      case 'screen-text':
        if (event.data.text) {
          this.extractFromText(String(event.data.text).slice(0, 500), addEntity);
        }
        break;
    }

    // Extract from summary (all event types)
    if (event.summary) {
      this.extractFromText(event.summary, addEntity);
    }

    return refs;
  }

  private extractFromText(
    text: string,
    addEntity: (type: EntityType, value: string) => void,
  ): void {
    // Extract file paths
    const fileMatches = text.match(FILE_PATTERN);
    if (fileMatches) {
      for (const m of fileMatches.slice(0, 5)) {
        addEntity('file', m.trim());
      }
    }

    // Extract URLs
    const urlMatches = text.match(URL_PATTERN);
    if (urlMatches) {
      for (const m of urlMatches.slice(0, 3)) {
        addEntity('url', m);
      }
    }

    // Extract topic keywords from user input/window titles
    // Only from shorter text (likely titles/topics, not code snippets)
    if (text.length < 200) {
      const words = text
        .replace(/[^a-zA-Z0-9\s-]/g, ' ')
        .split(/\s+/)
        .filter(w => w.length > 3 && w.length < 30)
        .filter(w => !STOP_WORDS.has(w.toLowerCase()));

      // Only extract meaningful multi-word phrases or specific keywords
      if (words.length >= 1 && words.length <= 8) {
        const topic = words.slice(0, 4).join(' ');
        if (topic.length > 4) {
          addEntity('topic', topic);
        }
      }
    }
  }

  private extractProjectFromTitle(title: string): string {
    if (!title) return '';

    // Common patterns: "file.ts — project-name", "project-name - VS Code"
    const separators = [' — ', ' – ', ' - ', ' | '];
    for (const sep of separators) {
      const parts = title.split(sep);
      if (parts.length >= 2) {
        // The project name is usually the last segment for editors
        const last = parts[parts.length - 1].trim();
        // Skip if it's the app name itself
        if (/^(VS Code|Visual Studio|Cursor|Chrome|Firefox|Slack|Notion)$/i.test(last)) {
          // Try the second-to-last part
          if (parts.length >= 3) {
            const prev = parts[parts.length - 2].trim();
            if (prev.length > 1 && prev.length < 50 && !prev.includes('.')) {
              return prev;
            }
          }
          continue;
        }
        if (last.length > 1 && last.length < 50) {
          return last;
        }
      }
    }

    return '';
  }

  // ── Entity Tracking ──────────────────────────────────────────────

  private trackEntity(entity: EntityRef, streamId?: string): void {
    const key = `${entity.type}:${entity.normalizedValue}`;
    const existing = this.entities.get(key);

    if (existing) {
      existing.occurrences++;
      existing.lastSeen = Math.max(existing.lastSeen, entity.lastSeen);
      if (streamId && !existing.sourceStreamIds.includes(streamId)) {
        existing.sourceStreamIds.push(streamId);
        // Cap stream references
        if (existing.sourceStreamIds.length > 20) {
          existing.sourceStreamIds.shift();
        }
      }
    } else {
      if (this.entities.size >= this.config.maxTotalEntities) {
        this.pruneEntities();
      }
      const newEntity = { ...entity };
      if (streamId) newEntity.sourceStreamIds = [streamId];
      this.entities.set(key, newEntity);
    }
  }

  // ── Stream Management ────────────────────────────────────────────

  getActiveStream(): WorkStream | null {
    if (!this.activeStreamId) return null;
    return this.streams.get(this.activeStreamId) ?? null;
  }

  getStream(streamId: string): WorkStream | null {
    return this.streams.get(streamId) ?? null;
  }

  getRecentStreams(limit = 10): WorkStream[] {
    return Array.from(this.streams.values())
      .sort((a, b) => b.lastActiveAt - a.lastActiveAt)
      .slice(0, limit);
  }

  getStreamsByTask(task: string): WorkStream[] {
    return Array.from(this.streams.values())
      .filter(s => s.task === task)
      .sort((a, b) => b.lastActiveAt - a.lastActiveAt);
  }

  // ── Entity Queries ───────────────────────────────────────────────

  getEntity(type: EntityType, value: string): EntityRef | null {
    return this.entities.get(`${type}:${value.toLowerCase().trim()}`) ?? null;
  }

  getEntitiesByType(type: EntityType, limit = 20): EntityRef[] {
    return Array.from(this.entities.values())
      .filter(e => e.type === type)
      .sort((a, b) => b.lastSeen - a.lastSeen)
      .slice(0, limit);
  }

  getTopEntities(limit = 15): EntityRef[] {
    const now = Date.now();
    return Array.from(this.entities.values())
      .map(e => ({
        entity: e,
        score: this.entityRelevanceScore(e, now),
      }))
      .sort((a, b) => b.score - a.score)
      .slice(0, limit)
      .map(x => x.entity);
  }

  getActiveEntities(windowMs = 5 * 60 * 1000): EntityRef[] {
    const cutoff = Date.now() - windowMs;
    return Array.from(this.entities.values())
      .filter(e => e.lastSeen >= cutoff)
      .sort((a, b) => b.lastSeen - a.lastSeen);
  }

  /**
   * Find entities that co-occur with the given entity across work streams.
   */
  getRelatedEntities(type: EntityType, value: string, limit = 10): EntityCluster | null {
    const key = `${type}:${value.toLowerCase().trim()}`;
    const target = this.entities.get(key);
    if (!target) return null;

    // Find all entities in the same streams
    const relatedMap = new Map<string, { entity: EntityRef; coOccurrences: number }>();

    for (const streamId of target.sourceStreamIds) {
      const stream = this.streams.get(streamId);
      if (!stream) continue;

      for (const entity of stream.entities) {
        const eKey = `${entity.type}:${entity.normalizedValue}`;
        if (eKey === key) continue; // Skip self

        const globalEntity = this.entities.get(eKey);
        if (!globalEntity) continue;

        const existing = relatedMap.get(eKey);
        if (existing) {
          existing.coOccurrences++;
        } else {
          relatedMap.set(eKey, { entity: globalEntity, coOccurrences: 1 });
        }
      }
    }

    const related = Array.from(relatedMap.values())
      .sort((a, b) => b.coOccurrences - a.coOccurrences)
      .slice(0, limit)
      .map(x => x.entity);

    return {
      entity: target,
      relatedEntities: related,
      relatedStreams: [...target.sourceStreamIds],
      strength: Math.min(1, target.occurrences / 20),
    };
  }

  // ── Snapshot ──────────────────────────────────────────────────────

  getSnapshot(): ContextGraphSnapshot {
    return {
      activeStream: this.serializeStream(this.getActiveStream()),
      recentStreams: this.getRecentStreams(10).map(s => this.serializeStream(s)!),
      topEntities: this.getTopEntities(15),
      activeEntities: this.getActiveEntities(),
      streamCount: this.streams.size,
      entityCount: this.entities.size,
    };
  }

  // ── Context Generation ───────────────────────────────────────────

  /**
   * Full markdown context for system prompt injection.
   */
  getContextString(): string {
    const active = this.getActiveStream();
    if (!active && this.streams.size === 0) return '';

    const lines: string[] = ['## Work Context'];

    // Active work stream
    if (active) {
      const durationMin = Math.round((Date.now() - active.startedAt) / 60_000);
      lines.push(`- **Active**: ${active.name} (${durationMin} min, ${active.eventCount} events)`);
      if (active.summary) {
        lines.push(`  ${active.summary}`);
      }

      // Active stream entities
      const streamEntities = active.entities
        .sort((a, b) => b.occurrences - a.occurrences)
        .slice(0, 8);
      if (streamEntities.length > 0) {
        const entityParts = streamEntities.map(
          e => `${e.type}:${e.value}`,
        );
        lines.push(`  Entities: ${entityParts.join(', ')}`);
      }
    }

    // Recent streams (last 3 besides active)
    const recent = this.getRecentStreams(4)
      .filter(s => s.id !== this.activeStreamId)
      .slice(0, 3);
    if (recent.length > 0) {
      lines.push('- Recent work:');
      for (const s of recent) {
        const ago = Math.round((Date.now() - s.lastActiveAt) / 60_000);
        lines.push(`  • [${ago}m ago] ${s.name} (${s.eventCount} events)`);
      }
    }

    // Top entities across all streams
    const topEntities = this.getTopEntities(10);
    if (topEntities.length > 0) {
      const grouped = new Map<EntityType, string[]>();
      for (const e of topEntities) {
        const group = grouped.get(e.type) || [];
        group.push(e.value);
        grouped.set(e.type, group);
      }

      lines.push('- Key entities:');
      for (const [type, values] of grouped) {
        lines.push(`  ${type}: ${values.join(', ')}`);
      }
    }

    return lines.join('\n');
  }

  /**
   * Shorter budget-aware context for prompt injection.
   */
  getPromptContext(): string {
    const active = this.getActiveStream();
    if (!active) return '';

    const parts: string[] = [];
    parts.push(`stream: ${active.name}`);

    const durationMin = Math.round((Date.now() - active.startedAt) / 60_000);
    if (durationMin > 1) {
      parts.push(`${durationMin}min`);
    }

    // Top 3 entities from active stream
    const entities = active.entities
      .sort((a, b) => b.occurrences - a.occurrences)
      .slice(0, 3)
      .map(e => e.value);
    if (entities.length > 0) {
      parts.push(`entities: ${entities.join(', ')}`);
    }

    return `[WORK] ${parts.join(' | ')}`;
  }

  // ── Status ───────────────────────────────────────────────────────

  getStatus(): ContextGraphStatus {
    return {
      activeStreamId: this.activeStreamId,
      streamCount: this.streams.size,
      entityCount: this.entities.size,
      totalEventsProcessed: this.totalEventsProcessed,
      memoryEstimateKb: this.estimateMemory(),
    };
  }

  // ── Config ───────────────────────────────────────────────────────

  getConfig(): ContextGraphConfig {
    return { ...this.config };
  }

  // ── Private Helpers ──────────────────────────────────────────────

  private updateStreamSummary(stream: WorkStream): void {
    const parts: string[] = [];

    // Task + app
    parts.push(`${this.capitalize(stream.task)} in ${stream.app}`);

    // Top entities
    const topFiles = stream.entities
      .filter(e => e.type === 'file')
      .sort((a, b) => b.occurrences - a.occurrences)
      .slice(0, 2);
    if (topFiles.length > 0) {
      parts.push(`files: ${topFiles.map(f => this.basename(f.value)).join(', ')}`);
    }

    const topTools = stream.entities
      .filter(e => e.type === 'tool')
      .sort((a, b) => b.occurrences - a.occurrences)
      .slice(0, 2);
    if (topTools.length > 0) {
      parts.push(`tools: ${topTools.map(t => t.value).join(', ')}`);
    }

    stream.summary = parts.join(' — ');
  }

  private entityRelevanceScore(entity: EntityRef, now: number): number {
    // Recency factor: exponential decay with 1-hour half-life
    const ageMs = now - entity.lastSeen;
    const recency = Math.pow(0.5, ageMs / (60 * 60 * 1000));

    // Frequency factor: log scale capped at 10
    const frequency = Math.min(1, Math.log10(entity.occurrences + 1) / Math.log10(11));

    // Cross-stream factor: entities in multiple streams are more important
    const crossStream = Math.min(1, entity.sourceStreamIds.length / 5);

    // Type weight: files/projects/tools are more actionable than topics
    const typeWeights: Record<EntityType, number> = {
      file: 1.0,
      project: 0.95,
      tool: 0.9,
      app: 0.85,
      person: 0.8,
      url: 0.7,
      channel: 0.6,
      topic: 0.5,
    };
    const typeWeight = typeWeights[entity.type] ?? 0.5;

    return recency * 0.4 + frequency * 0.25 + crossStream * 0.15 + typeWeight * 0.2;
  }

  private pruneIfNeeded(): void {
    // Prune old streams
    if (this.streams.size > this.config.maxWorkStreams) {
      const sorted = Array.from(this.streams.entries())
        .sort(([, a], [, b]) => a.lastActiveAt - b.lastActiveAt);

      const toRemove = sorted.slice(0, this.streams.size - this.config.maxWorkStreams);
      for (const [id] of toRemove) {
        if (id !== this.activeStreamId) {
          this.streams.delete(id);
        }
      }
    }

    // Close inactive active stream
    if (this.activeStreamId) {
      const active = this.streams.get(this.activeStreamId);
      if (active && Date.now() - active.lastActiveAt > this.config.streamTimeoutMs) {
        this.activeStreamId = null;
      }
    }
  }

  private pruneEntities(): void {
    // Remove least relevant entities to make room
    const now = Date.now();
    const scored = Array.from(this.entities.entries())
      .map(([key, entity]) => ({
        key,
        score: this.entityRelevanceScore(entity, now),
      }))
      .sort((a, b) => a.score - b.score);

    // Remove bottom 20%
    const toRemove = Math.ceil(scored.length * 0.2);
    for (let i = 0; i < toRemove; i++) {
      this.entities.delete(scored[i].key);
    }
  }

  private updateContextSnapshot(): void {
    // Update the context stream's snapshot with active work stream name
    const active = this.getActiveStream();
    // We can't directly modify the context stream's snapshot, but we
    // provide getPromptContext() and getContextString() for injection
    // The context stream's activeWorkStream field is updated via
    // a push event or direct snapshot update if available
    if (active) {
      // Push a lightweight system event to update the work stream reference
      // Only do this when stream changes (not every event)
      const snap = contextStream.getSnapshot();
      if (snap.activeWorkStream !== active.name) {
        // Don't push — just update via listener pattern.
        // The context stream will call getPromptContext() at prompt time.
      }
    }
  }

  private serializeStream(stream: WorkStream | null): WorkStream | null {
    if (!stream) return null;
    return {
      ...stream,
      eventTypes: new Set(stream.eventTypes),
      entities: [...stream.entities],
    };
  }

  private capitalize(s: string): string {
    if (!s) return s;
    return s.charAt(0).toUpperCase() + s.slice(1);
  }

  private basename(filePath: string): string {
    const parts = filePath.replace(/\\/g, '/').split('/');
    return parts[parts.length - 1] || filePath;
  }

  private estimateMemory(): number {
    // Rough estimate: 200 bytes per entity, 500 bytes per stream
    const entityMem = this.entities.size * 200;
    const streamMem = this.streams.size * 500;
    // Entity arrays in streams
    let streamEntityMem = 0;
    for (const s of this.streams.values()) {
      streamEntityMem += s.entities.length * 100;
    }
    return Math.round((entityMem + streamMem + streamEntityMem) / 1024);
  }
}

// ── Stop Words ─────────────────────────────────────────────────────

const STOP_WORDS = new Set([
  'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all', 'can', 'had',
  'her', 'was', 'one', 'our', 'out', 'are', 'has', 'have', 'that', 'this',
  'with', 'from', 'they', 'been', 'said', 'each', 'which', 'their',
  'will', 'other', 'about', 'many', 'then', 'them', 'these', 'some',
  'would', 'make', 'like', 'into', 'could', 'time', 'very', 'when',
  'come', 'made', 'find', 'back', 'only', 'long', 'just', 'over',
  'such', 'take', 'also', 'more', 'been', 'than', 'what', 'does',
  'using', 'used', 'test', 'file', 'true', 'false', 'null', 'undefined',
  'const', 'function', 'return', 'import', 'export', 'class', 'interface',
  'type', 'string', 'number', 'boolean', 'void', 'async', 'await',
]);

// ── Singleton ──────────────────────────────────────────────────────

export const contextGraph = new ContextGraph();
