"""Laya's safe writes on the live-bound chain: the conditions of the DA-3 re-review (403a4ada).

Each test names the condition it closes and fails without that fix:

* B1  undo is exact per MESSAGE over conversations of several messages, for all seven verbs;
      a batch that could not be put back exactly is refused, never run.
* B2  the safe-write card is voiced as the chain's fixed sealed line; a "yes" for a line that was not
      delivered (a speaker that said "done", a barge, silence, room audio) approves nothing.
* N1  when Laya refuses a request, no Laya-resolved rows go to the deeper mind for a verb that runs
      with no card on the model path (the router chain's CARDED_INTENTS rule).
* N2  the setting counts only as far as the owner's own Settings click recorded it.
* N3  the screen read and the encoder rank sit inside one bounded rung: a cold encoder cannot hold a turn.
* N4  a card is bound to the messages it listed; a reply that arrives before approval is not changed.
* N5  a card Laya raises names the sender's ADDRESS, not only the display name.
* N6  the typed-chat sealed line carries a row sample.
* N7  a ticket with no conversation binds to nothing.
* N8  typed-chat safe writes ride trusted_surface, not reflex_open_enabled.
* N9  a Laya batch whose conversation could not be read first is refused (undo must be exact).

Second review (CRITIC_LANE_0c8723e3): M1 the scope check never takes a read or a select; M3 every narrowing
narrows the owner-enable record, a failed write fails closed, a replayed record verifies as nothing, and the
record folder is denied to the file tools; M4 the turn path never reads the keychain; L1 no sender address in a
readback, a spoken line, a tool result or chat history; L3 the Settings page shows the effective value.

Everything is synthetic: no model, no network, no real mailbox.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from agent_friday.routes import voice as rv
from agent_friday.services import agent, approvals as ap, gmail_api
from agent_friday.services import gmail_mailbox as gm
from agent_friday.services import guarded_settings as gs
from agent_friday.services import item_actions as ia
from agent_friday.services import laya_router as lr
from agent_friday.services import laya_safe_writes as sw
from agent_friday.services import off_record, point_act as pa, voice_delivery
from tests.screen_fixtures import Page, ref, report
from tests.unit.test_laya_safe_writes import (  # noqa: F401  (fixtures are used by name)
    CONV, ON_CASES, _cards, _go, _mode, _pending, _route, _stage, chat, gmail, settings_file)
from tests.unit.test_voice_card_readback import CALL, _Call
from tests.desk_press_support import pressed      # "the click" is a verified press on this PC (services/desk_press)

_REAL = {n: getattr(gm, n) for n in ("apply_action", "modify_threads", "undo", "snapshot_messages")}
_REAL_LIST = ap.list_approvals
_REAL_PLAN = rv._voice_route_plan


# ── a Gmail with real conversations: several messages each, the owner's own sent replies ──────────

MAIL: dict = {}          # message id -> {"thread": id, "labels": set}
CALLS: list = []
FAIL_GET: set = set()    # thread ids whose read fails


class _Req:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


def _apply(m, body):
    m["labels"] = (m["labels"] | set(body["addLabelIds"])) - set(body["removeLabelIds"])


class _Svc:
    def users(self):
        return self

    def threads(self):
        svc = self

        class T:
            def get(self, userId=None, id=None, format=None, metadataHeaders=None):   # noqa: A002,N803
                def run():
                    CALLS.append(("get", id))
                    if id in FAIL_GET:
                        raise RuntimeError("Gmail would not answer")
                    return {"id": id, "messages": [
                        {"id": k, "threadId": id, "labelIds": sorted(v["labels"]), "internalDate": str(v.get("ts", 1000)),
                         "payload": {"headers": [{"name": "From", "value": v.get("from", "Sender <sender@example.test>")}]}}
                        for k, v in MAIL.items() if v["thread"] == id]}
                return _Req(run)

            def modify(self, userId=None, id=None, body=None):              # noqa: A002,N803
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
            def batchModify(self, userId=None, body=None):                  # noqa: N802,N803
                def run():
                    CALLS.append(("batch", tuple(body["ids"])))
                    for i in body["ids"]:
                        _apply(MAIL[i], body)
                    return {}
                return _Req(run)

            def modify(self, userId=None, id=None, body=None):              # noqa: A002,N803
                def run():
                    CALLS.append(("modify", id))
                    _apply(MAIL[id], body)
                    return {}
                return _Req(run)
        return M()


def _put(mid, thread, *labels, frm="Sender <sender@example.test>", ts=1000):
    MAIL[mid] = {"thread": thread, "labels": set(labels), "from": frm, "ts": ts}


@pytest.fixture
def real_mail(gmail, monkeypatch):
    """The real mailbox code (per-message changes, snapshots, undo) over multi-message conversations."""
    MAIL.clear(); CALLS.clear(); FAIL_GET.clear()
    _put("m1", "t1", "INBOX")                              # an old read message
    _put("m2", "t1", "SENT")                               # the owner's own sent reply
    _put("m3", "t1", "INBOX", "UNREAD", "STARRED")         # the new unread starred one
    _put("m4", "t2", "INBOX", "UNREAD")
    _put("m5", "t2", "INBOX", "UNREAD")
    _put("m6", "t3", "INBOX")
    _put("m7", "t3", "SENT")
    for t in ("t4", "t5", "t6"):
        _put("x_" + t, t, "INBOX")
    for name, fn in _REAL.items():
        monkeypatch.setattr(gm, name, fn)
    monkeypatch.setattr(gm, "_svc", lambda acct: _Svc())
    return gmail


def _snap():
    return {k: set(v["labels"]) for k, v in MAIL.items()}


# ── B1: undo is exact per message, all seven verbs ───────────────────────────────────────────────

@pytest.mark.parametrize("text,action,label", ON_CASES, ids=[c[1] for c in ON_CASES])
def test_b1_every_verb_round_trips_exactly_over_multi_message_conversations(real_mail, monkeypatch, text, action, label):
    """Fails on the DA-3 mailbox (labels unioned over the thread, undo applied to the whole thread):
    an archive undo puts INBOX on the owner's sent replies, unstar re-stars every message, and a
    mark-read undo marks all of them unread."""
    stage = _stage(selected=(1, 2, 3))
    report(stage)
    Page(monkeypatch)
    before = _snap()
    _go(text, stage)
    assert _snap() == before, "nothing changes before the card is answered"
    card = _pending()[0]
    with pressed(): ap.decide(card["approval_id"], "approve", decided_by="owner:ui")
    rec = ia.wait_for(card["approval_id"], 10)
    assert rec["status"] == "complete", rec
    assert _snap() != before, "the approved change really happened, so the round trip is not vacuous (%s)" % action
    assert all(_snap()[k] == before[k] for k in before if k.startswith("x_")), "only the three rows move"
    out = ia.undo(rec["receipt_id"], conversation_id=CONV)
    assert out["status"] == "pending_approval"
    with pressed(): ap.decide(out["approval_id"], "approve", decided_by="owner:ui")
    undone = ia.wait_for(out["approval_id"], 10)
    assert undone["status"] == "complete", undone
    assert _snap() == before, "undo puts every MESSAGE back exactly (%s)" % action


def test_b1_a_sent_reply_never_gains_a_label_it_did_not_lose(real_mail, monkeypatch):
    stage = _stage(selected=(1, 3))
    report(stage)
    Page(monkeypatch)
    _go("archive these", stage)
    cid = _pending()[0]["approval_id"]
    with pressed(): ap.decide(cid, "approve", decided_by="owner:ui")
    rec = ia.wait_for(cid, 10)
    assert MAIL["m2"]["labels"] == {"SENT"} and MAIL["m1"]["labels"] == set() and MAIL["m3"]["labels"] == {"UNREAD", "STARRED"}
    out = ia.undo(rec["receipt_id"], conversation_id=CONV)
    with pressed(): ap.decide(out["approval_id"], "approve", decided_by="owner:ui")
    ia.wait_for(out["approval_id"], 10)
    assert MAIL["m2"]["labels"] == {"SENT"}, "undo never gives the owner's own reply an INBOX"
    assert MAIL["m1"]["labels"] == {"INBOX"} and MAIL["m3"]["labels"] == {"INBOX", "UNREAD", "STARRED"}


def test_b1_a_batch_that_cannot_be_read_when_proposed_makes_no_card(real_mail, monkeypatch):
    """No exact record of the messages, no card, and no 'you can undo it' promise."""
    stage = _stage(selected=(1, 2, 3))
    report(stage)
    Page(monkeypatch)
    FAIL_GET.add("t2")
    r = _route("archive these", stage)
    assert r.layer == "safe_write"
    out = agent._tool_organize_email(dict(r.args))
    assert out.startswith("NOT DONE") and "message by message" in out
    assert not _cards() and not [c for c in CALLS if c[0] in ("batch", "modify", "thread_modify")]


def test_n9_a_conversation_that_cannot_be_read_at_run_time_is_left_alone_not_changed_inexactly(real_mail, monkeypatch):
    """The chain's mailbox runs a change it could not snapshot and marks it inexact. A card that promises
    an exact undo refuses that conversation instead; the rest of the batch still runs exactly."""
    stage = _stage(selected=(1, 2, 3))
    report(stage)
    Page(monkeypatch)
    _go("archive these", stage)
    before = _snap()
    FAIL_GET.add("t2")                                      # readable when proposed, not when approved
    card = _pending()[0]
    with pressed(): ap.decide(card["approval_id"], "approve", decided_by="owner:ui")
    rec = ia.wait_for(card["approval_id"], 10)
    assert rec["status"] == "partial", rec
    by = {i["id"]: i for i in rec["items"]}
    assert by["t2"]["ok"] is False and by["t2"]["error"] == gm.NOT_EXACT_REFUSED
    assert MAIL["m4"]["labels"] == before["m4"] and MAIL["m5"]["labels"] == before["m5"], "t2 was not touched"
    assert MAIL["m1"]["labels"] != before["m1"] and MAIL["m6"]["labels"] != before["m6"], "t1 and t3 were"
    assert not [c for c in CALLS if c == ("thread_modify", "t2")], "no thread-level fallback"
    FAIL_GET.clear()
    out = ia.undo(rec["receipt_id"], conversation_id=CONV)
    with pressed(): ap.decide(out["approval_id"], "approve", decided_by="owner:ui")
    ia.wait_for(out["approval_id"], 10)
    assert _snap() == before


def test_n9_the_mailbox_default_still_runs_an_unreadable_conversation_and_says_inexact(real_mail):
    """The non-Laya path keeps the chain's behaviour; only exact_only refuses."""
    FAIL_GET.add("t2")
    ran = gm.modify_threads("acct_work", ["t2"], (), ("INBOX",))
    assert "t2" in ran["changed"] and ran["changed"]["t2"].get("inexact")
    FAIL_GET.add("t5")
    refused = gm.modify_threads("acct_work", ["t5"], (), ("INBOX",), exact_only=True)
    assert refused["changed"] == {} and refused["failed"] == {"t5": gm.NOT_EXACT_REFUSED}
    assert ("thread_modify", "t5") not in CALLS


# ── N4: the card is bound to the messages it listed ──────────────────────────────────────────────

def test_n4_a_reply_that_arrives_before_approval_is_not_changed(real_mail, monkeypatch):
    """Fails on the DA-3 run path, which acted on the thread id at approval time."""
    stage = _stage(selected=(1, 2, 3))
    report(stage)
    Page(monkeypatch)
    _go("archive these", stage)
    card = _pending()[0]
    assert "arrives before you approve is not changed" in card["description"] or \
        "arrives before you approve is not changed" in card["action_description"]
    assert set(card["payload"]["messages"]["acct_work"]["t1"]) == {"m1", "m2", "m3"}
    _put("m_new", "t1", "INBOX", "UNREAD")                  # a reply lands after the card, before the yes
    with pressed(): ap.decide(card["approval_id"], "approve", decided_by="owner:ui")
    rec = ia.wait_for(card["approval_id"], 10)
    assert rec["status"] == "complete"
    assert MAIL["m_new"]["labels"] == {"INBOX", "UNREAD"}, "the new reply was never on the card"
    assert "INBOX" not in MAIL["m1"]["labels"] and "INBOX" not in MAIL["m3"]["labels"]
    assert "m_new" not in rec["undo"]["email"]["acct_work"]["t1"]["messages"]
    out = ia.undo(rec["receipt_id"], conversation_id=CONV)
    with pressed(): ap.decide(out["approval_id"], "approve", decided_by="owner:ui")
    ia.wait_for(out["approval_id"], 10)
    assert MAIL["m1"]["labels"] == {"INBOX"} and MAIL["m2"]["labels"] == {"SENT"}
    assert MAIL["m3"]["labels"] == {"INBOX", "UNREAD", "STARRED"} and MAIL["m_new"]["labels"] == {"INBOX", "UNREAD"}


def test_n4_the_mailbox_changes_only_the_messages_it_was_given(real_mail):
    ran = gm.modify_threads("acct_work", ["t1"], (), ("INBOX",), only={"t1": ["m1"]}, exact_only=True)
    assert set(ran["changed"]["t1"]["messages"]) == {"m1"}
    assert MAIL["m3"]["labels"] == {"INBOX", "UNREAD", "STARRED"}
    other = gm.modify_threads("acct_work", ["t4"], (), ("INBOX",), only={"t1": ["m1"]}, exact_only=True)
    assert other["changed"] == {} and "t4" in other["failed"]


# ── N5 / N6: the address is shown; the typed line has a row sample ───────────────────────────────

