"""From the user's words to the exact thing to open (services/desktop_targets).

"The Harbor Legal email", "my budget spreadsheet", "model settings" and
"Hard stop" each have to become one concrete id the desktop's own deep-link
handlers accept, from what Friday already holds, within a time budget, and an
answer that could not be found has to say so with the closest matches rather
than open something else.
"""
import time
from datetime import date
from pathlib import Path

import pytest

from agent_friday.services import desktop_bus
from agent_friday.services import desktop_targets as dt

MANIFEST = {"workspaces": {
    "news": {"label": "News", "key": "tab", "keys": ["tab"], "sections": [
        {"id": "frontpage", "label": "Front Page"}, {"id": "feed", "label": "Feed"},
        {"id": "readlater", "label": "Read Later"}]},
    "calendar": {"label": "Calendar", "keys": ["date", "view", "meeting_id"],
                 "sections": [{"id": "meetings", "label": "Meetings", "key": "view"}]},
    "settings": {"label": "Settings", "key": "tab", "keys": ["tab", "section"], "sections": [
        {"id": "general", "label": "General"},
        {"id": "intelligence", "label": "Models", "aliases": ["models", "model"]},
        {"id": "accounts", "label": "Accounts & Keys", "aliases": ["phone", "keys"]},
        {"id": "costs", "label": "Spending"},
        {"id": "advanced", "label": "Advanced"}]},
    "studio": {"label": "Studio", "key": "view", "sections": [
        {"id": "gallery", "label": "Gallery"},
        {"id": "files", "label": "Files 3D", "aliases": ["files", "file browser"]}]},
}}


@pytest.fixture(autouse=True)
def _manifest(monkeypatch):
    monkeypatch.setattr(dt, "_manifest", lambda: MANIFEST)


# ── scoring ──────────────────────────────────────────────────────────────────

def test_a_short_word_must_stand_alone_but_a_longer_one_may_sit_inside():
    assert dt._score(["al"], "michael jordan") == 0.0
    assert dt._score(["al"], "al jordan") == 1.5
    assert dt._score(["budget"], "householdbudget.xlsx") >= 1.0


def test_a_possessive_is_not_a_word():
    assert dt._content("John's budget") == ["john", "budget"]


# ── workspaces and sections ──────────────────────────────────────────────────

def test_a_section_resolves_to_the_key_its_workspace_reads():
    r = dt.resolve_workspace("news", "the feed")
    assert r["ok"] and r["target"] == {"workspace": "news", "tab": "feed"}
    assert r["verify"] == {"workspace": "news", "key": "tab", "value": "feed"}
    assert r["label"] == "News › Feed"


def test_a_section_with_its_own_key_uses_it():
    r = dt.resolve_workspace("calendar", "meetings")
    assert r["target"] == {"workspace": "calendar", "view": "meetings"}


def test_a_section_the_workspace_does_not_have_names_the_ones_it_does():
    r = dt.resolve_workspace("news", "podcasts")
    assert not r["ok"]
    assert "News has no section called 'podcasts'" in r["reason"]
    assert "Front Page (frontpage)" in r["reason"] and "Feed (feed)" in r["reason"]


def test_a_workspace_by_its_label():
    r = dt.resolve_workspace("Studio")
    assert r["ok"] and r["target"] == {"workspace": "studio"} and "verify" not in r


# ── settings ─────────────────────────────────────────────────────────────────

def test_settings_by_tab_label_or_alias():
    assert dt.resolve_settings("model settings")["target"] == {
        "workspace": "settings", "tab": "intelligence"}
    assert dt.resolve_settings("spending")["target"]["tab"] == "costs"


def test_settings_section_is_found_by_its_title(monkeypatch):
    monkeypatch.setattr(dt, "settings_parts", lambda: {
        "costs": ["Spend", "Budget alerts", "Hard stop"],
        "accounts": ["Setup checklist", "PHONE"]})
    r = dt.resolve_settings("the hard stop")
    assert r["target"] == {"workspace": "settings", "tab": "costs", "section": "Hard stop"}
    assert r["verify"] == {"workspace": "settings", "key": "section", "value": "Hard stop"}
    assert r["label"] == "Settings › Spending › Hard stop"


def test_settings_that_match_nothing_list_the_tabs(monkeypatch):
    monkeypatch.setattr(dt, "settings_parts", lambda: {})
    r = dt.resolve_settings("quantum flux")
    assert not r["ok"] and "Spending (costs)" in r["reason"]


def test_settings_parts_are_read_from_the_served_ui():
    """The section titles come from the real index.html, tab by tab."""
    dt._PARTS_CACHE.update(key=None, map={})
    parts = dt.settings_parts()
    assert "Hard stop" in parts["costs"] and "Budget alerts" in parts["costs"]
    assert "Identity" in parts["general"]
    assert "Setup checklist" in parts["accounts"]
    assert "Vault" in parts["privacy"]


