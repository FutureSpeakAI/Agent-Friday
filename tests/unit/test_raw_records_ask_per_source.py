"""Raw records ask per source (owner decision O7, first-run spec E-03).

Even under "cloud for everything", a mail archive or a bank statement goes to a
cloud model only with a yes for that one file. A folder's cloud permission does
not cover it; until the yes, every line and sentence of it is held from every
cloud payload (an excerpt as well as the whole), across a restart, and a
file-access card of its own names the file, says what it is and who would read
it. The never-send floor stays: a deny mark is never lifted by a raw record's yes.
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
    if hasattr(jg, "_RAW_HOLDS"):
        jg._RAW_HOLDS.clear()
        jg._RAW_LOADED.update(done=False, overflow=False)
    getattr(fg, "_RAW_ASKED", {}).clear()


@pytest.fixture
def cards(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path / "home"))
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
PARA = "Dear customer, your balance is due on the 14th."


def _archive(tmp_path, name="All mail.mbox", body=ARCHIVE):
    p = tmp_path / "exports" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_what_counts_as_a_raw_record(tmp_path):
    from agent_friday.services import raw_records as rr
    assert rr.kind_of("C:/x/All mail Including Spam.mbox") == "mail"
    assert rr.kind_of("x.PST") == "mail" and rr.kind_of("note.eml") == "mail"
    assert rr.kind_of("export.mbox.txt") == "mail"
    assert rr.kind_of("checking.ofx") == "finance"
    assert rr.kind_of("Bank Statement March.pdf") == "finance"
    assert rr.kind_of("eStmt_2026-09.pdf") == "finance"
    assert rr.kind_of("transactions_2026.csv") == "finance"
    assert rr.kind_of("holiday photos.pdf") is None and rr.kind_of("notes.md") is None
    inbox = tmp_path / "Inbox"
    inbox.write_bytes(b"From someone@example.test Mon Oct 12 10:00:00 2026\nSubject: hi\n\nbody\n")
    ofx = tmp_path / "download"
    ofx.write_bytes(b"OFXHEADER:100\nDATA:OFXSGML\n")
    plain = tmp_path / "readme"
    plain.write_bytes(b"From the start, this is just notes.\n")
    assert rr.kind_of(inbox) == "mail" and rr.kind_of(ofx) == "finance" and rr.kind_of(plain) is None


def test_a_folder_permission_does_not_cover_a_mail_archive(cards, tmp_path):
    p = _archive(tmp_path)
    fg.create_scope_grant(str(p.parent), "folder", 7)
    other = p.parent / "notes.txt"
    other.write_text("ordinary notes", encoding="utf-8")
    assert fg.check_grant(other).state == "active"
    assert fg.check_grant(p).state == "none"


def test_reading_it_holds_it_from_every_cloud_payload_and_asks_on_its_own_card(cards, tmp_path, monkeypatch):
    p = _archive(tmp_path)
    out = fg.on_file_read(p, ARCHIVE)
    assert out.state == "held_raw_record"
    assert jg.never_send_hits("summary: " + PARA), "held like a deny mark, under every posture"
    monkeypatch.setattr(eg, "is_unrestricted_cloud", lambda: True)
    with pytest.raises(eg.NeverSendBlocked):
        eg._gate_text(PARA, "anthropic", "prompt", log_path=tmp_path / "egress.jsonl")
    items, kw = cards[0]
    assert items[0]["path"] == str(p.resolve()) and items[0]["type"] == "file"
    assert "a mail archive" in items[0]["note"] and "would read it" in items[0]["note"]
    assert kw["merge"] is False, "never folded into another request's card"
    assert "only with your yes for this one file" in kw["reason"]
    assert "never-send list still applies" in kw["reason"]
    fg.on_file_read(p, ARCHIVE)
    assert len(cards) == 1, "read again the same day: no second card"


def test_a_long_statement_page_is_held_line_by_line_and_sentence_by_sentence(cards, tmp_path):
    lines = ["%02d/10 CARD PURCHASE GROCERY STORE NUMBER %04d   $%d.%02d" % (d, d * 7, d * 3, d) for d in range(1, 90)]
    page = "\n".join(lines) + "\nInterest charged this period was computed on your average daily balance. " * 3
    assert len(page) > 4000
    p = _archive(tmp_path, "Bank Statement.pdf", page)
    fg.on_file_read(p, page)
    assert jg.never_send_hits("from the statement: " + lines[41])
    assert jg.never_send_hits("Note: Interest charged this period was computed on your average daily balance.")
    assert not jg.never_send_hits("A sentence that never appeared in any statement at all.")


def test_the_hold_survives_a_restart(cards, tmp_path):
    p = _archive(tmp_path)
    fg.on_file_read(p, ARCHIVE)
    raw = (tmp_path / "home" / "privacy" / "raw_record_holds.bin").read_bytes()
    assert b"balance" not in raw and b"customer" not in raw, "hashes, encrypted, never the text"
    jg._RAW_HOLDS.clear()
    jg._RAW_LOADED.update(done=False, overflow=False)        # a new process
    assert jg.never_send_hits(PARA)


def test_a_yes_lifts_that_files_hold_but_not_a_line_another_held_file_shares(cards, tmp_path):
    p = _archive(tmp_path)
    other = _archive(tmp_path, "Old mail.mbox", "Another archive.\n\n" + PARA)
    fg.on_file_read(p, ARCHIVE)
    fg.on_file_read(other, "Another archive.\n\n" + PARA)
    fg.create_file_grant(str(p))
    assert fg.on_file_read(p, ARCHIVE).state == "active"
    assert not jg.never_send_hits("Account summary for the person who owns this mailbox.")
    assert jg.never_send_hits(PARA), "still held: the other archive is not allowed"


def test_a_deny_mark_is_never_lifted_by_a_raw_records_yes(cards, tmp_path):
    p = _archive(tmp_path)
    denied = tmp_path / "private.txt"
    shared = "Account summary for the person who owns this mailbox."
    denied.write_text(shared, encoding="utf-8")
    fg.create_deny_mark(str(denied), "file")
    fg.on_file_read(denied, shared)
    fg.on_file_read(p, ARCHIVE)
    assert jg.never_send_hits(PARA), "held before the yes"
    fg.create_file_grant(str(p))
    assert fg.on_file_read(p, ARCHIVE).state == "active"
    assert not jg.never_send_hits(PARA)
    assert jg.never_send_hits(shared), "a deny mark stays, whatever the raw record's yes"


def test_a_search_snippet_of_a_raw_record_shows_no_content(cards, tmp_path):
    from agent_friday.services import file_search
    p = _archive(tmp_path)
    assert file_search._held_raw_record(p) is True
    fg.create_file_grant(str(p))
    assert file_search._held_raw_record(p) is False
    assert file_search._held_raw_record(tmp_path / "exports" / "notes.txt") is False


def test_with_everything_kept_on_this_pc_nothing_is_asked(cards, tmp_path, monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings", lambda: {"model_routing": {"mode": "local_only"}})
    p = _archive(tmp_path)
    assert fg.on_file_read(p, ARCHIVE).state == "held_raw_record"
    assert cards == []