def test_n5_a_card_names_the_senders_address_not_only_the_display_name(gmail, monkeypatch):
    """A display name is the sender's to choose: 'Mara' on a phishing mail looks like Mara. The card the
    owner reads on screen (its lines) shows the address; nothing else does (L1)."""
    for mid in ("m_t1_1", "m_t1_2", "m_t2_1", "m_t2_2"):
        gmail.senders[mid] = "Mara <billing@phish.example>"
    stage = _stage(selected=(1, 2))
    report(stage)
    Page(monkeypatch)
    r, out = _go("archive these", stage)
    pay = _pending()[0]["payload"]
    assert all("Mara <billing@phish.example>" in line for line in pay["lines"][:2]), pay["lines"]
    assert "phish.example" not in str(pay["preview"]), "the stored preview carries names only"
    assert "phish.example" not in out and "@" not in out, "the tool result (what a chat history keeps) has no address"


def test_n5_the_who_rule_card_shows_the_address_of_the_row_it_matched(gmail, monkeypatch):
    stage = _stage()
    stage["items"][3]["who"] = "Mara Lee"
    report(stage)
    Page(monkeypatch)
    r = _route("archive the ones from Mara", stage)
    assert r.layer == "safe_write", (r.decision, r.layer, r.reason)
    assert agent._tool_organize_email(dict(r.args)).startswith("CARD_RAISED")
    assert "<sender@example.test>" in _pending()[0]["payload"]["lines"][0]
    assert "sender@example.test" not in str(_pending()[0]["payload"]["preview"])


def test_n6_the_chat_sealed_line_carries_a_row_sample(chat, gmail, monkeypatch):
    from agent_friday.services import reflex_chat as rc
    stage = _stage(selected=(1, 2, 3))
    report(stage)
    Page(monkeypatch)
    out = rc._safe_write_first("archive these", {"source": rc.PAGE_SOURCE}, session_id="s1", conversation_id=CONV,
                               turn_id="t-1", authenticated=True, local=True)
    assert out["outcome"] == "card"
    assert "Subject" not in out["reply"] and "Sender" not in out["reply"] and "@" not in out["reply"]
    assert "archive 3 conversations" in out["reply"] and "Nothing changes until you press Approve on it" in out["reply"]
    assert "@" not in str(out["tool_trace"]), "what the chat history keeps (and a later cloud turn reads) has no address"


# ── N7: a ticket with no conversation binds to nothing ───────────────────────────────────────────

def test_n7_an_empty_conversation_id_mints_and_redeems_nothing(gmail, monkeypatch):
    stage = _stage()
    report(stage)
    r = lr.route("archive these", trusted=True, stage=stage, pointed=None, log=False, conversation_id="",
                 rung_budget_ms=5000)
    assert r.layer != "safe_write" and not sw._TICKETS
    p, why = sw.build(pa.PointAct("defer", "archive", ["mail:acct_work:t1"], "messages", args={
        "intent": "archive", "refs": ["mail:acct_work:t1"], "workspace": "messages", "rule": "deictic:selection"}))
    assert p is not None, why
    p.conversation_id = ""
    sw.mint(p)                                              # even a ticket minted by hand with no conversation...
    with pytest.raises(sw.Refusal):
        sw.redeem({"ticket": p.ticket}, action="archive", conversation_id="",
                  stage={"workspace": "messages", "items": [{"ref": "mail:acct_work:t1"}]})   # ...redeems nowhere


# ── N8: typed chat rides trusted_surface, not reflex_open_enabled ────────────────────────────────

def test_n8_a_typed_safe_write_does_not_need_the_open_the_x_opt_in(chat, gmail, monkeypatch):
    from agent_friday.services import reflex_chat as rc
    stage = _stage(selected=(1, 2))
    report(stage)
    Page(monkeypatch)
    assert rc.open_enabled({}) is False                     # the unrelated opt-in is NOT on
    out = rc.open_turn("archive these", {"source": rc.PAGE_SOURCE}, session_id="s1", conversation_id=CONV,
                       turn_id="t-1", settings={}, authenticated=True, local=True)
    assert out and out["outcome"] == "card" and len(_pending()) == 1 and gmail.changes() == []


def test_n8_off_untrusted_and_shadow_typed_lines_take_the_ordinary_turn(chat, gmail, monkeypatch):
    from agent_friday.services import reflex_chat as rc
    stage = _stage(selected=(1, 2))
    report(stage)
    Page(monkeypatch)
    ask = lambda data: rc.open_turn("archive these", data, session_id="s1", conversation_id=CONV, turn_id="t-1",
                                    settings={}, authenticated=True, local=True)
    assert ask({"source": "voice"}) is None, "not the page's own line"
    assert ask({"source": rc.PAGE_SOURCE, "voice_mode": True}) is None
    for m in ("off", "shadow"):
        _mode(monkeypatch, m)
        assert ask({"source": rc.PAGE_SOURCE}) is None, m
    assert not _cards() and gmail.changes() == []


# ── N1: a refusal never hands rows to the deeper mind for a verb that runs with no card ───────────

@pytest.mark.parametrize("text,n", [("label these as <b>x</b>", 3), ("star these", 26), ("mark these as read", 26),
                                    ("unstar these", 26), ("label these as x" + "y" * 60, 3),
                                    ("label these as Reply Now", 3), ("label these as Bora Bora", 3)])
def test_n1_a_refused_no_card_verb_goes_to_the_deeper_mind_with_the_words_alone(gmail, monkeypatch, text, n):
    """Fails on DA-3's ordering fix, which attached rule-picked rows after Laya refused: the deeper mind
    then ran organize_email(star|read|label) at once with no card."""
    from tests.screen_fixtures import item, newsletter_stage
    extra = [item(i + 10) for i in range(n)]
    stage = newsletter_stage(selected=[x["ref"] for x in extra], extra_items=extra)
    report(stage)
    r = _route(text, stage)
    assert r.layer != "safe_write" and r.decision == "defer", (r.decision, r.layer)
    assert not r.args.get("refs"), "no Laya-resolved rows ride along for a verb that raises no card"
    assert rv._deferred_request(text, r, local=True) == text
    assert not sw._TICKETS and not _cards()


def test_a_label_is_the_whole_phrase_the_owner_said_after_as_or_to():
    """Fails on 3869afe37: the label only had to appear somewhere in the words, so "Reply Now" (normalised to
    "reply", a trailing filler dropped) or "Bora Bora" (a doubled word collapsed) carded a shorter label."""
    said = sw._said_as_written
    assert not said("Reply", "label these as Reply Now")
    assert not said("Bora", "label these as Bora Bora")
    assert not said("b x b", "label these as <b>x</b>")
    assert not said("receipts", "label the receipts as taxes")                  # in the words, but not the label
    assert said("Receipts", "label these as Receipts.") and said("Reply Now", "label these as  reply now!")
    assert said("Trips to Rome", "move these to Trips to Rome") and said("receipts", "move these into receipts")


def test_n1_a_card_verb_keeps_its_rows_because_the_model_path_raises_a_card_for_it(gmail, monkeypatch):
    from tests.screen_fixtures import item, newsletter_stage
    extra = [item(i + 10) for i in range(sw.MAX_ROWS + 1)]
    stage = newsletter_stage(selected=[x["ref"] for x in extra], extra_items=extra)
    report(stage)
    r = _route("archive these", stage)                       # too many for Laya: the model's, WITH its card
    assert r.layer != "safe_write" and r.decision == "defer" and r.args.get("intent") == "archive"
    assert rv._deferred_request("archive these", r, local=True) != "archive these"
    assert rv._deferred_request("archive these", r, local=False) == "archive these", "never to a cloud deeper mind"


# ── N3: the encoder cannot block the turn ────────────────────────────────────────────────────────

def test_n3_a_cold_encoder_cannot_hold_the_safe_write_turn(gmail, monkeypatch):
    """Fails when the rank runs on the turn's thread: the turn waited for the whole embed."""
    import threading
    release = threading.Event()
    seen = []

    def cold_rank(q, items, embed=None):
        seen.append(q)
        release.wait(4.0)
        return [(0.9, items[0])] + [(0.1, it) for it in items[1:]]
    monkeypatch.setattr(pa, "rank", cold_rank)
    stage = _stage()
    report(stage)
    t0 = time.perf_counter()
    try:
        r = lr.route("archive the newsletter about taxes", trusted=True, stage=stage, pointed=None, log=False,
                     conversation_id=CONV)
        spent = time.perf_counter() - t0
    finally:
        release.set()
    assert spent < 2.0, "the turn waited %.1fs on the encoder" % spent
    assert r.layer != "safe_write" and r.decision != "tool" and not r.args.get("refs")
    assert not sw._TICKETS and not _cards()


def test_n3_the_safe_write_rung_is_bounded_and_falls_back_to_none(gmail, monkeypatch):
    import threading
    release = threading.Event()
    monkeypatch.setattr(pa, "plan", lambda *a, **k: release.wait(4.0))
    t0 = time.perf_counter()
    try:
        got = lr.safe_write_bounded("archive these", stage=_stage(), pointed=None, conversation_id=CONV, budget_ms=100)
    finally:
        release.set()
    assert got is None and time.perf_counter() - t0 < 1.5


# ── N2: the owner-enable record ──────────────────────────────────────────────────────────────────

_ANCHOR: dict = {}           # the keychain anchor, in memory


@pytest.fixture
def owner_record(tmp_path, monkeypatch):
    from agent_friday.governance import proof_of_integrity as poi
    nonce = {}
    monkeypatch.setattr(gs, "_dir", lambda: tmp_path / "runtime" / "guarded_settings")
    monkeypatch.setattr(gs, "_load_nonce", lambda: nonce.get("n"))
    monkeypatch.setattr(gs, "_store_nonce", lambda n: nonce.__setitem__("n", n))
    _ANCHOR.clear()
    monkeypatch.setattr(gs, "_anchor_get", lambda key: _ANCHOR.get(key))
    monkeypatch.setattr(gs, "_anchor_set", lambda key, n: _ANCHOR.__setitem__(key, n))
    monkeypatch.setattr(poi, "get_governance_key", lambda: b"k" * 32)
    gs.invalidate()
    gs._FORCE_OFF.clear()
    yield tmp_path
    gs.invalidate()
    gs._FORCE_OFF.clear()


def test_n2_only_the_owners_click_enables_it_and_a_hand_edit_does_not(settings_file, owner_record, monkeypatch):
    """Fails when the mode is read from settings.json alone: an edit of the file (or an approved file write
    to it) turned the setting on with no click."""
    import json
    core = settings_file
    assert sw.mode() == "off"
    sf = core.SETTINGS_FILE
    sf.write_text(json.dumps({"laya_safe_writes": "on"}), encoding="utf-8")        # a hand edit, Friday stopped
    core._invalidate_settings_cache()
    assert sw.mode() == "off", "no owner record: the file's word is only a claim"
    core._save_settings({"laya_safe_writes": "on"})                                  # a generic write cannot record it
    assert sw.mode() == "off" and gs.owner_choice("laya_safe_writes") == "off"
    core._save_settings({"laya_safe_writes": "on"}, owner_safe_writes_change=True)  # the Settings click
    assert sw.mode() == "on" and gs.owner_choice("laya_safe_writes") == "on"
    core._save_settings({"laya_safe_writes": "shadow"}, owner_safe_writes_change=True)
    sf.write_text(json.dumps({"laya_safe_writes": "on"}), encoding="utf-8")        # edited back up by hand
    core._invalidate_settings_cache()
    assert sw.mode() == "shadow", "the narrower of the file and the owner's last click"


def test_n2_a_forged_or_tampered_record_reads_as_off(settings_file, owner_record, monkeypatch):
    import json
    core = settings_file
    core._save_settings({"laya_safe_writes": "on"}, owner_safe_writes_change=True)
    path = gs._record_path()
    rec = json.loads(path.read_text(encoding="utf-8"))
    assert rec["laya_safe_writes"]["value"] == "on" and rec["laya_safe_writes"]["mac"]
    rec["laya_safe_writes"]["at"] += 1.0                                              # any change breaks the signature
    path.write_text(json.dumps(rec), encoding="utf-8")
    assert sw.mode() == "off", "an unverified stamp reads as off at once"
    assert gs.owner_choice("laya_safe_writes", block=True) == "off"
    path.write_text("not json", encoding="utf-8")
    assert gs.owner_choice("laya_safe_writes", block=True) == "off" and sw.mode() == "off"
    from agent_friday.governance import proof_of_integrity as poi
    core._save_settings({"laya_safe_writes": "on"}, owner_safe_writes_change=True)
    assert sw.mode() == "on"
    monkeypatch.setattr(poi, "get_governance_key", lambda: b"z" * 32)                 # another key signed it
    gs.invalidate()
    assert gs.owner_choice("laya_safe_writes", block=True) == "off" and sw.mode() == "off"


def test_n2_a_same_size_tamper_inside_one_clock_tick_reads_as_off(settings_file, owner_record):
    """A rewrite that keeps the verified file's size and mtime (two writes inside one filesystem
    clock tick, as on a fast machine) is still a different record: it reads as off at once, not
    as the verified "on" cached for the file it replaced."""
    import json
    import os
    core = settings_file
    core._save_settings({"laya_safe_writes": "on"}, owner_safe_writes_change=True)
    assert sw.mode() == "on"
    path = gs._record_path()
    before = os.stat(path)
    rec = json.loads(path.read_text(encoding="utf-8"))
    mac = rec["laya_safe_writes"]["mac"]
    rec["laya_safe_writes"]["mac"] = ("0" if mac[0] != "0" else "1") + mac[1:]        # same length, wrong
    path.write_text(json.dumps(rec), encoding="utf-8")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = os.stat(path)
    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)
    assert sw.mode() == "off", "a tampered record in the verified file's size and tick reads as off"
    assert gs.owner_choice("laya_safe_writes", block=True) == "off"


def test_n2_a_redeem_after_the_record_goes_is_refused(settings_file, owner_record):
    core = settings_file
    core._save_settings({"laya_safe_writes": "on"}, owner_safe_writes_change=True)
    assert sw.mode() == "on"
    gs._record_path().unlink()
    assert sw.mode() == "off"
    with pytest.raises(sw.Refusal) as e:
        sw.redeem({"ticket": "x"}, action="archive", conversation_id=CONV, stage={})
    assert "off" in str(e.value)


