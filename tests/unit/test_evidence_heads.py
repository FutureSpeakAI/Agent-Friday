"""DM4: the evidence heads read retrieved memory in shadow and change nothing.

Spec: ftv/program/specs/decision_models_STORM_2026-10-24.md (DM4). On the base
branch there is no services/evidence_heads.py: the fixture turns that into a
failure that says so, and the hook test fails on its assertion.
"""
from __future__ import annotations

import importlib
import json

import pytest


@pytest.fixture
def ev(monkeypatch):
    try:
        mod = importlib.import_module("agent_friday.services.evidence_heads")
    except ImportError as e:
        pytest.fail("services/evidence_heads does not exist (%s)" % e)
    mod.clear_cache()
    mod._seen.clear()
    monkeypatch.setattr(mod, "RUN_INLINE", True)
    monkeypatch.setattr(mod, "_off_record", lambda: False)
    yield mod
    mod.clear_cache()
    mod._seen.clear()
    mod._scorer_override = None


def test_a_reading_is_taken_once_per_turn_and_question(ev, monkeypatch):
    _heads(monkeypatch)
    _stub(ev, [0.5])
    assert ev.shadow_memory("same question?", ["p"])
    assert not ev.shadow_memory("same question?", ["p"])     # the block was built twice this turn


def test_a_turn_that_went_off_the_record_while_it_waited_leaves_no_receipt(ev, monkeypatch):
    from agent_friday.services import laya
    _heads(monkeypatch)
    _stub(ev, [0.5])
    gens = iter([1, 2])                                      # the generation moved before the job ran
    monkeypatch.setattr(ev, "_generation", lambda: next(gens, 2))
    asked = []
    monkeypatch.setattr(laya, "ask", lambda *a, **k: asked.append(1))
    assert ev.shadow_memory("q?", ["p"])
    assert asked == []


def test_shadow_never_uses_laya1_and_never_reads_uncalibrated_cosines(ev, monkeypatch):
    _heads(monkeypatch, scorer="laya1")
    assert ev.score_holds("q?", ["p"], shadow=True) is None
    assert ev._series_one("q", ["p"], {}, shadow=True) is None


def _stub(ev, scores):
    ev._scorer_override = lambda q, ps: list(scores)[:len(ps)]


def _heads(monkeypatch, mode="shadow", act=0.7, abstain=0.3, scorer="series_one"):
    from agent_friday.services import decision_heads
    monkeypatch.setattr(decision_heads, "effective",
                        lambda hid: {"id": hid, "mode": mode, "thresholds": {"act": act, "abstain": abstain},
                                     "scorer": scorer, "budget_ms": 1500, "tag": "v1-test"})


def test_both_heads_ship_in_shadow_with_a_series_one_scorer():
    from agent_friday.services import decision_heads as dh
    dh._reset_caches_for_tests()
    for hid in ("evidence.holds", "evidence.sufficient"):
        spec = dh.shipped(hid)
        assert spec is not None, dh.problems().get(hid)
        assert spec["mode_default"] == "shadow" and spec["direction"] == dh.DIRECTIONS[hid]
        assert spec["scorer"] in dh.SCORERS
    dh._reset_caches_for_tests()


@pytest.mark.parametrize("best,label", [(0.9, "sufficient"), (0.5, "unsure"), (0.1, "search_more")])
def test_sufficiency_follows_the_thresholds_in_force(ev, monkeypatch, best, label):
    _heads(monkeypatch)
    assert ev.sufficiency(best) == label


def test_the_sufficiency_preset_answers_through_laya_with_a_receipt(ev, monkeypatch):
    from agent_friday.services import laya
    _heads(monkeypatch)
    _stub(ev, [0.2, 0.95, 0.4])
    r = laya.ask({"text": "When is the review?", "surface": "background",
                  "opts": {"passages": ["a", "b", "c"], "log": False}},
                 {"evidence.sufficient": {"type": "choice", "preset": "evidence.sufficient"}}, "test", 5000)
    a = r["answers"]["evidence.sufficient"]
    assert a["value"] == "sufficient" and a["p"] == pytest.approx(0.95)
    rec = laya.receipt(r["receipt_id"])
    assert "When is the review" not in json.dumps(rec)


