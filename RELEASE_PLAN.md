# Agent Friday — Public Release Plan

**Status:** Draft for review by Stephen. Nothing here has been executed.
**Date:** 2026-06-21
**Repo:** `FutureSpeakAI/Agent-Friday` (public) · local: `~\Projects\friday-desktop`
**Method:** Static analysis of the working tree, committed tree (`git show HEAD:`), worktree branches, and install scripts. No server was booted and no fresh clone was performed yet — those are validation gates inside the plan.

---

## TL;DR — the one thing that matters most

**The public repo is currently NOT installable from a fresh clone, because the files that make it installable are uncommitted.**

`main` has a single squashed commit (`69e9f27`). The following are **untracked or modified-but-uncommitted** in the working tree:

| File | Git state | Impact if a stranger clones today |
|------|-----------|-----------------------------------|
| `pyproject.toml` | **untracked** | `pip install -e .[all]` fails → no `friday` entry point; both installers fall back to a heavy `requirements.txt` |
| `install.bat` | **untracked** | Windows users who don't use `install.ps1` have no `.bat` path |
| `friday_cli.py` | modified, uncommitted | Clone gets the older CLI (port-mismatch bug may differ) |
| `install.sh` | modified, uncommitted | Clone gets older Linux/mac installer (no pyproject preference) |
| `requirements.txt` | modified, uncommitted | Clone gets older dep set |
| `tests/api/test_onboarding_routes.py`, `tests/unit/test_capability_router.py`, `tests/unit/test_demo_mode.py` | **untracked** | New tests for new subsystems never ship |

Everything else (the actual application, all session features, LICENSE, docs) is already on `main`. **Phase 1 is essentially "commit the release plumbing, fix two install bugs, and prove a clean clone boots."**

---

## Big surprise: there is almost no "merge reconciliation" work

The original brief assumed 7+ worktree features still need merging into a scrubbed public repo. **Investigation shows the opposite is true:**

- `main` (`69e9f27`) is a **decomposed superset**: `server.py` is a ~230-line entry point; logic lives in **32 `services/` modules** and **30 `routes/` Blueprints**.
- **Every** session feature the memory index claims as "new" is **already present on `main`**: `creative_engine`, `workspace_studio`, `credential_store` + `google_accounts`, `capability_router` + `provider_health` + `demo_mode`, `introspection`, `model_catalog` + `provider_registry`, `ambient_awareness`, plus their routes (`workspace_studio`, `google_accounts`, `creations`, `ambient`, `platform`, self-improvement via `routes/insights.py`, onboarding/capabilities/distros via `routes/core_routes.py` + `routes/platform.py`).
- The ~30 worktree branches (`feature/*`, `claude/*`) are **stale pre-decomposition snapshots**. They still carry the old ~1,960-line monolithic `server.py` with **no `core.py`, no `services/`, no `routes/`**. A branch-level merge would show ~93k deletions and **revert the decomposition**. Their features were already absorbed into `main` before the squash.
- `git stash@{1}` contains a **6,000-line monolithic `server.py` rewrite** — applying it would clobber the architecture. It must be **dropped, not applied**.

**Conclusion:** item #2 ("merge reconciliation") and item #9 ("new features not in public repo") collapse into a small cleanup task plus the Phase 1 commit of uncommitted plumbing. There is no large merge.

---

## Per-item current-state assessment

Verdict legend: ✅ done · 🟡 partial/needs work · 🔴 blocker/missing.

### 1. Fresh-clone smoke test — 🔴 BLOCKED (not yet runnable cleanly)
A true clone of `69e9f27` lacks `pyproject.toml`, `install.bat`, and the modified installer/CLI/requirements. The documented `pip install -e .[all]` path fails; the fallback `requirements.txt` path pulls the **heavy** ML stack (`sentence-transformers`→torch ~2GB, `chromadb`, `headroom-ai` which needs a Rust+MSVC build on Windows). So even the fallback is slow and failure-prone on a clean machine. **The smoke test cannot pass until Phase 1 lands.** Must then be run for real on an isolated dir.

