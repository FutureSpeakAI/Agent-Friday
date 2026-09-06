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
