# Engineering instructions for AI coding agents

These are the standing rules for any AI coding agent working in this repository.
They are deliberately short. Everything here is also true for humans; see
[CONTRIBUTING.md](CONTRIBUTING.md) for setup and the pull request process.

## The repository is public

- Never commit credentials, tokens, passphrases, personal identifiers, family or
  medical data, local usernames or home-directory paths, transcripts, or
  screenshots. The pre-commit scanner (`git config core.hooksPath .githooks`)
  blocks most of this; it is a backstop, not a substitute for judgement.
- Do not add private handoff notes, status reports, session diaries, or
  "for the maintainer" files to the tree. Working notes belong outside the
  repository.
- Comments and documentation explain invariants and intent in present tense.
  They do not narrate who found a bug, when, or what a previous session
  believed. Git history keeps the incident; the source keeps the rule.

## How a task runs

- Plan before building. A task of three or more steps, or one with an
  architectural decision, starts with a written plan: the steps, how each
  will be verified, and the spec it serves. When something goes sideways,
  stop and re-plan before the next edit.
- Simplicity first, minimal impact. Fix the root cause; no temporary fixes
  and no unrelated edits. For a non-trivial change, ask once whether a more
  elegant way exists and take it. A simple, obvious fix skips the question.
- A bug report is worked from the logs, the errors and the failing tests.
- Track the plan as checkable items, tick them as they land, and close with a
  review: what changed, how it was verified, what is left.
- Deterministic rules live in code, not in prompts. A rule a program can check
  is enforced by one: the pytest plugin, the pre-commit hook and the static
  checks listed below.

## Verify before claiming

- Code beats documentation. When a document and the implementation disagree,
  check the implementation and correct the document with its status header.
- Nothing may claim success it has not verified. A fix needs a test that fails
  before the change and passes after it; run it in both directions. A test
  that cannot fail counts for nothing.
- UI work needs rendered frames that someone has actually looked at. A
  passing DOM assertion is not a look.
- Before calling anything done: diff the behaviour between main and the
  change, run the checks, read the logs, and ask whether a staff engineer
  would approve it.
- Distinguish "refused by policy" from "not implemented". Some designs record
  a deliberate decision not to build; do not turn them back into backlog.

## Running tests

`pytest.ini` defaults to `-n auto`. On a small machine pass `-n 0`, `-n 1` or
`-n 2` explicitly, and run one full suite at a time.

## Required checks

```
pytest tests/unit tests/api -q          # the documented suite
python scripts/check_imports.py         # module-level import smoke test
ruff check --select E9,F63,F7,F82 .    # fatal-rule lint
python scripts/check_gated_prompt_callers.py
python scripts/check_settings_readers.py
python scripts/check_stale_model_names.py
python scripts/check_brand_tokens.py
python scripts/check_trust_in_governance.py
```

The pre-commit hook runs the scanner and these guards; do not bypass it with
`--no-verify`.

## Sensitive subsystems

Changes under `src/agent_friday/privacy/`, `src/agent_friday/governance/`,
`services/egress_gate.py`, `services/sensitivity_classifier.py`,
`services/credential_store.py`, `services/vault_passphrase.py`, and anything
touching authentication or session handling get extra review. Say so in the
change description.

## UI

`index.html` is the served, authoritative UI. `ui_parts/app.html` is a
hand-maintained mirror; a UI change edits both, and the build tool refuses to
regenerate `index.html` in a way that drops components. See
[docs/development/ui-build.md](docs/development/ui-build.md).

## Git

- Work on a branch, never on `main` directly.
- Force-pushes and history rewrites are not allowed on shared branches.
- Do not push, tag, delete branches, or change repository settings without
  explicit instruction.
- Commit messages describe the change and the invariant it protects, not the
  session that produced it.
