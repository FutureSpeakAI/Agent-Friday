# Agent Friday v5.5.0 — release candidate

*2026-08-21 · FutureSpeak.AI*

This release fixes a large number of things that were quietly broken and
documents a set that still are. Most of what follows had been failing for weeks
without announcing itself, and nearly all of it was found by *using* Friday
rather than by reading her.

There is no new headline feature. The installer is new because the old one
could not install; the tutorial is new because nobody had ever walked a stranger
through a first conversation. `KNOWN_ISSUES.md` is new and is the most useful
file in this release.

---

## The pattern behind most of these fixes

Friday's dominant failure mode is confident wrongness, and her second is hiding
her own injuries. She did not crash. She reported success.

- The server spawned its child with `stderr=DEVNULL`. Six overnight startup
  failures left no trace anywhere; the cause was a one-line import error.
- A whole API surface failed to register for **seven weeks and ~70 restarts**,
  logging one warning per boot that nobody read. `/api/health` returned 200
  throughout.
- Every voice conversation's wiki distillation was discarded for weeks. The task
  routed to a model that was not resident, received HTTP 404, and reported
  **"Task complete."**
- The system tray reported `FAILED TO START` on every *successful* start,
  because it waited 30 seconds against a measured 143-second boot.
- Local voice transcribed speech, emitted `status: thinking`, and never spoke.
  The readiness gate certified the microphone and the speaker and never asked
  whether the brain between them existed.
- An MCP subsystem with ~99 registered tools reported itself dead for the life
  of every process.

The rule that follows from it, now written into `KNOWN_ISSUES.md`:

> **Nothing in Friday may claim success it has not verified.**

Two specific bug classes are named there, because both recurred repeatedly and
both are greppable. The first is a comparison that discards the part of the
value carrying the meaning — a name's prefix instead of the name, a number's
magnitude instead of its sign. Nine instances were found. The second is an
assertion loose enough to accept a failure mode it wasn't testing for. The third
is evidence about a component that isn't in the path: two models were
benchmarked, real numbers produced, a seating plan built on them and an
installer written to download them, and nothing in the codebase loads either
one.

Worth stating plainly, because it shows the class is live rather than tidied
up: **the pattern appeared three times inside the fixes for itself.** The
installer's model planner reproduced the exact defect it was written to prevent.
The benchmark harness written to investigate the others shipped the same bug
twice. And while fixing a command that computed a correct failure and exited 0,
we found `cmd_health` returning a bool — `sys.exit(False)` is `0`, so a failed
health check had been reporting success.

---

## Fixed

**Startup and process integrity**

- The server could not start at all — a module-level use-before-definition.
  A ~3.5s import check is now wired into launch, pre-commit and CI.
- Child stdout and stderr are captured to `~/.friday/server_stderr.log`. The
  tray distinguishes "failed to start (exit N)" from "stopped", and its health
  wait went from 30s to 300s against a measured ~143s cold start.
- Blueprint registration failures are tiered: a required module failing exits
  loudly, an optional one starts but announces in the log, a notification, and
  `~/.friday/startup-report.json`. `GET /api/startup-report` serves it. Proved
  by fault injection, not the happy path.
- Any background task whose reply is a provider error is marked failed and
  reported as failed, instead of being summarised verbatim as a completed task.

**Correctness**

- `tool_results.append` was indented inside an `except` in `_call_claude_agent`,
  so on the normal path tool results were never appended at all.
- Chat turns died on request bodies containing non-ASCII UTF-8, and on empty
  messages. Both now handled; tracebacks reach the log rather than a stderr that
  does not exist under `pythonw`.
- Creation filenames used a fourteen-digit timestamp that matched the
  credit-card detector, so the tool result naming a successful render was
  withheld from the model. Friday could not see her own output and reported
  working videos as blocked.
- `/api/mcp/status` read the manager by value at import, pinning it to `None`.
- `friday doctor` reported a model installed when a *sibling tag* was installed.
- CLI commands now propagate exit codes. `friday models --install` computed a
  correct failure, printed it, and exited 0; no command in the file propagated
  one.

**Privacy and safety**

- Tool descriptions were run through the content classifier and blanked, so on
  cloud-fallback turns the model received a list of tools it could not read. A
  tool named `remember_contact` had its description removed for saying it stores
  phone numbers. Gating is now scoped to third-party MCP tools.
- TIER-2 sensitivity over-triggered on ordinary words. "family picture-book
  aesthetic" was rated PRIVATE and force-routed to a local seat that could not
  hold the payload, killing the turn.
- The secret scanner blocked a correct config read (`api_key=core.GEMINI_API_KEY`)
  and a code comment. A scanner that flags correct code teaches people it cries
  wolf. Fixed with a carve-out so no known key shape is ever exempted.
