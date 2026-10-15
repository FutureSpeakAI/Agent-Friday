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
        jg._RAW_LOADED.update(done=False, overflow=False, unreadable=False)
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
    tbird = tmp_path / "Sent"
    tbird.write_bytes(b"\xef\xbb\xbf\nFrom - Mon Jan 05 10:00:00 2026\nX-Mozilla-Status: 0001\n")
    ofx = tmp_path / "download"
    ofx.write_bytes(b"OFXHEADER:100\nDATA:OFXSGML\n")
    plain = tmp_path / "readme"
    plain.write_bytes(b"From the start, this is just notes.\n")
    assert rr.kind_of(inbox) == "mail" and rr.kind_of(ofx) == "finance" and rr.kind_of(plain) is None
    assert rr.kind_of(tbird) == "mail", "Thunderbird's separator, after a BOM and a blank line"
    photo = tmp_path / "holiday.jpg"
    photo.write_bytes(b"From someone@example.test")
    assert rr.kind_of(photo) is None, "a file with its own extension is not opened to look"


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
    note = kw["notes"][str(p.resolve())]
    assert "a mail archive" in note and "would read it" in note
    assert "note" not in items[0], "Friday's note travels as a keyword, never inside an item"
    assert kw["merge"] is False, "never folded into another request's card"
    assert "only with your yes for this one file" in kw["reason"]
    assert "never-send list still applies" in kw["reason"]
    fg.on_file_read(p, ARCHIVE)
    assert len(cards) == 1, "read again the same day: no second card"


def test_a_long_statement_page_is_held_line_by_line_and_sentence_by_sentence(cards, tmp_path):
    lines = ["%02d/10 CARD PURCHASE GROCERY STORE NUMBER %04d   $%d.%02d" % (d, d * 7, d * 3, d) for d in range(1, 90)]
    page = "\n".join(lines) + "\nInterest charged this period was computed on your average daily balance. "
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
    jg._RAW_LOADED.update(done=False, overflow=False, unreadable=False)        # a new process
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


def test_fridays_own_page_notes_are_never_held(cards, tmp_path):
    body = ARCHIVE + "\n\n[... 40 more lines: continue with read_file offset=2001.]\n...[extraction truncated to the first pages of this document]"
    p = _archive(tmp_path, body=body)
    fg.on_file_read(p, body)
    assert not jg.never_send_hits("[... 12 more lines: continue with read_file offset=2001.]")
    assert not jg.never_send_hits("...[extraction truncated to the first pages of this document]")
    from agent_friday.services.library.envelope import FILE_PREAMBLE
    assert not jg.never_send_hits(FILE_PREAMBLE)
    assert jg.never_send_hits(PARA)


def test_a_statements_short_lines_are_held_whole(cards, tmp_path):
    qif = "!Type:Bank\nD01/03/2026\nT-23.99\nPGROCERY 4471\n^\n"
    p = _archive(tmp_path, "checking.qif", qif)
    fg.on_file_read(p, qif)
    assert jg.never_send_hits("the lines were\nT-23.99\nPGROCERY 4471\nin the file")
    assert not jg.never_send_hits("it cost T-23.99 at PGROCERY 4471"), "matched only as whole lines"
    assert not jg.never_send_hits("T-23.99"), "one short line alone is not a record"


def test_a_json_escaped_payload_is_checked_as_text(cards, tmp_path):
    import json
    p = _archive(tmp_path)
    fg.on_file_read(p, ARCHIVE)
    payload = json.dumps({"tool_result": "Quoted: \u201c" + PARA + "\u201d\n" + PARA})
    assert jg.never_send_hits(payload)


def test_a_store_that_cannot_be_written_fails_closed_until_a_write_succeeds(cards, tmp_path, monkeypatch):
    from agent_friday.services import credential_store as cs
    real = cs.write_secret
    monkeypatch.setattr(cs, "write_secret", lambda *a, **k: (_ for _ in ()).throw(OSError("no key")))
    p = _archive(tmp_path)
    fg.on_file_read(p, ARCHIVE)
    assert jg.never_send_hits("An unrelated message that is long enough to count."), "fail closed"
    jg._RAW_HOLDS.clear()
    jg._RAW_LOADED.update(done=False, overflow=False, unreadable=False)        # a restart
    assert jg.never_send_hits("An unrelated message that is long enough to count."), "still closed after a restart"
    monkeypatch.setattr(cs, "write_secret", real)
    other = _archive(tmp_path, "Old.mbox", "Another archive with a line that is long enough.")
    fg.on_file_read(other, "Another archive with a line that is long enough.")
    assert not jg.never_send_hits("An unrelated message that is long enough to count."), "repaired by a full write"
    assert jg.never_send_hits("Another archive with a line that is long enough.")


