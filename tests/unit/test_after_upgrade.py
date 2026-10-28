"""After an upgrade or a restore (north star §37.3-9, §37.3-10): the indexes
Friday derives from her data are rebuilt (a restore's request is carried
out), never in safe mode, and what could not be sorted out is listed
for the owner, with what to do, until the owner marks it sorted."""
from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _mod(name):
    assert importlib.util.find_spec("agent_friday.services." + name) is not None, \
        "there is no services/%s.py" % name
    return importlib.import_module("agent_friday.services." + name)


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / ".friday"
    h.mkdir()
    monkeypatch.setenv("FRIDAY_HOME", str(h))
    for k in ("FRIDAY_SAFE_START", "FRIDAY_DIAGNOSTIC_START"):
        monkeypatch.delenv(k, raising=False)
    from agent_friday.services import safe_mode
    monkeypatch.setattr(safe_mode, "_failed_starts", lambda: 0)
    safe_mode.reset_for_tests()
    yield h
    safe_mode.reset_for_tests()


@pytest.fixture
def fake_indexes(monkeypatch):
    """Counting stand-ins for the three rebuilds; `fail` names those that raise."""
    calls = {"n": {}, "fail": set()}
    if importlib.util.find_spec("agent_friday.services.derived_rebuild") is None:
        return calls
    drb = importlib.import_module("agent_friday.services.derived_rebuild")

    def make(name):
        def fn():
            calls["n"][name] = calls["n"].get(name, 0) + 1
            if name in calls["fail"]:
                raise OSError("index file held open")
            return {"ok": True}
        return fn
    monkeypatch.setattr(drb, "INDEXES", {k: (v[0], make(k)) for k, v in drb.INDEXES.items()})
    return calls


def _restore_marker(home: Path, indexes) -> Path:
    m = home / "restore" / "needs_reindex.json"
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_text(json.dumps({"at": 1, "indexes": indexes}), encoding="utf-8")
    return m


def test_a_restores_request_to_reindex_is_carried_out(home, fake_indexes):
    drb = _mod("derived_rebuild")
    m = _restore_marker(home, ["knowledge_graph", "conversation_memory", "library"])
    out = drb.run_pending(home)
    assert out["deferred"] is None
    assert fake_indexes["n"] == {"knowledge_graph": 1, "conversation_search": 1, "library": 1}
    assert not m.exists() and drb.state(home)["waiting"] == []
    assert {r["index"] for r in drb.state(home)["results"] if r["ok"]} == set(drb.INDEXES)
    assert drb.run_pending(home)["ran"] == []                        # once


def test_the_bundles_request_names_only_indexes_that_are_rebuilt(home):
    drb = _mod("derived_rebuild")
    from agent_friday.services import friday_bundle
    friday_bundle._mark_reindex(home)
    names = json.loads((home / "restore" / "needs_reindex.json").read_text(encoding="utf-8"))["indexes"]
    assert names and all(drb._name(n) for n in names)


def test_a_library_whose_consent_record_cannot_be_checked_waits_without_using_a_try(home, monkeypatch):
    drb = _mod("derived_rebuild")
    from agent_friday.services.library import grants, runtime
    swept = []
    monkeypatch.setattr(grants, "suspended", lambda: True)
    monkeypatch.setattr(runtime, "resweep", lambda *a, **k: swept.append(1) or 0)
    drb.request(["library"], "a restore", home)
    out = drb.run_pending(home)
    assert swept == [] and out["ran"][0].get("deferred")
    assert drb.state(home)["waiting"] == [{"index": "library", "label": "the Library", "attempts": 0}]
    monkeypatch.setattr(grants, "suspended", lambda: False)
    drb.run_pending(home)
    assert swept == [1] and drb.state(home)["waiting"] == []


def test_an_unknown_or_odd_queue_entry_never_stops_the_others(home, fake_indexes):
    drb = _mod("derived_rebuild")
    p = home / "migrations" / drb.PENDING
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"a_newer_index": {"attempts": 0}, "library": {"attempts": 0}}), encoding="utf-8")
    drb.request(["library"], "a restore", home)                    # a row without its reasons
    drb.run_pending(home)
    assert fake_indexes["n"] == {"library": 1} and json.loads(p.read_text(encoding="utf-8")) == {}
    _restore_marker(home, "library")                                # not a list: rebuild them all
    drb.run_pending(home)
    assert set(fake_indexes["n"]) == set(drb.INDEXES)


def test_safe_mode_keeps_rebuilds_waiting(home, fake_indexes, monkeypatch):
    drb = _mod("derived_rebuild")
    from agent_friday.services import safe_mode
    monkeypatch.setenv("FRIDAY_SAFE_START", "1")
    safe_mode.reset_for_tests()
    m = _restore_marker(home, ["library"])
    out = drb.run_pending(home)
    assert out["ran"] == [] and "safe mode" in out["deferred"] and fake_indexes["n"] == {}
    assert m.exists() and drb.state(home)["restore_waiting"] is True       # nothing written in safe mode


def test_a_rebuild_that_keeps_failing_goes_to_the_owner_and_a_later_success_clears_it(home, fake_indexes):
    drb, ur = _mod("derived_rebuild"), _mod("upgrade_review")
    fake_indexes["fail"].add("knowledge_graph")
    drb.request(["knowledge_graph"], "a restore", home)
    for _ in range(drb.MAX_ATTEMPTS):
        drb.run_pending(home)
    assert fake_indexes["n"]["knowledge_graph"] == drb.MAX_ATTEMPTS
    items = ur.listing(home)
    assert [(i["code"], i["store"]) for i in items] == [("rebuild_failed", "knowledge_graph")]
    assert "OSError" in items[0]["said"] and "Rebuild now" in items[0]["todo"]
    assert "held open" not in items[0]["said"]                      # an error word, never its message
    drb.run_pending(home)
    assert fake_indexes["n"]["knowledge_graph"] == drb.MAX_ATTEMPTS  # not retried on its own
    fake_indexes["fail"].clear()
    drb.request(["knowledge_graph"], "asked on the Doctor page", home)
    drb.run_pending(home)
    assert ur.listing(home) == []


