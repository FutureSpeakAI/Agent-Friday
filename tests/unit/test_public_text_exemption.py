"""Third-party published news is not Stephen's private material.

The defect: 9 of 120 public headlines classified TIER_3 on the legal and
financial keyword rules — "Trump asks US Supreme Court to allow ballroom work
to continue", "Point2 Technology … raised a $136M Series B". One tainted
paragraph made the whole weekly story block sensitive, the gate withheld it,
and Friday received a folder of redaction notices and honestly refused to write
an editorial from them.

Those rules exist to keep Stephen's legal and financial affairs on the machine.
A headline the BBC published is neither.

This exemption loosens a gate, so these tests are mostly about what it does
NOT do.
"""
from __future__ import annotations

import pytest

from agent_friday.services import egress_gate as eg


@pytest.fixture(autouse=True)
def clean_registry():
    with eg._TRUSTED_LOCK:
        before = set(eg._PUBLIC_PARAS)
        eg._PUBLIC_PARAS.clear()
    yield
    with eg._TRUSTED_LOCK:
        eg._PUBLIC_PARAS.clear()
        eg._PUBLIC_PARAS.update(before)


HEADLINE = "- Trump asks US Supreme Court to allow ballroom work to continue (bbc.co.uk)"
PRIVATE = "My deposition in the custody case is scheduled for March 3rd."


def _gate(text):
    return eg._gate_text(text, "anthropic", "system")


# ── it works ─────────────────────────────────────────────────────────────────

def test_an_unregistered_headline_is_still_withheld():
    """The baseline. Nothing is exempt until provenance says so."""
    out = _gate(HEADLINE + "\n\n" + "Something ordinary and public.")
    assert "EGRESS-GATE" in out


def test_a_registered_headline_survives():
    eg.register_public_text(HEADLINE, origin="bbc.co.uk")
    out = _gate(HEADLINE + "\n\n" + "Something ordinary and public.")
    assert "EGRESS-GATE" not in out
    assert "Supreme Court" in out


# ── what it deliberately does not do ─────────────────────────────────────────

def test_registering_a_headline_does_not_exempt_the_users_own_material():
    """The exemption is per-string, not a mode. Private text in the same
    payload gates exactly as before."""
    eg.register_public_text(HEADLINE, origin="bbc.co.uk")
    out = _gate(HEADLINE + "\n\n" + PRIVATE)
    assert "Supreme Court" in out          # the headline goes
    assert "custody case" not in out       # his material does not
    assert "EGRESS-GATE" in out


def test_it_cannot_be_claimed_at_send_time():
    """There is no API that accepts "treat this as news" from a caller.

    The exemption is established at INGEST by the component that fetched the
    text. If a send-time switch existed, anything could wear a press badge.
    """
    import inspect
    sig = inspect.signature(eg.seal_outbound) if hasattr(eg, "seal_outbound") \
        else None
    if sig:
        names = set(sig.parameters)
        assert not (names & {"public", "is_news", "trusted", "exempt",
                             "public_text", "skip_gate"}), \
            "a send-time exemption parameter would defeat the provenance rule"


def test_interpolating_user_content_produces_a_different_string():
    """Exact match only. A headline with something of his spliced into it is
    not the registered string and gates normally."""
    eg.register_public_text(HEADLINE, origin="bbc.co.uk")
    tampered = HEADLINE.replace("ballroom work",
                                "my deposition in the custody case")
    out = _gate(tampered + "\n\nOrdinary public sentence.")
    assert "EGRESS-GATE" in out


def test_a_document_sized_blob_is_refused_registration():
    """A headline or a summary, not an article body — and certainly not a
    file someone pasted in."""
    eg.register_public_text("x" * 5000, origin="somewhere")
    assert eg.public_text_count() == 0


def test_the_registry_is_bounded():
    assert eg._PUBLIC_MAX <= 50000


def test_empty_and_non_string_input_is_ignored():
    for bad in (None, "", "   ", 42, [], {}):
        eg.register_public_text(bad, origin="x")
    assert eg.public_text_count() == 0


# ── it is a separate registry from the self-authored one ─────────────────────

def test_public_text_does_not_join_the_self_authored_registry():
    """Those contracts differ — self-authored means "contains no user data by
    construction", which third-party news does not claim. Merging them would
    weaken a statement that is relied on elsewhere."""
    eg.register_public_text(HEADLINE, origin="bbc.co.uk")
    with eg._TRUSTED_LOCK:
        assert HEADLINE not in eg._TRUSTED_TEXTS
        assert HEADLINE not in eg._TRUSTED_PARAS


def test_the_classifier_itself_is_untouched():
    """The exemption lives at the GATE. Tier rules are unchanged, so anything
    that consults the classifier directly still sees the same answer."""
    from agent_friday.services import sensitivity_classifier as sc
    eg.register_public_text(HEADLINE, origin="bbc.co.uk")
    assert sc.classify(HEADLINE) >= 2, \
        "registering must not rewrite what the classifier believes"