# ── B2: a "yes" for a line that was never delivered approves nothing ─────────────────────────────

class _SafeCall(_Call):
    """The chain's voice harness (real VoiceSession and FrontSeat over a fake mouth) with the REAL router
    rung and the REAL organize_email handler: the card is the one Laya's safe write raises."""

    def __init__(self, monkeypatch, stage, **kw):
        super().__init__(monkeypatch, **kw)
        monkeypatch.setattr(ap, "list_approvals", _REAL_LIST)
        monkeypatch.setattr(pa, "_default_stage", lambda: stage)
        monkeypatch.setattr(rv, "_voice_route_plan", lambda text, held, **k: _REAL_PLAN(
            text, held, route_fn=lambda t, **kk: lr.route(t, log=False, rung_budget_ms=5000, **kk), **k))
        monkeypatch.setattr(rv, "_local_voice_tool", self._tool2)
        self.front = {"contract": {"names": ["organize_email", "answer_card", "query_calendar", "delegate_to_friday"]}}

    def _tool2(self, name, args, send, session, cut=True, owner_call=False):
        self.ran.append((name, dict(args)))
        if name == "organize_email":
            tok = agent._CURRENT_CONVERSATION.set(CALL)
            try:
                return agent._tool_organize_email(dict(args))
            finally:
                agent._CURRENT_CONVERSATION.reset(tok)
        return "Recorded."

    def _generate(self, user_text, on_delta, cancel):
        return rv._run_routed_front_turn(
            user_text, on_delta, front=self.front, tool_session=self.tool_session, seat_turn=self._seat_turn,
            send=lambda o: None, session=lambda: self.session, owner_voice=True)


@pytest.fixture
def safe_call(gmail, monkeypatch):
    monkeypatch.setattr(off_record, "_settings", lambda: {})
    monkeypatch.setattr(voice_delivery, "settings_snapshot", lambda: {})
    monkeypatch.setattr(lr, "_write", lambda *a, **k: None)
    monkeypatch.setattr(ia, "_quiet", lambda: False)
    stage = _stage(selected=(1, 2, 3))
    report(stage)
    Page(monkeypatch)
    tok = agent._CURRENT_CONVERSATION.set(CALL)
    made = []

    def make(**kw):
        c = _SafeCall(monkeypatch, stage, **kw)
        made.append(c)
        return c
    yield make
    agent._CURRENT_CONVERSATION.reset(tok)
    for c in made:
        c.close()


def test_b2_the_safe_write_card_is_voiced_as_the_fixed_line_and_a_delivered_yes_answers_exactly_it(safe_call):
    c = safe_call(speaker_says="Done, all tidied.")
    c.say("archive these")
    spoken = " ".join(c.mouth.spoken)
    assert "Shall I archive 3 conversations?" in spoken and "Sender" in spoken
    assert "@" not in spoken, "names only are spoken, even outside room mode"
    assert "Done, all tidied." not in spoken and not c.model_calls, "the model's words never stand in for the question"
    card = _pending()[0]["approval_id"]
    c.say("yes")
    assert c.answered() == [("answer_card", {"card_id": card, "decision": "approve"})]


def test_b2_a_speaker_that_says_done_leaves_a_stray_yes_approving_nothing(safe_call):
    """Fails on DA-3's _cards_read_back: the card id in the result counted as voiced."""
    c = safe_call(fixed_line=False, speaker_says="Done, I've tidied those up.")
    c.say("archive these")
    assert len(_pending()) == 1 and not any("Shall I" in t for t in c.mouth.spoken)
    c.say("yes")
    assert c.answered() == [] and len(_pending()) == 1


@pytest.mark.parametrize("room", [False, True], ids=["owner alone", "room or TV audio"])
def test_b2_a_line_that_never_played_means_a_stray_yes_approves_nothing(safe_call, monkeypatch, room):
    c = safe_call(room=room)
    monkeypatch.setattr(c.mouth, "synthesize_stream", lambda text, cancel=None: iter(()))
    c.say("archive these")
    card = _pending()[0]["approval_id"]
    c.say("yes")                                              # the owner answering another thought, or a TV
    assert c.answered() == []
    assert ap.get_approval(card)["status"] == "pending"


def test_b2_a_yes_after_another_question_approves_nothing(safe_call):
    c = safe_call()
    c.say("archive these")
    c.say("what's on my calendar")
    c.say("yes")
    assert c.answered() == []


def test_b2_room_mode_line_names_no_sender_and_no_address(safe_call):
    c = safe_call(room=True)
    c.say("archive these")
    spoken = " ".join(c.mouth.spoken)
    assert "the list is on your screen" in spoken
    assert "sender@example.test" not in spoken and "Subject t1" not in spoken


# ── M1: the scope check never takes a read or a select ───────────────────────────────────────────

QUANTIFIED_READS = ["show me everything on my calendar today", "show me only the unread emails", "select all of them",
                    "pull up just the one from Dana", "open the other one", "show me all the emails",
                    "select everything", "show me everything from Dana"]


@pytest.mark.parametrize("text", QUANTIFIED_READS)
def test_m1_a_quantified_read_or_select_keeps_the_chains_decision_with_the_setting_on(gmail, monkeypatch, text):
    """Fails on 0c8723e34: the scope check ran for every verb, so a quantifier in a read or a select became a
    safe-write deferral (and the owner's words went to the deeper mind) instead of Friday's own rule."""
    stage = _stage(selected=(1, 2))
    report(stage)
    monkeypatch.setattr(pa, "_default_stage", lambda: stage)
    _mode(monkeypatch, "on")
    got = _route(text, stage)
    want = _route(text, stage, safe_writes=False)
    assert got.layer != "safe_write", (text, got.reason)
    assert (got.decision, got.tool, got.layer, got.args, got.question, got.handoff) == (
        want.decision, want.tool, want.layer, want.args, want.question, want.handoff), text
    assert not sw._TICKETS and not _cards()


def test_m1_the_diff_tool_carries_the_quantified_probes():
    from tools import laya_router_decision_diff as dd
    texts = [t for t, _kw in dd.PROBES]
    for t in ("show me everything on my calendar today", "show me only the unread emails", "select all of them"):
        assert t in texts


@pytest.mark.parametrize("text", ["archive all of them", "archive these not those", "star the rest"])
def test_m1_a_change_with_an_unclear_scope_is_still_a_row_less_defer(gmail, monkeypatch, text):
    stage = _stage(selected=(1, 2))
    report(stage)
    _mode(monkeypatch, "on")
    r = _route(text, stage)
    assert r.decision in ("defer", "ask") and r.tool is None and not r.args.get("refs"), (text, r.decision)
    assert not sw._TICKETS and not _cards()


# ── M3: every narrowing narrows the owner-enable record; a failed write fails closed; no replay ──

def _click(core, value):
    return core._save_settings({"laya_safe_writes": value}, owner_safe_writes_change=True)


def _hand_edit(core, value):
    import json
    core.SETTINGS_FILE.write_text(json.dumps({"laya_safe_writes": value}), encoding="utf-8")
    core._invalidate_settings_cache()


def test_m3_a_generic_save_that_narrows_narrows_the_record_so_a_file_edit_cannot_re_arm_it(settings_file, owner_record):
    """Fails on 0c8723e34: the record kept saying 'on' after a narrowing that was not the page's click."""
    core = settings_file
    _click(core, "on")
    assert sw.mode() == "on"
    core._save_settings({"laya_safe_writes": "off"})              # not the owner's click: narrowing only
    assert gs.owner_choice("laya_safe_writes", block=True) == "off"
    _hand_edit(core, "on")                                         # a hand edit, or an approved file write
    assert sw.mode() == "off"


def test_m3_the_command_line_and_the_wizard_narrow_the_record(settings_file, owner_record, monkeypatch, tmp_path):
    import types
    from agent_friday import cli
    from agent_friday import setup_wizard as wz
    core = settings_file
    for mod in (cli, wz):
        monkeypatch.setattr(mod, "SETTINGS_FILE", core.SETTINGS_FILE)
        monkeypatch.setattr(mod, "CONFIG_YAML", tmp_path / "config.yaml")
        monkeypatch.setattr(mod, "FRIDAY_DIR", tmp_path)
    _click(core, "on")
    cli.cmd_config(types.SimpleNamespace(config_cmd="set", key="laya_safe_writes", value=["off"]))
    assert gs.owner_choice("laya_safe_writes", block=True) == "off"
    _hand_edit(core, "on")
    assert sw.mode() == "off"
    _click(core, "on")
    wz._save_config({"laya_safe_writes": "shadow"})
    assert gs.owner_choice("laya_safe_writes", block=True) == "shadow"
    _hand_edit(core, "on")
    assert sw.mode() == "shadow"


def test_m3_a_boot_guard_rollback_narrows_the_record(settings_file, owner_record, tmp_path):
    import json
    core = settings_file
    _click(core, "on")
    restored = tmp_path / "restored.json"
    restored.write_text(json.dumps({"laya_safe_writes": "off", "x": 1}), encoding="utf-8")
    gs.clamp_file(restored, core.SETTINGS_FILE)
    assert gs.owner_choice("laya_safe_writes", block=True) == "off"
    restored.write_text(json.dumps({"x": 1}), encoding="utf-8")        # a snapshot that does not carry the key: the default
    _click(core, "on")
    gs.clamp_file(restored, None)
    assert gs.owner_choice("laya_safe_writes", block=True) == "off"


def test_m3_a_failed_record_write_fails_closed(settings_file, owner_record, monkeypatch):
    core = settings_file
    _click(core, "on")
    assert sw.mode() == "on"

    def boom(n):
        raise OSError("the protected store is not writable")
    monkeypatch.setattr(gs, "_store_nonce", boom)
    assert gs.narrow_record("laya_safe_writes", "shadow") is False       # could not keep the record honest...
    assert not gs._record_path().exists() and gs.owner_choice("laya_safe_writes", block=True) == "off"
    _hand_edit(core, "on")
    assert sw.mode() == "off", "...so it reads as off, never as the stale 'on'"
    assert _click(core, "on")["laya_safe_writes"] == "on"                # the file says on
    assert sw.mode() == "off", "a click whose record cannot be written does not enable it"


def test_m3_an_old_copy_of_the_record_is_not_a_record(settings_file, owner_record):
    """Replay: the owner clicked on, a copy was taken, the owner clicked off; writing the old copy back
    (an approved file write, a restored backup) must not bring the 'on' back."""
    import json
    core = settings_file
    _click(core, "on")
    old = gs._record_path().read_text(encoding="utf-8")
    _click(core, "off")
    gs._record_path().write_text(old, encoding="utf-8")
    _hand_edit(core, "on")
    assert gs.owner_choice("laya_safe_writes", block=True) == "off" and sw.mode() == "off"
    # each change rotates the nonce and raises the counter
    _click(core, "on")
    a = json.loads(gs._record_path().read_text(encoding="utf-8"))["laya_safe_writes"]["counter"]
    _click(core, "shadow")
    b = json.loads(gs._record_path().read_text(encoding="utf-8"))["laya_safe_writes"]["counter"]
    assert b > a


def test_m3_the_record_folder_is_denied_to_the_file_tools(settings_file, owner_record):
    from agent_friday.services import credential_paths as cred
    _click(settings_file, "on")
    assert cred.check(gs._record_path(), sniff=False), "the record is key-like: the file tools may not read or write it"
    assert cred.check(gs._dir() / "owner_enable.nonce", sniff=False)


# ── M4: the turn path never reads the keychain and is O(1) ───────────────────────────────────────

def test_m4_the_effective_setting_is_cached_and_the_turn_never_reads_the_keychain(settings_file, owner_record, monkeypatch):
    """Fails on 0c8723e34: mode() read the record file and called get_governance_key() (a keychain read) on
    every call, three or four times per voice turn."""
    import threading
    from agent_friday.governance import proof_of_integrity as poi
    core = settings_file
    _click(core, "on")
    keys, nonces = [], []
    real_key, real_nonce = poi.get_governance_key, gs._load_nonce
    monkeypatch.setattr(poi, "get_governance_key", lambda: keys.append(threading.get_ident()) or real_key())
    monkeypatch.setattr(gs, "_load_nonce", lambda: nonces.append(threading.get_ident()) or real_nonce())
    _hand_edit(core, "on")
    for _ in range(500):
        assert sw.mode() == "on"
        assert sw.is_card_route("organize_email", {"action": "archive", "laya_write": {"ticket": "x"}}) is False
    assert keys == [] and nonces == [], "500 turns, no keychain and no nonce read"


def test_m4_an_unverified_stamp_reads_off_at_once_and_is_verified_off_the_turn(settings_file, owner_record, monkeypatch):
    import threading
    import time
    from agent_friday.governance import proof_of_integrity as poi
    core = settings_file
    _click(core, "on")
    gs.invalidate()                                                    # e.g. a restart: nothing cached yet
    me = threading.get_ident()
    seen = []
    real_key = poi.get_governance_key
    monkeypatch.setattr(poi, "get_governance_key", lambda: seen.append(threading.get_ident()) or real_key())
    assert sw.mode() == "off", "fail closed on the first turn, not a keychain read"
    for _ in range(100):
        if sw.mode() == "on":
            break
        time.sleep(0.02)
    assert sw.mode() == "on" and seen and me not in seen, "verified on the background thread"


# ── L3: the Settings page shows the effective value ──────────────────────────────────────────────

