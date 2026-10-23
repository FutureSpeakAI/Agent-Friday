"""A Friday Bundle restore is finished or rolled back, whatever stops it
(north star §34.16-3, the restore half; §27.6). Each test stops a real child
interpreter with os._exit(9) at one step of the restore, then a new start on
the same folders finds either the old Friday whole or the restored one whole,
never neither. A bundle write stopped part way leaves only a partial file,
which the next bundle clears."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent_friday.governance import proof_of_integrity as poi

ROOT = Path(__file__).resolve().parents[2]
PASS = "orbit lantern pepper tundra violet 42"   # pragma: allowlist secret
VAULT_PASS = "vault passphrase for the test"  # pragma: allowlist secret

CHILD = r"""
import os, sys
from pathlib import Path
step, home, bundle = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
sys.modules["keyring"] = None
from agent_friday.governance import proof_of_integrity as poi
poi._GOV_KEY_FILE = home / "vault" / ".governance-key"
from agent_friday.services import friday_bundle as fb
if not hasattr(fb, "_journal_step"):
    sys.exit(3)                       # no step to stop at: this Friday keeps no journal
if step.startswith("replace:"):
    real_replace = fb._replace
    def rep(src, dst):
        real_replace(src, dst)
        if step[8:] in Path(dst).name:
            os._exit(9)
    fb._replace = rep
elif step.startswith("unlink:"):
    real_unlink = fb._unlink_retry
    def unl(p):
        real_unlink(p)
        if Path(p).name.endswith(step[7:]):
            os._exit(9)
    fb._unlink_retry = unl
else:
    def stop(s):
        if s == step:
            os._exit(9)
    fb._journal_step = stop
fb.restore(bundle, sys.argv[4], home=home, replace=True)
sys.exit(0)
"""

CHILD_MAKE = r"""
import os, sys
from pathlib import Path
home, out = Path(sys.argv[1]), Path(sys.argv[2])
sys.modules["keyring"] = None
from agent_friday.governance import proof_of_integrity as poi
poi._GOV_KEY_FILE = home / "vault" / ".governance-key"
from agent_friday.privacy import vault_crypto as vc
from agent_friday.services import friday_bundle as fb
real, n = fb._add_file, [0]
def add(tar, name, src):
    n[0] += 1
    if n[0] == 2:
        os._exit(9)
    return real(tar, name, src)
