## Interface Contract: Context Graph
**Generated:** 2026-03-06
**Source:** src/main/context-graph.ts (855 lines)

### Exports
- `contextGraph` — singleton instance of `ContextGraph`
- `WorkStream` — interface: { id, name, task, app, startedAt, lastActiveAt, eventCount, entities, eventTypes, summary }
- `EntityRef` — interface: { type, value, normalizedValue, firstSeen, lastSeen, occurrences, sourceStreamIds }
- `EntityType` — union: 'file' | 'app' | 'url' | 'person' | 'topic' | 'project'

### Public Methods
| Method | Signature | Description |
|--------|-----------|-------------|
| start() | `(): void` | Subscribe to context stream events |
| stop() | `(): void` | Unsubscribe and clear all state |
| getActiveStream() | `(): WorkStream \| null` | Current active work stream |
| getRecentStreams(limit?) | `(limit?: number): WorkStream[]` | Last N streams, most recent first |
| getTopEntities(limit?) | `(limit?: number): EntityRef[]` | Highest-occurrence entities |
| getActiveEntities(windowMs?) | `(windowMs?: number): EntityRef[]` | Entities seen within time window |
| getEntitiesByType(type, limit?) | `(type: EntityType, limit?: number): EntityRef[]` | Entities of one type |
| getContextString() | `(): string` | Human-readable context for prompts |
| getPromptContext() | `(): string` | Structured context for LLM injection |
| getSnapshot() | `(): ContextGraphSnapshot` | Full serialized state |

### IPC Channels
| Channel | Request | Response |
|---------|---------|----------|
| context-graph:get-streams | `{ limit? }` | `WorkStream[]` |
| context-graph:get-active-stream | — | `WorkStream \| null` |
| context-graph:get-entities | `{ type?, limit? }` | `EntityRef[]` |

### Dependencies
- Requires: context-stream (subscribes to events)
- Required by: briefing-pipeline (Track A), context-injector (Track C)
