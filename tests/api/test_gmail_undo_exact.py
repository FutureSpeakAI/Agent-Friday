"""Undo for a mailbox change is exact per MESSAGE, not per conversation.

The fake Gmail here holds real conversations: several messages each, the owner's own
sent replies (no INBOX), a mix of read and unread, one starred. After a change and its
undo every message must carry exactly the labels it had before. A message that arrives
after the change is never touched, and when the per-message record could not be taken the
change still runs and undo says it cannot be exact instead of claiming success."""
import pytest

from agent_friday.services import action_journal as journal
from agent_friday.services import gmail_mailbox as gm
from agent_friday.services import google_accounts as ga
from agent_friday.services import item_actions as ia

MAIL: dict = {}          # message id -> {"thread": id, "labels": set}
CALLS: list = []         # ("get"|"thread_modify"|"batch"|"modify", ...)
FAIL_GET: set = set()    # thread ids whose read fails
GONE: set = set()        # message ids Gmail no longer has
ARRIVE: dict = {}        # thread id -> callback run right after that thread is read


class _Req:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


def _apply(m, body):
    m["labels"] = (m["labels"] | set(body["addLabelIds"])) - set(body["removeLabelIds"])


class _Svc:
    def users(self):
        class U:
            def threads(self):
                class T:
                    def get(self, userId=None, id=None, format=None):      # noqa: A002,N803
                        def run():
                            CALLS.append(("get", id))
                            if id in FAIL_GET:
                                raise RuntimeError("Gmail would not answer")
                            got = {"id": id, "messages": [{"id": k, "threadId": id, "labelIds": sorted(v["labels"])}
                                                          for k, v in MAIL.items() if v["thread"] == id]}
                            if id in ARRIVE:
                                ARRIVE.pop(id)()
                            return got
                        return _Req(run)

                    def modify(self, userId=None, id=None, body=None):     # noqa: A002,N803
                        def run():
                            CALLS.append(("thread_modify", id))
                            for v in MAIL.values():
                                if v["thread"] == id:
                                    _apply(v, body)
                            return {"id": id}
                        return _Req(run)
                return T()

            def messages(self):
                class M:
                    def batchModify(self, userId=None, body=None):         # noqa: N802,N803
                        def run():
                            CALLS.append(("batch", tuple(body["ids"]), tuple(body["addLabelIds"]),
                                          tuple(body["removeLabelIds"])))
                            if any(i in GONE or i not in MAIL for i in body["ids"]):
                                raise RuntimeError("Invalid id")
                            for i in body["ids"]:
                                _apply(MAIL[i], body)
                            return {}
                        return _Req(run)

                    def modify(self, userId=None, id=None, body=None):     # noqa: A002,N803
                        def run():
                            CALLS.append(("modify", id))
                            if id in GONE or id not in MAIL:
                                raise RuntimeError("Invalid id")
                            _apply(MAIL[id], body)
                            return {}
                        return _Req(run)
                return M()
        return U()


def _put(mid, thread, *labels):
    MAIL[mid] = {"thread": thread, "labels": set(labels)}


@pytest.fixture(autouse=True)
def _fake(monkeypatch):
    MAIL.clear(); CALLS.clear(); FAIL_GET.clear(); GONE.clear(); ARRIVE.clear()
    # t1: an old read message, the owner's sent reply (no INBOX), and a new unread starred one.
    _put("m1", "t1", "INBOX")
    _put("m2", "t1", "SENT")
    _put("m3", "t1", "INBOX", "UNREAD", "STARRED")
    # t2: both unread.   t3: a read message, then the owner's reply.
    _put("m4", "t2", "INBOX", "UNREAD")
    _put("m5", "t2", "INBOX", "UNREAD")
    _put("m6", "t3", "INBOX")
    _put("m7", "t3", "SENT")
    accts = {"acct_m": [ga.GMAIL_READ, ga.GMAIL_MODIFY]}
    monkeypatch.setattr(ga, "get_account", lambda aid: {"id": aid, "scopes": accts.get(aid, [])} if aid in accts else None)
    monkeypatch.setattr(ga, "credentials_for", lambda aid: object())
    from agent_friday.services import gmail_read
    monkeypatch.setattr(gmail_read, "_service", lambda creds: _Svc())


def _snapshot():
    return {k: set(v["labels"]) for k, v in MAIL.items()}


def _round_trip(action):
    before = _snapshot()
    out = gm.apply_action("acct_m", ["t1", "t2", "t3"], action)
    assert out["failed"] == {}
    mid = _snapshot()
    undone = gm.undo("acct_m", out["changed"])
    assert undone == {"failed": {}}
    return before, mid, _snapshot()


@pytest.mark.parametrize("action", ["archive", "read", "unread", "flag", "unflag", "important",
                                    "unimportant", "unarchive", "spam", "notspam"])
def test_every_label_action_round_trips_per_message(action):
    before, mid, after = _round_trip(action)
    assert after == before, "undo must put every message back exactly as it was"


def test_archive_changes_only_inbox_messages_and_undo_never_gives_a_sent_reply_inbox():
    before, mid, after = _round_trip("archive")
    assert all("INBOX" not in v for v in mid.values())
    assert after["m2"] == {"SENT"} and after["m7"] == {"SENT"}


def test_move_to_a_label_round_trips_and_leaves_sent_replies_without_inbox():
    before = _snapshot()
    out = gm.modify_threads("acct_m", ["t1", "t3"], ("Label_2",), ("INBOX",))
    mid = _snapshot()
    assert "Label_2" in mid["m2"] and "INBOX" not in mid["m1"]
    gm.undo("acct_m", out["changed"])
    after = _snapshot()
    assert after == before
    assert "INBOX" not in after["m2"] and "INBOX" not in after["m7"]


