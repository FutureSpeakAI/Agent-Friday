"""Raw records ask per source (owner decision O7, first-run spec E-03).

Even under "cloud for everything", a mail archive or a bank statement goes to a
cloud model only with a yes for that one file. A folder's cloud permission does
not cover it; until the yes, its paragraphs are held from every cloud payload
exactly as a deny mark holds them, and a file-access card names the file, what
it is, and who would read it. The never-send floor stays: a deny mark is never
lifted by a raw record's yes.
"""
from __future__ import annotations

import pytest

from agent_friday.services import egress_gate as eg
from agent_friday.services import file_grants as fg
from agent_friday.services import judgment_gate as jg


def _clear():
    fg._invalidate_cache()
    with eg._TRUSTED_LOCK:
        eg._PUBLIC_PARAS.clear()
        eg._PUBLIC_ORIGINS.clear()
        eg._OVERRIDE_PARAS.clear()
    with jg._DENY_LOCK:
        jg._DENY_SPANS.clear()
        for name in ("_USER_DENY_SPANS", "_RAW_RECORD_SPANS"):
            if hasattr(jg, name):
                getattr(jg, name).clear()
    getattr(fg, "_RAW_ASKED", {}).clear()


@pytest.fixture
def cards(tmp_path, monkeypatch):
    ledger = tmp_path / "file_grants.jsonl"
    monkeypatch.setattr(fg, "_ledger_path", lambda: ledger)
    _clear()
    raised = []
    from agent_friday.services import file_grant_requests
    monkeypatch.setattr(file_grant_requests, "request_access",
                        lambda items, **kw: raised.append((items, kw)) or {"ok": True})
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings", lambda: {"model_routing": {"mode": "cloud_only"}})
    yield raised
    _clear()


ARCHIVE = ("From: Weekly Deals <news@shop.example>\nSubject: Your account statement is ready\n\n"
           "Dear customer, your balance is due on the 14th.\n\n"
           "Account summary for the person who owns this mailbox.")


def _archive(tmp_path, name="All mail.mbox"):
    p = tmp_path / "exports" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(ARCHIVE, encoding="utf-8")
    return p


def test_what_counts_as_a_raw_record():
    from agent_friday.services import raw_records as rr
    assert rr.kind_of("C:/x/All mail Including Spam.mbox") == "mail"
    assert rr.kind_of("x.PST") == "mail" and rr.kind_of("note.eml") == "mail"
    assert rr.kind_of("checking.ofx") == "finance"
    assert rr.kind_of("Bank Statement March.pdf") == "finance"
    assert rr.kind_of("transactions_2026.csv") == "finance"
    assert rr.kind_of("holiday photos.pdf") is None and rr.kind_of("notes.md") is None


def test_a_folder_permission_does_not_cover_a_mail_archive(cards, tmp_path):
    p = _archive(tmp_path)
    fg.create_scope_grant(str(p.parent), "folder", 7)
    other = p.parent / "notes.txt"
    other.write_text("ordinary notes", encoding="utf-8")
    assert fg.check_grant(other).state == "active"
    assert fg.check_grant(p).state == "none"


def test_reading_it_holds_it_from_every_cloud_payload_and_asks_on_a_card(cards, tmp_path, monkeypatch):
    p = _archive(tmp_path)
    out = fg.on_file_read(p, ARCHIVE)
    assert out.state == "held_raw_record"
    para = "Dear customer, your balance is due on the 14th."
    assert jg.never_send_hits("summary: " + para), "held like a deny mark, under every posture"
    monkeypatch.setattr(eg, "is_unrestricted_cloud", lambda: True)
    with pytest.raises(eg.NeverSendBlocked):
        eg._gate_text(para, "anthropic", "prompt", log_path=tmp_path / "egress.jsonl")
    items, kw = cards[0]
    assert items == [{"path": str(p.resolve()), "type": "file"}]
    assert "a mail archive" in kw["reason"] and "only with your yes for this one file" in kw["reason"]
    assert "never-send list still applies" in kw["reason"]
    # read again the same day: no second card
    fg.on_file_read(p, ARCHIVE)
    assert len(cards) == 1


def test_a_yes_for_that_file_lifts_the_hold_but_never_a_deny_mark(cards, tmp_path):
    p = _archive(tmp_path)
    denied = tmp_path / "private.txt"
    shared = "Account summary for the person who owns this mailbox."
    denied.write_text(shared, encoding="utf-8")
    fg.create_deny_mark(str(denied), "file")
    fg.on_file_read(denied, shared)
    fg.on_file_read(p, ARCHIVE)
    assert jg.never_send_hits("Dear customer, your balance is due on the 14th."), "held before the yes"
    fg.create_file_grant(str(p))
    out = fg.on_file_read(p, ARCHIVE)
    assert out.state == "active"
    assert not jg.never_send_hits("Dear customer, your balance is due on the 14th.")
    assert jg.never_send_hits(shared), "a deny mark stays, whatever the raw record's yes"


def test_with_everything_kept_on_this_pc_nothing_is_asked(cards, tmp_path, monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings", lambda: {"model_routing": {"mode": "local_only"}})
    p = _archive(tmp_path)
    assert fg.on_file_read(p, ARCHIVE).state == "held_raw_record"
    assert cards == []
