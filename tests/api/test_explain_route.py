"""A13: GET /api/ledger/explain, why Friday did it, is the owner's: the
session gets every section, the observer's credential is refused, an id that
is not one action id or one task id is refused, and a second explanation
while one runs is told busy. Synthetic records only."""
from __future__ import annotations

import json
import threading

from agent_friday.governance import action_gate
from agent_friday.services import explain as E
from agent_friday.services import observer_access as obs
from agent_friday.services import unified_ledger as U

WORDS = "SYNTHETIC-A13-ROUTE-MARKER"


def _seed() -> str:
    U.reset_cache()
    dec = action_gate._receipt({"tool": "probe_a13_route", "class": "outward", "decision": "allow",
                                "surface": "test", "target": WORDS})
    return dec["rec"]["action_id"]


def test_the_owner_gets_every_section_with_ids_and_words_only(client):
    aid = _seed()
    r = client.get("/api/ledger/explain?action_id=" + aid)
    assert r.status_code == 200, r.get_data(as_text=True)
    d = r.get_json()
    assert d["status"] == "ok" and d["found"] is True and d["subject"] == {"action_id": aid}
    assert d["order"] == list(E.SECTION_NAMES) and set(d["sections"]) == set(E.SECTION_NAMES)
    assert d["sections"]["what"]["action"]["tool"] == "probe_a13_route"
    assert d["sections"]["egress"]["status"] == "not_recorded"
    assert E.NOT_RECORDED in d["sections"]["cost"]["said"]
    assert d["text"].startswith("Action " + aid)
    assert WORDS not in json.dumps(d), "ids and fixed words only"


def test_the_observer_cannot_ask_why(client):
    aid = _seed()
    tok = client.post("/api/tasks/observer-token").get_json()["token"]
    r = client.get("/api/ledger/explain?action_id=" + aid, headers={obs.HEADER: tok})
    assert r.status_code == 403, r.status_code
    r = client.get("/api/ledger/explain?task_id=a13-route-task", headers={obs.HEADER: tok})
    assert r.status_code == 403


def test_an_id_that_is_not_one_action_or_task_id_is_refused(client):
    for q in ("", "?action_id=..%5C..%5Cx", "?task_id=a%2Fb", "?action_id=act_a13_x&task_id=t_a13_x",
              "?action_id=" + "a" * 200):
        r = client.get("/api/ledger/explain" + q)
        assert r.status_code == 400, (q, r.status_code)
        assert r.get_json()["code"] == "bad_id"


def test_a_second_explanation_while_one_runs_is_told_busy(client, monkeypatch):
    held = threading.Lock()
    held.acquire()                     # another explanation holds it
    monkeypatch.setattr(E, "_RUN_LOCK", held)
    monkeypatch.setattr(E, "BUSY_WAIT_S", 0.01)
    try:
        r = client.get("/api/ledger/explain?action_id=act_00000000000000000000")
        assert r.status_code == 429 and r.get_json()["code"] == "busy"
        assert r.headers.get("Retry-After") == "2"
    finally:
        held.release()
