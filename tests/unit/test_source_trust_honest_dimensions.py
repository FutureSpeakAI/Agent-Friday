"""What the trust score shows is what is measured; a placeholder is never a number.

Red on main 2f88c152a: "prediction accuracy" was a placeholder pinned at 0.5
that still carried 5% of the composite, "narrative independence" had no local
measurement at all yet carried 15% at its seed value, and the UI labelled a
primary-document corroboration proxy "Factual accuracy". An outlet whose every
measured signal was perfect could not score above ~0.8, and the feed, the
voice tool and the Trust view all showed 0.50 for "Predictions".
"""
from __future__ import annotations

import json
import pathlib

import pytest

from agent_friday import source_trust_graph as stg
from agent_friday.source_trust_graph import SourceTrustGraph

ROOT = pathlib.Path(__file__).resolve().parents[2]
LOCAL_DIMS = ("factual_accuracy", "correction_behavior", "source_attribution", "opinion_separation")


@pytest.fixture
def graph(tmp_path):
    return SourceTrustGraph(friday_dir=tmp_path)


def _perfect(graph, domain, n=40):
    for dim in LOCAL_DIMS:
        for _ in range(n):
            graph.observe(domain, "test_signal", dim, 1.0)


def test_unmeasured_dimensions_have_no_score(graph):
    graph.observe("outlet.test", "attribution_present", "source_attribution", 0.9)
    dims = graph.dimensions_for("outlet.test")
    assert dims["prediction_accuracy"] is None
    assert dims["narrative_independence"] is None
    assert graph.dimensions_for("never-seen.test")["prediction_accuracy"] is None


def test_a_placeholder_never_pulls_the_composite(graph):
    _perfect(graph, "outlet.test")
    rec = graph.get("outlet.test")
    measured = [rec["scores"][d] for d in LOCAL_DIMS]
    w = stg._WEIGHTS
    want = sum(w[d] * rec["scores"][d] for d in LOCAL_DIMS) / sum(w[d] for d in LOCAL_DIMS)
    assert rec["trust_score"] == pytest.approx(want, abs=1e-3)
    assert rec["trust_score"] >= min(measured) - 1e-6


def test_a_seeded_outlet_scores_its_seed_not_a_blend_with_placeholders(graph):
    # Every measured dimension of a high-trust seed is 0.75; the composite is 0.75.
    assert graph.score_for("reuters.com") == pytest.approx(0.75, abs=1e-6)


def test_a_peer_measurement_brings_the_dimension_in(graph):
    graph.observe("outlet.test", "prediction_wrong", "prediction_accuracy", 0.2,
                  signed_by="peer-key")
    ex = graph.explain("outlet.test")
    pred = ex["dimensions"]["prediction_accuracy"]
    assert pred["score"] is not None and pred["in_composite"] and pred["evidence_count"] == 1


def test_explain_says_what_each_dimension_is_based_on(graph):
    graph.observe("outlet.test", "attribution_present", "source_attribution", 0.9)
    ex = graph.explain("outlet.test")
    fa = ex["dimensions"]["factual_accuracy"]
    assert "accuracy" not in fa["label"].lower()
    assert "primary" in fa["label"].lower()
    assert fa["status"] == "prior only (no evidence yet)" and fa["evidence_count"] == 0
    sa = ex["dimensions"]["source_attribution"]
    assert "writing style" in sa["label"].lower() and sa["status"] == "measured"
    assert sa["evidence_count"] == 1
    for k in ("prediction_accuracy", "narrative_independence"):
        assert ex["dimensions"][k]["status"] == "not measured"
        assert ex["dimensions"][k]["score"] is None
        assert ex["dimensions"][k]["in_composite"] is False


def test_the_feed_metadata_carries_no_placeholder_number(graph, monkeypatch):
    from agent_friday.services import news_engine as ne
    graph.observe("outlet.test", "attribution_present", "source_attribution", 0.9)
    monkeypatch.setattr(ne, "_HAS_TRUST_GRAPHS", True)
    monkeypatch.setattr(ne, "get_source_trust_graph", lambda friday_dir=None: graph)
    meta = ne._source_trust_meta("outlet.test", banned=set(), boosted=set())
    assert meta["trust_dims"]["prediction_accuracy"] is None
    assert meta["trust_dims"]["source_attribution"] is not None


def test_the_voice_tool_says_not_measured(graph, monkeypatch):
    from agent_friday.services import voice_engine as ve
    graph.observe("outlet.test", "attribution_present", "source_attribution", 0.9)
    monkeypatch.setattr(ve, "_HAS_TRUST_GRAPHS", True)
    monkeypatch.setattr(ve, "get_source_trust_graph", lambda friday_dir=None: graph)
    out = json.loads(ve._tool_get_source_trust({"domain": "outlet.test"}))
    assert out["dimensions"]["Predictions"] == "not measured"
    assert isinstance(out["observations"], int)
    assert not any("accuracy" in k.lower() for k in out["dimensions"])


@pytest.mark.parametrize("rel", ["index.html", "ui_parts/app.html"])
def test_the_ui_labels_say_what_is_measured(rel):
    text = (ROOT / rel).read_text(encoding="utf-8")
    start = text.index("const TRUST_DIMS")
    block = text[start:text.index("];", start)]
    assert "Factual accuracy" not in block
    assert "Primary-source corroboration" in block and "writing style" in block
    # A dimension with no score renders as "not measured", never as a number.
    assert "TRUST_NOT_MEASURED = 'not measured'" in text or "TRUST_NOT_MEASURED='not measured'" in text
    assert text.count("TRUST_NOT_MEASURED") >= 3
