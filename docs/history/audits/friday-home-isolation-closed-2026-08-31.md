# `FRIDAY_HOME` isolation gap — closed, and how it was proven

Closes the gap reported in [#13](https://github.com/FutureSpeakAI/Agent-Friday/pull/13)
(`docs/audits/friday-home-isolation-gap-2026-08-31.md`, on that PR's branch).
This file records what was actually changed and what the proof was, because the
gap's whole character was *looking* fixed while not being fixed — so "trust the
diff" is the wrong standard here.

## What was wrong

`FRIDAY_HOME` was documented, and relied on by concurrent fine-tuning/eval work
and by the in-app end-to-end suite, as the way to point Friday at a throwaway
directory instead of a real user's `~/.friday`. It did not do that.

`core/__init__.py` — the module `services/`, `routes/` and the settings loader
all resolve `FRIDAY_DIR` through — computed the state root inline as
`Path(os.path.expanduser("~")) / ".friday"`, with no environment check. The ~14
peripheral modules that *did* honor `FRIDAY_HOME` made the guardrail look
trustworthy on a shallow check while the paths that mattered were unprotected.

## Two corrections to the original audit

Worth recording, because both would mislead someone picking this up later:

1. **The two modules #13 named were already fixed.** `epistemic_engine.py` and
   `cognitive_memory.py` both route through `friday_home()` on `main` — the
   merged PR #6 got them. #13 was written against the pinned tag `v5.7.0`, so
   its file list was stale by the time it was read. The real remaining set was
   found by a fresh sweep, not from that list.

2. **There were two incompatible readings of `FRIDAY_HOME` in the tree**, which
   #13 did not surface:
   - `agent_friday.paths.friday_home()` (PR #6): `FRIDAY_HOME` **is** the state
     directory.
   - 13 service modules, plus `packaging/windows/uninstall.ps1` and the voice
     spec's env table: `FRIDAY_HOME` replaces `~`, and `.friday` is appended.

   Both isolate. Holding both at once splits one Friday across two directories
   — `settings.json` at `$FRIDAY_HOME`, soul/goals/approvals/platforms/voice at
   `$FRIDAY_HOME/.friday`. That is not an isolated instance, it is two half
   instances, which is its own failure mode for an end-to-end suite. The
   `friday_home()` reading survives; the uninstaller (which *deletes* what it
   resolves) and the spec were corrected to match.

## The family

33 modules computed a state root independently, in three flavours:

| Flavour | Count | Symptom under `FRIDAY_HOME` |
|---|---|---|
| `_HOME = Path(os.environ.get("FRIDAY_HOME") or Path.home())` then `/ ".friday"` | 13 | isolated, but into `$FRIDAY_HOME/.friday` — split brain |
| `Path.home()` / `expanduser("~")`, no env check | 18 | **wrote to the real home** |
| `os.environ["USERPROFILE"] or expanduser("~")` | 2 | **read from the real home** |

All now resolve through `agent_friday.paths.friday_home()`. Reproduce the sweep
with:

```
grep -rnE 'Path\.home\(\)|expanduser\("~"\)|USERPROFILE' src/agent_friday/
```

## The part that is not a path

Storing state elsewhere is not enough if the process still mutates the host
home. Two import-time side effects in `core/__init__.py` now gate on
`paths.is_redirected()`:

- **The legacy `~/wiki` migration**, which ends by **renaming** `~/wiki` to
  `~/wiki_migrated_to_friday`. A redirected test run moved a real user's wiki
  out from under them — a data scare caused by the safety mechanism itself.
- **`~/Desktop/friday-creations`**, created on the host's actual Desktop.

Correspondingly, `core.HOME` is now `paths.user_home()` and is deliberately
**not** redirected. It answers a different question: the human's machine —
`~/Desktop`, `~/Projects`, the sandbox root bounding which files Friday may
read. Pointing the sandbox root at an empty temp directory would not isolate
anything; it would just stop Friday reading the files it was asked about.
Conflating "the human's home" with "Friday's state directory" is the root cause
of the whole gap.

## Proof

An isolation test that passes on broken code is worth nothing, so the test was
written and committed **red** first (commit `test(isolation): prove FRIDAY_HOME
does not isolate Friday from ~/.friday`). Five of its six tests fail at that
commit; all six pass after the fix.

`tests/unit/test_friday_home_isolation.py` runs each check in a **subprocess**
(every path here is a module-level constant frozen at import, so monkeypatching
a loaded interpreter proves nothing) with `FRIDAY_HOME` pointed at a temp
directory and `HOME`/`USERPROFILE` at a *decoy* home seeded with tripwires. It
snapshots the decoy — path, size, `mtime_ns`, sha256 — around a real exercise of
Friday and fails on any delta. It does not lean on `tests/conftest.py`'s
suite-wide `HOME` redirect, so it tests the `FRIDAY_HOME` mechanism
independently of the OS-level one.

What the red looked like — merely importing `agent_friday.core` with
`FRIDAY_HOME` set to an empty directory:

```
CREATED  .friday/audio-cache/          CREATED  Desktop/friday-creations/
CREATED  .friday/creations/            CREATED  wiki_migrated_to_friday/
CREATED  .friday/vibe-code-logs/       DELETED  wiki/            <- the user's ~/wiki
MODIFIED .friday/settings.json  (96 -> 7216 bytes)
```

plus reads: `_load_settings()` returned the decoy's `agent_name`. 22 of 34
state roots resolved outside `FRIDAY_HOME`.

### End-to-end, against a real server

Beyond the unit tests, a **real Friday server** was booted with `FRIDAY_HOME`
redirected and `HOME` left pointing at the actual user home — the configuration
the guarantee is actually claimed for — and driven over HTTP (`/api/health`,
settings read/write/re-read, `/api/wiki/structure`, `/api/wiki/update`,
`/api/conversations`, `/api/models`; all 200). Afterwards:

- Everything the run produced landed under `FRIDAY_HOME`: `settings.json` (with
  the written value read back), `secret_key`, `vault/`, `wiki/`, `conversations/`,
  `boot_guard/`, the server lock and pid.
- Nothing the run wrote appeared in the real `~/.friday`.

**Caveat, stated because it matters for anyone repeating this:** a real
`~/.friday` cannot be held still while its owner's Friday is running. The
observed deltas during the run (`friday.log`, `forensics/*`, `ambient_state.json`,
`google_accounts/accounts.json`, `security/credential_audit.jsonl`,
`server_restart_stderr.log`) were checked against a **control window of the same
length with nothing of ours running**, which showed the same files changing — a
strict superset. Those are the live instance writing its own state. The
falsifiable claim is the narrow one: no file the isolated run wrote was touched
in the real home, and the run's own state is entirely under `FRIDAY_HOME`.

## Regression surface

Full suite (`tests/unit tests/api`) on `origin/main` and on this branch:
**identical** failure sets — the same 8 pre-existing failures (`residency_arbiter`,
`ollama_manager`, `gate_harness_integrity`, all of which read real GPU state).
6694 passed on main, 6700 on the branch: the six new isolation tests.

With `FRIDAY_HOME` unset, behavior is byte-identical — every root still resolves
to `Path.home()/".friday"`, pinned by its own test. The only behavior change
lands on callers already setting `FRIDAY_HOME` under the "replacement for `~`"
reading, whose 13 subsystems move from `$FRIDAY_HOME/.friday` to `$FRIDAY_HOME`,
joining the other 20.

## Known gap deliberately left open

`services/local_seats.py` and `services/model_catalog.py` read the runtime model
index. They now honor `FRIDAY_HOME`, but keep the literal `runtime` path segment
they always had, so they still ignore `FRIDAY_RUNTIME_DIR` and
`settings.json["runtime_dir"]`. Routing them through `runtime_dir()` would import
`agent_friday.core` — a ~4s Flask bootstrap — onto the `friday models` CLI path.
Pre-existing, noted in a comment at both sites, not widened here.
