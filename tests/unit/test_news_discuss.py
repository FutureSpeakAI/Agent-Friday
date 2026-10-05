"""Discuss with Friday: evidence first, sources linked by code, her analysis
labelled as hers, the media diet held, the local model only. Synthetic
stories, a fake local model and a fake web."""
from __future__ import annotations

import json

import pytest

from agent_friday.services import news_discuss as nd
# Imported here, before the autouse fixture swaps core._load_settings: both
# modules bind it by name at import, so a first import inside a test would keep
# the stub for every later test in the process (an off-record voice turn would
# then read settings with no off_record in them and be written to disk).
from agent_friday.services import agent, voice_engine  # noqa: F401

STORY = {"title": "Council passes the transit budget, 7-2", "url": "https://examplewire.com/transit",
         "source": "examplewire.com"}
ARCHIVE = [
    {"title": "Transit budget passes after a split council vote", "url": "https://exampleledger.com/budget",
     "source": "exampleledger.com", "snippet": "The council voted 7-2 on Tuesday; two members said fares rise.",
     "published_at": "2031-03-11T18:00:00Z"},
    {"title": "Council transit budget vote set for Tuesday", "url": "https://examplepost.com/preview",
     "source": "examplepost.com", "snippet": "The council will vote Tuesday on the transit budget.",
     "published_at": "2031-03-09T12:00:00Z"},
    {"title": "Riverton bakery wins a regional prize", "url": "https://examplepost.com/bakery",
     "source": "examplepost.com", "snippet": "A bakery won.", "published_at": "2031-03-10T09:00:00Z"},
    {"title": "Transit budget: what the council left out", "url": "https://blocked.example/budget",
     "source": "blocked.example", "snippet": "Fares and the transit budget.", "published_at": "2031-03-11T19:00:00Z"},
]
ARTICLE = ("The city council passed the transit budget on Tuesday by a vote of 7-2. The budget adds "
           "night buses. The full ordinance is at https://council.example.gov/ordinances/2031-14.pdf .")


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(core, "_load_settings", lambda: {"news_local_area": "Riverton"})
    return tmp_path


def fake_model(answer):
    calls = []

    def llm(system, user, *, max_tokens=3000):
        calls.append({"system": system, "user": user})
        return answer, "local-model"
    llm.calls = calls
    return llm


def run(mode, answer, **k):
    llm = fake_model(answer)
    out = nd.discuss(STORY["url"], STORY["title"], mode, fetch=lambda url: (STORY["title"], ARTICLE),
                     archive=lambda days: list(ARCHIVE), llm=llm, **k)
    return out, llm


def test_compare_coverage_cites_by_id_and_links_by_code():
    out, llm = run("compare", {"findings": [{"text": "Example Ledger reports the council voted 7-2 on Tuesday.",
                                             "cites": ["D2"]}],
                               "outlets": [{"id": "D2", "framing": "a split vote", "left_out": "night buses"}],
                               "read": ["The split matters more than the total."]})
    assert out["status"] == "ok" and out["mode"] == "compare"
    by_id = {s["id"]: s for s in out["sources"]}
    assert by_id["D1"]["url"] == STORY["url"] and by_id["D2"]["url"] == "https://exampleledger.com/budget"
    assert out["findings"][0]["links"] == ["https://exampleledger.com/budget"]
    assert out["outlets"][0]["outlet"] == "Exampleledger"
    assert out["read"] == ["The split matters more than the total."]
    prompt = llm.calls[0]["user"]
    assert "[D1]" in prompt and "[D2]" in prompt and "https://" not in prompt
    assert "bakery" not in prompt.lower()               # not the same story


def test_a_finding_its_source_does_not_support_moves_to_her_read():
    out, _ = run("compare", {"findings": [
        {"text": "Example Ledger reports the council voted 7-2 on Tuesday.", "cites": ["D2"]},
        {"text": "The vote shows the mayor has lost the council for good.", "cites": ["D2"]}], "read": []})
    assert [f["text"] for f in out["findings"]] == ["Example Ledger reports the council voted 7-2 on Tuesday."]
    assert "The vote shows the mayor has lost the council for good." in out["read"]


def test_the_media_diet_holds_with_a_receipt(monkeypatch):
    from agent_friday.services import media_diet
    monkeypatch.setattr(media_diet, "blocked", lambda: [{"kind": "block", "outlet": "blocked.example", "name": ""}])
    out, llm = run("compare", {"findings": [], "read": []})
    assert "blocked.example" not in llm.calls[0]["user"]
    assert not any(s["url"].startswith("https://blocked.example") for s in out["sources"])
    assert out["diet_removed"] == 1


def test_primary_source_is_a_link_from_the_article_never_a_guess():
    out, llm = run("primary", {"findings": [{"text": "The ordinance adds night buses.", "cites": ["P1"]}], "read": []})
    assert out["primary"] == [{"id": "P1", "url": "https://council.example.gov/ordinances/2031-14.pdf"}]
    out2 = nd.discuss(STORY["url"], STORY["title"], "primary", fetch=lambda url: (STORY["title"], "No links here."),
                      archive=lambda days: [], llm=fake_model({"findings": [], "read": []}))
    assert out2["primary"] == [] and "not found" in out2["note"].lower()


