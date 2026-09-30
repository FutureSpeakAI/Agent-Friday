# Repository guards

The repository carries a small set of static checks that protect specific
invariants. They run from the pre-commit hook and from CI. This page lists
each guard, the invariant it protects, and its known limits.

Enable the hooks once after cloning:

```bash
git config core.hooksPath .githooks
```

## The pre-commit hook

`.githooks/pre-commit` runs, in order:

1. **Import smoke test** — `scripts/check_imports.py` (only when a `venv/`
   exists). Imports the application modules so a module-level ordering error
   is caught before it reaches a running server. Lint cannot catch this class:
   the names are defined, just too late.
2. **Gated-prompt callers** — `scripts/check_gated_prompt_callers.py`. A
   stdlib-only AST scan that fails if any call to `_get_friday_system_prompt()`
   omits `provider=` or `vault_control=`. Both parameters are keyword-only with
   no defaults so that a prompt cannot be assembled for a cloud provider
   without a deliberate vault decision.
3. **Settings readers** — `scripts/check_settings_readers.py`. Every settings
   key written by `index.html` or `ui_parts/app.html` must exist in
   `DEFAULT_SETTINGS` and must appear as a string literal somewhere under
   `src/`, and the two HTML files must write the same key set.
4. **Stale model names** — `scripts/check_stale_model_names.py`. User-facing
   documents must not name a model that the product no longer ships.
5. **Secret and PII scanner** — `.githooks/security_scan.py`. Scans staged
   additions for credentials, private keys, personal identifiers, and
   username-bearing paths. Its exit status is the hook's.

Bypass a single false positive with a trailing `# pragma: allowlist secret`.
`git commit --no-verify` skips every check and is not acceptable for a change
that will be reviewed.

## Known limits

- **The settings-readers guard proves consumption, not enforcement.** A key
  that is read into a dictionary and never branched on passes check 3 exactly
  as an enforced key does. Only a behavioural test tells the two apart.
- **The scanner cannot read intent.** Documentation that quotes a credential
  shape, or a comment about a leak, looks like a leak. Keep examples generic
  and narrow the rule rather than exempting a whole file.
- **The hook picks its interpreter by path.** Under WSL or a container that
  mounts a Windows checkout, `venv/Scripts/python.exe` exists but cannot run,
  and the hook fails closed. Run the checks from the host, or from a venv
  created inside that environment.
- **Three source files carry a UTF-8 byte-order mark** and parse fine in
  Python but not in tools that read source as text without `utf-8-sig`.

## CI

`.github/workflows/tests.yml` runs the unit, API, and security suites on
Windows and Ubuntu, the import smoke test, the fatal-rule `ruff` set, every
guard listed above, a Markdown link check, a whole-tree run of the secret
scanner, and a package build that verifies the bundled seed content is
included in the wheel.

## The Claude Code guard hook

`scripts/hooks/friday_guard.py` is a `PreToolUse` hook: it reads the tool
call Claude Code is about to make and exits 2, with the reason on stderr, to
block it. Deterministic rules are enforced here instead of being asked for in
a prompt. Each rule has tests in both directions in
`tests/unit/test_friday_guard_hook.py`.

1. **Full-suite pytest** in a checkout that carries `pytest_resource_guard.py`
   is refused and pointed at `scripts/run_suite_guarded.py`. Named test files
   and node ids run directly.
2. **`wsl` and `docker`** are refused while free memory is under the floor.
   `wsl --shutdown`, listing and status queries never boot the VM and stay
   allowed.
3. **The live checkout** (the tree the running Friday serves) is not edited,
   switched, reset or used to launch a server, from any tool: file edits,
   `git` with that tree as its directory, shell writers, redirects and server
   entry points are all caught. Its `.claude/` directory is exempt (locks,
   receipts, settings, agent worktrees). The deploy lane bypasses the rule by
   writing a reason into the token file (`<live>/.claude/DEPLOY_LANE` by
   default), which expires after `deploy_lane_ttl_hours`; every bypass and
   every refusal is appended to the audit log.
4. **Force-pushes and history rewrites** (`push --force*`, `+refspec`,
   `:refspec`, `--mirror`, `--delete`, `commit --amend`, `filter-branch`,
   `filter-repo`, `replace`, `reflog expire`) are refused everywhere.

Register it once per machine in `~/.claude/settings.json` so it applies to
every checkout and worktree, whatever branch they are on. The path is
machine-specific and stays out of the tree:

```json
{
  "hooks": {
    "PreToolUse": [
      {"matcher": "Bash|PowerShell",
       "hooks": [{"type": "command", "command": "python \"<path to a main checkout>/scripts/hooks/friday_guard.py\"", "timeout": 20}]},
      {"matcher": "Edit|Write|MultiEdit|NotebookEdit",
       "hooks": [{"type": "command", "command": "python \"<path to a main checkout>/scripts/hooks/friday_guard.py\"", "timeout": 20}]}
    ]
  }
}
```

Machine-specific values live in `~/.claude/friday-desktop.local.json` (or the
file `$FRIDAY_GUARD_CONFIG` names), never in the tree: `live_checkout`,
`min_free_ram_gb`, `min_free_disk_gb`, `deploy_lane_token`,
`deploy_lane_ttl_hours`, `audit_log`, and for the suite runner `suite_lock`,
`receipts_dir`, `seat_port`, `max_workers_with_seat`,
`max_workers_without_seat`, `abort_free_ram_gb`, `abort_free_disk_gb`.
Without the file rules 1, 2 and 4 apply with the repository's floors; rule 3
needs `live_checkout`.

An internal fault in the hook lets the call through with the traceback on
stderr and in the audit log (exit 1), because the hook runs for every project
on the machine and a crash must not brick every session. A registration in
the user settings reaches sessions that are already open; the first blocked
call in this repository's own history came from the session that registered
the hook.

## The guarded suite runner

`scripts/run_suite_guarded.py [--tree DIR] [--workers N] [--wait] [--session NAME] [pytest args]`
is the one door for a full run. In order: the free-memory and free-disk
floors (exit 4 below either, or wait with `--wait`); `SUITE_LOCK`, claimed by
holder id with an exclusive create, taken over only when its holder's process
is gone, released only by ownership (exit 5 while held); the worker cap
(`max_workers_with_seat` while the local model seat answers on `seat_port`,
`pytest_resource_guard.MAX_WORKERS` otherwise); the run, streamed to
`<receipts_dir>/<sha>/suite.log` and killed if free memory or disk falls under
the abort floors; and the receipt `<receipts_dir>/<sha>/suite.json`, whose `ok`
is the child's real exit code compared with zero and nothing else. The
runner exits with that same code. `tests/unit/test_run_suite_guarded.py`
proves a red file yields a not-ok receipt with exit code 1 and a green one an
ok receipt with exit code 0.
