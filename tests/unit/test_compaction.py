"""Unit tests for transcript auto-compaction (Part C)."""
from services import compaction as comp


_CFG = {"enabled": True, "keep_head": 3, "keep_tail": 10,
        "context_window": 2000, "trigger_ratio": 0.70, "summary_max_tokens": 400}


def _msgs(n_middle=40):
    head = [{"role": "user", "content": "H0"},
            {"role": "assistant", "content": "H1"},
            {"role": "user", "content": "H2"}]
    middle = [{"role": ("user" if i % 2 == 0 else "assistant"),
               "content": "y" * 5000} for i in range(n_middle)]
    tail = [{"role": "assistant", "content": "TAIL"}]
    return head + middle + tail


def test_estimate_tokens_charwise():
    msgs = [{"role": "user", "content": "x" * 400}]
    assert comp.estimate_tokens(msgs) == 100   # 400 chars / 4


def test_below_threshold_is_noop(monkeypatch):
    monkeypatch.setattr(comp, "_cfg", lambda: dict(_CFG, context_window=10_000_000))
    msgs = _msgs()
    assert comp.maybe_compact(msgs, summarizer=lambda t, m: "S") is msgs


def test_disabled_is_noop(monkeypatch):
    monkeypatch.setattr(comp, "_cfg", lambda: {"enabled": False})
    msgs = _msgs()
    assert comp.maybe_compact(msgs, summarizer=lambda t, m: "S") is msgs


def test_compaction_preserves_head_and_tail(monkeypatch):
    monkeypatch.setattr(comp, "_cfg", lambda: dict(_CFG))
    msgs = _msgs()
    out = comp.maybe_compact(msgs, summarizer=lambda t, m: "CONDENSED")
    assert len(out) < len(msgs)
    # Head intact.
    assert out[0]["content"] == "H0"
    # Tail preserved (last turn's content ends the compacted list).
    assert out[-1]["content"].endswith("TAIL")
    # Exactly one summary marker present.
    summaries = [m for m in out if isinstance(m.get("content"), str)
                 and comp._SUMMARY_PREFIX in m["content"]]
    assert len(summaries) == 1
    assert "CONDENSED" in summaries[0]["content"]


def test_compaction_role_alternation(monkeypatch):
    monkeypatch.setattr(comp, "_cfg", lambda: dict(_CFG))
    out = comp.maybe_compact(_msgs(), summarizer=lambda t, m: "S")
    roles = [m["role"] for m in out]
    assert all(roles[i] != roles[i + 1] for i in range(len(roles) - 1))


def test_empty_summary_degrades_to_noop(monkeypatch):
    monkeypatch.setattr(comp, "_cfg", lambda: dict(_CFG))
    msgs = _msgs()
    # Summarizer returns nothing → never silently lose the middle.
    assert comp.maybe_compact(msgs, summarizer=lambda t, m: "") is msgs


def test_idempotent_recompaction(monkeypatch):
    monkeypatch.setattr(comp, "_cfg", lambda: dict(_CFG))
    out1 = comp.maybe_compact(_msgs(), summarizer=lambda t, m: "S1")
    # Feed it back enlarged — a second pass folds the prior summary in, still
    # yielding exactly one summary marker (no summary-of-summaries pileup).
    grown = out1[:1] + [{"role": "assistant", "content": "z" * 5000}
                        for _ in range(40)] + out1[1:]
    out2 = comp.maybe_compact(grown, summarizer=lambda t, m: "S2")
    summaries = [m for m in out2 if isinstance(m.get("content"), str)
                 and comp._SUMMARY_PREFIX in m["content"]]
    assert len(summaries) == 1
