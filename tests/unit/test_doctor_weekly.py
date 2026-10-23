"""The weekly "send a Doctor report to FutureSpeak?" ask (amendment A3).

Nothing here sends by itself.
"""
from __future__ import annotations

import hashlib
import json
import time

import pytest


@pytest.fixture
def weekly(tmp_path, monkeypatch):
    home = tmp_path / ".friday"
    home.mkdir()
    monkeypatch.setenv("FRIDAY_HOME", str(home))
    from agent_friday.services import cloud_intent, doctor_weekly, off_record
    monkeypatch.setattr(cloud_intent, "routing_mode", lambda *a, **k: "cloud_only")
    monkeypatch.setattr(off_record, "active", lambda *a, **k: False)
    # install_hooks() registers process-wide: undone after the test, so no
    # later test's off_record.end() writes through this one's hook.
    monkeypatch.setattr(off_record, "_END_HOOKS", list(off_record._END_HOOKS))
    monkeypatch.setattr(doctor_weekly, "_OFF_RECORD_SEEN", [])
    doctor_weekly._PREPARED.clear()
    return doctor_weekly


class _Mail:
    def __init__(self, senders=("acct-1",), drafts=()):
        self.senders, self.drafts, self.sent, self.saved = list(senders), list(drafts), [], []

    def install(self, monkeypatch):
        from agent_friday.services import gmail_send, google_accounts
        monkeypatch.setattr(gmail_send, "sendable_accounts", lambda: [{"id": a} for a in self.senders])
        monkeypatch.setattr(gmail_send, "request_send", lambda **k: (self.sent.append(k) or
                            {"status": "pending", "approval": {"approval_id": "ap-1"}}))
        monkeypatch.setattr(gmail_send, "save_draft", lambda **k: (self.saved.append(k) or {"link": "x"}))
        monkeypatch.setattr(google_accounts, "list_accounts", lambda: [
            {"id": a, "scopes": [getattr(google_accounts, "GMAIL_MODIFY", "modify")]} for a in self.drafts])
        return self


def test_it_asks_once_and_remembers_one_of_three_answers(weekly):
    assert weekly.next_item() == "ask"
    weekly.answer("not_now")
    assert weekly.next_item() is None
    assert weekly.next_item(now=time.time() + 29 * 86400) == "ask"     # asked again later
    weekly.answer("never")
    assert weekly.next_item(now=time.time() + 400 * 86400) is None
    weekly.answer("weekly")                                          # Settings turns it back on
    assert weekly.next_item() == "weekly"
    with pytest.raises(weekly.WeeklyError):
        weekly.answer("always")


def test_the_card_shows_exactly_what_would_be_sent_and_send_sends_only_that(weekly, monkeypatch):
    mail = _Mail().install(monkeypatch)
    weekly.answer("weekly")
    p = weekly.prepare()
    assert p["to"] == weekly.FUTURESPEAK_DOCTOR_ADDRESS and p["promise"] == weekly.PROMISE
    assert hashlib.sha256(p["text"].encode("utf-8")).hexdigest() == p["sha256"]
    with pytest.raises(weekly.WeeklyError):
        weekly.send("0" * 64)                                        # not the text shown
    assert mail.sent == []
    out = weekly.send(p["sha256"])
    assert out["how"] == "approval_card" and out["approval_id"] == "ap-1"
    with pytest.raises(weekly.WeeklyError):
        weekly.send(p["sha256"])                                     # once a week
    assert len(mail.sent) == 1
    s = mail.sent[0]
    assert s["to"] == weekly.FUTURESPEAK_DOCTOR_ADDRESS and s["account_id"] == "acct-1"
    assert s["body"].startswith(p["text"]) and weekly.PROMISE in s["body"]
    # never twice in one week
    assert weekly.next_item() is None


def test_the_report_holds_no_personal_data(weekly, monkeypatch):
    from agent_friday.paths import friday_home
    (friday_home() / "core_memory").mkdir(parents=True)
    (friday_home() / "core_memory" / "ME.md").write_text("- my address is 12 Elm Street", encoding="utf-8")
    weekly.answer("weekly")
    p = weekly.prepare()
    assert "Elm Street" not in p["text"] and str(friday_home()) not in p["text"]
    assert "@" not in p["text"].replace(weekly.FUTURESPEAK_DOCTOR_ADDRESS, "")


