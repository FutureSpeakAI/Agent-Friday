"""Every mail change from Friday's page is the owner's press, and each one that reaches Gmail
or a mailing list passes the action checkpoint first.

Before: the Messages routes took the owner from the request body
(`requested_by: "ui:..."`, or nothing at all for labels, label changes, undo,
restore and Gmail drafts), so any program on this PC that could reach the API
could archive, delete, relabel, unsubscribe or write drafts as the owner. None
of them passed the checkpoint's integrity check or signed receipt.

Now, as on the Calendar page:
  * a body that says "ui:..." without the owner's press changes nothing, and the
    answer says so;
  * the owner's press that reaches Gmail passes governance/action_gate.record_external
    BEFORE Gmail is called, and a hold changes nothing and says "Nothing ...";
  * changes that stay in Friday (snooze) are the press alone;
  * Friday asking for archive / Trash / spam / mute / unsubscribe still gets a card,
    and asking for any other change is answered with what it can ask for;
  * cancelling a held send stays open (stopping a send is always allowed).
Synthetic accounts and conversations only (the fake Gmail of test_mail_gmail_parity).
"""
from __future__ import annotations

import pytest

from agent_friday.governance import action_gate as ag
from tests.api.test_mail_gmail_parity import CALLS, THREADS, _fake, _items, state  # noqa: F401  (fixtures)
from tests.owner_press_support import NOT_A_PRESS, press

ONE = _items(("c1", "acct_m", "t1"))


@pytest.fixture
def checkpoint(monkeypatch):
    seen = {"calls": [], "hold": False}

    def record_external(action, *, surface, approval_id=None, target=""):
        # How many Gmail calls had been made when the checkpoint ran: it must run first.
        seen["calls"].append({"action": action, "surface": surface, "target": target,
                              "gmail_before": len(CALLS)})
        if seen["hold"]:
            raise ag.Held("the signed receipt could not be written")

    monkeypatch.setattr(ag, "record_external", record_external)
    return seen


@pytest.fixture
def side_doors(monkeypatch):
    """Unsubscribe, label creation and Gmail drafts, recorded instead of run."""
    from agent_friday.services import gmail_mailbox as gm
    from agent_friday.services import gmail_send as gs
    from agent_friday.services import mail_unsubscribe as mu
    ran = []
    monkeypatch.setattr(mu, "unsubscribe", lambda aid, mid: ran.append(("unsubscribe", aid, mid)) or {"status": "done"})
    monkeypatch.setattr(gm, "create_label", lambda aid, name: ran.append(("create_label", aid, name)) or {"id": "Label_9", "name": name})
    monkeypatch.setattr(gs, "save_draft", lambda **kw: ran.append(("save_draft", kw.get("account_id"))) or {"draft_id": "d1"})
    return ran


def _forged():
    """Each mail change, as a body that claims (or does not need to claim) the owner's click."""
    return [
        ("archive", "/api/messages/action",
         {"requested_by": "ui:messages", "ids": ["c1"], "action": "archive", "gmail": ONE}),
        ("trash", "/api/messages/action",
         {"requested_by": "ui:messages", "ids": ["c1"], "action": "trash", "gmail": ONE}),
        ("mark-read", "/api/messages/action",
         {"requested_by": "ui:messages", "ids": ["c1"], "action": "read", "gmail": ONE}),
        ("snooze", "/api/messages/action",
         {"requested_by": "ui:messages", "ids": ["c1"], "action": "snooze"}),
        ("label", "/api/mail/modify", {"account_id": "acct_m", "thread_ids": ["t3"], "add": ["Label_2"]}),
        ("new-label", "/api/mail/labels", {"account_id": "acct_m", "name": "Leads"}),
        ("label-undo", "/api/mail/modify/undo", {"account_id": "acct_m", "changed": {"t1": {"trashed": True}}}),
        ("restore", "/api/messages/restore",
         {"states": {"c1": {"archived": True}}, "gmail_changes": {"acct_m": {"t1": {"trashed": True}}}}),
        ("unsubscribe", "/api/mail/unsubscribe",
         {"account_id": "acct_r", "message_id": "m1", "confirmed": True, "requested_by": "ui:messages"}),
        ("draft", "/api/mail/draft", {"account_id": "acct_m", "to": ["a@example.test"], "subject": "s", "body": "b"}),
    ]


@pytest.mark.parametrize("headers", NOT_A_PRESS, ids=["program", "no-token"])
@pytest.mark.parametrize("name,url,body", _forged(), ids=[f[0] for f in _forged()])
def test_a_mail_change_that_is_not_the_owners_press_changes_nothing(client, state, checkpoint, side_doors,
                                                                    headers, name, url, body):
    before = {k: set(v) for k, v in THREADS.items()}
    r = client.post(url, json=body, headers=headers)
    assert r.status_code == 403, (name, r.status_code, r.get_json())
    j = r.get_json() or {}
    assert str(j.get("message") or j.get("error") or "").strip(), "the refusal says why"
    assert CALLS == [] and THREADS == before, "nothing reached Gmail"
    assert side_doors == [] and state["s"] == {}
    assert checkpoint["calls"] == []


