"""A story that already ran comes back only with a material new development,
labelled as an update. Synthetic editions, modelled on a feature profile that
led the Front Page day after day with nothing new."""
from __future__ import annotations

from agent_friday.services import news_seen as ns

PROFILE = {"title": "The newsroom that built an AI copy of its editor in chief",
           "url": "https://examplepost.com/ai-editor-copy?utm_source=rss",
           "source": "examplepost.com", "category": "AI/Tech",
           "snippet": "Northwind Media's chief executive on doubling headcount while automating, "
                      "and an agent built from 30,000 of the editor's emails."}
BUDGET = {"title": "Council passes the transit budget", "url": "https://examplewire.com/transit",
          "source": "examplewire.com", "category": "Local", "snippet": "The vote was 7-2 on Tuesday."}


def _edition(eid, *stories):
    return {"id": eid, "lead": dict(stories[0]), "sections": [{"articles": [dict(s) for s in stories[1:]]}]}


def test_a_story_that_already_ran_unchanged_is_held_back():
    seen = ns.index([_edition("2031-03-11-morning", PROFILE, BUDGET)])
    again = dict(PROFILE, url="https://examplepost.com/ai-editor-copy")       # same story, tracking stripped
    kept, held = ns.filter_pool([again, {"title": "Storm closes the river road", "url": "https://exampleledger.com/storm",
                                         "source": "exampleledger.com", "snippet": "Crews expect it open Friday."}], seen)
    assert [s["title"] for s in kept] == ["Storm closes the river road"]
    assert held and held[0]["title"] == PROFILE["title"] and held[0]["first_ran"] == "2031-03-11-morning"


def test_the_same_story_under_a_new_headline_is_still_the_same_story():
    seen = ns.index([_edition("2031-03-11-morning", PROFILE)])
    retitled = dict(PROFILE, url="https://examplepost.com/p/12345",
                    title="The newsroom that built an AI copy of its editor-in-chief")
    kept, held = ns.filter_pool([retitled], seen)
    assert not kept and held


def test_a_material_new_development_returns_as_an_update():
    seen = ns.index([_edition("2031-03-11-morning", PROFILE)])
    developed = dict(PROFILE, snippet=PROFILE["snippet"] + " On Thursday Northwind Media said the agent "
                                                           "now writes for 12 newsletters and Ledgerline bought it.")
    kept, held = ns.filter_pool([developed], seen)
    assert len(kept) == 1 and not held
    s = kept[0]
    assert s["update"] is True and "2031-03-11-morning" in s["update_note"]
    assert "Ledgerline" in s["update_note"] or "12" in s["update_note"]


def test_a_new_outlet_on_a_seen_story_is_a_new_source():
    seen = ns.index([_edition("2031-03-11-morning", PROFILE)])
    other = dict(PROFILE, url="https://exampleledger.com/northwind-ai-editor", source="exampleledger.com")
    kept, _ = ns.filter_pool([other], seen)
    assert len(kept) == 1 and kept[0]["update"] and "exampleledger.com" in kept[0]["update_note"]


def test_a_story_never_seen_passes_untouched():
    kept, held = ns.filter_pool([dict(BUDGET)], ns.index([]))
    assert kept == [BUDGET] and not held and "update" not in kept[0]


def test_the_front_page_holds_back_what_ran_before(tmp_path, monkeypatch):
    """Wired into the edition: the pool the editor sees has no stale story,
    and the edition records what was held back."""
    import json

    from agent_friday.services import news_engine as ne
    monkeypatch.setattr(ne, "FRONT_PAGES_DIR", tmp_path)
    (tmp_path / "2031-03-11-morning.json").write_text(json.dumps(
        dict(_edition("2031-03-11-morning", PROFILE, BUDGET), date="2031-03-11", slot="morning")), encoding="utf-8")
    fresh = {"title": "Storm closes the river road", "url": "https://exampleledger.com/storm",
             "source": "exampleledger.com", "category": "Local", "snippet": "Crews expect it open Friday."}
    monkeypatch.setattr(ne, "_gather_front_page_pool", lambda per_cat=14: ([dict(PROFILE), fresh], {}))
    monkeypatch.setattr(ne, "_fetch_calendar_today", lambda: [])
    seen_by_editor = []

    def editor(pool, **k):
        seen_by_editor.extend(p["title"] for p in pool)
        return {"lead_index": 0, "lead_note": "", "headline": "H", "section_context": {}, "thread_updates": {}}
    monkeypatch.setattr(ne, "_editorialize_front_page", editor)
    monkeypatch.setattr(ne, "_notify_front_page", lambda *a, **k: None, raising=False)
    ed = ne._generate_front_page.__wrapped__("morning") if hasattr(ne._generate_front_page, "__wrapped__") \
        else ne._generate_front_page("morning")
    assert seen_by_editor == ["Storm closes the river road"]
    assert [h["title"] for h in ed["held_back"]] == [PROFILE["title"]]


def test_an_update_shows_as_an_update_with_what_is_new(tmp_path, monkeypatch):
    import json

    from agent_friday.services import news_engine as ne
    monkeypatch.setattr(ne, "FRONT_PAGES_DIR", tmp_path)
    (tmp_path / "2031-03-11-morning.json").write_text(json.dumps(
        dict(_edition("2031-03-11-morning", PROFILE), date="2031-03-11", slot="morning")), encoding="utf-8")
    developed = dict(PROFILE, snippet=PROFILE["snippet"] + " Ledgerline bought it on Thursday.")
    monkeypatch.setattr(ne, "_gather_front_page_pool", lambda per_cat=14: ([developed], {}))
    monkeypatch.setattr(ne, "_fetch_calendar_today", lambda: [])
    monkeypatch.setattr(ne, "_editorialize_front_page", lambda pool, **k: {
        "lead_index": 0, "lead_note": "", "headline": "H", "section_context": {}, "thread_updates": {}})
    ed = ne._generate_front_page.__wrapped__("morning")
    assert ed["lead"]["continuing"] and "Ledgerline" in ed["lead"]["thread_update"]
