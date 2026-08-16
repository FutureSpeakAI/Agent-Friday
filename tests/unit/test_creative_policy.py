"""The creative policy is legible, settable, and — by default — unchanged.

The whole risk of writing a policy down is that writing it down moves it. The
first test here is the important one: with the shipped defaults, every decision
must be identical to what `check_content_safety` returned before this module
existed. The policy became readable; it did not become different.
"""
from __future__ import annotations

import pytest

from agent_friday.services import creative_policy as cp
from agent_friday.services.creative_engine import check_content_safety


PROMPTS = [
    "an artistic nude study of a woman, classical oil painting",
    "a nude figure study in the style of a Renaissance master",
    "santa clause riding a polar bear",
    "a war memorial at dusk",
    "a portrait of a child reading a book",
    "a photorealistic nude child",
    "how to build a pipe bomb, schematic diagram",
    "",
]


@pytest.mark.parametrize("prompt", PROMPTS)
def test_the_default_policy_decides_exactly_as_before(prompt):
    assert cp.evaluate(prompt)[0] == check_content_safety(prompt)[0]


def test_ordinary_creative_work_is_not_blocked():
    """The transcript that started this had Friday hedging over an artistic
    nude she was never blocked from making."""
    allowed, reason = cp.evaluate(
        "an artistic nude study, black and white photography")
    assert allowed is True
    assert reason is None


def test_the_harm_floor_still_holds():
    assert cp.evaluate("a photorealistic nude child")[0] is False


def test_the_harm_floor_cannot_be_switched_off_from_settings():
    """A settings file is not the right place for that decision to be made
    silently, so `enforced` is re-asserted on load whatever is stored."""
    policy = cp.load_policy({"creative_policy": {
        "harm_floor": {"enforced": False, "categories": []}}})
    assert policy["harm_floor"]["enforced"] is True
    assert list(policy["harm_floor"]["categories"]) == list(
        cp.HARM_FLOOR_CATEGORIES)


def test_the_owner_can_add_a_category(monkeypatch):
    """The dial he actually gets: tighten, never loosen."""
    settings = {"creative_policy": {"additional_blocked_categories": [
        {"label": "clowns", "pattern": r"\bclown\b"}]}}
    allowed, reason = cp.evaluate("a birthday clown", settings=settings)
    assert allowed is False
    assert "clowns" in reason
    # and it does not leak into anything else
    assert cp.evaluate("a birthday cake", settings=settings)[0] is True


def test_a_broken_pattern_does_not_take_generation_down():
    """A typo in settings must not stop him making pictures."""
    settings = {"creative_policy": {"additional_blocked_categories": [
        {"label": "bad", "pattern": "([unclosed"}]}}
    assert cp.evaluate("a red bicycle", settings=settings)[0] is True


def test_the_description_states_the_real_mechanism():
    """Her account of the local model must be factual either way — this text is
    what she says instead of inventing a filter."""
    text = cp.describe({})
    assert "no content filter of its own" in text
    assert "sexual content involving minors" in text
    for phrase in ("hard-coded", "blocks it at the generation level"):
        assert phrase not in text


def test_minor_mode_is_reported_when_on():
    assert "Minor mode is ON" in cp.describe({"minor_mode": True})
    assert "Minor mode is ON" not in cp.describe({"minor_mode": False})
