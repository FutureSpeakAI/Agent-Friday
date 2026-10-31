"""The Ledger workspace: a readable receipt viewer with filters (north star
§18.10, NS-18.10-1/-3), export, and the retention setting of amendment A22.

/api/ledger honours every filter, and each entry carries its verification and
a plain line.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from agent_friday.governance import action_gate
from agent_friday.services import action_ledger as L

ROOT = Path(__file__).resolve().parents[2]


def _seed():
    action_gate._receipt({"tool": "send_email", "class": "outward", "decision": "allow",
                          "approval": "ap-123", "surface": "test", "reason": "you pressed Send"})
    action_gate._receipt({"tool": "create_calendar_event", "class": "outward", "decision": "deny",
                          "surface": "test"})
    from agent_friday.services import activity_ledger
    activity_ledger.record("model_invocation", model="m1", provider="ollama", duration_ms=5)
    activity_ledger.record("tool_call", tool="read_file", ok=False, status="error", duration_ms=1)


def test_the_ledger_page_is_served(client):
    r = client.get("/ledger")
    assert r.status_code == 200, r.status_code
    html = r.get_data(as_text=True)
    assert "<title>Agent Friday™ Ledger</title>" in html
    assert "/api/ledger" in html and "__FRIDAY_API_TOKEN" in html


def test_entries_filter_by_tool_status_provider_words_and_date(client):
    _seed()
    d = client.get("/api/ledger?tool=send_email").get_json()
    assert [e["fields"]["tool"] for e in d["entries"]] == ["send_email"]
    e = d["entries"][0]
    assert e["verified"] is True
    assert e["plain"].startswith("Checkpoint for send_email: allow")
    assert e["links"].get("approval") == "ap-123"
    d = client.get("/api/ledger?status=deny").get_json()
    assert {x["fields"]["tool"] for x in d["entries"]} == {"create_calendar_event"}
    d = client.get("/api/ledger?provider=ollama").get_json()
    assert d["entries"] and all(x["fields"]["provider"] == "ollama" for x in d["entries"])
    d = client.get("/api/ledger?status=error&kind=activity").get_json()
    assert [x["fields"]["tool"] for x in d["entries"]] == ["read_file"]
    d = client.get("/api/ledger?q=pressed%20send").get_json()
    assert [x["fields"]["tool"] for x in d["entries"]] == ["send_email"]
    today = time.strftime("%Y-%m-%d", time.gmtime())
    assert len(client.get("/api/ledger?since=" + today).get_json()["entries"]) >= 4
    assert client.get("/api/ledger?until=2000-01-01").get_json()["entries"] == []
    d = client.get("/api/ledger?principal=owner&tool=send_email").get_json()
    assert len(d["entries"]) == 1


def test_a_changed_entry_shows_its_signature_does_not_hold(client):
    _seed()
    p = L.live_path()
    p.write_text(p.read_text(encoding="utf-8").replace("ap-123", "ap-999"), encoding="utf-8")
    L.reset_caches()
    d = client.get("/api/ledger?q=ap-999").get_json()
    assert d["entries"] and d["entries"][0]["verified"] is False


def test_export_gives_whole_entries_and_the_verification_report(client):
    _seed()
    r = client.get("/api/ledger/export?format=jsonl&kind=decision")
    assert r.status_code == 200 and "attachment" in r.headers.get("Content-Disposition", "")
    rows = [json.loads(x) for x in r.get_data(as_text=True).splitlines() if x.strip()]
    assert [x["rec"]["tool"] for x in rows] == ["send_email", "create_calendar_event"]
    assert all(L.body_hash(x) == x["hash"] for x in rows), "checkable outside Friday"
    r = client.get("/api/ledger/export?format=report")
    rep = json.loads(r.get_data(as_text=True))
    assert rep["ok"] is True and rep["records"] >= 4


def _press():
    from agent_friday import core
    return {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost",
            "X-Friday-Token": core._current_api_token(),
            "X-Friday-Desk": __import__("agent_friday.services.desk_press", fromlist=["x"])._KEY}


def test_retention_is_two_years_by_default_and_changed_on_this_pc_only(client):
    d = client.get("/api/ledger/retention").get_json()
    assert d["archive"]["after_days"] == 730 and d["default_days"] == 730
    r = client.post("/api/ledger/retention", json={"days": 365}, headers=_press(),
                    environ_base={"REMOTE_ADDR": "203.0.113.7"})
    assert r.status_code == 403
    r = client.post("/api/ledger/retention", json={"days": 365})       # no press
    assert r.status_code == 403
    assert L.archive_after_days() == 730
    # the generic settings write cannot change it either
    client.post("/api/settings", json={"settings": {L.ARCHIVE_SETTING: 30}}, headers=_press())
    assert L.archive_after_days() == 730
    r = client.post("/api/ledger/retention", json={"days": 365}, headers=_press())
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["days"] == 365 and L.archive_after_days() == 365
    r = client.post("/api/ledger/retention", json={"days": 3}, headers=_press())
    assert r.get_json()["days"] == L.MIN_ARCHIVE_AFTER_DAYS
    r = client.post("/api/ledger/retention", json={"days": 0}, headers=_press())
    assert r.get_json()["days"] == 0 and L.archive_after_days() == 0
    kinds = [e["kind"] for e in L.entries(["setting_changed"])]
    assert kinds, "the change is on record"


def test_archived_entries_are_searchable_from_the_workspace(client):
    for i in range(3):
        action_gate._receipt({"tool": "probe_archived", "class": "outward", "decision": "allow", "i": i})
    with L._TLOCK:
        L._check_dir()
        with L._lock():
            L._seal_live_locked(L.ledger_dir(), L.live_path())
    L.reset_caches()
    L.append("activity", {"kind": "tool_call", "tool": "fresh"})
    assert L.archive_old(now=time.time() + 800 * 86400, days=730)["archived"] == 1
    assert client.get("/api/ledger?tool=probe_archived").get_json()["entries"] == []
    d = client.get("/api/ledger?tool=probe_archived&archive=1").get_json()
    assert len(d["entries"]) == 3 and d["archive_searched"] is True
    assert all(e["verified"] for e in d["entries"])
    # the three decisions and the policy version they were made under (BD-11a)
    assert d["ledger"]["archived_records"] == 4 and d["ledger"]["ok"] is True


def test_settings_shows_the_retention_row_in_both_ui_files():
    for rel in ("index.html", "ui_parts/app.html"):
        t = (ROOT / rel).read_text(encoding="utf-8")
        assert "FridayLedgerRetentionRow" in t and '"Action record"' in t, rel
    js = (ROOT / "static" / "friday_ledger.js").read_text(encoding="utf-8")
    assert "window.FridayLedgerRetentionRow" in js and "/api/ledger/retention" in js
    assert "href: '/ledger'" in js


def test_the_ledger_page_runs_as_one_of_fridays_own_pages(client):
    """Not sandboxed like a creation: its own script may call the API and fetch
    the desk key from the loopback door, or its Save could never count as a press."""
    r = client.get("/ledger")
    csp = r.headers.get("Content-Security-Policy", "")
    assert r.status_code == 200
    assert "sandbox" not in csp and "connect-src 'none'" not in csp, csp
    assert "connect-src 'self'" in csp, csp
    assert b"X-Friday-Desk" in r.data and b"desk_proof_required" in r.data

