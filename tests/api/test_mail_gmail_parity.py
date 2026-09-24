"""Gmail parity for the Messages workspace: Delete (Trash, restorable),
spam, importance, mute, folders, unsubscribe, the original message and the
signature, and the rule that separates the owner's click from Friday's own
initiative.

* Delete moves a conversation to Gmail's Trash and undo takes it back out;
  nothing ever deletes permanently.
* Trash, spam and importance exist only in Gmail: on an account that has not
  allowed changes they are refused, and Friday's view does not pretend.
* The owner's own click acts at once. Friday asking for the same thing files
  an approval card and changes nothing until it is approved.
"""
import pytest

from agent_friday.services import gmail_mailbox as gm
from agent_friday.services import google_accounts as ga

THREADS: dict = {}
CALLS: list = []
MSG_HEADERS: dict = {}
RAW = (b"From: Ada Example <ada@example.test>\r\nSubject: Hello\r\n"
       b"List-Unsubscribe: <https://lists.example.test/u/1>\r\n\r\nBody text\r\n")


class _Req:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class _Svc:
    def users(self):
        class U:
            def threads(self):
                class T:
                    def get(self, userId=None, id=None, format=None):      # noqa: A002,N803
                        return _Req(lambda: {"id": id, "messages": [{"labelIds": sorted(THREADS[id])}]})

                    def modify(self, userId=None, id=None, body=None):     # noqa: A002,N803
                        def run():
                            CALLS.append(("modify", id, list(body["addLabelIds"]), list(body["removeLabelIds"])))
                            THREADS[id] = (THREADS[id] | set(body["addLabelIds"])) - set(body["removeLabelIds"])
                            return {"id": id}
                        return _Req(run)

                    def trash(self, userId=None, id=None):                 # noqa: A002,N803
                        def run():
                            CALLS.append(("trash", id))
                            THREADS[id] = THREADS[id] | {"TRASH"}
                            return {"id": id}
                        return _Req(run)

                    def untrash(self, userId=None, id=None):               # noqa: A002,N803
                        def run():
                            CALLS.append(("untrash", id))
                            THREADS[id] = THREADS[id] - {"TRASH"}
                            return {"id": id}
                        return _Req(run)

                    def delete(self, userId=None, id=None):                # noqa: A002,N803
                        raise AssertionError("permanent delete must never be called")
                return T()

            def messages(self):
                class M:
                    def get(self, userId=None, id=None, format=None, metadataHeaders=None):  # noqa: A002,N803
                        import base64
                        if format == "raw":
                            return _Req(lambda: {"raw": base64.urlsafe_b64encode(RAW).decode()})
                        return _Req(lambda: {"payload": {"headers": [{"name": k, "value": v} for k, v in MSG_HEADERS.items()]}})
                return M()

            def settings(self):
                class S:
                    def sendAs(self):                                      # noqa: N802
                        class A:
                            def list(self, userId=None):                   # noqa: N803
                                return _Req(lambda: {"sendAs": [
                                    {"sendAsEmail": "other@example.test", "signature": "<b>other</b>"},
                                    {"sendAsEmail": "me@example.test", "displayName": "Me", "isDefault": True,
                                     "signature": "<div>Me<br>Reporter</div>"}]})
                        return A()
                return S()

            def labels(self):
                class L:
                    def list(self, userId=None):                           # noqa: N803
                        return _Req(lambda: {"labels": [{"id": "INBOX", "name": "INBOX", "type": "system"},
                                                        {"id": "Label_2", "name": "Stories", "type": "user"}]})
                return L()
        return U()


@pytest.fixture(autouse=True)
def _fake(monkeypatch):
    THREADS.clear(); CALLS.clear(); MSG_HEADERS.clear()
    THREADS.update({"t1": {"INBOX", "UNREAD"}, "t2": {"INBOX", "IMPORTANT"}, "t3": {"INBOX"}})
    accts = {"acct_m": [ga.GMAIL_READ, ga.GMAIL_MODIFY], "acct_r": [ga.GMAIL_READ]}
    monkeypatch.setattr(ga, "get_account", lambda aid: {"id": aid, "scopes": accts.get(aid, [])} if aid in accts else None)
    monkeypatch.setattr(ga, "credentials_for", lambda aid: object())
    from agent_friday.services import gmail_read
    monkeypatch.setattr(gmail_read, "_service", lambda creds: _Svc())
    yield


@pytest.fixture
def state(monkeypatch):
    store = {"s": {}}
    from agent_friday.routes import messages as rm
    monkeypatch.setattr(rm, "_load_message_state", lambda: {k: dict(v) for k, v in store["s"].items()})
    monkeypatch.setattr(rm, "_save_message_state", lambda st: store.__setitem__("s", {k: dict(v) for k, v in st.items()}))
    return store