def test_without_send_permission_a_draft_and_without_mail_by_hand(weekly, monkeypatch):
    weekly.answer("weekly")
    mail = _Mail(senders=(), drafts=("acct-2",)).install(monkeypatch)
    p = weekly.prepare()
    out = weekly.send(p["sha256"])
    assert out["how"] == "draft" and mail.saved and mail.saved[0]["account_id"] == "acct-2"
    assert mail.sent == []
    # next week, no account at all
    st = json.loads(weekly._path().read_text(encoding="utf-8"))
    st["decided_week"] = None
    weekly._path().write_text(json.dumps(st), encoding="utf-8")
    _Mail(senders=(), drafts=()).install(monkeypatch)
    p = weekly.prepare()
    assert weekly.send(p["sha256"])["how"] == "by_hand"


def test_skip_this_week_and_stop_asking(weekly):
    weekly.answer("weekly")
    weekly.skip()
    assert weekly.next_item() is None
    weekly.answer("never")
    assert weekly.next_item() is None


def test_local_only_mode_needs_its_own_yes_and_off_the_record_raises_nothing(weekly, monkeypatch):
    from agent_friday.services import cloud_intent, off_record
    weekly.answer("weekly")
    monkeypatch.setattr(cloud_intent, "routing_mode", lambda *a, **k: "local_only")
    assert weekly.next_item() is None
    p_sha = None
    with pytest.raises(weekly.WeeklyError):
        weekly.send(p_sha or "x")
    weekly.answer("weekly", local_only_too=True)
    assert weekly.next_item() == "weekly"
    monkeypatch.setattr(off_record, "active", lambda *a, **k: True)
    assert weekly.next_item() is None
    with pytest.raises(weekly.WeeklyError):
        weekly.prepare()


def test_the_weekly_card_is_quiet_and_raised_once_a_week(weekly, monkeypatch):
    from agent_friday import notifications_engine as ne
    pushed = []
    monkeypatch.setattr(ne, "push", lambda **k: pushed.append(k) or {})
    weekly.answer("weekly")
    assert weekly.tick() == {"offered": "weekly"}
    assert weekly.tick() is None
    assert len(pushed) == 1
    k = pushed[0]
    assert k["tier"] == "fyi" and k["emitter"] == "L07" and k["dedupe_key"].startswith("doctor-weekly:")
    from agent_friday.services import notice_registry as reg
    assert reg.get("L07").place == reg.NEEDS_YOU


def test_nothing_is_ever_sent_by_the_daily_tick(weekly, monkeypatch):
    mail = _Mail().install(monkeypatch)
    from agent_friday import notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **k: {})
    weekly.answer("weekly")
    for _ in range(3):
        weekly.tick()
    assert mail.sent == [] and mail.saved == []



def test_a_standing_send_pre_approval_never_covers_the_weekly_email(weekly, monkeypatch):
    """The send is made outside any tool decision."""
    from agent_friday.governance import action_gate
    from agent_friday.services import gmail_send
    seen = []
    monkeypatch.setattr(gmail_send, "sendable_accounts", lambda: [{"id": "acct-1"}])
    monkeypatch.setattr(gmail_send, "request_send",
                        lambda **k: seen.append(action_gate.DECIDED.get()) or {"approval": {"approval_id": "a"}})
    weekly.answer("weekly")
    p = weekly.prepare()
    tok = action_gate.DECIDED.set({"decision": "allow", "tool": "send_email"})
    try:
        weekly.send(p["sha256"])
    finally:
        action_gate.DECIDED.reset(tok)
    assert seen == [None]


def test_an_unanswered_question_is_asked_once_then_again_in_four_weeks(weekly, monkeypatch):
    from agent_friday import notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **k: {})
    assert weekly.tick() == {"offered": "ask"}
    assert weekly.next_item() is None
    assert weekly.next_item(now=time.time() + 8 * 86400) is None
    assert weekly.next_item(now=time.time() + 29 * 86400) == "ask"


def test_the_emailed_report_says_it_was_chosen_and_a_partly_off_record_week_leaves_out_counts(weekly, monkeypatch):
    from agent_friday.services import off_record
    weekly.answer("weekly")
    p = weekly.prepare()
    assert "by hand only" not in p["text"] and weekly.SHARING in p["text"]
    weekly._PREPARED.clear()
    monkeypatch.setattr(off_record, "active", lambda *a, **k: True)
    weekly.tick()                                                    # notes the week
    monkeypatch.setattr(off_record, "active", lambda *a, **k: False)
    p = weekly.prepare()
    assert "left out this week" in p["text"]


