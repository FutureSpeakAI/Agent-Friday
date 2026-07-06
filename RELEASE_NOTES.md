# Agent Friday v5.4.0 — "Second Brain"

*Release date: 2026-07-06 · FutureSpeak.AI · Asimov's Mind*

Friday has always *kept* your knowledge — a wiki, memories, a soul file, a
learning loop. As of this release she **connects** it, and you can fly
through it.

---

## The Knowledge Galaxy

Open the new 🌌 **Knowledge** workspace (or hit **Galaxy** in the Wiki) and
your knowledge base renders as a living galaxy: every wiki page a star,
every link and cross-mention a filament of light, your wiki sections
clustered into named, glowing constellations. The camera flies in from deep
space while your constellations ignite one by one — then it's yours: orbit,
zoom, hover a star to trace its connections, double-click to open the page
in the Wiki. Search lights up a constellation. When Friday learns something
new — a fact consolidated by overnight memory dreaming, a skill promoted by
the learning loop — a new star ignites, live.

It holds its frame rate on real GPUs, degrades gracefully on weak ones, and
the whole experience runs with zero network — stars, physics, bloom, and
data are all local.

## The graph underneath

Two tiers, spec'd in `docs/KNOWLEDGE_SYSTEM_SPEC.md`:

- **Tier A — structural, always on, no LLM.** The wiki *is* the graph:
  `[[wikilinks]]`, markdown links, and title-mentions become edges;
  communities come from your own organization; the layout is computed
  server-side and deterministically. Rebuilds in milliseconds on every wiki
  save. A structural query engine answers "how is X related to Y?" with
  actual link paths and tells the agent exactly which 2–3 pages are worth
  opening — no LLM call, no waiting, works offline.
- **Tier B — semantic, opt-in, sovereign.** Microsoft-GraphRAG-style entity
  and relationship extraction across the wiki, SOUL.md, conversation memory,
  and cognitive memory, with LLM-written community reports and local
  embeddings. **Local-only by default** — nothing leaves your machine unless
  you explicitly enable gated-cloud mode, and even then: content classified
  TIER_2/3 is pinned to local models in *every* mode, a failed egress-gate
  self-test disables cloud indexing outright, and anything derived from
  sensitive sources is AES-256-GCM vault-encrypted at rest. An adversarial
  test suite plants fake SSNs and health data in the corpus and proves they
  never reach a cloud-eligible call.

New agent tools ride the graph too: `knowledge_query` (structural answers +
reading shortlists), `knowledge_related`, and `knowledge_communities` — all
Ring 0, instant, offline.

Control it under **Settings → Knowledge Graph**: indexing mode, which
corpora to index, constellation grouping, nightly reindex (03:30, right
after memory dreaming), and manual rebuild buttons.

## Voice: Gemini Live fluidity

The Tier-3 cloud voice got a tuning pass verified against Google's current
Live API documentation:

- **Barge-in by default.** Speak over Friday and she stops within a frame —
  the old default silently disabled interruption entirely. Open-speaker
  setups keep an explicit no-interruption opt-out.
- **No more progressive rasp.** The playback worklet now runs on a 120 ms
  jitter cushion (re-primed after any underrun) with an anti-wrap ring
  guard — hours-long calls stay clean.
- **Hours-long sessions.** Context-window compression is on by default,
  removing the ~15-minute session cap; combined with session resumption and
  reconnect draining, multi-hour conversations carry full context.

## Also in this release

- Offline-first hardening: web fonts and MediaPipe now load asynchronously —
  a dead or stalling network can no longer block first paint.
- `/api/knowledge-graph/*` — nine new endpoints, documented in
  `docs/API.md`; SSE event stream for live graph updates.
- 78 new backend tests including the adversarial egress suite, plus a
  Playwright spec asserting the galaxy mounts, holds its fps floor, and
  click-through to the Wiki works.

---

**Install:** download `AgentFriday.exe` below (Windows, no Python needed),
or `pip install -e .` from source — see `docs/INSTALLATION.md`.

**Upgrade note:** the knowledge graph builds itself on first open of the
Knowledge workspace; no migration steps. Your wiki is never modified — the
graph is a derived index you can delete or rebuild at any time.

---

*Every capability in this release is governed by Asimov's cLaws, gated by the
sovereign egress classifier, and provable via Ed25519 content credentials.
Friday distributes; you own.*
