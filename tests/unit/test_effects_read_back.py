"""A change at a connected service counts as done only once it is read back from that service
(NS-4.6-1: verify effects against an authoritative source).

Before: a calendar event, task or contact Friday made was "done" on the strength of the service's
own reply, and a task that made one was verified by any tool that ran. Now each is read back
(events.get, tasks.get, people.get; a sent email is looked up in Sent) and marked verified only when
it is there as asked; a task whose such change was not read back is completed_unverified, and says
which. Fakes only; synthetic data.
"""
from __future__ import annotations

import itertools

import pytest

from agent_friday.services import agent
from agent_friday.services import connector_outcome as co

_ids = itertools.count(1)


class HttpError(Exception):
    def __init__(self, status):
        super().__init__("HttpError %s" % status)
        self.resp = type("R", (), {"status": status})()


class _Exec:
    def __init__(self, fn):
        self.fn = fn

    def execute(self, *a, **k):
        return self.fn()


class FakeCalendar:
    def __init__(self, lose_on_read=False):
        self.store, self.lose_on_read = {}, lose_on_read

    def events(self):
        return self

    def insert(self, calendarId, body, **kw):
        def run():
            eid = body.get("id") or "gen%d" % next(_ids)
            self.store[eid] = dict(body, id=eid)
            return dict(self.store[eid])
        return _Exec(run)

    def get(self, calendarId, eventId):
        def run():
            if self.lose_on_read or eventId not in self.store:
                raise HttpError(404)
            return dict(self.store[eventId])
        return _Exec(run)


@pytest.fixture
def cal(monkeypatch):
    from agent_friday.services import calendar_write as cw
    box = {"svc": FakeCalendar()}
    monkeypatch.setattr(cw, "write_ready", lambda: (True, ""))
    monkeypatch.setattr(cw, "resolve_write_account", lambda aid=None: ("acct_c1", None))
    monkeypatch.setattr(cw, "_service", lambda aid=None: (box["svc"], None))
    monkeypatch.setattr(cw, "_gate_calendar_field", lambda text, field: (text, ""))
    monkeypatch.setattr(cw, "_drop_calendar_cache", lambda *a, **k: None)
    return cw, box


EVENT = dict(title="Synthetic lunch", start="2026-11-03T12:00:00+00:00", end="2026-11-03T13:00:00+00:00")


def test_an_event_made_and_found_is_verified(cal):
    cw, _box = cal
    with co.call() as notes:
        out = cw.create_event(**EVENT)
    assert out.get("ok") and notes.verified == [{"kind": "calendar", "ref": out["id"]}]


def test_an_event_google_said_it_made_but_cannot_be_found_is_not_verified(cal):
    cw, box = cal
    box["svc"] = FakeCalendar(lose_on_read=True)
    with co.call() as notes:
        out = cw.create_event(**EVENT)
    assert out.get("ok") and notes.verified == []


def test_a_task_that_made_an_event_is_complete_only_when_it_was_read_back():
    made = {"name": "create_calendar_event", "input": {}, "result": '{"ok": true, "id": "e1"}'}
    verified, summary, status = agent._evidence_verdict([made])
    assert status == "completed_unverified" and "not read back" in summary
    verified, summary, status = agent._evidence_verdict([dict(made, verified=[{"kind": "calendar", "ref": "e1"}])])
    assert status == "complete" and verified is True


def test_a_task_of_reads_is_judged_as_before():
    assert agent._evidence_verdict([{"name": "read_file", "input": {}, "result": "text"}])[2] == "complete"


def test_the_tool_call_hands_what_it_read_back_to_the_loop(monkeypatch):
    def handler(inp):
        co.verified("calendar", "e-synthetic")
        return "done"
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "read_file", handler)
    agent._LAST_VERIFIED.set(None)
    agent._execute_tool("read_file", {"path": "x.txt"}, session_ctx={"authenticated": True})
    assert agent._take_verified() == [{"kind": "calendar", "ref": "e-synthetic"}]
    assert agent._take_verified() == [], "taken once"


def test_a_sent_email_found_in_sent_is_marked_verified(tmp_path, monkeypatch):
    from agent_friday.services import approvals
    from agent_friday.services import gmail_send as gs
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    from agent_friday.services import dissent_gate as dg
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    monkeypatch.setattr(gs, "_outbox_path", lambda: tmp_path / "sent_mail.jsonl")
    monkeypatch.setattr(gs, "sendable_accounts", lambda: [{"id": "acct_a", "email": "owner@example.test"}])
    monkeypatch.setattr(gs, "scope_granted", lambda account_id=None: True)
    from agent_friday.services import google_accounts as ga
    monkeypatch.setattr(ga, "credentials_for", lambda aid: object())
    monkeypatch.setattr(approvals, "_HOOKS", {})
    from agent_friday.governance import action_gate
    monkeypatch.setattr(action_gate, "record_external", lambda *a, **k: None)

    class _Msgs:
        def send(self, userId=None, body=None):
            return _Exec(lambda: {"id": "m1", "threadId": "t1"})

        def get(self, userId=None, id=None, format=None):
            return _Exec(lambda: {"id": id, "labelIds": ["SENT"]})

    class _Svc:
        def users(self):
            return type("U", (), {"messages": lambda s: _Msgs()})()
    import googleapiclient.discovery as disc
    monkeypatch.setattr(disc, "build", lambda *a, **k: _Svc(), raising=False)
    aid = gs.request_send(to="someone@example.test", subject="Synthetic", body="Synthetic text.")["approval_id"]
    approvals.decide(aid, "approve")
    out = gs.send(aid)
    assert out.get("verified") is True
    assert gs.outbox()[0].get("verified") is True
