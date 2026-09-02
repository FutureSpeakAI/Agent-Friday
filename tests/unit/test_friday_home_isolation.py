"""`FRIDAY_HOME` must be a real isolation boundary, not a decorative one.

Several efforts (fine-tuning evals, the in-app end-to-end suite, kiosk images,
unattended agents driving Friday's CLI/API) are told to set `FRIDAY_HOME` to a
throwaway directory so they never touch a real user's `~/.friday`. Before the
change these tests accompany, that guarantee did not hold: `core/__init__.py`
computed `FRIDAY_DIR` from `os.path.expanduser("~")` with no environment check,
so merely *importing* it wrote `secret_key`, `settings.json`, `vibe-code-logs/`,
`creations/` and `friday-creations/` into the REAL home while `FRIDAY_HOME` sat
empty.

Every test here runs in a **subprocess** with three env vars set:

  * `FRIDAY_HOME`  -> the isolated directory Friday's state must land in
  * `HOME` / `USERPROFILE` -> a *decoy* home, pre-seeded with sentinel files

The decoy stands in for the real user's home. The assertions are deliberately
filesystem-level rather than "does this constant look right": we snapshot the
decoy (path, size, mtime_ns, sha256) before and after, and fail on ANY delta.
A test that only compared path strings could pass while a module wrote through
a path it computed some other way.

Subprocesses are required because every path in this codebase is a module-level
constant frozen at import time -- `monkeypatch.setenv` inside an already-imported
interpreter proves nothing.

Note these tests do NOT rely on the suite-wide `HOME` redirect in
`tests/conftest.py`. They set up their own decoy explicitly, so they would still
catch the regression if that redirect were ever removed -- and, more to the
point, they test the `FRIDAY_HOME` mechanism *independently* of the OS-level
home redirect, which is the whole claim under test.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_REAL_HOME_SENTINEL = "REAL-HOME-SETTINGS-MUST-NOT-BE-READ"
_ISOLATED_MARK = "ISOLATED-RUN-AGENT"

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src"


# ── The family ────────────────────────────────────────────────────────────────
# Every module that independently computes a Friday-state root, and the module
# attribute that holds it. Keep this list in sync with:
#   grep -rnE 'Path\.home\(\)|expanduser\("~"\)|USERPROFILE' src/agent_friday/
# Anything new that grows its own root computation belongs here.
STATE_ROOT_ATTRS: list[tuple[str, str]] = [
    ("agent_friday.core", "FRIDAY_DIR"),
    ("agent_friday.core", "WIKI_DIR"),
    ("agent_friday.core", "VIBE_LOG_DIR"),
    ("agent_friday.core", "DAILY_CREATIONS_DIR"),
    ("agent_friday.conversation_memory", "FRIDAY_DIR"),
    ("agent_friday.emotional_arc", "FRIDAY_DIR"),
    ("agent_friday.notifications_engine", "FRIDAY_DIR"),
    ("agent_friday.skill_capture", "FRIDAY_DIR"),
    ("agent_friday.skill_registry", "SKILLS_DIR"),
    ("agent_friday.mcp_oauth", "OAUTH_DIR"),
    ("agent_friday.governance.behavioral_monitor", "BASE_DIR"),
    ("agent_friday.routes.skills", "_SKILLS_WATCH_DIR"),
    ("agent_friday.services.approvals", "FRIDAY_DIR"),
    ("agent_friday.services.boot_guard", "STATE_DIR"),
    ("agent_friday.services.channels.manager", "FRIDAY_DIR"),
    ("agent_friday.services.connectors", "FRIDAY_DIR"),
    ("agent_friday.services.dissent_gate", "FRIDAY_DIR"),
    ("agent_friday.services.goals", "FRIDAY_DIR"),
    ("agent_friday.services.introspection", "FRIDAY_DIR"),
    ("agent_friday.services.learning_loop", "FRIDAY_DIR"),
    ("agent_friday.services.liveness_audit", "FRIDAY_DIR"),
    ("agent_friday.services.local_voice", "LOCAL_VOICE_DIR"),
    ("agent_friday.services.memory_dreaming", "FRIDAY_DIR"),
    ("agent_friday.services.nemo_voice", "NEMO_DIR"),
    ("agent_friday.services.platforms", "FRIDAY_DIR"),
    ("agent_friday.services.platforms.base", "FRIDAY_DIR"),
    ("agent_friday.services.soul", "FRIDAY_DIR"),
    ("agent_friday.services.user_model", "FRIDAY_DIR"),
    ("agent_friday.seed.data.job_tracker_schema", "DEFAULT_TRACKER_PATH"),
    ("agent_friday.seed.skills.application_engine.engine", "USER_OVERRIDE"),
    ("agent_friday.seed.skills.job_scanner.scanner", "USER_OVERRIDE"),
]

# Roots produced by a *call* rather than a module constant.
STATE_ROOT_CALLS: list[tuple[str, str]] = [
    ("agent_friday.core", "runtime_dir()"),
    ("agent_friday.services.presidio_shadow", "_privacy_dir()"),
    ("agent_friday.services.voice_installer", "_log_path()"),
]


# ── Decoy-home plumbing ───────────────────────────────────────────────────────
def _seed_decoy_home(root: Path) -> Path:
    """A stand-in for the real user's home, with tripwires in it.

    `settings.json` carries a sentinel value: if any module reads the decoy's
    settings instead of the isolated ones, the sentinel shows up in the
    subprocess's output and `test_no_read_of_the_real_home` fails. `wiki/` is
    the legacy directory `core/__init__.py` migrates and then RENAMES on
    import -- a destructive write to the real home that must not fire under
    `FRIDAY_HOME`.
    """
    friday = root / ".friday"
    friday.mkdir(parents=True, exist_ok=True)
    (friday / "settings.json").write_text(
        json.dumps({"agent_name": _REAL_HOME_SENTINEL,
                    "user_email": "the-real-user@example.com"}),
        encoding="utf-8")
    (friday / "secret_key").write_text("real-home-secret-must-not-be-read\n",
                                       encoding="utf-8")
    (friday / "conversations.db").write_bytes(b"real-home-conversation-data")
    wiki = friday / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    (wiki / "note.md").write_text("real wiki note\n", encoding="utf-8")
    legacy_wiki = root / "wiki"          # the ~/wiki core migrates + renames
    legacy_wiki.mkdir(parents=True, exist_ok=True)
    (legacy_wiki / "legacy.md").write_text("legacy wiki note\n", encoding="utf-8")
    (root / "Desktop").mkdir(parents=True, exist_ok=True)
    return root


def _snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    """path -> (size, mtime_ns, sha256). Directories get (-1, mtime_ns, "")."""
    out: dict[str, tuple[int, int, str]] = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        st = p.stat()
        if p.is_dir():
            out[rel + "/"] = (-1, st.st_mtime_ns, "")
        else:
            out[rel] = (st.st_size, st.st_mtime_ns,
                        hashlib.sha256(p.read_bytes()).hexdigest())
    return out


def _diff(before: dict, after: dict) -> list[str]:
    problems = []
    for k in sorted(set(after) - set(before)):
        problems.append(f"CREATED in the real home: {k}")
    for k in sorted(set(before) - set(after)):
        problems.append(f"DELETED from the real home: {k}")
    for k in sorted(set(before) & set(after)):
        if before[k] != after[k]:
            problems.append(f"MODIFIED in the real home: {k} "
                            f"({before[k]} -> {after[k]})")
    return problems


def _run(code: str, *, friday_home: Path | None, decoy: Path,
         extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    # Wipe anything the parent suite (or the developer's shell) set that would
    # let the child resolve state somewhere other than where this test decides.
    for k in ("FRIDAY_HOME", "FRIDAY_RUNTIME_DIR", "FRIDAY_MODELS_DIR",
              "FRIDAY_VOICE_ASSETS", "FRIDAY_SANDBOX_ROOT", "FRIDAY_SECRET_KEY"):
        env.pop(k, None)
    if friday_home is not None:
        env["FRIDAY_HOME"] = str(friday_home)
    env["HOME"] = str(decoy)
    env["USERPROFILE"] = str(decoy)
    env["HOMEDRIVE"] = decoy.drive or "C:"
    env["HOMEPATH"] = str(decoy)[len(decoy.drive):] or os.sep
    env["FRIDAY_TESTING"] = "1"
    env["PYTHONPATH"] = str(_SRC)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra_env or {})
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code)],
                          env=env, capture_output=True, text=True, timeout=600)


def _json_tail(proc: subprocess.CompletedProcess) -> dict:
    """Parse the last line of stdout as JSON, with a readable failure."""
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if not lines:
        pytest.fail("child produced no stdout.\n"
                    f"rc={proc.returncode}\nstderr:\n{proc.stderr[-4000:]}")
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        pytest.fail("child's last stdout line was not JSON.\n"
                    f"rc={proc.returncode}\nstdout tail:\n{proc.stdout[-4000:]}"
                    f"\nstderr:\n{proc.stderr[-4000:]}")


@pytest.fixture
def homes(tmp_path: Path) -> tuple[Path, Path]:
    """(decoy real home, isolated FRIDAY_HOME)."""
    decoy = _seed_decoy_home(tmp_path / "decoy_real_home")
    iso = tmp_path / "isolated"
    iso.mkdir(parents=True, exist_ok=True)
    return decoy, iso


# ── The load-bearing test ─────────────────────────────────────────────────────
_IMPORT_THE_WORLD = """
    import json, os, sys
    from pathlib import Path
    mark = os.environ["FRIDAY_ISOLATION_MARK"]
    mods = %r
    loaded, failed = [], {}
    for name in mods:
        try:
            __import__(name)
            loaded.append(name)
        except Exception as e:
            failed[name] = f"{type(e).__name__}: {e}"

    import agent_friday.core as core
    # READ FIRST, before anything writes: if this picks up the decoy's
    # settings.json the sentinel comes back and the read assertion fires.
    read_before_write = (core._load_settings() or {}).get("agent_name")
    # Then do real work -- what an eval harness or the in-app end-to-end
    # suite triggers on its first request.
    core._save_settings({"agent_name": mark})
    read_after_write = (core._load_settings() or {}).get("agent_name")
    core.WIKI_DIR.mkdir(parents=True, exist_ok=True)
    (core.WIKI_DIR / "isolation-probe.md").write_text(
        "written under isolation", encoding="utf-8")
    print(json.dumps({
        "loaded": loaded, "failed": failed,
        "read_before_write": read_before_write,
        "read_after_write": read_after_write,
        "settings_file": str(core.SETTINGS_FILE),
        "secret_key_len": len(core.app.secret_key or ""),
    }))
