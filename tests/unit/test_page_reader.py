"""One page reader for every article reader (services/page_reader.py).

Blocks are scored, not cut by length; passages are chosen by the question
inside a token budget; links stay numbered beside the text; the page's own
metadata becomes a header and says whether it is an article; feeds are
fetched conditionally and cached reads expire. All pages here are synthetic.
"""
from __future__ import annotations

import json
import time

import pytest

from agent_friday.services import page_reader as pr

STORY = """<html><head><title>Council passes budget - Example News</title>
<meta property="og:site_name" content="Example News">
<script type="application/ld+json">{"@context": "https://schema.org", "@type": "NewsArticle",
 "headline": "Council passes budget", "datePublished": "2031-03-04T09:00:00Z",
 "dateModified": "2031-03-04T12:30:00Z", "author": [{"@type": "Person", "name": "Jane Roe"}],
 "publisher": {"@type": "Organization", "name": "Example News"}}</script>
</head><body>
<header class="site-header"><nav><ul><li><a href="/news">News</a></li><li><a href="/sports">Sports</a></li>
<li><a href="/weather">Weather</a></li></ul></nav></header>
<main><article class="story">
<header><h1>Council passes budget</h1><div class="byline">By Jane Roe</div></header>
<p>RIVERTON (AP) — The city council passed a $5 billion budget on Tuesday.</p>
<p>Officials agreed.</p>
<p>The vote was 8-3, according to <a href="https://www.courtlistener.com/docket/123/">the court filing</a>
and a <a href="https://www.riverton.gov/budget.pdf">city report</a>.</p>
<div class="share-bar"><p>Share this on Facebook and on Twitter and everywhere else please</p></div>
<p>Advertisement</p>
<blockquote><p>"We had no choice," the mayor said of the tax increase.</p></blockquote>
<p>Critics argued the plan raises property taxes by 6 percent while cutting library hours.</p>
<aside class="related"><p><a href="/other">Related: Another story about something else entirely</a></p></aside>
</article>
<article class="teaser"><p><a href="/x">Teaser for another story that runs quite long indeed</a></p></article>
</main>
<footer><p>Copyright 2031 Example News. All rights reserved, and other words here.</p></footer>
</body></html>"""


def test_clutter_is_dropped_and_the_story_kept():
    page = pr.read_html(STORY, "https://examplenews.com/budget")
    text = page.text
    for gone in ("Sports", "Share this", "Advertisement", "Related:", "Teaser", "Copyright"):
        assert gone not in text, gone
    assert page.dropped == 3            # share bar, advertisement, related rail (the rest is outside the story)


def test_a_cms_dressing_the_story_in_tag_classes_still_keeps_it():
    """WordPress puts "tag-politics" on the <article> and "has-sidebar" on
    its wrapper: words of page furniture that are not furniture."""
    paras = "".join("<p>Paragraph %d of the story, with enough words to be a paragraph.</p>" % i
                    for i in range(6))
    html = ('<html><body><div class="site-content has-sidebar"><article class="post tag-politics '
            'category-news">%s<div class="share-tools"><p>Share this story with your friends</p></div>'
            '</article></div></body></html>' % paras)
    page = pr.read_html(html)
    assert [b["text"][:11] for b in page.blocks] == ["Paragraph %d" % i for i in range(6)]
    assert page.dropped == 1


def test_short_wire_paragraphs_headline_and_byline_survive():
    """The old rule kept only paragraphs over 40 characters."""
    page = pr.read_html(STORY, "https://examplenews.com/budget")
    texts = [b["text"] for b in page.blocks]
    assert texts[0] == "Council passes budget"
    assert "By Jane Roe" in texts
    assert "Officials agreed." in texts
    assert any(b["kind"] == "blockquote" and "no choice" in b["text"] for b in page.blocks)


