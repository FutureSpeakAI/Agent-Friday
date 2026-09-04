# Seam coverage

Legend: UNSTARTED / IN PROGRESS / SWEEP 1 CLEAN / SWEEP 2 CLEAN (done) / PARKED

## Exit criteria (corrected 2026-09-04, per handoff from the session that
## wrote the gauntlet-loop prompt — see progress.md "Handoff" section)

The prompt's original wording ("two consecutive fresh-context sweeps ...
and every claim on it holds blind") cannot be satisfied for a seam carrying
a BROKEN finding whose fix is queued rather than landed — a queued fix
never "holds." The corrected exit condition, which this run uses:

A seam is done when **two consecutive fresh-context sweeps surface nothing
new**, AND **every claim on it carries a verdict with an artifact**:
- HOLDS — the observation/call-graph that established it (findings.jsonl `H*` entries), or
- BROKEN/UNREACHED — a red probe under tests/gauntlet/ pinning it (fixed: probe now green with red/green/red evidence; queued: probe stays red, evidence + reason in progress.md's QUEUED FOR STEPHEN section).

Green-everywhere is not the bar. Judged-everywhere is.

| Seam | Status | Notes |
|---|---|---|
| Settings write → settings read | SWEEP 3 FOUND NEW (F22, federation sync overlay, queued) — needs a 4th clean sweep | Round 1: F3 (context_retention_days, queued+probe-pinned), F6 (camera_auto_describe, dead, low-priority), 7 HOLDS confirmed. Round 3: fresh-context second sweep dispatched (Voice/Creative/Scheduler/Notification/Connector settings panels specifically, outside what round 1 covered). |
| Prompt assembly → provider payload | SWEEP 1 CLEAN (F12 verified unreachable today, fixed as hardening anyway) | Round 2: KG retrieval, context_injection, web_fetch/firecrawl, /api/analyze, and routes/voice.py's Gemini Live gating all traced and HOLD. F12: egress_gate.py has zero handling of the OpenAI-compatible `tool_calls` wire field (vs. the already-fixed Anthropic `tool_use` equivalent) — structural gap confirmed, but the two obvious cross-call vectors are incidentally closed. A dedicated verifier is checking the one open question (worker/orchestrator/sub-agent retry paths carrying tool_calls cross-provider) before this seam can close. |
| Router request → seat actually served | SWEEP 4 CLEAN (nothing new — 1 of 2 consecutive clean sweeps needed) | Round 1: F2 (provider_health gaps, FIXED), H2/H3/H6 confirmed HOLDS. Round 3: fresh-context second sweep dispatched (_route_basic's other branches, _apply_cloud_provider substitution risk, other seat-guess functions besides effective_seat, stale conversation_seat overrides). |
| UI copy ↔ code (index.html / app.html labels vs behavior) | SWEEP 1 CLEAN | Round 1 covered settings-panel toggles (F3/F5/F6/F7). Round 2 covered onboarding_copy.py (F10 — SEVERE, local_only's "nothing sent, ever" is false by default, queued as top priority; F11 — vault-passphrase copy overstatement, queued; H11 — 6 other claims confirmed accurate). Needs 1 more clean fresh-context sweep before this seam can close, given F10's severity. |
| index.html ↔ ui_parts/app.html divergence | SWEEP 1 CLEAN (F13: stale "17" count corrected, real gap is 14 and growing, 4 high-severity components pinned+queued) | Round 1 found the orchestrator_model/creative_model flat-key divergence (F7, HOLDS-with-latent-risk, not fixed — currently works). Full component-count diff (KNOWN_ISSUES.md's "17 components the mirror lacks" claim) not independently re-verified yet. |
| Repo → installer payload (packaging/windows) | SWEEP 2 FOUND NEW (Q8: disk_warning computed but never gates the download, queued) — needs a 3rd clean sweep | Round 2: Q5 (embedder "arrives with install" claim false on the installer's own recommended no-GPU path, queued, low severity), model-id defaults vs. installer ladder HOLD, INSTALLATION.md vs install.ps1 sizes/steps HOLD, jobs/skills top-level-import concern confirmed scoped to pip-install only (Windows installer payload includes both dirs, HOLDS), no Breeze TTS2 or other NC-licensed leakage found. Needs 1 more clean fresh-context sweep. |
| Voice pipeline end-to-end | SWEEP 3 CLEAN (nothing new — 1 of 2 consecutive clean sweeps needed; also surfaced a real test-infrastructure gap: flask-sock websocket handlers are structurally untestable behaviorally in this suite, only source-level pins are possible) | Round 1 (via the F4 investigation) confirmed the screenshot/vision-attachment sub-path respects local-only routing (H9, HOLDS, fixed 2026-08-23). Round 2's prompt-assembly finder also confirmed routes/voice.py's Gemini Live text/tool-result gating HOLDS. Broader voice pipeline (STT/TTS audio path itself, live voice barge-in, Tier-1/Tier-2 fallback, and Q6's unmetered Gemini TTS/Live billing gap) not yet fully swept. |
| Scheduler & workflows | SWEEP 1 CLEAN (F17: chain_run_status retry-reset bug, fixed; registration→execution chain, UI-action/handler correctness, and error/retry handling for a failing step all otherwise confirmed HOLDS) | Round 1 found + fixed the orphaned nightly KG reindex job (F1). Scheduler's other ~15 builtin tasks spot-checked incidentally but not exhaustively; routes/workflows.py and workflow_plan.py (the "workflows" half of this seam) not yet swept. |
| Startup wiring | PARKED (dynamic, confirmed unprovable by env vars alone) / IN PROGRESS (static) | Read server.py:220-401. Finding: `if not _TESTING:` at module scope (line 317) gates the ENTIRE daemon cascade — MCP connector launch (`_mcp_boot()`, line 375), real provider-key decryption into env (`credential_store.bootstrap_provider_env()`, line 321-326), residency/GPU boot (line 400), scheduler, network monitor, connector-health monitor — as a side effect of merely *importing* server.py, not of reaching `__main__`. `FRIDAY_NO_ARBITER=1` only gates the residency/GPU thread (line 399); it does NOT gate MCP boot or credential decryption. So env vars alone cannot satisfy conditions 3 (no MCP servers) or 4 (no real provider key) — and per existing memory ([[gotcha_isolated_home_does_not_isolate_keychain]]) redirecting HOME/USERPROFILE does not isolate the Windows keychain or the GPU either, so condition 1/4 aren't safe that way either. Verdict: cannot prove all four conditions with a launcher built from env vars + redirected profile. Dynamic boot stays parked; would need to actually patch/stub `_mcp_boot`, `bootstrap_provider_env`, and the residency arbiter before import, which is a code change, not a launch config — out of scope for an unattended overnight run. Independently reconfirmed by the aborted Fable 5.1 session's parallel static read (see progress.md Handoff item 3) — same evidence, same call. Static reachability tracing continues. |
| Knowledge graph ingest → retrieval | SWEEP 2 FOUND NEW (Q9: forget-me description leak, queued; Q10: no correction-invalidation + unbounded growth, queued) — needs a 3rd clean sweep | Round 1: F1 (nightly reindex orphaned, FIXED), H4 (indexer method-name fix confirmed correct and now reachable via the fix). Round 2's prompt-assembly finder additionally confirmed KG retrieval content is double-protected (sensitivity-based model pinning + full egress classification) before reaching cloud. Needs 1 more clean fresh-context sweep. |
| MCP and connector registration | SWEEP 2 CLEAN (nothing new — 1 of 2 consecutive clean sweeps needed) | Round 2: F8 (restart() bypassed the security-disable gate, FIXED), F9 (status mislabeling for a security-blocked connector, queued — the observability half of the same cluster), H10 (registration→tool-callable chain, OAuth expiry disclosure, local-seat tool dropping disclosure, no orphaned registration function — all confirmed HOLDS). Needs 1 more clean fresh-context sweep. |
| Cost and budget accounting | SWEEP 2 FOUND NEW (Q7: creative-gen unmetered + a budget gate that undercounts its own spend, queued) — needs a 3rd clean sweep | Round 2: Q6, three-part finding, all queued — (a) USD budget alert-only-by-design and off by default (documented intentional, root cause of the historical incident, still unaddressed), (b) budget_enforcer fail-closed but scoped to a synthetic unit disconnected from USD and explicitly fail-open-on-error, (c) Gemini TTS/Live voice calls completely unmetered despite PRICING table entries showing clear intent to track them — highest-priority sub-item. routes/costs.py confirmed to read the live ledger with no drift (HOLDS). Checked the finder's minor note that GET /api/costs/budget lacks @login_required while the sibling POST has it: all three OTHER GET routes in the same file (/api/costs/summary, /timeseries, /scheduled) are equally unguarded, so this is the file's consistent read-vs-write pattern, not an anomaly — HOLDS, not a fix target. Needs 1 more clean fresh-context sweep.|

## Claim corpus extraction

**Correction 2026-09-04 (Stephen's check-in):** this table previously said
UNSTARTED for every source despite progress.md describing a completed round-1
claim-extraction pass and 6 agents having returned full claim lists — that
was a bookkeeping failure (the claims were reported back in agent transcripts
but never transcribed into claims.jsonl or reflected here). claims.jsonl now
holds C1-C90, extracted from the round-1 and round-2 agent reports, each
with a quote, location, and either a `linked_finding` (F#/H#) or a `status`
of `verified_holds` / `verified_BROKEN*` / `noted_not_independently_chased`
(extracted and read, but not individually traced to a verdict this round —
honestly marked as such, not silently dropped).

| Source | Status |
|---|---|
| THREAT_MODEL.md | EXTRACTED (C1-C13) — 4 independently verified, 9 noted-not-chased |
| KNOWN_ISSUES.md (incl. 2026-09-03 entries) | EXTRACTED (C14-C41) — most "Fixed" entries independently re-verified against current code, several still-open items confirmed still open |
| docs/FILE_GRANTS.md | EXTRACTED (C42-C49) — 5 rules noted, 1 partially verified (no-grant-tool half); deeper per-rule verification not yet done |
| README.md | EXTRACTED (C50-C59) |
| RELEASE_NOTES.md | EXTRACTED (C60-C63) — historical entries, low priority for this release's focus |
| docs/CONFIGURATION.md | EXTRACTED (C64-C67) — defaults cross-checked against DEFAULT_SETTINGS |
| docs/API.md | EXTRACTED (C68-C73) — route-registration cross-check done (no documented-but-missing routes found) |
| tests/README.md | EXTRACTED (C74-C77) — 2 of 4 claims checked (route count, suite timing) found to be planning claims rather than assertions/facts, per Stephen's explicit correction; treated as corpus items to judge, not trusted |
| index.html (settings labels, disclosures, Saved affordances) | EXTRACTED (C78-C82) — settings-panel toggles covered; broader disclosure-string sweep (onboarding aside) not exhaustive |
| ui_parts/app.html (same) | PARTIAL — covered via the divergence check (F7), not independently re-read end to end |
| src/agent_friday/services/*.py docstrings | PARTIAL — onboarding_copy.py, provider_health.py, cost_meter.py, egress_gate.py docstrings specifically checked (C83-C90); not every service module's docstrings individually corpus-walked |
| src/agent_friday/routes/*.py docstrings | NOT SYSTEMATICALLY DONE — individual routes checked as part of seam work, no dedicated docstring sweep across all ~61 route files |
| Implicit claims: exported fns / UI controls / settings keys / routes / scheduled jobs / worker adapters actually reachable | IN PROGRESS — this is the core method used throughout (F1, F8, F9, F12 are all reachability findings), not a one-time pass; continues every round |