def test_the_page_reuses_this_weeks_text_and_planted_text_is_never_shown_or_sent(weekly, monkeypatch):
    """The text lives in memory only."""
    import hashlib as _h
    mail = _Mail().install(monkeypatch)
    weekly.answer("weekly")
    planted = "planted words in the owner's name"
    weekly._path().parent.mkdir(parents=True, exist_ok=True)
    weekly._path().write_text(json.dumps({"prepared": {"week": weekly.week_key(), "text": planted,
                                                       "sha256": _h.sha256(planted.encode()).hexdigest()}}),
                              encoding="utf-8")
    a, b = weekly.prepare(), weekly.prepare()
    assert a["sha256"] == b["sha256"] and planted not in a["text"]
    with pytest.raises(weekly.WeeklyError):
        weekly.send(_h.sha256(planted.encode()).hexdigest())
    weekly.send(a["sha256"])
    assert planted not in mail.sent[0]["body"]


def test_the_answer_comes_from_its_signed_receipt_not_a_file(weekly):
    """A planted file cannot say yes, or yes in local-only mode."""
    weekly._path().parent.mkdir(parents=True, exist_ok=True)
    weekly._path().write_text(json.dumps({"answer": "weekly", "local_only_ok": True}), encoding="utf-8")
    assert weekly.status()["answer"] is None and weekly.next_item() == "ask"


def test_an_off_record_session_keeps_counts_out_for_thirty_days(weekly, monkeypatch):
    """The off-record session is heard at its end, not only by the tick."""
    from agent_friday.services import off_record
    weekly.answer("weekly")
    weekly.install_hooks()
    off_record.end()                       # the hook notes it
    st = json.loads(weekly._path().read_text(encoding="utf-8"))
    assert st["last_off_record_at"]
    p = weekly.prepare()
    assert "left out this week" in p["text"] and '"action_ledger"' not in p["text"]


def test_nothing_is_prepared_when_no_weekly_report_is_due(weekly):
    with pytest.raises(weekly.WeeklyError):
        weekly.prepare()                                             # not answered yet


def test_the_weekly_text_passes_the_privacy_gate_whole(weekly):
    """The gate read "Doctor report" as health information and a full report
    id as an ID number, and withheld the header: the email says diagnostic
    report and carries a short reference instead."""
    from agent_friday.services import doctor, egress_gate
    text = weekly._render(doctor.build(), False)
    assert egress_gate.gate_text(text, provider="futurespeak-mail", field="doctor_report") == text
    assert "Doctor report" not in text.splitlines()[0]



def test_a_cleared_file_does_not_bring_off_record_figures_back(weekly):
    """The mark is kept in memory too."""
    from agent_friday.services import off_record
    weekly.answer("weekly")
    weekly.install_hooks()
    off_record.end()
    weekly._path().write_text("{}", encoding="utf-8")
    assert "left out this week" in weekly.prepare()["text"]


def test_a_ledger_with_a_break_offers_nothing(weekly):
    """The answer is read strictly; a break answers never."""
    from agent_friday.services import action_ledger as L
    weekly.answer("weekly")
    p = L.live_path()
    p.write_text(p.read_text(encoding="utf-8").replace('"weekly"', '"WEEKLY"'), encoding="utf-8")
    L.reset_caches()
    st = weekly.status()
    assert st["answer"] == "never" and st["ledger_unreadable"] is True and st["item"] is None


def test_a_never_still_holds_after_its_receipt_is_archived(weekly, monkeypatch):
    """The answer is read from the archive too."""
    import time as _t
    from agent_friday.services import action_ledger as L
    weekly.answer("never")
    with L._TLOCK:
        L._check_dir()
        with L._lock():
            L._seal_live_locked(L.ledger_dir(), L.live_path())
    L.reset_caches()
    L.append("activity", {"kind": "x"})
    assert L.archive_old(now=_t.time() + 800 * 86400, days=730)["archived"] == 1
    assert weekly.status()["answer"] == "never" and weekly.next_item() is None
