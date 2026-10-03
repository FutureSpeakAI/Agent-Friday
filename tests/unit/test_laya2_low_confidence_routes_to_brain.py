"""Low confidence, a state change, a missing or slow encoder: the brain's turn.

The encoder is faked at `laya2_encoder.embed`, so this runs without the
artifact. The fake maps every seed utterance to the unit vector of its own
class, which makes each prototype a basis vector, and then returns whatever
vector the test chooses for the query: a basis vector is a confident match, a
blend of two is a top-two within the margin.

Mutation-checked: a `decide` that ignores the margin fails
`test_a_top_two_within_the_margin_routes_to_the_brain`; one that ignores the
mutation fails `test_a_state_change_never_routes_reflex`.
"""
from __future__ import annotations

import hashlib
import json
import math
import time

from agent_friday.services import laya2_encoder, laya2_seeds, reflex_turn

SHAPES = list(laya2_seeds.SHAPE_SEEDS)
MUTS = list(laya2_seeds.MUTATION_SEEDS)
DIM = len(SHAPES) + len(MUTS)


def _basis(index):
    v = [0.0] * DIM
    v[index] = 1.0
    return v


def _unit(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _query(shape, mutation, second_shape=None, second_weight=0.0, scale=1.0):
    """A query vector: `shape` and `mutation` on, optionally a runner-up."""
    v = [0.0] * DIM
    v[SHAPES.index(shape)] = 1.0
    if second_shape:
        v[SHAPES.index(second_shape)] = second_weight
    v[len(SHAPES) + MUTS.index(mutation)] = 1.0
    return [x * scale for x in _unit(v)]


class _FakeEncoder:
    def __init__(self, query_vec, delay_s=0.0):
        self.query_vec = query_vec
        self.delay_s = delay_s
        self.by_text = {}
        for i, shape in enumerate(SHAPES):
            for t in laya2_seeds.SHAPE_SEEDS[shape]:
                self.by_text[t] = _basis(i)
        for j, mut in enumerate(MUTS):
            for t in laya2_seeds.MUTATION_SEEDS[mut]:
                self.by_text[t] = _basis(len(SHAPES) + j)

    def embed(self, texts, max_len=256):
        if all(t in self.by_text for t in texts):
            return [self.by_text[t] for t in texts]
        if self.delay_s:
            time.sleep(self.delay_s)
        return [list(self.query_vec) for _ in texts]


def _install(monkeypatch, fake):
    reflex_turn.reset_cache()
    monkeypatch.setattr(laya2_encoder, "is_available", lambda: True)
    monkeypatch.setattr(laya2_encoder, "embed", fake.embed)


def test_a_confident_far_vector_routes_reflex(monkeypatch):
    _install(monkeypatch, _FakeEncoder(_query("reflex_open", "read_only")))
    v = reflex_turn.classify("open the thing", budget_ms=2000)
    assert v["degraded"] is False
    assert v["shape"] == "reflex_open" and v["mutation"] == "read_only"
    assert v["shape_conf"] >= reflex_turn.ACT_THRESHOLD
    assert v["margin"] >= reflex_turn.MARGIN_THRESHOLD
    assert v["oos"] is False
    assert (v["route"], v["effort"]) == ("reflex", "none")


def test_a_top_two_within_the_margin_routes_to_the_brain(monkeypatch):
    # Two reflex prototypes almost equally near: a margin far under 0.10.
    _install(monkeypatch, _FakeEncoder(
        _query("reflex_open", "read_only", second_shape="reflex_navigate", second_weight=0.95)))
    v = reflex_turn.classify("open or go to the thing", budget_ms=2000)
    assert v["degraded"] is False
    assert v["margin"] < reflex_turn.MARGIN_THRESHOLD
    assert (v["route"], v["effort"]) == ("brain", "medium")


def test_a_state_change_never_routes_reflex(monkeypatch):
    _install(monkeypatch, _FakeEncoder(_query("reflex_organise", "state_changing")))
    v = reflex_turn.classify("delete the thing", budget_ms=2000)
    assert v["degraded"] is False
    assert v["shape"] == "reflex_organise"
    assert v["shape_conf"] >= reflex_turn.ACT_THRESHOLD
    assert v["mutation"] == "state_changing"
    assert v["route"] == "brain"


def test_out_of_scope_routes_to_the_brain(monkeypatch):
    _install(monkeypatch, _FakeEncoder(_query("out_of_scope", "read_only")))
    v = reflex_turn.classify("flibbertigibbet", budget_ms=2000)
    assert v["oos"] is True and v["route"] == "brain"


def test_a_weak_best_match_is_out_of_scope(monkeypatch):
    # Nearest prototype is a reflex, but the cosine is under the floor.
    _install(monkeypatch, _FakeEncoder(_query("reflex_open", "read_only", scale=0.2)))
    v = reflex_turn.classify("something the seeds never saw", budget_ms=2000)
    assert v["shape"] == "reflex_open"
    assert v["oos"] is True and v["route"] == "brain"


def test_a_deep_shape_asks_for_xhigh_and_stays_with_the_brain(monkeypatch):
    _install(monkeypatch, _FakeEncoder(_query("deep_coding", "read_only")))
    v = reflex_turn.classify("refactor everything", budget_ms=2000)
    assert (v["route"], v["effort"]) == ("brain", "xhigh")


def test_a_missing_encoder_degrades_to_the_brain(monkeypatch):
    reflex_turn.reset_cache()
    monkeypatch.setattr(laya2_encoder, "is_available", lambda: False)
    before = reflex_turn.counters()["degraded_missing"]
    v = reflex_turn.classify("open my inbox")
    assert v["degraded"] is True
    assert (v["route"], v["effort"]) == ("brain", "medium")
    assert v["reason"] == "missing"
    assert reflex_turn.counters()["degraded_missing"] == before + 1


def test_shadow_writes_a_line_that_never_carries_the_text(monkeypatch, tmp_path):
    _install(monkeypatch, _FakeEncoder(_query("reflex_open", "read_only")))
    log = tmp_path / "reflex_shadow.jsonl"
    monkeypatch.setattr(reflex_turn, "shadow_path", lambda: log)
    utterance = "open the secret plans for the surprise party"
    v = reflex_turn.shadow(utterance, conversation_id="conv-1234")
    assert v is not None and v["shape"] == "reflex_open"
    raw = log.read_text(encoding="utf-8")
    assert utterance not in raw
    for word in ("secret", "surprise", "conv-1234"):
        assert word not in raw
    lines = [json.loads(line) for line in raw.splitlines() if line.strip()]
    assert len(lines) == 1
    line = lines[0]
    assert set(line) == {"ts", "text_sha16", "len", "shape", "shape_conf", "margin",
                         "mutation", "oos", "route", "effort", "elapsed_ms", "degraded"}
    assert line["text_sha16"] == hashlib.sha256(utterance.encode()).hexdigest()[:16]
    assert line["len"] == len(utterance)
    assert line["route"] == "reflex"


def test_shadow_never_raises(monkeypatch):
    reflex_turn.reset_cache()
    monkeypatch.setattr(laya2_encoder, "is_available", lambda: False)

    def boom():
        raise OSError("disk gone")
    monkeypatch.setattr(reflex_turn, "shadow_path", boom)
    v = reflex_turn.shadow("anything")
    assert v is not None and v["degraded"] is True


def test_a_slow_encoder_is_not_waited_for(monkeypatch):
    # Last on purpose: the fake keeps the worker busy for its delay.
    _install(monkeypatch, _FakeEncoder(_query("reflex_open", "read_only"), delay_s=0.4))
    t0 = time.perf_counter()
    v = reflex_turn.classify("open the thing", budget_ms=30)
    waited_ms = (time.perf_counter() - t0) * 1000
    assert v["degraded"] is True and v["reason"] == "budget"
    assert (v["route"], v["effort"]) == ("brain", "medium")
    assert waited_ms < 300, waited_ms
    time.sleep(0.5)
