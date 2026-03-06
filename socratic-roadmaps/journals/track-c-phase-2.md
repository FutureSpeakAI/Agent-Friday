## Session Journal: Track C, Phase 2 — "The Threads"
**Date:** 2026-03-06
**Commit:** (pending)

### What Was Built
`ContextInjector` — a pure computation module that merges work stream context (entities, active stream) with briefing intelligence to produce per-app context objects. No singletons, no side effects.

### Architecture

```
streamData { activeStream, entities }  ──┐
                                         ├── ContextInjector
briefings [{ topic, content, priority }] ─┘
                                         │
                    getContextForApp(appId) → AppContext {
                                                activeStream,
                                                entities (filtered, max 5),
                                                briefingSummary (1 or null)
                                              }
```

### App-Domain Mapping Strategy

Two static mappings define what each app cares about, following the intelligence-router pattern of mapping identifiers to domain filters without importing app code:

1. **`APP_ENTITY_TYPES`** — maps app IDs to relevant entity types:
   - `notes` → file, project, topic
   - `files` → file, project
   - `monitor` → app
   - `weather` → topic
   - Unknown apps → all types (generic)

2. **`APP_BRIEFING_KEYWORDS`** — maps app IDs to topic keywords:
   - `notes` → documentation, writing, note, doc, readme, text
   - `monitor` → system, cpu, memory, process, performance
   - Unknown apps → highest-priority briefing regardless of domain

### Key Design Choices
1. **Pure computation, not a service**: Unlike WorkContextStore (C.1) which manages subscription lifecycle, the injector is a plain class with no singleton. Multiple instances are independent. This makes testing trivial and avoids shared state bugs.

2. **Keyword matching for briefings**: Rather than semantic similarity (which would require LLM calls), briefing relevance uses case-insensitive substring matching on topic + content. Fast, deterministic, and sufficient for domain-level filtering. The fallback (highest-priority briefing) ensures no app gets nothing.

3. **Entity filtering by type, then sort by occurrence**: First filter to the app's relevant types, then sort by occurrence count descending, then slice to max 5. This surfaces the most-referenced entities in the user's work session.

4. **Defensive copying**: `ingest()` spreads both entity and briefing arrays to prevent callers from mutating internal state. The input arrays remain untouched.

5. **Static mappings over dynamic discovery**: The app-domain mappings are compile-time constants, not runtime configuration. This is deliberate — the set of apps is known and stable (26 apps in the registry), and adding a new app's mapping is a one-line change.

### What Surprised Me
How little the injector needs to know about apps. The entity type system (6 types: file, app, url, person, topic, project) provides enough granularity to meaningfully filter without any app-specific logic. The briefing keyword matching is crude but effective — domain relevance at the desktop OS level is coarse-grained.

### Test Coverage
17 tests covering all 10 validation criteria:
- Criterion 1: ingest merges stream + briefing data
- Criterion 2: getContextForApp returns AppContext shape
- Criterion 3: notes gets file/project entities + writing briefings
- Criterion 4: files gets file entities
- Criterion 5: weather gets topic entities
- Criterion 6: monitor gets app entities + system briefings
- Criterion 7: unknown apps get generic context (all types + highest-priority briefing)
- Criterion 8: updates on re-ingest (stream + briefing)
- Criterion 9: pure computation (independent instances, no input mutation)
- Criterion 10: max 5 entities + 1 briefing summary per app
- Plus: edge cases (empty state, null activeStream)

### Files Created/Modified
- `src/main/context-injector.ts` (NEW, ~155 lines) — pure computation module
- `tests/track-c/context-injector.test.ts` (NEW, ~290 lines) — 17 tests

### Socratic Reflection
*"The whole is greater than the sum of its parts."* — Two independent information channels (work streams and briefings) combine through domain-aware filtering to create curated context that each app can use without drowning in irrelevant data. The injector doesn't create new information — it curates existing information, making the right subset visible to the right consumer. Understanding is selective attention.

### Handoff Notes for Phase C.3
Phase C.3 ("The Tapestry") needs to know:
- `AppContext` shape: `{ activeStream, entities, briefingSummary }`
- Entities are already sorted by occurrence and limited to 5
- `briefingSummary` is a single string (the briefing's content field) or null
- The injector is instantiated per-use, not a singleton — C.3's bridge code will create one