def test_shadow_memory_changes_nothing_and_reads_on_its_worker(ev, monkeypatch):
    from agent_friday.services import laya
    _heads(monkeypatch)
    _stub(ev, [0.1, 0.2])
    seen = []
    real = laya.ask
    monkeypatch.setattr(laya, "ask", lambda *a, **k: seen.append(a[1]) or real(*a, **k))
    snippets = ["first", "second"]
    before = list(snippets)
    assert ev.shadow_memory("question?", snippets)
    assert snippets == before and len(seen) == 1 and "evidence.sufficient" in seen[0]


def test_off_or_off_the_record_computes_nothing(ev, monkeypatch):
    calls = []
    ev._scorer_override = lambda q, ps: calls.append(1) or [0.9]
    _heads(monkeypatch, mode="off")
    assert not ev.shadow_memory("q?", ["p"])
    ev.clear_cache()
    _heads(monkeypatch)
    monkeypatch.setattr(ev, "_off_record", lambda: True)
    assert not ev.shadow_memory("q?", ["p"])
    assert calls == []


def test_a_missing_head_is_off(ev, monkeypatch):
    from agent_friday.services import decision_heads
    monkeypatch.setattr(decision_heads, "effective", lambda hid: None)
    assert ev.mode() == "off" and not ev.shadow_memory("q?", ["p"])


def test_no_reading_degrades_rather_than_guessing(ev, monkeypatch):
    from agent_friday.services import laya
    _heads(monkeypatch)
    ev._scorer_override = lambda q, ps: None
    r = laya.ask({"text": "q?", "surface": "background", "opts": {"passages": ["p"], "log": False}},
                 {"evidence.sufficient": {"type": "choice", "preset": "evidence.sufficient"}}, "test", 5000)
    assert r["layer"] == "degraded" and r["answers"]["evidence.sufficient"]["abstain"]


def test_the_memory_block_hands_its_recall_to_the_shadow_head_and_is_unchanged(monkeypatch):
    # A stand-in module, so this fails on its assertion (not on an import) where the
    # hook is missing, and so no real worker or encoder runs.
    import sys
    import types
    from agent_friday.services import model_router as mr
    calls = []

    class Mem:
        def available(self):
            return True

        def search(self, q, n=5, query_embedding=None):
            return [{"text": "We moved the review to Friday.", "relevance": 0.8, "date": "2026-10-01",
                     "timestamp": "2026-10-01T10:00", "role": "user", "id": "abc"}]
    monkeypatch.setattr(mr, "_get_conversation_memory", lambda: Mem())
    monkeypatch.setattr(mr, "_load_settings", lambda: {"memory_recall_enabled": True})
    stub = types.ModuleType("agent_friday.services.evidence_heads")

    def boom(*a, **k):
        raise RuntimeError("the shadow head failed")
    stub.shadow_memory = boom
    monkeypatch.setitem(sys.modules, "agent_friday.services.evidence_heads", stub)
    import agent_friday.services as _svc
    monkeypatch.setattr(_svc, "evidence_heads", stub, raising=False)
    block_when_it_raises = mr._build_memory_context_block("When is the review?", "s1")
    stub.shadow_memory = lambda q, snippets, surface="chat": calls.append((q, list(snippets)))
    block = mr._build_memory_context_block("When is the review?", "s1")
    assert calls == [("When is the review?", ["We moved the review to Friday."])]
    assert block == block_when_it_raises and "review to Friday" in block


def test_no_generative_model_scores_a_passage():
    from agent_friday.services import decision_heads as dh
    for hid in ("evidence.holds", "evidence.sufficient"):
        spec = json.loads((dh.SHIPPED_DIR / ("%s.json" % hid)).read_text(encoding="utf-8"))
        assert not dh._is_generative(spec["scorer"])