### 2. Merge reconciliation — 🟡 cleanup only (see "Big surprise" above)
No feature merge needed. Work = archive/prune ~30 worktrees + branches, drop `stash@{1}` (dangerous) and the empty/trivial stashes, document the decision so no one later "rescues" a stale branch.

### 3. README quality — 🟡 good prose, install commands drift from reality
README is well-written and accurate about architecture. Gaps: (a) the manual path recommends `pip install -r requirements.txt` (the heavy set) rather than the lean core + opt-in extras that `pyproject.toml` enables; (b) it never mentions the `friday` console command or `.[all]` groups; (c) the "under 5 minutes" goal is undermined by the heavy default dependency install, not by the prose. Requirements line says Python 3.10+ ✅.

### 4. Install error handling — 🟡 uneven across three installers
- `install.sh` (working tree): solid — checks Python ≥3.10, missing-git messaging per-OS, pyproject-then-requirements fallback. **But uncommitted.**
- `install.bat` (working tree): minimal — Python-presence check only, pip-fail handled only by silent fallback, **no port check, no friendly pip diagnostics. And uncommitted.**
- `install.ps1` (the **committed**, README-advertised Windows path): **not yet audited in this pass** — must verify it references the committed files, handles pip failure, and surfaces PowerShell execution-policy/SmartScreen friction.
None of the three detect a busy port or a partially-failed pip cleanly.

### 5. Windows Defender / SmartScreen — 🔴 undocumented
No `.md` mentions SmartScreen, Defender, Gatekeeper, or unsigned-script execution policy. The README's `irm … | iex` and the unsigned `.bat`/`.ps1`/PyInstaller EXE will all trip SmartScreen / `ExecutionPolicy Restricted` / macOS Gatekeeper. Users get a scary warning with zero guidance. Pure-docs fix (plus optional signing in Phase 3).

### 6. Port conflict handling — 🔴 real bug
- `server.py:227-232` reads `FRIDAY_PORT` (default **3000**) but does **no in-use detection and no fallback** — a taken port throws a raw `OSError` traceback.
- `friday_cli.py:61` defaults `FRIDAY_PORT` to **5000**, and the CLI launcher starts `server.py` without passing the port → **server binds 3000 while the CLI probes 5000** for readiness and opens the browser at the wrong port. This is a release-blocking inconsistency.
- `docs/INSTALLATION.md:222` documents only a manual `set FRIDAY_PORT=3001` workaround.

### 7. Python version compatibility — ✅ clean
`requires-python = ">=3.10"`. No `tomllib` (3.11+), no structural `match/case`, no `type` aliases, no `ExceptionGroup`/`itertools.batched`. All `match` hits are `re.match`. Installers and `friday_cli.py:470` self-check ≥3.10.

### 8. ChromaDB first-run — ✅ degrades gracefully
`conversation_memory.py:104-142` wraps `import chromadb` + `PersistentClient` + `get_or_create_collection` in one try/except, lazy-initialized via `model_router._get_conversation_memory`. `mkdir(parents=True, exist_ok=True)` creates the store; absent chromadb → labelled no-op, never blocks a chat. chromadb is correctly an **optional** (`local`) dependency.

### 9. New features not in public repo — 🟡 = the Phase 1 commit
All session *services/routes* are on `main`. The genuine "not in the public repo" delta is exactly the **uncommitted plumbing + tests** listed in the TL;DR. Once committed, this item is closed.