"""


def _probe(decoy, iso, mods=None):
    """Run the standard exercise-Friday probe under isolation."""
    return _run(_IMPORT_THE_WORLD % ([m for m, _ in STATE_ROOT_ATTRS]
                                     if mods is None else mods,),
                friday_home=iso, decoy=decoy,
                extra_env={"FRIDAY_ISOLATION_MARK": _ISOLATED_MARK})


def test_writes_never_reach_the_real_home(homes):
    """With FRIDAY_HOME set, importing and exercising Friday must leave the
    real home byte-for-byte and mtime-for-mtime untouched.

    This is the assertion the whole isolation claim rests on. Before the fix it
    failed with six creations under the decoy: `.friday/secret_key`,
    `.friday/settings.json`, `.friday/vibe-code-logs/`, `.friday/creations/`,
    `.friday/friday-creations/`, `.friday/audio-cache/` -- plus the legacy
    `wiki/` directory renamed out from under the user.
    """
    decoy, iso = homes
    before = _snapshot(decoy)
    proc = _probe(decoy, iso)
    after = _snapshot(decoy)

    problems = _diff(before, after)
    assert not problems, (
        "FRIDAY_HOME did not isolate Friday from the real home.\n  "
        + "\n  ".join(problems)
        + f"\n\nchild rc={proc.returncode}\nstderr tail:\n{proc.stderr[-2000:]}")


def test_no_read_of_the_real_home(homes):
    """Settings must come from FRIDAY_HOME, never the real home's copy.

    Writes are the loud failure; reads are the quiet one. A run that merely
    *reads* the real `settings.json` still leaks the user's configuration --
    model seats, API-key routing, budgets -- into a process that was told it
    was isolated.
    """
    decoy, iso = homes
    proc = _probe(decoy, iso)
    payload = _json_tail(proc)

    assert payload["read_before_write"] != _REAL_HOME_SENTINEL, (
        "Friday read settings.json out of the REAL home while FRIDAY_HOME was "
        f"set (settings_file={payload['settings_file']})")
    assert payload["read_after_write"] == _ISOLATED_MARK, (
        "settings written under FRIDAY_HOME did not round-trip; got "
        f"{payload['read_after_write']!r} from {payload['settings_file']}")
    assert Path(payload["settings_file"]) == iso / "settings.json", (
        f"settings.json resolved to {payload['settings_file']}, expected it "
        f"under FRIDAY_HOME ({iso})")
    assert payload["secret_key_len"], "no Flask secret key was resolved at all"
    assert (iso / "settings.json").is_file(), (
        "nothing was actually written under FRIDAY_HOME")


def test_legacy_wiki_migration_does_not_touch_the_real_home(homes):
    """`core` migrates and then RENAMES `~/wiki` at import time.

    Under FRIDAY_HOME that would be a destructive write to a real user's
    home directory by a process that was promised it could not touch it.
    """
    decoy, iso = homes
    _probe(decoy, iso, mods=["agent_friday.core"])
    assert (decoy / "wiki").is_dir(), "the real ~/wiki was renamed away"
    assert not (decoy / "wiki_migrated_to_friday").exists(), (
        "core renamed the real ~/wiki despite FRIDAY_HOME being set")
    assert (decoy / "wiki" / "legacy.md").read_text(encoding="utf-8") == \
        "legacy wiki note\n"


def test_every_state_root_resolves_under_friday_home(homes):
    """Not one module may compute its own root from the real home.

    Path-string check, complementary to the filesystem check above: a module
    can be *inert on import* and still resolve wrong the moment it is used.
    """
    decoy, iso = homes
    code = """
        import json, os
        from pathlib import Path
        attrs = %r
        calls = %r
        results, failed = {}, {}
        for mod, attr in attrs:
            try:
                m = __import__(mod, fromlist=["_"])
                results[f"{mod}.{attr}"] = str(getattr(m, attr))
            except Exception as e:
                failed[f"{mod}.{attr}"] = f"{type(e).__name__}: {e}"
        for mod, expr in calls:
            try:
                m = __import__(mod, fromlist=["_"])
                fn = getattr(m, expr[:-2])
                results[f"{mod}.{expr}"] = str(fn())
            except Exception as e:
                failed[f"{mod}.{expr}"] = f"{type(e).__name__}: {e}"
        print(json.dumps({"results": results, "failed": failed,
                          "home": str(Path.home())}))
    """ % (STATE_ROOT_ATTRS, STATE_ROOT_CALLS)
    proc = _run(code, friday_home=iso, decoy=decoy)
    payload = _json_tail(proc)

    assert not payload["failed"], (
        "some state roots could not be resolved at all:\n  "
        + "\n  ".join(f"{k}: {v}" for k, v in payload["failed"].items()))

    iso_s, decoy_s = str(iso).lower(), str(decoy).lower()
    stray = {k: v for k, v in payload["results"].items()
             if not v.lower().startswith(iso_s)}
    assert not stray, (
        f"{len(stray)} state root(s) resolved OUTSIDE FRIDAY_HOME ({iso}); "
        f"the real home is {decoy}:\n  "
        + "\n  ".join(f"{k} -> {v}"
                      + ("   <-- IN THE REAL HOME" if v.lower().startswith(decoy_s)
                         else "")
                      for k, v in sorted(stray.items())))


def test_default_behaviour_is_unchanged_when_friday_home_is_unset(homes):
    """No FRIDAY_HOME -> every root stays exactly where it has always been:
    directly under `Path.home()/.friday`. This is the regression guard for the
    overwhelming majority of users, who set nothing."""
    decoy, _ = homes
    code = """
        import json
        from pathlib import Path
        attrs = %r
        calls = %r
        results = {}
        for mod, attr in attrs:
            m = __import__(mod, fromlist=["_"])
            results[f"{mod}.{attr}"] = str(getattr(m, attr))
        for mod, expr in calls:
            m = __import__(mod, fromlist=["_"])
            results[f"{mod}.{expr}"] = str(getattr(m, expr[:-2])())
        print(json.dumps({"results": results,
                          "expected_root": str(Path.home() / ".friday")}))
    """ % (STATE_ROOT_ATTRS, STATE_ROOT_CALLS)
    proc = _run(code, friday_home=None, decoy=decoy)
    payload = _json_tail(proc)
    expected = payload["expected_root"].lower()
    stray = {k: v for k, v in payload["results"].items()
             if not v.lower().startswith(expected)}
    assert not stray, (
        "with FRIDAY_HOME unset these roots moved away from ~/.friday:\n  "
        + "\n  ".join(f"{k} -> {v}" for k, v in sorted(stray.items())))


def test_friday_home_is_the_state_dir_not_its_parent(homes):
    """One `FRIDAY_HOME` convention, not two.

    Thirteen service modules used to read `FRIDAY_HOME` as a replacement for
    `~` and append `.friday` themselves, while `agent_friday.paths.friday_home()`
    treats it as the state directory itself. Both isolate, but together they
    split one Friday instance across two directories -- settings in
    `$FRIDAY_HOME`, soul and goals in `$FRIDAY_HOME/.friday`. This pins the
    single surviving convention.
    """
    decoy, iso = homes
    code = """
        import json
        from agent_friday.paths import friday_home
        from agent_friday.services import soul, goals, user_model
        import agent_friday.core as core
        print(json.dumps({
            "paths": str(friday_home()),
            "core": str(core.FRIDAY_DIR),
            "soul": str(soul.FRIDAY_DIR),
            "goals": str(goals.FRIDAY_DIR),
            "user_model": str(user_model.FRIDAY_DIR),
        }))
    """
    proc = _run(code, friday_home=iso, decoy=decoy)
    payload = _json_tail(proc)
    assert len(set(v.lower() for v in payload.values())) == 1, (
        "FRIDAY_HOME resolves to more than one state directory: "
        + json.dumps(payload, indent=2))
    assert Path(payload["paths"]) == iso
