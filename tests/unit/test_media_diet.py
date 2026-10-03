"""Media diet notes: Friday hears a preference ("never cite Fox News"), proposes
it as an approval card in the Media diet, and only an approved rule is ever
applied: in every edition, briefing, podcast and Discuss answer, with a
receipt of what it removed. Synthetic outlets except the one in the owner's
own example."""
from __future__ import annotations

import json

import pytest

from agent_friday.services import approvals, media_diet


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(core, "_load_settings", lambda: {})
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(approvals, "_HOOKS", {media_diet.APPROVAL_KIND: [media_diet._on_decision]})
    from agent_friday.services import dissent_gate as dg
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    receipts = []
    from agent_friday.governance import action_gate
    monkeypatch.setattr(action_gate, "record_external",
                        lambda action, **k: receipts.append((action, k)) or {"ok": True})
    return receipts


ITEMS = [{"title": "Council passes the budget", "source": "examplewire.com", "url": "https://examplewire.com/a"},
         {"title": "Senate vote tonight", "source": "foxnews.com", "url": "https://www.foxnews.com/politics/x"},
         {"title": "Storm closes the road", "source": "news.google.com",
          "url": "https://news.google.com/rss/articles/abc", "snippet": "Storm closes the road Fox News"}]


def test_a_preference_heard_is_a_proposal_and_changes_nothing():
    rec = media_diet.propose("block", "Fox News", said="never cite Fox News")
    assert rec["status"] == "pending" and rec["kind"] == media_diet.APPROVAL_KIND
    card = rec["action_description"]
    assert "+ Never cite Fox News (foxnews.com)" in card and "Approve" in card
    assert media_diet.rules() == []
    kept, _ = media_diet.enforce(ITEMS, "front_page")
    assert kept == ITEMS


def test_an_approved_rule_is_applied_with_a_receipt(_isolate):
    rec = media_diet.propose("block", "Fox News", said="never cite Fox News")
    approvals.decide(rec["approval_id"], "approve", decided_by="owner")
    assert [(r["kind"], r["outlet"]) for r in media_diet.rules()] == [("block", "foxnews.com")]
    assert _isolate and _isolate[0][0] == "news:media_diet_rule"
    assert approvals.get_approval(rec["approval_id"])["consumed"]
    kept, removed = media_diet.enforce(ITEMS, "briefing")
    assert [i["title"] for i in kept] == ["Council passes the budget"]
    assert {i["title"] for i in removed} == {"Senate vote tonight", "Storm closes the road"}
    log = [json.loads(x) for x in media_diet.receipts_path().read_text(encoding="utf-8").splitlines()]
    assert log[-1]["where"] == "briefing" and log[-1]["rule"] == "Never cite Fox News"
    assert sorted(log[-1]["removed"]) == ["Senate vote tonight", "Storm closes the road"]


def test_a_rejected_proposal_is_dropped():
    rec = media_diet.propose("block", "Fox News", said="never cite Fox News")
    approvals.decide(rec["approval_id"], "deny", decided_by="owner")
    assert media_diet.rules() == []
    assert media_diet.pending() == []


def test_an_outlet_is_named_by_its_domain_or_its_spoken_name():
    assert media_diet.outlet_domain("Fox News") == "foxnews.com"
    assert media_diet.outlet_domain("The Guardian") == "theguardian.com"
    assert media_diet.outlet_domain("https://www.examplewire.com/x") == "examplewire.com"
    assert media_diet.outlet_domain("Example Ledger") == "exampleledger.com"


def test_a_podcast_never_hears_a_blocked_outlet():
    from agent_friday.services import podcast_news
    rec = media_diet.propose("block", "examplewire.com", said="drop Example Wire")
    approvals.decide(rec["approval_id"], "approve", decided_by="owner")
    docs = podcast_news._front_page_docs({"id": "x", "headline": "H", "lead": {
        "title": "Council passes the budget", "source": "examplewire.com", "url": "https://examplewire.com/a",
        "snippet": "7-2."}, "sections": [{"articles": [
            {"title": "Storm closes the road", "source": "exampleledger.com",
             "url": "https://exampleledger.com/s", "snippet": "Open Friday."}]}]})
    kept = media_diet.enforce_docs(docs, "podcast")
    assert [d["title"] for d in kept if d.get("outlet")] == ["Storm closes the road"]


def test_the_note_tool_is_voice_callable_and_only_proposes():
    from agent_friday.services import media_diet as md
    from agent_friday.services import voice_engine
    assert "media_diet_note" in voice_engine._VOICE_SHARED_TOOLS
    out = json.loads(md.tool_media_diet_note({"kind": "block", "outlet": "Fox News", "said": "never cite Fox News"}))
    assert out["status"] == "proposed" and "approve" in out["say"].lower()
    assert media_diet.rules() == []
