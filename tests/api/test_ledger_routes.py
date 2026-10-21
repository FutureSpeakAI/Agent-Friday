"""Activity reads the one action ledger.

Red on the old code by assertion: /api/ledger did not exist (404), activity
rows carried no place in any chain, and a break could not be reviewed or
acknowledged from the page.
"""
from __future__ import annotations

import json

from agent_friday.governance import action_gate
from agent_friday.services import observer_access as obs


def _press(client):
    return {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost",
            "X-Friday-Token": __import__("agent_friday.core", fromlist=["x"])._current_api_token()}


def _outward():
    return action_gate.authorize("create_calendar_event", {"title": "x"},
                                 {"is_background_task": True})


def test_activity_shows_receipts_and_activity_from_one_ledger(client):
    from agent_friday.services import activity_ledger
    action_gate._receipt({"tool": "probe_route_send", "class": "outward", "decision": "allow",
                          "surface": "test"})
    activity_ledger.record("tool_call", tool="probe_route_tool", ok=True, duration_ms=3)
    r = client.get("/api/ledger?limit=50")
    assert r.status_code == 200, r.get_data(as_text=True)
    d = r.get_json()
    kinds = {e["kind"] for e in d["entries"]}
    assert {"decision", "activity"} <= kinds
    dec = next(e for e in d["entries"] if e["kind"] == "decision")
    assert dec["record"]["tool"] == "probe_route_send" and "hmac" not in dec["record"]
    assert dec["label"] == "Checkpoint decision"
    assert d["ledger"]["ok"] is True and d["ledger"]["records"] >= 2
    seqs = [e["seq"] for e in d["entries"]]
    assert seqs == sorted(seqs, reverse=True), "newest first"


def test_activity_rows_carry_their_place_in_the_chain(client):
    from agent_friday.services import activity_ledger
    activity_ledger.record("tool_call", tool="probe_seq", ok=True, duration_ms=1)
    ev = client.get("/api/activity?limit=5").get_json()["events"]
    mine = [e for e in ev if e.get("tool") == "probe_seq"]
    assert mine and isinstance(mine[0].get("seq"), int)


def test_a_break_is_shown_held_and_cleared_only_by_the_owners_press(client):
    action_gate._receipt({"tool": "probe_one", "class": "outward", "decision": "allow"})
    action_gate._receipt({"tool": "probe_two", "class": "outward", "decision": "allow"})
    from agent_friday.services import action_ledger
    p = action_ledger.live_path()
    p.write_text(p.read_text(encoding="utf-8").replace("probe_one", "probe_ONE"), encoding="utf-8")
    action_ledger.reset_caches()

    state = client.get("/api/ledger").get_json()["ledger"]
    assert state["ok"] is False and state["open_problem_count"] >= 1
    rep = client.get("/api/ledger/verify").get_json()["report"]
    assert any("changed" in pr["reason"] for pr in rep["problems"])
    assert "probe_ONE" not in json.dumps(rep), "the report names the line, never its content"
    assert _outward().action == "deny"

    # not without a press on Friday's own page
    r = client.post("/api/ledger/acknowledge", json={})
    assert r.status_code == 403
    assert _outward().action == "deny"

    # the acknowledgement names what the owner reviewed
    r = client.post("/api/ledger/acknowledge", json={}, headers=_press(client))
    assert r.status_code == 400
    shown = [p for p in rep["problems"] if not p["acknowledged"]]
    r = client.post("/api/ledger/acknowledge", json={"reviewed": shown}, headers=_press(client))
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["acknowledged"] >= 1
    assert _outward().action != "deny"

    # a new change holds again, and an acknowledgement of the old review is refused
    p.write_text(p.read_text(encoding="utf-8").replace("probe_two", "probe_TWO"), encoding="utf-8")
    action_ledger.reset_caches()
    assert _outward().action == "deny"
    r = client.post("/api/ledger/acknowledge", json={"reviewed": shown}, headers=_press(client))
    assert r.status_code == 409
    assert _outward().action == "deny"


def test_the_observer_cannot_read_the_ledger(client):
    tok = client.post("/api/tasks/observer-token").get_json()["token"]
    for path in ("/api/ledger", "/api/ledger/verify"):
        r = client.get(path, headers={obs.HEADER: tok})
        assert r.status_code == 403, (path, r.status_code)
    r = client.post("/api/ledger/acknowledge", headers={obs.HEADER: tok}, json={})
    assert r.status_code == 403