# ── email ────────────────────────────────────────────────────────────────────

CARDS = [
    {"id": "m1", "thread_id": "t-old", "account_id": "acc1", "sender": "Harbor Legal",
     "sender_email": "office@harborlegal.example", "subject": "Engagement letter",
     "snippet": "", "timestamp": "2026-09-01T10:00:00Z"},
    {"id": "m2", "thread_id": "t-new", "account_id": "acc1", "sender": "Harbor Legal",
     "sender_email": "office@harborlegal.example", "subject": "Invoice for September",
     "snippet": "", "timestamp": "2026-09-20T10:00:00Z"},
    {"id": "m3", "thread_id": "t-other", "account_id": "acc2", "sender": "Stripe",
     "sender_email": "receipts@stripe.example", "subject": "Your receipt",
     "snippet": "Harbor mentioned in passing", "timestamp": "2026-09-24T10:00:00Z"},
]


@pytest.fixture
def mail_cache(monkeypatch):
    from agent_friday.services import message_triage as mt
    monkeypatch.setattr(mt, "_collect_cache", {("k",): {"result": {"messages": list(CARDS)}}})
    calls = []
    monkeypatch.setattr(mt, "collect", lambda **kw: calls.append(kw) or {"messages": []})
    return calls


def test_the_newest_matching_email_opens_and_the_others_are_named(mail_cache):
    r = dt.resolve_email("the Harbor Legal email")
    assert r["ok"]
    assert r["target"] == {"workspace": "messages", "thread_id": "t-new", "account": "acc1",
                           "subject": "Invoice for September", "from": "Harbor Legal"}
    assert r["verify"] == {"workspace": "messages", "key": "thread_id", "value": "t-new"}
    assert r["also"] == ["Harbor Legal — Engagement letter"]
    assert mail_cache == [], "a match in the cached list must not go to Gmail"


def test_an_email_not_in_the_list_is_searched_for(monkeypatch, mail_cache):
    from agent_friday.services import message_triage as mt
    found = {"id": "m9", "thread_id": "t-dentist", "account_id": "acc2",
             "sender": "Smile Dental", "subject": "Appointment reminder",
             "timestamp": "2026-09-22T09:00:00Z"}
    calls = []
    monkeypatch.setattr(mt, "collect", lambda **kw: calls.append(kw) or {"messages": [found]})
    r = dt.resolve_email("the dentist appointment reminder")
    assert r["ok"] and r["target"]["thread_id"] == "t-dentist"
    assert calls and calls[0]["query"] == "dentist appointment reminder"


def test_no_email_matches_names_the_closest(mail_cache):
    r = dt.resolve_email("Harbor tax refund")
    assert not r["ok"] and "no email matches" in r["reason"]
    assert any("Harbor Legal" in c for c in r.get("candidates", []))


def test_gmail_that_does_not_answer_in_time_is_said_plainly(monkeypatch, mail_cache):
    from agent_friday.services import message_triage as mt
    monkeypatch.setattr(mt, "collect", lambda **kw: time.sleep(2) or {"messages": []})
    t0 = time.time()
    r = dt.resolve_email("the plumber quote", budget_s=0.2)
    assert time.time() - t0 < 1.5
    assert not r["ok"] and "did not answer within" in r["reason"]


def test_an_exact_thread_id_needs_no_search(mail_cache):
    r = dt.resolve_email(id="t-abc", account="acc9")
    assert r["target"] == {"workspace": "messages", "thread_id": "t-abc", "account": "acc9"}


# ── files ────────────────────────────────────────────────────────────────────

@pytest.fixture
def roots(monkeypatch, tmp_path):
    from agent_friday.services import studio_files
    r = {"documents": tmp_path / "Documents", "downloads": tmp_path / "Downloads",
         "projects": tmp_path / "Projects"}
    for p in r.values():
        p.mkdir()
    (r["documents"] / "Finance" / "2026").mkdir(parents=True)
    monkeypatch.setattr(studio_files, "roots", lambda: r)
    return r


def _rows(*paths):
    return {"results": [{"path": str(p), "name": Path(p).name, "mtime": i}
                        for i, p in enumerate(paths)]}


