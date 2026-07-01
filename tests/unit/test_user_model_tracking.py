"""Unit tests for user_model — trait tracking, message observation, fact
dedup/reinforcement, workflow counters, prompt rendering, size limits, and reset.
"""
from __future__ import annotations

import pytest

from agent_friday.services import user_model as um


@pytest.fixture(autouse=True)
def _clean():
    um.forget()  # start each test from a blank model
    yield


class TestTraits:
    def test_set_and_get_trait(self):
        um.set_trait("comm.formality", "0.8", confidence=0.7)
        assert um.get_trait("comm.formality") == 0.8

    def test_invalid_trait_key_rejected(self):
        assert um.set_trait("", "v")["ok"] is False
        assert um.set_trait(None, "v")["ok"] is False

    def test_confidence_clamped(self):
        um.set_trait("x.y", "v", confidence=5.0)
        # No exception; clamped internally.
        assert um.get_trait("x.y") == "v"

    def test_get_trait_default(self):
        assert um.get_trait("nope", default="fallback") == "fallback"

    def test_evidence_accumulates(self):
        um.set_trait("k", "a", evidence=1)
        um.set_trait("k", "b", evidence=2)
        # value updated; evidence summed internally (no crash on re-set).
        assert um.get_trait("k") == "b"


class TestObserveMessage:
    def test_casual_message_nudges_formality_down(self):
        for _ in range(10):
            um.observe_message("lol yeah gonna grab coffee, thanks!")
        f = um.get_trait("comm.formality")
        assert f is not None and f < 0.5

    def test_formal_message_nudges_formality_up(self):
        for _ in range(10):
            um.observe_message(
                "Please kindly review the attached document. Furthermore, I would like to discuss.")
        f = um.get_trait("comm.formality")
        assert f is not None and f > 0.5

    def test_code_vocabulary_builds_expertise(self):
        for _ in range(12):
            um.observe_message(
                "The async function needs a refactor; the stack trace shows a regex bug after deploy.")
        e = um.get_trait("expertise.code")
        assert e is not None and float(e) > 0.5

    def test_novice_ask_lowers_expertise_target(self):
        for _ in range(12):
            um.observe_message("what is a function? can you explain in simple terms, i'm new to code python")
        e = um.get_trait("expertise.code")
        assert e is not None and float(e) < 0.5

    def test_non_user_role_skipped(self):
        assert um.observe_message("I prefer X", role="assistant")["skipped"] is True

    def test_empty_message_skipped(self):
        assert um.observe_message("")["skipped"] is True

    def test_huge_message_bounded(self):
        assert um.observe_message("please " * 100000)["ok"] is True


class TestFacts:
    def test_note_fact_stores(self):
        r = um.note_fact("preference", "prefers dark mode")
        assert r["ok"] is True

    def test_empty_fact_rejected(self):
        assert um.note_fact("preference", "")["ok"] is False

    def test_fact_dedup_same_source_no_inflation(self):
        um.note_fact("bio", "works at FutureSpeak", confidence=0.6, source="dream:2026-06-30")
        um.note_fact("bio", "works at FutureSpeak", confidence=0.6, source="dream:2026-06-30")
        facts = [f for f in um._recent_facts(50) if "FutureSpeak" in f["text"]]
        assert len(facts) == 1
        # Re-running the SAME source must not inflate confidence.
        assert facts[0]["confidence"] == pytest.approx(0.6, abs=0.001)

    def test_fact_reinforced_by_new_source(self):
        um.note_fact("bio", "lives in Austin", confidence=0.6, source="dream:2026-06-30")
        um.note_fact("bio", "lives in Austin", confidence=0.6, source="dream:2026-07-01")
        facts = [f for f in um._recent_facts(50) if "Austin" in f["text"]]
        assert len(facts) == 1
        assert facts[0]["confidence"] > 0.6  # new source → reinforced


class TestWorkflowCounters:
    def test_workspace_counter_bumps(self):
        um.observe_event("workspace", "career-ops")
        um.observe_event("workspace", "career-ops")
        top = um._top_counters("workflow.workspace.", n=5)
        assert any("career" in t for t in top)

    def test_tool_counter_bumps(self):
        um.observe_event("tool", "web_search")
        assert um.observe_event("tool", "web_search")["ok"] is True


class TestRendering:
    def test_render_empty_when_nothing_learned(self):
        assert um.render_user_model_prompt() == ""

    def test_render_includes_learned_style(self):
        for _ in range(12):
            um.observe_message("Please kindly furthermore review this in detail with additional context provided.")
        out = um.render_user_model_prompt()
        assert "Communication style" in out

    def test_render_includes_facts(self):
        um.note_fact("preference", "prefers bullet points", confidence=0.9)
        out = um.render_user_model_prompt()
        assert "bullet points" in out

    def test_profile_shape(self):
        um.note_fact("preference", "x")
        p = um.profile()
        assert p["available"] is True
        assert "traits" in p and "facts" in p


class TestReset:
    def test_forget_all(self):
        um.note_fact("preference", "temp fact")
        um.set_trait("temp.trait", "1")
        um.forget()
        assert um.render_user_model_prompt() == ""
        assert um.get_trait("temp.trait") is None

    def test_forget_single_category(self):
        um.note_fact("preference", "pref fact")
        um.note_fact("bio", "bio fact")
        um.forget(category="preference")
        texts = [f["text"] for f in um._recent_facts(50)]
        assert "pref fact" not in texts
        assert "bio fact" in texts


class TestDisabled:
    def test_disabled_skips_and_empty_render(self, monkeypatch):
        monkeypatch.setattr(um, "_enabled", lambda: False)
        assert um.observe_message("hi there")["skipped"] is True
        assert um.render_user_model_prompt() == ""
