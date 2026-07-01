"""Edge-case tests: empty inputs and Unicode extremes (emoji, RTL, null bytes,
zalgo) fed to every user-data function. Nothing should raise; everything should
degrade to a safe, well-formed result.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_friday.services import economy as econ
from agent_friday.services import user_model as um
from agent_friday.services import learning_loop as ll
from agent_friday.services import sensitivity_classifier as sc
from agent_friday.services import egress_gate as eg
from agent_friday.services import qa_gates as qa
from agent_friday.services import soul

econ._ensure_schema()

DEVNULL = Path(os.devnull)

# (id, value) pairs. Explicit short ids keep the pytest test-id small — the raw
# 50 KB value would otherwise overflow Windows' 32767-char PYTEST_CURRENT_TEST.
UNICODE_CASES = [
    ("empty", ""),
    ("spaces", "   "),
    ("emoji", "😀🔥🎉"),
    ("rtl", "‮reversed rtl"),
    ("nullbytes", "\x00\x01\x02"),
    ("zalgo", "z̸̢͈a̷l̴g̶o̸"),
    ("japanese", "日本語のテキスト"),
    ("math", "𝔘𝔫𝔦𝔠𝔬𝔡𝔢 math"),
    ("large-50k", "a" * 50000),
]
UNICODE_IDS = [c[0] for c in UNICODE_CASES]
UNICODE_VALUES = [c[1] for c in UNICODE_CASES]


class TestClassifierEdges:
    @pytest.mark.parametrize("s", UNICODE_VALUES, ids=UNICODE_IDS)
    def test_classify_never_raises(self, s):
        result = sc.classify(s)
        assert result in (sc.Tier.PUBLIC, sc.Tier.PRIVATE, sc.Tier.SENSITIVE)

    def test_empty_returns_default(self):
        assert sc.classify("") == sc.Tier.PUBLIC
        assert sc.classify(None) == sc.Tier.PUBLIC


class TestEgressEdges:
    @pytest.mark.parametrize("s", UNICODE_VALUES, ids=UNICODE_IDS)
    def test_gate_text_never_raises(self, s):
        out = eg._gate_text(s, "anthropic", "field", DEVNULL)
        assert isinstance(out, str) or out is None

    def test_seal_empty_messages(self):
        sealed = eg.seal_outbound({"messages": []}, "anthropic", log_path=DEVNULL)
        assert sealed["messages"] == []

    def test_seal_no_recognized_keys(self):
        sealed = eg.seal_outbound({"random": "data"}, "anthropic", log_path=DEVNULL)
        assert sealed["random"] == "data"


class TestUserModelEdges:
    @pytest.mark.parametrize("s", UNICODE_VALUES, ids=UNICODE_IDS)
    def test_observe_message_never_raises(self, s):
        r = um.observe_message(s)
        assert "ok" in r or "skipped" in r

    @pytest.mark.parametrize("s", UNICODE_VALUES, ids=UNICODE_IDS)
    def test_note_fact_handles_extremes(self, s):
        r = um.note_fact("preference", s)
        assert "ok" in r


class TestEconomyEdges:
    @pytest.mark.parametrize("s", ["", "😀", "\x00", "‮rtl"],
                             ids=["empty", "emoji", "nullbyte", "rtl"])
    def test_earn_with_unicode_reason(self, s):
        tx = econ.earn(f"unicode-agent-{hash(s) & 0xffff}", 100, s)
        assert tx is not None

    def test_earn_zero_amount(self):
        tx = econ.earn("zero-earn-agent", 0, "nothing")
        assert tx is not None


class TestLearningEdges:
    @pytest.mark.parametrize("s", UNICODE_VALUES, ids=UNICODE_IDS)
    def test_observe_extreme_prompts(self, s):
        r = ll.observe("edge_task", s, approach="a", success=True)
        assert "ok" in r


class TestQAEdges:
    def test_parse_empty_score(self):
        assert qa._parse_score("")["score"] is None

    def test_evaluate_empty_content(self):
        r = qa.evaluate_text("", "intent")
        assert r["passed"] is True


class TestSoulEdges:
    def test_unicode_soul_saves(self):
        soul.reset_soul()
        r = soul.save_soul("# Persona 日本語\n😀 warm and sharp")
        assert r["ok"] is True
        soul.reset_soul()
