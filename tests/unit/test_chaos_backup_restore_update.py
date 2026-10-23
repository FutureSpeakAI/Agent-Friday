"""Chaos for the gate (north star NS-34.16-3): a real kill during a backup, a
rollback (restore), an update check's state write and a connector token write.

Each case runs the operation in a separate interpreter that is killed with
os._exit(9) at the worst moment, then starts a new interpreter on the same
files, as the next start of Friday would. Nothing may be left torn under a
real name, and nothing may be missing.

- Backup: `friday export` (data zip) and `friday export --full` (.fbak) leave
  no file under the archive's name until it is whole; the next export clears
  the hidden partial a killed one left.
- Restore: the boot guard's rollback swaps the known-good copy in. A kill
  between setting the live path aside and putting the copy in its place used to
  leave the path missing (the whole package, when the path is the package);
  the next start now finishes the swap from its journal.
- Update: update_check's state file is written whole, so a start killed
  mid-write keeps the release it already announced.
- Connector token refresh: a Google token being rewritten when the process dies
  is the old token or the new one, never neither (a guard: this already held).

Not covered here: a kill during an index rebuild (memory lane, NS-34.9-9), the
Friday Bundle and its restore (#275/#291, not on main), receipt writes (#199),
and the Windows installer's own upgrade (packaging/windows; its next run puts
the kept user files back and recopies the payload).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
PASS = "a long enough test passphrase 7 kites"


def _run(script: str, home: Path, *, expect: int) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("PYTEST_") and k != "FRIDAY_HOME"}
    env.update(FRIDAY_HOME=str(home), FRIDAY_TESTING="1", PYTHONIOENCODING="utf-8")
    head = "import os, sys\nsys.path.insert(0, %r)\n" % str(SRC)
    r = subprocess.run([sys.executable, "-c", head + textwrap.dedent(script)],
                       env=env, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=300)
    assert r.returncode == expect, (
        "exit %s (expected %s)\n--- stdout\n%s\n--- stderr\n%s"
        % (r.returncode, expect, r.stdout[-2000:], r.stderr[-3000:]))
    return r


@pytest.fixture
def world(tmp_path):
    home = tmp_path / ".friday"
    for rel, text in (("settings.json", '{"agent_name": "T"}'),
                      ("wiki/notes/page.md", "a page " * 2000),
                      ("vault/finance/ledger.vault", "x" * 4000)):
        p = home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    (tmp_path / "app").mkdir()
    (tmp_path / "out").mkdir()
    return tmp_path


_EXPORT_SETUP = """
from pathlib import Path
from agent_friday import cli
from agent_friday.privacy import vault_crypto as vc
cli.FRIDAY_DIR = Path(%(home)r)
cli.PROJ_ROOT = Path(%(app)r)
cli._backup_profile = lambda: vc.FAST_PROFILE
"""

# Dies as the archive takes its name: half the bytes into a direct write, or
# just before the rename of a finished partial.
_KILL_ON_ARCHIVE = """
import pathlib, zipfile
_wb = pathlib.Path.write_bytes
def _half_then_die(self, data, *a, **k):
    if self.suffix in (".fbak", ".zip"):
        with open(self, "wb") as f:
            f.write(data[: len(data) // 2]); f.flush()
        os._exit(9)
    return _wb(self, data, *a, **k)
pathlib.Path.write_bytes = _half_then_die
_replace = os.replace
def _die_on_rename(a, b, *x, **k):
    if str(b).endswith((".fbak", ".zip")):
        os._exit(9)
    return _replace(a, b, *x, **k)
os.replace = _die_on_rename
_close = zipfile.ZipFile.close
def _die_before_the_directory(self):
    if getattr(self, "mode", "") == "w" and %(zip_kill)s:
        os._exit(9)
    return _close(self)
zipfile.ZipFile.close = _die_before_the_directory
"""


def _paths(world):
    return {"home": str(world / ".friday"), "app": str(world / "app"),
            "out": str(world / "out")}


def test_a_full_backup_killed_while_writing_leaves_no_torn_backup(world):
    p = _paths(world)
    _run(_EXPORT_SETUP % p + _KILL_ON_ARCHIVE % {"zip_kill": "False"} + """
cli.cmd_export(full=True, out=%r, passphrase=%r)
print("not killed")
""" % (p["out"], PASS), world / ".friday", expect=9)
    out = world / "out"
    assert not list(out.glob("friday-backup-*.fbak")), \
        "a killed backup left a torn file under the backup's own name"
    assert [x for x in out.iterdir() if ".partial-" in x.name], \
        "the kill did not land while the backup was being written"

    # The next run: the leftover partial is cleared and the backup opens.
    _run(_EXPORT_SETUP % p + """
assert cli.cmd_export(full=True, out=%r, passphrase=%r) == 0
""" % (p["out"], PASS), world / ".friday", expect=0)
    backups = list(out.glob("friday-backup-*.fbak"))
    assert len(backups) == 1
    assert [x.name for x in out.iterdir() if "partial" in x.name] == []
    _run(_EXPORT_SETUP % p + """
assert cli.cmd_decrypt_backup(%r, out=%r, passphrase=%r) == 0
""" % (str(backups[0]), p["out"], PASS), world / ".friday", expect=0)
    with zipfile.ZipFile(backups[0].with_suffix(".zip")) as zf:
        assert ".friday/settings.json" in {n.replace("\\", "/") for n in zf.namelist()}


def test_a_data_export_killed_while_writing_leaves_no_torn_zip(world):
    p = _paths(world)
    _run(_EXPORT_SETUP % p + _KILL_ON_ARCHIVE % {"zip_kill": "True"} + """
cli.cmd_export(full=False, out=%r)
print("not killed")
""" % p["out"], world / ".friday", expect=9)
    assert not list((world / "out").glob("friday-data-export-*.zip")), \
        "a killed export left a zip with no central directory under the export's name"


def test_an_update_check_killed_mid_write_keeps_what_it_announced(tmp_path):
    state = tmp_path / ".friday" / "update_check.json"
    _run("""
import json, pathlib
from agent_friday.services import update_check as uc
uc.STATE_FILE = pathlib.Path(%(state)r)
uc._write_state({"notified_version": "1.2.0", "last_success_ts": 1})
_wt = pathlib.Path.write_text
def _half_then_die(self, data, *a, **k):
    if self.name == "update_check.json":
        with open(self, "w", encoding="utf-8") as f:
            f.write(data[: len(data) // 2]); f.flush()
        os._exit(9)
    return _wt(self, data, *a, **k)
pathlib.Path.write_text = _half_then_die
_replace = os.replace
def _die_on_rename(a, b, *x, **k):
    if str(b).endswith("update_check.json"):
        os._exit(9)
    return _replace(a, b, *x, **k)
os.replace = _die_on_rename
uc._write_state({"notified_version": "1.3.0", "last_success_ts": 2, "pad": "x" * 20000})
""" % {"state": str(state)}, tmp_path / ".friday", expect=9)
    try:
        got = json.loads(state.read_text(encoding="utf-8"))
    except ValueError:
        pytest.fail("a killed update check left a torn state file (it reads as "
                    "empty, and the same release is announced again)")
    assert got.get("notified_version") == "1.2.0", got


def _rollback_world(tmp_path):
    app = tmp_path / "app"
    pkg = app / "pkg"
    (pkg / "sub").mkdir(parents=True)
    (pkg / "a.py").write_text("GOOD = 1\n", encoding="utf-8")
    (pkg / "sub" / "b.py").write_text("GOOD = 2\n", encoding="utf-8")
    settings = app / "settings.json"
    settings.write_text('{"x": "good"}', encoding="utf-8")
    return app, pkg, settings


def test_a_rollback_killed_mid_swap_is_repaired_by_the_next_start(tmp_path):
    home = tmp_path / ".friday"
    app, pkg, settings = _rollback_world(tmp_path)
    _run("""
import shutil
from pathlib import Path
from agent_friday.services import boot_guard as bg
pkg, settings = Path(%(pkg)r), Path(%(settings)r)
assert bg.snapshot_known_good(paths=[pkg, settings])["ok"]
(pkg / "a.py").write_text("BAD = 1\\n", encoding="utf-8")
settings.write_text('{"x": "bad!"}', encoding="utf-8")
# Die the moment the live package has been moved out of its place.
_move, _rename = shutil.move, os.rename
def _then_die(op):
    def wrapped(src, dst, *a, **k):
        r = op(src, dst, *a, **k)
        if str(src) == str(pkg):
            os._exit(9)
        return r
    return wrapped
shutil.move = _then_die(_move)
os.rename = _then_die(_rename)
bg.restore_known_good()
print("not killed")
""" % {"pkg": str(pkg), "settings": str(settings)}, home, expect=9)

    # The next start.
    _run("""
from agent_friday.services import boot_guard as bg
bg.mark_boot_started()
""", home, expect=0)
    assert pkg.is_dir(), "the rolled-back package was left missing"
    # Rolled back to what was live (the guard then restores again, whole, if
    # the start still fails), never a mix and never a hole.
    assert (pkg / "a.py").read_text(encoding="utf-8") == "BAD = 1\n"
    assert (pkg / "sub" / "b.py").read_text(encoding="utf-8") == "GOOD = 2\n"
    assert settings.read_text(encoding="utf-8") == '{"x": "bad!"}'
    leftovers = [x.name for x in app.iterdir() if x.name.startswith(".friday-restore-")]
    assert leftovers == [], leftovers
    assert not (home / "boot_guard" / "restore_journal.json").exists()
    notes = (home / "boot_guard" / "rollback_notes.jsonl").read_text(encoding="utf-8")
    assert "interrupted" in notes


def test_a_rollback_killed_before_any_swap_changes_nothing(tmp_path):
    """A guard: this already held before the journal (nothing live had moved)."""
    home = tmp_path / ".friday"
    app, pkg, settings = _rollback_world(tmp_path)
    _run("""
from pathlib import Path
from agent_friday.services import boot_guard as bg
pkg, settings = Path(%(pkg)r), Path(%(settings)r)
assert bg.snapshot_known_good(paths=[pkg, settings])["ok"]
(pkg / "a.py").write_text("BAD = 1\\n", encoding="utf-8")
# Die as the first known-good copy is staged, before any live path moves.
_copy = bg._copy_into
def _die(src, dst):
    _copy(src, dst)
    os._exit(9)
bg._copy_into = _die
bg.restore_known_good()
""" % {"pkg": str(pkg), "settings": str(settings)}, home, expect=9)
    _run("""
from agent_friday.services import boot_guard as bg
bg.mark_boot_started()
""", home, expect=0)
    assert (pkg / "a.py").read_text(encoding="utf-8") == "BAD = 1\n"   # untouched
    assert settings.read_text(encoding="utf-8") == '{"x": "good"}'
    assert [x.name for x in app.iterdir() if x.name.startswith(".friday-restore-")] == []


def test_a_token_rewrite_killed_mid_write_keeps_a_working_token(tmp_path):
    """A guard: credential_store.write_secret already writes beside and renames."""
    home = tmp_path / ".friday"
    _run("""
from agent_friday.services import google_accounts as ga
class C:
    def __init__(self, t): self.t = t
    def to_json(self): return '{"token": "%s", "refresh_token": "r"}' % self.t
ga._persist_token("0123456789abcdef", C("old-token"))
import pathlib
_rep = pathlib.Path.replace
def _die(self, target):
    if str(target).endswith(".token.enc"):
        os._exit(9)
    return _rep(self, target)
pathlib.Path.replace = _die
ga._persist_token("0123456789abcdef", C("new-token"))
""", home, expect=9)
    r = _run("""
import json
from agent_friday.services import google_accounts as ga, credential_store as cs
print(json.loads(cs.read_secret(ga._token_path("0123456789abcdef")))["token"])
""", home, expect=0)
    assert r.stdout.strip().splitlines()[-1] in ("old-token", "new-token")


def test_a_rollback_killed_after_the_swap_keeps_settings_no_wider(tmp_path):
    """A rollback is not the owner's click: a guarded setting comes back no
    wider than the live file had it, even when the process dies the moment the
    known-good settings file has taken the live name."""
    home = tmp_path / ".friday"
    settings = tmp_path / "app" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"laya_safe_writes": "on", "temperature": 0.3}), encoding="utf-8")
    _run("""
import json, shutil
from pathlib import Path
from agent_friday.services import boot_guard as bg
settings = Path(%(settings)r)
assert bg.snapshot_known_good(paths=[settings])["ok"]
settings.write_text(json.dumps({"laya_safe_writes": "off", "temperature": 0.9}), encoding="utf-8")
_rename = os.rename
def _then_die(src, dst, *a, **k):
    r = _rename(src, dst, *a, **k)
    if str(dst) == str(settings):
        os._exit(9)
    return r
os.rename = _then_die
bg.restore_known_good()
""" % {"settings": str(settings)}, home, expect=9)
    _run("""
from agent_friday.services import boot_guard as bg
bg.mark_boot_started()
""", home, expect=0)
    got = json.loads(settings.read_text(encoding="utf-8"))
    assert got["laya_safe_writes"] == "off", got


def test_the_launcher_puts_back_a_package_a_killed_rollback_set_aside(tmp_path):
    """The kill between the two renames of the package itself: the guard that
    repairs a rollback is inside the package, so the repository-root launcher
    puts the set-aside package back before it starts anything."""
    app = tmp_path / "app"
    src = app / "src"
    aside = src / ".friday-restore-aside-agent_friday-0123456789ab"
    aside.mkdir(parents=True)
    (aside / "server.py").write_text(
        "import pathlib\npathlib.Path('started.txt').write_text('started')\n", encoding="utf-8")
    (src / ".friday-restore-incoming-agent_friday-0123456789ab").mkdir()
    (app / "server.py").write_text((ROOT / "server.py").read_text(encoding="utf-8"), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    r = subprocess.run([sys.executable, str(app / "server.py")], cwd=str(tmp_path), env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    assert (src / "agent_friday" / "server.py").is_file(), "the package was not put back"
    assert (app / "started.txt").read_text(encoding="utf-8") == "started"
    assert not aside.exists()