def test_an_unreadable_store_is_never_overwritten(cards, tmp_path):
    store = tmp_path / "home" / "privacy" / "raw_record_holds.bin"
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_bytes(b"not something Friday can decrypt")
    p = _archive(tmp_path)
    fg.on_file_read(p, ARCHIVE)
    assert store.read_bytes() == b"not something Friday can decrypt"
    assert jg.never_send_hits("An unrelated message that is long enough to count.")
    assert jg.raw_hold_status()["fail_closed"] is True


# ── round 3 of the review (v4) ───────────────────────────────────────────────

def test_an_escaped_emoji_does_not_switch_the_floor_off(cards, tmp_path):
    import json
    p = _archive(tmp_path)
    fg.on_file_read(p, ARCHIVE)
    payload = json.dumps({"text": "\U0001F600 " + PARA + "\n" + PARA})      # "\ud83d\ude00"
    assert "\\ud83d" in payload
    assert jg.never_send_hits(payload)
    lone = json.dumps({"text": "\ud83d " + PARA + "\n" + PARA})          # half an emoji
    assert jg.never_send_hits(lone)


def test_a_never_send_check_that_breaks_holds_the_payload(tmp_path, monkeypatch):
    def boom(_t):
        raise UnicodeEncodeError("utf-8", "x", 0, 1, "surrogates not allowed")
    monkeypatch.setattr(jg, "never_send_hits", boom)
    monkeypatch.setattr(eg, "is_unrestricted_cloud", lambda: True)
    with pytest.raises(eg.NeverSendBlocked):
        eg._gate_text("An ordinary sentence with nothing in it.", "anthropic", "prompt",
                      log_path=tmp_path / "egress.jsonl")


def test_a_command_or_program_that_reads_a_raw_record_is_not_run(cards, tmp_path):
    p = _archive(tmp_path)
    why = fg.raw_refusal_for_command('Get-Content "%s" | Select -First 50' % p)
    assert why and "read_file" in why and "All mail.mbox" in why
    assert cards, "and the owner is asked about that file"
    assert fg.raw_refusal_for_command("Get-ChildItem *.mbox | Get-Content")
    assert fg.raw_refusal_for_command("import pandas; pandas.read_csv('transactions_2026.csv')")
    assert fg.raw_refusal_for_command("dir C:\\Users") is None
    fg.create_file_grant(str(p))
    assert fg.raw_refusal_for_command('type "%s"' % p) is None, "allowed by the owner: runs"


def test_the_sandbox_and_shell_tools_refuse_before_running(cards, tmp_path, monkeypatch):
    from agent_friday.services import agent, code_sandbox
    ran = []
    monkeypatch.setattr(code_sandbox, "run", lambda *a, **k: ran.append(a) or {"ok": True, "stdout": ""})
    p = _archive(tmp_path)
    out = agent._tool_run_sandboxed({"code": "print(open(r'%s').read())" % p})
    assert out.startswith("Not run") and not ran


def test_reading_a_held_record_returns_a_notice_not_its_contents(cards, tmp_path):
    from agent_friday.services import agent, tool_hooks
    p = _archive(tmp_path)
    ctx = tool_hooks.HookContext(tool_name="read_file", input={"path": str(p)})
    out = agent._hook_file_grant_registration(ctx, ARCHIVE)
    assert PARA not in out and "All mail.mbox" in out and "a mail archive" in out
    row = [h for h in tool_hooks.list_hooks() if h["name"] == "file_grant_registration"][0]
    assert row["critical"] is True, "cannot be switched off in settings"
    fg.create_file_grant(str(p))
    assert agent._hook_file_grant_registration(ctx, ARCHIVE) == ARCHIVE


