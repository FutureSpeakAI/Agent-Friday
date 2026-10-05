"""screen_select op=point: outline and number rows so the owner sees which ones Friday means.

Shows only (no mail changes, no card). The count is what the page confirmed (I4); badges stop at 12
and the rest are counted; the pointed set is remembered for two minutes so "the second one" and
"those" resolve; a cloud voice hears counts and kinds, never a name.
"""
from __future__ import annotations

import time

import pytest

from agent_friday.services import agent, approvals as ap, desktop_bus, screen_stage as ss
from tests.screen_fixtures import Page, item, newsletter_stage, ref, report


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    desktop_bus.reset()
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    yield
    desktop_bus.reset()


def _point(**kw):
    return agent._tool_screen_select(dict({"op": "point"}, **kw))


def test_the_rows_the_page_outlined_are_the_rows_reported(monkeypatch):
    report(newsletter_stage())
    page = Page(monkeypatch, answer={"ok": True, "count": 3})
    out = _point(workspace="messages", match={"category": "newsletters"})
    assert out == "POINT_OK 3 marked (numbered 1-3)", out
    sent = page.sent[-1][0]
    assert sent["type"] == "point" and sent["refs"] == [ref(1), ref(2), ref(3)] and sent["badges"] == "numbers"
    assert sent["workspace"] == "messages" and sent["id"].startswith("pt_")


def test_a_page_that_did_not_confirm_is_a_failure(monkeypatch):
    report(newsletter_stage())
    Page(monkeypatch, answer={"ok": False})
    assert _point(workspace="messages", match={"category": "newsletters"}).startswith("POINT_FAIL")


def test_it_changes_nothing_and_raises_no_card(monkeypatch):
    from agent_friday.services import item_actions as ia
    report(newsletter_stage())
    Page(monkeypatch, answer={"ok": True, "count": 3})
    touched = []
    monkeypatch.setattr(ia, "propose_email", lambda *a, **k: touched.append(1))
    monkeypatch.setattr(ia, "run_email", lambda *a, **k: touched.append(2))
    _point(workspace="messages", match={"category": "newsletters"})
    assert touched == [] and ap.list_approvals() == []


def test_badges_stop_at_twelve_and_the_rest_are_counted(monkeypatch):
    many = [item(i, lane="subscriptions", bulk=True) for i in range(1, 31)]
    st = {"workspace": "messages", "rev": 1, "items": many, "loaded": 30, "selection": {"refs": []}, "filters": []}
    report(st)
    page = Page(monkeypatch, answer={"ok": True, "count": 12})
    out = _point(workspace="messages", match={"category": "newsletters"})
    assert len(page.sent[-1][0]["refs"]) == ss.POINT_CAP == 12
    assert out == "POINT_OK 12 marked (numbered 1-12) and 18 more not marked", out


def test_the_second_one_is_the_second_thing_just_pointed_at(monkeypatch):
    report(newsletter_stage())
    page = Page(monkeypatch, answer={"ok": True, "count": 3})
    _point(workspace="messages", match={"category": "newsletters"})            # points at rows 1, 2, 3
    assert desktop_bus.pointed("messages")["refs"] == [ref(1), ref(2), ref(3)]
    page.answer = {"ok": True, "count": 1}
    _point(workspace="messages", match={"ordinals": [2]})
    assert page.sent[-1][0]["refs"] == [ref(2)]
    # pointing at a different set changes what "the second one" means
    page.answer = {"ok": True, "count": 2}
    _point(workspace="messages", match={"category": "unread"})                  # rows 1 and 5
    page.answer = {"ok": True, "count": 1}
    _point(workspace="messages", match={"ordinals": [2]})
    assert page.sent[-1][0]["refs"] == [ref(5)]


def test_ordinals_fall_back_to_the_on_screen_numbers_when_nothing_was_pointed(monkeypatch):
    report(newsletter_stage())
    page = Page(monkeypatch, answer={"ok": True, "count": 1})
    _point(workspace="messages", match={"ordinals": [4]})
    assert page.sent[-1][0]["refs"] == [ref(4)]