def _items(*pairs):
    return [{"id": c, "account_id": a, "thread_id": t} for c, a, t in pairs]


# ── Delete = Trash, restorable ────────────────────────────────────────────

def test_trash_moves_to_gmail_trash_and_undo_restores():
    out = gm.apply_action("acct_m", ["t1"], "trash")
    assert out["changed"] == {"t1": {"trashed": True}} and "TRASH" in THREADS["t1"]
    gm.undo("acct_m", out["changed"])
    assert "TRASH" not in THREADS["t1"]
    assert [c[0] for c in CALLS] == ["trash", "untrash"]


def test_restore_from_trash_and_its_undo():
    THREADS["t3"].add("TRASH")
    out = gm.apply_action("acct_m", ["t3"], "untrash")
    assert "TRASH" not in THREADS["t3"]
    gm.undo("acct_m", out["changed"])
    assert "TRASH" in THREADS["t3"]


def test_trash_is_never_a_label_change():
    with pytest.raises(ValueError):
        gm.modify_threads("acct_m", ["t1"], add=["TRASH"])
    with pytest.raises(ValueError):
        gm.modify_threads("acct_m", ["t1"], add=["SPAM"])        # spam only as the spam action
    assert CALLS == []


def test_delete_through_the_route_changes_gmail_first_and_undo_restores(client, state):
    r = client.post("/api/messages/action", json={"requested_by": "ui:test",
        "ids": ["c1"], "action": "trash", "gmail": _items(("c1", "acct_m", "t1"))}).get_json()
    assert r["status"] == "ok" and r["gmail_status"] == {"acct_m": "synced"}
    assert "TRASH" in THREADS["t1"] and state["s"]["c1"]["trashed"] is True
    client.post("/api/messages/restore", json={"states": r["before"], "gmail_changes": r["gmail_changes"]})
    assert "TRASH" not in THREADS["t1"] and state["s"] == {}


def test_delete_on_a_read_only_account_is_refused_not_faked(client, state):
    r = client.post("/api/messages/action", json={"requested_by": "ui:test",
        "ids": ["c1", "c3"], "action": "trash",
        "gmail": _items(("c1", "acct_r", "t1"), ("c3", "acct_m", "t3"))}).get_json()
    assert r["ids"] == ["c3"]                                          # only the one Gmail moved
    assert "Reconnect" in r["not_changed"]["c1"]
    assert "c1" not in state["s"] and "TRASH" not in THREADS["t1"]
    r = client.post("/api/messages/action", json={"requested_by": "ui:test", "ids": ["c1"], "action": "trash",
                                                    "gmail": _items(("c1", "acct_r", "t1"))}).get_json()
    assert r["status"] == "error" and "Reconnect" in r["message"]


def test_delete_without_knowing_the_account_is_refused(client, state):
    r = client.post("/api/messages/action", json={"requested_by": "ui:test", "ids": ["c9"], "action": "trash"}).get_json()
    assert r["status"] == "error" and state["s"] == {}


# ── spam, importance, mute ────────────────────────────────────────────────

def test_report_spam_and_not_spam(client, state):
    r = client.post("/api/messages/action", json={"requested_by": "ui:test", "ids": ["c1"], "action": "spam",
                                                  "gmail": _items(("c1", "acct_m", "t1"))}).get_json()
    assert r["status"] == "ok" and "SPAM" in THREADS["t1"] and "INBOX" not in THREADS["t1"]
    client.post("/api/messages/restore", json={"states": r["before"], "gmail_changes": r["gmail_changes"]})
    assert "SPAM" not in THREADS["t1"] and "INBOX" in THREADS["t1"]


def test_importance_is_gmails_label(client, state):
    client.post("/api/messages/action", json={"requested_by": "ui:test", "ids": ["c2"], "action": "unimportant",
                                              "gmail": _items(("c2", "acct_m", "t2"))})
    assert "IMPORTANT" not in THREADS["t2"]


def test_mute_is_friday_only_and_hides_the_conversation(client, state):
    r = client.post("/api/messages/action", json={"requested_by": "ui:test", "ids": ["c1"], "action": "mute",
                                                  "gmail": _items(("c1", "acct_r", "t1"))}).get_json()
    assert r["status"] == "ok" and state["s"]["c1"]["muted"] is True and CALLS == []


# ── the permission rule ───────────────────────────────────────────────────

