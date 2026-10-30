"""A12: GET /api/ledger/unified, every receipt family together, is the
owner's: the session serves it, the observer's credential is refused, and a
page link the list did not give out is refused without reading anything.
Synthetic records only."""
from __future__ import annotations

import json

from agent_friday.governance import action_gate
from agent_friday.services import observer_access as obs

WORDS = "SYNTHETIC-A12-ROUTE-MARKER"


def _seed():
    from agent_friday.services import activity_ledger
    action_gate._receipt({"tool": "probe_a12_route", "class": "outward", "decision": "allow",
                          "surface": "test", "target": WORDS})
    activity_ledger.record("tool_call", tool="probe_a12_route_tool", ok=True, duration_ms=1)


def test_the_owner_reads_every_family_together(client):
    _seed()
    r = client.get("/api/ledger/unified?limit=50")
    assert r.status_code == 200, r.get_data(as_text=True)
    d = r.get_json()
    fams = {row["family"] for row in d["rows"]}
    assert {"decision", "tool"} <= fams
    assert "action_ledger" in d["sources"] and d["families"]["decision"]
    assert WORDS not in json.dumps(d), "ids and fixed words only"
    dec = next(row for row in d["rows"] if row["family"] == "decision")
    aid = dec["ids"]["action_id"]
    t = client.get("/api/ledger/unified?id=" + aid).get_json()
    assert t["traced"] is True and any(row["ids"].get("action_id") == aid for row in t["rows"])
    only = client.get("/api/ledger/unified?family=tool").get_json()
    assert only["rows"] and {row["family"] for row in only["rows"]} == {"tool"}


def test_the_observer_cannot_read_everything_together(client):
    tok = client.post("/api/tasks/observer-token").get_json()["token"]
    r = client.get("/api/ledger/unified", headers={obs.HEADER: tok})
    assert r.status_code == 403, r.status_code
    r = client.get("/api/ledger/unified?id=act_00000000000000000000", headers={obs.HEADER: tok})
    assert r.status_code == 403


def test_a_page_link_the_list_did_not_give_out_is_refused(client):
    r = client.get("/api/ledger/unified?cursor=not-a-cursor!")
    assert r.status_code == 400 and r.get_json()["code"] == "bad_cursor"