def test_link_words_stay_in_the_text_and_links_are_numbered_beside_it():
    page = pr.read_html(STORY, "https://examplenews.com/budget")
    assert "according to the court filing and a city report." in page.text
    assert "http" not in page.text
    assert [(x["n"], x["url"]) for x in page.links] == [
        (1, "https://www.courtlistener.com/docket/123/"), (2, "https://www.riverton.gov/budget.pdf")]
    assert "[1] the court filing — www.courtlistener.com" in pr.link_refs(page.links)


def test_metadata_becomes_one_header_line():
    page = pr.read_html(STORY, "https://examplenews.com/budget")
    assert page.meta["publisher"] == "Example News" and page.meta["author"] == "Jane Roe"
    assert page.header == "Example News · Mar 4, 2031 · by Jane Roe · NewsArticle"
    assert pr.is_article_meta(page.meta) is True


def test_a_section_front_says_it_is_not_an_article():
    html = ('<html><head><script type="application/ld+json">{"@type": "CollectionPage", '
            '"name": "Local news"}</script></head><body><p>x</p></body></html>')
    meta = pr.read_html(html).meta
    assert pr.is_article_meta(meta) is False
    assert pr.is_article_meta({}) is None


def test_thin_markup_falls_back_to_the_whole_story():
    html = ("<html><body><main><div>"
            + "".join("<div>Line of the story number %d here.</div>" % i for i in range(1, 12))
            + "</div></main></body></html>")
    page = pr.read_html(html)
    assert "Line of the story number 11 here." in page.text


def test_passages_are_chosen_by_the_question_inside_the_budget():
    paras = ["Lead paragraph about the council budget vote on Tuesday."]
    paras += ["Filler paragraph %d about parking meters and street sweeping schedules." % i for i in range(40)]
    paras += ['"We had no choice," the mayor said of the tax increase.']
    paras += ["Closing paragraph %d about weather." % i for i in range(20)]
    text = "\n\n".join(paras)
    sel = pr.select_text(text, '"We had no choice," the mayor said', 120)
    assert sel.startswith("Lead paragraph")
    assert "We had no choice" in sel
    assert "[…]" in sel
    assert pr.tokens(sel) <= 120 + 2
    # Everything fits: everything comes back, in order, unmarked.
    assert pr.select_text("A one.\n\nB two.", "two", 100) == "A one.\n\nB two."


def test_a_block_longer_than_the_room_is_cut_at_a_sentence():
    big = " ".join("Sentence number %d is here." % i for i in range(200))
    sel = pr.select_text(big, "number", 50)
    assert sel.endswith(".") and pr.tokens(sel) <= 50


def test_primary_sources_come_from_where_links_point():
    page = pr.read_html(STORY, "https://examplenews.com/budget")
    urls = [x["url"] for x in pr.primary_links(page.links)]
    assert urls == ["https://www.courtlistener.com/docket/123/", "https://www.riverton.gov/budget.pdf"]
    other = [{"n": 1, "anchor": "our earlier story", "url": "https://examplenews.com/earlier"}]
    assert pr.primary_links(other) == []


def test_discuss_finds_the_primary_source_from_the_articles_links(monkeypatch):
    from agent_friday.services import news_discuss as nd
    page = pr.read_html(STORY, "https://examplenews.com/budget")
    got = nd._primary_links(page.links, page.text)
    assert got[0] == "https://www.courtlistener.com/docket/123/"


def test_what_a_page_said_about_itself_is_kept_and_expires(monkeypatch, tmp_path):
    monkeypatch.setattr(pr, "_meta_path", lambda: tmp_path / "page_meta.json")
    pr.remember_meta(["https://agg.example/item", "https://examplenews.com/a"],
                     {"publisher": "Example News", "type": "NewsArticle"})
    assert pr.known_meta("https://agg.example/item")["publisher"] == "Example News"
    store = json.loads((tmp_path / "page_meta.json").read_text(encoding="utf-8"))
    for v in store.values():
        v["at"] = time.time() - pr.META_TTL_S - 1
    (tmp_path / "page_meta.json").write_text(json.dumps(store), encoding="utf-8")
    pr._STORE_CACHE.clear()          # a rewrite inside one clock tick keeps its mtime
    assert pr.known_meta("https://agg.example/item") == {}