def test_a_file_becomes_its_studio_folder_and_name(monkeypatch, roots):
    from agent_friday.services import file_search
    target = roots["documents"] / "Finance" / "2026" / "HouseholdBudget.xlsx"
    notes = roots["documents"] / "budget-notes.md"
    seen = {}
    monkeypatch.setattr(file_search, "search_files",
                        lambda **kw: seen.update(kw) or _rows(notes, target))
    r = dt.resolve_file("my budget spreadsheet")
    assert r["ok"]
    assert r["target"] == {"workspace": "studio", "view": "files", "root": "documents",
                           "path": "Finance/2026", "file": "HouseholdBudget.xlsx"}
    assert r["verify"] == {"workspace": "studio", "key": "file", "value": "HouseholdBudget.xlsx"}
    assert seen["query"] == "budget" and seen["root"] is None


def test_a_named_folder_narrows_the_search(monkeypatch, roots):
    from agent_friday.services import file_search
    f = roots["downloads"] / "tax-return-2025.pdf"
    seen = {}
    monkeypatch.setattr(file_search, "search_files",
                        lambda **kw: seen.update(kw) or _rows(f))
    r = dt.resolve_file("the tax return PDF in Downloads")
    assert r["target"]["root"] == "downloads" and r["target"]["file"] == "tax-return-2025.pdf"
    assert seen["root"] == "downloads" and seen["query"] == "tax return"


def test_a_file_outside_studios_folders_is_not_opened(monkeypatch, roots, tmp_path):
    from agent_friday.services import file_search
    outside = tmp_path / "elsewhere" / "budget.xlsx"
    monkeypatch.setattr(file_search, "search_files", lambda **kw: _rows(outside))
    r = dt.resolve_file("budget spreadsheet")
    assert not r["ok"] and "outside the folders Studio shows" in r["reason"]


def test_the_wrong_kind_of_file_does_not_match(monkeypatch, roots):
    from agent_friday.services import file_search
    monkeypatch.setattr(file_search, "search_files",
                        lambda **kw: _rows(roots["documents"] / "budget.docx"))
    r = dt.resolve_file("budget spreadsheet")
    assert not r["ok"] and "no file matches 'budget (spreadsheet)'" in r["reason"]


def test_a_slow_file_search_is_cut_off(monkeypatch, roots):
    from agent_friday.services import file_search
    monkeypatch.setattr(file_search, "search_files", lambda **kw: time.sleep(2) or _rows())
    t0 = time.time()
    r = dt.resolve_file("budget", budget_s=0.2)
    assert time.time() - t0 < 1.5
    assert not r["ok"] and "did not finish within" in r["reason"]


# ── knowledge ────────────────────────────────────────────────────────────────

ENTS = [
    {"id": "page:concepts/bootstrap", "type": "page", "title": "Bootstrap",
     "provenance": {"wiki_pages": ["concepts/bootstrap.md"]}},
    # A node the index found: its provenance names a page that MENTIONS it.
    {"id": "person:ada", "type": "person", "title": "Ada Lovelace", "description": "mathematician",
     "provenance": {"wiki_pages": ["concepts/bootstrap.md"]}},
    {"id": "project:loom", "type": "project", "title": "Loom", "description": "the Ada project"},
]


def test_a_page_opens_by_its_path(monkeypatch):
    monkeypatch.setattr(dt, "_entities", lambda: ENTS)
    r = dt.resolve_knowledge("the bootstrap page", pages_only=True)
    assert r["target"] == {"workspace": "knowledge", "path": "concepts/bootstrap.md"}
    assert r["verify"]["key"] == "path"


def test_a_node_without_a_page_opens_by_its_id(monkeypatch):
    monkeypatch.setattr(dt, "_entities", lambda: ENTS)
    r = dt.resolve_knowledge("Ada Lovelace")
    assert r["target"] == {"workspace": "knowledge", "node": "person:ada"}
    assert r["verify"] == {"workspace": "knowledge", "key": "node", "value": "person:ada"}


def test_pages_only_skips_nodes_without_a_page(monkeypatch):
    monkeypatch.setattr(dt, "_entities", lambda: ENTS)
    r = dt.resolve_knowledge("Ada Lovelace", pages_only=True)
    assert not r["ok"]


# ── calendar, contacts, content ──────────────────────────────────────────────

THU = date(2026, 9, 24)          # a Thursday


@pytest.mark.parametrize("text,expected", [
    ("today", THU), ("tomorrow", date(2026, 9, 25)), ("yesterday", date(2026, 9, 23)),
    ("friday", date(2026, 9, 25)), ("thursday", THU), ("next thursday", date(2026, 10, 1)),
    ("2026-10-02", date(2026, 10, 2)), ("october 2nd", date(2026, 10, 2)),
    ("10/2", date(2026, 10, 2)), ("jan 5", date(2027, 1, 5)), ("someday", None),
])
def test_days_are_read_the_way_people_say_them(text, expected):
    assert dt._parse_day(text, today=THU) == expected


