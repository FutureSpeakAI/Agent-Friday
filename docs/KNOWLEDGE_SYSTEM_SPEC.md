# Agent Friday — Knowledge System Overhaul: Technical Specification

**Spec stage:** Opus 4.8 (STORM method — multi-perspective interrogation → synthesis)
**Build stage:** Fable 5 (one build session per phase)
**Date:** 2026-07-06
**Status:** Draft for Stephen's review. Sections marked ⚠️ need a decision before Phase 2+.
**Deliverable of this overhaul:** GraphRAG-indexed, wiki-linked knowledge graph for Agent Friday, plus a 3D "explore your second brain" view rendered inside the holographic desktop.

> **⚠️ Read the Architecture Decision (§2) first.** This spec's single most important finding is that the "knowledge backend" the task brief places *in asimovs-mind* actually already lives *in friday-desktop*. The recommendation is to build the overhaul in **friday-desktop**. Every file path below assumes that unless noted. If Stephen overrides this, §2 explains what changes.

---

## 1. Executive Summary

Agent Friday already has a rich but **disconnected** knowledge substrate. It has a hand-edited wiki (markdown in `~/.friday/wiki/`), semantic conversation memory (ChromaDB + `all-MiniLM-L6-v2`), a tamper-evident cognitive-memory ledger, nightly "memory dreaming," a self-improving learning loop, a Series Bible for creative work, and a `SOUL.md` personality file. What it does **not** have is a **graph** that connects any of these to each other: no wikilinks/backlinks, no entities-and-relationships extracted across sources, no thematic community structure, and no way to *see* the whole thing.

This overhaul adds that connective tissue as a **two-tier knowledge graph**:

- **Tier A — Structural (always-on, no-LLM, offline, instant).** The wiki *is* the graph: pages are nodes, `[[wikilinks]]` are edges, communities are detected from link structure. Cheap enough to run on every save. This is the default backbone and the default 3D view. Ported from **obsidian-wiki** (pure-stdlib Python — drops straight in).
- **Tier B — Semantic (GraphRAG, LLM-indexed, opt-in, incremental).** Entity + relationship + community-report extraction across wiki bodies, conversation memory, and cognitive memory — the Microsoft-GraphRAG data model — routed through Friday's existing `model_router` + `egress_gate`, embedded locally, encrypted at rest. Powers local/global/drift search and enriches the graph with entities that aren't explicit wiki pages. Harvested from **graphrag-workbench** (prompts + data model) with a native lightweight indexer; Microsoft's `graphrag` pip package is an *optional* "power indexer," not a hard dependency.

