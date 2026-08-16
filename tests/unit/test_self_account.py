"""Friday's account of herself is generated, so it cannot go stale.

She had said "Gemma 4" without distinguishing the 12b from the 26b, called real
Arbiter-scheduled seats "integrated capabilities", and listed Lyria among her
abilities when the installed SDK exposes no music surface at all.
"""
from __future__ import annotations

from agent_friday.services import self_account as sa


class _StubArb:
    plan = {"seats": {
        "interactive_brain": {"model_id": "gemma4:12b", "device": "gpu:0",
                              "num_ctx": 131072, "status": "pinned",
                              "backend": "llama-server"},
        "heavy_hitter": {"model_id": "gemma4:26b", "device": "gpu:0+cpu",
                         "num_ctx": 32768, "status": "leased",
                         "backend": "llama-server"},
    }}


def test_seats_are_named_with_their_real_models(monkeypatch):
    import agent_friday.services.residency_arbiter as ra
    monkeypatch.setattr(ra, "get_arbiter", lambda: _StubArb())
    text = sa.describe(include_policy=False)
    assert "gemma4:12b" in text and "gemma4:26b" in text
    assert "interactive_brain" in text and "heavy_hitter" in text


def test_no_arbiter_means_it_says_less_not_wrong(monkeypatch):
    """Degrading to silence is correct; falling back on a hand-written
    description is what produced the wrong answers."""
    import agent_friday.services.residency_arbiter as ra
    monkeypatch.setattr(ra, "get_arbiter", lambda: None)
    text = sa.describe(include_policy=False)
    assert "seats the residency Arbiter" not in text


def test_capabilities_are_probed_not_declared():
    caps = {c["name"]: c for c in sa.capabilities()}
    assert "music generation (Lyria)" in caps
    assert "image generation" in caps
    # Whatever the answers are on this machine, each one is a measured boolean
    # with a reason attached when it is False.
    for c in caps.values():
        assert isinstance(c["available"], bool)
        if not c["available"]:
            assert c.get("note")


def test_an_unavailable_capability_is_not_offered(monkeypatch):
    from agent_friday.services import music_engine
    monkeypatch.setattr(music_engine, "cloud_music_available",
                        lambda: (False, "no batch Lyria surface"))
    caps = {c["name"]: c for c in sa.capabilities()}
    music = caps["music generation (Lyria)"]
    assert music["available"] is False
    assert "Do not offer it" in music["note"]


def test_describe_never_raises(monkeypatch):
    """A self-description that fails must degrade, never block a turn."""
    import agent_friday.services.residency_arbiter as ra

    def boom():
        raise RuntimeError("no arbiter today")
    monkeypatch.setattr(ra, "get_arbiter", boom)
    assert isinstance(sa.describe(), str)
