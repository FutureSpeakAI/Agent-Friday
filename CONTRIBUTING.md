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
2. **Install** in editable mode: `pip install -e .` then `pip install pytest`
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

pip install -e .
pip install pytest
pytest tests/unit tests/api -q
```

### Enable the git hooks — first thing, before your first commit

```bash
git config core.hooksPath .githooks
```

This is one line and it is not optional in practice, because the hook enforces
things a reviewer cannot reliably catch by reading a diff. It runs four checks:

1. **Secret / PII scan.** This repository is public and its history has been
   scrubbed once already. Bypass a single false positive with a trailing
   `# pragma: allowlist secret`.
2. **Import smoke test** (~4 s, only when `venv/` exists). A module-level
   use-before-definition in `services/agent.py` once killed every server start
   about two seconds in, and the tray was discarding stderr, so six overnight
   failures left no trace. Ruff cannot catch it — the names *are* defined, just
   too late — so only a real import proves statement order is sound.
3. **Gated-prompt caller check** — see below.
4. The scanner runs last and its exit status is the hook's.

Emergency bypass is `git commit --no-verify`, and it is discouraged in the
ordinary way but genuinely bad here: two of these checks exist because the
failure they catch is *silent*.

### Why `_get_friday_system_prompt` demands arguments you might not want to think about

`scripts/check_gated_prompt_callers.py` will fail your commit if you call
`_get_friday_system_prompt()` without both `provider=` and `vault_control=`.
Both are keyword-only and neither has a default. This is deliberate, and the
reason is worth understanding before you reach for a value that makes the error
go away.

The function's former defaults were `provider='cloud'` and `vault_control=None`
— which its own docstring already described as "legacy ungated". On
2026-08-25 we found **22 call sites** that had taken those defaults, meaning
TIER_2 vault content was assembled into a system prompt and shipped to a cloud
provider whenever the call happened to route there. Nobody chose that; they just
did not pass an argument.

Making the parameters required converts a silent leak into a loud `TypeError`.
But a `TypeError` is only loud if something *calls* the function — and **two of
the twenty-two were background jobs** (a daily unattended briefing and a
session-summary distiller) that would not have run again, and so would not have
raised, until their next scheduled run hours or a day later. Hence the static
scan: it makes the same failure loud at commit time regardless of which code
path would eventually have hit it. It is a stdlib-only AST parse, so it runs
even without the venv.

**So: decide the gating.** If you are building a prompt for a local model, say
so. If it is going to the cloud, say that, and pass the vault control that
belongs to the caller. Do not pass `provider='cloud', vault_control=None` to
silence the checker — that is precisely the bug, spelled out longhand.

### `index.html` is the UI, not `ui_parts/`

**Read this before running any UI build.** `index.html` is the file the server
serves and the source of truth. `ui_parts/app.html` is now a strict **subset**
of it: every top-level component in `app.html` also exists in `index.html`, but
**18 top-level components — including the entire conversations feature — exist
only in `index.html`.**

Running `src/agent_friday/ui/build_ui.py` naively therefore *deletes shipped
code*, silently. Since 2026-08-24 the build refuses to write output that drops
any top-level component the existing `index.html` defines, and tells you which
ones. `--force` overrides it, and you should not use `--force` unless you
genuinely mean to discard the components it names.

Also note: **JSX precompilation was silently off from 18 August until 25
August.** The precompile step falls back to in-browser Babel when it cannot run,
printing a message that nobody read. If you are touching the UI build, check
that precompilation actually happened rather than assuming.

### The forensics snapshotter is a scheduled task

Named `AgentFridayForensics`, installed by `ops/forensics-install.ps1`. If you
need it to stop — it writes snapshots on a timer and will keep doing so across
reboots — run `ops/forensics-down.ps1`. `ops/forensics-verify.ps1` reports its
state. It is a Windows scheduled task, so uninstalling the app is not what stops
it.

## Project layout

```
src/agent_friday/    # Python package (Flask app)
  server.py          # entry point, Flask app object
  core/              # shared state, DEFAULT_SETTINGS, auth, config, vault helpers
  cli.py             # `friday` CLI entry point
  services/          # background services
  routes/            # Flask Blueprints, one per domain
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
