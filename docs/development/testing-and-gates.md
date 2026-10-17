# Testing and gates

> **Status:** current · **Last verified against the code:** 2026-10-17 (main b605f2216)

How the tests are organised, what stops a test run from starving the machine,
how a change is proven before it merges, and the budgets and records that
only move one way. Setup is in [Developer setup](setup.md); the guard scripts
are in [Repository guards](repository-guards.md).

## How the tests are organised

| Where | What | How it runs |
|---|---|---|
| `tests/unit/` | Fast tests of one module each, plus rule and contract tests that scan the source tree or the UI files. No server, no network, no keys. | `pytest tests/unit` |
| `tests/api/` | The Flask app through its test client, every model call stubbed (`tests/api/conftest.py`). | `pytest tests/api` |
| `tests/security/`, `tests/test_egress_adversarial.py`, `tests/test_judgment_gate.py` | The egress and privacy boundary suites. CI runs them as their own step. | `pytest tests/security tests/test_egress_adversarial.py tests/test_judgment_gate.py` |
| `tests/test_laya_workspace_contracts.py` | Every workspace's Laya manifest against the code ([Adding a workspace](adding-a-workspace.md)). | `pytest tests/test_laya_workspace_contracts.py`. Not collected by `pytest tests/unit tests/api`; CI runs it in its own step of `tests.yml`. Run it yourself for any workspace or UI-action change. |
| `tests/ui/*.test.cjs` | UI logic run under Node, each wrapped by a pytest test in `tests/unit/` so one command runs both. The wrapper skips when `node` is not on `PATH`. | Through pytest |
| `tests/app/specs/*.spec.ts` | Playwright app specs: the Settings walk, See & Touch, the calendar, Connect your services, the first-run walk and others, against a scratch server. | `npx playwright test --config=playwright.app.config.ts <spec>` |
| `tests/*.spec.ts`, `tests/test_friday_ui.py`, `tests/test_ui_audit.py` | Older browser tests against a running server on `localhost:3000`. `pytest.ini` ignores the Python ones by default. | By hand |
| `tests/integration/`, `tests/regression/`, `tests/gauntlet/`, others | Collected by a plain `pytest`. | `pytest` |
| `scripts/check_*.cjs` | Isolated UI fixtures (Crew, the spatial desktop and others) that drive Chromium against a design preview server. Not wired into CI. | `node scripts/check_<name>.cjs` after `npm ci` |

Markers that are deselected by default and need a flag: `network` (live
network or money, `--run-network`), `live_residency` (real multi-GB models,
`--run-live-residency`) and `honesty` (asks a live seat questions,
`--run-honesty`). See `pytest.ini`.

Every pytest run is hermetic. `tests/conftest.py` sets `FRIDAY_TESTING=1`
before anything imports the server, so no background daemon starts, and
redirects the home to a fresh temporary folder, so nothing touches real data.
The Playwright app config (`playwright.app.config.ts`, `tests/app/target.js`)
starts a scratch server on `127.0.0.1:3197` with its own temporary
`FRIDAY_HOME`, and refuses the live server unless `LIVE_READONLY=1`, in
which mode every non-GET request fails. See
[tests/app/README.md](../../tests/app/README.md).

`scripts/run_tests.py [pytest args]` runs pytest with its exit code passed
through verbatim and prints `RESULT: PASS` or `RESULT: FAIL` as the last
line, so `| tail` cannot hide a failure.

## The resource guard (RAM and disk floors)

The live Friday, its local model seat and every test run can share one PC.
`pytest_resource_guard.py`, loaded for every invocation by the root
`conftest.py`, enforces two rules:

1. **At most two xdist workers.** `-n auto`, `-n logical` and any larger
   `-n N` are capped at `MAX_WORKERS = 2`. `pytest.ini` still says
   `-n auto`; the guard caps it.
2. **A broad run needs room.** A run over directories (`pytest`,
   `pytest tests/unit tests/api`) starts only with at least
   `MIN_FREE_RAM_GB = 12.0` GB of free memory and `MIN_FREE_DISK_GB = 20.0`
   GB free on the repository's drive. Below either floor it stops before
   collecting anything (exit code 4) and says which floor, and by how much.

A run that names test files (`pytest tests/unit/test_x.py`) is never
refused. On GitHub Actions (both `CI` and `GITHUB_ACTIONS` true) neither rule
applies: the runner takes every core. `tests/unit/test_resource_guard.py`
pins the floors. Use `-n 0` to debug one test in-process (not
`-p no:xdist`, which makes `-n auto` an unknown argument).