def test_l3_the_settings_route_returns_the_effective_value_not_the_files_word(settings_file, owner_record):
    import json
    from flask import Flask
    from agent_friday.routes import core_routes as cr
    core = settings_file
    _hand_edit(core, "on")
    with Flask(__name__).test_request_context("/api/settings", method="GET"):
        data = json.loads(cr.api_settings().get_data(as_text=True))
    assert data["settings"]["laya_safe_writes"] == "on" and data["effective"]["laya_safe_writes"] == "off"
    _click(core, "on")
    with Flask(__name__).test_request_context("/api/settings", method="GET"):
        data = json.loads(cr.api_settings().get_data(as_text=True))
    assert data["effective"]["laya_safe_writes"] == "on"


@pytest.mark.parametrize("page", ["index.html", "ui_parts/app.html"])
def test_l3_the_panel_reads_the_effective_value(page):
    from tests.unit.test_laya_safe_writes import _panel
    _text, panel = _panel(page)
    assert ".effective" in panel and "only your own click here counts" in panel


# ══ Third pass (the fresh-critic conditions): R1 anchor, R2 notice, CLI note, L4 newer messages, L5 typed yes, L6 ticket ══

# ── R1: the counter is anchored outside the file pair ────────────────────────────────────────────

def test_r1_copying_back_an_old_record_and_its_nonce_file_reads_as_off(settings_file, owner_record):
    """Fails on f927387bc: record and nonce were one pair, so putting both back replayed the old 'on'."""
    core = settings_file
    _click(core, "on")
    old_record = gs._record_path().read_text(encoding="utf-8")
    old_nonce = gs._load_nonce()
    _click(core, "off")
    gs._record_path().write_text(old_record, encoding="utf-8")        # the whole pair, as it was
    gs._store_nonce(old_nonce)
    _hand_edit(core, "on")
    assert gs.owner_choice("laya_safe_writes", block=True) == "off"
    assert sw.mode() == "off"


def test_r1_an_unreadable_anchor_fails_closed(settings_file, owner_record, monkeypatch):
    core = settings_file
    _click(core, "on")
    assert sw.mode() == "on"

    def unreadable(key):
        raise OSError("the keychain did not answer")
    monkeypatch.setattr(gs, "_anchor_get", unreadable)
    gs.invalidate()
    assert gs.owner_choice("laya_safe_writes", block=True) == "off"
    assert _click(core, "on")["laya_safe_writes"] == "on"              # the file says on...
    assert sw.mode() == "off", "...and with no anchor to count against the click is not recorded"


def test_r1_the_anchor_is_read_only_when_a_record_is_verified_never_on_a_turn(settings_file, owner_record, monkeypatch):
    core = settings_file
    _click(core, "on")
    reads = []
    real = gs._anchor_get
    monkeypatch.setattr(gs, "_anchor_get", lambda key: reads.append(key) or real(key))
    _hand_edit(core, "on")
    for _ in range(200):
        assert sw.mode() == "on"
    assert reads == []


def test_r1_each_click_raises_the_anchor(settings_file, owner_record):
    core = settings_file
    _click(core, "on")
    a = _ANCHOR["laya_safe_writes"]
    _click(core, "shadow")
    assert _ANCHOR["laya_safe_writes"] == a + 1


def test_r1_the_anchor_file_is_denied_to_the_file_tools(settings_file, owner_record):
    from agent_friday.services import credential_paths as cred
    assert cred.check(gs._anchor_vault_path("laya_safe_writes").parent / ".guarded-anchor-laya_safe_writes", sniff=False)


# ── R2: the notice carries names only ────────────────────────────────────────────────────────────

def test_r2_the_tray_notice_for_a_laya_card_carries_names_only(gmail, monkeypatch):
    """Fails on f927387bc: the notice body was the card's description, whose lines name each address."""
    import agent_friday.notifications_engine as ne
    pushed = []
    monkeypatch.setattr(ne, "push", lambda **kw: pushed.append(kw))
    gmail.senders.update({"m_t1_1": "Mara <billing@phish.example>", "m_t1_2": "Mara <billing@phish.example>"})
    stage = _stage(selected=(1, 2))
    report(stage)
    Page(monkeypatch)
    _go("archive these", stage)
    card = _pending()[0]
    assert "billing@phish.example" in card["description"], "the card on screen does show the address"
    ap._notify_pending(card)
    assert pushed, "the notice was pushed"
    body = pushed[-1]["body"]
    assert "@" not in body and "<" not in body and "Subject" not in body and "Mara" not in body, body
    assert "Open Friday" in body


def test_r2_a_card_a_model_raised_is_notified_as_before(monkeypatch):
    import agent_friday.notifications_engine as ne
    pushed = []
    monkeypatch.setattr(ne, "push", lambda **kw: pushed.append(kw))
    ap._notify_pending({"approval_id": "apr_x", "title": "T", "description": "Subject — Name <a@b.example>",
                        "payload": {"handler": ia.HANDLER, "origin": ""}})
    assert pushed[-1]["body"] == "Subject — Name <a@b.example>"


# ── CLI: a write that cannot keep the key says so ────────────────────────────────────────────────

def test_cli_a_config_write_without_the_key_says_safe_writes_are_turned_off(settings_file, owner_record, monkeypatch,
                                                                              tmp_path, capsys):
    import json
    from agent_friday import cli
    core = settings_file
    monkeypatch.setattr(cli, "SETTINGS_FILE", core.SETTINGS_FILE)
    monkeypatch.setattr(cli, "CONFIG_YAML", tmp_path / "config.yaml")
    monkeypatch.setattr(cli, "FRIDAY_DIR", tmp_path)
    _click(core, "on")
    cli._save_config({"unrelated": 1})                              # config.yaml does not carry the key
    out = capsys.readouterr().out
    assert "laya_safe_writes" in out and "turned OFF" in out and "Settings" in out, out
    assert json.loads(core.SETTINGS_FILE.read_text(encoding="utf-8"))["laya_safe_writes"] == "off"
    cli._save_config({"unrelated": 2, "laya_safe_writes": "off"})
    assert "turned OFF" not in capsys.readouterr().out, "no note when the key was already off"


# ── L4: a message newer than the owner's screen is left out and named; each listed sender is shown ──

def test_l4_a_newer_message_is_left_out_and_the_card_says_so(real_mail, monkeypatch):
    """Fails on f927387bc: the snapshot took every message, including one that arrived after the screen."""
    import time
    stage = _stage(selected=(1, 2))
    report(stage)
    Page(monkeypatch)
    _put("m_new", "t1", "INBOX", "UNREAD", frm="Eve <eve@late.example>", ts=int(time.time() * 1000) + 60_000)
    r, out = _go("archive these", stage)
    card = _pending()[0]
    assert "1 newer message not included" in out, out
    assert "m_new" not in card["payload"]["messages"]["acct_work"]["t1"]
    assert card["payload"]["newer_not_included"] == 1
    # the card's own text on screen (its action line; the description is the fixed approve sentence and the rows)
    assert "newer message" in card["action_description"] and "left out" in card["action_description"]
    assert not any("eve@late.example" in line for line in card["payload"]["lines"]), "the left-out sender is not shown"
    with pressed(): ap.decide(card["approval_id"], "approve", decided_by="owner:ui")
    ia.wait_for(card["approval_id"], 10)
    assert MAIL["m_new"]["labels"] == {"INBOX", "UNREAD"}, "the newer message was never touched"
    assert "INBOX" not in MAIL["m1"]["labels"]


def test_l4_a_conversation_that_is_all_newer_is_left_out_whole(real_mail, monkeypatch):
    import time
    stage = _stage(selected=(1, 4))
    report(stage)
    Page(monkeypatch)
    MAIL["x_t4"]["ts"] = int(time.time() * 1000) + 60_000
    r, out = _go("archive these", stage)
    pay = _pending()[0]["payload"]
    assert pay["accounts"] == {"acct_work": ["t1"]} and pay["count"] == 1
    assert "1 newer message not included" in out


def test_l4_the_card_shows_the_sender_of_each_listed_message_not_the_first(real_mail, monkeypatch):
    stage = _stage(selected=(1,))
    report(stage)
    Page(monkeypatch)
    MAIL["m1"]["from"] = "Alice <alice@a.example>"
    MAIL["m2"]["from"] = "Me <me@work.example>"
    MAIL["m3"]["from"] = "Bob <bob@b.example>"
    r, out = _go("archive this", stage)
    pay = _pending()[0]["payload"]
    line = pay["lines"][0]
    assert "Alice <alice@a.example>" in line and "Bob <bob@b.example>" in line and "Me <me@work.example>" in line, line
    assert "@" not in out and "@" not in str(pay["preview"]), "names only everywhere but the card's own lines"


# ── a card Laya raised is approved with its Approve button: no typed reply approves it ───────────

def _arm(chat_fixture_gmail, monkeypatch):
    """Raise a Laya card by the typed path (the reply is the notice; the card id is on the card only)."""
    from agent_friday.services import reflex_chat as rc
    stage = _stage(selected=(1, 2))
    report(stage)
    Page(monkeypatch)
    out = rc._safe_write_first("archive these", {"source": rc.PAGE_SOURCE}, session_id="s1", conversation_id=CONV,
                               turn_id="t-1", authenticated=True, local=True)
    assert out["outcome"] == "card"
    return _pending()[0]["approval_id"]


def _typed_yes(cid, words="yes", decision="approve"):
    toks = [agent._CURRENT_SURFACE.set("chat"), agent._CURRENT_CONVERSATION.set(CONV),
            agent._CURRENT_OWNER_TEXT.set(words)]
    try:
        return agent._tool_answer_card({"card_id": cid, "decision": decision})
    finally:
        agent._CURRENT_OWNER_TEXT.reset(toks[2])
        agent._CURRENT_CONVERSATION.reset(toks[1])
        agent._CURRENT_SURFACE.reset(toks[0])


TYPED_REPLIES = ["yes", "ok thanks", "fine", "show me", "please", "go ahead", "sounds good", "right", "open it"]


@pytest.mark.parametrize("words", TYPED_REPLIES)
def test_n_mj1_no_typed_reply_approves_a_laya_card(chat, gmail, monkeypatch, words):
    """Fails on 7f7706984: any of Friday's general yes-words ('ok thanks', 'fine', 'show me') typed as the very
    next message approved the card, and so did a model's answer_card call."""
    cid = _arm(gmail, monkeypatch)
    res = _typed_yes(cid, words)
    assert res == ia.LAYA_CARD_BUTTON and "Approve button" in res
    assert ia.answer_card(cid, "approve", words, surface="chat")["text"] == ia.LAYA_CARD_BUTTON
    assert ia.typed_decision(words, cid) is None, "typed_decision never approves a Laya card"
    assert ap.get_approval(cid)["status"] == "pending" and gmail.changes() == []


@pytest.mark.parametrize("words", TYPED_REPLIES)
def test_n_mj1_the_reflex_path_answers_no_card_from_a_typed_reply(chat, gmail, monkeypatch, words):
    from agent_friday.services import reflex_chat as rc
    cid = _arm(gmail, monkeypatch)
    assert rc.open_turn(words, {"source": rc.PAGE_SOURCE}, session_id="s1", conversation_id=CONV, turn_id="t-2",
                        settings={}, authenticated=True, local=True) is None
    assert not hasattr(rc, "take_armed_card") and not hasattr(rc, "note_user_turn")
    assert ap.get_approval(cid)["status"] == "pending"


def test_n_mj1_the_button_still_approves_and_a_typed_no_can_decline(chat, gmail, monkeypatch):
    cid = _arm(gmail, monkeypatch)
    with pressed(): ap.decide(cid, "approve", decided_by="owner:ui")                                         # the Approve button's route
    assert ia.wait_for(cid, 10)["status"] == "complete"
    gmail.calls.clear()
    stage = _stage(selected=(3, 4))
    report(stage)
    from agent_friday.services import reflex_chat as rc
    rc._safe_write_first("star these", {"source": rc.PAGE_SOURCE}, session_id="s1", conversation_id=CONV,
                         turn_id="t-3", authenticated=True, local=True)
    cid3 = _pending()[0]["approval_id"]
    assert "Recorded: declined" in _typed_yes(cid3, "no", "decline")
    assert ap.get_approval(cid3)["status"] == "denied" and gmail.changes() == []


def test_n_mj1_a_card_a_model_raised_keeps_its_typed_yes_rule(chat, gmail, monkeypatch):
    stage = _stage()
    report(stage)
    Page(monkeypatch)
    out = agent._tool_organize_email({"action": "archive", "selection": "screen"})
    assert out.startswith("CARD_RAISED")
    cid = _pending()[0]["approval_id"]
    assert ia.typed_decision("yes", cid) == "approve"
    assert "NOT RECORDED" not in _typed_yes(cid, "yes")


def test_n_mj1_the_notice_tells_the_owner_to_press_approve(chat, gmail, monkeypatch):
    from agent_friday.services import reflex_chat as rc
    stage = _stage(selected=(1, 2))
    report(stage)
    Page(monkeypatch)
    out = rc._safe_write_first("archive these", {"source": rc.PAGE_SOURCE}, session_id="s1", conversation_id=CONV,
                               turn_id="t-1", authenticated=True, local=True)
    assert "press Approve on it" in out["reply"] and "press Approve on it" in out["history_text"]


# ── n-3: turning the setting down expires the cards still waiting ──────────────────────────────

def test_n3_turning_the_setting_off_expires_every_pending_laya_card_and_only_those(chat, gmail, settings_file, owner_record,
                                                                                     monkeypatch):
    """Fails on 7f7706984: a card raised under 'on' stayed approvable after the owner turned it off."""
    core = settings_file
    laya = _arm(gmail, monkeypatch)
    other = _stage(selected=(3, 4))
    report(other)
    model_card = agent._tool_organize_email({"action": "archive", "selection": "screen"})
    assert model_card.startswith("CARD_RAISED"), model_card
    pend = {c["approval_id"]: c for c in _pending()}
    mine = [i for i, c in pend.items() if c["payload"].get("origin") == "laya_safe_write"]
    others = [i for i in pend if i not in mine]
    assert laya in mine and others
    core._save_settings({"laya_safe_writes": "off"})
    assert ap.get_approval(laya)["status"] == "expired"
    assert all(ap.get_approval(i)["status"] == "pending" for i in others), "a card a model raised is untouched"
    assert ia.answer_card(laya, "approve", "yes", surface="chat")["ok"] is False


