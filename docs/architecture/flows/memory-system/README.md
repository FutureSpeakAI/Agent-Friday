# Memory System Flow

## Quick Reference

| Property | Value |
|----------|-------|
| **Status** | Active, three-tier memory with consolidation |
| **Type** | Extract-store-retrieve pipeline with periodic consolidation |
| **Complexity** | High (LLM-powered extraction, semantic search, vault encryption, Obsidian sync) |
| **Last Analysed** | 2026-03-24 |

## Overview

The memory system implements a three-tier architecture (short-term, medium-term, long-term) inspired by human memory consolidation. After each conversation turn, an LLM analyses the exchange to extract new facts and patterns. Memories are stored encrypted via the Sovereign Vault, indexed for semantic search via embeddings, optionally synced to an Obsidian vault, and periodically consolidated (promoting confident observations to long-term, merging duplicates). Episodic memory captures entire session summaries, and relationship memory tracks the evolving user-agent dynamic over time.

## Flow Boundaries

- **Start**: Conversation completes a user+assistant exchange (server.ts post-response hook or IPC call)
- **End**: Memories persisted to disk, indexed for search, synced to Obsidian, injected into future system prompts

## Component Quick Reference

| Component | File | Purpose |
|-----------|------|---------|
| MemoryManager | `src/main/memory.ts` | Core three-tier store: short/medium/long-term |
| EpisodicMemory | `src/main/episodic-memory.ts` | Session-level episode summaries with topics, tone, decisions |
| RelationshipMemory | `src/main/relationship-memory.ts` | User-agent relationship tracking (trust, jokes, preferences) |
| MemoryConsolidation | `src/main/memory-consolidation.ts` | Periodic promotion, merging, cross-episode insights (every 6h) |
| MemoryQuality | `src/main/memory-quality.ts` | Benchmark engine: extraction precision, retrieval MRR, consolidation accuracy |
| MemoryPersonalityBridge | `src/main/memory-personality-bridge.ts` | Syncs memory patterns into personality calibration |
| ObsidianMemory | `src/main/obsidian-memory.ts` | Read/write Obsidian vault markdown notes |
| SemanticSearch | `src/main/semantic-search.ts` | Embedding-based memory retrieval |
| MemoryWatchdog | `src/main/integrity/memory-watchdog.ts` | Integrity verification for memory stores |
| TrustGraph | `src/main/trust-graph.ts` | Person mention routing from memory extraction |
| Memory IPC Handlers | `src/main/ipc/memory-handlers.ts` | IPC endpoints for all memory operations |
| MemoryExplorer | `src/renderer/components/MemoryExplorer.tsx` | UI for browsing/editing memories |
| FridayProfile | `src/main/friday-profile.ts` | Living intelligence profile, receives learning appends |
| Vault | `src/main/vault.ts` | Encrypted read/write for memory JSON files |
| Personality | `src/main/personality.ts` | Injects memory context into system prompts |

## Detailed Steps

### 1. Memory Extraction Trigger

Extraction is triggered from two paths:

**Path A: Express `/api/chat` post-response hook** (server.ts:244-251)
1. After the chat response is sent, the full conversation (history + new exchange) is passed to `memoryManager.extractMemories()` as a fire-and-forget async call.

**Path B: IPC direct call** (memory-handlers.ts:28-34)
2. Renderer calls `window.eve.memory.extract(history)` via the `memory:extract` IPC channel.
3. The handler validates the message array and delegates to `memoryManager.extractMemories()`.

### 2. LLM-Powered Extraction (MemoryManager)

4. `extractMemories()` (memory.ts:167) checks that the conversation has at least 2 messages.
5. An extraction prompt is built containing:
   - The full conversation text
   - Already-known long-term facts (to avoid duplicates)
   - Already-known medium-term patterns (to avoid duplicates)
   - Instructions to return JSON with `longTerm[]`, `mediumTerm[]`, and `personMentions[]`
6. If the `MemoryPersonalityBridge` is available, personality-informed extraction guidance is appended (memory.ts:214-221).
7. The prompt is sent to `llmClient.text()` with `maxTokens: 1024` (memory.ts:224).
8. The response is parsed for JSON (handles potential markdown wrapping via regex, memory.ts:228).

### 3. Long-Term Fact Storage

9. For each item in `extracted.longTerm` (memory.ts:233-256):
   - Skip if `fact` is missing or not a string.
   - **Duplicate check**: `isDuplicateFact()` tokenizes both the new fact and all existing facts, removes stop words, and computes Jaccard similarity. Threshold: >= 0.80 similarity = duplicate (memory.ts:408-440).
   - New facts get a UUID, category (`identity|preference|relationship|professional`), `confirmed: false`, `source: 'extracted'`.
   - Fact is appended to `fridayProfile` via `appendLearning()` (memory.ts:250).
   - Fact is indexed for semantic search via `semanticSearch.index()` (memory.ts:252).