`scripts/run_suite_guarded.py [--tree DIR] [--workers N] [--wait] [--session NAME] [pytest args]`
is the one door for a full local run on a machine that also serves a live
Friday: the same floors (or `--wait`), a machine-wide suite lock (exit 5
while another run holds it), a worker cap of two while the local model seat
answers, abort floors that kill the run if free memory falls under 3 GB or
free disk under 8 GB, and a receipt `<receipts_dir>/<tree sha>/suite.json`
whose `ok` is the child's real exit code. Machine-specific values come from
`~/.claude/friday-desktop.local.json` or `$FRIDAY_GUARD_CONFIG`; see
[Repository guards](repository-guards.md#the-guarded-suite-runner).

## Lanes: quick gate and full gate

Only one full suite runs on the maintainer's machine at a time; holding that
slot is called holding the lane. A change reaches the live install through
one of two gates, both run with the guarded runner by whoever holds the lane
(the release lead), never by a builder:

- **Quick gate.** A selection of test files for what the change touches: a
  changed module selects every test file that imports it or names it, a
  changed test selects itself, `index.html` and `ui_parts/` select every test
  that reads the UI, docs select nothing, and every rule and contract test
  (`test_no_*`, `test_every_*`, `*_guard`, `*contract*`, `*manifest*`,
  `*secret*`, `*smoke*`) is always added. The selection tool is part of the
  maintainer's local tooling and is not in this repository.
- **Full gate.** `tests/unit` and `tests/api` in full. A change to a
  `conftest.py`, `pytest.ini`, `pyproject.toml`, a requirements file or
  `pytest_resource_guard.py` always takes the full gate, as does a selection
  that reaches 60% of all test files. A change to a shared hub (the agent
  loop, the egress gate, the tool hooks) is gated in full in practice.

A builder working beside a held lane writes the tests, runs only the named
test files it changed (which the guard never refuses), and hands the tree to
the release lead with the files it ran. A plain Python runner of a test file
is still a test run and waits for the lane like any other.

## CI: tests.yml, codeql.yml, installer.yml

`.github/workflows/tests.yml` runs on every pull request and every push to
`main`:

| Job | What |
|---|---|
| `pytest (<os>, py<version>)` | Windows and Ubuntu, Python 3.11 and 3.12: `scripts/check_imports.py`, `pytest tests/unit tests/api -q`, then the security and egress suites (which run even if the first step failed). |
| `repository guards` | `ruff` fatal rules and every guard script, the Markdown link check, the whole-tree secret scan, `git diff --check`. |
| `app specs (Settings walk, See & Touch)` | On Windows: the listed app specs against a scratch server, then `first_run_setup.spec.ts` on its own scratch home. Traces and the first-run screenshots are uploaded. `seams.spec.ts` is left out (it needs live provider catalogues). |
| `package build` | `python -m build` and `scripts/check_package_data.py` on the wheel. |

`.github/workflows/codeql.yml` runs `analyze (python)` and
`analyze (javascript)` on pull requests, pushes to `main` and weekly.
`.github/workflows/installer.yml` is described in
[Release process](release-process.md).

Windows and Linux differ in ways a local run on one cannot show: PowerShell
behaviour, CRLF line endings, Windows fonts in the top bar, cp1252 decoding of
Node output (read Node subprocess output with `encoding="utf-8"`), and the
desktop-control tools that register only where `pyautogui` is installed.

### CI-only pull requests

When the machine has no room for a local run, CI runs the whole suite
without touching it. Two draft pull requests prove a change:

1. **Green PR.** A draft titled `CI only: <what> (do not merge)` carrying one
   squashed, neutrally worded commit of the change on current `main`. Every
   job must pass: pytest on all four runners, app specs, guards, package
   build, CodeQL.
2. **Red PR.** A second draft, also `CI only: ... (do not merge)`, that adds
   **only the new test files** on unchanged `main`. It must fail, and only
   inside those new files. That is the proof the tests can fail.

The drafts are closed, not merged, once the real change lands; the change
itself merges through its own pull request. Neutral wording applies to the
draft titles, branch names and commit messages too: they are public.

## Budgets that only move on purpose

| Budget | Where | Rule |
|---|---|---|
| Spoken tool contract | `services/voice_engine.py` `VOICE_CONTRACT_MAX_TOKENS = 10000`; `tests/unit/test_voice_contract_parity.py` | The contract must fit with **300 tokens spare**. Measure a new voice tool with `build_voice_tool_contract()` before declaring it. |
| Tool catalogue | `tests/unit/test_latency_budget.py` `MAX_TOOL_CATALOGUE_TOKENS = 23_800` | Plus `MAX_SYSTEM_PROMPT_TOKENS = 6_000`, `MAX_TOOL_OPENING_TOKENS = 4_500`, `MAX_STANDING_PROMPT_TOKENS = 6_500`. A ceiling is raised only for an owner-requested capability, after trimming, with the reason written beside the constant. `test_the_ceilings_leave_room_but_not_a_field` fails a ceiling far above the measured value. |
| Laya coverage | `tests/fixtures/laya_coverage_floor.json` | Per workspace and overall, the covered count and the percentage may only go up. Raise the floor in the same commit that raises coverage. |

Both token budgets measure more on Windows than on Linux (about 40 and 600
tokens), because the desktop-control tools register only where `pyautogui`
is installed. Integrate parallel branches and measure on Windows CI before
calling a budget met; two branches that each fit alone can overflow
together.

## CodeQL alerts

A CodeQL alert is fixed in code wherever a fix exists. One is dismissed only
when the flow cannot carry sensitive or attacker-controlled data, or the
behaviour is deliberate and safe:

1. The builder states the case in the pull request or hand-off (rule, path,
   why it is a false positive) and does not dismiss anything.
2. The release lead reviews each alert in the code and dismisses it on GitHub
   with a one-line justification (false positive, won't fix, used in tests).
3. The alert is recorded in
   [docs/security/codeql-dismissals.md](../security/codeql-dismissals.md)
   with its disposition and reason; residual stack-trace alerts with their
   sanitising call go in
   [codeql-residual.md](../security/codeql-residual.md).

Stack-trace exposure is fixed (fixed words, `routes/_errors.public_result`),
not dismissed as won't fix. Log lines carry fixed words, ids and counts, never
message text or keys.

## Proving a fix

A fix needs a test that fails before the change and passes after it. Run it
in both directions: red on the parent commit, green on the fix. A test that
plants a victim must plant it in the form the old code actually breaks, or it
cannot fail. A rerun of a red CI job counts only for a failure shown to be
the runner's (a known network canary, a Windows socket abort), and a second
failure is treated as real.