def test_n3_a_file_edited_down_expires_them_too(chat, gmail, settings_file, owner_record, monkeypatch):
    core = settings_file
    laya = _arm(gmail, monkeypatch)
    _click(core, "on")
    _hand_edit(core, "off")
    gs.follow_file("laya_safe_writes", "off")
    import time
    for _ in range(150):
        if ap.get_approval(laya)["status"] == "expired":
            break
        time.sleep(0.02)
    assert ap.get_approval(laya)["status"] == "expired"


# ── L6: the spent ticket is stored nowhere ───────────────────────────────────────────────────────

def test_l6_the_chat_trace_does_not_keep_the_ticket(chat, gmail, monkeypatch):
    from agent_friday.services import reflex_chat as rc
    stage = _stage(selected=(1, 2))
    report(stage)
    Page(monkeypatch)
    out = rc._safe_write_first("archive these", {"source": rc.PAGE_SOURCE}, session_id="s1", conversation_id=CONV,
                               turn_id="t-1", authenticated=True, local=True)
    assert out["tool_trace"][0]["input"] == {"action": "archive"}
    assert "laya_write" not in str(out["tool_trace"]) and "ticket" not in str(out["tool_trace"])


def test_l6_the_voice_follow_up_memory_does_not_keep_the_ticket(safe_call):
    c = safe_call()
    c.say("archive these")
    assert "laya_write" not in str(c.tool_session.get("last_route")) and "ticket" not in str(c.tool_session)


# ══ Fresh-critic pass (6e50a52a4): MJ-1, MJ-2, MD-1, MD-3, mn-1, mn-3, mn-4, mn-6 ══

# ── MJ-1: rows Friday highlighted are never the owner's pointer ──────────────────────────────────

def _friday_pointed(monkeypatch):
    from agent_friday.services import desktop_bus
    desktop_bus.set_pointed("messages", [ref(4), ref(5), ref(6)])          # a model's screen_select


def test_mj1_rows_friday_pointed_at_are_not_what_these_means(gmail, monkeypatch):
    """Fails on 6e50a52a4: with nothing ticked, 'archive these' resolved to the rows Friday had highlighted
    and the card said 'the ones I just pointed at'."""
    stage = _stage(selected=())
    report(stage)
    _friday_pointed(monkeypatch)
    r = lr.route("archive these", trusted=True, stage=stage, log=False, conversation_id=CONV, rung_budget_ms=5000)
    assert r.decision != "tool", (r.decision, r.layer)
    assert not (set(r.args.get("refs") or []) & {ref(4), ref(5), ref(6)}), "no Friday-pointed row rides along"
    assert not sw._TICKETS and not _cards()


def test_mj1_with_fridays_numbers_on_screen_an_ordinal_is_asked_never_guessed(gmail, monkeypatch):
    """Fails on 7f7706984 (which counted down the screen and called the rows 'the rows you numbered')."""
    stage = _stage(selected=())
    report(stage)
    _friday_pointed(monkeypatch)
    for text in ("archive the second one", "archive the first two"):
        r = lr.route(text, trusted=True, stage=stage, log=False, conversation_id=CONV, rung_budget_ms=5000)
        assert (r.decision, r.layer) == ("ask", "safe_write"), (text, r.decision, r.layer, r.reason)
        assert r.question == sw.ORDINAL_QUESTION and not r.args and r.tool is None
        assert "Say it again with a number" in r.question and "“" not in r.question, "it needs no special reply"
    assert not sw._TICKETS and not _cards()


def test_mj1_with_no_numbers_on_screen_an_ordinal_counts_from_the_top_and_the_card_says_so(gmail, monkeypatch):
    stage = _stage(selected=())
    report(stage)
    Page(monkeypatch)
    r = lr.route("archive the first two", trusted=True, stage=stage, log=False, conversation_id=CONV,
                 rung_budget_ms=5000)
    assert r.layer == "safe_write" and r.decision == "tool"
    assert agent._tool_organize_email(dict(r.args)).startswith("CARD_RAISED")
    card = _pending()[0]
    assert card["payload"]["refs"] == [ref(1), ref(2)]
    assert card["payload"]["laya_rule"] == "the rows you counted from the top of your screen"
    assert "numbered" not in card["description"] and "numbered" not in card["action_description"]


def test_mj1_a_proposal_resolved_from_pointed_rows_is_refused_and_never_worded_as_the_owners(gmail):
    args = {"intent": "archive", "refs": [ref(1)], "workspace": "messages", "rule": "deictic:pointed"}
    p, why = sw.build(pa.PointAct("defer", "archive", [ref(1)], "messages", args=args))
    assert p is None and "model's" in why
    assert "deictic:pointed" not in sw._RULE_WORDS
    assert sw.rule_words("ordinals:ordinals") == "the rows you counted from the top of your screen"
    assert "numbered" not in sw.rule_words("ordinals:ordinals") and "numbered" not in sw.rule_words("who")


# ── MJ-2: the history keeps no title and no sender name ──────────────────────────────────────────

def test_mj2_the_typed_card_reply_is_shown_live_but_the_history_line_is_neutral(chat, gmail, monkeypatch):
    """Fails on 6e50a52a4: the reply with “subject” from sender was the line persisted into the chat history."""
    from agent_friday.services import reflex_chat as rc
    stage = _stage(selected=(1, 2))
    report(stage)
    Page(monkeypatch)
    out = rc._safe_write_first("archive these", {"source": rc.PAGE_SOURCE}, session_id="s1", conversation_id=CONV,
                               turn_id="t-1", authenticated=True, local=True)
    assert "Subject" not in out["reply"] and "Sender" not in out["reply"], "the notice names no row"
    h = out["history_text"]
    assert "Subject" not in h and "Sender" not in h and "@" not in h and "archive 2 conversations" in h
    assert "Subject" not in str(out["tool_trace"]) and "Sender" not in str(out["tool_trace"])


def test_mj2_which_sender_is_asked_live_and_kept_neutral(chat, gmail, monkeypatch):
    from agent_friday.services import reflex_chat as rc
    stage = _stage(selected=(1, 2))
    stage["cursor"] = {"ref": ref(4), "state": "locked", "age_s": 1.0}
    report(stage)
    out = rc._safe_write_first("archive these", {"source": rc.PAGE_SOURCE}, session_id="s1", conversation_id=CONV,
                               turn_id="t-1", authenticated=True, local=True)
    assert out["outcome"] == "ask" and out["history_text"] == rc.NEUTRAL_ASK


def test_mj2_the_route_persists_the_neutral_line_not_the_live_reply(chat, gmail, monkeypatch):
    import json
    from flask import Flask
    from agent_friday.routes import chat as chat_routes
    from agent_friday.services import reflex_chat as rc
    stage = _stage(selected=(1, 2))
    report(stage)
    Page(monkeypatch)
    out = rc._safe_write_first("archive these", {"source": rc.PAGE_SOURCE}, session_id="s1", conversation_id=CONV,
                               turn_id="t-1", authenticated=True, local=True)
    saved = []
    monkeypatch.setattr(chat_routes, "_persist_turn", lambda cid, u, f: saved.append(f))
    monkeypatch.setattr(chat_routes, "_save_chat_history", lambda h: None)
    with Flask(__name__).app_context():
        resp = chat_routes._instant_response("archive these", out, CONV)
    body = json.loads(resp.get_data(as_text=True))
    assert "Subject" not in body["response"] and "archive 2 conversations" in body["response"]
    assert saved and saved[0]["text"] == out["history_text"] and "Subject" not in saved[0]["text"]


# ── MD-1: a file turned down narrows the record ──────────────────────────────────────────────────

def _wait_record(core, want="off"):
    import time
    for _ in range(150):
        if gs.owner_choice("laya_safe_writes", block=True) == want:
            return True
        time.sleep(0.02)
    return False


def test_md1_a_hand_edit_down_turns_the_record_down_so_a_later_write_of_on_does_not_re_arm(settings_file, owner_record):
    """Fails on 6e50a52a4: only a save through core narrowed the record; an edit of the file left it at 'on'."""
    core = settings_file
    _click(core, "on")
    assert sw.mode() == "on"
    _hand_edit(core, "off")                                         # Friday stopped, the file edited down
    assert sw.mode() == "off"                                       # this read notices and narrows the record
    assert _wait_record(core)
    _hand_edit(core, "on")                                          # weeks later an approved file write says on
    assert sw.mode() == "off" and gs.owner_choice("laya_safe_writes", block=True) == "off"


def test_md1_a_file_turned_to_shadow_narrows_to_shadow(settings_file, owner_record):
    core = settings_file
    _click(core, "on")
    _hand_edit(core, "shadow")
    assert sw.mode() == "shadow" and _wait_record(core, "shadow")
    _hand_edit(core, "on")
    assert sw.mode() == "shadow"


def test_md1_if_the_record_cannot_be_turned_down_it_is_removed_and_reads_off(settings_file, owner_record, monkeypatch):
    core = settings_file
    _click(core, "on")

    def boom(n):
        raise OSError("not writable")
    monkeypatch.setattr(gs, "_store_nonce", boom)
    _hand_edit(core, "off")
    assert sw.mode() == "off"
    import time
    for _ in range(150):
        if not gs._record_path().exists():
            break
        time.sleep(0.02)
    assert not gs._record_path().exists()
    _hand_edit(core, "on")
    assert sw.mode() == "off"


# ── the palette reports a raised card as a card ───────────────────────────────────────────────────

def test_md3_a_palette_request_reports_a_card_as_a_card_not_done(chat, gmail, monkeypatch):
    import json
    from flask import Flask
    from agent_friday.routes import core_routes as cr
    from agent_friday.services import reflex_chat as rc
    monkeypatch.setattr(cr, "_load_settings", lambda: {"reflex_open_enabled": True})
    monkeypatch.setattr(rc, "trusted_surface", lambda data, text, **kw: True)
    monkeypatch.setattr(rc, "open_turn", lambda *a, **k: {"outcome": "card", "reply": "waiting on your card"})
    body = {"text": "open the budget", "source": "app_page", "conversation_id": CONV}
    with Flask(__name__).test_request_context("/api/reflex/open", method="POST", json=body):
        resp = cr.reflex_open()
    assert json.loads(resp.get_data(as_text=True))["status"] == "card"


# ── mn-1: a display name that is itself an address ───────────────────────────────────────────────

def test_mn1_a_display_name_that_is_an_address_shows_its_local_part_only(real_mail, monkeypatch):
    stage = _stage(selected=(1,))
    report(stage)
    Page(monkeypatch)
    MAIL["m1"]["from"] = '"bob@evil.example" <bob@evil.example>'
    r, out = _go("archive this", stage)
    pay = _pending()[0]["payload"]
    assert "@" not in str(pay["preview"]) and "@" not in out
    assert "bob <bob@evil.example>" in pay["lines"][0], "the card on screen still shows the address"


# ── mn-3: no shadow rows off the record; the text hash is keyed ──────────────────────────────────

def test_mn3_no_safe_write_shadow_row_is_written_off_the_record(gmail, monkeypatch, tmp_path):
    from agent_friday.services import off_record
    _mode(monkeypatch, "shadow")
    monkeypatch.setattr(off_record, "active", lambda settings=None: True)
    stage = _stage(selected=(1, 2))
    report(stage)
    _route("archive these", stage)
    assert not (tmp_path / "runtime" / "laya_safe_writes").exists()


def test_mn3_the_head_shadow_row_hash_is_keyed_and_off_the_record_writes_nothing(gmail, monkeypatch, tmp_path):
    import hashlib
    from agent_friday.services import laya_router_head as lh
    from agent_friday.services import off_record
    final = lr.Route("no_tool")
    row = lh.shadow_row("star these", None, final, None, "voice", 0.9)
    assert row["text_sha"] != hashlib.sha256(b"star these").hexdigest()[:16]
    assert row["text_sha"] == sw.keyed_hash("star these", 16)
    monkeypatch.setattr(lh, "available", lambda profile=lh.DEFAULT_PROFILE: True)
    monkeypatch.setattr(off_record, "active", lambda settings=None: True)
    assert lh.shadow("star these", final, None, "voice", sync=True) is False


def test_mn3_the_learning_hash_is_keyed(gmail):
    import hashlib
    from agent_friday.services import laya_router_learn as ll
    h = ll._sha("archive these")
    assert len(h) == 64 and h != hashlib.sha256(b"archive these").hexdigest() and h == ll._sha("archive these")


# ── mn-4: a safe-write turn read the screen ──────────────────────────────────────────────────────

def test_mn4_safe_write_is_a_screen_layer_for_learning_and_chat_routing():
    from agent_friday.services import chat_tool_routing as ctr
    from agent_friday.services import laya_personal as lp
    assert "safe_write" in lp.SCREEN_LAYERS and "safe_write" in ctr._SCREEN_LAYERS


# ── MD-4: the docs say what the code does ───────────────────────────────────────────────────────

def test_md4_the_docs_match_the_code():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    flat = lambda name: " ".join((root / name).read_text(encoding="utf-8").split())
    mail, known, log = flat("docs/user-guide/mail.md"), flat("KNOWN_ISSUES.md"), flat("CHANGELOG.md")
    assert "left to the model's path" not in mail and "left to the model's path" not in known
    assert "she refuses it" in mail and "is refused" in known
    assert "Undo puts each message back exactly as it was" not in mail and "Gmail filter" in mail
    assert "up to three senders" in mail
    for needle in ("Laya's safe writes", "reflex_approval_replies", "reflex_open_enabled", "chat_tool_routing",
                   "laya_tool_head", "Voice point-and-act"):
        assert needle in log, needle