10. Long-term store is saved to disk (memory.ts:255).

### 4. Medium-Term Pattern Storage

11. For each item in `extracted.mediumTerm` (memory.ts:259-291):
    - Skip if `observation` is missing or not a string.
    - **If duplicate found**: Increment `occurrences`, update `lastReinforced`, boost `confidence` by 0.1 (capped at 1.0). If >30 minutes since last reinforcement, increment `sessionCount`.
    - **If new**: Create entry with `confidence: 0.5`, `occurrences: 1`, `sessionCount: 1`.
    - Entry indexed for semantic search.
12. Medium-term store saved to disk.

### 5. Person Mention Routing

13. If `personMentions[]` is non-empty (memory.ts:294-302):
    - Mentions are routed to `trustGraph.processPersonMentions()`.
    - Each mention includes: name, context, sentiment (-1 to +1), domains, evidence type.
    - The Trust Graph builds a network of known people and their reliability.

### 6. Personality Sync

14. If `MemoryPersonalityBridge` is available, `bridge.syncMemoryToPersonality()` is called (memory.ts:305-308).
15. This propagates memory patterns into personality calibration sliders.

### 7. Persistence Pipeline (MemoryManager._doSave)

16. `save(tier)` serializes writes via `this.saveQueue` promise chain (memory.ts:465-473).
17. `_doSave(tier)` (memory.ts:475-515):
    - Writes JSON via `vaultWrite()` (encrypted if vault is unlocked).
    - For long-term and medium-term: signs the store via `integrityManager.signMemories()` for tamper detection.
    - If Obsidian vault path is configured: mirrors entries as markdown notes via `writeLongTermNote()` / `writeMediumTermNote()`.

### 8. Episodic Memory Creation

18. Episodes are created from two paths:
    - **Voice sessions**: When Gemini Live disconnects (handled in useGeminiLive.ts).
    - **Text sessions**: When 5 minutes of silence is detected (server.ts:256-270), `flushTextSession()` calls `episodicMemory.createFromSession()`.
19. `createFromSession()` (episodic-memory.ts:66):
    - Skips sessions < 60 seconds or < 2 turns.
    - Sends transcript to LLM for analysis, extracting: summary, topics, emotional tone, key decisions.
    - Falls back to a basic "Session about: ..." summary if LLM fails (episodic-memory.ts:150-153).
    - Episode is stored, capped at 200 episodes.
    - Synced to Obsidian as a markdown file with frontmatter (episodic-memory.ts:319-368).
    - Indexed for semantic search (episodic-memory.ts:181-186).
    - Triggers `relationshipMemory.updateFromEpisode()` (episodic-memory.ts:189-191).

### 9. Relationship Memory Update

20. `updateFromEpisode()` (relationship-memory.ts:92):
    - Updates basic stats: `totalSessions`, `totalDurationMinutes`, streak tracking.
    - Tracks peak interaction hours (last 100 entries).
    - Updates topic frequency counts (top 20 kept).
    - Computes trust level: `0.3 + log10(sessions+1)*0.2 + min(streak*0.02, 0.2)`, capped at 1.0.
21. `analyseEpisodeForRelationship()` (relationship-memory.ts:246):
    - Only runs on episodes >= 120s or >= 6 turns.
    - LLM extracts: inside jokes, shared references, communication preferences, mood summary.
    - Results merged into state (jokes capped at 20, references at 30, preferences at 15).
22. Relationship state saved to `{userData}/memory/relationship.json`.

### 10. Periodic Consolidation

23. `MemoryConsolidation` runs every 6 hours (memory-consolidation.ts:60):
    - **Promotion**: Medium-term observations with a weighted score >= 10 and >= 3 occurrences are promoted to long-term facts. Scoring weights: frequency (max 20), cross-session count (max 10), time span (3-5), confidence bonus (3), staleness penalty (-2 to -5).
    - **Merge**: Duplicate long-term entries with >= 85% similarity are merged via LLM.
    - **Cross-episode insights**: Recent episodes are analysed for emergent patterns.

### 11. Memory Retrieval (Prompt Injection)

24. `buildSystemPrompt()` in `personality.ts` calls:
    - `memoryManager.buildMemoryContext()` -- formats long-term facts and top-10 medium-term observations.
    - `episodicMemory.getContextString()` -- formats 5 most recent episode summaries with relative timestamps.
    - `relationshipMemory.getContextString()` -- formats trust level, streaks, favourite topics, inside jokes, communication preferences, peak hours.
25. These strings are injected as sections of the system prompt sent to every LLM call.

### 12. Semantic Search

