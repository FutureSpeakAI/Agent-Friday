# Gauntlet Audit — 2026-09-03

Live page. Overnight autonomous run, dispatched by Stephen via a relayed
gauntlet-loop prompt (drafted earlier at `docs/gauntlet-loop-prompt`,
worktree `.claude/worktrees/gauntlet-prompt`, commit 7d77304). Executed
unattended per his standing authority; judgment calls made along the way
are logged here as "JUDGMENT CALL" so he can see and override any of them.

Worktree: `.claude/worktrees/gauntlet-audit-2026-09-03`, branch
`gauntlet-audit-2026-09-03`, based on `integration/release-2026-09-03`
at f000f07. Never pushed. Main checkout at
`C:/Users/swebs/Projects/friday-desktop` was left untouched — it has
unrelated in-progress uncommitted work (knowledge-graph/credential-store
changes) that does not belong to this task.

## JUDGMENT CALL 1 — second-instance boot (startup seam)

Per the operational note: if I can't prove all four inertness conditions
(off-GPU, off-real-Ollama, no MCP servers, no real provider key), park the
seam rather than boot anything. I have not yet completed that proof (see
coverage.md). Until it's done, the startup-wiring seam is probed **statically
only** (call-graph / reachability analysis by reading code, no process
started). If the four conditions get proven later tonight, dynamic boot
work against a scratch second instance may follow — this file will say so
explicitly before it happens.

## Status

- [x] Worktree created, scaffold written
- [ ] Claim corpus extracted (claims.jsonl)
- [ ] Seam-by-seam finder/falsifier rounds
- [ ] Builder/critic probes under tests/gauntlet/
- [ ] Two consecutive clean sweeps per seam

## Round log

(newest first)

### Round 0 — setup (2026-09-03)
Worktree + scaffold created. No findings yet. Launching claim-extraction
agents and first-wave finders next.

### Round 1 — first wave launched (2026-09-03, in progress)
6 background agents launched, all fresh-context, all instructed not to
mutate the live app or this worktree:
1. Claim extraction: THREAT_MODEL.md, KNOWN_ISSUES.md, docs/FILE_GRANTS.md
2. Claim extraction: README.md, RELEASE_NOTES.md, docs/CONFIGURATION.md,
   docs/API.md, tests/README.md
3. UI claim extraction + direct probe of the `settings-readers-check`
   pre-commit hook (it printed "OK" on a commit that touched none of the
   files it claims to check — flagged for verification, not yet a finding)
4. Finder: knowledge-graph ingest → retrieval seam (checking for
   recurrence of the "indexer calls a nonexistent method, swallowed" class)
5. Finder: settings write → read seam, privacy/egress toggles specifically
   (checking for recurrence of the "Saved but nothing reads it" class)
6. Finder: provider-health / router-to-seat seam (checking for recurrence
   of the "special-cased two providers, six misreport" class)

None have reported back yet. Meanwhile doing static-only startup-wiring
analysis myself (reading server.py's daemon block / _residency_boot) to
either clear or park the dynamic second-instance boot question — see
JUDGMENT CALL 1 above. Not booting anything yet.