def test_the_owners_archive_passes_the_checkpoint_before_gmail(client, state, checkpoint):
    r = client.post("/api/messages/action", json={"requested_by": "ui:messages", "ids": ["c1"], "action": "archive",
                                                  "gmail": ONE}, headers=press())
    assert r.status_code == 200, r.get_json()
    assert [c["action"] for c in checkpoint["calls"]] == ["gmail:archive"]
    call = checkpoint["calls"][0]
    assert call["surface"] == "messages_workspace" and call["gmail_before"] == 0
    assert "acct_m" in call["target"] and "t1" in call["target"]
    assert "INBOX" not in THREADS["t1"] and state["s"]["c1"]["archived"] is True


def test_a_held_archive_changes_nothing_and_says_so(client, state, checkpoint):
    checkpoint["hold"] = True
    for action in ("archive", "trash"):
        r = client.post("/api/messages/action", json={"requested_by": "ui:messages", "ids": ["c1"], "action": action,
                                                      "gmail": ONE}, headers=press())
        assert r.status_code == 403, action
        assert r.get_json()["message"].startswith("Nothing was changed")
    assert CALLS == [] and THREADS["t1"] == {"INBOX", "UNREAD"} and state["s"] == {}


def test_a_change_that_stays_in_friday_is_the_press_alone(client, state, checkpoint):
    r = client.post("/api/messages/action", json={"requested_by": "ui:messages", "ids": ["c1"], "action": "snooze"},
                    headers=press())
    assert r.status_code == 200 and state["s"]["c1"]["snoozed_until"]
    assert checkpoint["calls"] == [] and CALLS == []


def test_the_owners_label_change_and_its_undo_pass_the_checkpoint(client, checkpoint):
    r = client.post("/api/mail/modify", json={"account_id": "acct_m", "thread_ids": ["t3"], "add": ["Label_2"]},
                    headers=press())
    assert r.status_code == 200, r.get_json()
    assert "Label_2" in THREADS["t3"]
    label = [c for c in checkpoint["calls"] if c["action"] == "gmail:label"]
    assert len(label) == 1 and label[0]["gmail_before"] == 0 and "Label_2" in label[0]["target"]
    made = len(CALLS)
    r = client.post("/api/mail/modify/undo", json={"account_id": "acct_m", "changed": r.get_json()["changed"]},
                    headers=press())
    assert r.status_code == 200 and "Label_2" not in THREADS["t3"]
    undo = [c for c in checkpoint["calls"] if c["action"] == "gmail:undo"]
    assert len(undo) == 1 and undo[0]["gmail_before"] == made


def test_a_held_label_change_changes_nothing(client, checkpoint):
    checkpoint["hold"] = True
    r = client.post("/api/mail/modify", json={"account_id": "acct_m", "thread_ids": ["t3"], "add": ["Label_2"]},
                    headers=press())
    assert r.status_code == 403 and r.get_json()["message"].startswith("No label was changed")
    assert CALLS == [] and "Label_2" not in THREADS["t3"]


def test_the_owners_restore_puts_gmail_back_after_the_checkpoint(client, state, checkpoint):
    THREADS["t1"].add("TRASH")
    r = client.post("/api/messages/restore", json={"states": {"c1": {}},
                                                   "gmail_changes": {"acct_m": {"t1": {"trashed": True}}}},
                    headers=press())
    assert r.status_code == 200, r.get_json()
    assert [c["action"] for c in checkpoint["calls"]] == ["gmail:undo"]
    assert checkpoint["calls"][0]["gmail_before"] == 0 and "TRASH" not in THREADS["t1"]


def test_a_held_restore_puts_nothing_back(client, state, checkpoint):
    THREADS["t1"].add("TRASH")
    checkpoint["hold"] = True
    r = client.post("/api/messages/restore", json={"states": {"c1": {"archived": True}},
                                                   "gmail_changes": {"acct_m": {"t1": {"trashed": True}}}},
                    headers=press())
    assert r.status_code == 403 and r.get_json()["message"].startswith("Nothing was put back")
    assert CALLS == [] and "TRASH" in THREADS["t1"] and state["s"] == {}


@pytest.mark.parametrize("url,body,action,ran,nothing", [
    ("/api/mail/labels", {"account_id": "acct_m", "name": "Leads"}, "gmail:create_label",
     ("create_label", "acct_m", "Leads"), "No label was made"),
    ("/api/mail/unsubscribe", {"account_id": "acct_r", "message_id": "m1", "confirmed": True,
                               "requested_by": "ui:messages"}, "mail:unsubscribe",
     ("unsubscribe", "acct_r", "m1"), "You were not unsubscribed"),
    ("/api/mail/draft", {"account_id": "acct_m", "to": ["a@example.test"], "subject": "s", "body": "b"},
     "gmail:save_draft", ("save_draft", "acct_m"), "No draft was saved"),
], ids=["new-label", "unsubscribe", "draft"])
def test_the_owners_other_mail_changes_pass_the_checkpoint_first(client, checkpoint, side_doors,
                                                                 url, body, action, ran, nothing):
    checkpoint["hold"] = True
    r = client.post(url, json=body, headers=press())
    assert r.status_code == 403 and r.get_json()["message"].startswith(nothing)
    assert side_doors == [], "a hold reaches nothing"
    checkpoint["hold"] = False
    r = client.post(url, json=body, headers=press())
    assert r.status_code == 200, r.get_json()
    assert [c["action"] for c in checkpoint["calls"]] == [action, action]
    assert side_doors == [ran]