def test_a_line_is_held_whether_it_was_scrubbed_before_or_after(cards, tmp_path):
    body = ARCHIVE + "\nCall the branch about the overdraft at 312-555-0147 before Friday please."
    p = _archive(tmp_path, body=body)
    fg.on_file_read(p, body)
    import agent_friday.core as core
    scrubbed = core._scrub_pii("Call the branch about the overdraft at 312-555-0147 before Friday please.")[0]
    assert "[PII:" in scrubbed
    assert jg.never_send_hits(scrubbed)


def test_a_full_list_stays_recorded_after_a_write_is_repaired(cards, tmp_path, monkeypatch):
    jg._fail_closed("the hold list is full")
    jg._fail_closed(jg._WRITE_FAILED)
    p = _archive(tmp_path)
    fg.on_file_read(p, ARCHIVE)                          # a good write
    st = jg.raw_hold_status()
    assert st["fail_closed"] and "full" in st["why"] and jg._WRITE_FAILED not in st["why"]


def test_a_hold_that_cannot_even_be_loaded_fails_closed(cards, tmp_path, monkeypatch):
    p = _archive(tmp_path)
    fg.on_file_read(p, ARCHIVE)
    jg._RAW_HOLDS.clear()
    jg._RAW_LOADED.update(done=False, overflow=False, unreadable=False)
    monkeypatch.setattr(jg, "_raw_failed_path", lambda: (_ for _ in ()).throw(RuntimeError("no home")))
    assert jg.raw_record_hits("A message that is long enough to be a unit of text.")


def test_what_every_inbox_and_statement_share_is_not_held(cards, tmp_path):
    msg = ("From: a@shop.example\nContent-Type: text/plain; charset=utf-8\n\n%s\n"
           "Let me know if you have any questions.\nThanks for reaching out.\n"
           "To unsubscribe from these emails click the link below this line.\n")
    body = msg % "Your order 4471 for the blue kettle has shipped to the cottage." + \
        msg % "The refund for the lamp was declined because the box was opened."
    p = _archive(tmp_path, body=body)
    fg.on_file_read(p, body)
    mine = "Let me know if you have any questions.\nThanks for reaching out."
    assert not jg.never_send_hits(mine), "the person's own everyday words"
    assert not jg.never_send_hits("Content-Type: text/plain; charset=utf-8\nTo unsubscribe from these emails click the link below this line.")
    assert jg.never_send_hits("Your order 4471 for the blue kettle has shipped to the cottage.")
    ofx = "OFXHEADER:100\n<STMTTRN>\n<TRNAMT>-23.99\n<NAME>GROCERY 4471\ndescription\nbalance\n</STMTTRN>\n"
    q = _archive(tmp_path, "checking.ofx", ofx)
    fg.on_file_read(q, ofx)
    assert not jg.never_send_hits("<STMTTRN>\ndescription\nbalance\nOFXHEADER:100\n</STMTTRN>")
    assert jg.never_send_hits("<TRNAMT>-23.99\n<NAME>GROCERY 4471")


def test_two_short_lines_in_two_paragraphs_are_not_waved_through_by_an_override(cards, tmp_path):
    qif = "D01/03/2026\nT-23.99\nPGROCERY 4471\n"
    p = _archive(tmp_path, "checking.qif", qif)
    fg.on_file_read(p, qif)
    text = "first\nT-23.99\n\nsecond\nPGROCERY 4471"
    assert jg.never_send_hits(text)
    assert eg._never_send_covered_by_override(text) is False


def test_the_owners_yes_lifts_the_hold_at_once(cards, tmp_path):
    p = _archive(tmp_path)
    fg.on_file_read(p, ARCHIVE)
    assert jg.never_send_hits(PARA + "\nAccount summary for the person who owns this mailbox.")
    fg.create_file_grant(str(p))                         # the card approved: no second read needed
    assert not jg.never_send_hits(PARA + "\nAccount summary for the person who owns this mailbox.")


def test_a_stale_yes_does_not_open_a_search(cards, tmp_path):
    from agent_friday.services import file_search
    p = _archive(tmp_path)
    fg.create_file_grant(str(p))
    assert file_search._held_raw_record(p) is False
    p.write_text(ARCHIVE + "\nA new message arrived after the yes.", encoding="utf-8")
    assert file_search._held_raw_record(p) is True