def test_a_calendar_day_is_verified_by_its_date():
    r = dt.resolve_calendar("2026-10-02")
    assert r["target"] == {"workspace": "calendar", "date": "2026-10-02"}
    assert r["verify"]["value"] == "2026-10-02"


def test_a_meeting_id_opens_meetings():
    assert dt.resolve_calendar(id="mtg_42")["target"] == {
        "workspace": "calendar", "view": "meetings", "meeting_id": "mtg_42"}


PEOPLE = [{"name": "John Carter", "aliases": ["Johnny"], "company": "Acme", "overall": 0.9},
          {"name": "Joan Smith", "aliases": [], "company": "Harbor Legal", "overall": 0.4}]


def test_a_contact_resolves_to_the_name_on_the_card(monkeypatch):
    monkeypatch.setattr(dt, "_contacts", lambda: PEOPLE)
    assert dt.resolve_contact("John's contact card")["target"] == {
        "workspace": "contacts", "name": "John Carter"}
    assert dt.resolve_contact("johnny")["target"]["name"] == "John Carter"
    assert dt.resolve_contact("the person from Harbor Legal")["target"]["name"] == "Joan Smith"


def test_a_contact_that_is_not_there_says_so(monkeypatch):
    monkeypatch.setattr(dt, "_contacts", lambda: PEOPLE)
    r = dt.resolve_contact("Zed")
    assert not r["ok"] and "no contact matches" in r["reason"]


def test_a_content_post_needs_its_id():
    assert not dt.resolve_content_post("my latest post")["ok"]
    assert dt.resolve_content_post(id="post_abc123")["target"] == {
        "workspace": "content", "post": "post_abc123"}


def test_an_unknown_kind_lists_the_kinds():
    r = dt.resolve("spaceship", "x")
    assert not r["ok"] and "email" in r["reason"] and "settings" in r["reason"]


# ── what the tool says happened ──────────────────────────────────────────────

def _open_with(monkeypatch, sent, kind="calendar", query="2026-10-02"):
    monkeypatch.setattr(desktop_bus, "send", lambda actions, verify=None, **kw: sent)
    return dt.open_on_desktop(kind, query=query)


def test_confirmed_is_nav_ok(monkeypatch):
    r = _open_with(monkeypatch, {"delivered": True, "acked": True,
                                 "ack": {"opened": True, "matched": True}})
    assert r["status"] == "opened"
    assert r["text"].startswith("NAV_OK:calendar — opened the calendar for Friday 02 October")


def test_no_page_is_nav_fail(monkeypatch):
    r = _open_with(monkeypatch, {"delivered": False, "reason": "no Friday desktop page is open to show it in"})
    assert r["status"] == "no_desktop" and r["text"].startswith("NAV_FAIL: no Friday desktop page")


def test_a_silent_page_is_nav_sent(monkeypatch):
    r = _open_with(monkeypatch, {"delivered": True, "acked": False, "ack": {}})
    assert r["status"] == "sent" and r["text"].startswith("NAV_SENT:calendar")


def test_a_window_that_did_not_open_is_nav_fail(monkeypatch):
    r = _open_with(monkeypatch, {"delivered": True, "acked": True,
                                 "ack": {"opened": False, "note": "Calendar did not open"}})
    assert r["status"] == "failed" and r["text"].startswith("NAV_FAIL: the desktop did not open")


def test_the_wrong_thing_showing_is_nav_partial(monkeypatch):
    r = _open_with(monkeypatch, {"delivered": True, "acked": True, "ack": {
        "opened": True, "matched": False, "label": "Calendar", "note": "it shows date=2026-09-25"}})
    assert r["status"] == "partial"
    assert r["text"].startswith("NAV_PARTIAL:calendar — opened Calendar, but it is not showing")
    assert "date=2026-09-25" in r["text"]


def test_a_window_that_cannot_report_is_ok_but_says_so(monkeypatch):
    r = _open_with(monkeypatch, {"delivered": True, "acked": True, "ack": {
        "opened": True, "matched": None, "note": "Calendar does not report its date"}})
    assert r["status"] == "opened_unconfirmed" and r["text"].startswith("NAV_OK:calendar")
    assert "could not confirm the exact item" in r["text"]


def test_a_minimized_window_is_mentioned(monkeypatch):
    r = _open_with(monkeypatch, {"delivered": True, "acked": True, "ack": {
        "opened": True, "matched": True, "visible": False}})
    assert "minimized or covered" in r["text"]


def test_nothing_is_sent_when_nothing_matched(monkeypatch):
    sent = []
    monkeypatch.setattr(desktop_bus, "send", lambda *a, **k: sent.append(a) or {})
    r = dt.open_on_desktop("calendar", query="someday")
    assert r["status"] == "not_found" and r["text"].startswith("NAV_FAIL:") and sent == []