### 10. Windows installer (NSIS/MSI) — 🟡 partial (EXE spec exists, no installer/signing)
`AgentFriday.spec` builds a **PyInstaller onefile EXE** (excludes the heavy ML stack; degrades to no-ops). There is a `dist/` dir. **No** NSIS/MSI wrapper, **no** code signing. Building a signed installer is a Phase 3 nice-to-have; the unsigned EXE itself will hit SmartScreen (see #5).

### 11. Docker — 🔴 none
No `Dockerfile`/`docker-compose.yml`. Feasible and clean for the cloud-provider path (slim Python base, no GPU). The local/Ollama path is awkward in-container (Ollama is an external daemon). Phase 3.

### 12. License — ✅ done
`LICENSE` present and committed: standard MIT, "Copyright (c) 2026 FutureSpeak.AI". `pyproject.toml` and README both declare MIT. Consistent. **No action.**

### 13. pyproject / pip install — 🟡 well-formed but uncommitted; PyPI not published
`pyproject.toml` is good: lean core deps, sensible optional groups (`voice/creative/google/local/compression/federation/windows/all`), `friday = "friday_cli:main"` entry point, explicit `py-modules`/`packages` to defeat auto-discovery. Blocked only by being **untracked**. `pip install agent-friday` from PyPI is a **separate publish effort** (name availability, build, `twine upload`) — Phase 3.

### 14. Health-check completeness — ✅ strong
`GET /api/health/full` (`routes/platform.py:276-347`) reports server/distribution/providers/capabilities/demo/hardware/vault/dependencies/google_accounts/mcp, each independently try/except-guarded. Provider statuses distinguish **ok / missing / down / error**. `friday health` (`friday_cli.py:760-822`) renders the same without booting the server. Minor optional polish only.

### 15. Demo mode — ✅ works (one audit gap)
`services/demo_mode.py` auto-enables when no provider key is usable, returns labelled `[DEMO]` responses for chat/briefing/image/voice, surfaced in health + a UI banner. **Caveat:** per-route demo wiring wasn't exhaustively traced — a focused audit should confirm every chat/voice/creative route calls `demo_response` before attempting a live provider (so nothing 500s for a keyless visitor).

---

## Phased execution plan

### Phase 0 — Safety gate (do FIRST, before any commit) · ~0.5 day
The repo is **public**; the working tree carries files that have never been committed. Memory flags lingering PII risk in git *history* and the rule "never commit absolute home paths / personal data."
- [ ] PII/secret scan of the **exact** files to be committed (`pyproject.toml`, `install.bat`, `friday_cli.py`, `install.sh`, `requirements.txt`, the 3 test files) — no usernames, absolute home paths, keys, or minor's data.
- [ ] Run the pre-commit secret scanner; resolve any `token/key/password` false-positives per the known gotcha.
- **Dependency:** blocks every commit in Phase 1.

### Phase 1 — Critical blockers (make a fresh clone install & boot) · ~2–3 days
1. **Commit the release plumbing** (after Phase 0). `pyproject.toml`, `install.bat`, modified `friday_cli.py`/`install.sh`/`requirements.txt`, 3 test files. · ~0.5 day · *depends on Phase 0*
2. **Fix the port bug (item 6).** Reconcile `friday_cli.py` default (5000→3000) with `server.py`; have the CLI pass/learn the actual port; add in-use detection in `server.py` that either auto-increments or exits with a clean, actionable message instead of a traceback. · ~0.5 day
3. **Reconcile the three installers (item 4).** Make `install.ps1` (committed/advertised), `install.bat`, and `install.sh` agree: same install path (lean core by default, `.[all]` opt-in), friendly errors for missing Python, pip failure, and busy port. Audit `install.ps1` specifically. · ~1 day · *depends on #1*
4. **Slim the default install for the "5-minute" goal (items 1, 3).** Default to **core deps**; make the heavy ML stack (`sentence-transformers`/`chromadb`/`headroom`) explicit opt-in (`.[local]`/`.[all]`), since all degrade gracefully. Trim the committed `requirements.txt` fallback accordingly or point it at the core set. · ~0.5 day · *depends on #1*
5. **Run the real fresh-clone smoke test (item 1).** Clone `main` into an isolated dir on a clean-ish environment, run each installer, boot, hit `/api/health/full`, exercise demo mode with **no keys**. Document every failure; loop back into Phase 1 until clean. · ~0.5 day · **validation gate — depends on #1–#4**

### Phase 2 — Important (first-run UX & repo hygiene) · ~2–3 days
6. **Defender/SmartScreen/Gatekeeper doc section (item 5).** Add to README + `docs/INSTALLATION.md`: what the warning looks like, "More info → Run anyway", PowerShell `ExecutionPolicy`/`Unblock-File`, macOS Gatekeeper override. · ~0.5 day
7. **README install reconciliation (item 3).** Align commands with the committed installers and the lean-core decision; document the `friday` command and optional groups; verify the "zero-to-running" path end-to-end. · ~0.5 day · *depends on Phase 1 #3–#4*
8. **Worktree/branch/stash cleanup (item 2).** Archive (tag) anything worth keeping, prune the ~30 worktrees + branches, **drop `stash@{1}`** and the trivial stashes. Record the "branches are superseded" decision in `docs/`. · ~0.5 day
9. **Keyless-route demo audit (item 15).** Trace every chat/voice/creative route to confirm `demo_response` fires before any live-provider call; fix any route that could 500 for a keyless visitor. · ~0.5–1 day
10. **Health-check polish (item 14, optional).** Confirm degraded-vs-missing wording is user-legible in the UI banner; minor only. · ~0.25 day

### Phase 3 — Nice-to-have (reach & distribution) · ~4–7 days, parallelizable
11. **Dockerfile (item 11).** Slim Python base for the cloud path; `docker-compose` optional; document that local/Ollama runs as an external service. · ~1 day
12. **Signed Windows installer (items 5, 10).** NSIS/MSI wrapper around the PyInstaller EXE; code-signing cert to defeat SmartScreen (cert acquisition is the long pole). · ~2–3 days
13. **Publish to PyPI (item 13).** Confirm `agent-friday` name, build sdist/wheel, `twine upload`, verify `pip install agent-friday` + `friday` console script on a clean venv. · ~1 day · *depends on Phase 1 #1*
14. **macOS signing/notarization (item 5).** Parallel to #12 if Mac distribution matters. · ~2 days

---

## Dependency graph (what must precede what)

```
Phase 0 (PII/secret scan)
   └─► P1.1 commit plumbing ─┬─► P1.3 reconcile installers ─► P1.5 SMOKE TEST ◄─ gate
                             ├─► P1.4 slim install ──────────►        │
                             └─► P3.13 PyPI publish                   │
   P1.2 port fix ─────────────────────────────────────────────►──────┘
P1.5 smoke test ─► P2.7 README reconciliation
(independent: P2.6 Defender docs, P2.8 branch cleanup, P2.9 demo audit, P3.11 Docker, P3.12 signed installer)
```

## Recommended order of execution
1. **Phase 0** PII/secret scan — *unblocks everything, ship nothing public until clean.*
2. **P1.1** commit plumbing → **P1.2** port fix → **P1.3** installer reconciliation → **P1.4** slim install → **P1.5** real smoke test *(loop until green — this is the release gate).*
3. **P2.6** Defender docs + **P2.7** README reconciliation *(once installers are final).*
4. **P2.8** branch/stash cleanup + **P2.9** demo audit *(independent, can run in parallel).*
5. **Phase 3** Docker / signed installer / PyPI / macOS *(post-launch or parallel track; none block the basic public-clone experience).*

## Effort summary
| Phase | Scope | Estimate |
|-------|-------|----------|
| 0 | Safety gate | ~0.5 day |
| 1 | Critical blockers (installable fresh clone) | ~2–3 days |
| 2 | First-run UX & hygiene | ~2–3 days |
| 3 | Distribution (Docker/installer/PyPI/signing) | ~4–7 days (parallelizable) |
| **Total to "public clone just works"** | **Phases 0–2** | **~5–7 days** |

## Open questions for Stephen
1. **Default install weight:** OK to make the heavy ML stack (embeddings/chromadb/headroom) opt-in so the default install is fast, accepting that semantic memory/compression are off until a user opts into `.[local]`/`.[all]`?
2. **Branch cleanup:** delete the ~30 stale worktrees/branches outright, or tag-archive first? Confirm `stash@{1}` (monolith server.py) can be dropped.
3. **Distribution scope for launch:** is a signed Windows installer and/or PyPI publish required for *this* release, or acceptable as a fast-follow?
4. **Docker:** in scope for launch, or post-launch?
