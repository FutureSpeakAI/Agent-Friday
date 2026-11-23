"""The News workspace's latest stories, as the owner's phone reads them (PW-G2, adapter 5).

The News workspace had a verb list and nothing to draw on the phone. These tests hold the stories
(tv_gateway/ws_news.py) over a stand-in for the desktop's kept-news route:

* the phone reads only what the PC has already kept: the one route it asks is the archive, never the live feed;
* every row and item says the words are an outlet's (`taint: "outside:news"`), and no article link or markup leaves;
* stories come newest first, with the category as a facet that narrows;
* a news archive the PC could not read is a 503, never "no news";
* a story id that is not an id never reaches the desktop, and one that is not kept is a 404.
"""
import json
from datetime import date, timedelta

import pytest

from agent_friday.tv_gateway import pc_read
from tests.unit import test_tv_pc_read as shared
from tests.unit.test_tv_pc_read import get, paired

client = shared.client
_clean = shared._clean

TODAY = date.today()


def at(days, hh):
    return "%sT%02d:00:00Z" % ((TODAY - timedelta(days=days)).isoformat(), hh)


class Desk:
    def __init__(self):
        self.calls, self.down, self.saved = [], False, [
            {"url": "https://outlet.example/long-read?id=7", "title": "A <i>long</i> read", "source": "outlet.example",
             "snippet": "Worth the time. See https://outlet.example/long-read now.", "category": "Science",
             "saved_at": "2026-11-17T08:00:00"},
            {"url": "https://wire.example/brief", "title": "Brief", "source": "wire.example", "snippet": "", "category": "",
             "saved_at": "2026-11-16T08:00:00"},
            {"title": "No link", "source": "x.example"},
        ]
        self.stories = [
            {"id": "a1", "title": "Moon <b>mission</b> slips", "url": "https://outlet.example/moon?utm=1", "source": "outlet.example",
             "category": "Science", "snippet": "The launch slips a week. Read https://outlet.example/moon now.\n\n- new date\n- cost",
             "published_at": at(0, 9), "trust_dims": {"secret": 1}, "trust_score": 0.9},
            {"id": "b2", "title": "Markets close higher", "url": "https://wire.example/m", "source": "wire.example",
             "category": "Business", "snippet": "Stocks rose.", "published_at": at(1, 16)},
            {"id": "c3", "title": "Older science note", "url": "https://outlet.example/old", "source": "outlet.example",
             "category": "Science", "snippet": "Telescope data.", "published_at": at(2, 7)},
            {"title": "No id", "source": "x.example", "published_at": at(0, 1)},
        ]

    def __call__(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if self.down:
            return 500, b"{}", "application/json"
        if path == "/api/news/archive":
            return 200, json.dumps({"status": "ok", "items": self.stories, "total": len(self.stories or [])}).encode(), "application/json"
        if path == "/api/news/read-later":
            return 200, json.dumps({"status": "ok", "items": self.saved}).encode(), "application/json"
        return 404, b"{}", "application/json"


@pytest.fixture
def desk(monkeypatch):
    d = Desk()
    monkeypatch.setattr(pc_read, "_desk", d)
    return d


def listing(client, query=""):
    r = get(client, paired(), "/tv/v1/workspaces/news/list" + query)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def test_stories_come_newest_first_with_the_contracts_fields_and_the_outside_label(client, desk):
    got = listing(client)
    assert [r["ref"] for r in got["rows"]] == ["news:a1", "news:b2", "news:c3"]
    assert got["rows"][0] == {"ref": "news:a1", "kind": "story", "title": "Moon mission slips", "line": "outlet.example",
                              "state": "Science", "when": at(0, 9), "thumb": None, "lives_on": "pc", "sensitivity": "open",
                              "taint": "outside:news"}


def test_the_phone_asks_only_for_what_the_pc_already_kept_never_the_live_feed(client, desk):
    listing(client)
    get(client, paired(), "/tv/v1/workspaces/news/item/news:a1")
    get(client, paired(), "/tv/v1/workspaces/news/search?q=moon")
    assert {c[0] for c in desk.calls} == {"/api/news/archive", "/api/news/read-later"}
    assert desk.calls[0][1] == {"date_from": (TODAY - timedelta(days=2)).isoformat(), "limit": 200}


def test_a_story_with_no_usable_id_is_left_out_not_sent_broken(client, desk):
    assert not [r for r in listing(client)["rows"] if r["title"] == "No id"]


def test_the_category_facet_counts_and_narrows(client, desk):
    got = listing(client)
    assert got["facets"] == [{"name": "category", "values": [{"v": "Business", "n": 1}, {"v": "Science", "n": 2}]}]
    one = listing(client, "?facet.category=Business")
    assert [r["ref"] for r in one["rows"]] == ["news:b2"] and one["total"] == 1


def test_no_article_link_markup_or_private_trust_note_leaves_anywhere(client, desk):
    dev = paired()
    texts = [get(client, dev, "/tv/v1/workspaces/news/list").get_data(as_text=True),
             get(client, dev, "/tv/v1/workspaces/news/search?q=moon").get_data(as_text=True),
             get(client, dev, "/tv/v1/workspaces/news/item/news:a1").get_data(as_text=True)]
    for text in texts:
        for leak in ("https", "utm=1", "<b>", "trust_dims", "secret"):
            assert leak not in text, leak


def test_an_item_has_the_outlet_the_category_the_time_and_the_plain_summary(client, desk):
    got = get(client, paired(), "/tv/v1/workspaces/news/item/news:a1").get_json()
    assert got["taint"] == "outside:news" and got["kind"] == "story" and got["next"] is None
    assert got["meta"] == [{"k": "Outlet", "v": "outlet.example"}, {"k": "Category", "v": "Science"},
                           {"k": "Published", "v": at(0, 9)}]
    assert [b["t"] for b in got["blocks"]] == ["para", "list"] and got["blocks"][0]["text"] == "The launch slips a week. Read now."
    assert got["sources"] == [{"id": "outlet", "title": "outlet.example", "host": "outlet.example"}]


def test_search_looks_at_the_headline_and_the_summary_over_a_week(client, desk):
    dev = paired()
    by_title = get(client, dev, "/tv/v1/workspaces/news/search?section=latest&q=MARKETS").get_json()
    assert [r["ref"] for r in by_title["rows"]] == ["news:b2"]
    by_summary = get(client, dev, "/tv/v1/workspaces/news/search?section=latest&q=telescope").get_json()
    assert [r["ref"] for r in by_summary["rows"]] == ["news:c3"] and by_summary["rows"][0]["snippet"] == "Telescope data."
    assert desk.calls[-1][1]["date_from"] == (TODAY - timedelta(days=6)).isoformat()


def test_a_news_archive_that_could_not_be_read_is_503_never_no_news(client, desk):
    desk.down = True
    dev = paired()
    for path in ("list", "search?q=moon", "item/news:a1"):
        r = get(client, dev, "/tv/v1/workspaces/news/" + path)
        assert r.status_code == 503 and r.get_json()["code"] == "pc_unavailable", path


@pytest.mark.parametrize("ref", ["news:../x", "news:a/b", "news:", "page:x.md", "news:a b", "news:" + "x" * 65])
def test_an_id_that_is_not_a_story_id_never_reaches_the_desktop(client, desk, ref):
    assert get(client, paired(), "/tv/v1/workspaces/news/item/" + ref).status_code == 404
    assert desk.calls == []


def test_a_story_that_is_not_kept_is_404(client, desk):
    assert get(client, paired(), "/tv/v1/workspaces/news/item/news:nope").status_code == 404


def test_the_news_workspace_offers_the_phone_no_act_yet(client, desk):
    got = get(client, paired(), "/tv/v1/workspaces/news/item/news:a1").get_json()
    assert got["verbs"] == []


def test_an_archive_answer_with_no_list_in_it_is_503_never_no_news(client, desk):
    desk.stories = None
    assert get(client, paired(), "/tv/v1/workspaces/news/list").status_code == 503


def later(client, path=""):
    return get(client, paired(), "/tv/v1/workspaces/news/" + path)


def test_the_read_later_list_is_what_the_owner_saved_newest_first_and_never_names_the_link(client, desk):
    got = later(client, "list?section=later").get_json()
    rows = got["rows"]
    assert [r["title"] for r in rows] == ["A long read", "Brief"]            # a saved entry with no link is left out
    assert rows[0] == {"ref": rows[0]["ref"], "kind": "saved_story", "title": "A long read", "line": "outlet.example",
                       "state": "Science", "when": "2026-11-17T08:00:00", "thumb": None, "lives_on": "pc",
                       "sensitivity": "open", "taint": "outside:news"}
    assert rows[0]["ref"].startswith("later:") and len(rows[0]["ref"]) == len("later:") + 16
    for leak in ("https", "long-read", "id=7", "<i>"):
        assert leak not in later(client, "list?section=later").get_data(as_text=True), leak


def test_a_saved_story_opens_by_its_fingerprint_with_the_plain_summary(client, desk):
    ref = later(client, "list?section=later").get_json()["rows"][0]["ref"]
    got = later(client, "item/" + ref).get_json()
    assert got["taint"] == "outside:news" and got["kind"] == "saved_story" and got["title"] == "A long read"
    assert [b["text"] for b in got["blocks"]] == ["Worth the time. See now."]
    assert got["meta"] == [{"k": "Outlet", "v": "outlet.example"}, {"k": "Category", "v": "Science"},
                           {"k": "Saved", "v": "2026-11-17T08:00:00"}]
    assert "https" not in str(got) and {c[0] for c in desk.calls} == {"/api/news/read-later"}


def test_search_without_a_section_looks_in_both_the_latest_and_the_saved(client, desk):
    got = later(client, "search?q=long").get_json()
    assert [r["title"] for r in got["rows"]] == ["A long read"]
    assert {c[0] for c in desk.calls} == {"/api/news/archive", "/api/news/read-later"}


@pytest.mark.parametrize("ref", ["later:", "later:abc", "later:" + "g" * 16, "later:" + "0" * 17, "later:../x"])
def test_a_fingerprint_that_is_not_one_is_404_and_so_is_one_that_matches_nothing(client, desk, ref):
    assert later(client, "item/" + ref).status_code == 404
    assert later(client, "item/later:" + "0" * 16).status_code == 404


def test_a_read_later_list_the_pc_could_not_read_is_503_never_an_empty_list(client, desk):
    desk.saved = None
    assert later(client, "list?section=later").status_code == 503