The **3D explorer** is a new holographic workspace (`KnowledgeGraphWS`) rendering the graph as a galaxy — communities as nebulae, entities as stars, relationships as light filaments, your wiki pages as the brightest stars. Click a star → open its wiki page; search → light up a constellation; when Friday learns something, a new star ignites. Ported to **vanilla Three.js r128** (friday-desktop has no JS bundler; the workbench's react-three-fiber cannot be reused directly — see §7).

Delivered in **5 phases, each sized for one Fable build session** (§9).

---

## 2. ⚠️ Architecture Decision: Which Repo Owns the Knowledge Backend?

The task brief says: *"the 3D exploration view renders in friday-desktop, with the graph/knowledge backend here in asimovs-mind."* Investigation shows this framing does not match the codebases, and following it literally would create real problems. This is the #1 decision for Stephen.

### 2.1 What the two repos actually are

| | **asimovs-mind** (this repo) | **friday-desktop** (`friday-desktop (sibling checkout)`) |
|---|---|---|
| Version | friday-core **v2.3.0** | **v5.3.0** ("Ship It") |
| Language | Node.js (ESM) | Python (Flask) backend + React 18 / Three.js r128 frontend |
| Role | MCP server (19 subsystems, HTTP bridge, holographic dashboard) | **The shipping Agent Friday app** |
| Knowledge system | `memory` (3-tier), `context` (ephemeral 7-day entity graph, 500-node cap), no wiki, no SOUL | `wiki_engine.py`, `soul.py`, `memory_dreaming.py`, `learning_loop.py`, `cognitive_memory.py`, `conversation_memory.py`, `creative_memory.py` |
| LLM routing | `llm` subsystem — **3 providers** (Anthropic, OpenRouter, Ollama), no egress gate | `model_router.py` + `provider_registry.py` — **~14 providers**, plus **`egress_gate.py`** (fail-closed, Presidio NER, adversarial tests) |
| Wiki data | — | `~/.friday/wiki/` markdown, per-section AES-256-GCM |
| 3D capability | — | Three.js r128 scene already live (bloom, particles, shaders) |
| Spec convention | `docs/*_GUIDE.md` reference docs | `docs/*_SPEC.md` phased build specs (VOICE_SYSTEM_OVERHAUL_SPEC, CONTENT_PIPELINE_SPEC) |

**Every "v5 knowledge system" element the brief describes — learning loop, memory dreaming, SOUL.md, the multi-provider model system, the recent package restructure, the living wiki — lives in friday-desktop, not here.** (Corroborated by memory note *"Agent Friday app lives in friday-desktop, not asimovs-mind."*) The asimovs-mind→friday-desktop bridge (`mcp_client.py`, the `.asimovs-mind/vault/port` file) exists but is **not used for knowledge or memory** today.

### 2.2 The contradiction in the brief

The brief simultaneously asks to (a) put the backend *in asimovs-mind* **and** (b) route GraphRAG's LLM calls *through the existing 14-provider model system and the fail-closed egress gate*. But (b) — the 14-provider router and the egress gate — **only exist in friday-desktop.** asimovs-mind's `llm` subsystem has 3 providers and no egress gate. You cannot satisfy (b) from asimovs-mind without either duplicating the entire egress/provider stack in Node or having asimovs-mind call back into friday-desktop for every LLM call (an inversion, since friday-desktop is the app). So the two halves of the constraint point at **friday-desktop**.

### 2.3 Options

**Option A — Build entirely in friday-desktop. ✅ RECOMMENDED.**
Graph index + graph API + 3D view all in friday-desktop. Reuses `model_router`, `egress_gate`, `wiki_engine`, ChromaDB embeddings, vault crypto, blueprint auto-discovery. obsidian-wiki (Python) and the GraphRAG prompts drop into the Python backend; the visualizer ports into the React/Three frontend. No cross-repo bridge, one source of truth, honors the routing + egress constraints natively. **Both source repos' languages match friday-desktop, not asimovs-mind** — a strong signal. Only cost: contradicts the brief's "backend in asimovs-mind" line.

**Option B — Split per the literal brief (graph backend in asimovs-mind, 3D in friday-desktop).**
Costs: (1) asimovs-mind has 3 providers and no egress gate → to honor constraint (b), GraphRAG LLM calls must be proxied back to friday-desktop — backwards. (2) Two knowledge stores (friday-desktop wiki + asimovs-mind graph) → a sync/migration problem with two sources of truth. (3) obsidian-wiki is Python; reimplementing its graph algorithms in Node is avoidable rework. (4) friday-desktop v5.3.0 is the shipping product; friday-core v2.3.0 is older and dormant for knowledge. Choose this only if the graph **must** be exposed through asimovs-mind's MCP tool ecosystem to other agents.

**Option C — Hybrid.** Build in friday-desktop (Option A), then expose the finished graph read-API over asimovs-mind's holographic HTTP bridge as a thin MCP proxy, *if and when* other MCP agents need to query Friday's brain. Defer this until there's a concrete consumer. Recommended as a *future* add-on to A, not now.

**Recommendation: Option A**, with C as a later option. The rest of this spec is written for A. **If Stephen picks B**, see §12-Q1 for the deltas (a Node port of Tiers A/B, an egress-gate proxy contract, and a wiki-sync protocol).

### 2.4 Where does *this spec file* live vs. where the build happens

This file is written to `asimovs-mind/docs/KNOWLEDGE_SYSTEM_SPEC.md` (the literal deliverable path / pipeline cwd). **The build target is friday-desktop.** Recommend the Fable build session be launched against `friday-desktop (sibling checkout)` with this spec as input; optionally copy this file to `friday-desktop/docs/KNOWLEDGE_SYSTEM_SPEC.md` to match that repo's spec convention. (This session did **not** write into friday-desktop to avoid colliding with the parallel voice worktree.)

---

## 3. Source Repository Assessment

Both repos cloned and read in full. Both **MIT-licensed** → vendoring, forking, and porting are all legally clean.

### 3.1 graphrag-workbench (github.com/ChristopherLyon/graphrag-workbench)

| Attribute | Finding |
|---|---|
| What it is | A **Next.js 15 / React 19 front-end workbench** that wraps **Microsoft's Python `graphrag` package**. Uploads a corpus, shells out to the `graphrag` CLI to index (`spawn`), reads the parquet output, and renders a **3D force-directed graph** + a chat panel. |
| License / maintenance | MIT. Last commit 2025-09-09; single-author hobby project; no tests; not a maintained library. |
| Dependency weight | Heavy front-end (Radix UI, Tailwind 4, TanStack). **3D stack: `three@0.179`, `@react-three/fiber@9`, `@react-three/drei@10`, `d3-force-3d`.** Backend indexing = Microsoft `graphrag` (pandas, pyarrow, lancedb, tiktoken, networkx, graspologic). |
| Reusable assets | **(1) The GraphRAG prompt set** (`prompts/*.txt` — extract_graph, summarize_descriptions, community_report_{graph,text}, local/global/drift/basic search) — reusable verbatim. **(2) The data model** (`lib/graphData.ts`: `Entity`, `Relationship`, `Community`, `CommunityReport` — this *is* the Microsoft GraphRAG output schema) — adopt as the API contract. **(3) The 3D visualizer** (`components/GraphVisualizer.tsx`, `lib/forceSimulation.ts`, `GalaxyBackground.tsx`, UnrealBloom post-processing) — a **design reference to port**, not reusable code (react-three-fiber, React 19). |
| ⚠️ Rendering caveat | The visualizer renders **one React `<Node>` component with its own mesh + shader material per entity** + per-frame billboarding. Fine for hundreds; **will not hold framerate at thousands.** The port must switch to `InstancedMesh` (§7). |
| **Verdict** | **HARVEST, don't depend or fork.** Take the prompts (verbatim), the data model (as contract), and the visualizer (as design to reimplement in vanilla Three.js r128). Do **not** adopt the Next.js app, its parquet pipeline, or `@react-three/*`. Treat Microsoft's `graphrag` pip package as an **optional opt-in power indexer** (§6.4), not a base dependency. |

### 3.2 obsidian-wiki (github.com/ar9av/obsidian-wiki)

| Attribute | Finding |
|---|---|
| What it is | A **pure-Python CLI + agent-skill framework** implementing **Andrej Karpathy's "LLM Wiki"** pattern: distill knowledge once into interconnected markdown with `[[wikilinks]]`, keep it current, query the compiled graph instead of re-running RAG. Active (v2026.x, CalVer; last commit 2026-07-03; 33 tests). |
| License / deps | MIT. **Core is stdlib-only** (`dependencies = []`). Optional extras: `tree-sitter` (AST), `leidenalg`+`igraph` (better community detection — falls back to pure-Python greedy modularity). |
| Reusable assets | **`obsidian_wiki/graphrag.py`** — builds an index from frontmatter + wikilinks; `rank_candidates`, `find_path` (BFS multi-hop), `classify_query`, returns a **`should_read` shortlist** so the agent opens 2-3 pages instead of 10+. **No LLM per query.** **`obsidian_wiki/graph_analysis.py`** — community detection (Leiden→greedy fallback), **god-nodes** (hubs), dead-ends, isolated pages, **surprising connections** (cross-community edges). `ast_extractor.py`, `lint.py`, and a `.manifest.json` **delta/incremental** system. The `llm-wiki` skill defines the vault conventions (`index.md`, `log.md`, `.manifest.json`, categories: concepts/entities/skills/references/synthesis/journal; canonical-path manifest keys). |
| Relationship to Friday | **Maps 1:1 onto Friday's existing wiki.** Friday's `~/.friday/wiki/` = obsidian-wiki's vault; sections = categories; the pending-approval workflow = agent-grown wiki. The one missing primitive Friday lacks — **wikilinks/backlinks** — is exactly what these modules assume. |
| **Verdict** | **PORT NATIVELY into friday-desktop's Python backend.** `graphrag.py` + `graph_analysis.py` (+ manifest/delta) are stdlib and drop in with light adaptation to Friday's paths. Adopt the retrieval philosophy (query the graph; `should_read`) and the special-file conventions (`index.md`, `log.md`, `.manifest.json`). Do **not** adopt the pip packaging, the `.skills/` CLI, or the multi-agent installers (Friday has its own skills). This is the cheap, high-leverage core of Tier A. |

### 3.3 One-line verdicts

- **graphrag-workbench** → *harvest* prompts + data model + visualizer-as-design. Microsoft `graphrag` = optional.
- **obsidian-wiki** → *port* `graphrag.py` + `graph_analysis.py` + manifest/delta natively; adopt conventions.

---

## 4. Design Interrogation (STORM — perspectives before synthesis)

Seven perspectives interrogated the design; each surfaced a requirement or risk that shapes §5–§9. (This mirrors the "research synthesis / expert perspectives" section of `CONTENT_PIPELINE_SPEC.md`.)

**P1 — Knowledge Architect: "Does Friday even need GraphRAG?"**
Friday's `context` graph (in asimovs-mind) is ephemeral (7-day prune, 500-node cap, session-scoped) — not durable knowledge. friday-desktop has *no* cross-source graph at all. The real gaps: (a) no entity/relationship graph spanning wiki + conversations + memory; (b) no thematic/community summarization (the "what's the big picture of what I know" question); (c) wiki has no wikilinks. GraphRAG fills (a)+(b); obsidian-wiki fills (c) cheaply. → **Requirement: two tiers, structural-first.** Don't pay LLM cost for questions the link graph answers for free.

**P2 — Privacy/Security Engineer: "Indexing reads the user's most sensitive data."**
GraphRAG makes *many* LLM calls over the entire corpus — including health/finance/legal wiki sections. Every cloud call must pass `seal_outbound()`; TIER_3 content must never leave device. Embeddings must default to local (`all-MiniLM-L6-v2`, on-device, bypasses gate). The **derived index is as sensitive as its inputs** → encrypt at rest with `vault_crypto` for any content derived from encrypted/sensitive sources. → **Requirements: gate every cloud call; local-first embeddings; classify-before-extract; encrypt derived index; adversarial egress test must pass (§10).**

**P3 — Performance Engineer: "Thousands of nodes at interactive framerate."**
The workbench's per-node React-mesh approach caps out in the hundreds. Need `InstancedMesh` nodes, merged `LineSegments` edges, labels only on hover/selection/LOD, layout **precomputed server-side** (the `Entity` model already carries `x,y,z`), and **community-level aggregation** as the primary LOD lever — render ~200-500 super-nodes, expand to members on zoom/click. GraphRAG's community *hierarchy* (levels) is purpose-built for this. → **Requirements: instanced rendering; server-side layout; community LOD; explicit budget (§8).**

**P4 — Migration/Data Engineer: "Don't lose or fork the truth."**
The wiki markdown remains the **source of truth (Layer 1/2)**; the graph index is **derived and rebuildable (Layer 3)**. Migration must be incremental (manifest/delta), idempotent, and reversible. Existing facts live in five places (cognitive memory, conversation memory, learning loop, creative memory, wiki) — they become graph nodes *with provenance back-links*, never copies that can drift. → **Requirements: derived-not-authoritative index; manifest-driven delta; provenance on every node; full rebuild is always safe.**

**P5 — Retrieval Designer: "How does the agent actually use this at runtime?"**
Four retrieval paths, auto-routed by query type: (1) **structural** (no LLM — "how are X and Y related", multi-hop, `should_read`); (2) **local search** (entity-centric facts); (3) **global search** (community-report map-reduce — thematic questions); (4) **drift** (hybrid). One agent tool `knowledge_query` classifies and routes; specialized tools (`knowledge_related`, `knowledge_path`, `knowledge_communities`) expose primitives. The existing "always-on wiki context" upgrades to inject graph-aware context. → **Requirement: a router in front of retrieval; structural is the default and the cheapest.**

**P6 — Product/UX: "This is Agent Friday's *head*."**
The 3D view is the emotional hook — flying through the agent's mind. Reuse the holographic language (cyan/magenta/green mood palette, glass, bloom already vendored). Communities = glowing nebulae; entities = stars; relationships = filaments; *your* wiki pages = brightest stars; a newly learned fact = a star igniting live. Click a star → the wiki page opens in `WikiWS`. → **Requirement: the view is a first-class workspace, visually native, wired to wiki navigation and live learning events.**

**P7 — Build Sequencer: "One Fable session per phase."**
Each phase must be independently shippable, testable, and small enough for one focused build. Tier A (no LLM, no UI risk) ships before Tier B (LLM cost/egress risk) ships before the 3D view (perf risk) ships before integration. → **Requirement: the phasing in §9, with Phase 2 pre-split into 2a/2b in case the indexer+retrieval is too large for one session.**

---

## 5. Target Architecture

### 5.1 The two-tier graph

```
                         ┌─────────────────────────────────────────────┐
   SOURCES (truth)       │  DERIVED INDEX (rebuildable)                 │      RETRIEVAL
                         │  ~/.friday/knowledge-graph/                  │
 ~/.friday/wiki/*.md ───▶│                                             │──▶ knowledge_query (router)
 conversation_memory ───▶│  Tier A  structural graph (no LLM)          │      ├─ structural  (Tier A)
 cognitive_memory   ───▶│    nodes = wiki pages / entities            │      ├─ local search (Tier B)
 dreaming / learning ──▶│    edges = [[wikilinks]] + backlinks        │      ├─ global search(Tier B)
                         │    communities = link-structure clusters    │      └─ drift        (Tier B)
                         │                                             │
                         │  Tier B  semantic graph (LLM-indexed)       │──▶ 3D Explorer (KnowledgeGraphWS)
                         │    entities + relationships (extracted)     │      GET /api/knowledge-graph/*
                         │    community reports (LLM summaries)         │
                         │    embeddings (local MiniLM, on-device)     │
                         │    x,y,z layout (precomputed server-side)   │
                         └─────────────────────────────────────────────┘
                                  every cloud LLM call ▲
                                  ── model_router._generate_text ──▶ egress_gate.seal_outbound ──▶ provider
```

### 5.2 Canonical data model (the contract)

Adopt the graphrag-workbench schema verbatim as the backend↔frontend contract, extended with a `provenance` block (Friday-specific). JSON served by the API; stored as the graph index.

```jsonc
// Entity (node)
{
  "id": "ent_9f3a…",
  "title": "Transformer Architecture",
  "type": "concept",              // concept|entity|person|org|tool|project|event|place
  "description": "…",             // LLM-written (Tier B) or wiki summary (Tier A)
  "degree": 14, "frequency": 9,
  "x": 12.3, "y": -4.1, "z": 7.8, // precomputed layout (server-side)
  "community": "3", "level": 1,
  "provenance": {                 // ← Friday extension; never a copy, always a pointer
    "wiki_pages": ["concepts/transformers.md"],
    "conversations": ["sess_…#turn_42"],
    "cognitive_keys": ["fact:…"],
    "sensitivity": "TIER_1"       // 1 public | 2 private | 3 sensitive  (drives encryption + egress)
  }
}
// Relationship (edge)
{ "id":"rel_…","source":"ent_a","target":"ent_b","description":"…","weight":0.8,
  "provenance": { "wiki_pages":[…], "conversations":[…] } }
// Community + CommunityReport (for LOD + global search)
{ "id":"com_3","level":1,"parent":"com_0","children":["com_7"],
  "title":"ML fundamentals","entity_ids":[…],"relationship_ids":[…],"size":22 }
{ "id":"rep_3","community":"3","title":"…","summary":"…","full_content":"…","rank":8.5 }
```

### 5.3 Storage

- Location: `~/.friday/knowledge-graph/` (sibling to `wiki/`, `memory/`, `dreams.db`).
- Format: start with JSON files per artifact (`entities.json`, `relationships.json`, `communities.json`, `community_reports.json`, `layout.json`, `.manifest.json`) — matches the workbench's `GraphDataLoader` contract and is trivial to serve. Migrate to SQLite if/when node counts demand it (open question §12-Q5).
- Encryption at rest: any artifact whose provenance includes a TIER_2/TIER_3 or encrypted-section source is written through `vault_crypto.encrypt()` (same key as `~/.friday/finance|health`), transparently decrypted on read — mirroring `wiki_engine`'s per-section encryption.
- Embeddings: reuse `conversation_memory`'s ChromaDB + `all-MiniLM-L6-v2` (local). A dedicated `knowledge-graph` ChromaDB collection.

### 5.4 LLM routing + egress contract (hard constraint)

All Tier B extraction/summarization LLM calls go through the **existing** path — no new network code:

```python
# GraphRAG indexer NEVER calls a provider directly. It calls:
text = _generate_text(messages, system=EXTRACT_GRAPH_PROMPT, model=<routed>, workspace="research")
#   model_router._generate_text  →  _seal_or_block  →  egress_gate.seal_outbound(payload, provider)
#   → local provider: stays on device;  cloud provider: classify + redact/drop per tier
```

Rules the indexer must obey:
1. **One `seal_outbound` per request** (batch-level, not per-token) — already how `_generate_text` works.
2. **Classify before extract.** For each chunk, resolve source sensitivity from provenance. TIER_3 chunks are indexed **only** with a local provider (`is_local_provider()` true) or skipped with a logged reason — never sent to cloud.
3. **Embeddings default local** (MiniLM, on-device, bypasses the gate). Cloud embeddings are opt-in and gated.
4. **Trusted constants** (the GraphRAG prompt templates themselves) may be registered via `register_trusted_text()` so the prompts don't trip the classifier — but **never** interpolate user data into a trusted-registered string.
5. **`gate_operational()` gates the whole pipeline**: if the startup self-test failed, Tier B cloud indexing refuses to run (local-only fallback).
6. If Microsoft's `graphrag` package is used (opt-in), it is pointed at a **local OpenAI-compatible shim** (`api_base: http://127.0.0.1:<port>/v1`) that forwards to `_generate_text`/embeddings — GraphRAG never opens its own internet socket. (Confirmed: GraphRAG uses LiteLLM and honors `api_base` for OpenAI-compatible endpoints.)

### 5.5 The 3D-view boundary (backend ↔ frontend API)

Backend (Flask blueprint `routes/knowledge_graph.py`, auto-discovered; **also add `'knowledge_graph'` to `server.py`'s `ROUTE_MODULES`** or `test_blueprint_discovery.py` fails):

| Method | Path | Purpose | Tier |
|---|---|---|---|
| GET | `/api/knowledge-graph/summary` | counts, communities, last-index time, index health | A |
| GET | `/api/knowledge-graph/graph?level=N&community=C` | nodes+edges for a LOD level or a community (paginated) | A/B |
| GET | `/api/knowledge-graph/node/<id>` | one node + neighbors + provenance + report | A/B |
| GET | `/api/knowledge-graph/neighbors/<id>?depth=1..3` | ego-graph for expand-on-click | A |
| POST | `/api/knowledge-graph/query` | `{question}` → routed retrieval (`should_read`, path, answer) | A/B |
| POST | `/api/knowledge-graph/reindex` | trigger index (`{tier:"A"\|"B", scope, mode:"delta"\|"full"}`) → SSE progress | A/B |
| GET | `/api/knowledge-graph/search?q=` | fast entity/title search for the explorer's search box | A |
| WS | `/ws/knowledge-graph` | live events: node ignited, community changed, reindex progress | — |

Contract notes: `/graph` **must** return precomputed `x,y,z` (client does not cold-start a full simulation); responses are capped/paginated (server never streams 10k nodes unasked — it returns the top community level and lets the client drill down); the WS channel reuses friday-desktop's existing `/ws/live` pattern.

Frontend consumes this via the existing `apiFetch` wrapper (adds `X-Friday-Token`). Clicking a node fires `fridayNavigate('wiki', {file})` to open the page in `WikiWS`.

---

## 6. What "Knowledge System Overhaul" Means, Concretely

### 6.1 Ingestion
Sources → chunks. Wiki pages (bodies + frontmatter), conversation turns (already in ChromaDB), cognitive-memory facts, and dreaming-mined facts. Chunking reuses GraphRAG defaults (≈1200 chars / 100 overlap) for bodies; conversation turns and facts are atomic units. The `.manifest.json` tracks every ingested source by **canonical absolute path** (obsidian-wiki's rule — prevents double-ingest) with the pages/nodes it produced.

### 6.2 GraphRAG indexing (Tier B)
Per the workbench prompt pipeline, natively orchestrated in Python: `extract_graph` (entities+relationships) → `summarize_descriptions` (merge duplicate entity descriptions) → build graph → community detection (reuse Tier A's detector) → `community_report_{graph,text}` (LLM summaries per community) → embed entity/report text (local). Incremental: only re-extract chunks whose source changed since the manifest entry; recompute communities on the affected subgraph.

### 6.3 Entity/community extraction — and the wiki-link bridge
Tier A gives communities from link structure for free. Tier B adds *extracted* entities that aren't wiki pages (people/tools/concepts mentioned across sources). **New primitive Friday lacks today: wikilink + backlink parsing** — add `[[Page]]` parsing to the wiki engine so authored links become first-class edges, and offer `cross-linker`-style suggestions (ported from obsidian-wiki) to weave orphan pages in.

### 6.4 Retrieval paths for the agent
`knowledge_query` classifies (obsidian-wiki's `classify_query`) → **direct/list/gap** answers structurally (no LLM, returns `should_read`); **path** questions run BFS; **thematic** questions run global search over community reports; **specific-fact** questions run local search. New MCP/agent tools: `knowledge_query`, `knowledge_related`, `knowledge_path`, `knowledge_communities`, `knowledge_reindex`. The always-on context injector calls `knowledge_query` for the current topic and injects the `should_read` summaries.

### 6.5 Memory migration
No data moves; the graph *references* existing stores. Migration = a one-time full index that (a) turns every wiki page into a node, (b) parses/creates wikilinks as edges, (c) extracts entities from conversation + cognitive memory and links them with provenance, (d) attaches long-term facts to entities. Idempotent, manifest-guarded, fully re-runnable. `memory_dreaming` and `learning_loop` gain a post-step that writes newly discovered facts/entities into the graph (Phase 4).

### 6.6 The Microsoft-graphrag opt-in
A settings flag `knowledge_graph.power_indexer = "native" | "microsoft"`. `native` (default) uses the lightweight Python indexer above. `microsoft` installs `graphrag` on demand and drives its CLI, pointed at the local gated shim (§5.4-6), converting parquet→JSON via the workbench's converter approach. Off the happy path; for power users with large corpora.

---

## 7. Frontend Port: react-three-fiber → vanilla Three.js r128

friday-desktop has **no JS bundler**: React 18 + Three.js r128 (+ EffectComposer, RenderPass, ShaderPass, UnrealBloomPass) are **vendored as global scripts** in `static/vendor/`; JSX is precompiled by `scripts/build_ui.py`. Therefore:

- The workbench's `@react-three/fiber` / `@react-three/drei` components **cannot be dropped in.** Reimplement the renderer as a **vanilla Three.js r128** scene, following the pattern already in `ui_parts/styles_and_scene.html` (which already uses UnrealBloom — the exact aesthetic).
- **Reuse as algorithms, not code:** the force-simulation math (`lib/forceSimulation.ts`, d3-force-3d) → run **server-side** for layout (Python port or a one-off Node/worker step), store `x,y,z`; optional client-side refinement for small expanded subgraphs via a vendored `d3-force-3d` UMD in a Web Worker.
- **Reuse as design:** node/edge/bloom look, galaxy background, hover/selection behavior.
- Rendering primitives (see §8): `InstancedMesh` for node spheres, `LineSegments`/merged `BufferGeometry` for edges, sprite-atlas or on-demand labels.

---

## 8. Performance Budget (3D)

| Metric | Target | Floor |
|---|---|---|
| Framerate — ≤2,000 visible nodes / ≤5,000 edges | 60 fps | 30 fps |
| Framerate — via community aggregation (≤500 super-nodes) representing ≤10,000 entities | 60 fps | 30 fps |
| Initial render (2k nodes, precomputed layout) | < 1.5 s | < 3 s |
| Hover highlight latency | < 16 ms | < 33 ms |
| Click → wiki page open | < 100 ms | < 250 ms |
| Reindex (delta, ~20 changed pages, local model) | < 30 s | < 90 s |

Techniques required to hit these: instanced nodes (one draw call), merged edge geometry, **community LOD as the primary lever** (never render 10k individual nodes — aggregate and expand), frustum culling, labels only for near/selected nodes, precomputed server-side layout, bloom applied once to the whole scene (reuse vendored pass). Graceful degradation on weak GPUs: cap visible nodes, drop bloom, freeze layout. The renderer must **measure and log fps** so the Phase 3 acceptance test can assert the floor.

---

## 9. Phased Build Plan (one Fable session per phase)

Each phase: independently shippable, own tests, own acceptance gate. Build order minimizes risk (no-LLM → LLM → perf → integration).

### Phase 0 — Data model, store & manifest (foundation; no LLM, no UI)
**Scope:** `services/knowledge_graph/store.py` (`KnowledgeGraphStore`: read/write/rebuild the JSON artifacts, encryption-at-rest via `vault_crypto`, provenance model per §5.2); port obsidian-wiki `_slug`/index primitives + `.manifest.json` delta (canonical-path keys); `~/.friday/knowledge-graph/` layout; settings schema (`knowledge_graph.*`).
**Acceptance:** `KnowledgeGraphStore` round-trips all artifact types; manifest delta correctly identifies changed sources; TIER_2/3-derived artifacts are encrypted at rest and transparently read back; **no** LLM call, **no** route, **no** UI.
**Tests:** `tests/unit/test_knowledge_graph_store.py`, `test_knowledge_graph_manifest.py`.

### Phase 1 — Tier A: structural graph + wikilinks + structural query (no LLM)
**Scope:** `services/knowledge_graph/wiki_graph.py` (`[[wikilink]]`/backlink parsing over `~/.friday/wiki/`); port `graph_analysis.py` (communities, god-nodes, dead-ends, surprising connections) and `graphrag.py` (rank_candidates, find_path, classify_query, `should_read`); server-side layout pass producing `x,y,z`; `routes/knowledge_graph.py` (`/summary`, `/graph`, `/neighbors`, `/search`, structural `/query`, structural `/reindex`) + `ROUTE_MODULES` entry; agent tool `knowledge_query` (structural mode); wire `/api/wiki/*` saves to mark the graph dirty.
**Acceptance:** from the *existing* wiki, `/graph` returns nodes+edges+communities with layout; authored `[[links]]` appear as edges and backlinks resolve; `/query "how are X and Y related"` returns a BFS path + `should_read` with **zero** LLM calls; reindex(A) is incremental.
**Tests:** `tests/unit/test_wiki_graph.py`, `test_structural_query.py`; `tests/api/test_knowledge_graph_routes.py`.

### Phase 2 — Tier B: GraphRAG semantic index + retrieval (LLM, gated)
*If too large for one session, split at the marked line into 2a (indexer) / 2b (retrieval).*
**Scope (2a — indexer):** `services/knowledge_graph/indexer.py` — chunk → `extract_graph` → `summarize_descriptions` → community detection (reuse Phase 1) → `community_report_*` → local embeddings; **all LLM via `_generate_text`**; classify-before-extract; TIER_3 → local-only-or-skip; incremental via manifest; SSE progress via `orb_label`; encrypt derived artifacts; GraphRAG prompt set vendored under `services/knowledge_graph/prompts/`. — *split —* **Scope (2b — retrieval):** local search, global search (map-reduce over reports), drift; `knowledge_query` router upgraded to route structural/local/global; tools `knowledge_related`, `knowledge_communities`.
**Acceptance:** `reindex(B)` on a sample corpus produces entities/relationships/communities/reports; **runs fully offline with Ollama** (no cloud); with a cloud model, the **egress adversarial test passes — no vault/health/finance content leaves the device**; global search answers a thematic question citing community reports; local search answers a fact with provenance; `gate_operational()==False` disables cloud indexing.
**Tests:** `tests/unit/test_kg_indexer.py`, `test_kg_retrieval.py`; `tests/security/test_kg_egress_adversarial.py` (extends the existing egress adversarial suite); `tests/api/test_kg_reindex_route.py`.

### Phase 3 — 3D Knowledge Explorer (friday-desktop frontend)
**Scope:** new workspace `KnowledgeGraphWS` in `ui_parts/app.html` (register in `wsMap` + `DOCK_GROUPS`); vanilla Three.js r128 renderer (`InstancedMesh` nodes, merged `LineSegments` edges, community LOD/aggregation + expand-on-click, vendored UnrealBloom, holographic palette + mood colors); consume `/api/knowledge-graph/*`; interactions (orbit/zoom, hover-highlight-neighbors, click→`fridayNavigate('wiki',…)`, search→constellation highlight); live "ignite" via `/ws/knowledge-graph`; an fps meter.
**Acceptance:** renders ≥2,000 nodes at ≥30 fps (measured) and ≥10,000 via aggregation at ≥30 fps; click-to-wiki opens the page in `WikiWS`; search highlights matches; a newly learned fact ignites a node live; visually consistent with the holographic theme; meets §8 latencies.
**Tests:** `tests/api` for the data endpoints; Playwright smoke (`@playwright/test` already present) asserting the workspace mounts, renders a canvas, and click→wiki navigates; a headless fps-floor assertion where feasible.

### Phase 4 — Integration, migration & hardening
**Scope:** upgrade always-on context injection to graph-aware `knowledge_query`; post-steps in `memory_dreaming` + `learning_loop` that write discovered facts/entities into the graph; one-time migration pass (wiki→nodes, conversation/cognitive→provenance-linked entities); large-graph perf hardening; settings UI (index budget caps, native vs. microsoft indexer, per-section inclusion, cloud-vs-local indexing toggle, nightly reindex with dreaming); docs update.
**Acceptance:** end-to-end — a fact learned in chat appears (approved) in the wiki, as a graph node, and ignites in the 3D view; migration is idempotent (re-run = no dupes); large-corpus perf holds; all settings honored; egress suite still green.
**Tests:** `tests/api/test_kg_integration.py`; migration idempotency test; regression run of the full egress + wiki suites.

---

## 10. Test Plan (cross-cutting)

- **Framework:** `pytest` (`tests/unit` fast/no-Flask, `tests/api` Flask `client` fixture, `tests/security`, `tests/smoke`), fixtures `test_home`/`friday_dir`, `FRIDAY_TESTING=1`. Frontend: `@playwright/test`.
- **Security (non-negotiable):** an adversarial egress test seeded with SSN/health/finance strings in wiki sources must show **zero** sensitive spans in any cloud-bound payload during indexing; local-only indexing of TIER_3 verified; `gate_operational()==False` blocks cloud indexing. Extends `tests/security/test_egress_gate_adversarial.py`.
- **Determinism:** structural graph, community detection, and layout must be deterministic given fixed input (seed the layout) so tests and the UI are stable.
- **Idempotency:** reindex(delta) after no change = no-op; migration re-run = no dupes.
- **Performance:** Phase 3 asserts the fps floor and click-to-wiki latency.
- **Offline:** the entire pipeline must pass with **no network** (Ollama + local embeddings) — a first-class CI mode, matching Friday's offline-first posture.

---

## 11. File Map (build target: friday-desktop; Option A)

```
src/agent_friday/
  services/knowledge_graph/
    __init__.py
    store.py            # Phase 0  KnowledgeGraphStore + encryption + manifest
    wiki_graph.py       # Phase 1  wikilink/backlink parse + graph build
    graph_analysis.py   # Phase 1  ← ported from obsidian-wiki (communities, hubs…)
    structural_query.py # Phase 1  ← ported from obsidian-wiki graphrag.py (should_read)
    layout.py           # Phase 1  server-side x,y,z (d3-force-3d port)
    indexer.py          # Phase 2a GraphRAG extraction (via _generate_text)
    retrieval.py        # Phase 2b local/global/drift search + router
    prompts/            # Phase 2  ← vendored verbatim from graphrag-workbench
  routes/knowledge_graph.py   # Phase 1+ Flask blueprint (add to ROUTE_MODULES)
ui_parts/app.html               # Phase 3 KnowledgeGraphWS workspace (+ wsMap, DOCK_GROUPS)
static/vendor/d3-force-3d.min.js# Phase 3 (optional) client-side subgraph refinement
docs/KNOWLEDGE_SYSTEM_SPEC.md   # this file (copy from asimovs-mind/docs)
tests/unit|api|security|smoke/…
~/.friday/knowledge-graph/      # runtime derived index (git-ignored user data)
```

Reused unchanged: `services/model_router.py`, `services/egress_gate.py`, `privacy/vault_crypto.py`, `conversation_memory.py` (ChromaDB/MiniLM), `wiki_engine.py`, `server.py` blueprint discovery.

---

## 12. Open Questions for Stephen

**Q1 — ⚠️ Repo boundary (blocks Phase 0 scope).** Approve **Option A (build in friday-desktop)**? If you want **Option B (backend in asimovs-mind)** as the brief literally states, the deltas are: port Tiers A/B to Node in friday-core; define an egress-gate **proxy** contract so friday-core's LLM calls are gated by friday-desktop (or duplicate the gate — not recommended); and a wiki-sync protocol between the two stores. My strong recommendation is A now, with C (expose over the MCP bridge) later if another agent needs the graph.

**Q2 — GraphRAG engine default.** Confirm **native lightweight indexer** as default with Microsoft `graphrag` as an opt-in "power" mode? Or do you want Microsoft `graphrag` as the primary engine (heavier deps: pandas/pyarrow/lancedb/tiktoken/graspologic; parquet pipeline) from day one?

**Q3 — Cloud indexing policy.** Default posture for Tier B extraction: **local-only** (Ollama, fully private, slower/lower-quality) or **gated-cloud allowed for TIER_1 content** (better extraction, health/finance still never leave device)? This sets the default of the cloud-vs-local toggle.

**Q4 — Scope of what gets indexed.** Should Tier B index **conversation memory and cognitive memory** by default, or **wiki-only** at first (conversations opt-in)? Indexing all chat history is powerful but expensive and privacy-heavy.

**Q5 — Storage format.** Start with **JSON artifacts** (simple, matches the workbench contract) and migrate to **SQLite** only if node counts demand — acceptable? Or go straight to SQLite/DuckDB?

**Q6 — Embeddings.** Keep **`all-MiniLM-L6-v2`** (already in the app, on-device) for graph embeddings, or introduce a stronger local model (e.g. `nomic-embed-text` via Ollama, which asimovs-mind's memory already uses)?

**Q7 — Wikilink migration.** Existing wiki pages have **no** `[[links]]`. Should Phase 1 run the ported **cross-linker** to *propose* links across the whole vault (via the existing pending-approval workflow, so you review them), or only track links you add going forward?

**Q8 — 3D scope for v1.** Is a **read-only explorer** (fly, inspect, click-to-wiki, search) the right v1, with editing/curating from within the 3D view deferred to a later phase?

---

## 13. Appendix — Provenance & sovereignty invariants (must not be broken)

1. **Wiki markdown is the source of truth.** The graph index is derived and may be deleted/rebuilt at any time without data loss.
2. **Every node/edge carries provenance** back to its wiki page / conversation / cognitive key — never an uncited copy.
3. **No new uncontrolled egress.** Every cloud LLM/embedding call during indexing passes `egress_gate.seal_outbound`; TIER_3 content is local-only or skipped; the derived index inherits its sources' sensitivity for at-rest encryption.
4. **Offline-capable.** The full pipeline runs with no network (Ollama + local embeddings).
5. **Deterministic & idempotent.** Same inputs → same graph; reindex/migration re-runs never duplicate.
6. **Human-in-the-loop for writes.** Agent-proposed wiki changes and cross-links flow through the existing pending-approval queue.

*End of specification.*
