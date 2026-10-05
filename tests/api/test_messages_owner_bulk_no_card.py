"""I3: the owner's own click on a Friday-made selection stays the owner's: no card.

Friday ticking rows changes nothing about who presses Archive. The bulk bar sends
requested_by "ui:messages"; the same request from Friday waits for approval.
"""
from __future__ import annotations

import pytest

from agent_friday.routes import messages as routes_messages
from agent_friday.services import mail_proposals


@pytest.fixture
def msg_client():
    """The app with the Messages blueprint on it; requests from 127.0.0.1 are the local owner."""
    import agent_friday.core as core
    if routes_messages.messages_bp.name not in core.app.blueprints:
        core.app.register_blueprint(routes_messages.messages_bp)
    core.app.config["TESTING"] = True
    return core.app.test_client()


def _rows(n=142):
    ids = ["m%d" % i for i in range(n)]
    return ids, [{"id": i, "account_id": "acct_work", "thread_id": "t%d" % k} for k, i in enumerate(ids)]


def test_the_owners_archive_on_a_ticked_batch_runs_with_no_card(msg_client, monkeypatch):
    ran, carded = [], []
    monkeypatch.setattr(routes_messages, "run_action",
                        lambda action, ids, gmail, data=None: ran.append((action, len(ids))) or {"status": "ok", "ids": ids})
    monkeypatch.setattr(mail_proposals, "propose", lambda *a, **k: carded.append(a) or {"status": "pending"})
    ids, gmail = _rows()
    for action in ("archive", "trash", "spam", "read", "flag"):
        r = msg_client.post("/api/messages/action", json={"ids": ids, "action": action, "gmail": gmail,
                                                      "requested_by": "ui:messages"})
        assert r.status_code == 200, (action, r.get_json())
    assert carded == [] and [a for a, _n in ran] == ["archive", "trash", "spam", "read", "flag"]
    assert all(n == 142 for _a, n in ran)


def test_the_same_request_from_friday_waits_for_approval(msg_client, monkeypatch):
    ran, carded = [], []
    monkeypatch.setattr(routes_messages, "run_action", lambda *a, **k: ran.append(a) or {"status": "ok"})
    monkeypatch.setattr(mail_proposals, "propose", lambda *a, **k: carded.append(a) or {"status": "pending"})
    ids, gmail = _rows(3)
    for who in ("friday", "", "tool:organize_email"):
        r = msg_client.post("/api/messages/action", json={"ids": ids, "action": "archive", "gmail": gmail,
                                                      "requested_by": who})
        assert r.status_code == 202, (who, r.get_json())
    assert ran == [] and len(carded) == 3