def test_friday_asking_to_delete_files_an_approval_and_changes_nothing(client, state, monkeypatch):
    made = {}
    from agent_friday.services import approvals as ap
    monkeypatch.setattr(ap, "create_approval", lambda **kw: made.update(kw) or {"approval_id": "ap1", "status": "pending"})
    r = client.post("/api/messages/action", json={
        "ids": ["c1"], "action": "trash", "requested_by": "agent:inbox-cleanup", "reason": "old newsletters",
        "gmail": _items(("c1", "acct_m", "t1"))})
    assert r.status_code == 202 and r.get_json()["status"] == "pending_approval"
    assert CALLS == [] and state["s"] == {}
    assert made["force_gate"] is True and made["payload"]["action"] == "trash"
    assert "old newsletters" in made["action_description"]


def test_a_request_that_does_not_say_who_made_it_waits_for_approval(client, state, monkeypatch):
    from agent_friday.services import approvals as ap
    monkeypatch.setattr(ap, "create_approval", lambda **kw: {"approval_id": "ap3", "status": "pending"})
    r = client.post("/api/messages/action", json={"ids": ["c1"], "action": "trash", "gmail": _items(("c1", "acct_m", "t1"))})
    assert r.status_code == 202 and CALLS == [] and state["s"] == {}


def test_an_approved_proposal_runs_the_same_change(state):
    from agent_friday.services import mail_proposals as mp
    mp._on_decision({"status": "approved", "approval_id": "ap1", "payload": {
        "handler": "mail_proposal", "action": "trash", "ids": ["c1"],
        "gmail": _items(("c1", "acct_m", "t1"))}})
    assert "TRASH" in THREADS["t1"] and state["s"]["c1"]["trashed"] is True


def test_a_denied_or_foreign_card_does_nothing(state):
    from agent_friday.services import mail_proposals as mp
    base = {"handler": "mail_proposal", "action": "trash", "ids": ["c1"], "gmail": _items(("c1", "acct_m", "t1"))}
    mp._on_decision({"status": "denied", "payload": base})
    mp._on_decision({"status": "approved", "payload": dict(base, handler="something_else")})
    assert CALLS == [] and state["s"] == {}


def test_the_owners_click_is_not_gated(client, state):
    r = client.post("/api/messages/action", json={"ids": ["c1"], "action": "archive", "requested_by": "ui:messages",
                                                  "gmail": _items(("c1", "acct_m", "t1"))})
    assert r.status_code == 200 and "INBOX" not in THREADS["t1"]


# ── folders ───────────────────────────────────────────────────────────────

def test_folder_queries():
    from agent_friday.routes.messages import folder_query
    assert folder_query("") == ""
    assert folder_query("trash") == "in:trash" and folder_query("sent") == "in:sent"
    assert folder_query("category:promotions") == "in:inbox category:promotions"
    assert folder_query('label:My "Stories"') == 'label:"My Stories"'
    assert folder_query("category:bogus") is None and folder_query("nope") is None


def test_a_folder_is_read_from_gmail_and_shows_everything_in_it(client, monkeypatch):
    from agent_friday.services import message_triage as mt
    seen = {}

    def fake_collect(limit_per_account=40, query=None, **kw):
        seen["q"] = query
        return {"messages": [{"id": "c1", "archived": True, "trashed": True, "snoozed_until": "", "lane": "career"}],
                "source": "merged", "errors": []}
    monkeypatch.setattr(mt, "collect", fake_collect)
    d = client.get("/api/messages?folder=trash&q=from:ada").get_json()
    assert seen["q"] == "in:trash from:ada" and d["folder"] == "trash" and len(d["messages"]) == 1
    d = client.get("/api/messages").get_json()
    assert d["messages"] == []                                   # put away: not in the triage view
    assert client.get("/api/messages?folder=bogus").status_code == 400


def test_labels_can_be_listed_on_a_read_only_account(client):
    assert client.get("/api/mail/labels?account=acct_r").get_json()["labels"] == [{"id": "Label_2", "name": "Stories"}]


# ── card state and "waiting for your reply" ───────────────────────────────

def test_cards_carry_gmail_state_and_awaiting_reply():
    from agent_friday.services import message_triage as mt
    card = {"id": "c1", "labels": ["INBOX", "STARRED", "CATEGORY_UPDATES"], "sender_email": "ada@example.test", "lane": "career"}
    mt._mailbox_state(card, {"list_unsubscribe": True}, {})
    assert card["flagged"] and not card["trashed"] and card["category"] == "updates" and card["list_unsubscribe"]
    local = {"id": "c2", "labels": [], "sender_email": "x@example.test", "lane": "career"}
    mt._mailbox_state(local, {}, {"c2": {"flagged": True, "trashed": True}})
    assert local["flagged"] and local["trashed"]                  # read offline: Friday's record stands in
    cards = [dict(card), {"id": "c3", "sender_email": "me@example.test", "lane": "career"},
             {"id": "c4", "sender_email": "no-reply@shop.example.test", "lane": "career"},
             {"id": "c5", "sender_email": "list@example.test", "lane": "subscriptions"},
             {"id": "c6", "sender_email": "bo@example.test", "lane": "family", "archived": True}]
    mt._mark_awaiting(cards, {"me@example.test"})
    assert [c["awaiting_reply"] for c in cards] == [True, False, False, False, False]
    assert cards[1]["from_me"] is True