def test_friday_asking_for_a_change_it_has_no_card_for_is_told_what_it_can_ask(client, state, checkpoint):
    r = client.post("/api/messages/action", json={"requested_by": "agent:inbox", "ids": ["c1"], "action": "flag",
                                                  "gmail": ONE})
    assert r.status_code == 403
    msg = r.get_json()["message"]
    assert msg.startswith("Nothing was changed") and "archive" in msg
    assert CALLS == [] and state["s"] == {}


def test_friday_asking_to_archive_still_gets_a_card(client, state, monkeypatch):
    from agent_friday.services import approvals as ap
    made = {}
    monkeypatch.setattr(ap, "create_approval", lambda **kw: made.update(kw) or {"approval_id": "ap9", "status": "pending"})
    r = client.post("/api/messages/action", json={"requested_by": "agent:inbox", "ids": ["c1"], "action": "archive",
                                                  "gmail": ONE})
    assert r.status_code == 202 and made["payload"]["action"] == "archive"
    assert CALLS == [] and state["s"] == {}


def test_the_press_is_the_owner_even_without_a_label(client, state, checkpoint):
    """The page's own label is not what makes it the owner; the press is."""
    r = client.post("/api/messages/action", json={"ids": ["c1"], "action": "archive", "gmail": ONE}, headers=press())
    assert r.status_code == 200 and "INBOX" not in THREADS["t1"]
    assert [c["action"] for c in checkpoint["calls"]] == ["gmail:archive"]


def test_cancelling_a_held_send_stays_open(client, monkeypatch):
    from agent_friday.services import gmail_send as gs
    monkeypatch.setattr(gs, "cancel", lambda aid: {"cancelled": aid})
    r = client.post("/api/mail/held/ap1/cancel", json={})
    assert r.status_code == 200 and r.get_json()["cancelled"] == "ap1"


# -- the inbox clean-up board ----------------------------------------------

@pytest.fixture
def cleanup(monkeypatch):
    from flask import Flask

    import agent_friday.core as core
    from agent_friday.routes import inbox_cleanup as route
    from agent_friday.services import inbox_cleanup as ic
    ran = []
    monkeypatch.setattr(core, "_is_local_request", lambda: True)
    monkeypatch.setattr(ic, "unsubscribe", lambda senders, **kw: ran.append(list(senders)) or {"done": []})
    monkeypatch.setattr(ic, "view", lambda: {})
    monkeypatch.setattr(ic, "propose", lambda senders, **kw: ran.append(("card", list(senders))) or {"status": "pending_approval"})
    app = Flask(__name__)
    app.register_blueprint(route.inbox_cleanup_bp)
    return app.test_client(), ran


def test_the_clean_up_unsubscribes_only_on_the_owners_press_after_the_checkpoint(cleanup, checkpoint):
    c, ran = cleanup
    body = {"senders": ["shop.example"], "requested_by": "ui:inbox-cleanup"}
    for headers in NOT_A_PRESS:
        r = c.post("/api/inbox-cleanup/unsubscribe", json=body, headers=headers)
        assert r.status_code == 403, r.get_json()
    assert ran == [] and checkpoint["calls"] == []
    checkpoint["hold"] = True
    r = c.post("/api/inbox-cleanup/unsubscribe", json=body, headers=press())
    assert r.status_code == 403 and r.get_json()["message"].startswith("Nobody was unsubscribed") and ran == []
    checkpoint["hold"] = False
    r = c.post("/api/inbox-cleanup/unsubscribe", json=body, headers=press())
    assert r.status_code == 200 and ran == [["shop.example"]]
    assert [x["action"] for x in checkpoint["calls"]] == ["mail:unsubscribe", "mail:unsubscribe"]
    assert checkpoint["calls"][0]["surface"] == "messages_workspace"


def test_the_clean_up_reads_mail_only_on_the_owners_press(cleanup, monkeypatch):
    c, _ran = cleanup
    from agent_friday.services import inbox_cleanup as ic
    started = []
    monkeypatch.setattr(ic, "start", lambda src: started.append(src) or {"job": {"state": "running"}})
    body = {"source": {"kind": "mbox", "path": "x.mbox"}, "requested_by": "ui:inbox-cleanup"}
    assert c.post("/api/inbox-cleanup/start", json=body).status_code == 403
    assert started == []
    assert c.post("/api/inbox-cleanup/start", json=body, headers=press()).status_code == 200
    assert started == [{"kind": "mbox", "path": "x.mbox"}]
