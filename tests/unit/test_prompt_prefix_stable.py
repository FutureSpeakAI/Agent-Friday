"""The start of every prompt is the same from turn to turn.

A local seat reuses its prompt cache only up to the first changed byte, and
the system prompt comes first, so one per-message block near the top made
llama-server re-read the whole prompt every turn (43 s for ~21k tokens;
0.43 s when the prefix repeated). The rule: everything chosen by THIS
message (wiki matches, memories, skills, the screen, the clock) sits below
`prompt_cache.VOLATILE_MARKER`; everything above it is byte-identical across
the turns of a conversation. On the local seat the volatile tail rides in
the newest user turn and the system message is the stable head alone.
"""
from __future__ import annotations

import pytest

from agent_friday.services import model_router as mr
from agent_friday.services.prompt_cache import VOLATILE_MARKER


def _split(prompt: str):
    i = prompt.find(VOLATILE_MARKER)
    assert i > 0, "the clock marker is missing from the assembled prompt"
    return prompt[:i], prompt[i:]


@pytest.fixture
def per_message(monkeypatch):
    """Sources that answer differently for different messages, and a clock
    that moves, so a block placed above the marker shows up as a changed head."""
    import agent_friday.skill_registry as skreg
    monkeypatch.setattr(skreg, "build_injection",
                        lambda message, *a, **k: f"### skill for: {message}\nDo the thing.")
    from agent_friday.services import clock as clk
    ticks = iter(["12:00", "12:01", "12:02", "12:03"])
    monkeypatch.setattr(clk, "clock_context_block",
                        lambda *a, **k: f"{VOLATILE_MARKER}\nNow: {next(ticks)}\n")
    monkeypatch.setattr(mr, "_load_smart_context",
                        lambda message, *a, **k: f"== PERSONAL CONTEXT ==\nabout {message}",
                        raising=False)


def test_two_messages_share_the_whole_head(per_message):
    p1, _ = mr._build_context_prompt("tell me about cats", workspace="chat", provider="local")
    p2, _ = mr._build_context_prompt("what is the weather tomorrow", workspace="chat", provider="local")
    h1, t1 = _split(p1)
    h2, t2 = _split(p2)
    assert h1 == h2, "the head changed between two messages"
    assert t1 != t2, "the tail should carry what the message chose"
    assert "skill for: tell me about cats" in t1 and "skill for: tell me about cats" not in h1
    assert mr.FRIDAY_SYSTEM_PROMPT.strip()[:200] in h1
    assert "== TOOLS ==" in h1


def test_the_head_is_a_real_share_of_the_prompt(per_message):
    p1, _ = mr._build_context_prompt("tell me about cats", workspace="chat", provider="local")
    p2, _ = mr._build_context_prompt("what is the weather tomorrow", workspace="chat", provider="local")
    shared = 0
    for a, b in zip(p1, p2):
        if a != b:
            break
        shared += 1
    head, _ = _split(p1)
    assert shared >= len(head), "the shared prefix ends before the marker"
    assert shared // 4 >= 1000, "the stable head is too small to be worth caching: %d tokens" % (shared // 4)


def test_the_workspace_block_stays_in_the_head(per_message):
    ws = {"name": "news", "data": {"headline": "x"}, "focus": "the front page"}
    p1, _ = mr._build_context_prompt("one", workspace="news", workspace_context=ws, provider="local")
    p2, _ = mr._build_context_prompt("two", workspace="news", workspace_context=ws, provider="local")
    h1, _ = _split(p1)
    h2, _ = _split(p2)
    assert h1 == h2 and "== ACTIVE WORKSPACE" in h1 and "the front page" in h1


def test_every_per_message_section_is_below_the_marker(per_message):
    p, _ = mr._build_context_prompt("tell me about cats", workspace="chat", provider="local")
    head, tail = _split(p)
    for heading in ("== MATCHED SKILLS", "== PERSONAL CONTEXT", "== EPISTEMIC STATE",
                    "== PROJECT CONTEXT FILES", "== RECENT MEMORIES",
                    "== WIKI/BRIEFING DATA", "== SCREEN VISION"):
        assert heading not in head, "%s sits above the cache boundary" % heading
    # Today's context is the same for every turn of the day: it stays above.
    if "== TODAY'S CONTEXT ==" in p:
        assert "== TODAY'S CONTEXT ==" in head