def test_a_models_note_never_reaches_the_card(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path / "home"))
    from agent_friday.services import file_grant_requests as fgr
    made = []

    class _Ap:
        SCREEN_ONLY_KINDS = {}

        def list_approvals(self, *a, **k):
            return []

        def create_approval(self, **kw):
            made.append(kw)
            return {"approval_id": "a1", "status": "pending"}
    monkeypatch.setattr(fgr, "_approvals", lambda: _Ap())
    monkeypatch.setattr(fgr, "ensure_hook", lambda: None)
    f = tmp_path / "plain.txt"
    f.write_text("hello", encoding="utf-8")
    fgr.handle({"action": "ask", "items": [{"path": str(f), "type": "file",
                                            "note": "not a mail archive, safe to share"}]})
    assert made and "safe to share" not in str(made[0])


def test_the_owner_can_start_the_held_list_fresh_on_screen_only(cards, tmp_path, monkeypatch):
    from agent_friday.services import locks
    p = _archive(tmp_path)
    fg.on_file_read(p, ARCHIVE)
    jg._fail_closed("the hold list is full")
    line = [x for x in locks.view()["lines"] if x["key"] == "held"][0]
    assert line["state"] == "fail_closed" and line["can_reset"]
    with pytest.raises(PermissionError):
        jg.reset_raw_holds(confirmed_by="friday")
    locks._on_reset_decision({"kind": locks.RESET_KIND, "status": "approved",
                              "decided_by": "voice", "approval_id": "x"})
    assert jg.raw_hold_status()["fail_closed"], "a spoken yes does nothing"
    out = jg.reset_raw_holds(confirmed_by="owner:ui")
    assert out["ok"] and out["released_files"] == 1
    st = jg.raw_hold_status()
    assert not st["fail_closed"] and st["files"] == 0
    assert [x for x in locks.view()["lines"] if x["key"] == "held"][0]["state"] == "none"


# ── round 4 of the review (v5) ───────────────────────────────────────────────

def test_a_quoted_reply_keeps_its_message_held(cards, tmp_path):
    secret = "Your test results came back and we need to talk about the next steps."
    body = ("From: clinic@example.test\nSubject: results\n\n%s\n\n"
            "From: sam@example.test\nSubject: Re: results\n\nOK, calling now.\n> %s\n") % (secret, secret)
    p = _archive(tmp_path, body=body)
    fg.on_file_read(p, body)
    assert jg.never_send_hits(secret)


def test_a_long_sentence_is_held_however_it_starts(cards, tmp_path):
    line = "Thank you for your payment of $2,450.00 to the fertility clinic for March."
    p = _archive(tmp_path, body=ARCHIVE + "\n" + line)
    fg.on_file_read(p, ARCHIVE + "\n" + line)
    assert jg.never_send_hits(line)


def test_one_specific_short_line_is_enough(cards, tmp_path):
    line = "Amount owed to Dr Kim: $8,400"
    p = _archive(tmp_path, body=ARCHIVE + "\n" + line)
    fg.on_file_read(p, ARCHIVE + "\n" + line)
    assert jg.never_send_hits("fyi " + "\n" + line)


def test_matching_does_not_depend_on_the_watchlist(cards, tmp_path, monkeypatch):
    line = "Sam Rivera still owes the landlord for the broken window in flat 4."
    p = _archive(tmp_path, body=ARCHIVE + "\n" + line)
    fg.on_file_read(p, ARCHIVE + "\n" + line)
    import agent_friday.core as core
    monkeypatch.setattr(core, "_scrub_pii", lambda t: (str(t).replace("Rivera", "[PII:name:0badf00d]"), {}))
    assert jg.never_send_hits(line), "a name added to the watchlist later does not let the line go"


def test_a_critical_post_hook_that_breaks_withholds_the_result():
    from agent_friday.services import tool_hooks as h
    def boom(ctx, result):
        raise RuntimeError("broken")
    h.register_post_hook(boom, name="t_crit_post", priority=97, tools={"t_tool"}, critical=True)
    try:
        out = h.run_post_hooks(h.HookContext(tool_name="t_tool", input={}), "the secret result")
        assert "secret" not in out and "withheld" in out
    finally:
        h.unregister_hook("t_crit_post")