def test_the_edition_filter_drops_a_page_that_is_a_list_of_links(monkeypatch, tmp_path):
    from agent_friday.services import news_seen as ns
    monkeypatch.setattr(pr, "_meta_path", lambda: tmp_path / "page_meta.json")
    item = {"title": "Riverton schools roundup", "url": "https://examplelocal.com/schools", "ts": time.time()}
    assert ns.is_article(item)
    pr.remember_meta([item["url"]], {"type": "CollectionPage"})
    assert not ns.is_article(item)


def test_an_aggregators_item_is_named_from_the_publishers_page(monkeypatch, tmp_path):
    from agent_friday.services import podcast_quality as q
    monkeypatch.setattr(pr, "_meta_path", lambda: tmp_path / "page_meta.json")
    story = {"outlet": "news.google.com", "url": "https://news.google.com/rss/articles/abc",
             "title": "Council passes budget", "text": "Council passes budget"}
    assert q.relayed_outlet(story) == ""
    pr.remember_meta([story["url"]], {"publisher": "Google News"})      # the aggregator's own page
    assert q.relayed_outlet(story) == ""
    pr.remember_meta([story["url"]], {"publisher": "Example News"})
    assert q.relayed_outlet(story) == "Example News"


def test_the_fetch_refuses_loopback_before_any_request(monkeypatch):
    import requests

    from agent_friday.services import web_safety as ws
    calls = []
    monkeypatch.setattr(requests, "get", lambda *a, **k: calls.append(a))
    with pytest.raises(ws.UnsafeURLError):
        pr.fetch("http://127.0.0.1:3000/api/settings")
    assert calls == []


# ── feeds: conditional GETs ─────────────────────────────────────────────────

