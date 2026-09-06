# `FRIDAY_HOME` does not isolate Friday from `~/.friday`

**Found by:** the Friday-Models fine-tuning mission (separate repo, `FutureSpeakAI/Friday-Models`),
auditing this codebase at the pinned tag `v5.7.0` before running Agent Friday against
mission-owned test data. Recorded here because the gap is a product-level safety guarantee that
several other efforts (this mission, at least one other active session tonight) were told to rely
on, not something specific to the fine-tuning work — it belongs in this repo more than in ours.

## The claim that doesn't hold

Multiple docs and mission briefs describe pointing `FRIDAY_HOME` at a mission- or test-owned
directory as the way to run Friday without touching a user's real `~/.friday`. That guarantee is
false on the code as it stands at `v5.7.0`.

## What's actually true

`FRIDAY_HOME` **is** read in ~14 files, each independently doing something like:

```python
_HOME = Path(os.environ.get("FRIDAY_HOME") or Path.home())
```

(`services/approvals.py:86`, `services/channels/manager.py:23`, `services/dissent_gate.py:79`,
`services/goals.py:197`, `services/learning_loop.py:33`, `services/local_voice.py:44`,
`services/memory_dreaming.py:32`, `services/nemo_voice.py:54`, `services/onboarding.py:25`,
`services/platforms/base.py:39`, `services/platforms/__init__.py:29`, `services/soul.py:27`,
`services/user_model.py:32` — line numbers as of `v5.7.0`, re-verify before relying on them if
this file is read much later).

**The load-bearing module does not.** `src/agent_friday/core/__init__.py:635-636`:

```python
HOME = Path(os.path.expanduser("~"))
FRIDAY_DIR = HOME / ".friday"
```

No environment check at all. This is the `HOME`/`FRIDAY_DIR` that `services/agent.py` itself
imports, and that settings (`_load_settings()`/`_load_settings_raw()` reading
`settings.json`), the wiki (`WIKI_DIR = FRIDAY_DIR / "wiki"`), and — checked directly, not
inferred — `epistemic_engine.py:25` (`FRIDAY_DIR = Path.home() / ".friday"`) and
`cognitive_memory.py:35` (default `Path.home() / ".friday" / "memory"`) all resolve through,
independently, also hardcoded.

`runtime_dir()` (`core/__init__.py:657-668`, used by `residency_catalog.py`) checks a
**different** variable, `FRIDAY_RUNTIME_DIR`, and even that only redirects the runtime-artifacts
subtree — not settings, not conversation memory, not the wiki, not the vault.

## Net effect

Setting `FRIDAY_HOME` today redirects voice, onboarding, the dissent gate, goals, and a handful
of other peripheral subsystems. It does **not** stop Friday from reading and writing the real
`~/.friday/settings.json`, the real conversation memory, the real wiki, the real epistemic score
history, or the real cognitive-memory ledger. Anything that assumes `FRIDAY_HOME` gives it a
clean, isolated Friday instance — a test suite, an eval harness, an unattended agent driving
Friday's CLI/API — is, on the evidence here, still touching the real user's data through the
paths above.

This is worse than a feature that plainly doesn't exist: it's implemented in enough visible
places to look trustworthy on a shallow check, while the paths that actually matter for privacy
and data integrity are unprotected.

## Suggested fix shape (not implemented, not validated — a starting point for whoever picks this up)

Two options, in order of how much they actually close the gap:

1. **Make `core/__init__.py`'s `HOME`/`FRIDAY_DIR` honor `FRIDAY_HOME`,** matching the pattern
   already used in the 14 peripheral files, and audit every other module that computes
   `Path.home() / ".friday"` independently (`epistemic_engine.py`, `cognitive_memory.py`, and
   possibly others not caught by this pass — grep for `Path.home()` and `expanduser("~")` across
   `src/agent_friday/` rather than trusting this list is exhaustive) to route through the same
   central constant instead of recomputing it.
2. **A narrower stopgap for callers who can't wait for (1):** override the process's actual `HOME`
   (`USERPROFILE` on Windows) environment variable for the subprocess running Friday, since that's
   what `Path.home()`/`os.path.expanduser("~")` actually read. Not validated end-to-end by this
   audit — worth testing for side effects (anything else that reads `HOME`/`USERPROFILE` for an
   unrelated purpose) before trusting it near real user data.

## Who should care

Anyone writing an eval harness, test suite, or automated agent that seats a model or drives
Friday's CLI/API against synthetic or throwaway data and is relying on `FRIDAY_HOME` to keep it
away from the real `~/.friday`. As of this writing that includes at least the Friday-Models
mission and reportedly another concurrent effort tonight; there are likely more instances of this
same assumption in test code or CI that this audit didn't search for.
