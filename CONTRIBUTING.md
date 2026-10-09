# Contributing to Agent Friday™

Thank you for contributing to Agent Friday™. This document covers setup, the checks every change
must pass, and the parts of the codebase that need extra care. For how the
code is organised, read [ARCHITECTURE.md](ARCHITECTURE.md) first.

This repository is public. Never commit credentials, tokens, passphrases,
personal identifiers, family or medical data, or local paths that contain a
username. The pre-commit scanner catches most of this, and you remain
responsible for the rest.

All contributors follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Reporting bugs and requesting features

- Bugs: use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.md).
  Include the Agent Friday version (Settings > About), your Windows build,
  your RAM and graphics card, how you installed it, the steps to reproduce,
  and the relevant lines from `%USERPROFILE%\.friday\` logs (`friday.log`,
  and `logs\crashes.log` after a crash). Remove API keys and private text first.
- Features: use the [feature request template](.github/ISSUE_TEMPLATE/feature_request.md).
  Describe the problem before the solution.
- Questions: use [GitHub Discussions](https://github.com/FutureSpeakAI/Agent-Friday/discussions).
- Security problems: **do not open a public issue.** See [SECURITY.md](SECURITY.md).

## Development setup

Agent Friday™ is developed on Windows 10 and 11. Install Python 3.11 or 3.12
(tick "Add python.exe to PATH" in the installer) and Git. Node.js 18 or later is
needed only for the Playwright browser tests.

```powershell
git clone https://github.com/FutureSpeakAI/Agent-Friday.git
cd Agent-Friday
python -m venv venv
venv\Scriptsctivate
pip install -e ".[dev,windows]"
git config core.hooksPath .githooks
```

Python 3.10 or later satisfies `requires-python` in `pyproject.toml`; CI runs
3.11 and 3.12. `pip install -e ".[dev,windows]"` installs the application in
editable mode plus `pytest`, `pytest-xdist` and `ruff`, and the Windows extras
(the tray tests import `pystray`). CI also installs the `google`, `federation`
and `podcast` extras. The other extras in `pyproject.toml` are `voice`,
`voice-local-lite`, `voice-local-gpu`, `creative`, `compose`, `provenance`,
`local`, `compression`, `pii`, `keyring`, `pdf`, `documents` and `all`.
`requirements.txt` lists the same packages for a plain `pip install -r`.

On macOS and Linux the same commands work with `source venv/bin/activate`, but
Windows is the supported platform and several features (tray, computer control,
the installer) are Windows only.

### Run from source

```powershell
python server.py
```

The server listens on `http://localhost:3000`, or the next free port if that one
is busy (`FRIDAY_PORT` pins one) and prints the address it chose. `friday` (the
console command installed with the package) starts the server and opens the
browser; `friday setup` runs the terminal setup wizard, `friday models` shows
which local models fit your hardware, and `friday doctor` checks the
installation. `python friday_tray.py` runs the system tray app, which starts the
server for you.

Point `FRIDAY_HOME` at a scratch folder unless you mean to touch your real
`%USERPROFILE%\.friday`. Importing the server seeds schedules and creates state
files there, and `scripts/check_imports.py` does the same. The test suite
isolates its own home directory.

Friday runs local models itself (`llama-server`, managed by the residency
arbiter), so Ollama is optional. Cloud keys are entered in the running app and
stored in the encrypted credential store; a key in an environment variable such
as `ANTHROPIC_API_KEY` or `GEMINI_API_KEY` overrides the stored copy.

The `git config` line enables the repository's git hooks. It is required: the
hooks enforce invariants a reviewer cannot reliably catch by reading a diff (see
[Repository guards](docs/development/repository-guards.md)). The pre-commit hook
runs the secret and personal-data scanner on staged additions, the import smoke
test (when a `venv` exists in the checkout), and the gated-prompt,
settings-reader, stale-model-name, trust-in-governance and brand-token checks.
Do not bypass it with `--no-verify`; if it blocks a false positive, allowlist
that line with `# pragma: allowlist secret`.