# ── original, signature, unsubscribe ──────────────────────────────────────

def test_show_original_and_download(client):
    d = client.get("/api/mail/original?account=acct_r&message=m1").get_json()
    assert d["status"] == "ok" and ["Subject", "Hello"] in d["headers"] and "Body text" in d["source"]
    r = client.get("/api/mail/original?account=acct_r&message=m1&dl=1")
    assert r.mimetype == "message/rfc822" and r.data == RAW and ".eml" in r.headers["Content-Disposition"]


def test_signature_is_the_default_send_as(client):
    d = client.get("/api/mail/signature?account=acct_r").get_json()
    assert d["signature"] == "<div>Me<br>Reporter</div>" and d["email"] == "me@example.test"


def test_unsubscribe_header_parsing():
    from agent_friday.services import mail_unsubscribe as mu
    w = mu.parse_header("<mailto:leave@list.example.test?subject=stop>, <https://list.example.test/u?x=1>, <http://insecure.example.test/u>")
    assert w == {"https": ["https://list.example.test/u?x=1"], "mailto": ["mailto:leave@list.example.test?subject=stop"]}


def test_one_click_unsubscribe_posts_once_to_a_public_host(client, monkeypatch):
    from agent_friday.services import mail_unsubscribe as mu
    import socket
    MSG_HEADERS.update({"From": "News <news@list.example.test>",
                        "List-Unsubscribe": "<https://list.example.test/u/1>",
                        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"})
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
    posts = []

    class R:
        status_code = 200
    import requests
    monkeypatch.setattr(requests, "post", lambda url, **kw: posts.append((url, kw)) or R())
    opt = client.get("/api/mail/unsubscribe?account=acct_r&message=m1").get_json()
    assert opt["method"] == "one_click" and opt["host"] == "list.example.test"
    assert client.post("/api/mail/unsubscribe", json={"account_id": "acct_r", "message_id": "m1", "requested_by": "ui:test"}).status_code == 400
    d = client.post("/api/mail/unsubscribe", json={"account_id": "acct_r", "message_id": "m1", "confirmed": True, "requested_by": "ui:test"}).get_json()
    assert d["status"] == "done" and len(posts) == 1
    url, kw = posts[0]
    assert url == "https://list.example.test/u/1" and kw["data"] == "List-Unsubscribe=One-Click" and kw["allow_redirects"] is False
    assert mu._public_host("https://list.example.test/u/1")


def test_unsubscribe_never_posts_into_the_local_network(monkeypatch):
    from agent_friday.services import mail_unsubscribe as mu
    import socket
    import requests
    MSG_HEADERS.update({"List-Unsubscribe": "<https://intranet.example.test/u>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"})
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("192.168.1.10", 443))])
    monkeypatch.setattr(requests, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not post")))
    d = mu.unsubscribe("acct_r", "m1")
    assert d["status"] == "error" and "public" in d["message"]


def test_mailto_unsubscribe_hands_over_to_the_composer(client):
    MSG_HEADERS.update({"List-Unsubscribe": "<mailto:leave@list.example.test?subject=unsubscribe%20me>"})
    d = client.post("/api/mail/unsubscribe", json={"account_id": "acct_r", "message_id": "m1", "confirmed": True, "requested_by": "ui:test"}).get_json()
    assert d["status"] == "next" and d["method"] == "mailto"
    assert d["mailto"] == {"to": "leave@list.example.test", "subject": "unsubscribe me", "body": "unsubscribe"}


def test_friday_asking_to_unsubscribe_needs_approval(client, monkeypatch):
    from agent_friday.services import approvals as ap
    made = {}
    monkeypatch.setattr(ap, "create_approval", lambda **kw: made.update(kw) or {"approval_id": "ap2", "status": "pending"})
    r = client.post("/api/mail/unsubscribe", json={"account_id": "acct_r", "message_id": "m1", "requested_by": "agent:x",
                                                   "confirmed": True, "sender": "News"})
    assert r.status_code == 202 and made["payload"]["action"] == "unsubscribe" and "News" in made["title"]


def test_nothing_in_the_source_deletes_mail_permanently():
    """Delete means Trash. Gmail's permanent deletes are never called."""
    import pathlib
    import re
    src = pathlib.Path(__file__).resolve().parents[2] / "src"
    bad = re.compile(r"(threads|messages)\(\)\.(delete|batchDelete)\(")
    hits = [str(p) for p in src.rglob("*.py") if bad.search(p.read_text(encoding="utf-8", errors="replace"))]
    assert hits == []
