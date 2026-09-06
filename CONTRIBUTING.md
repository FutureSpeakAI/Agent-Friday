# Contributing to Agent Friday

Thank you for contributing. This document covers setup, the checks every change
must pass, and the parts of the codebase that need extra care. It is written as
present-tense engineering rules; the reasoning behind a rule, where it matters,
lives in a linked design or security note rather than here.

All contributors follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Reporting bugs and requesting features

- Bugs: use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.md).
  Include your OS, Python version, how you installed Agent Friday, steps to
  reproduce, and the relevant lines from the terminal or `~/.friday/logs/`.
- Features: use the [feature request template](.github/ISSUE_TEMPLATE/feature_request.md).
  Describe the problem before the solution.
- Security problems: **do not open a public issue.** See [SECURITY.md](SECURITY.md).

## Development setup

```bash
git clone https://github.com/FutureSpeakAI/Agent-Friday.git
cd Agent-Friday
python -m venv venv
venv\Scripts\activate          # Windows
source venv/bin/activate       # Linux / macOS
pip install -e ".[dev]"
git config core.hooksPath .githooks
```

`pip install -e ".[dev]"` installs the application in editable mode plus
`pytest` and `ruff`. Optional capability groups (`voice-local-lite`, `local`,
`google`, …) are listed in `pyproject.toml`. On Windows install
`".[dev,windows]"`: three tray tests import `pystray`, and with `-x` a fresh
`[dev]`-only environment collects zero tests. CI installs
`[dev,google,federation]` plus `[windows]` on the Windows runner.

Run `scripts/check_imports.py` and the server with `FRIDAY_HOME` pointed at a
scratch directory unless you mean to touch your real `~/.friday`: importing
the server seeds schedules and creates state files there.

The last line enables the repository's git hooks. It is required: the hooks
enforce invariants a reviewer cannot reliably catch by reading a diff (see
[Repository guards](docs/development/repository-guards.md)).

## Required checks

Run these before opening a pull request. CI runs the same set on Windows and
Ubuntu.

| Check | Command |
|---|---|
| Unit and API suites | `pytest tests/unit tests/api -q` |
| Security and egress-boundary suites | `pytest tests/security tests/test_egress_adversarial.py tests/test_judgment_gate.py -q` |
| Import smoke test | `python scripts/check_imports.py` |
| Fatal-rule lint | `ruff check --select E9,F63,F7,F82 .` |
| Gated-prompt callers | `python scripts/check_gated_prompt_callers.py` |
| Settings readers | `python scripts/check_settings_readers.py` |
| Stale model names | `python scripts/check_stale_model_names.py` |

The unit and API suites are hermetic: no live server, no network, no API keys.
Tests that need a live server (`tests/test_friday_ui.py`, `tests/test_ui_audit.py`)
or real network are deselected by default; see `pytest.ini`.

## Submitting a pull request

1. Branch from `main`.
2. Keep each pull request to one logical change. Refactors and bug fixes are
   separate pull requests.
3. Every bug fix includes a test that fails before the change and passes after
   it. Run it in both directions.
4. No new external dependency without prior discussion in an issue.
5. Fill in the [pull request template](.github/PULL_REQUEST_TEMPLATE.md),
   including the security checklist when it applies.

## Engineering invariants

These are the rules the hooks and guards exist to enforce. Each is stated as
the invariant; the guard that checks it is named.

- **Cloud prompt construction requires an explicit vault control.**
  `_get_friday_system_prompt()` takes keyword-only `provider=` and
  `vault_control=` with no defaults. A caller that builds a prompt for a local
  model says so; a caller that builds one for the cloud passes the vault control
  it owns. Passing `provider='cloud', vault_control=None` to satisfy the check
  is the bug the check exists to catch. Guard: `scripts/check_gated_prompt_callers.py`.
- **A settings control must be read by something.** Every key the UI writes
  must exist in `DEFAULT_SETTINGS` (unknown keys are dropped on load) and must
  have a reader under `src/`. Guard: `scripts/check_settings_readers.py`. The
  guard proves a reader exists, not that the reader enforces the setting; a
  behavioural test is still required.
- **Module-level statement order must be sound.** A dictionary mutated above
  its own definition passes `ruff` (the name exists, just later) and kills the
  server at import. Guard: `scripts/check_imports.py`.
- **Nothing may claim success it has not verified.** A component that cannot
  verify its own result says so; "done" without verification is a defect.
- **`index.html` is the UI.** It is the served file and the source of truth;
  `ui_parts/app.html` is a hand-maintained mirror. A UI change edits both, and
  the build tool refuses to regenerate `index.html` in a way that drops
  components. See [UI build](docs/development/ui-build.md).
- **User-facing documentation never names a retired model.** Guard:
  `scripts/check_stale_model_names.py`.
- **The repository is public.** No credentials, personal identifiers, local
  paths, or private material. Guard: the pre-commit scanner in `.githooks/`.

## Project layout

```
src/agent_friday/    # the Python package (Flask app)
  server.py          # entry point, Flask app object
  cli.py             # `friday` command-line entry point
  core/              # shared state, DEFAULT_SETTINGS, auth, config, vault helpers
  services/          # background services and engines
  routes/            # Flask blueprints, one per domain
  routing/           # model routing, Ollama management, provider descriptors
  privacy/           # vault access control, crypto, cloud consent
  governance/        # proof of integrity, behavioural monitor
  pipeline/          # context pruning and compression
  ui/                # UI build tooling
  seed/              # bundled skills and data shipped inside the package
tests/
  unit/              # fast, no server, no LLM
  api/               # Flask test client, every LLM call stubbed
  security/          # egress-boundary suites
packaging/windows/   # the Windows installer and its tests
docs/                # documentation — start at docs/README.md
```

## Sensitive subsystems

Changes here have security implications and receive extra review. Say so in
the pull request.

- `src/agent_friday/privacy/` — vault access control, encryption, cloud consent
- `src/agent_friday/governance/` — behavioural constraints and integrity
- `src/agent_friday/services/egress_gate.py` and `sensitivity_classifier.py` — the fail-closed outbound gate
- `src/agent_friday/services/credential_store.py` and `vault_passphrase.py` — where secrets live
- Authentication, session, and cookie handling in `src/agent_friday/core/`

## Local-only files

Some files in a working tree are intentionally never committed: launch scripts
that hold keys, per-user runtime state, generated assets, and private notes.
`.gitignore` covers the classes; for a file that is specific to one clone, use
`.git/info/exclude` rather than adding its name to the public ignore file.
Check a path with `git check-ignore -v <path>`.
