"""The Briefing, end to end through its route: the model cites stories by id
and the saved briefing links them from the fetched URLs. Synthetic stories."""
from __future__ import annotations

import json

from agent_friday.services import news_links as nl  # noqa: F401

ITEMS = [
    {"title": "Council passes the transit budget", "source": "examplewire.com",
     "url": "https://examplewire.com/2031/transit", "snippet": "The vote was 7-2."},
    {"title": "Storm closes the river road", "source": "exampleledger.com",
     "url": "https://exampleledger.com/storm", "snippet": "Crews expect it to reopen Friday."},
]


def test_the_briefing_route_links_its_stories_from_the_feed(client, tmp_path, monkeypatch):
    import agent_friday.core as core
    import agent_friday.routes.news as rn
    from agent_friday.services import news_engine as ne
    from agent_friday.services import podcast_news
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(rn, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(ne, "_fetch_calendar_today", lambda: [])
    monkeypatch.setattr(ne, "_fetch_news_items", lambda categories=None, limit_per=4: [
        dict(i, category="Local", boosted=False) for i in ITEMS])
    monkeypatch.setattr(ne, "_fetch_gmail_recent", lambda limit=12: [])
    monkeypatch.setattr(ne, "_queue_podcast", lambda *a, **k: None)
    prompts = []

    def model(messages, **k):
        prompts.append(messages[0]["content"])
        return ("# Briefing\n## News\nThe council passed the transit budget, 7-2. [N1]\n"
                "The river road is closed after the storm; read more at "
                "[the paper](https://invented.example.com/storm). [N2]\n")
    monkeypatch.setattr(rn, "_generate_text", model)
    monkeypatch.setattr(rn, "_get_friday_system_prompt", lambda **k: "")
    r = client.post("/api/briefing/generate", json={})
    assert r.status_code == 200, r.get_json()
    assert "[N1] Council passes the transit budget" in prompts[0]
    assert "https://examplewire.com" not in prompts[0]
    date = ne.datetime.now().strftime("%Y-%m-%d")
    md = (tmp_path / "wiki" / "briefings" / (date + ".md")).read_text(encoding="utf-8")
    assert "[Examplewire](https://examplewire.com/2031/transit)" in md
    assert "invented.example.com" not in md
    side = json.loads(podcast_news.sidecar_path(date).read_text(encoding="utf-8"))
    assert [n["id"] for n in side["news"]][:2] == ["N1", "N2"]
    assert side["link_check"] == []


def test_a_briefing_run_never_keeps_an_earlier_runs_stories(client, tmp_path, monkeypatch):
    """Each run's sources are its own: stories kept by an earlier run (or an
    earlier test in the same process) never become this run's sidecar."""
    import agent_friday.core as core
    import agent_friday.routes.news as rn
    from agent_friday.services import news_engine as ne
    from agent_friday.services import podcast_news
    today = ne.datetime.now().strftime("%Y-%m-%d")
    monkeypatch.setattr(ne, "_LAST_BRIEFING_SOURCES", {"version": 1, "date": today, "calendar": [],
                                                       "news": nl.number([dict(i) for i in ITEMS])})
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(rn, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(rn, "_gather_live_briefing_context", lambda: "")
    monkeypatch.setattr(rn, "_generate_text", lambda *a, **k: "# Briefing\n## Calendar\nNothing today.\n")
    monkeypatch.setattr(rn, "_get_friday_system_prompt", lambda **k: "")
    monkeypatch.setattr(ne, "_queue_podcast", lambda *a, **k: None)
    r = client.post("/api/briefing/generate", json={})
    assert r.status_code == 200, r.get_json()
    p = podcast_news.sidecar_path(today)
    side = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"news": []}
    assert side.get("news") == []