## Required checks

Run these before opening a pull request. CI runs the same set (the unit and API
suites on Windows and Ubuntu; the guard scripts on Ubuntu).

| Check | Command |
|---|---|
| Unit and API suites | `pytest tests/unit tests/api -q` |
| Security and egress-boundary suites | `pytest tests/security tests/test_egress_adversarial.py tests/test_judgment_gate.py -q` |
| Import smoke test | `python scripts/check_imports.py` |
| Fatal-rule lint | `ruff check --select E9,F63,F7,F82 .` |
| Gated-prompt callers | `python scripts/check_gated_prompt_callers.py` |
| Settings readers | `python scripts/check_settings_readers.py` |
| Stale model names | `python scripts/check_stale_model_names.py` |
| Brand tokens | `python scripts/check_brand_tokens.py` |
| Trust in governance | `python scripts/check_trust_in_governance.py` |
| Documentation links | `python scripts/check_doc_links.py` |
| Secret and personal-data scan of the whole tree | `python .githooks/security_scan.py --tree` |

The scanner without `--tree` checks only what is staged, which is what the
pre-commit hook runs.

`pytest.ini` runs with `-n auto` (all cores) by default. Use `-n 0` to debug a
single test with normal tracebacks, or `-n 2` if the machine is also running a
local model. The unit and API suites are hermetic: no live server, no network,
no API keys. Tests that need a live server (`tests/test_friday_ui.py`,
`tests/test_ui_audit.py`) are excluded by default; see `pytest.ini`. The
Playwright app tests start their own scratch server: `npm install`, then
`npm run test:smoke` (see [tests/app/README.md](tests/app/README.md)).
A change to `index.html` or `ui_parts/app.html` needs every test that reads
those files to pass; find them with a search for the file names under `tests/`.

## Using an AI coding agent

`AGENTS.md` at the repository root carries the same rules for AI coding agents
(`CLAUDE.md` imports it). You remain responsible for every line you submit, so
read the diff before you open the pull request.

## Submitting a pull request

1. Branch from `main`; never commit to `main` directly. Branch names use a
   short prefix such as `feat/`, `fix/` or `docs/`, for example
   `fix/mail-send-receipt`.
2. Keep each pull request to one logical change. Refactors and bug fixes are
   separate pull requests.
3. Every bug fix includes a test that fails before the change and passes after
   it. Run it in both directions.
4. No new external dependency without prior discussion in an issue.
5. Commit messages describe the change and the invariant it protects, in the
   present tense, for example
   `fix(mail): an approved send writes a signed receipt`. They do not narrate
   the session or the person that produced the change.
6. Comments and documentation state invariants and intent in the present
   tense. Git history keeps the incident; the source keeps the rule.
