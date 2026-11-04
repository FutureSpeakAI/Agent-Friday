"""Every approval card shows its facts and an Inspect button, in both UI files
(NS-18.4-3, slice A14).

index.html is the precompiled app and ui_parts/app.html its JSX twin; both
must agree. ApprovalCardBody renders every card: the generic body puts the
facts block (ApprovalCardFacts) above the decide buttons, and every kind with
a body of its own is wrapped so the block follows it. The block shows undo,
cost, privacy, model and checks, offers Inspect (the card's /inspect, read
only) and Edit disabled with why, and sets every value as text.
"""
from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
FILES = ("index.html", "ui_parts/app.html")

#: The kinds that have a card body of their own in ApprovalCardBody.
OWN_BODIES = ("FileGrantRequestCard", "SkillImportCard", "CloudReleaseCard", "LocalContextShareCard",
              "window.FridayWorkspaceSwapCard", "window.FridayPublishCard", "window.FridayTrialCard")


def _text(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _fn(text, name):
    """The source of one top-level function, up to the next top-level one."""
    m = re.search(r"^function %s\(" % re.escape(name), text, flags=re.M)
    assert m, name
    nxt = re.search(r"^function \w+\(", text[m.end():], flags=re.M)
    return text[m.start(): m.end() + (nxt.start() if nxt else len(text))]


@pytest.mark.parametrize("rel", FILES)
def test_the_facts_block_shows_every_fact_with_inspect_and_edit(rel):
    facts = _fn(_text(rel), "ApprovalCardFacts")
    for testid in ("approval-card-facts", "approval-card-inspect", "approval-card-edit",
                   "approval-card-inspect-text"):
        assert testid in facts, (rel, testid)
    for label in ("'Undo'", "'Cost'", "'Privacy'", "'Model'", "'Checks'", "'Targets'", "'Details'",
                  "'Expires'", "'What'", "'Why'"):
        assert label in facts, (rel, label)
    for key in ("reversibility", "cost", "privacy_impact", "model", "verification_plan", "targets",
                "provenance", "expires_at"):
        assert "'%s'" % key in facts or "." + key in facts, (rel, key)
    assert "/inspect" in facts and "a.card_facts" in facts
    # Edit is shown disabled with its reason (no generic edit path exists)
    assert re.search(r"disabled[:=]\s*\{?true", facts), rel
    assert "not known" in facts
    # text only: never markup from a fact or an explanation
    assert "dangerouslySetInnerHTML" not in facts and "innerHTML" not in facts


@pytest.mark.parametrize("rel", FILES)
def test_every_card_body_ends_with_the_facts_block(rel):
    body = _fn(_text(rel), "ApprovalCardBody")
    # the generic card: the block sits before the decide buttons (the last div,
    # which a read-only place hides)
    generic = body[body.index("data-testid"):]
    assert "ApprovalCardFacts" in generic, rel
    assert generic.index("ApprovalCardFacts") < generic.index("onDecide(a.approval_id, 'approve')"), rel
    # every kind with its own body is wrapped
    lines = body.splitlines()
    for comp in OWN_BODIES:
        assert any(comp in ln and "return withFacts(" in ln for ln in lines), (rel, comp)
        assert not any(comp in ln and re.search(r"return /\*#__PURE__\*/|return <", ln) for ln in lines), (rel, comp)
    assert body.count("return withFacts(") == len(OWN_BODIES), rel


def test_both_ui_files_agree_on_the_facts_block():
    a, b = (_fn(_text(rel), "ApprovalCardFacts") for rel in FILES)
    for needle in ("approval-card-facts", "approval-card-inspect", "approval-card-edit",
                   "'/api/approvals/' + encodeURIComponent(a.approval_id) + '/inspect'",
                   "This card could not be inspected right now.",
                   "Nothing is recorded for this card yet."):
        assert (needle in a) and (needle in b), needle