# ══ Third review (7f7706984): N-MD-1, n-4, N-MJ-4, N-MD-3, n-1 ══

def test_n_md1_a_cold_cache_still_narrows_from_the_file(settings_file, owner_record):
    """Fails on 7f7706984: right after a start the cache is cold, follow_file saw 'off' for the record and did
    nothing, so a later write of 'on' counted."""
    core = settings_file
    _click(core, "on")
    gs.invalidate()                                   # a restart: nothing verified yet
    _hand_edit(core, "off")
    sw.mode()                                          # the first read after the start
    assert _wait_record(core), "the record was turned down even though the cache was cold"
    _hand_edit(core, "on")
    assert sw.mode() == "off"


def test_n_md1_boot_narrows_the_record_before_any_turn(settings_file, owner_record):
    core = settings_file
    _click(core, "on")
    gs.invalidate()
    gs.boot_narrow({"laya_safe_writes": "off"})
    assert gs.owner_choice("laya_safe_writes", block=True) == "off"
    _click(core, "on")
    gs.boot_narrow({})                                  # a missing key is the default, off
    assert gs.owner_choice("laya_safe_writes", block=True) == "off"
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "src" / "agent_friday" / "server.py").read_text(encoding="utf-8")
    assert "boot_narrow" in src, "the server calls it at start"


def test_n4_follow_file_never_undoes_a_fresher_owner_click(settings_file, owner_record, monkeypatch):
    """Fails on 7f7706984: a narrowing decided on an old read of the file ran after the owner's new ON click and
    turned the record back down."""
    import types
    core = settings_file
    _click(core, "on")
    _hand_edit(core, "off")
    ran = []

    class Held:
        def __init__(self, target=None, **kw):
            ran.append(target)

        def start(self):
            pass
    real_threading = gs._threading
    monkeypatch.setattr(gs, "_threading", types.SimpleNamespace(Thread=Held, RLock=real_threading.RLock))
    gs.follow_file("laya_safe_writes", "off")           # decided on the old file; the thread has not run yet
    assert len(ran) == 1
    monkeypatch.setattr(gs, "_threading", real_threading)
    _click(core, "on")                                  # the owner's fresher click
    assert sw.mode() == "on"
    ran[0]()                                            # the held narrowing now runs
    assert gs.owner_choice("laya_safe_writes", block=True) == "on" and sw.mode() == "on"


# ── N-MJ-4: voice keeps a neutral line ──────────────────────────────────────────────────────────

def test_n_mj4_the_spoken_card_line_is_said_aloud_but_the_history_keeps_a_neutral_line(safe_call, monkeypatch):
    """Fails on 7f7706984: the card line with subjects and sender names was saved as the agent's words."""
    saved = []
    monkeypatch.setattr(rv, "_persist_voice_turn", lambda u, a, conversation_id=None, provider=None:
                        saved.append((u, a, conversation_id, provider)) or True)
    c = safe_call(room=False)
    c.say("archive these")
    spoken = " ".join(c.mouth.spoken)
    assert "Sender" in spoken, "the card line is spoken with the senders' names"
    assert c.tool_session["history_text"] == rv._CARD_LINE_FALLBACK
    rv._persist_local_turn(c.tool_session, "archive these", spoken, CALL)
    assert saved == [("archive these", rv._CARD_LINE_FALLBACK, CALL, "local")]
    assert "Sender" not in saved[0][1] and "Subject" not in saved[0][1] and "history_text" not in c.tool_session


def test_n_mj4_a_question_built_from_the_screen_keeps_a_neutral_line(monkeypatch):
    saved = []
    monkeypatch.setattr(rv, "_persist_voice_turn", lambda u, a, conversation_id=None, provider=None:
                        saved.append(a) or True)

    def run(layer):
        plan = {"tool": None, "args": None, "ack": "", "label": "", "question": "Which sender: Sarah Jones or Sarah Lee?",
                "receipt": {"layer": layer}}
        monkeypatch.setattr(rv, "_voice_route_plan", lambda *a, **k: plan)
        ts = {"conversation_id": CALL, "spoken": []}
        rv._run_routed_front_turn("star the ones from Sarah", lambda s: None, front={"contract": {"names": []}},
                                  tool_session=ts, seat_turn=lambda u, od, p, rt, cl=None: p["question"],
                                  send=lambda o: None, session=lambda: None, owner_voice=True)
        rv._persist_local_turn(ts, "star the ones from Sarah", plan["question"], CALL)
    run("point_act")
    run("resolver")
    run("safe_write")
    assert saved == [rv._NEUTRAL_QUESTION] * 3
    run("rule")                                          # a question that names nothing from the screen is kept
    assert saved[-1] == "Which sender: Sarah Jones or Sarah Lee?"


# ── N-MD-3: the oracle's ON class is exact ──────────────────────────────────────────────────────

def test_n_md3_the_oracle_takes_its_write_labels_from_the_case_file_not_the_parser():
    from tools import laya_router_decision_diff as dd
    cases = dd.build_cases(Path(__file__).resolve().parents[2])
    labelled = {c["text"] for c in cases if c["write_referential"]}
    assert labelled == set(dd.WRITE_REFERENTIAL) and "archive these" in labelled
    assert not any(c["write_referential"] for c in cases if c["text"].startswith("show me"))
    assert "write_referential(" not in Path(dd.__file__).read_text(encoding="utf-8")


# ── n-1: the log key is sealed in the keystore, and no key means no hashed row ───────────────────

def test_n1_the_salt_is_sealed_by_the_keystore_and_denied_to_the_file_tools(gmail, monkeypatch, tmp_path):
    from agent_friday.services import credential_paths as cred
    from agent_friday.services import credential_store as cs
    calls = []
    monkeypatch.setattr(cs, "write_secret", lambda path, data, **kw: calls.append(("w", path)) or path.write_bytes(b"SEALED" + data))
    monkeypatch.setattr(cs, "read_secret", lambda path: calls.append(("r", path)) or path.read_bytes()[6:])
    sw._KEY_CACHE = None
    sw._KEY_RETRY_AT = 0.0
    salt = sw._salt()
    assert salt and len(salt) == 32 and calls and calls[0][0] == "w"
    path = sw.log_path().parent / "salt"
    assert path.read_bytes().startswith(b"SEALED"), "never plain on disk"
    assert cred.check(path, sniff=False)


def test_n1_no_key_means_no_hashed_row_and_no_throwaway_key(gmail, monkeypatch, tmp_path):
    from agent_friday.services import laya_router_head as lh
    from agent_friday.services import laya_router_learn as ll
    monkeypatch.setattr(sw, "_salt", lambda: None)
    sw._KEY_CACHE = None
    sw._KEY_RETRY_AT = 0.0
    assert sw.keyed_hash("archive these") == "" and sw._KEY_CACHE is None, "nothing is cached over a failed read"
    _mode(monkeypatch, "shadow")
    stage = _stage(selected=(1, 2))
    report(stage)
    _route("archive these", stage)
    assert not (tmp_path / "runtime" / "laya_safe_writes" / "shadow.jsonl").exists()
    assert ll._sha("x") == ""
    final = lr.Route("no_tool")
    wrote = []
    monkeypatch.setattr(lh, "log_path", lambda: tmp_path / "head.jsonl")
    lh._write_shadow(lh.shadow_row("star these", None, final, None, "voice", 0.9))
    assert not (tmp_path / "head.jsonl").exists() and not wrote
    sw._KEY_RETRY_AT = 0.0


# ══ Third review of three (da66aaa40) ══

# ── F3-MD-4: the chat notice and the tray body name no subject and no sender ─────────────────────

def test_f3_md4_the_chat_notice_and_its_history_line_name_no_subject_and_no_sender(chat, gmail, monkeypatch):
    """Fails on da66aaa40: the live chat reply carried “subject” from name."""
    from agent_friday.services import reflex_chat as rc
    gmail.senders.update({"m_t1_1": "Mara Quinn <m@q.example>", "m_t1_2": "Mara Quinn <m@q.example>"})
    stage = _stage(selected=(1, 2))
    report(stage)
    Page(monkeypatch)
    out = rc._safe_write_first("archive these", {"source": rc.PAGE_SOURCE}, session_id="s1", conversation_id=CONV,
                               turn_id="t-1", authenticated=True, local=True)
    for text in (out["reply"], out["history_text"], str(out["tool_trace"])):
        assert "Subject" not in text and "Mara" not in text and "Sender" not in text and "@" not in text, text
    assert "press Approve on it" in out["reply"] and out["reply"] == out["history_text"]


# ── F3-MJ-1: a Laya card is approved only by the screen button or the owner's voice ──────────────

@pytest.mark.parametrize("who", ["owner:sms", "owner", "owner:chat", "owner:voice", "friday", "system:schedule"])
def test_f3_mj1_no_other_channel_approves_a_laya_card(chat, gmail, monkeypatch, who):
    """Fails on da66aaa40: the screen-only rule was by kind, so any decided_by could approve a Laya card."""
    cid = _arm(gmail, monkeypatch)
    rec, won = ap.decide_with_outcome(cid, "approve", decided_by=who)
    assert won is False and rec["status"] == "pending" and gmail.changes() == []


@pytest.mark.parametrize("who", ["owner:ui", "owner:/voice/"])
def test_f3_mj1_the_button_and_the_owners_voice_still_approve(chat, gmail, monkeypatch, who):
    cid = _arm(gmail, monkeypatch)
    rec, won = ap.decide_with_outcome(cid, "approve", decided_by=who)
    assert won is True and ia.wait_for(cid, 10)["status"] == "complete"


def test_f3_mj1_any_channel_can_decline(chat, gmail, monkeypatch):
    cid = _arm(gmail, monkeypatch)
    rec, won = ap.decide_with_outcome(cid, "deny", decided_by="owner:sms")
    assert won is True and rec["status"] == "denied" and gmail.changes() == []


# ── F3-MD-1: the end-of-call summary gets the neutral line ───────────────────────────────────────

def test_f3_md1_the_call_summary_reads_the_neutral_line_not_the_spoken_card(safe_call, monkeypatch):
    """Fails on da66aaa40: turn_log (the distil's transcript) held the spoken line with subjects and names."""
    saved = []
    monkeypatch.setattr(rv, "_persist_voice_turn", lambda u, a, conversation_id=None, provider=None:
                        saved.append(a) or True)
    c = safe_call(room=False)
    c.session.hooks["persist"] = lambda u, a, cid: rv._persist_local_turn(c.tool_session, u, a, cid)
    c.say("archive these")
    assert "Sender" in " ".join(c.mouth.spoken), "the line is still said aloud with the names"
    assert saved and saved[-1] == rv._CARD_LINE_FALLBACK
    log = str(c.session.turn_log)
    assert rv._CARD_LINE_FALLBACK in log and "Sender" not in log and "Subject" not in log


def test_f3_md1_a_turn_with_nothing_to_hide_keeps_its_words_in_the_summary(safe_call, monkeypatch):
    monkeypatch.setattr(rv, "_persist_voice_turn", lambda u, a, conversation_id=None, provider=None: True)
    c = safe_call(room=False)
    c.session.hooks["persist"] = lambda u, a, cid: rv._persist_local_turn(c.tool_session, u, a, cid)
    c.say("what's on my calendar")
    assert c.session.turn_log and "calendar" in c.session.turn_log[-1][0]


# ── F3-MD-2: a deictic beside Friday's numbers is asked when it would pick something else ─────────

def test_f3_md2_these_with_a_row_open_and_fridays_numbers_elsewhere_is_asked(gmail, monkeypatch):
    """Fails on da66aaa40: 'archive these' became a card for the open email while Friday had numbered three others."""
    stage = _stage(selected=())
    stage["open"] = ref(4)
    report(stage)
    _friday_pointed(monkeypatch)
    r = lr.route("archive these", trusted=True, stage=stage, log=False, conversation_id=CONV, rung_budget_ms=5000)
    assert (r.decision, r.layer) == ("ask", "safe_write") and r.question == sw.DEICTIC_QUESTION and not r.args
    assert not sw._TICKETS and not _cards()


def test_f3_md2_a_different_count_than_the_numbered_rows_is_asked(gmail, monkeypatch):
    stage = _stage(selected=(1, 2))
    report(stage)
    _friday_pointed(monkeypatch)                          # three rows numbered, two ticked
    r = lr.route("archive these", trusted=True, stage=stage, log=False, conversation_id=CONV, rung_budget_ms=5000)
    assert (r.decision, r.layer) == ("ask", "safe_write") and not sw._TICKETS


def test_f3_md2_a_plural_that_resolves_to_one_row_is_asked(gmail, monkeypatch):
    from agent_friday.services import desktop_bus
    stage = _stage(selected=(1,))
    report(stage)
    desktop_bus.set_pointed("messages", [ref(5)])         # one row numbered, one ticked: the count matches
    r = lr.route("archive these", trusted=True, stage=stage, log=False, conversation_id=CONV, rung_budget_ms=5000)
    assert (r.decision, r.layer) == ("ask", "safe_write") and not sw._TICKETS


def test_f3_md2_the_owners_own_ticks_in_the_same_number_still_make_a_card(gmail, monkeypatch):
    stage = _stage(selected=(1, 2, 3))
    report(stage)
    Page(monkeypatch)
    _friday_pointed(monkeypatch)                          # three numbered, three ticked: the owner's rows
    r = lr.route("archive these", trusted=True, stage=stage, log=False, conversation_id=CONV, rung_budget_ms=5000)
    assert r.layer == "safe_write" and r.decision == "tool"


