"""The Doctor offers repairs when the local model is down (north star §32.2-4),
and they need no model (§28.1-1): restart the seats through the arbiter's own
restore, check the model files by checksum, reinstall a broken runtime. Each
is a press, one at a time, with a receipt; safe mode starts and downloads
nothing. Fakes stand in for the arbiter, the store and the runtime: no model
is loaded and nothing is downloaded."""
from __future__ import annotations

import importlib
import importlib.util
import re

import pytest


def _lr():
    assert importlib.util.find_spec("agent_friday.services.local_repair") is not None, \
        "the Doctor offers no repair for the local model"
    return importlib.import_module("agent_friday.services.local_repair")


class FakeArbiter:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or {"ok": True, "restored": ["brain-model"], "missing": []}

    def restore_unheld_seats(self, reason=None):
        self.calls.append(reason)
        return self.result


@pytest.fixture
def world(monkeypatch):
    """A local model installed and not serving; an arbiter; two registered
    model files, one changed on disk; a sound runtime; normal start."""
    from agent_friday.services import (action_ledger, capability_state as cs, model_download,
                                       model_store, residency_arbiter, safe_mode)
    w = {"brain": cs.PRESENT_FAILING, "arb": FakeArbiter(), "safe": False, "runtime": None,
         "receipts": [], "reinstalled": 0}
    monkeypatch.setattr(cs, "local_brain_state", lambda: cs.CapabilityState(
        "local_brain", "Local model (brain seat)", w["brain"], (), "", "brain-model"))
    monkeypatch.setattr(residency_arbiter, "get_arbiter", lambda: w["arb"])
    monkeypatch.setattr(safe_mode, "active", lambda *a, **k: w["safe"])
    monkeypatch.setattr(model_store, "all_models", lambda: {"brain-model": {}, "voice-model": {}})
    monkeypatch.setattr(model_store, "verify", lambda mid: {"ok": True, "checked": "sha256"}
                        if mid == "brain-model" else {"ok": False, "error": "checksum mismatch"})
    monkeypatch.setattr(model_download, "installed_runtime_problem", lambda *a, **k: w["runtime"])

    def reinstall():
        w["reinstalled"] += 1
        w["runtime"] = None
    monkeypatch.setattr(model_download, "repair_runtime", reinstall)
    monkeypatch.setattr(action_ledger, "try_append", lambda kind, rec, **k: w["receipts"].append((kind, rec)))
    if importlib.util.find_spec("agent_friday.services.local_repair") is not None:
        lr = importlib.import_module("agent_friday.services.local_repair")
        lr._JOB.clear()
        lr._JOB.update(state="idle")
    return w


def test_a_model_that_is_not_serving_gets_its_repairs_offered(world):
    st = _lr().state()
    offers = {o["id"]: o for o in st["offers"]}
    assert st["brain"]["down"] is True
    assert offers["restart"]["available"] and offers["restart"]["recommended"]
    assert offers["verify"]["available"]
    assert "runtime" not in offers                     # a sound runtime is not offered for reinstalling


def test_restart_goes_through_the_arbiters_own_restore_with_a_receipt(world):
    job = _lr().start("restart", wait=True)
    assert world["arb"].calls == ["doctor_repair"]
    assert job["state"] == "done" and job["result"]["ok"] and job["result"]["restored"] == ["brain-model"]
    events = [(k, r["event"], r["repair"]) for k, r in world["receipts"]]
    assert events == [("doctor_repair", "started", "restart"), ("doctor_repair", "finished", "restart")]


def test_the_check_names_a_changed_model_file_and_where_to_get_it_again(world):
    job = _lr().start("verify", wait=True)
    rows = {r["model"]: r for r in job["result"]["models"]}
    assert rows["brain-model"]["ok"] and not rows["voice-model"]["ok"]
    assert rows["voice-model"]["problem"] == "checksum"
    assert job["result"]["ok"] is False and "Settings > Models" in job["result"]["next"]


def test_a_broken_runtime_is_offered_and_reinstalled(world):
    world["runtime"] = "the local model runtime has no server program"
    offers = {o["id"]: o for o in _lr().state()["offers"]}
    assert offers["runtime"]["available"] and "no server program" in offers["runtime"]["problem"]
    job = _lr().start("runtime", wait=True)
    assert world["reinstalled"] == 1 and job["result"]["ok"] is True


def test_safe_mode_starts_and_downloads_nothing(world):
    lr = _lr()
    world["safe"] = True
    world["runtime"] = "broken"
    offers = {o["id"]: o for o in lr.state()["offers"]}
    assert not offers["restart"]["available"] and "Safe mode" in offers["restart"]["why"]
    assert not offers["runtime"]["available"]
    for what in ("restart", "runtime"):
        with pytest.raises(lr.RepairError):
            lr.start(what, wait=True)
    assert world["arb"].calls == [] and world["reinstalled"] == 0
    assert lr.start("verify", wait=True)["state"] == "done"     # reading files is still allowed


def test_one_repair_at_a_time(world):
    lr = _lr()
    assert lr._LOCK.acquire(blocking=False)
    try:
        with pytest.raises(lr.RepairError, match="running already"):
            lr.start("verify")
    finally:
        lr._LOCK.release()


def test_no_arbiter_means_no_restart_offered(world):
    world["arb"] = None
    offers = {o["id"]: o for o in _lr().state()["offers"]}
    assert not offers["restart"]["available"]


def test_the_doctor_names_a_local_model_that_is_not_serving(world):
    from agent_friday.services import capability_state as cs, doctor
    checks = {c["id"]: c for c in doctor.build()["checks"]}
    assert "local_brain" in checks, "the Doctor does not check that the local model is serving"
    assert checks["local_brain"]["state"] == "broken"
    world["brain"] = cs.WORKING
    assert {c["id"]: c for c in doctor.build()["checks"]}["local_brain"]["state"] == "healthy"
    world["brain"] = cs.PRESENT_FAILING
    world["safe"] = True
    assert {c["id"]: c for c in doctor.build()["checks"]}["local_brain"]["state"] == "blocked_by_policy"


def test_no_repair_asks_a_model():
    lr = _lr()
    src = open(lr.__file__, encoding="utf-8").read()
    imported = set(re.findall(r"from agent_friday\.services import ([a-z_]+)", src)) | \
        set(re.findall(r"import agent_friday\.services\.([a-z_]+)", src))
    assert imported <= {"safe_mode", "capability_state", "residency_arbiter", "model_download",
                        "model_store", "action_ledger"}, imported
