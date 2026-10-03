"""Media by voice: "show my drafts" changes the view and says what is there;
"turn this into a podcast" makes a linked draft card; publishing is not a tool."""
from __future__ import annotations

import pytest

import agent_friday.core as core
from agent_friday.services import media_index as mi
from agent_friday.services import media_card_tools as mt


@pytest.fixture
def home(tmp_path, monkeypatch):
    fd = tmp_path / ".friday"
    fd.mkdir()
    monkeypatch.setattr(core, "FRIDAY_DIR", fd)
    monkeypatch.setattr(core, "CREATIONS_DIR", tmp_path / "friday-creations")
    (tmp_path / "friday-creations").mkdir()
    monkeypatch.setattr(core, "DAILY_CREATIONS_DIR", fd / "creations")
    from agent_friday.services import office_engine, content_pipeline as cp, creative_engine as ce
    (fd / "documents").mkdir()
    monkeypatch.setattr(office_engine, "DOCUMENTS_DIR", fd / "documents")
    (fd / "creations_meta").mkdir()
    monkeypatch.setattr(ce, "CREATIVE_META_DIR", fd / "creations_meta")
    monkeypatch.setattr(cp, "DB_PATH", fd / "content_pipeline.db")
    (fd / "content").mkdir()
    monkeypatch.setattr(cp, "PUBLISH_LOG", fd / "content" / "publish_log.jsonl")
    # Every store this test reads or writes lives under its own home: nothing from an
    # earlier test's home can leak in, and nothing leaks out.
    from agent_friday.services import misc_engine, provenance, approvals
    monkeypatch.setattr(misc_engine, "CONTENT_DIR", fd / "content")
    monkeypatch.setattr(misc_engine, "CONTENT_PIPELINE_FILE", fd / "content" / "pipeline.json")
    monkeypatch.setattr(provenance, "PROVENANCE_DIR", fd / "provenance", raising=False)
    monkeypatch.setattr(approvals, "APPROVALS_FILE", fd / "approvals.json", raising=False)
    mi.reindex()
    mi.create_card(kind="draft", title="Reply to the harbour board", body="Thanks for the figures.", status="draft")
    mi.create_card(kind="article", title="The ferry story", body="The 06:40 left on time.", status="draft")
    sent = []
    from agent_friday.services import desktop_bus
    monkeypatch.setattr(desktop_bus, "send", lambda msg: sent.append(msg))
    return sent


def test_show_my_drafts_moves_the_screen_and_says_what_is_there(home):
    import json
    out = json.loads(mt._tool_media_show({"kind": "drafts"}))
    assert out["status"] == "shown" and out["count"] == 1
    assert home[-1] == {"type": "navigate", "workspace": "media", "view": "library", "kind": "draft"}
    assert out["say"].startswith("Reply to the harbour board: draft, draft.")


def test_what_needs_me_uses_the_default_view(home):
    import json
    out = json.loads(mt._tool_media_show({"view": "needs me"}))
    assert home[-1]["default_view"] == "review" and out["count"] == 0 and out["say"] == "Nothing there."


def test_turn_this_into_an_article_makes_a_linked_draft_and_opens_it(home):
    import json
    out = json.loads(mt._tool_media_turn({"query": "harbour board", "into": "article"}))
    assert out["status"] == "ok" and out["card"]["kind"] == "article" and out["card"]["status"] == "draft"
    assert home[-1] == {"type": "navigate", "workspace": "media", "card": out["card"]["id"]}
    assert any(r["how"] == "made_from" for r in mi.get(out["card"]["id"])["relations"])


def test_publishing_is_not_a_tool_and_the_tools_are_internal():
    names = {t["name"] for t in mt.TOOLS}
    assert names == {"media_show", "media_cards", "media_turn"}
    from agent_friday.governance import action_gate
    for n in names:
        assert n in action_gate.INTERNAL_TOOLS
    from agent_friday.services import voice_engine
    assert "media_show" in voice_engine._VOICE_SHARED_TOOLS and "media_turn" in voice_engine._VOICE_SHARED_TOOLS


def test_media_tools_stay_out_of_the_always_on_catalogue():
    """The always-on catalogue has a latency budget (tests/unit/test_latency_budget.py).
    Media's tools are the workspace's own: registered to run anywhere, sent only
    with a turn in Media or when the loader is asked for them by name. This
    fails the moment one of them is appended to CLAUDE_TOOLS."""
    from agent_friday.services import agent
    always_on = {t["name"] for t in agent.CLAUDE_TOOLS if isinstance(t, dict)}
    assert not [n for n in always_on if n.startswith("media_")], "a Media tool is in the always-on catalogue"
    assert {t["name"] for t in agent.WORKSPACE_TOOLS["media"]} == {"media_show", "media_cards", "media_turn"}
    for n in ("media_show", "media_cards", "media_turn"):
        assert n in agent.CLAUDE_TOOL_HANDLERS and n in agent.TOOL_RINGS, n + " runs wherever it is named"
    in_media = {t["name"] for t in agent.tools_for_workspace("media")}
    elsewhere = {t["name"] for t in agent.tools_for_workspace("news")}
    assert {"media_show", "media_turn"} <= in_media and not ({"media_show", "media_turn"} & elsewhere)
    assert len(agent.tools_for_workspace(None)) == len(agent.CLAUDE_TOOLS)


def test_the_loader_hands_over_a_media_tool_by_name_from_anywhere():
    from agent_friday.services import tool_catalogue as tc
    new, msg = tc.expand([], ["media_show", "no_such_tool"], [])
    assert [t["name"] for t in new] == ["media_show"] and "No such tool: no_such_tool" in msg


def test_voice_still_shares_media_show_and_turn():
    from agent_friday.services import voice_engine
    got = {name for name, _d, _s in voice_engine._voice_shared_tool_specs()}
    assert {"media_show", "media_turn"} <= got


def test_navigate_to_knows_a_card(home):
    from agent_friday.services import desktop_targets as dt
    assert "card" in dt.KINDS
    r = dt.resolve("card", query="ferry story")
    assert r.get("ok") or r.get("status") == "ok", r
    assert r["target"]["workspace"] == "media" and r["target"]["card"].startswith("media:")