def test_f3_md2_with_no_numbers_on_screen_the_open_row_is_still_what_these_means(gmail, monkeypatch):
    stage = _stage(selected=())
    stage["open"] = ref(4)
    report(stage)
    Page(monkeypatch)
    r = lr.route("archive this", trusted=True, stage=stage, log=False, conversation_id=CONV, rung_budget_ms=5000)
    assert r.layer == "safe_write" and r.decision == "tool"


# ── F3-MD-3: a record that cannot be verified is not narrow ──────────────────────────────────────

def test_f3_md3_an_unverifiable_record_is_removed_when_narrowing_so_a_recovery_cannot_revive_it(
        settings_file, owner_record, monkeypatch):
    """Fails on da66aaa40: the unreadable record counted as 'already off', stayed on disk, and a keychain that
    came back later verified the old 'on'."""
    core = settings_file
    _click(core, "on")
    real = gs._anchor_get

    def unreadable(key):
        raise OSError("the keychain did not answer")
    monkeypatch.setattr(gs, "_anchor_get", unreadable)
    gs.invalidate()
    assert gs.narrow_record("laya_safe_writes", "off") is True
    assert not gs._record_path().exists()
    monkeypatch.setattr(gs, "_anchor_get", real)           # the keychain recovers
    gs.invalidate()
    _hand_edit(core, "on")                                  # a later file write says on
    assert gs.owner_choice("laya_safe_writes", block=True) == "off" and sw.mode() == "off"


def test_f3_md3_a_file_that_says_on_never_removes_a_record_on_a_transient_failure(settings_file, owner_record, monkeypatch):
    core = settings_file
    _click(core, "on")

    def unreadable(key):
        raise OSError("the keychain did not answer")
    monkeypatch.setattr(gs, "_anchor_get", unreadable)
    assert gs.narrow_record("laya_safe_writes", "on") is True
    assert gs._record_path().exists(), "nothing to narrow when the file is as wide as it gets"


# ── n3-2: a card being built while the setting goes off does not survive ─────────────────────────

def test_n3_2_a_card_built_while_the_owner_turns_the_setting_off_is_not_raised(gmail, monkeypatch):
    """Fails on da66aaa40: the mode was read at redeem, before the network snapshot, and the card was stored after."""
    stage = _stage(selected=(1, 2))
    report(stage)
    Page(monkeypatch)
    r = _route("archive these", stage)
    assert r.layer == "safe_write"
    real = gm.snapshot_messages

    def snapshot_then_off(acct, tids):
        out = real(acct, tids)
        _mode(monkeypatch, "off")                          # the owner clicks Off during the Gmail round trip
        return out
    monkeypatch.setattr(gm, "snapshot_messages", snapshot_then_off)
    out = agent._tool_organize_email(dict(r.args))
    assert out.startswith("NOT DONE") and "turned off" in out
    assert not _pending() and gmail.changes() == []


# ── n3-3: the boot check cannot hold start-up ────────────────────────────────────────────────────

def test_n3_3_a_hung_boot_keychain_read_times_out_and_fails_closed(settings_file, owner_record, monkeypatch):
    import threading
    core = settings_file
    _click(core, "on")
    release = threading.Event()
    monkeypatch.setattr(gs, "narrow_record", lambda key, value: release.wait(5.0))
    t0 = time.perf_counter()
    try:
        gs.boot_narrow({"laya_safe_writes": "off"}, timeout_s=0.1)
        spent = time.perf_counter() - t0
    finally:
        release.set()
    assert spent < 2.0, "start-up waited %.1fs on a hung keychain" % spent
    assert gs.owner_choice("laya_safe_writes", block=False) == "off" and "laya_safe_writes" in gs._FORCE_OFF


# ── n3-4: the card hooks run with no settings lock held ──────────────────────────────────────────

def test_n3_4_card_hooks_do_not_run_under_the_write_lock(settings_file, owner_record, monkeypatch):
    core = settings_file
    _click(core, "on")
    held = []
    monkeypatch.setattr(gs, "_after_narrowing", lambda key, value: held.append(gs._WRITE_LOCK._is_owned()))
    _hand_edit(core, "off")
    gs.follow_file("laya_safe_writes", "off")
    for _ in range(150):
        if held:
            break
        time.sleep(0.02)
    assert held == [False], held


# ── n3-5 and the docs ────────────────────────────────────────────────────────────────────────────

def test_f3_docs_say_how_a_laya_card_is_approved_and_what_is_named():
    root = Path(__file__).resolve().parents[2]
    flat = lambda name: " ".join((root / name).read_text(encoding="utf-8").split())
    mail, phone, log = flat("docs/user-guide/mail.md"), flat("docs/user-guide/phone.md"), flat("CHANGELOG.md")
    assert "A text message does not approve it either" in mail and "never the addresses" in mail
    # The card may be texted with its code; a YES for it is refused (phone/service.py), so the
    # page says "never approved by text".
    assert "never approved by text" in phone and "Voice history keeps no card details" in log
    assert "they say that a card was raised" in mail


# ══ Owner decisions: MD-2 (a spoken yes needs the line read in this conversation) and MJ-4 (card-free, logged, pinned) ══

def _voice_answer(cid, words, voiced, conv=CALL):
    toks = [agent._CURRENT_SURFACE.set("voice"), agent._CURRENT_CONVERSATION.set(conv),
            agent._CURRENT_OWNER_TEXT.set(words), agent._CURRENT_TOOL_CONTEXT.set({"voiced_cards": list(voiced)})]
    try:
        return agent._tool_answer_card({"card_id": cid, "decision": "approve"})
    finally:
        agent._CURRENT_TOOL_CONTEXT.reset(toks[3])
        agent._CURRENT_OWNER_TEXT.reset(toks[2])
        agent._CURRENT_CONVERSATION.reset(toks[1])
        agent._CURRENT_SURFACE.reset(toks[0])


def test_md2_a_line_that_never_played_is_not_enough_even_when_the_action_is_named(safe_call, monkeypatch):
    """Fails on da66aaa40: 'yes, archive them' approved a Laya card whose line was never delivered."""
    c = safe_call()
    monkeypatch.setattr(c.mouth, "synthesize_stream", lambda text, cancel=None: iter(()))   # nothing is played
    c.say("archive these")
    cid = _pending()[0]["approval_id"]
    c.say("what's on my calendar")                      # the next turn works out which cards were voiced
    voiced = c.tool_session.get("voiced_cards") or []
    assert cid not in voiced
    assert _voice_answer(cid, "yes archive them", voiced) == ia.UNVOICED_LAYA_CARD
    assert _voice_answer(cid, "yes", voiced) == ia.UNVOICED_LAYA_CARD
    assert ap.get_approval(cid)["status"] == "pending"


def test_md2_a_line_read_whole_lets_a_yes_approve_that_card(safe_call, monkeypatch):
    c = safe_call()
    c.say("archive these")
    cid = _pending()[0]["approval_id"]
    c.say("yes")
    voiced = c.tool_session.get("voiced_cards") or []
    assert cid in voiced, "the fixed line was played whole in this conversation"
    res = _voice_answer(cid, "yes", voiced)
    assert res != ia.UNVOICED_CARD and "NOT RECORDED" not in res
    assert ia.wait_for(cid, 10)["status"] == "complete"


def test_md2_a_line_read_in_another_conversation_does_not_count(safe_call, monkeypatch):
    """Fails on da66aaa40: the voiced list of whatever session asked was trusted."""
    c = safe_call()
    c.say("archive these")
    cid = _pending()[0]["approval_id"]
    c.say("yes")
    voiced = c.tool_session.get("voiced_cards") or []
    assert cid in voiced
    assert _voice_answer(cid, "yes", voiced, conv="another-call") == ia.UNVOICED_LAYA_CARD
    assert ap.get_approval(cid)["status"] == "pending"


def test_md2_a_card_a_model_raised_keeps_the_action_naming_rule(safe_call, monkeypatch):
    c = safe_call()
    out = agent._tool_organize_email({"action": "archive", "selection": "screen"})
    assert out.startswith("CARD_RAISED")
    cid = _pending()[0]["approval_id"]
    assert ia.is_laya_card(cid) is False
    assert _voice_answer(cid, "yes archive them", []) != ia.UNVOICED_CARD
    assert ia.wait_for(cid, 10)["status"] == "complete"


def test_md2_the_live_voice_bridge_applies_the_same_rule(gmail, monkeypatch):
    stage = _stage(selected=(1, 2))
    report(stage)
    Page(monkeypatch)
    _go("archive these", stage)
    cid = _pending()[0]["approval_id"]
    assert ia.unvoiced_card_approval_blocked(cid, "yes archive them") is True
    assert ia.unvoiced_card_approval_blocked(cid, "no thanks") is False, "a refusal is never blocked"
    assert ia.card_in_conversation(cid, CONV) and not ia.card_in_conversation(cid, "elsewhere")


# ── MJ-4: card-free star, unstar, label and mark read/unread: pinned, logged, exactly undoable ───

CARD_FREE = {"read", "unread", "star", "unstar", "label", "unlabel"}


def test_mj4_the_card_free_list_is_pinned_to_the_owners_rule():
    """Any other mutation added to EMAIL_NO_CARD fails here (a source check, so it needs no run of the tool)."""
    import ast
    src = (Path(__file__).resolve().parents[2] / "src" / "agent_friday" / "services" / "item_actions.py").read_text(
        encoding="utf-8")
    values = [ast.literal_eval(n.value) for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "EMAIL_NO_CARD" for t in n.targets)]
    assert len(values) == 1 and set(values[0]) == CARD_FREE and len(values[0]) == len(CARD_FREE)
    assert set(ia.EMAIL_NO_CARD) == CARD_FREE
    always_a_card = set(ia.EMAIL_ACTIONS) - CARD_FREE
    assert {"archive", "move", "trash", "spam", "restore", "not_spam", "inbox"} <= always_a_card


@pytest.mark.parametrize("action", ["archive", "move", "trash", "spam", "restore", "not_spam", "inbox"])
def test_mj4_these_always_raise_a_card_and_change_nothing(gmail, monkeypatch, action):
    Page(monkeypatch)
    out = ia.propose_email(action, thread_ids=["acct_work:t1"], label="receipts")
    assert out["status"] == "pending_approval" and gmail.changes() == []


def test_mj4_a_card_free_change_is_in_the_turn_receipt_without_subjects_and_undoes_exactly(real_mail, monkeypatch):
    """Fails on da66aaa40: the turn receipt knew nothing of the change (action, count, message ids)."""
    import json
    from agent_friday.services import turn_plan, turn_receipt
    before = _snap()
    turn_plan.begin_turn("turn-card-free")
    try:
        out = agent._tool_organize_email({"action": "star", "thread_ids": ["acct_work:t1", "acct_work:t3"]})
    finally:
        turn_plan.begin_turn(None)
    assert out.startswith("DONE"), out
    assert not _pending(), "no card was raised"
    assert MAIL["m1"]["labels"] >= {"STARRED"} and MAIL["m7"]["labels"] >= {"STARRED"}
    got = turn_receipt.build("turn-card-free")
    (change,) = got["card_free_changes"]
    assert change["tool"] == "organize_email" and change["action"] == "star" and change["count"] == 2
    assert change["message_ids"] == {"acct_work": {"t1": ["m1", "m2"], "t3": ["m6", "m7"]}}, change["message_ids"]
    assert change["undoable"] is True and got["found"] is True
    assert "Subject" not in json.dumps(got["card_free_changes"]) and "Sender" not in json.dumps(got["card_free_changes"])
    # the existing Undo path puts every message back exactly (m3 was already starred and stays so)
    undo = ia.undo(change["receipt_id"], conversation_id=CONV)
    assert undo["status"] == "pending_approval"
    with pressed(): ap.decide(undo["approval_id"], "approve", decided_by="owner:ui")
    assert ia.wait_for(undo["approval_id"], 10)["status"] == "complete"
    assert _snap() == before


def test_mj4_a_card_that_was_approved_is_not_listed_as_card_free(real_mail, monkeypatch):
    from agent_friday.services import turn_plan, turn_receipt
    stage = _stage(selected=(1,))
    report(stage)
    Page(monkeypatch)
    turn_plan.begin_turn("turn-with-card")
    try:
        _go("archive this", stage)
        cid = _pending()[0]["approval_id"]
        with pressed(): ap.decide(cid, "approve", decided_by="owner:ui")
        ia.wait_for(cid, 10)
    finally:
        turn_plan.begin_turn(None)
    assert "card_free_changes" not in turn_receipt.build("turn-with-card")


def test_mj4_the_docs_call_it_a_rule_not_a_known_issue():
    root = Path(__file__).resolve().parents[2]
    flat = lambda name: " ".join((root / name).read_text(encoding="utf-8").split())
    mail, known = flat("docs/user-guide/mail.md"), flat("KNOWN_ISSUES.md")
    assert "Changes Friday makes without a card (a rule you set)" in mail and "never a subject" in mail
    section = known[known.index("## 3. Deliberate behaviour"):]
    assert "run with no card when a model asks" in section and "This is your rule" in section
    assert "Open since Beta 1.1" in known and "model path still runs star" not in known
    assert "only if Friday actually read that card's line aloud in this conversation" in mail


# ══ Final review (cfcc5d6d1) ══

# ── FN-MD-1: every path names its turn, so a card-free change reaches a receipt ───────────────────

def _card_free_star(session_ctx, tid=None):
    from agent_friday.services import turn_plan
    if tid:
        turn_plan.begin_turn(tid)
    try:
        return agent._execute_tool("organize_email", {"action": "star", "thread_ids": ["acct_work:t1"]},
                                   session_ctx=session_ctx)
    finally:
        turn_plan.begin_turn(None)