def test_the_pointed_set_is_forgotten_after_two_minutes():
    desktop_bus.set_pointed("messages", [ref(1)], "pt_x")
    assert desktop_bus.pointed("messages") is not None
    assert desktop_bus.pointed("messages", now=time.time() + desktop_bus.POINTED_TTL_S + 5) is None
    assert ss.POINTED_S == desktop_bus.POINTED_TTL_S


def test_those_resolves_to_what_was_pointed_at_when_nothing_is_ticked(monkeypatch):
    report(newsletter_stage())
    page = Page(monkeypatch, answer={"ok": True, "count": 2})
    _point(workspace="messages", match={"category": "unread"})
    _point(workspace="messages", match={"deictic": "these"})
    assert page.sent[-1][0]["refs"] == [ref(1), ref(5)]


def test_the_cloud_hears_a_count_and_a_kind_never_a_name(monkeypatch):
    st = newsletter_stage()
    for it in st["items"]:
        it["title"], it["who"] = "Quarterly invoice", "Harbor Legal Billing"
    report(st)
    Page(monkeypatch, answer={"ok": True, "count": 3})
    tok = agent._CURRENT_SURFACE.set("voice-live")
    try:
        out = _point(workspace="messages", match={"category": "newsletters"})
    finally:
        agent._CURRENT_SURFACE.reset(tok)
    assert out == "POINT_OK I've marked 3 conversations.", out


def test_it_points_in_news_media_and_the_library_by_their_own_facets(monkeypatch):
    stage = {"workspace": "media", "rev": 1, "loaded": 3, "selection": {"refs": []}, "filters": [], "items": [
        {"ref": "media:c1", "n": 1, "facets": {"kind": "podcast", "status": "draft", "project": "Harbor"}, "title": "a", "who": ""},
        {"ref": "media:c2", "n": 2, "facets": {"kind": "post", "status": "published", "project": "Harbor"}, "title": "b", "who": ""},
        {"ref": "media:c3", "n": 3, "facets": {"kind": "post", "status": "draft", "project": "Other"}, "title": "c", "who": ""}]}
    report(stage)
    page = Page(monkeypatch, answer={"ok": True, "count": 2})
    out = _point(workspace="media", match={"status": "draft"})
    assert page.sent[-1][0]["refs"] == ["media:c1", "media:c3"] and out.startswith("POINT_OK 2 marked")
    _point(workspace="media", category="Harbor")           # the flat voice word names any facet value
    assert page.sent[-1][0]["refs"] == ["media:c1", "media:c2"]


def test_an_unknown_kind_of_mail_is_not_guessed(monkeypatch):
    report(newsletter_stage())
    page = Page(monkeypatch)
    assert _point(workspace="messages", match={"category": "invoices"}).startswith("POINT_FAIL")
    assert page.sent == []


def test_a_workspace_it_cannot_point_in_is_refused_in_words(monkeypatch):
    assert _point(workspace="finance", match={"category": "x"}).startswith("POINT_FAIL")


def test_with_no_list_in_view_it_says_so(monkeypatch):
    Page(monkeypatch)
    assert _point(workspace="messages", match={"category": "newsletters"}) == "POINT_FAIL: I can't see your list right now."


def test_the_list_in_front_is_the_default_workspace(monkeypatch):
    stage = {"workspace": "news", "rev": 1, "loaded": 1, "selection": {"refs": []}, "filters": [], "items": [
        {"ref": "news:a1", "n": 1, "facets": {"category": "Tech", "source": "x"}, "title": "t", "who": ""}]}
    report(stage)
    page = Page(monkeypatch, answer={"ok": True, "count": 1})
    out = _point(match={"category": "tech"})
    assert page.sent[-1][0]["workspace"] == "news" and out.startswith("POINT_OK 1 marked")
