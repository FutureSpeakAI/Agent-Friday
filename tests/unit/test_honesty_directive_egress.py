"""Unit tests for WO-1 item 1 (2026-08-25): the anti-fabrication directive
must survive cloud egress gating intact.

BUG: `REFUSAL_HONESTY_DIRECTIVE` was ONE paragraph — its 7 items joined by a
single "\n", never "\n\n" — and item 4's worked example said "...asked for
its phone number", a literal TIER_2 strong phrase
(sensitivity_classifier._TIER2_STRONG). `egress_gate._gate_text_span` picks
ONE separator for the WHOLE text it is gating; the real system prompt DOES
contain "\n\n" elsewhere, so "\n\n" was chosen, and because the directive
itself had no "\n\n" of its own, the entire un-split directive rode inside
one paragraph-sized chunk and was withheld as a unit. The instruction that
exists to stop Friday from inventing tool results was itself being redacted
from every cloud call.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import agent_friday.services.model_router as mr
from agent_friday.services import egress_gate


def test_directive_survives_in_the_real_assembled_system_prompt():
    """Build the real system prompt (hermetic — no vault/wiki data available
    under the test's isolated home) and gate it for a cloud provider exactly
    as a live call would. The directive must come through unchanged."""
    prompt = mr._get_friday_system_prompt()
    gated = egress_gate._gate_text(prompt, "anthropic", "system")
    assert mr.REFUSAL_HONESTY_DIRECTIVE in gated, (
        "the anti-fabrication directive did not survive cloud egress gating "
        "verbatim — Friday's instruction not to invent results is itself "
        "being withheld"
    )
    # Spot-check the item that carried the original trigger phrase.
    assert "lookup, not a question" in gated


def test_directive_registered_as_trusted_text():
    """WO-1 asked for the directive to be `register_trusted_text`-exempt, the
    same mechanism FRIDAY_SYSTEM_PROMPT uses — belt-and-suspenders alongside
    the paragraph split. Confirm the registration actually happened."""
    assert mr.REFUSAL_HONESTY_DIRECTIVE.strip() in egress_gate._TRUSTED_TEXTS


def test_self_knowledge_registered_as_trusted_at_load(monkeypatch):
    """WO-1 item 2: SELF.md is loaded and registered gate-exempt the same way
    as the directive — same rationale (Friday-authored, no user data), same
    mechanism. Force a known SELF.md body through `_load_self_knowledge` and
    confirm it lands in the trusted registry once `_get_friday_system_prompt`
    has assembled a prompt from it."""
    body = "Friday runs on a local seat when one is available. (test fixture)"
    monkeypatch.setattr(mr, "_load_self_knowledge", lambda: body)
    prompt = mr._get_friday_system_prompt()
    assert body in prompt
    assert body in egress_gate._TRUSTED_TEXTS


def test_no_paragraph_of_the_directive_carries_a_tier2_or_tier3_phrase():
    """The paragraph split only localizes damage; it does not prevent it. The
    item that originally tripped the classifier must actually be reworded,
    not just isolated — confirm every item, gated alone, comes back PUBLIC
    (unchanged) rather than redacted."""
    for i, para in enumerate(mr.REFUSAL_HONESTY_DIRECTIVE.split("\n\n"), start=1):
        gated = egress_gate._gate_text_span(para, "anthropic", f"honesty-item-{i}")
        assert gated == para, (
            f"directive item {i} classified as sensitive on its own: {para!r}"
        )


# ── Falsifiability ──────────────────────────────────────────────────────────
def test_the_original_single_paragraph_form_would_have_been_withheld():
    """Reconstruct the ORIGINAL bug's exact shape — items joined by a single
    "\n" (not "\n\n"), item 4's example restored to mention a phone number —
    and confirm THAT version does not survive `_gate_text` unchanged embedded
    in a realistic prompt. This is the contrast that proves the fix above is
    real: the same content, joined differently, is a materially different
    outcome for the gate — so the tests above are not vacuously passing
    regardless of what the directive says.
    """
    old_form = mr.REFUSAL_HONESTY_DIRECTIVE.replace("\n\n", "\n").replace(
        "He told you a restaurant's name and asked whether they take "
        "walk-ins — that is a lookup, not a question.",
        "He told you the clinic's name and address and asked for its phone "
        "number — that is a lookup, not a question.",
    )
    assert "\n\n" not in old_form                       # really is one paragraph
    assert old_form.strip() not in egress_gate._TRUSTED_TEXTS   # never registered
    assert "phone number" in old_form                   # really has the trigger phrase

    # Assembled the way _get_friday_system_prompt actually joins it in: "\n\n"
    # exists elsewhere in the prompt, but only a single "\n" surrounds this
    # block — the exact condition that merged it into one paragraph.
    prompt = ("Some preceding system text.\n\n== HONEST LIMITS ==\n"
             + old_form + "\n\n== NEXT SECTION ==\nmore text")
    gated = egress_gate._gate_text(prompt, "anthropic", "system")
    assert old_form not in gated, (
        "the original single-paragraph form (with the phone-number example) "
        "should NOT survive gating verbatim; it did, so this reconstruction "
        "does not actually reproduce the original bug, and the tests above "
        "are not distinguishing fixed from broken"
    )