def test_fn_md1_a_card_free_change_on_a_spoken_turn_reaches_that_turns_receipt(chat, gmail, monkeypatch):
    """Fails on cfcc5d6d1: a spoken turn had no turn id, so the change was in no turn receipt."""
    from agent_friday.services import turn_receipt, voice_engine as ve
    ctx = ve._voice_ctx({"engine": "local", "conversation_id": CALL, "turn_id": "voice-abc123"})
    out = _card_free_star(ctx)
    assert "DONE" in out or "starred" in out.lower(), out
    got = turn_receipt.build("voice-abc123")
    (change,) = got["card_free_changes"]
    # the count is conversations (one was named), as the receipt's summary says; message ids are listed apart
    assert change["action"] == "star" and change["count"] == 1 and change["undoable"] is True


def test_fn_md1_a_live_tool_call_and_a_background_task_get_their_own_ids(chat, gmail, monkeypatch):
    from agent_friday.services import action_journal, turn_receipt, voice_engine as ve
    _card_free_star(ve._voice_ctx({"conversation_id": CALL}))                        # Gemini Live: not a local session
    live = [r for r in action_journal.recent(20) if r.get("card_free")]
    assert live and str(live[0]["turn_id"]).startswith("live-")
    assert turn_receipt.build(live[0]["turn_id"])["card_free_changes"]
    gmail.calls.clear()
    _card_free_star({"authenticated": True, "is_background_task": True, "task_id": "t42", "conversation_id": CONV})
    assert turn_receipt.build("task-t42")["card_free_changes"], "a delegated task's change is in its own receipt"


def test_fn_md1_a_routed_voice_turn_names_itself_in_the_tool_session_and_the_route_frame(monkeypatch):
    frames = []
    plan = {"tool": None, "args": None, "ack": "", "label": "", "question": "",
            "receipt": {"layer": "rule", "decision": "no_tool"}}
    monkeypatch.setattr(rv, "_voice_route_plan", lambda *a, **k: plan)
    ts = {"conversation_id": CALL, "spoken": []}
    rv._run_routed_front_turn("hello", lambda s: None, front={"contract": {"names": []}}, tool_session=ts,
                              seat_turn=lambda u, od, p, rt, cl=None: "hi", send=frames.append,
                              session=lambda: None, owner_voice=True)
    assert ts["turn_id"].startswith("voice-") and frames[0]["turn_id"] == ts["turn_id"]


# ── FN-MD-2: a card-free change is exact or refused; the label it makes is accounted for ──────────

def test_fn_md2_a_conversation_that_cannot_be_read_is_refused_not_changed_inexactly(real_mail, monkeypatch):
    """Fails on cfcc5d6d1: the card-free path ran the thread-level fallback and marked it inexact."""
    before = _snap()
    FAIL_GET.add("t2")
    out = agent._tool_organize_email({"action": "star", "thread_ids": ["acct_work:t1", "acct_work:t2", "acct_work:t3"]})
    assert "could not be changed" in out or out.startswith("PARTLY"), out
    assert MAIL["m4"]["labels"] == before["m4"] and MAIL["m5"]["labels"] == before["m5"], "t2 was not touched"
    assert ("thread_modify", "t2") not in CALLS
    assert "STARRED" in MAIL["m1"]["labels"] and "STARRED" in MAIL["m6"]["labels"], "the readable ones were changed"


def test_fn_md2_a_label_the_change_created_is_removed_by_undo_when_it_is_empty(chat, gmail, monkeypatch):
    from agent_friday.services import action_journal as journal
    out = ia.propose_email("label", thread_ids=["acct_work:t1"], label="Taxes 2026")
    rec = journal.get(out["receipt_id"])
    assert rec["labels_created"] == ["Taxes 2026"] and rec["undo"]["created_labels"] == {"acct_work": "Label_new"}
    undo = ia.undo(out["receipt_id"], conversation_id=CONV)
    with pressed(): ap.decide(undo["approval_id"], "approve", decided_by="owner:ui")
    done = ia.wait_for(undo["approval_id"], 10)
    assert ("delete_label", "acct_work", "Label_new") in gmail.calls and "removed the label" in done["summary"]
    assert not [l for l in gmail.labels["acct_work"] if l["name"] == "Taxes 2026"]


def test_fn_md2_a_label_that_still_holds_mail_stays_and_undo_says_so(chat, gmail, monkeypatch):
    gmail.label_empty = False
    out = ia.propose_email("label", thread_ids=["acct_work:t1"], label="Taxes 2026")
    undo = ia.undo(out["receipt_id"], conversation_id=CONV)
    with pressed(): ap.decide(undo["approval_id"], "approve", decided_by="owner:ui")
    done = ia.wait_for(undo["approval_id"], 10)
    assert "left in place" in done["summary"] and [l for l in gmail.labels["acct_work"] if l["name"] == "Taxes 2026"]


def test_fn_md2_undoable_in_the_turn_receipt_reflects_the_truth():
    from agent_friday.services import action_journal as journal
    from agent_friday.services import turn_receipt
    def rec(tid, inexact):
        ch = {"added": ["STARRED"], "removed": [], "messages": {"m1": {"added": ["STARRED"], "removed": []}}}
        if inexact:
            ch = {"added": [], "removed": [], "messages": {}, "inexact": "the read failed"}
        journal.record("organize_email", "star", "email", items=[], summary="x", status="complete",
                       undo={"email": {"acct_work": {"t1": ch}}},
                       extra={"card_free": True, "turn_id": tid, "count": 1, "message_ids": {}})
    rec("turn-exact", False)
    rec("turn-inexact", True)
    assert turn_receipt.build("turn-exact")["card_free_changes"][0]["undoable"] is True
    assert turn_receipt.build("turn-inexact")["card_free_changes"][0]["undoable"] is False


# ── FN-mn-1 / FN-mn-2: the voice label is the server's; the caption and the route say it plainly ──

def test_fn_mn1_a_body_claimed_voice_label_is_the_plain_owner(gmail):
    """Fails on cfcc5d6d1: any other claimed label passed through, so an HTTP body could claim 'owner:/voice/'."""
    from flask import Flask, request
    from agent_friday.services import screen_click
    with Flask(__name__).test_request_context("/x", method="POST", json={}):
        assert screen_click.decided_by(request, "owner:/voice/") == "owner"
        assert screen_click.decided_by(request, "owner:ui") == "owner"
        assert screen_click.decided_by(request, "owner:caption") == "owner:caption"


def test_fn_mn2_the_decide_route_refuses_a_laya_approval_with_a_clear_message(chat, gmail, monkeypatch):
    import json
    from flask import Flask
    from agent_friday.routes import goals
    cid = _arm(gmail, monkeypatch)
    fn = getattr(goals.decide_approval_route, "__wrapped__", goals.decide_approval_route)
    for who in ("owner:caption", "owner:/voice/", "owner:sms"):
        body = {"decision": "approve", "decided_by": who}
        with Flask(__name__).test_request_context("/x", method="POST", json=body):
            resp = fn(cid)
        resp, status = resp if isinstance(resp, tuple) else (resp, 200)
        data = json.loads(resp.get_data(as_text=True))
        assert status == 403 and data["error"] == "needs_approve_button" and "Approve button" in data["message"], who
        assert "already_decided" not in data
    assert ap.get_approval(cid)["status"] == "pending"
    with Flask(__name__).test_request_context("/x", method="POST", json={"decision": "deny", "decided_by": "owner:caption"}):
        resp = fn(cid)
    assert json.loads((resp[0] if isinstance(resp, tuple) else resp).get_data(as_text=True))["ok"] is True


def test_fn_mn2_the_caption_never_offers_a_laya_card():
    root = Path(__file__).resolve().parents[2]
    text = (root / "index.html").read_text(encoding="utf-8")
    start = text.index("var CAPTION_NEVER_DECIDES")
    assert "x.payload.origin === 'laya_safe_write'" in text[start:start + 1200]


# ── FN-mn-3 / FN-mn-4: a removed record cannot come back; no settings stall ──────────────────────

def test_fn_mn3_removing_a_record_advances_the_anchor_so_a_restored_file_cannot_verify(settings_file, owner_record):
    """Fails on cfcc5d6d1: the anchor stayed at the removed record's counter, so a restored copy verified."""
    core = settings_file
    _click(core, "on")
    old = gs._record_path().read_text(encoding="utf-8")
    before = _ANCHOR["laya_safe_writes"]
    assert gs._remove_record("laya_safe_writes") is True
    assert _ANCHOR["laya_safe_writes"] > before
    gs._record_path().write_text(old, encoding="utf-8")        # a backup tool puts it back
    _hand_edit(core, "on")
    assert gs.owner_choice("laya_safe_writes", block=True) == "off" and sw.mode() == "off"


def test_fn_mn3_with_the_keychain_down_a_tombstone_still_retires_the_counter(settings_file, owner_record, monkeypatch):
    core = settings_file
    _click(core, "on")
    old = gs._record_path().read_text(encoding="utf-8")
    real = gs._anchor_set

    def down(key, n):
        raise OSError("the keychain did not answer")
    monkeypatch.setattr(gs, "_anchor_set", down)
    assert gs._remove_record("laya_safe_writes") is True
    monkeypatch.setattr(gs, "_anchor_set", real)               # the keychain recovers; the anchor never moved
    gs._record_path().write_text(old, encoding="utf-8")
    gs.invalidate()
    _hand_edit(core, "on")
    assert gs.owner_choice("laya_safe_writes", block=True) == "off" and sw.mode() == "off"
    _click(core, "on")                                         # a fresh click still works
    assert gs.owner_choice("laya_safe_writes", block=True) == "on"


def test_fn_mn4_a_click_does_not_stall_behind_a_stuck_boot_worker(settings_file, owner_record, monkeypatch):
    """Fails on cfcc5d6d1: record_owner_choice waited on the write lock for as long as the keychain hung."""
    import threading
    core = settings_file
    monkeypatch.setattr(gs, "LOCK_TIMEOUT_S", 0.2)
    holding, release = threading.Event(), threading.Event()

    def hold():
        with gs._WRITE_LOCK:
            holding.set()
            release.wait(5.0)
    t = threading.Thread(target=hold, daemon=True)
    t.start()
    assert holding.wait(2.0)
    done = []
    c = threading.Thread(target=lambda: done.append(gs.record_owner_choice("laya_safe_writes", "on")), daemon=True)
    c.start()
    c.join(2.0)
    release.set()
    assert not c.is_alive() and done == [False], "the click failed closed instead of waiting"
    assert sw.mode() == "off"


# ── FN-mn-5, FN-mn-7: one row is asked even with no numbers; the pointed argument is honoured ─────

def test_fn_mn5_these_that_finds_one_row_is_asked_even_with_no_numbers_on_screen(gmail, monkeypatch):
    """Fails on cfcc5d6d1: the one-row plural was asked only when Friday's numbers were on screen."""
    stage = _stage(selected=(1,))
    report(stage)
    Page(monkeypatch)
    r = lr.route("archive these", trusted=True, stage=stage, log=False, conversation_id=CONV, rung_budget_ms=5000)
    assert (r.decision, r.layer, r.question) == ("ask", "safe_write", sw.ONE_ROW_QUESTION) and not sw._TICKETS
    r = lr.route("archive this", trusted=True, stage=stage, log=False, conversation_id=CONV, rung_budget_ms=5000)
    assert r.layer == "safe_write" and r.decision == "tool", "the singular is still a card"


def test_fn_mn7_the_pointed_argument_decides_whether_friday_numbers_are_on_screen(gmail, monkeypatch):
    """Fails on cfcc5d6d1: the check read the bus and ignored the argument."""
    stage = _stage(selected=())
    report(stage)
    numbers = {"refs": [ref(4), ref(5), ref(6)], "at": time.time()}
    r = lr.safe_write_route("archive the second one", stage=stage, pointed=numbers, conversation_id=CONV)
    assert r is not None and r.decision == "ask" and r.question == sw.ORDINAL_QUESTION
    _friday_pointed(monkeypatch)                                  # the bus has numbers, the caller says there are none
    r = lr.safe_write_route("archive the second one", stage=stage, pointed=None, conversation_id=CONV)
    assert r is not None and r.decision == "tool"
    r = lr.safe_write_route("archive the second one", stage=stage, conversation_id=CONV)     # none given: the bus
    assert r is not None and r.decision == "ask"


# ── FN-mn-6, Gemini Live: the unheard-card text, and no Laya card id on a Live turn ───────────────

def test_fn_mn6_the_refusal_for_an_unheard_laya_card_says_what_works(safe_call, monkeypatch):
    """Fails on cfcc5d6d1: it told the owner to name the action, which no longer works for a Laya card."""
    c = safe_call()
    monkeypatch.setattr(c.mouth, "synthesize_stream", lambda text, cancel=None: iter(()))
    c.say("archive these")
    cid = _pending()[0]["approval_id"]
    res = _voice_answer(cid, "yes archive them", [])
    assert res == ia.UNVOICED_LAYA_CARD and "Approve" in res and "say what the card does" not in res
    assert ia.UNVOICED_CARD != res


def test_live_never_speaks_approves_or_arms_a_laya_card(chat, gmail, monkeypatch):
    import asyncio
    cid = _arm(gmail, monkeypatch)
    session = {"conversation_id": CONV}

    async def fake_limit(fname, fargs, send, sess, runner=None):
        return "CARD_RAISED: nothing has changed yet. Read this back and ask: x (card_id=%s)" % cid
    monkeypatch.setattr(rv, "_voice_tool_with_limit", fake_limit)
    out = asyncio.run(rv._live_card_tool("organize_email", {}, lambda f: None, session))
    assert out.endswith("(card_id=%s)" % cid) and "Subject" not in out and "Sender" not in out
    assert rv._LIVE_CARD_ASK not in out, "no line was handed to the cloud voice"
    res = asyncio.run(rv._live_card_tool("answer_card", {"card_id": cid}, lambda f: None, session))
    assert res == ia.LAYA_CARD_NOT_ON_LIVE and "Subject" not in res and "Sender" not in res
    assert ap.get_approval(cid)["status"] == "pending"
