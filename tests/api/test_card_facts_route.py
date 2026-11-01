"""Every card the owner is served carries its facts, and Inspect explains it
(NS-18.4-3, slice A14; services/card_facts, services/explain).

The list, the detail and the live feed each give a card with `card_facts`
worked out as it is read; nothing is written to the card. Inspect answers
"why" for the action the card is about (the one whose decision names the
card, else the card's task), read only, and never repeats an error's text.
The observer credential gets none of it: approval cards are not on its list.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import approval_feed as feed
from agent_friday.services import approvals as ap
from agent_friday.services import observer_access as obs

TITLE = "Dentist with Dr Okafor about the crown"


@pytest.fixture(autouse=True)
def clean(friday_dir, tmp_path, monkeypatch):
    if ap.APPROVALS_FILE.exists():
        ap.APPROVALS_FILE.unlink()
    monkeypatch.setattr(obs, "_hash_path", lambda: tmp_path / "obs.sha256")
    feed.reset()
    yield
    feed.reset()


def _card(task_id=""):
    return ap.create_approval(
        kind="tool_confirm", subject_type="tool_action", subject_id="a14:%s" % (task_id or "x"),
        title=TITLE, action_description="create_calendar_event {}", force_gate=True, action_class="outward",
        payload={"tool": "create_calendar_event", "input": {"title": TITLE},
                 "conversation_id": "", "task_id": task_id},
        requested_by="confirmation_gate")


def _frames(resp, n):
    out, buf = [], ""
    try:
        for chunk in resp.response:
            buf += chunk.decode() if isinstance(chunk, bytes) else chunk
            while "\n\n" in buf:
                frame, buf = buf.split("\n\n", 1)
                if frame.startswith("data: "):
                    out.append(json.loads(frame[6:]))
                    if len(out) >= n:
                        return out
    finally:
        resp.close()
    return out


def test_the_owner_list_carries_each_cards_facts_and_the_card_is_not_changed(client):
    rec = _card()
    r = client.get("/api/approvals?status=pending")
    assert r.status_code == 200
    (served,) = [a for a in r.get_json()["approvals"] if a["approval_id"] == rec["approval_id"]]
    f = served["card_facts"]
    assert f["what"]["words"] == "Add a calendar event"
    assert f["reversibility"]["words"].startswith("can be undone")
    assert f["privacy_impact"]["channel"] == "calendar" and f["privacy_impact"]["leaves_pc"] is True
    assert f["verification_plan"]["words"].startswith("Friday will check")
    assert f["model"]["words"] == "not recorded"
    assert f["editable"]["can"] is False
    assert TITLE not in json.dumps(f)
    # worked out when read: never stored on the card
    assert "card_facts" not in ap.get_approval(rec["approval_id"])
    assert "card_facts" not in ap.APPROVALS_FILE.read_text(encoding="utf-8")


def test_the_detail_carries_the_facts(client):
    rec = _card()
    d = client.get("/api/approvals/%s" % rec["approval_id"]).get_json()
    assert d["ok"] and d["approval"]["card_facts"]["cost"]["words"] == "no charge expected"


def test_the_live_feed_carries_the_facts_on_the_snapshot_and_on_a_new_card(client):
    a = _card()
    resp = client.get("/api/approvals/events", buffered=False)
    it = iter(resp.response)
    first = next(it)
    snap = json.loads((first.decode() if isinstance(first, bytes) else first)[6:])
    assert snap["type"] == "snapshot"
    assert snap["pending"][0]["approval_id"] == a["approval_id"]
    assert snap["pending"][0]["card_facts"]["what"]["words"] == "Add a calendar event"
    b = _card(task_id="task-0002")
    got = None
    for chunk in it:
        text = chunk.decode() if isinstance(chunk, bytes) else chunk
        if text.startswith("data: "):
            evt = json.loads(text[6:].strip())
            if evt.get("type") == "pending":
                got = evt
                break
    resp.close()
    assert got["approval"]["approval_id"] == b["approval_id"]
    assert got["approval"]["card_facts"]["privacy_impact"]["channel"] == "calendar"


def test_inspect_explains_the_action_whose_decision_names_the_card(client, monkeypatch):
    rec = _card()
    aid = "act_" + "a1" * 10
    rows = [{"kind": "decision", "rec": {"approval": "appr_someoneelse", "action_id": "act_" + "b2" * 10}},
            {"kind": "decision", "rec": {"approval": rec["approval_id"], "action_id": aid}}]
    from agent_friday.services import action_ledger, explain
    monkeypatch.setattr(action_ledger, "entries",
                        lambda kinds=None, *, where=None, limit=None, **kw:
                        [e for e in rows if e["kind"] in kinds and (where is None or where(e))][:limit])
    asked = {}

    def fake_explain(action_id=None, task_id=None):
        asked.update(action_id=action_id, task_id=task_id)
        return {"text": "What: create_calendar_event, allowed", "sections": {}}
    monkeypatch.setattr(explain, "explain", fake_explain)
    d = client.get("/api/approvals/%s/inspect" % rec["approval_id"]).get_json()
    assert d["ok"] and d["linked_by"] == "action_id" and asked == {"action_id": aid, "task_id": None}
    assert d["text"] == "What: create_calendar_event, allowed"
    assert d["card_facts"]["what"]["words"] == "Add a calendar event"


def test_inspect_falls_back_to_the_cards_task(client, monkeypatch):
    rec = _card(task_id="task-0001")
    from agent_friday.services import action_ledger, explain
    monkeypatch.setattr(action_ledger, "entries", lambda *a, **kw: [])
    asked = {}
    monkeypatch.setattr(explain, "explain",
                        lambda action_id=None, task_id=None: asked.update(a=action_id, t=task_id) or {"text": "T"})
    d = client.get("/api/approvals/%s/inspect" % rec["approval_id"]).get_json()
    assert d["ok"] and d["linked_by"] == "task_id" and asked == {"a": None, "t": "task-0001"}


def test_inspect_with_nothing_recorded_says_so(client, monkeypatch):
    rec = _card()
    from agent_friday.services import action_ledger, explain
    monkeypatch.setattr(action_ledger, "entries", lambda *a, **kw: [])
    monkeypatch.setattr(explain, "explain", lambda **kw: pytest.fail("nothing to explain"))
    d = client.get("/api/approvals/%s/inspect" % rec["approval_id"]).get_json()
    assert d["ok"] and d["linked_by"] is None and d["explain"] is None
    assert d["text"].startswith("No action record names this card yet")
    assert d["card_facts"]["reversibility"]["words"].startswith("can be undone")


def test_inspect_an_unknown_card_is_404(client):
    assert client.get("/api/approvals/appr_nope000000/inspect").status_code == 404


def test_inspect_never_returns_an_errors_text(client, monkeypatch):
    rec = _card(task_id="task-0003")
    from agent_friday.services import action_ledger, explain
    monkeypatch.setattr(action_ledger, "entries", lambda *a, **kw: [])

    def boom(**kw):
        raise RuntimeError("C:/Users/someone/private/ledger.jsonl is broken")
    monkeypatch.setattr(explain, "explain", boom)
    r = client.get("/api/approvals/%s/inspect" % rec["approval_id"])
    assert r.status_code == 500
    assert "private" not in r.get_data(as_text=True)


def test_the_observer_gets_no_card_list_detail_or_inspect(client):
    rec = _card()
    hdr = {obs.HEADER: client.post("/api/tasks/observer-token").get_json()["token"]}
    for path in ("/api/approvals?status=pending", "/api/approvals/%s" % rec["approval_id"],
                 "/api/approvals/%s/inspect" % rec["approval_id"]):
        r = client.get(path, headers=hdr)
        assert r.status_code == 403, (path, r.status_code)
        assert TITLE not in r.get_data(as_text=True)
