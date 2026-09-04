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
| Startup wiring | PARKED (dynamic, confirmed unprovable by env vars alone) / IN PROGRESS (static) | Read server.py:220-401. Finding: `if not _TESTING:` at module scope (line 317) gates the ENTIRE daemon cascade — MCP connector launch (`_mcp_boot()`, line 375), real provider-key decryption into env (`credential_store.bootstrap_provider_env()`, line 321-326), residency/GPU boot (line 400), scheduler, network monitor, connector-health monitor — as a side effect of merely *importing* server.py, not of reaching `__main__`. `FRIDAY_NO_ARBITER=1` only gates the residency/GPU thread (line 399); it does NOT gate MCP boot or credential decryption. So env vars alone cannot satisfy conditions 3 (no MCP servers) or 4 (no real provider key) — and per existing memory ([[gotcha_isolated_home_does_not_isolate_keychain]]) redirecting HOME/USERPROFILE does not isolate the Windows keychain or the GPU either, so condition 1/4 aren't safe that way either. Verdict: cannot prove all four conditions with a launcher built from env vars + redirected profile. Dynamic boot stays parked; would need to actually patch/stub `_mcp_boot`, `bootstrap_provider_env`, and the residency arbiter before import, which is a code change, not a launch config — out of scope for an unattended overnight run. Static reachability tracing continues. |
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