7. Fill in the [pull request template](.github/PULL_REQUEST_TEMPLATE.md),
   including the security checklist when it applies, and say so when the change
   touches a [sensitive subsystem](#sensitive-subsystems).

## Line endings

Most of the tree is committed with LF line endings and is left byte-for-byte as
committed; `.gitattributes` forces LF for `*.sh` and CRLF for `*.bat`, `*.cmd`
and `*.ps1`, and leaves `static/vendor/` untouched. Before committing, check
that you have not converted a file: `git ls-files --eol <path>` should show the
same `i/` value as before, and `git diff --stat` should not show whole-file
rewrites. Some editors on Windows rewrite LF files as CRLF on save.

## Editing the UI

`index.html` is the served, authoritative UI; `ui_parts/app.html` is a
hand-maintained JSX mirror. A UI change edits `index.html` and, where the
component exists in both, keeps `app.html` in step. The build tool refuses to
regenerate `index.html` in a way that drops components. After editing compiled
code in `index.html`, syntax-check the function you touched. The full rules,
and the tests that parse both files, are in
[UI build](docs/development/ui-build.md). Every Settings tab you name in
user-facing text must be a tab that exists
(`tests/unit/test_settings_signposts_are_real.py`).

## Adding a tool

Every tool Friday can call passes the governance checkpoint, and the tests fail
if a new tool is not classified. In `src/agent_friday/services/agent.py`:

1. Add the schema to `CLAUDE_TOOLS`.
2. Add the handler, `_tool_<name>(inp)`, to `CLAUDE_TOOL_HANDLERS`.
3. Give it a privilege ring in `TOOL_RINGS` (0 read, 1 local write, 2 network,
   3 computer control). An unlisted tool defaults to ring 2.

Then classify it in `src/agent_friday/governance/action_gate.py`:

4. Add it to `INTERNAL_TOOLS` (stays on this PC and is reversible) or
   `OUTWARD_TOOLS` (reaches other people, accounts, money, publishing, code
   execution, or has no undo). If the answer depends on the arguments, add it
   to `BY_ARGUMENT` and extend `classify()`. If the handler raises its own
   approval card, also add it to `SELF_GATED` and to `taint.SELF_CARDING`.
5. If an argument names a recipient, link, path, command or memory text, make
   sure `services/taint.py` inspects it, so a value taken from read content
   raises a card.
6. Never call a handler directly. Everything goes through `_execute_tool`
   (use its `handler=` argument for a tool outside the registry). A side
   effect that is not a tool call (for example a send after an approved card)
   goes through `action_gate.authorize_external` or `record_external`, and must
   be listed in `tests/unit/test_governance_off_chat_paths.py`.

`tests/unit/test_every_action_is_governed.py` discovers every registered tool
and every executor call site and fails on an unclassified tool or a bypass. An
unknown tool is treated as outward at runtime, so forgetting step 4 makes the
tool ask for approval rather than run silently, but the test still fails.

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
  governance/        # per-action checkpoint (action_gate), proof of integrity
  phone/             # Twilio phone: the webhook-only ingress and its service
  voice/             # the GPU voice worker process
  pipeline/          # context pruning and compression
  ui/                # UI build tooling
  seed/              # bundled skills and data shipped inside the package
  resources/         # data files such as the model tier tables
index.html           # the served UI (authoritative)
ui_parts/app.html    # hand-maintained mirror of the UI
static/, assets/     # fonts, vendored libraries, icons
scripts/             # guard scripts and developer tools
tests/
  unit/              # fast, no server, no LLM
  api/               # Flask test client, every LLM call stubbed
  security/          # egress-boundary suites
  app/               # Playwright application tests
packaging/windows/   # the Windows installer and its tests
docs/                # documentation: start at docs/README.md
```

## Sensitive subsystems

Changes here have security implications and receive extra review. Say so in
the pull request.

- `src/agent_friday/privacy/`: vault access control, encryption, cloud consent
- `src/agent_friday/governance/`: the per-action checkpoint, grants, receipts, behavioural constraints and integrity
- `src/agent_friday/services/egress_gate.py` and `sensitivity_classifier.py`: the fail-closed outbound gate
- `src/agent_friday/services/credential_store.py`, `keystore.py` and `vault_passphrase.py`: where secrets live
- `src/agent_friday/services/taint.py`, `approvals.py`, and the tool hook chain in `services/agent.py`: provenance and approvals
- `src/agent_friday/phone/`: the only code reachable from the internet
- Authentication, session, cookie handling and the locality rule in `src/agent_friday/core/`, and `services/local_address.py`, `local_ca.py`, `local_proxy.py`

## Local-only files

Some files in a working tree are intentionally never committed: launch scripts
that hold keys, per-user runtime state, generated assets, and private notes.
`.gitignore` covers the classes; for a file that is specific to one clone, use
`.git/info/exclude` rather than adding its name to the public ignore file.
Check a path with `git check-ignore -v <path>`.