def test_ordinary_code_and_downloads_are_not_mistaken_for_records():
    from agent_friday.services import raw_records as rr
    for code in ("print(e.msg)", "x = self.msg + record.msg", "curl -O https://bank.example/statement.pdf",
                 "pip install mail.parser", "git log --oneline"):
        assert rr.named_in(code) == [], code
    assert rr.named_in("cat ~/Mail/archive.mbox")
    assert rr.kind_of("Chase1234_Activity_20261001.CSV") == "finance"
    assert rr.kind_of("Checking1.csv") == "finance" and rr.kind_of("activity.pdf") is None


def test_program_output_that_reads_like_mail_or_a_statement_is_withheld(cards):
    grep = "exports/All mail.mbox:4471:Your balance is due on the 14th, please pay now."
    assert fg.raw_output_notice(grep)
    mbox = "From sam@example.test Mon Oct 12 10:00:00 2026\nSubject: hi\n\nbody"
    assert fg.raw_output_notice(mbox)
    assert fg.raw_output_notice("OFXHEADER:100\n<STMTTRN><TRNAMT>-23.99")
    hdrs = "From: a@x.test\nTo: b@x.test\nSubject: rent\n\nThe rent is late."
    assert fg.raw_output_notice(hdrs)
    assert fg.raw_output_notice("total 4\n-rw-r--r-- 1 sam sam 120 notes.txt\nhello world") is None
    assert fg.raw_output_notice("Traceback: raise ValueError(e.msg) - bad value") is None


def test_the_codebase_reader_holds_a_record_too(cards, tmp_path, monkeypatch):
    from agent_friday.services import agent, codebases
    p = _archive(tmp_path)
    monkeypatch.setattr(agent, "_codebase_in_scope", lambda inp: {"id": "cb1"})
    monkeypatch.setattr(codebases, "path_of", lambda cid, rel: p)
    monkeypatch.setattr(codebases, "read", lambda cid, rel: ARCHIVE)
    out = agent._tool_codebase_read({"path": "All mail.mbox"})
    assert out["status"] == "held" and PARA not in out["content"]
    assert jg.never_send_hits(PARA + "\nAccount summary for the person who owns this mailbox.")


def test_any_card_that_lists_a_record_says_what_it_is(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path / "home"))
    from agent_friday.services import file_grant_requests as fgr
    made = []

    class _Ap:
        SCREEN_ONLY_KINDS = {}

        def list_approvals(self, *a, **k):
            return []

        def create_approval(self, **kw):
            made.append(kw)
            return {"approval_id": "a1", "status": "pending"}
    monkeypatch.setattr(fgr, "_approvals", lambda: _Ap())
    monkeypatch.setattr(fgr, "ensure_hook", lambda: None)
    p = _archive(tmp_path)
    fgr.handle({"action": "ask", "items": [{"path": str(p), "type": "file"}],
                "reason": "just some notes"})
    assert "a mail archive" in made[0]["action_description"]


# ── round 5 of the review (v6) ───────────────────────────────────────────────

def test_a_pattern_is_judged_by_the_files_it_matches(cards, tmp_path):
    d = tmp_path / "Downloads"
    d.mkdir()
    (d / "Chase_Activity_2026.csv").write_text("date,amount\n10/01,23.99\n", encoding="utf-8")
    (d / "recipes.csv").write_text("name\nsoup\n", encoding="utf-8")
    assert fg.raw_refusal_for_command("Get-Content %s | Select-String Amazon" % (d / "*.csv"))
    assert cards, "the owner is asked about the matching file"
    (d / "Chase_Activity_2026.csv").unlink()
    assert fg.raw_refusal_for_command("Get-Content %s" % (d / "*.csv")) is None


def test_a_codebase_command_that_reads_a_record_is_not_run(cards, tmp_path, monkeypatch):
    from agent_friday.services import codebases
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "inbox.mbox").write_text(ARCHIVE, encoding="utf-8")
    monkeypatch.setattr(codebases, "load", lambda cid: {"id": cid})
    monkeypatch.setattr(codebases, "repo_path", lambda cid: repo)
    monkeypatch.setattr(codebases, "is_friday_checkout", lambda r: False)
    out = codebases.run("cb1", "cat inbox.mbox")
    assert out["status"] == "refused" and "inbox.mbox" in out["say"]


