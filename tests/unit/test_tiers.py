"""A tier that cannot be served says so, and never substitutes.

Spec 3.1. Nothing could direct work to a model other than the one already
answering, so the four-tier architecture existed on paper and was inert: every
background task ran on whatever the orchestrator happened to be.

The tests that matter here are the refusals. A resolver that quietly hands
back the cloud when the local seat is down has lied about the one property the
caller named, and that is not hypothetical - on 2026-09-18 a local model
silently stopped being local because a written-down port had gone stale, and
the user saw Sonnet answer with nothing on screen to explain it.
"""
from __future__ import annotations

import pytest

from agent_friday.services import tiers


def _routing(monkeypatch, **seats):
    monkeypatch.setattr(tiers, "_capability",
                        lambda name: seats.get(name, ""))


def _live(monkeypatch, *models):
    monkeypatch.setattr(tiers, "_serving_locally", lambda: set(models))


def test_large_local_resolves_to_the_heavy_seat(monkeypatch):
    _routing(monkeypatch, heavy_hitter="bonsai2:27b")
    _live(monkeypatch, "bonsai2:27b")
    r = tiers.resolve(tiers.LARGE_LOCAL)
    assert r.ok and r.model == "bonsai2:27b" and r.is_local


def test_large_local_is_refused_when_the_model_is_not_loaded(monkeypatch):
    """THE CRUX. Not loaded means refused, with what IS loaded named - not
    the cloud, and not a smaller model wearing the big one's name."""
    _routing(monkeypatch, heavy_hitter="bonsai2:27b",
             reasoning="claude-sonnet-5")
    _live(monkeypatch, "gemma4:e2b")
    r = tiers.resolve(tiers.LARGE_LOCAL)
    assert not r.ok
    assert "bonsai2:27b" in r.reason and "gemma4:e2b" in r.reason
    assert r.model is None, "a refusal must not hand back a model"


def test_small_local_resolves_to_the_sidekick(monkeypatch):
    _routing(monkeypatch, sidekick_fast="gemma4:e2b")
    _live(monkeypatch, "gemma4:e2b")
    r = tiers.resolve(tiers.SMALL_LOCAL)
    assert r.ok and r.model == "gemma4:e2b"


def test_an_unbound_seat_is_refused_rather_than_guessed(monkeypatch):
    _routing(monkeypatch)
    _live(monkeypatch, "bonsai2:27b")
    r = tiers.resolve(tiers.SMALL_LOCAL)
    assert not r.ok and "no model is bound" in r.reason


def test_cloud_frontier_is_marked_not_local(monkeypatch):
    """The caller has to be able to tell that this one leaves the machine,
    because that is what it will have to say to the user."""
    _routing(monkeypatch, reasoning="claude-sonnet-5")
    r = tiers.resolve(tiers.CLOUD_FRONTIER)
    assert r.ok and r.model == "claude-sonnet-5"
    assert r.is_local is False


def test_cloud_frontier_does_not_return_a_local_model(monkeypatch):
    """If the reasoning seat is bound to the 27B, `cloud_frontier` has nothing
    to offer and must say so. Returning the local model would make the tier
    names meaningless in the one direction that costs money to get wrong."""
    _routing(monkeypatch, reasoning="bonsai2:27b")
    r = tiers.resolve(tiers.CLOUD_FRONTIER)
    assert not r.ok and "no cloud model" in r.reason


def test_an_unknown_tier_is_refused_and_lists_the_real_ones(monkeypatch):
    r = tiers.resolve("enormous")
    assert not r.ok
    for t in tiers.TIERS:
        assert t in r.reason


def test_a_failed_residency_probe_does_not_refuse_the_work(monkeypatch):
    """Absence of evidence is not evidence of absence - the rule applied
    everywhere else in this codebase.

    Refusing because a probe failed denies the caller something that would
    have worked, for a reason that is not true. The resolution carries the
    model AND says the state is unconfirmed, so the caller can pass that on.
    """
    _routing(monkeypatch, heavy_hitter="bonsai2:27b")
    monkeypatch.setattr(tiers, "_serving_locally", lambda: set())
    r = tiers.resolve(tiers.LARGE_LOCAL)
    assert r.ok and r.model == "bonsai2:27b"
    assert "could not confirm" in r.reason


