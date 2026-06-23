"""Unit tests for Take Comparison (services/take_comparison.py).

Generation + scoring are stubbed (creative_engine + qa_gates), so no model or
key is touched. Covers image/text take generation, best-take recommendation,
and the generic ranker.
"""
import pytest

from services import take_comparison as tc
from services import qa_gates
from services import creative_engine as ce


# ── recommendation logic ────────────────────────────────────────────────────
def test_recommend_picks_highest_score():
    takes = [{"score": 0.4}, {"score": 0.9}, {"score": 0.6}]
    assert tc._recommend(takes) == 1


def test_recommend_all_unscored_falls_back_to_first():
    assert tc._recommend([{"score": None}, {"score": None}]) == 0


# ── text takes ──────────────────────────────────────────────────────────────
def test_compare_text_recommends_best(monkeypatch):
    scores = {"cand-0": 0.3, "cand-1": 0.85, "cand-2": 0.5}
    monkeypatch.setattr(qa_gates, "evaluate_text",
                        lambda content, intent, **k: {"score": scores[content],
                                                      "critique": "", "suggestions": ""})
    res = tc.compare_text("be punchy", lambda i: f"cand-{i}", n=3)
    assert res["status"] == "ok"
    assert len(res["takes"]) == 3
    assert res["recommended_index"] == 1
    assert res["recommended"]["content"] == "cand-1"


def test_compare_text_skips_empty_candidates(monkeypatch):
    monkeypatch.setattr(qa_gates, "evaluate_text",
                        lambda *a, **k: {"score": 0.7, "critique": "", "suggestions": ""})
    res = tc.compare_text("x", lambda i: "" if i == 0 else f"c{i}", n=2)
    statuses = {t["status"] for t in res["takes"]}
    assert "error" in statuses and "ok" in statuses


# ── image takes ─────────────────────────────────────────────────────────────
def test_compare_images_recommends_best(monkeypatch):
    monkeypatch.setattr(ce, "is_available", lambda: True)

    counter = {"n": 0}

    def fake_gen(prompt, **k):
        counter["n"] += 1
        i = counter["n"]
        return {"status": "ok",
                "files": [{"filename": f"img{i}.png", "url": f"/api/creations/img{i}.png",
                           "path": f"/tmp/img{i}.png"}]}
    monkeypatch.setattr(ce, "generate_image", fake_gen)

    img_scores = {"/tmp/img1.png": 0.5, "/tmp/img2.png": 0.95, "/tmp/img3.png": 0.6}
    monkeypatch.setattr(qa_gates, "evaluate_image",
                        lambda path, intent: {"score": img_scores[path],
                                              "critique": "ok", "suggestions": ""})

    res = tc.compare_images("a dragon", n=3)
    assert res["status"] == "ok"
    assert len(res["takes"]) == 3
    assert res["recommended"]["filename"] == "img2.png"


def test_compare_images_unavailable_without_key(monkeypatch):
    monkeypatch.setattr(ce, "is_available", lambda: False)
    res = tc.compare_images("a dragon", n=2)
    assert res["status"] == "unavailable"


def test_compare_images_blocked_propagates(monkeypatch):
    monkeypatch.setattr(ce, "is_available", lambda: True)
    monkeypatch.setattr(ce, "generate_image",
                        lambda *a, **k: {"status": "blocked", "reason": "nope"})
    res = tc.compare_images("bad prompt", n=2)
    assert res["status"] == "blocked"


# ── generic ranker ──────────────────────────────────────────────────────────
def test_rank_takes_sorts_desc_and_ranks():
    takes = [{"id": "a", "score": 0.2}, {"id": "b", "score": 0.8},
             {"id": "c", "score": 0.5}]
    res = tc.rank_takes(takes)
    assert [t["id"] for t in res["takes"]] == ["b", "c", "a"]
    assert res["takes"][0]["rank"] == 1
    assert res["recommended"]["id"] == "b"


def test_rank_takes_with_custom_scorer():
    takes = [{"len": 3}, {"len": 9}, {"len": 1}]
    res = tc.rank_takes(takes, scorer=lambda t: t["len"] / 10.0)
    assert res["recommended"]["len"] == 9
