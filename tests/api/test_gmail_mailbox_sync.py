"""Archive, read/unread, star and labels change Gmail itself for an account
that granted gmail.modify, and undo reverses exactly what changed. Accounts
without the permission change in Friday only and are told so. Spam and trash
are never touched."""
import pytest

from agent_friday.services import gmail_mailbox as gm
from agent_friday.services import google_accounts as ga

THREADS: dict = {}
CALLS: list = []


class _Req:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class _Svc:
    def users(self):
        svc = self

        class U:
            def threads(self):
                class T:
                    def get(self, userId=None, id=None, format=None):      # noqa: A002,N803
                        return _Req(lambda: {"id": id, "messages": [{"labelIds": sorted(THREADS[id])}]})

                    def modify(self, userId=None, id=None, body=None):     # noqa: A002,N803
                        def run():
                            CALLS.append((id, list(body["addLabelIds"]), list(body["removeLabelIds"])))
                            THREADS[id] = (THREADS[id] | set(body["addLabelIds"])) - set(body["removeLabelIds"])
                            return {"id": id}
                        return _Req(run)
                return T()

            def labels(self):
                class L:
                    def list(self, userId=None):                           # noqa: N803
                        return _Req(lambda: {"labels": [{"id": "INBOX", "name": "INBOX", "type": "system"},
                                                        {"id": "Label_2", "name": "Stories", "type": "user"}]})

                    def create(self, userId=None, body=None):              # noqa: N803
                        return _Req(lambda: {"id": "Label_9", "name": body["name"]})
                return L()
        return U()


@pytest.fixture(autouse=True)
def _fake(monkeypatch):
    THREADS.clear(); CALLS.clear()
    THREADS.update({"t1": {"INBOX", "UNREAD"}, "t2": {"STARRED"}, "t3": {"INBOX"}})
    accts = {"acct_m": [ga.GMAIL_READ, ga.GMAIL_MODIFY], "acct_r": [ga.GMAIL_READ]}
    monkeypatch.setattr(ga, "get_account", lambda aid: {"id": aid, "scopes": accts.get(aid, [])} if aid in accts else None)
    monkeypatch.setattr(ga, "credentials_for", lambda aid: object())
    from agent_friday.services import gmail_read
    monkeypatch.setattr(gmail_read, "_service", lambda creds: _Svc())
    yield


def test_archive_changes_gmail_and_undo_reverses_only_what_changed():
    out = gm.apply_action("acct_m", ["t1", "t2"], "archive")
    assert THREADS["t1"] == {"UNREAD"} and THREADS["t2"] == {"STARRED"}
    assert out["changed"]["t2"] == {"added": [], "removed": []}          # was not in the inbox
    assert ("t2", [], ["INBOX"]) not in CALLS                               # so nothing was sent for it
    gm.undo("acct_m", out["changed"])
    assert THREADS["t1"] == {"INBOX", "UNREAD"} and THREADS["t2"] == {"STARRED"}


def test_star_and_read_map_to_gmail_labels():
    gm.apply_action("acct_m", ["t3"], "flag")
    gm.apply_action("acct_m", ["t1"], "read")
    assert "STARRED" in THREADS["t3"] and "UNREAD" not in THREADS["t1"]


def test_without_modify_nothing_is_attempted():
    with pytest.raises(gm.NotPermitted):
        gm.apply_action("acct_r", ["t1"], "archive")
    assert CALLS == []


def test_spam_and_trash_are_refused():
    for lab in ("TRASH", "SPAM"):
        with pytest.raises(ValueError):
            gm.modify_threads("acct_m", ["t1"], add=[lab])
    assert CALLS == []


def test_messages_action_syncs_gmail_and_restore_undoes_it(client, monkeypatch):
    store = {"s": {}}
    from agent_friday.routes import messages as rm
    monkeypatch.setattr(rm, "_load_message_state", lambda: {k: dict(v) for k, v in store["s"].items()})
    monkeypatch.setattr(rm, "_save_message_state", lambda st: store.__setitem__("s", {k: dict(v) for k, v in st.items()}))
    r = client.post("/api/messages/action", json={
        "ids": ["c1", "c3"], "action": "archive",
        "gmail": [{"id": "c1", "account_id": "acct_m", "thread_id": "t1"},
                  {"id": "c3", "account_id": "acct_r", "thread_id": "t3"}]}).get_json()
    assert r["status"] == "ok"
    assert r["gmail_status"] == {"acct_m": "synced", "acct_r": "not_permitted"}
    assert "INBOX" not in THREADS["t1"] and "INBOX" in THREADS["t3"]       # read-only account: Friday only
    assert store["s"]["c1"]["archived"] and store["s"]["c3"]["archived"]
    client.post("/api/messages/restore", json={"states": r["before"], "gmail_changes": r["gmail_changes"]})
    assert "INBOX" in THREADS["t1"] and store["s"] == {}


def test_labels_list_create_apply_and_undo(client):
    labs = client.get("/api/mail/labels?account=acct_m").get_json()["labels"]
    assert labs == [{"id": "Label_2", "name": "Stories"}]                   # own labels only
    assert client.post("/api/mail/labels", json={"account_id": "acct_m", "name": "Leads"}).get_json()["label"]["id"] == "Label_9"
    r = client.post("/api/mail/modify", json={"account_id": "acct_m", "thread_ids": ["t3"], "add": ["Label_2"]}).get_json()
    assert "Label_2" in THREADS["t3"]
    client.post("/api/mail/modify/undo", json={"account_id": "acct_m", "changed": r["changed"]})
    assert "Label_2" not in THREADS["t3"]
    assert client.get("/api/mail/labels?account=acct_r").status_code == 403