def test_label_and_unlabel_round_trip():
    before = _snapshot()
    out = gm.modify_threads("acct_m", ["t1", "t2"], ("Label_2",), ())
    assert gm.undo("acct_m", out["changed"]) == {"failed": {}}
    assert _snapshot() == before
    gm.modify_threads("acct_m", ["t1"], ("Label_2",), ())
    labelled = _snapshot()
    out = gm.modify_threads("acct_m", ["t1", "t2"], (), ("Label_2",))
    gm.undo("acct_m", out["changed"])
    assert _snapshot() == labelled


def test_mark_read_undo_restores_unread_only_on_the_messages_that_were_unread():
    out = gm.apply_action("acct_m", ["t1"], "read")
    assert "UNREAD" not in MAIL["m3"]["labels"]
    gm.undo("acct_m", out["changed"])
    assert "UNREAD" in MAIL["m3"]["labels"]
    assert "UNREAD" not in MAIL["m1"]["labels"] and "UNREAD" not in MAIL["m2"]["labels"]


def test_mark_unread_is_undoable_and_restores_the_per_message_state():
    before = _snapshot()
    out = gm.apply_action("acct_m", ["t1"], "unread")
    assert all("UNREAD" in MAIL[m]["labels"] for m in ("m1", "m2", "m3"))
    assert gm.undo("acct_m", out["changed"]) == {"failed": {}}
    assert _snapshot() == before
    assert {"m1", "m2"} == set(out["changed"]["t1"]["messages"]), "m3 was already unread: nothing recorded"


def test_a_message_that_arrives_after_the_change_is_not_touched_by_undo():
    out = gm.apply_action("acct_m", ["t1"], "read")
    _put("m9", "t1", "INBOX")                              # a reply lands, already read
    gm.undo("acct_m", out["changed"])
    assert MAIL["m9"]["labels"] == {"INBOX"}
    assert not any(c[0] == "batch" and "m9" in c[1] for c in CALLS)


def test_a_message_that_arrives_between_reading_and_changing_is_not_changed_either():
    # The change goes to the message ids that were read, not to the whole conversation.
    ARRIVE["t1"] = lambda: _put("m8", "t1", "INBOX", "UNREAD")
    gm.apply_action("acct_m", ["t1"], "archive")
    assert MAIL["m8"]["labels"] == {"INBOX", "UNREAD"}


def test_a_message_that_vanished_does_not_block_the_rest_of_the_undo():
    out = gm.apply_action("acct_m", ["t1", "t2"], "archive")
    GONE.add("m4")
    res = gm.undo("acct_m", out["changed"])
    assert set(res["failed"]) == {"t2"}
    assert "INBOX" in MAIL["m1"]["labels"] and "INBOX" in MAIL["m3"]["labels"] and "INBOX" in MAIL["m5"]["labels"]


def test_when_the_per_message_record_cannot_be_taken_the_change_runs_and_undo_says_so():
    FAIL_GET.add("t1")
    out = gm.apply_action("acct_m", ["t1", "t2"], "archive")
    assert out["failed"] == {}
    assert "INBOX" not in MAIL["m1"]["labels"] and "INBOX" not in MAIL["m4"]["labels"], "the action still ran"
    assert out["changed"]["t1"]["inexact"]
    FAIL_GET.clear()
    CALLS.clear()
    res = gm.undo("acct_m", out["changed"])
    assert res["failed"] == {"t1": gm.UNDO_INEXACT}
    assert "INBOX" not in MAIL["m1"]["labels"], "nothing was guessed for the unrecorded conversation"
    assert "INBOX" in MAIL["m4"]["labels"], "the recorded conversation was still put back"


def test_an_old_thread_level_record_is_not_guessed_at():
    legacy = {"t1": {"added": [], "removed": ["INBOX"]}}
    res = gm.undo("acct_m", legacy)
    assert list(res["failed"]) == ["t1"] and "exactly" in res["failed"]["t1"]
    assert gm.undo("acct_m", {"t2": {"added": [], "removed": []}}) == {"failed": {}}
    assert not [c for c in CALLS if c[0] != "get"]


def test_undo_email_tells_the_owner_when_a_conversation_cannot_be_undone_exactly(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "_path", lambda: tmp_path / "receipts.jsonl")
    journal.reset()
    FAIL_GET.add("t1")
    out = gm.apply_action("acct_m", ["t1", "t2"], "archive")
    FAIL_GET.clear()
    rec = journal.record("organize_email", "archive", "email", items=[{"id": "t1"}, {"id": "t2"}],
                         summary="Archived 2 conversations.", status="complete",
                         undo={"email": {"acct_m": out["changed"]}})
    back = ia.undo_email(rec)
    assert back["status"] == "partial"
    assert "can't be undone exactly" in back["summary"] and "Put back 1 conversation" in back["summary"]
    assert journal.get(rec["receipt_id"])["undone"] is False
    journal.reset()


def test_api_calls_for_a_batch_of_25_conversations():
    for n in range(25):
        _put("a%d" % n, "ta%d" % n, "INBOX", "UNREAD")
        _put("b%d" % n, "ta%d" % n, "SENT")
    CALLS.clear()
    out = gm.apply_action("acct_m", ["ta%d" % n for n in range(25)], "archive")
    apply_calls = list(CALLS)
    CALLS.clear()
    gm.undo("acct_m", out["changed"])
    undo_calls = list(CALLS)
    assert [c[0] for c in apply_calls].count("get") == 25
    assert [c[0] for c in apply_calls].count("batch") == 1 and not any(c[0] == "thread_modify" for c in apply_calls)
    assert [c[0] for c in undo_calls] == ["batch"]
