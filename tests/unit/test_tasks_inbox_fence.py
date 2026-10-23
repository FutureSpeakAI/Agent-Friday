"""Nothing but the phone list itself may write to it, and nothing seen there can approve a card.

Two guarantees under services/tasks_inbox (spec GT-E03, GT-E08), tested through the modules
that exist on the old tree too, so each test fails there by assertion:

* google_accounts refuses Friday's inbox list to every Tasks write (complete, update, create,
  delete) and leaves it out of every read, whatever the caller. If Friday's own model could
  tick that list, a planted email could make her "approve" her own card;
* approvals never takes an approve labelled as coming from Google Tasks, for any kind and
  whatever channel the caller names, and its receipt says google_tasks_tick.

The inbox list is planted as the settings file names it, unsigned: the fence reads it raw on
purpose, so a damaged or hand-edited file still fences the list.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import approvals as ap
from agent_friday.services import google_accounts as ga

INBOX = "INBOXLIST1"


@pytest.fixture
def planted(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    d = tmp_path / "tasks_inbox"
    d.mkdir()
    (d / "config.json").write_text(json.dumps({"enabled": True, "account_id": "acct1", "list_id": INBOX}),
                                   encoding="utf-8")
    writes = []
    monkeypatch.setattr(ga, "credentials_for", lambda account_id: object())
    monkeypatch.setattr(ga, "_gate_task_text", lambda body: (dict(body), ""))
    monkeypatch.setattr(ga, "_write_task_for_creds",
                        lambda creds, tasklist_id, body, task_id: writes.append(("write", tasklist_id, task_id)) or
                        {"id": task_id or "new", "title": body.get("title", ""), "status": body.get("status", "")})
    monkeypatch.setattr(ga, "_delete_task_for_creds",
                        lambda creds, tasklist_id, task_id: writes.append(("delete", tasklist_id, task_id)) or
                        {"deleted": True, "id": task_id})
    return writes


@pytest.mark.parametrize("call", [
    lambda: ga.complete_task("acct1", INBOX, "t1"),
    lambda: ga.update_task("acct1", INBOX, "t1", notes="approved"),
    lambda: ga.update_task("acct1", INBOX, "t1", status="completed"),
    lambda: ga.create_task("acct1", "Friday needs your yes", tasklist_id=INBOX),
    lambda: ga.delete_task("acct1", INBOX, "t1"),
    # The fence never depends on the caller naming the right account.
    lambda: ga.complete_task("some-other-account", INBOX, "t1"),
], ids=["complete", "update_notes", "update_status", "create", "delete", "other_account"])
def test_no_tasks_write_reaches_the_inbox_list(planted, call):
    out = call()
    assert planted == [], "a Tasks write reached Friday's inbox list"
    assert out.get("error") and out.get("fenced") is True


def test_writes_to_the_owners_other_lists_still_go_through(planted):
    out = ga.complete_task("acct1", "groceries", "t1")
    assert "error" not in out
    assert planted == [("write", "groceries", "t1")]


def test_the_inbox_list_is_left_out_of_every_read(planted, monkeypatch):
    seen = []

    class _Svc:
        def tasklists(self):
            class L:
                def list(self, maxResults=10):
                    class E:
                        def execute(self):
                            return {"items": [{"id": INBOX, "title": "Friday — awaiting you"},
                                              {"id": "groceries", "title": "Groceries"}]}
                    return E()
            return L()

        def tasks(self):
            class T:
                def list(self, tasklist, **kw):
                    seen.append(tasklist)

                    class E:
                        def execute(self):
                            return {"items": [{"id": "x-" + tasklist, "title": "an item"}]}
                    return E()
            return T()

    import googleapiclient.discovery as disc
    monkeypatch.setattr(disc, "build", lambda *a, **k: _Svc())
    out = ga._tasks_for_creds(object(), 50)
    assert INBOX not in seen
    assert [t["tasklist_id"] for t in out] == ["groceries"]


def test_a_damaged_settings_file_still_fences_its_list(tmp_path, monkeypatch, planted):
    (tmp_path / "tasks_inbox" / "config.json").write_text(
        json.dumps({"enabled": True, "account_id": "acct1", "list_id": INBOX, "sig": "forged"}),
        encoding="utf-8")
    ga.complete_task("acct1", INBOX, "t1")
    assert planted == []


def test_when_the_inbox_record_cannot_be_read_every_tasks_write_is_refused(tmp_path, planted):
    """Fail closed: an unreadable record might name the inbox list, so no list is safe."""
    (tmp_path / "tasks_inbox" / "record.jsonl").mkdir()          # reading it raises an OS error
    out = ga.complete_task("acct1", "groceries", "t1")
    assert planted == [] and out.get("fenced") is True


# ── approvals: a tick never approves ─────────────────────────────────────────

@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(ap, "_fire_hook", lambda rec: None)
    monkeypatch.setattr(ap, "_feed", lambda *a, **k: None)
    monkeypatch.setattr(ap, "_notify_pending", lambda rec: None)


@pytest.mark.parametrize("kind", ["tool_confirm", "tainted_action", "governed_action", "external_message",
                                  "memory_proposal", "file_grant_request"])
@pytest.mark.parametrize("source", ["", "typed", "voice", "text_message", "in_process"])
def test_an_approve_from_google_tasks_is_refused_for_every_kind_and_channel(store, kind, source):
    card = ap.create_approval(kind=kind, subject_type="fence_test", subject_id="s-%s-%s" % (kind, source),
                              title="A decision", force_gate=True)
    rec, won = ap.decide_with_outcome(card["approval_id"], "approve", decided_by="remote:tasks_tick",
                                      source=source)
    assert not won
    assert ap.get_approval(card["approval_id"])["status"] == "pending"


def test_a_decline_from_google_tasks_is_recorded_as_such(store):
    card = ap.create_approval(kind="tool_confirm", subject_type="fence_test", subject_id="d1",
                              title="Send it", force_gate=True)
    rec, won = ap.decide_with_outcome(card["approval_id"], "deny", decided_by="remote:tasks_tick")
    assert won and rec["status"] == "denied"
    assert rec["decided_source"] == "google_tasks_tick"


def test_only_the_tick_label_is_ever_recorded_as_google_tasks(store):
    """A caller may name its channel, but never this one: the source is worked out from the label."""
    card = ap.create_approval(kind="tool_confirm", subject_type="fence_test", subject_id="d2",
                              title="Send it", force_gate=True)
    rec, won = ap.decide_with_outcome(card["approval_id"], "deny", decided_by="owner",
                                      source="google_tasks_tick")
    assert won and rec["decided_source"] != "google_tasks_tick"