FEED = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
<item><title>Council passes budget</title><link>https://examplenews.com/budget</link>
<pubDate>Tue, 04 Mar 2031 09:00:00 GMT</pubDate></item></channel></rss>"""


class _Resp:
    def __init__(self, body, headers):
        self._body, self.headers = body, headers

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_an_unchanged_feed_is_reused_on_a_304(monkeypatch):
    import urllib.error
    import urllib.request

    from agent_friday.services import news_engine as ne
    url = "https://feeds.example/rss"
    monkeypatch.setattr(ne, "_RSS_CACHE", {})
    monkeypatch.setattr(ne, "_FEED_VALIDATORS", {})
    sent = []

    def first(req, timeout=None):
        sent.append(dict(req.header_items()))
        return _Resp(FEED, {"ETag": '"v1"', "Last-Modified": "Tue, 04 Mar 2031 09:00:00 GMT"})
    monkeypatch.setattr(urllib.request, "urlopen", first)
    a = ne._parse_feed(url)
    assert [x["title"] for x in a] == ["Council passes budget"]
    assert "If-none-match" not in sent[0]

    ne._RSS_CACHE.clear()                       # past the short TTL

    def second(req, timeout=None):
        sent.append(dict(req.header_items()))
        raise urllib.error.HTTPError(url, 304, "Not Modified", {}, None)
    monkeypatch.setattr(urllib.request, "urlopen", second)
    b = ne._parse_feed(url)
    assert b == a
    assert sent[1]["If-none-match"] == '"v1"'
    assert sent[1]["If-modified-since"] == "Tue, 04 Mar 2031 09:00:00 GMT"


def test_a_304_without_a_first_answer_is_an_empty_feed(monkeypatch):
    import urllib.error
    import urllib.request

    from agent_friday.services import news_engine as ne
    monkeypatch.setattr(ne, "_RSS_CACHE", {})
    monkeypatch.setattr(ne, "_FEED_VALIDATORS", {})

    def nm(req, timeout=None):
        raise urllib.error.HTTPError("u", 304, "Not Modified", {}, None)
    monkeypatch.setattr(urllib.request, "urlopen", nm)
    assert ne._parse_feed("https://feeds.example/other") == []


# ── cached reads expire ─────────────────────────────────────────────────────

def test_a_deep_dive_cache_expires_and_an_old_extractor_is_read_again(monkeypatch, tmp_path):
    from datetime import datetime, timedelta

    from agent_friday.services import news_engine as ne
    path = tmp_path / "x.json"
    good = {"status": "ok", "summary": "s", "extractor": pr.VERSION,
            "generated_at": datetime.now().isoformat(timespec="seconds")}
    path.write_text(json.dumps(good), encoding="utf-8")
    assert ne._read_cached_dive(path)["cached"] is True
    path.write_text(json.dumps(dict(good, extractor=pr.VERSION - 1)), encoding="utf-8")
    assert ne._read_cached_dive(path) is None
    old = (datetime.now() - timedelta(seconds=ne.DEEP_DIVE_CACHE_TTL_S + 60)).isoformat(timespec="seconds")
    path.write_text(json.dumps(dict(good, generated_at=old)), encoding="utf-8")
    assert ne._read_cached_dive(path) is None


def test_a_web_fetch_cache_entry_expires(monkeypatch):
    from agent_friday.services import web_fetch as wf
    now = time.time()
    assert wf._fresh({"fetched_at": now, "via": "direct", "extractor": pr.VERSION})
    assert not wf._fresh({"fetched_at": now, "via": "direct", "extractor": pr.VERSION - 1})
    assert not wf._fresh({"fetched_at": now - wf.CACHE_TTL_S - 1, "via": "firecrawl"})
    assert wf._fresh({"fetched_at": now, "via": "firecrawl"})


# ── the deep dive reads the passages its headline needs ─────────────────────

def test_the_deep_dive_reads_by_headline_inside_its_budget(monkeypatch, tmp_path):
    from agent_friday.services import news_engine as ne
    body = "\n\n".join(["Lead: the council passed the transit plan."]
                       + ["Unrelated paragraph %d on parking and zoning matters downtown." % i for i in range(400)]
                       + ["The transit plan adds three bus lines, officials said."])
    seen = {}

    def gen(messages, system=None, max_tokens=None, **k):
        seen["prompt"] = messages[0]["content"]
        return '{"summary": "s", "implications": "i", "key_quotes": []}'
    monkeypatch.setattr(ne, "DEEP_DIVE_DIR", tmp_path)
    monkeypatch.setattr(ne, "_extract_article_text", lambda url: ("Transit plan", body))
    monkeypatch.setattr(ne, "_get_friday_system_prompt", lambda *a, **k: "")
    monkeypatch.setattr(ne, "_predict_route_provider", lambda *a, **k: "local")
    monkeypatch.setattr(ne, "_gated_vault_control", lambda *a, **k: None)
    monkeypatch.setattr(ne, "_generate_text", gen)
    out, status = ne._deep_dive_article("https://example.test/t", title="Transit plan bus lines", refresh=True)
    assert status == 200 and out["extractor"] == pr.VERSION
    article = seen["prompt"].split("ARTICLE TEXT:\n", 1)[1]
    assert "three bus lines" in article and article.startswith("Lead:")
    assert pr.tokens(article) < ne.DEEP_DIVE_BODY_TOKENS + 200


def test_the_quote_check_reads_the_passage_the_quote_is_in(monkeypatch):
    """The fetched article is searched with the quoted sentence itself."""
    from agent_friday.services import podcast_engine as pe
    article = "\n\n".join(["The governor spoke on Tuesday."]
                          + ["Background paragraph %d about rail funding history." % i for i in range(300)]
                          + ["She said the hoax claims no longer apply now that the bill passed."])
    monkeypatch.setattr(pe, "article_text", lambda url: article)
    got = pr.select_text(pe.article_text("u"), "She said the hoax claims no longer apply", pe.QUOTE_CHECK_TOKENS)
    assert "no longer apply now that the bill passed" in got
    assert pr.tokens(got) <= pe.QUOTE_CHECK_TOKENS + 2