def test_background_is_a_dated_timeline():
    out, _ = run("background", {"timeline": [{"when": "2031-03-09", "what": "The vote was set.", "cites": ["D3"]}],
                                "who": [{"name": "The city council", "role": "voted 7-2", "cites": ["D1"]}],
                                "findings": [], "read": []})
    assert out["timeline"][0]["links"] == ["https://examplepost.com/preview"]
    assert out["who"][0]["name"] == "The city council"


def test_claim_check_marks_each_claim():
    out, _ = run("claims", {"claims": [{"claim": "The vote was 7-2.", "status": "confirmed", "cites": ["D1", "D2"]},
                                       {"claim": "Fares will rise.", "status": "one side only", "cites": ["D2"]},
                                       {"claim": "Buses run all night.", "status": "invented", "cites": []}],
                            "findings": [], "read": []})
    assert [c["status"] for c in out["claims"]] == ["confirmed", "one side only"]


def test_the_local_angle_uses_the_owners_city_or_says_none():
    out, llm = run("local", {"findings": [], "read": []})
    assert "Riverton" in llm.calls[0]["user"]
    import agent_friday.core as core
    core._load_settings = lambda: {}
    out2, llm2 = run("local", {"findings": [], "read": []})
    assert not llm2.calls and "no local" in out2["note"].lower()


def test_follow_tracks_a_story_and_the_next_edition_reports_on_it():
    out = nd.discuss(STORY["url"], STORY["title"], "follow", fetch=None, archive=None, llm=None)
    assert out["status"] == "ok" and nd.follows()[0]["title"] == STORY["title"]
    report = nd.follow_report([dict(ARCHIVE[0], update=True, update_note="Update: fares rise.")])
    assert report[0]["title"] == STORY["title"] and report[0]["status"] == "update"
    assert nd.follow_report([ARCHIVE[2]])[0]["status"] == "no change"


def test_no_read_on_a_story_of_violence():
    out = nd.discuss("https://examplewire.com/shooting", "Two injured in a shooting at a bar, police say", "compare",
                     fetch=lambda url: ("t", "Police said two people were injured in a shooting at a bar."),
                     archive=lambda days: [], llm=fake_model({"findings": [], "read": ["It shows a pattern."]}))
    assert out["read"] == [] and "violence" in out["note"].lower()


def test_the_seat_down_is_said_never_a_cloud_fallback():
    def down(system, user, *, max_tokens=3000):
        raise RuntimeError("the local model is not serving")
    out = nd.discuss(STORY["url"], STORY["title"], "compare", fetch=lambda url: (STORY["title"], ARTICLE),
                     archive=lambda days: list(ARCHIVE), llm=down)
    assert out["status"] == "error" and "local" in out["message"].lower()


def test_make_a_podcast_queues_a_two_host_episode_from_the_story(monkeypatch):
    from agent_friday.services import podcast_engine as pe
    made = []
    monkeypatch.setattr(pe, "create", lambda refs, **k: made.append((refs, k)) or {"id": "E1", "status": "queued"})
    monkeypatch.setattr(pe, "start_worker", lambda: None)
    out = nd.make(STORY["url"], STORY["title"], "podcast")
    assert out["episode"]["id"] == "E1"
    refs, k = made[0]
    assert refs[0] == {"kind": "url", "url": STORY["url"]}
    assert k["length"] == "standard" and k["origin"] == "user"


def test_discuss_is_a_voice_tool():
    from agent_friday.services import voice_engine
    assert "discuss_story" in voice_engine._VOICE_SHARED_TOOLS
    assert nd.TOOLS[0]["name"] == "discuss_story" and nd.RINGS["discuss_story"] == 1
    assert set(nd.TOOLS[0]["input_schema"]["properties"]["mode"]["enum"]) == set(nd.MODES)


def test_the_news_tools_travel_with_news_not_the_always_on_catalogue():
    """The always-on catalogue has a latency budget (test_latency_budget).
    discuss_story and media_diet_note are News's own: sent with a turn in News
    or loaded by name, and runnable anywhere they are named (voice included)."""
    from agent_friday.services import agent, voice_engine
    always_on = {t["name"] for t in agent.CLAUDE_TOOLS if isinstance(t, dict)}
    news = {t["name"] for t in agent.WORKSPACE_TOOLS.get("news", [])}
    for n in ("discuss_story", "media_diet_note"):
        assert n not in always_on, n + " is in the always-on catalogue"
        assert n in news, n + " is not one of News's own tools"
        assert n in agent.CLAUDE_TOOL_HANDLERS and n in agent.TOOL_RINGS, n + " must run wherever it is named"
        assert n in {t["name"] for t in agent.tools_for_workspace("news")}
        assert n in voice_engine._VOICE_SHARED_TOOLS

