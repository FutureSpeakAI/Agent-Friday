# Contributing to Agent Friday

Thank you for taking the time to contribute. This document covers everything you need to get started.

## Code of Conduct

All contributors are expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md). Please read it before participating.

## How to contribute

### Reporting bugs

Open an issue using the [bug report template](.github/ISSUE_TEMPLATE/bug_report.md). Include:
- OS, Python version, and how you installed Agent Friday
- Steps to reproduce
- What you expected vs. what happened
- Relevant logs (check the terminal output or `~/.friday/logs/`)

### Requesting features

Open an issue using the [feature request template](.github/ISSUE_TEMPLATE/feature_request.md). Describe the problem you're trying to solve, not just the solution.

### Submitting a pull request

1. **Fork** the repo and create a branch from `main`.
2. **Install** in editable mode: `pip install -e ".[dev]"`
3. **Run the tests** before and after your change: `pytest tests/unit tests/api -q`
4. **Keep changes focused** — one logical change per PR. Refactors and bug fixes belong in separate PRs.
5. **No new external dependencies** without prior discussion in an issue.
6. Open a PR using the [pull request template](.github/PULL_REQUEST_TEMPLATE.md).

CI runs `pytest` on Windows and Ubuntu against Python 3.11 and 3.12, plus `ruff check --select E9,F63,F7,F82`. Both must pass.

## Development setup

```bash
git clone https://github.com/FutureSpeakAI/Agent-Friday.git
cd Agent-Friday
python -m venv venv
# Windows
venv\Scripts\activate
# Linux / macOS
source venv/bin/activate

pip install -e ".[dev]"
pytest tests/unit tests/api -q
```

## Project layout

```
src/agent_friday/    # Python package (Flask app)
  server.py          # entry point, Flask app object
  core.py            # shared state, settings, bootstrap
  cli.py             # `friday` CLI entry point
  services/          # background services (58 modules)
  routes/            # Flask Blueprints (38 route files)
  routing/           # model router, Ollama manager
  privacy/           # vault access, crypto
  pipeline/          # context pruner, compressor
  governance/        # proof of integrity, behavioral monitor
  ui/                # build_ui.py, liquid_ui.py
tests/
  unit/              # fast, no server, no LLM
  api/               # Flask test client, all LLM calls stubbed
docs/                # reference documentation
```

## Sensitive areas

The following subsystems have security implications — changes here get extra review:

- `src/agent_friday/privacy/` — vault access control and encryption
- `src/agent_friday/governance/` — Asimov cLaws, behavioral monitor
- `src/agent_friday/services/sensitivity_classifier.py` — egress gate
- `src/agent_friday/services/egress_gate.py` — fail-closed outbound classifier

If you're unsure whether a change affects these areas, say so in the PR and a maintainer will review it.

## Reporting security vulnerabilities

Please **do not** open a public issue for security vulnerabilities. See [SECURITY.md](SECURITY.md).
