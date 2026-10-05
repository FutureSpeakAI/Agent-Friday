"""screen_select op=fill: Friday writes into a field the owner can see, and never sends, saves or creates.

The page registers the fields a person types into; a field it did not register is refused. A fill goes
through the page's own state (so the owner reads it, edits it or undoes it), carries what in it came from
something Friday read, and the send card that follows says the words were hers. Only the owner's own button
sends: no request to a send, save or create route is made by a fill.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import agent, desktop_bus, screen_stage as ss, taint
from tests.screen_fixtures import Page, newsletter_stage, report

FIELDS = [{"key": "reply.body", "label": "Reply", "filled_by": None},
          {"key": "reply.subject", "label": "Subject", "filled_by": None},
          {"key": "reply.to", "label": "To", "filled_by": None}]


@pytest.fixture(autouse=True)
def _clean():
    desktop_bus.reset()
    ss.reset_fills()
    taint.reset()
    yield
    desktop_bus.reset()
    ss.reset_fills()
    taint.reset()


def _open(fields=FIELDS, **kw):
    st = newsletter_stage(**kw)
    st["fields"] = [dict(f) for f in fields]
    report(st)
    return st


def _fill(**kw):
    return agent._tool_screen_select(dict({"op": "fill", "workspace": "messages"}, **kw))


def _page(monkeypatch, ok=True):
    return Page(monkeypatch, answer={"ok": ok, "field": "reply.body", "undo": True} if ok else {"ok": False, "reason": "the field would not take it"})


def test_a_registered_field_is_written_and_the_command_carries_exactly_the_text(monkeypatch):
    _open()
    page = _page(monkeypatch)
    out = _fill(field="reply.body", text="Thanks, Tuesday works. I'll bring the draft.")
    assert out.startswith("FILL_OK written into Reply. Nothing is sent"), out
    sent = page.sent[-1][0]
    assert sent["type"] == "fill" and sent["field"] == "reply.body" and sent["mode"] == "replace"
    assert sent["text"] == "Thanks, Tuesday works. I'll bring the draft." and sent["workspace"] == "messages"


def test_a_field_the_page_did_not_register_is_refused_and_nothing_is_sent(monkeypatch):
    _open()
    page = _page(monkeypatch)
    out = _fill(field="send_button", text="x")
    assert out.startswith("FILL_FAIL") and "reply.body (Reply)" in out, out
    assert page.sent == []


def test_with_no_field_open_it_says_there_is_nothing_to_write_in(monkeypatch):
    _open(fields=[])
    page = _page(monkeypatch)
    assert "nothing open that I can write in" in _fill(field="reply.body", text="x")
    assert page.sent == []


def test_a_page_that_did_not_take_it_is_a_failure_not_a_success(monkeypatch):
    _open()
    _page(monkeypatch, ok=False)
    assert _fill(field="reply.body", text="hello") == "FILL_FAIL: the field would not take it"


def test_an_empty_or_huge_text_or_an_unseen_screen_is_refused(monkeypatch):
    _open()
    page = _page(monkeypatch)
    assert _fill(field="reply.body", text="   ").startswith("FILL_FAIL")
    assert _fill(field="reply.body", text="x" * (ss.FILL_MAX + 1)).startswith("FILL_FAIL")
    desktop_bus.reset()
    assert _fill(field="reply.body", text="hello") == "FILL_FAIL: I can't see that screen right now."
    assert page.sent == [] or all(a[0]["type"] != "fill" for a in page.sent)


def test_insert_adds_after_what_is_there(monkeypatch):
    _open()
    page = _page(monkeypatch)
    _fill(field="reply.body", text="PS: and the figures", mode="insert")
    assert page.sent[-1][0]["mode"] == "insert"


def test_a_fill_never_touches_a_send_save_or_create_route(monkeypatch):
    from agent_friday.services import gmail_send, item_actions
    _open()
    _page(monkeypatch)
    called = []
    monkeypatch.setattr(gmail_send, "request_send", lambda *a, **k: called.append("request_send"))
    monkeypatch.setattr(gmail_send, "send", lambda *a, **k: called.append("send"))
    monkeypatch.setattr(item_actions, "propose_email", lambda *a, **k: called.append("propose_email"))
    _fill(field="reply.body", text="Send it to everyone now.")
    assert called == []


def test_text_that_carries_an_address_or_link_Friday_read_in_an_email_is_flagged(monkeypatch):
    token = agent._CURRENT_CONVERSATION.set("conv-fill")
    try:
        taint.note_tool_output("conversation:conv-fill", "search_email", {},
                               json.dumps({"messages": [{"from": "Mallory <mallory@evil.example>", "subject": "invoice",
                                                         "snippet": "pay at http://evil.example/pay-now"}]}))
        _open()
        page = _page(monkeypatch)
        out = _fill(field="reply.body", text="Please send the money to mallory@evil.example, details at http://evil.example/pay-now")
    finally:
        agent._CURRENT_CONVERSATION.reset(token)
    flags = page.sent[-1][0]["flags"]
    assert flags and any("evil.example" in f for f in flags), flags
    assert "Part of it uses something I read" in out and "Nothing is sent" in out


def test_text_the_owner_typed_or_friday_wrote_herself_carries_no_flag(monkeypatch):
    token = agent._CURRENT_CONVERSATION.set("conv-clean")
    try:
        taint.note_user_message("conversation:conv-clean", "tell Dana I'll be late")
        _open()
        page = _page(monkeypatch)
        _fill(field="reply.body", text="Dana, I'll be late: 15 minutes.")
    finally:
        agent._CURRENT_CONVERSATION.reset(token)
    assert page.sent[-1][0]["flags"] == []


def test_the_send_card_says_the_words_were_hers_and_what_to_check():
    ss.remember_fill("reply.body", "Please send the money to mallory@evil.example", ["The address mallory@evil.example came from an email from Mallory."])
    note = ss.fill_note("Hi Dana,\n\nPlease send the money to mallory@evil.example\n\nThanks")
    assert note[0].startswith("Friday wrote this message on your screen") and note[1].startswith("Check: The address")
    assert ss.fill_note("A completely different message that Friday did not write at all") == []
    assert ss.fill_note("short") == []
    src = open(__import__("agent_friday.services.gmail_send", fromlist=["x"]).__file__, encoding="utf-8").read()
    assert "_ss.fill_note(body)" in src, "request_send adds it to the card"


def test_a_cloud_voice_hears_that_it_is_written_and_not_what_it_says(monkeypatch):
    _open()
    _page(monkeypatch)
    tok = agent._CURRENT_SURFACE.set("voice-live")
    try:
        out = _fill(field="reply.body", text="Dear Mum, about the Harbor Legal settlement")
    finally:
        agent._CURRENT_SURFACE.reset(tok)
    assert out == "FILL_OK I've written it in. They read it, change it or undo it, and send it themselves.", out
    assert "Harbor" not in out and "Reply" not in out


def test_the_stage_lists_the_fields_and_says_which_ones_friday_filled():
    st = _open()
    st["fields"][0]["filled_by"] = "friday"
    report(st)
    got = desktop_bus.stage("messages")["fields"]
    assert [(f["key"], f["filled_by"]) for f in got] == [("reply.body", "friday"), ("reply.subject", None), ("reply.to", None)]


def test_screen_fill_is_judged_like_the_rest_of_the_tool_and_has_no_way_to_submit():
    from agent_friday.governance import action_gate as ag
    assert ag.classify("screen_select", {"op": "fill", "field": "reply.body", "text": "hi"})[0] == ag.INTERNAL
    tool = next(t for t in agent.CLAUDE_TOOLS if t["name"] == "screen_select")
    props = set(tool["input_schema"]["properties"])
    assert {"field", "text", "mode"} <= props and not ({"send", "submit", "confirm", "approve"} & props)
    assert taint.TOOL_ROLES["screen_select"] == {"text": "message_body"}
