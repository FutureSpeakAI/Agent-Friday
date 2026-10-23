"""Scheduled Friday Bundles and the restore rehearsal (north star §27.4,
services/backup_schedule.py).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from agent_friday.governance import proof_of_integrity as poi

PASS = "orbit lantern pepper tundra violet 42"   # pragma: allowlist secret


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / ".friday"
    (h / "core_memory").mkdir(parents=True)
    (h / "core_memory" / "ME.md").write_text("- likes long walks\n", encoding="utf-8")
    (h / "settings.json").write_text(json.dumps({"agent_name": "Friday"}), encoding="utf-8")
    monkeypatch.setenv("FRIDAY_HOME", str(h))
    monkeypatch.setenv("FRIDAY_LEDGER_DIR", str(h / "ledger"))
    monkeypatch.setenv("FRIDAY_VAULT_PASSPHRASE", "vault passphrase for the test")  # pragma: allowlist secret
    monkeypatch.setitem(sys.modules, "keyring", None)
    monkeypatch.setattr(poi, "_GOV_KEY_FILE", h / "vault" / ".governance-key")
    from agent_friday.services import action_ledger, backup_schedule
    action_ledger.reset_caches()
    backup_schedule._TEST_SECRETS.clear()
    action_ledger.append("decision", {"tool": "probe", "decision": "allow"}, durable=True)
    return h


def _fast():
    from agent_friday.privacy import vault_crypto as vc
    return vc.FAST_PROFILE


def _on(bs, dest, **kw):
    return bs.set_schedule(enabled=True, every=kw.pop("every", "daily"), folder=str(dest),
                           keep=kw.pop("keep", 2), passphrase=kw.pop("passphrase", PASS), **kw)


def test_off_until_the_owner_turns_it_on(home):
    from agent_friday.services import backup_schedule as bs
    st = bs.public_state()
    assert st["enabled"] is False and st["passphrase_kept"] is False
    assert not bs.due() and bs.tick() is None


def test_turning_it_on_needs_a_folder_outside_friday_and_a_strong_passphrase(home, tmp_path):
    from agent_friday.services import backup_schedule as bs
    with pytest.raises(bs.ScheduleError):
        _on(bs, home / "inside")
    with pytest.raises(bs.ScheduleError):
        _on(bs, "relative/folder")
    with pytest.raises(bs.ScheduleError):
        _on(bs, tmp_path / "out", passphrase="password1234")
    with pytest.raises(bs.ScheduleError):
        _on(bs, tmp_path / "out", passphrase=None)        # none kept yet
    st = _on(bs, tmp_path / "out")
    assert st["enabled"] and st["passphrase_kept"]
    raw = bs.schedule_path().read_text(encoding="utf-8") if bs.schedule_path().exists() else ""
    assert PASS not in raw, "the passphrase is never in the schedule file"
    assert str(tmp_path / "out") not in raw, "nor the folder: it is sealed with the passphrase"


def test_a_due_bundle_is_made_verified_rotated_and_on_record(home, tmp_path, monkeypatch):
    from agent_friday.services import action_ledger as L
    from agent_friday.services import backup_schedule as bs
    from agent_friday.services import friday_bundle as fb
    real = fb.create
    monkeypatch.setattr(fb, "create", lambda out, pw, **k: real(out, pw, **dict(k, kdf_profile=_fast())))
    dest = tmp_path / "out"
    _on(bs, dest, keep=2)
    assert bs.due()
    owner_file = dest / "friday-scheduled-mine.fridaybundle"
    names = []
    for i in range(3):
        r = bs.run_now()
        names.append(r["file"])
        time.sleep(1.1)
    owner_file.write_bytes(b"the owner's own file")
    files = sorted(p.name for p in dest.glob("*.fridaybundle"))
    assert names[0] not in files and names[1] in files and names[2] in files
    assert owner_file.name in files, "a file the schedule did not make is never removed"
    assert fb.verify(dest / names[2], PASS)["ok"]
    st = bs.public_state()
    assert st["last_ok"] and not st["last_error"] and not bs.due()
    kinds = [e["rec"].get("event") for e in L.entries(["backup"], newest_first=False)]
    assert kinds.count("scheduled_bundle") == 3 and "schedule_on" in kinds
    assert not any(str(dest) in json.dumps(e["rec"]) for e in L.entries(["backup"]))
    doc = json.loads((home / "backups" / "last_bundle.json").read_text(encoding="utf-8"))
    assert doc["verified"] is True


def test_a_rotated_file_that_was_replaced_is_left_alone(home, tmp_path, monkeypatch):
    from agent_friday.services import backup_schedule as bs
    from agent_friday.services import friday_bundle as fb
    real = fb.create
    monkeypatch.setattr(fb, "create", lambda out, pw, **k: real(out, pw, **dict(k, kdf_profile=_fast())))
    dest = tmp_path / "out"
    _on(bs, dest, keep=1)
    first = bs.run_now()["file"]
    (dest / first).write_bytes(b"something else now")
    time.sleep(1.1)
    bs.run_now()
    assert (dest / first).exists()


def test_a_failed_run_is_said_and_tried_again_later(home, tmp_path, monkeypatch):
    from agent_friday.services import backup_schedule as bs
    from agent_friday.services import friday_bundle as fb
    _on(bs, tmp_path / "out")
    monkeypatch.setattr(fb, "create", lambda *a, **k: (_ for _ in ()).throw(fb.BundleError("disk full")))
    with pytest.raises(fb.BundleError):
        bs.run_now()
    st = bs.public_state()
    assert st["last_error"] == "disk full"
    assert not bs.due(), "not again at once"
    assert bs.due(now=time.time() + 3700)
    from agent_friday.services import doctor
    assert doctor._c_backup() == ("degraded", "scheduled_bundle_failing")


def test_the_restore_rehearsal_reads_the_newest_bundle_and_writes_nothing(home, tmp_path, monkeypatch):
    from agent_friday.services import backup_schedule as bs
    from agent_friday.services import friday_bundle as fb
    real = fb.create
    monkeypatch.setattr(fb, "create", lambda out, pw, **k: real(out, pw, **dict(k, kdf_profile=_fast())))
    dest = tmp_path / "out"
    _on(bs, dest)
    newest = bs.run_now()["file"]
    before = sorted(str(p.relative_to(home)) for p in home.rglob("*") if p.is_file()
                    and "ledger" not in p.parts and "backups" not in p.parts)
    assert bs.rehearsal_due()
    r = bs.rehearse()
    assert r["ok"] is True, r
    after = sorted(str(p.relative_to(home)) for p in home.rglob("*") if p.is_file()
                   and "ledger" not in p.parts and "backups" not in p.parts)
    assert before == after
    assert not bs.rehearsal_due()
    # a damaged bundle fails its rehearsal, and the Doctor says so
    b = bytearray((dest / newest).read_bytes())
    b[-10] ^= 1
    (dest / newest).write_bytes(bytes(b))
    r = bs.rehearse()
    assert r["ok"] is False
    from agent_friday.services import doctor
    assert doctor._c_backup() == ("degraded", "restore_rehearsal_failed")


def test_turning_it_off_removes_the_kept_passphrase(home, tmp_path):
    from agent_friday.services import backup_schedule as bs
    _on(bs, tmp_path / "out")
    st = bs.set_schedule(enabled=False)
    assert st["enabled"] is False and st["passphrase_kept"] is False


def test_the_doctor_view_names_no_folder_or_file(home, tmp_path):
    from agent_friday.services import backup_schedule as bs
    _on(bs, tmp_path / "out")
    assert str(tmp_path) not in json.dumps(bs.doctor_view())



def test_files_planted_in_the_data_folder_never_turn_a_schedule_on_or_redirect_it(home, tmp_path, monkeypatch):
    """A planted schedule.json and a plain passphrase file never make Friday
    write the keychain's secrets into an attacker's bundle."""
    from agent_friday.services import backup_schedule as bs
    monkeypatch.setenv("FRIDAY_SCHEDULE_SECRET", "keyring")     # the real stores, not the test memory
    evil = tmp_path / "attacker-share"
    bs.schedule_path().parent.mkdir(parents=True, exist_ok=True)
    bs.schedule_path().write_text(json.dumps({"enabled": True, "folder": str(evil), "keep": 1,
                                              "every": "daily"}), encoding="utf-8")
    f = home / "vault" / ".bundle-schedule-passphrase"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(PASS, encoding="utf-8")                       # plain text the attacker knows
    st = bs.state()
    assert st["enabled"] is False and st["folder"] is None
    assert not bs.due() and bs.tick() is None
    assert not evil.exists()


def test_a_redirected_folder_in_the_state_file_is_never_used(home, tmp_path, monkeypatch):
    from agent_friday.services import backup_schedule as bs
    from agent_friday.services import friday_bundle as fb
    real = fb.create
    monkeypatch.setattr(fb, "create", lambda out, pw, **k: real(out, pw, **dict(k, kdf_profile=_fast())))
    mine, evil = tmp_path / "mine", tmp_path / "attacker"
    _on(bs, mine)
    d = json.loads(bs.schedule_path().read_text(encoding="utf-8")) if bs.schedule_path().exists() else {}
    d.update(folder=str(evil), keep=1, made=[{"file": "..\\..\\x.fridaybundle", "bundle_id": "b"},
                                           {"file": str(tmp_path / "victim.txt"), "bundle_id": "b"}, "x"])
    bs.schedule_path().parent.mkdir(parents=True, exist_ok=True)
    bs.schedule_path().write_text(json.dumps(d), encoding="utf-8")
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me", encoding="utf-8")
    r = bs.run_now()
    assert (mine / r["file"]).exists() and not evil.exists()
    assert victim.read_text(encoding="utf-8") == "keep me"
    assert bs.public_state()["keep"] == 2


def test_an_unverified_bundle_is_not_left_behind_and_failures_back_off(home, tmp_path, monkeypatch):
    from agent_friday.services import backup_schedule as bs
    from agent_friday.services import friday_bundle as fb
    real = fb.create
    monkeypatch.setattr(fb, "create", lambda out, pw, **k: real(out, pw, **dict(k, kdf_profile=_fast())))
    monkeypatch.setattr(fb, "verify", lambda *a, **k: {"ok": False})
    dest = tmp_path / "out"
    _on(bs, dest)
    for _ in range(3):
        with pytest.raises(fb.BundleError):
            bs.run_now()
    assert not list(dest.glob("*.fridaybundle"))
    st = bs.state()
    assert st["failures"] == 3
    assert bs.next_due(st) >= st["last_run"] + 4 * 3600 - 1


def test_turning_it_off_removes_the_record_from_every_store(home, tmp_path, monkeypatch):
    from agent_friday.services import backup_schedule as bs
    f = home / "vault" / ".bundle-schedule-passphrase"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"left over from a time without a keychain")
    _on(bs, tmp_path / "out")
    bs.set_schedule(enabled=False)
    assert not f.exists() and bs.public_state()["passphrase_kept"] is False


def test_the_sealed_record_never_travels_in_a_bundle():
    from pathlib import PurePosixPath
    from agent_friday.services import friday_bundle as fb
    assert fb.category_of(PurePosixPath("vault/.bundle-schedule-passphrase")) is None



def _with_fast(monkeypatch):
    from agent_friday.services import friday_bundle as fb
    real = fb.create
    monkeypatch.setattr(fb, "create", lambda out, pw, **k: real(out, pw, **dict(k, kdf_profile=_fast())))


def test_a_planted_list_cannot_make_rotation_delete_the_owners_bundles(home, tmp_path, monkeypatch):
    """Neither untagged entries nor the real ones reordered make rotation delete bundles."""
    from agent_friday.services import backup_schedule as bs
    _with_fast(monkeypatch)
    dest = tmp_path / "out"
    _on(bs, dest, keep=3)
    made = []
    for _ in range(3):
        made.append(bs.run_now()["file"])
        time.sleep(1.1)
    d = json.loads(bs.schedule_path().read_text(encoding="utf-8"))
    real = d["made"]
    fake = [{"file": "friday-scheduled-20990101-00000%dZ.fridaybundle" % i, "bundle_id": "x%d" % i,
             "at": time.time() - 10, "verified": True} for i in range(5)]
    d["made"] = list(reversed(real)) + fake          # newest first, then entries with no tag
    bs.schedule_path().write_text(json.dumps(d), encoding="utf-8")
    bs.run_now()
    left = {p.name for p in dest.glob("*.fridaybundle")}
    assert made[1] in left and made[2] in left, "the newest real bundles were deleted"
    assert made[0] not in left, "rotation still removes the oldest real one"


def test_a_time_in_the_future_does_not_stop_the_schedule(home, tmp_path, monkeypatch):
    from agent_friday.services import backup_schedule as bs
    _with_fast(monkeypatch)
    _on(bs, tmp_path / "out")
    bs.schedule_path().parent.mkdir(parents=True, exist_ok=True)
    bs.schedule_path().write_text(json.dumps({"last_run": 9e10, "last_ok": 9e10,
                                              "last_rehearsal": 9e10}), encoding="utf-8")
    assert bs.due()
    st = bs.state()
    assert st["last_ok"] is None and st["last_rehearsal"] is None


def test_the_doctor_says_when_scheduled_bundles_are_overdue(home, tmp_path, monkeypatch):
    from agent_friday.services import backup_schedule as bs
    from agent_friday.services import doctor
    _with_fast(monkeypatch)
    _on(bs, tmp_path / "out", every="daily")
    bs.run_now()
    d = json.loads(bs.schedule_path().read_text(encoding="utf-8"))
    d["last_ok"] = d["last_run"] = time.time() - 4 * 86400
    bs.schedule_path().write_text(json.dumps(d), encoding="utf-8")
    assert doctor._c_backup() == ("degraded", "scheduled_bundle_overdue")


def test_without_a_keychain_a_schedule_cannot_be_turned_on(home, tmp_path, monkeypatch):
    """On a system with no keychain nothing in the data
    folder stands in for it."""
    from agent_friday.services import backup_schedule as bs
    monkeypatch.setenv("FRIDAY_SCHEDULE_SECRET", "keyring")      # sys.modules["keyring"] is None here
    with pytest.raises(bs.ScheduleError) as e:
        _on(bs, tmp_path / "out")
    assert "credential store" in str(e.value)
    assert not (home / "vault" / ".bundle-schedule-passphrase").exists()
    assert bs.state()["enabled"] is False



def test_a_real_entry_said_again_cannot_push_other_bundles_out(home, tmp_path, monkeypatch):
    """Duplicated tagged entries never make rotation delete real bundles."""
    from agent_friday.services import backup_schedule as bs
    _with_fast(monkeypatch)
    dest = tmp_path / "out"
    _on(bs, dest, keep=3)
    made = []
    for _ in range(3):
        made.append(bs.run_now()["file"])
        time.sleep(1.1)
    d = json.loads(bs.schedule_path().read_text(encoding="utf-8"))
    d["made"] = [d["made"][0]] * 10 + d["made"][1:]
    bs.schedule_path().write_text(json.dumps(d), encoding="utf-8")
    bs.run_now()
    left = {p.name for p in dest.glob("*.fridaybundle")}
    assert made[1] in left and made[2] in left


def test_a_record_from_before_tags_gets_a_key_and_rotates(home, tmp_path, monkeypatch):
    from agent_friday.services import backup_schedule as bs
    _with_fast(monkeypatch)
    dest = tmp_path / "out"
    _on(bs, dest, keep=1)
    rec = bs._load()
    rec.pop("made_key")
    bs._store(rec)
    first = bs.run_now()["file"]
    time.sleep(1.1)
    bs.run_now()
    assert not (dest / first).exists()


def test_a_record_too_big_for_the_credential_store_is_refused_plainly(home, tmp_path):
    from agent_friday.services import backup_schedule as bs
    deep = tmp_path / ("x" * 200) / ("y" * 200) / ("z" * 200) / ("w" * 200) / ("v" * 200)
    try:
        deep.mkdir(parents=True)
    except OSError:
        pytest.skip("this file system will not hold the path")
    with pytest.raises(bs.ScheduleError) as e:
        bs.set_schedule(enabled=True, every="daily", folder=str(deep),
                        passphrase="orbit lantern pepper tundra violet 42 " + "ж" * 200)
    assert "too long" in str(e.value)


def test_the_rehearsal_checks_the_newest_bundle_this_schedule_made(home, tmp_path, monkeypatch):
    """A reordered list never points the rehearsal at an older bundle."""
    from agent_friday.services import backup_schedule as bs
    _with_fast(monkeypatch)
    dest = tmp_path / "out"
    _on(bs, dest, keep=3)
    bs.run_now()
    time.sleep(1.1)
    newest = bs.run_now()["file"]
    b = bytearray((dest / newest).read_bytes())
    b[-10] ^= 1
    (dest / newest).write_bytes(bytes(b))
    d = json.loads(bs.schedule_path().read_text(encoding="utf-8"))
    d["made"] = list(reversed(d["made"]))
    bs.schedule_path().write_text(json.dumps(d), encoding="utf-8")
    assert bs.rehearse()["ok"] is False


def test_off_says_so_when_the_credential_store_will_not_let_go(home, tmp_path, monkeypatch):
    """Off reports failure while the record stays."""
    import types
    from agent_friday.services import backup_schedule as bs
    monkeypatch.setenv("FRIDAY_SCHEDULE_SECRET", "keyring")
    kept = {}

    class Locked(Exception):
        pass

    fake = types.SimpleNamespace(
        get_password=lambda s, a: kept.get(a),
        set_password=lambda s, a, v: kept.__setitem__(a, v),
        delete_password=lambda s, a: (_ for _ in ()).throw(Locked("locked")))
    monkeypatch.setattr(bs, "_keyring", lambda: (fake, type("NoKeyringError", (Exception,), {}),
                                                 type("PasswordDeleteError", (Exception,), {})))
    _on(bs, tmp_path / "out")
    with pytest.raises(bs.ScheduleError):
        bs.set_schedule(enabled=False)
    assert bs.state()["enabled"] is True



def test_off_is_read_back_when_the_store_says_nothing_was_there(home, tmp_path, monkeypatch):
    """Some stores report a refused delete as "not found", so off is read back."""
    import types
    from agent_friday.services import backup_schedule as bs
    monkeypatch.setenv("FRIDAY_SCHEDULE_SECRET", "keyring")
    kept = {}
    NotFound = type("PasswordDeleteError", (Exception,), {})
    fake = types.SimpleNamespace(
        get_password=lambda s, a: kept.get(a),
        set_password=lambda s, a, v: kept.__setitem__(a, v),
        delete_password=lambda s, a: (_ for _ in ()).throw(NotFound("refused")))
    monkeypatch.setattr(bs, "_keyring", lambda: (fake, type("NoKeyringError", (Exception,), {}), NotFound))
    _on(bs, tmp_path / "out")
    with pytest.raises(bs.ScheduleError):
        bs.set_schedule(enabled=False)
    assert bs.state()["enabled"] is True


def test_a_due_bundle_waits_while_everything_is_stopped_or_authority_is_revoked(home, tmp_path, monkeypatch):
    from agent_friday.services import authority, global_stop
    from agent_friday.services import backup_schedule as bs
    from agent_friday.services import friday_bundle as fb
    made = []
    monkeypatch.setattr(fb, "create", lambda *a, **k: made.append(a) or {})
    _on(bs, tmp_path / "out")
    assert bs.due()
    monkeypatch.setattr(global_stop, "active", lambda: True)
    assert bs.tick() == {"held": "everything is stopped"} and made == []
    monkeypatch.setattr(global_stop, "active", lambda: False)
    monkeypatch.setattr(authority, "hold_reason", lambda: "you revoked all action authority (test)")
    assert "revoked" in bs.tick()["held"] and made == []