fb._add_file = add
fb.create(out, sys.argv[3], home=home, kdf_profile=vc.FAST_PROFILE, categories=("memory",))
sys.exit(0)
"""


def _env(home: Path, scratch: Path) -> dict:
    env = dict(os.environ)
    env.update(FRIDAY_HOME=str(home), FRIDAY_LEDGER_DIR=str(home / "ledger"),
               FRIDAY_VAULT_PASSPHRASE=VAULT_PASS, FRIDAY_PASSWORD=VAULT_PASS,
               HOME=str(scratch), USERPROFILE=str(scratch), FRIDAY_NO_ARBITER="1",
               CUDA_VISIBLE_DEVICES="-1", PYTHONIOENCODING="utf-8",
               PYTHONPATH=str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", ""))
    return env


def _run(code: str, args: list, home: Path, scratch: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code, *[str(a) for a in args]], env=_env(home, scratch),
                          cwd=str(scratch), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=300)


def _point_at(home: Path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(home))
    monkeypatch.setenv("FRIDAY_LEDGER_DIR", str(home / "ledger"))
    monkeypatch.setattr(poi, "_GOV_KEY_FILE", home / "vault" / ".governance-key")
    from agent_friday.services import action_ledger
    action_ledger.reset_caches()


@pytest.fixture
def machines(tmp_path, monkeypatch):
    """A bundle made on machine A, and machine B with an older Friday in use."""
    a, b, scratch = tmp_path / "a" / ".friday", tmp_path / "b" / ".friday", tmp_path / "scratch"
    for p in (a, b, scratch):
        p.mkdir(parents=True)
    monkeypatch.setitem(sys.modules, "keyring", None)
    monkeypatch.setenv("FRIDAY_VAULT_PASSPHRASE", VAULT_PASS)
    monkeypatch.setenv("FRIDAY_PASSWORD", VAULT_PASS)
    from agent_friday.services import vault_passphrase
    vault_passphrase.reset_cache()
    _point_at(a, monkeypatch)
    for rel, text in {"settings.json": json.dumps({"agent_name": "Friday"}),
                      "core_memory/ME.md": "- the restored Friday\n",
                      "wiki/page.md": "# A page\n"}.items():
        (a / rel).parent.mkdir(parents=True, exist_ok=True)
        (a / rel).write_text(text, encoding="utf-8")
    from agent_friday.services import action_ledger
    action_ledger.append("decision", {"tool": "probe_send", "decision": "allow"}, durable=True)
    from agent_friday.privacy import vault_crypto as vc
    from agent_friday.services import friday_bundle as fb
    bundle = tmp_path / "out" / "a.fridaybundle"
    fb.create(bundle, PASS, home=a, kdf_profile=vc.FAST_PROFILE, categories=("memory", "settings", "receipts"))
    (b / "core_memory").mkdir()
    (b / "core_memory" / "ME.md").write_text("- the Friday that was here\n", encoding="utf-8")
    (b / ".setup_complete").write_text("1", encoding="utf-8")
    _point_at(b, monkeypatch)
    return a, b, bundle, scratch


def _siblings(b: Path, part: str) -> list:
    return sorted(p.name for p in b.parent.iterdir() if part in p.name)


def _fb():
    from agent_friday.services import friday_bundle as fb
    assert hasattr(fb, "recover_interrupted_restore"), "an interrupted restore is never recovered"
    return fb


@pytest.mark.parametrize("step", ["keys-pending", "keys", "aside"])
def test_a_restore_stopped_before_its_swap_is_rolled_back(machines, step):
    a, b, bundle, scratch = machines
    fb = _fb()
    r = _run(CHILD, [step, b, bundle, PASS], b, scratch)
    assert r.returncode == 9, (r.returncode, r.stdout[-2000:], r.stderr[-2000:])
    if step == "aside":
        assert not b.exists(), "the window this protects: the old folder is aside, the new not in yet"
    out = fb.recover_interrupted_restore(b)
    assert out and out["rolled"] == "back", out
    assert (b / "core_memory" / "ME.md").read_text(encoding="utf-8") == "- the Friday that was here\n"
    assert not _siblings(b, ".friday-restore-"), "the staging copy was left behind"
    assert not _siblings(b, ".pre-restore-")
    assert not _siblings(b, ".restore-journal")
    assert fb.recover_interrupted_restore(b) is None          # once only


def test_a_restore_stopped_after_its_swap_is_finished_at_the_next_start(machines):
    a, b, bundle, scratch = machines
    fb = _fb()
    r = _run(CHILD, ["swapped", b, bundle, PASS], b, scratch)
    assert r.returncode == 9, (r.returncode, r.stdout[-2000:], r.stderr[-2000:])
    assert not list((b / "restore").glob("receipt-*.json")), "the receipt was written before the stop"
    out = fb.recover_interrupted_restore(b)
    assert out and out["rolled"] == "forward", out
    assert (b / "core_memory" / "ME.md").read_text(encoding="utf-8") == "- the restored Friday\n"
    receipt = json.loads(next((b / "restore").glob("receipt-*.json")).read_text(encoding="utf-8"))
    assert receipt["checked_in_place"]["files_match"] is True and "next start" in receipt["finished"]
    assert (b / "restore" / "hold.json").exists(), "outward actions must wait after any restore"
    assert (b / "restore" / "needs_reindex.json").exists()
    assert not (b / "restore" / "pending.json").exists()
    assert not _siblings(b, ".restore-journal")
    old = _siblings(b, ".pre-restore-")
    assert len(old) == 1 and (b.parent / old[0] / "core_memory" / "ME.md").read_text(encoding="utf-8") \
        == "- the Friday that was here\n"


def test_a_start_that_made_a_fresh_folder_in_the_gap_is_kept_aside(machines):
    a, b, bundle, scratch = machines
    fb = _fb()
    r = _run(CHILD, ["aside", b, bundle, PASS], b, scratch)
    assert r.returncode == 9
    b.mkdir()
    (b / "settings.json").write_text("{}", encoding="utf-8")       # what a fresh start seeds
    out = fb.recover_interrupted_restore(b)
    assert out["rolled"] == "back"
    assert (b / "core_memory" / "ME.md").read_text(encoding="utf-8") == "- the Friday that was here\n"
    assert len(_siblings(b, ".restore-undone-")) == 1


def test_a_failure_before_the_swap_rolls_back_at_once(machines, monkeypatch):
    a, b, bundle, scratch = machines
    fb = _fb()

    def boom(step):
        if step == "aside":
            raise OSError("the disk went away")
    monkeypatch.setattr(fb, "_journal_step", boom)
    with pytest.raises(OSError):
        fb.restore(bundle, PASS, home=b, replace=True)
    assert (b / "core_memory" / "ME.md").read_text(encoding="utf-8") == "- the Friday that was here\n"
    assert not _siblings(b, ".friday-restore-") and not _siblings(b, ".restore-journal")


def test_a_whole_restore_leaves_no_journal(machines):
    a, b, bundle, scratch = machines
    fb = _fb()
    fb.restore(bundle, PASS, home=b, replace=True)
    assert (b / "core_memory" / "ME.md").read_text(encoding="utf-8") == "- the restored Friday\n"
    assert not _siblings(b, ".restore-journal") and not (b / "restore" / "pending.json").exists()
    assert fb.recover_interrupted_restore(b) is None


def test_a_bundle_write_stopped_part_way_leaves_only_a_partial_that_the_next_one_clears(machines, tmp_path):
    a, b, bundle, scratch = machines
    fb = _fb()
    out = tmp_path / "later" / "b.fridaybundle"
    r = _run(CHILD_MAKE, [a, out, PASS], a, scratch)
    assert r.returncode == 9, (r.returncode, r.stderr[-2000:])
    assert not out.exists(), "a stopped write must never look like a bundle"
    partials = list(out.parent.glob("b.fridaybundle.partial-*"))
    assert len(partials) == 1
    old = time.time() - fb.STALE_PARTIAL_S - 60
    os.utime(partials[0], (old, old))
    fresh = out.parent / "c.fridaybundle.partial-abcdef"
    fresh.write_bytes(b"a writer still at work")
    assert fb.clear_stale_partials(out.parent) == [partials[0].name]
    assert fresh.exists() and not partials[0].exists()


class _Keyring:
    """A keychain in memory."""

    class errors:
        class NoKeyringError(Exception):
            pass

    def __init__(self):
        self.store = {}

    def get_password(self, svc, name):
        return self.store.get((svc, name))

    def set_password(self, svc, name, value):
        self.store[(svc, name)] = value

    def delete_password(self, svc, name):
        self.store.pop((svc, name), None)


def test_a_rolled_back_restore_gives_the_keychain_its_key_back(machines, monkeypatch):
    a, b, bundle, scratch = machines
    fb = _fb()
    kr = _Keyring()
    other = "22" * 32
    kr.set_password("agent-friday", "governance-key", other)       # the Friday that was here
    monkeypatch.setitem(sys.modules, "keyring", kr)
    monkeypatch.setitem(sys.modules, "keyring.errors", kr.errors)

    def boom(step):
        if step == "aside":
            raise OSError("the disk went away")
    monkeypatch.setattr(fb, "_journal_step", boom)
    with pytest.raises(OSError):
        fb.restore(bundle, PASS, home=b, replace=True)
    assert kr.get_password("agent-friday", "governance-key") == other
    assert (b / "core_memory" / "ME.md").read_text(encoding="utf-8") == "- the Friday that was here\n"


def test_a_second_restore_waits_for_the_first(machines):
    a, b, bundle, scratch = machines
    fb = _fb()
    with fb._RestoreLock(b) as lk:
        assert lk.held
        with pytest.raises(fb.BundleError, match="Another restore"):
            fb.restore(bundle, PASS, home=b, replace=True)
    assert (b / "core_memory" / "ME.md").read_text(encoding="utf-8") == "- the Friday that was here\n"


def test_a_journal_naming_other_folders_is_never_acted_on(machines, tmp_path):
    a, b, bundle, scratch = machines
    fb = _fb()
    victim = tmp_path / "Documents"
    victim.mkdir()
    (victim / "letter.txt").write_text("keep me", encoding="utf-8")
    fb._journal_path(b).write_text(json.dumps({
        "v": 1, "step": "aside", "home": str(Path(os.path.abspath(str(b)))), "stage": str(victim),
        "moved_aside": str(victim), "bundle_id": "x", "keys": {}}), encoding="utf-8")
    out = fb.recover_interrupted_restore(b)
    assert out["rolled"] == "none"
    assert (victim / "letter.txt").read_text(encoding="utf-8") == "keep me"
    assert (b / "core_memory" / "ME.md").exists()

def test_a_stop_right_after_the_old_folder_is_moved_aside_puts_it_back(machines):
    a, b, bundle, scratch = machines
    fb = _fb()
    r = _run(CHILD, ["replace:.pre-restore-", b, bundle, PASS], b, scratch)
    assert r.returncode == 9, (r.returncode, r.stderr[-2000:])
    assert not b.exists()
    out = fb.recover_interrupted_restore(b)
    assert out and out["rolled"] == "back", out
    assert (b / "core_memory" / "ME.md").read_text(encoding="utf-8") == "- the Friday that was here\n"
    assert not _siblings(b, ".pre-restore-")


def test_a_stop_as_the_journal_is_dropped_leaves_the_restore_finished(machines):
    a, b, bundle, scratch = machines
    fb = _fb()
    r = _run(CHILD, ["unlink:" + fb.JOURNAL_SUFFIX, b, bundle, PASS], b, scratch)
    assert r.returncode == 9, (r.returncode, r.stderr[-2000:])
    assert fb.recover_interrupted_restore(b) is None
    assert (b / "core_memory" / "ME.md").read_text(encoding="utf-8") == "- the restored Friday\n"
    assert list((b / "restore").glob("receipt-*.json"))


def test_a_keychain_that_will_not_take_the_key_stops_the_restore(machines, monkeypatch):
    a, b, bundle, scratch = machines
    fb = _fb()
    other = "33" * 32

    class Stubborn(_Keyring):
        def set_password(self, svc, name, value):
            if name == "governance-key" and value != other:
                raise RuntimeError("locked")
            super().set_password(svc, name, value)
    kr = Stubborn()
    kr.set_password("agent-friday", "governance-key", other)
    monkeypatch.setitem(sys.modules, "keyring", kr)
    monkeypatch.setitem(sys.modules, "keyring.errors", kr.errors)
    with pytest.raises(fb.BundleError, match="keychain"):
        fb.restore(bundle, PASS, home=b, replace=True)
    assert kr.get_password("agent-friday", "governance-key") == other
    assert (b / "core_memory" / "ME.md").read_text(encoding="utf-8") == "- the Friday that was here\n"
    assert not _siblings(b, ".restore-journal") and not _siblings(b, ".friday-restore-")


@pytest.mark.skipif(os.name != "nt", reason="folder names are not case-sensitive on Windows only")
def test_a_journal_written_with_other_letter_case_is_still_used(machines):
    a, b, bundle, scratch = machines
    fb = _fb()
    r = _run(CHILD, ["aside", b, bundle, PASS], b, scratch)
    assert r.returncode == 9
    jp = fb._journal_path(b)
    jr = json.loads(jp.read_text(encoding="utf-8"))
    jr["home"] = jr["home"].upper()
    jp.write_text(json.dumps(jr), encoding="utf-8")
    out = fb.recover_interrupted_restore(b)
    assert out and out["rolled"] == "back", out
    assert (b / "core_memory" / "ME.md").exists()