def test_resolve_never_raises(monkeypatch):
    """A resolver that can crash the turn asking it is worse than none."""
    def _boom(*a, **k):
        raise RuntimeError("settings are gone")
    monkeypatch.setattr(tiers, "_settings", _boom)
    for t in list(tiers.TIERS) + ["", None, "nonsense"]:
        try:
            tiers.resolve(t)
        except Exception as e:      # pragma: no cover - this is the assertion
            pytest.fail("resolve(%r) raised %s" % (t, e))


def test_every_tier_is_described_for_the_model():
    """The tool schema quotes these. A tier with no description is a tier the
    model will guess at."""
    for t in tiers.TIERS:
        assert tiers.DESCRIPTIONS.get(t), "%s has no description" % t
        assert t in tiers.describe_for_model()
    assert "costs money" in tiers.describe_for_model().lower(), \
        "the cloud tier must state its cost where the model will read it"


# ── spawn_task, the caller that makes the vocabulary real ──────────────────

def test_spawn_task_refuses_an_unservable_tier_instead_of_relocating(monkeypatch):
    """A refusal the model can repeat, not a task quietly started elsewhere.

    Without this the tier argument is decoration: the model asks for the 27B,
    the 27B is not there, and something runs anyway on a seat nobody chose.
    """
    import json as _json
    from agent_friday.services import agent as _agent

    _routing(monkeypatch, heavy_hitter="bonsai2:27b")
    _live(monkeypatch, "gemma4:e2b")

    spawned = []
    monkeypatch.setattr(_agent, "_spawn_task",
                        lambda *a, **k: spawned.append(k) or "task-1")

    out = _json.loads(_agent._tool_spawn_task({
        "name": "Deep read", "prompt": "Read the brief.",
        "tier": "large_local"}))
    assert out["status"] == "refused"
    assert "bonsai2:27b" in out["message"]
    assert not spawned, "nothing may be started when the tier was refused"


def test_spawn_task_passes_the_resolved_model_through(monkeypatch):
    import json as _json
    from agent_friday.services import agent as _agent

    _routing(monkeypatch, heavy_hitter="bonsai2:27b")
    _live(monkeypatch, "bonsai2:27b")

    seen = {}
    monkeypatch.setattr(_agent, "_spawn_task",
                        lambda *a, **k: seen.update(k) or "task-2")

    out = _json.loads(_agent._tool_spawn_task({
        "name": "Deep read", "prompt": "Read the brief.",
        "tier": "large_local"}))
    assert out["status"] == "running"
    assert seen.get("model") == "bonsai2:27b"


def test_spawn_task_says_when_the_work_leaves_the_machine(monkeypatch):
    """Escalation surfaces rather than spending quietly."""
    import json as _json
    from agent_friday.services import agent as _agent

    _routing(monkeypatch, reasoning="claude-sonnet-5")
    monkeypatch.setattr(_agent, "_spawn_task", lambda *a, **k: "task-3")

    out = _json.loads(_agent._tool_spawn_task({
        "name": "Hard one", "prompt": "Think.", "tier": "cloud_frontier"}))
    assert out["status"] == "running"
    assert "off-device" in out["message"] and "billed" in out["message"]


def test_spawn_task_without_a_tier_is_unchanged(monkeypatch):
    """The argument is optional and its absence must not alter behaviour."""
    import json as _json
    from agent_friday.services import agent as _agent

    seen = {}
    monkeypatch.setattr(_agent, "_spawn_task",
                        lambda *a, **k: seen.update(k) or "task-4")
    out = _json.loads(_agent._tool_spawn_task({
        "name": "Ordinary", "prompt": "Do the thing."}))
    assert out["status"] == "running"
    assert seen.get("model") is None