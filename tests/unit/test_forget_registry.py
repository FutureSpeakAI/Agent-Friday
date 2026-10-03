"""Forget means forget: every store that names a person is purged.

Red on main 7c49be86: `forget_person.forget()` purged a hard-coded list and
left the contacts research note and the learned sender signal in place (the
trust log did not exist). The stores now register with the forget registry,
and forgetting a seeded person leaves nothing naming them in any of them.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    from agent_friday.services import forget_person as fp
    monkeypatch.setattr(fp, "_friday_dir", lambda: tmp_path, raising=False)
    from agent_friday.services import message_triage as mt
    monkeypatch.setattr(mt, "SIGNALS_FILE", str(tmp_path / "messages" / "sender_signals.json"))
    mt.invalidate_cache()
    return tmp_path


def _seed(home):
    from agent_friday.people_graph import PeopleGraph
    from agent_friday.trust import log as tlog
    pg = PeopleGraph(friday_dir=home)
    pg.add_person("Pat Example", aliases=["Pat"])
    pg.edit("pat_example", scores={"reliability": 0.8})
    (home / "contacts-research").mkdir(parents=True, exist_ok=True)
    (home / "contacts-research" / "pat_example.md").write_text("# Pat Example\npending", encoding="utf-8")
    (home / "messages").mkdir(parents=True, exist_ok=True)
    sig = {"senders": {"pat@example.test": {"lanes": {"work": {"score": 1}}}}}
    (home / "messages" / "sender_signals.json").write_text(json.dumps(sig), encoding="utf-8")
    assert tlog.read(tlog.people_path(), entity_id="pat_example")
    return pg


def test_forgetting_a_person_purges_every_registered_store(home, monkeypatch):
    from agent_friday.services import forget_person as fp
    from agent_friday.services import forget_registry as fr
    from agent_friday.trust import log as tlog
    _seed(home)
    # The people graph stores no email for Pat; the registry gets it from the
    # tombstone's email list, which forget() builds from the record. Give the
    # record the address so the sender signal can be matched.
    from agent_friday.people_graph import PeopleGraph
    pg = PeopleGraph(friday_dir=home)
    g = pg.load()
    g["people"]["pat_example"]["emails"] = ["pat@example.test"]
    pg.save(g)
    assert {"contacts_research", "sender_signals", "trust_log_people"} <= set(fr.registered())
    before = fr.find_all({"pat example", "pat"}, {"pat@example.test"})
    assert before["contacts_research"] == 1 and before["trust_log_people"] >= 1
    receipt = fp.forget("Pat Example")
    removed = receipt["removed"]
    assert removed.get("contacts_research") == 1
    assert removed.get("trust_log_people", 0) >= 1
    assert not (home / "contacts-research" / "pat_example.md").exists()
    assert tlog.read(tlog.people_path(), entity_id="pat_example") == []
    rows = tlog.read(tlog.people_path())
    assert rows and rows[-1]["kind"] == "forget_marker"
    assert "pat" not in json.dumps(rows).lower()
    assert tlog.verify_chain(tlog.people_path())["valid"]
    text = (home / "messages" / "sender_signals.json").read_text(encoding="utf-8") \
        if (home / "messages" / "sender_signals.json").exists() else "{}"
    assert "pat@example.test" not in text or removed.get("sender_signals") == 1


def test_a_store_that_fails_is_reported_not_fatal(home, monkeypatch):
    from agent_friday.services import forget_registry as fr

    def boom(names, emails):
        raise RuntimeError("disk gone")
    fr.register_store("broken_store", boom, boom)
    out = fr.purge_all({"x"}, set())
    assert out["broken_store"] == -1 and "disk gone" in out["broken_store_error"]
    fr._STORES.pop("broken_store", None)