- Package import no longer prints the *names* of secrets loaded from launch
  scripts — including `FRIDAY_PASSWORD` — to stdout on every command and test
  run.
- Tag `v4.4.0` and 93 local `archive/*` tags were deleted. All 42 origin tags
  were audited; `v4.4.0` was the only one carrying pre-scrub content.

**Install**

- The one-line installer cloned `friday-desktop.git`, which does not exist. The
  repository is `Agent-Friday.git`. Every fresh install died at the clone.
- Both installers ran `setup_wizard.py` from the wrong directory.
- All three installers and `friday update` regenerated `index.html` from a dead
  mirror, deleting 17 components that exist only in the served file and silently
  downgrading to CDN Babel. No shipped code path writes `index.html` any more.

---

## New

**`friday models`** detects RAM, disk and GPU and reports what your machine can
run *before* downloading anything. Every refusal names its rule and shows the
arithmetic, and it downloads **only models something actually loads**.

Two things worth stating plainly, because the first version of this got both
wrong:

**Memory needs no GPU.** It runs on `all-MiniLM-L6-v2` through
sentence-transformers, a declared pip dependency, so it arrives with the install
rather than as a model download.

**Tools do not work on a local brain today, on any machine.** `function_manager`
exists as a role in the residency contract and nothing in the chat path consults
it, so a local model has no function seat to delegate to. A local-only Friday
converses and remembers; she does not act. Tools need a cloud key. This is a
missing wire, not a hardware limit — see `KNOWN_ISSUES.md`.

`friday models --install` verifies each model against the daemon's own inventory
after downloading. A pull that exits zero having fetched no weights is reported
as failed.

**`docs/TUTORIAL.md`** takes a stranger from clone to one working conversation
and stops.

**`KNOWN_ISSUES.md`** lists what is broken, what is unverified, and what leaves
your machine.

---

## Not verified

This section is separate from "known issues" on purpose. Nothing here is a claim
that something works.

**Proven on a clean Ubuntu machine, from a fresh clone of the pushed branch:**
`friday --help` lists `models`; the tutorial is present; free space reads 9 GiB
against a ground truth of 8.8; every refusal shows its arithmetic; a real
`qwen3:8b` pull was verified against the daemon's inventory and exited 0; the
venv instructions resolve the PATH problem.

**Not proven, and needing a Windows machine:**

- Whether the planner correctly declines to re-propose already-installed models
  against a real inventory.
- The secret-name fix against actual launch scripts. The clean box had none, so
  that pass proved only that nothing crashed.
- Any GPU branch of the planner against real `nvidia-smi` output.

**Not proven, and needing one conversation:**

- **Local voice is fixed in code and unproven in practice.** The local session
  now pins a resident brain and refuses to start when none exists, and the code
  compiles and is tested. But nobody has spoken to Friday, received a spoken
  answer, and confirmed from the egress log that nothing left the machine. Until
  someone does, treat local voice as unproven. The proof is one conversation
  plus a look at `~/.friday/friday.log` for an egress line during the turn — if
  the gate logs nothing, nothing left.

**Not proven, and unmeasured:**

- CPU generation throughput, for any model. The planner says so where it
  recommends a CPU brain.
- 8 GB VRAM. The hardware fixture representing it carries measurements copied
  from a 12 GB card.
- The KV-cache slope the entire seat-planning model rests on. It is inferred
  from two anchors taken on two different backends, and a directly measured
  slope on one model was an order of magnitude away from it.

**Structurally broken and deliberately not fixed here:** a wheel install cannot
run the career pipeline. `data/` and `skills/` are top-level directories that
are not packaged. Installing from a clone avoids it entirely. Resolving it
properly means deciding what the skills system *is*, which is a product decision
and not something to answer inside a bug fix.

---

## Before this is announced

**Do not publish or announce a release until these are rotated.** The reason is
specific rather than ceremonial: the repository is already public, and drawing
attention to it while these are live is the one outcome the tag cleanup existed
to prevent.

1. **The Google / Gemini API key.**
2. **`FRIDAY_PASSWORD`** — it derives the key protecting the credential store.
   Check `services/credential_store.py` for a re-key path first, or you will
   lock yourself out of your own vault.
3. **The Discord invite** `discord.gg/f2VM6qNk` — revoke and reissue. It was
   public in tag `v4.4.0` until today. Deleting the tag does not purge GitHub's
   object store; forks, PR refs and caches persist independently, so revoking
   the invite is what actually closes it.

Also worth doing before an announcement: `FRIDAY_SECRET_KEY` currently ships as
a known default string, which means forgeable sessions on any exposed instance.

---

*The known-issues file is long. That is the argument for this release, not
against it — it is shorter than the list of things that work.*

---

<details>
<summary>Previous release — v5.4.0</summary>

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

</details>