def test_claudes_agent_is_not_handed_a_folder_with_a_record_in_it(cards, tmp_path, monkeypatch):
    from agent_friday.services import agent, claude_engine, codebases
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    (repo / "data" / "checking.ofx").write_text("OFXHEADER:100\n", encoding="utf-8")
    monkeypatch.setattr(agent, "_codebase_in_scope", lambda inp: {"id": "cb1", "seats": {"engine": "claude_agent"}})
    monkeypatch.setattr(codebases, "repo_path", lambda cid: repo)
    ran = []
    monkeypatch.setattr(claude_engine, "run_task", lambda *a, **k: ran.append(a) or {"status": "ok"})
    monkeypatch.setattr(agent, "_codebase_task_gate", lambda *a, **k: None)
    out = agent._tool_codebase_agent({"task": "tidy up"})
    assert out["status"] == "refused" and "checking.ofx" in out["say"] and not ran


def test_names_addresses_and_separators_are_not_held(cards, tmp_path):
    body = ("From: Christopher Montgomery <chris@example.test>\nTo: Sam Doe <sam@example.test>\n\n"
            "-----Original Message-----\n---------- Forwarded message ---------\n"
            "The surveyor found damp in the north wall and wants another visit.\n")
    p = _archive(tmp_path, "one.eml", body)
    fg.on_file_read(p, body)
    for line in ("-----Original Message-----", "From: Christopher Montgomery <chris@example.test>",
                 "To: Sam Doe <sam@other.example>", "---------- Forwarded message ---------"):
        assert not jg.never_send_hits(line), line
    assert jg.never_send_hits("The surveyor found damp in the north wall and wants another visit.")


def test_a_footer_with_a_figure_in_it_is_held(cards, tmp_path):
    line = "You are receiving this because account 1234 is $4,210 past due as of today."
    p = _archive(tmp_path, body=ARCHIVE + "\n" + line)
    fg.on_file_read(p, ARCHIVE + "\n" + line)
    assert jg.never_send_hits(line)


def test_a_line_that_looks_like_a_street_is_still_held(cards, tmp_path):
    body = ARCHIVE + "\nThe 2 reports from Dr Smith came back and the lump is benign.\n"
    p = _archive(tmp_path, body=body)
    fg.on_file_read(p, body)
    assert jg.never_send_hits("The 2 reports from Dr Smith came back and the lump is benign.")


def test_developer_output_is_not_withheld(cards):
    gitlog = ("commit 0123abcd\nAuthor: Dev <dev@example.test>\nDate:   Mon Oct 12 10:00 2026\n\n"
              "    Fix the parser\n")
    diff = ("diff --git a/x.py b/x.py\n+    if '<STMTTRN>' in text:\n+OFXHEADER = 'OFXHEADER:100'\n"
            " <TRNAMT> stays\n")
    grep_src = "src/raw_records.py:12:_OFX = re.compile(r'<STMTTRN>')"
    for out in (gitlog, diff, grep_src, "report.msg-2026.txt\nnotes.md\n"):
        assert fg.raw_output_notice(out) is None, out
    block = "From: a@x.test\nTo: b@x.test\nSubject: rent\n\nThe rent is late."
    assert fg.raw_output_notice(block)
    assert fg.raw_output_notice("Downloads/Chase_Activity_2026.csv:3:10/01,AMAZON,23.99")


def test_the_locks_page_asks_only_about_a_file_it_holds(cards, tmp_path):
    from agent_friday.services import locks
    p = _archive(tmp_path)
    fg.on_file_read(p, ARCHIVE)
    shown = [x for x in locks.view()["lines"] if x["key"] == "held"][0]["held_files"]
    assert shown == [str(p)], "shown as spelled, not folded to lower case"
    assert locks.ask_for_held(shown[0])["ok"] and len(cards) == 2
    assert locks.ask_for_held(str(tmp_path / "other.mbox"))["ok"] is False