def test_a_migration_asks_for_the_rebuilds_its_change_needs(home, monkeypatch):
    mg, drb = _mod("migrations"), _mod("derived_rebuild")
    assert "rebuilds" in mg.Migration.__dataclass_fields__, "a migration cannot ask for a rebuild"

    def apply(h):
        (h / "notes.txt").write_text("after", encoding="utf-8")
    monkeypatch.setattr(mg, "MIGRATIONS", [mg.Migration(1, "one", ("notes.txt",), apply, lambda h: True,
                                                        rebuilds=("conversation_search",))])
    assert mg.run_pending(home)["ok"]
    assert [w["index"] for w in drb.state(home)["waiting"]] == ["conversation_search"]


def test_a_failed_migration_is_listed_for_the_owner_and_cleared_when_it_runs(home, monkeypatch):
    mg, ur = _mod("migrations"), _mod("upgrade_review")
    from agent_friday.services import safe_mode
    monkeypatch.setattr(safe_mode, "set_next_start", lambda on, reason=None: {})
    (home / "notes.txt").write_text("before", encoding="utf-8")
    state = {"fail": True}

    def apply(h):
        if state["fail"]:
            raise OSError("disk went away")
        (h / "notes.txt").write_text("after", encoding="utf-8")
    monkeypatch.setattr(mg, "MIGRATIONS", [mg.Migration(1, "one", ("notes.txt",), apply, lambda h: True)])
    assert mg.run_pending(home)["safe_mode"] is True
    items = ur.listing(home)
    assert [(i["code"], i["store"]) for i in items] == [("migration_failed", "migration-1")]
    assert "safe mode" in items[0]["said"] and items[0]["todo"]
    state["fail"] = False
    assert mg.run_pending(home)["ok"] and ur.listing(home) == []


def test_an_upgrade_that_finds_a_link_in_the_way_says_so(home, monkeypatch):
    mg, ur = _mod("migrations"), _mod("upgrade_review")
    monkeypatch.setattr(mg, "_links_in", lambda h, t: ["notes.txt"])
    monkeypatch.setattr(mg, "MIGRATIONS", [mg.Migration(1, "one", ("notes.txt",), lambda h: None, lambda h: True)])
    assert mg.run_pending(home)["safe_mode"] is True
    assert [i["code"] for i in ur.listing(home)] == ["migration_link"]


def test_the_same_problem_again_is_one_item_and_a_sorted_one_can_come_back(home):
    ur = _mod("upgrade_review")
    a = ur.add("rebuild_failed", "library", "first", "do x", home=home)
    b = ur.add("rebuild_failed", "library", "second", "do x", home=home)
    assert a["id"] == b["id"] and b["count"] == 2 and len(ur.listing(home)) == 1
    assert ur.resolve(a["id"], home)["resolved_at"] and ur.listing(home) == []
    assert ur.resolve(a["id"], home) is None                         # already sorted
    ur.add("rebuild_failed", "library", "third", home=home)
    assert len(ur.listing(home)) == 1
    odd = ur.add("../../etc", "C:\\Users\\x", "s", home=home)
    assert odd["code"] == "unknown" and odd["store"] == "unknown"


def test_the_doctor_report_counts_them_and_the_weekly_email_leaves_them_out(home):
    ur = _mod("upgrade_review")
    _mod("derived_rebuild")
    ur.add("rebuild_failed", "library", "Friday could not rebuild the Library private-words-xyz.", home=home)
    from agent_friday.services import doctor, doctor_weekly
    rep = doctor.build()
    assert "after_upgrade" in rep, "the Doctor report does not say what an upgrade left open"
    assert rep["after_upgrade"]["unresolved"]["open"] == 1
    assert "private-words-xyz" not in json.dumps(rep)               # counts only
    assert "After upgrades and restores: 1 open item(s)" in doctor.render_text(rep)
    assert "After upgrades" not in doctor_weekly._render(rep, False)


def test_rebuilds_start_after_boot_and_the_doctor_page_lists_them():
    _mod("derived_rebuild")
    src = (ROOT / "src" / "agent_friday" / "server.py").read_text(encoding="utf-8")
    assert "derived_rebuild" in src and "start_after_boot()" in src
    assert src.index("= _acquire_single_instance_lock()") < src.index("start_after_boot()")
    html = (ROOT / "static" / "doctor.html").read_text(encoding="utf-8")
    assert 'id="afterupgrade"' in html and "/api/doctor/after-upgrade" in html and "Mark as sorted" in html


def test_start_after_boot_waits_then_runs(home, fake_indexes, monkeypatch):
    drb = _mod("derived_rebuild")
    import threading
    slept, done = [], threading.Event()
    real = drb.run_pending

    def run(*a, **k):
        try:
            return real(*a, **k)
        finally:
            done.set()
    monkeypatch.setattr(drb, "run_pending", run)
    drb.request(["library"], "a restore", home)
    drb.start_after_boot(delay_s=5, sleep=slept.append)
    assert done.wait(30) and slept == [5] and fake_indexes["n"] == {"library": 1}