26. All memory entries (long-term, medium-term, episodes) are indexed via `semanticSearch.index()` with embeddings.
27. Retrieval via `search:query` IPC channel returns ranked results across all memory types.

## IPC Channels Used

| Channel | Direction | Purpose |
|---------|-----------|---------|
| `memory:get-short-term` | Renderer -> Main | Read short-term conversation buffer |
| `memory:get-medium-term` | Renderer -> Main | Read medium-term observations |
| `memory:get-long-term` | Renderer -> Main | Read long-term facts |
| `memory:update-short-term` | Renderer -> Main | Replace short-term buffer |
| `memory:extract` | Renderer -> Main | Trigger LLM extraction from conversation |
| `memory:update-long-term` | Renderer -> Main | Edit a long-term entry |
| `memory:delete-long-term` | Renderer -> Main | Delete a long-term entry |
| `memory:delete-medium-term` | Renderer -> Main | Delete a medium-term entry |
| `memory:add-immediate` | Renderer -> Main | Directly add a confirmed fact (from save_memory tool) |
| `episodic:create` | Renderer -> Main | Create episode from transcript |
| `episodic:list` | Renderer -> Main | List all episodes |
| `episodic:search` | Renderer -> Main | Search episodes by text query |
| `episodic:get` | Renderer -> Main | Get episode by ID |
| `episodic:delete` | Renderer -> Main | Delete an episode |
| `episodic:recent` | Renderer -> Main | Get N most recent episodes |
| `search:query` | Renderer -> Main | Semantic search across all memory types |
| `search:stats` | Renderer -> Main | Get search index statistics |

## Storage Locations

| Store | File Path | Format | Encryption |
|-------|-----------|--------|------------|
| Short-term | `{userData}/memory/shortTerm.json` | JSON array | Vault-encrypted |
| Medium-term | `{userData}/memory/mediumTerm.json` | JSON array | Vault-encrypted + integrity-signed |
| Long-term | `{userData}/memory/longTerm.json` | JSON array | Vault-encrypted + integrity-signed |
| Episodes | `{userData}/memory/episodes.json` | JSON array (transcripts stripped) | Plaintext |
| Relationship | `{userData}/memory/relationship.json` | JSON object | Plaintext |
| Chat history | `{userData}/memory/chat-history.json` | JSON array | Plaintext |
| Obsidian (long-term) | `{vaultPath}/Friday/memories/*.md` | Markdown with frontmatter | N/A |
| Obsidian (medium-term) | `{vaultPath}/Friday/observations/*.md` | Markdown with frontmatter | N/A |
| Obsidian (episodes) | `{vaultPath}/Friday/episodes/*.md` | Markdown with frontmatter | N/A |

## State Changes

| State | Location | Trigger |
|-------|----------|---------|
| `shortTerm[]` | MemoryManager | Each conversation turn |
| `mediumTerm[]` | MemoryManager | LLM extraction finds new pattern or reinforces existing |
| `longTerm[]` | MemoryManager | LLM extraction finds new fact, or consolidation promotes |
| `episodes[]` | EpisodicMemoryStore | Session ends (voice disconnect or text silence timeout) |
| `RelationshipState` | RelationshipMemory | Each episode triggers stats + LLM analysis |
| Semantic index | SemanticSearch | Each new memory entry |
| Obsidian vault files | ObsidianMemory | Each save of medium/long-term or episode creation |
| Integrity signatures | IntegrityManager | Each save of medium/long-term |
| FridayProfile learnings | FridayProfile | Each new long-term fact |

## Error Scenarios

| Scenario | Handling | Location |
|----------|----------|----------|
| LLM extraction fails | Warning logged, no memories stored | memory.ts:314-316 |
| Duplicate fact detected | Silently skipped (Jaccard >= 0.80) | memory.ts:237-238 |
| Obsidian vault sync fails | Warning logged, JSON store unaffected | memory.ts:511-514 |
| Vault locked (boot Phase A) | Falls back to empty defaults; reloaded after unlock | memory.ts:126-136 |
| Integrity signing fails | Warning logged, memory still saved | memory.ts:491-494 |
| Episode LLM analysis fails | Fallback to basic summary from first turn | episodic-memory.ts:148-153 |
| Relationship LLM analysis fails | Warning logged, basic stats still updated | relationship-memory.ts:371-373 |
| Consolidation run fails | Warning logged, retried in 6 hours | memory-consolidation.ts:63-66 |
| Concurrent file writes | Serialized via `saveQueue` promise chain | memory.ts:466-473 |
| Short-term overflow (>20 entries) | Oldest entries trimmed | memory.ts:161-163 |
| Medium-term expiry (>30 days, <5 occurrences) | Pruned on initialization | memory.ts:442-455 |
| Episode cap exceeded (>200) | Oldest episodes dropped | episodic-memory.ts:172-174 |
