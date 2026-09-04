# Seam coverage

Legend: UNSTARTED / IN PROGRESS / SWEEP 1 CLEAN / SWEEP 2 CLEAN (done) / PARKED

| Seam | Status | Notes |
|---|---|---|
| Settings write → settings read | UNSTARTED | |
| Prompt assembly → provider payload | UNSTARTED | |
| Router request → seat actually served | UNSTARTED | |
| UI copy ↔ code (index.html / app.html labels vs behavior) | UNSTARTED | |
| index.html ↔ ui_parts/app.html divergence | UNSTARTED | |
| Repo → installer payload (packaging/windows) | UNSTARTED | |
| Voice pipeline end-to-end | UNSTARTED | |
| Scheduler & workflows | UNSTARTED | |
| Startup wiring | PARKED (dynamic) / IN PROGRESS (static) | Dynamic second-instance boot needs 4-condition proof (GPU-off, real-Ollama-off, no MCP servers, no real provider key) via server.py daemon block + _residency_boot — not yet done. Static call-graph reachability proceeds without booting anything. |
| Knowledge graph ingest → retrieval | UNSTARTED | Memory already flags indexer bug class here (recent_turns()) — check for recurrence/other instances |
| MCP and connector registration | UNSTARTED | |
| Cost and budget accounting | UNSTARTED | |

## Claim corpus extraction

| Source | Status |
|---|---|
| THREAT_MODEL.md | UNSTARTED |
| KNOWN_ISSUES.md (incl. 2026-09-03 entries) | UNSTARTED |
| docs/FILE_GRANTS.md | UNSTARTED |
| README.md | UNSTARTED |
| RELEASE_NOTES.md | UNSTARTED |
| docs/CONFIGURATION.md | UNSTARTED |
| docs/API.md | UNSTARTED |
| tests/README.md | UNSTARTED |
| index.html (settings labels, disclosures, Saved affordances) | UNSTARTED |
| ui_parts/app.html (same) | UNSTARTED |
| src/agent_friday/services/*.py docstrings | UNSTARTED |
| src/agent_friday/routes/*.py docstrings | UNSTARTED |
| Implicit claims: exported fns / UI controls / settings keys / routes / scheduled jobs / worker adapters actually reachable | UNSTARTED |
