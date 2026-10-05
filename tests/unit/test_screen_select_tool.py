"""screen_select: ticks rows on the owner's screen and reports what the page confirmed (I2, I4, I7)."""
from __future__ import annotations

import pytest

from agent_friday.services import agent, approvals as ap, desktop_bus
from tests.screen_fixtures import Page, newsletter_stage, ref, report


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    desktop_bus.reset()
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    yield
    desktop_bus.reset()


def _call(**kw):
    return agent._tool_screen_select(kw)


def test_the_ticks_the_page_confirms_are_the_ticks_reported(monkeypatch):
    report(newsletter_stage())
    page = Page(monkeypatch, answer={"ok": True, "applied": 3, "missing": 0, "accepted": 3, "count": 3, "rev": 4})
    out = _call(op="select", scope="screen", match={"category": "newsletters"}, label="Newsletters")
    assert out.startswith("SELECT_OK") and "3 selected" in out and "Newsletters" in out, out
    sent = page.sent[-1][0]
    assert sent["type"] == "select" and sent["selection"]["refs"] == [ref(1), ref(2), ref(3)]
    assert sent["selection"]["mode"] == "replace"


def test_a_page_that_did_not_confirm_is_a_failure_not_a_success(monkeypatch):
    report(newsletter_stage())
    Page(monkeypatch, answer={"ok": False})
    out = _call(op="select", scope="screen", match={"category": "newsletters"})
    assert out.startswith("SELECT_FAIL") and "can't say anything is selected" in out, out


def test_no_page_is_an_honest_refusal(monkeypatch):
    monkeypatch.setattr(desktop_bus, "send", lambda *a, **k: {"delivered": False, "reason": "no Friday desktop page is open to show it in"})
    out = _call(op="select", scope="screen", match={"category": "newsletters"})
    assert out.startswith("SELECT_FAIL"), out


def test_a_page_that_accepted_fewer_than_asked_is_partial(monkeypatch):
    report(newsletter_stage())
    Page(monkeypatch, answer={"ok": True, "applied": 2, "missing": 0, "accepted": 2, "count": 2})
    out = _call(op="select", scope="screen", match={"category": "newsletters"})
    assert out.startswith("SELECT_PARTIAL") and "1 could not be shown" in out, out


def test_rows_below_the_list_are_counted_not_hidden(monkeypatch):
    report(newsletter_stage())
    Page(monkeypatch, answer={"ok": True, "applied": 3, "missing": 62, "accepted": 3 + 62, "count": 65})
    monkeypatch.setattr(agent, "_mail_select_all", lambda m: ([ref(i) for i in range(1, 66)], None, False, ""))
    out = _call(op="select", scope="all", match={"category": "newsletters"}, label="Newsletters")
    assert out.startswith("SELECT_PARTIAL") or out.startswith("SELECT_OK")
    assert "65 selected (3 shown, 62 more below the list)" in out, out


def test_more_than_the_cap_is_partial_and_says_the_cap(monkeypatch):
    report(newsletter_stage())
    Page(monkeypatch, answer={"ok": True, "applied": 6, "missing": 494, "accepted": 500, "count": 500})
    monkeypatch.setattr(agent, "_mail_select_all", lambda m: ([ref(i) for i in range(1, 501)], None, True, ""))
    out = _call(op="select", scope="all", match={"category": "newsletters"})
    assert out.startswith("SELECT_PARTIAL") and "500" in out and "narrow" in out, out


def test_selecting_never_creates_an_approval_or_touches_mail(monkeypatch, tmp_path):
    from agent_friday.services import item_actions as ia
    report(newsletter_stage())
    Page(monkeypatch, answer={"ok": True, "applied": 3, "missing": 0, "accepted": 3, "count": 3})
    called = []
    monkeypatch.setattr(ia, "propose_email", lambda *a, **k: called.append(1))
    monkeypatch.setattr(ia, "run_email", lambda *a, **k: called.append(2))
    _call(op="select", scope="screen", match={"category": "newsletters"})
    _call(op="clear")
    assert called == [] and ap.list_approvals() == []


def test_an_unknown_kind_of_mail_is_not_guessed_from_titles(monkeypatch):
    report(newsletter_stage())
    page = Page(monkeypatch)
    out = _call(op="select", scope="screen", match={"category": "invoices"})
    assert out.startswith("SELECT_FAIL") and "match.query" in out, out
    assert page.sent == [], "nothing was ticked"


def test_nothing_matching_ticks_nothing(monkeypatch):
    report(newsletter_stage())
    page = Page(monkeypatch)
    out = _call(op="select", scope="screen", match={"from": "nowhere"})
    assert out.startswith("SELECT_FAIL") and page.sent == []


def test_untick_is_resolved_against_the_stage_and_sent_as_a_remove(monkeypatch):
    report(newsletter_stage(selected=[1, 2, 3]))
    page = Page(monkeypatch, answer={"ok": True, "applied": 1, "missing": 0, "accepted": 2, "count": 1})
    out = _call(op="remove", scope="screen", match={"from": "substack"})
    sent = page.sent[-1][0]
    assert sent["selection"]["mode"] == "remove" and sent["selection"]["refs"] == [ref(1), ref(2)]
    assert out.startswith("SELECT_OK"), out


def test_clear_asks_the_page_to_clear(monkeypatch):
    page = Page(monkeypatch, answer={"ok": True})
    assert _call(op="clear").startswith("SELECT_OK")
    assert page.types() == ["clear_selection"]


def test_these_with_a_tie_asks_the_owner(monkeypatch):
    st = newsletter_stage(selected=[1, 2])
    st["cursor"] = {"ref": ref(6), "state": "locked", "age_s": 0.4}
    report(st)
    page = Page(monkeypatch)
    out = _call(op="add", scope="screen", match={"deictic": "these"})
    assert out.startswith("SELECT_ASK") and "2 ticked" in out and page.sent == []


def test_messages_that_is_not_open_is_opened_first(monkeypatch):
    opened = []
    from agent_friday.services import desktop_targets

    def open_it(kind, **kw):
        opened.append((kind, kw))
        report(newsletter_stage())          # the window mounts and reports its stage
        return {"status": "opened"}
    monkeypatch.setattr(desktop_targets, "open_on_desktop", open_it)
    page = Page(monkeypatch, answer={"ok": True, "applied": 3, "missing": 0, "accepted": 3, "count": 3})
    out = _call(op="select", scope="screen", match={"category": "newsletters"})
    assert opened and opened[0][0] == "workspace" and opened[0][1].get("workspace") == "messages", opened
    assert out.startswith("SELECT_OK"), out
    assert page.types()[0] == "stage_request" and page.types()[-1] == "select"


def test_only_the_message_center_can_show_ticks_so_far(monkeypatch):
    assert _call(op="select", workspace="news", match={"category": "x"}).startswith("SELECT_FAIL")


def test_cloud_voice_hears_counts_and_kinds_never_names(monkeypatch):
    report(newsletter_stage())
    Page(monkeypatch, answer={"ok": True, "applied": 3, "missing": 0, "accepted": 3, "count": 3})
    tok = agent._CURRENT_SURFACE.set("voice-live")
    try:
        out = _call(op="select", scope="screen", match={"category": "newsletters"}, label="Mum's tax letters")
    finally:
        agent._CURRENT_SURFACE.reset(tok)
    assert out == "SELECT_OK I've ticked 3 newsletters; 3 on screen.", out
    assert "Mum" not in out
