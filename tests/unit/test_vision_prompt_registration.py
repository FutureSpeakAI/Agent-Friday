"""Unit tests for security-boundary.md §19 row 11 (corrected on the §1.3
re-verification pass): the vision prompt text at routes/chat.py:388,:1489 is
a fixed self-authored constant today, not a leak — but the two call sites
duplicate the literal, and neither runs it through the egress gate at all.
The fix is registration (join the trusted self-authored constants) PLUS
actually gating both sites, so a future edit that interpolates user text
into this prompt is protected by the gate instead of silently shipping
ungated — stronger than a coverage-table CI check alone, and buildable now.

A single shared constant, `routes.chat.VISION_SCREEN_PROMPT`, replaces the
duplicated literals so the two sites cannot drift apart.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agent_friday.routes import chat
from agent_friday.services import egress_gate


def test_shared_constant_exists_and_is_registered_trusted():
    assert hasattr(chat, "VISION_SCREEN_PROMPT")
    prompt = chat.VISION_SCREEN_PROMPT
    assert "Briefly describe what is visible on this screen" in prompt
    gated = egress_gate._gate_text(prompt, "google-gemini", "vision.prompt")
    assert gated == prompt, (
        "the fixed vision prompt must be registered as a trusted "
        "self-authored constant so gating it is a no-op today"
    )


def test_gate_still_protects_future_interpolation(monkeypatch):
    """If a future edit interpolates user text into the vision prompt, the
    gate must not treat the whole altered string as the trusted constant —
    exact-match only, per register_trusted_text's own contract."""
    hypothetical_future_prompt = (
        chat.VISION_SCREEN_PROMPT
        + " The user also said: my SSN is 123-45-6789."  # pragma: allowlist secret
    )
    gated = egress_gate._gate_text(hypothetical_future_prompt, "google-gemini",
                                   "vision.prompt")
    assert "123-45-6789" not in gated  # pragma: allowlist secret
