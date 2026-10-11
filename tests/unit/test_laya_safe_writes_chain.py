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

import pytest

from agent_friday.routes import voice as rv
from agent_friday.services import agent, approvals as ap, gmail_api
from agent_friday.services import gmail_mailbox as gm
from agent_friday.services import guarded_settings as gs
from agent_friday.services import item_actions as ia
from agent_friday.services import laya_router as lr
from agent_friday.services import laya_safe_writes as sw
from agent_friday.services import off_record, point_act as pa, voice_delivery
from tests.screen_fixtures import Page, report
from tests.unit.test_laya_safe_writes import (  # noqa: F401  (fixtures are used by name)
    CONV, ON_CASES, _cards, _go, _mode, _pending, _route, _stage, chat, gmail, settings_file)
from tests.unit.test_voice_card_readback import CALL, _Call

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
            def get(self, userId=None, id=None, format=None):               # noqa: A002,N803
                def run():
                    CALLS.append(("get", id))
                    if id in FAIL_GET:
                        raise RuntimeError("Gmail would not answer")
                    return {"id": id, "messages": [{"id": k, "threadId": id, "labelIds": sorted(v["labels"])}
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


def _put(mid, thread, *labels):
    MAIL[mid] = {"thread": thread, "labels": set(labels)}


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
    ap.decide(card["approval_id"], "approve")
    rec = ia.wait_for(card["approval_id"], 10)
    assert rec["status"] == "complete", rec
    assert _snap() != before, "the approved change really happened, so the round trip is not vacuous (%s)" % action
    assert all(_snap()[k] == before[k] for k in before if k.startswith("x_")), "only the three rows move"
    out = ia.undo(rec["receipt_id"], conversation_id=CONV)
    assert out["status"] == "pending_approval"
    ap.decide(out["approval_id"], "approve")
    undone = ia.wait_for(out["approval_id"], 10)
    assert undone["status"] == "complete", undone
    assert _snap() == before, "undo puts every MESSAGE back exactly (%s)" % action


def test_b1_a_sent_reply_never_gains_a_label_it_did_not_lose(real_mail, monkeypatch):
    stage = _stage(selected=(1, 3))
    report(stage)
    Page(monkeypatch)
    _go("archive these", stage)
    cid = _pending()[0]["approval_id"]
    ap.decide(cid, "approve")
    rec = ia.wait_for(cid, 10)
    assert MAIL["m2"]["labels"] == {"SENT"} and MAIL["m1"]["labels"] == set() and MAIL["m3"]["labels"] == {"UNREAD", "STARRED"}
    out = ia.undo(rec["receipt_id"], conversation_id=CONV)
    ap.decide(out["approval_id"], "approve")
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
    ap.decide(card["approval_id"], "approve")
    rec = ia.wait_for(card["approval_id"], 10)
    assert rec["status"] == "partial", rec
    by = {i["id"]: i for i in rec["items"]}
    assert by["t2"]["ok"] is False and by["t2"]["error"] == gm.NOT_EXACT_REFUSED
    assert MAIL["m4"]["labels"] == before["m4"] and MAIL["m5"]["labels"] == before["m5"], "t2 was not touched"
    assert MAIL["m1"]["labels"] != before["m1"] and MAIL["m6"]["labels"] != before["m6"], "t1 and t3 were"
    assert not [c for c in CALLS if c == ("thread_modify", "t2")], "no thread-level fallback"
    FAIL_GET.clear()
    out = ia.undo(rec["receipt_id"], conversation_id=CONV)
    ap.decide(out["approval_id"], "approve")
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
    ap.decide(card["approval_id"], "approve")
    rec = ia.wait_for(card["approval_id"], 10)
    assert rec["status"] == "complete"
    assert MAIL["m_new"]["labels"] == {"INBOX", "UNREAD"}, "the new reply was never on the card"
    assert "INBOX" not in MAIL["m1"]["labels"] and "INBOX" not in MAIL["m3"]["labels"]
    assert "m_new" not in rec["undo"]["email"]["acct_work"]["t1"]["messages"]
    out = ia.undo(rec["receipt_id"], conversation_id=CONV)
    ap.decide(out["approval_id"], "approve")
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
    monkeypatch.setattr(ia, "_thread_heads", lambda acct, tids: {
        t: {"subject": "Invoice", "sender": "Mara", "address": "billing@phish.example", "date": ""} for t in tids})
    stage = _stage(selected=(1, 2))
    report(stage)
    Page(monkeypatch)
    r, out = _go("archive these", stage)
    pay = _pending()[0]["payload"]
    assert all("<billing@phish.example>" in line for line in pay["lines"][:2]), pay["lines"]
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
    assert "“Subject t1” from Sender" in out["reply"] and "@" not in out["reply"]
    assert "archive 3 conversations" in out["reply"] and "Nothing changes until you approve it" in out["reply"]
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
                                    ("unstar these", 26), ("label these as x" + "y" * 60, 3)])
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

@pytest.fixture
def owner_record(tmp_path, monkeypatch):
    from agent_friday.governance import proof_of_integrity as poi
    nonce = {}
    monkeypatch.setattr(gs, "_dir", lambda: tmp_path / "runtime" / "guarded_settings")
    monkeypatch.setattr(gs, "_load_nonce", lambda: nonce.get("n"))
    monkeypatch.setattr(gs, "_store_nonce", lambda n: nonce.__setitem__("n", n))
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
