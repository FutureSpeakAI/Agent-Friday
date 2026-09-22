"""The one property that lets Laya near the approval gate at all.

THE CLAIM UNDER TEST

    Enabling Laya can never REMOVE an approval card that the keyword scan
    would have raised.

That is what makes `laya-union` shippable while the model itself is unproven.
It is a structural property, not a statistical one: `keyword`'s verdict is one
of the two inputs to the OR, so the worst case of a bad fine-tune, a corrupted
download or a model that simply answers badly is MORE approval cards, never
fewer. It mirrors `judgment_gate`, which may rescue a blocked span and may
never authorise one.

WHY THESE TESTS DRIVE `approvals.classify`, NOT `union_backend`

The primitive is easy to test and proves the wrong thing. What Stephen
experiences is a card, and a card is `classify(...)["gated"]` — the end of a
chain that runs union_backend -> decide() -> the policy table, any link of
which could drop an escalation while `union_backend` itself stayed correct.
So the assertions below compare GATED-NESS through the real seam, with the
real policy table, and only the model is faked.

HOW THESE CAN FAIL, which is the point

Each adversary is a Laya that is wrong in a different direction — one that
says `soft` to everything (the corrupt-checkpoint case), one that inverts the
incumbent, one that returns a value outside the label set, one that raises,
and one that never loaded. A union that trusted the model anywhere would drop
a card under at least one of them.

Verified by mutation rather than asserted. Adding a de-escalating branch to
`union_backend`'s policy_class arm — `if severity == "soft": return
"internal"` — fails `test_no_input_loses_a_card_under_any_adversary` under
two of the five adversaries, losing 14 cards under `inverts_the_incumbent`
including "Pay the AWS invoice with the card on file" and the literal
gmail_send card shape.

AND THE FIRST MUTATION THIS FILE COULD NOT SEE, kept because it is the more
useful half. The obvious probe — make the `action_severity` arm return Laya's
answer directly — PASSED against these tests. Not because they are weak, but
because `approvals.classify` asks `policy_class`; `decide("action_severity")`
has no production caller at all today, only test callers. So the arm that
reads like the heart of the union is, at present, dead weight at the gate.
`test_the_severity_arm_holds_the_property_too` covers it anyway, since it is
registered and a future call site would reach it — but it is labelled here so
that nobody reads a green suite as evidence that arm is load-bearing.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import (approvals, decisions, dissent_gate,
                                   laya_backend)


# ═══════════════════════════════════════════════════════════════════════════
#  THE CORPUS — the same adversarial set the eval harness scores
# ═══════════════════════════════════════════════════════════════════════════
#
# Imported from tools/severity_eval.py rather than copied. A second copy of
# the set would drift from the one the numbers were measured on, and then this
# file would be pinning a property about a corpus nobody evaluates.

def _cases():
    import pathlib
    import sys
    root = pathlib.Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "tools"))
    try:
        from severity_eval import CASES  # type: ignore
    finally:
        sys.path.pop(0)
    return [c[0] for c in CASES]


CASE_TEXTS = _cases()


# ═══════════════════════════════════════════════════════════════════════════
#  FAKES
# ═══════════════════════════════════════════════════════════════════════════

class _FakeAgent:
    """Stands in for a loaded Laya at the `predict` boundary.

    Faked one level lower than `_answer` on purpose, so `_answer`'s own
    parsing — the choice/confidence extraction and the label-set check — is
    still the code under test rather than something the fake skipped past.
    """

    def __init__(self, decide):
        self._decide = decide

    def predict(self, text, question):
        choice = self._decide(text)
        return {"answers": {"severity": {
            "choice": choice, "confidence": 0.9,
            "probabilities": {"hard": 0.9, "soft": 0.1}}}}


class _ExplodingAgent:
    def predict(self, text, question):
        raise RuntimeError("checkpoint is corrupt")


def _keyword_says(text):
    return dissent_gate.classify_severity(text)


#: Five ways for the model to be wrong, and one way for it to be absent.
#: `always_soft` is the case that matters most — it is what a corrupted or
#: badly fine-tuned checkpoint looks like from outside, and it is precisely
#: the input on which a union that trusted the model would open the gate.
ADVERSARIES = {
    "always_soft": lambda t: "soft",
    "always_hard": lambda t: "hard",
    "inverts_the_incumbent": lambda t: (
        "soft" if _keyword_says(t) == "hard" else "hard"),
    "outside_the_label_set": lambda t: "probably?",
    "empty_answer": lambda t: None,
}


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(decisions, "log_path",
                        lambda: tmp_path / "decisions.jsonl")
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(dissent_gate, "EVENTS_PATH", tmp_path / "dissent.jsonl")
    monkeypatch.delenv("FRIDAY_DECISION_BACKEND", raising=False)
    monkeypatch.delenv("FRIDAY_DECISION_SHADOW", raising=False)
    # Settings must not be able to reach in and change which backend a test
    # is exercising. `active_backend` consults them when the env is unset.
    monkeypatch.setattr("agent_friday.core._load_settings", lambda: {},
                        raising=False)
    laya_backend.register()
    monkeypatch.setattr(laya_backend, "_agent", None)
    yield
    laya_backend._agent = None


def _gated(text, *, backend, monkeypatch):
    monkeypatch.setenv("FRIDAY_DECISION_BACKEND", backend)
    return approvals.classify(text)["gated"]


# ═══════════════════════════════════════════════════════════════════════════
#  THE SAFETY PROPERTY
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("adversary", sorted(ADVERSARIES))
def test_no_input_loses_a_card_under_any_adversary(adversary, monkeypatch):
    """gated(keyword) implies gated(union), for every case and every fake.

    This is the whole argument for the union mode, stated as the thing that
    would have to break for it to be unsafe.
    """
    lost = []
    for text in CASE_TEXTS:
        monkeypatch.setattr(laya_backend, "_agent", None)
        want = _gated(text, backend="keyword", monkeypatch=monkeypatch)

        monkeypatch.setattr(laya_backend, "_agent",
                            _FakeAgent(ADVERSARIES[adversary]))
        got = _gated(text, backend="laya-union", monkeypatch=monkeypatch)

        if want and not got:
            lost.append(text)

    assert not lost, (
        "enabling laya-union REMOVED an approval card under adversary %r:\n  %s"
        % (adversary, "\n  ".join(lost)))


def test_a_model_that_raises_leaves_the_gate_exactly_as_it_was(monkeypatch):
    """A broken checkpoint must degrade to today's behaviour, not to an open
    gate — and not merely to "still gated sometimes"."""
    for text in CASE_TEXTS:
        monkeypatch.setattr(laya_backend, "_agent", None)
        want = _gated(text, backend="keyword", monkeypatch=monkeypatch)

        monkeypatch.setattr(laya_backend, "_agent", _ExplodingAgent())
        got = _gated(text, backend="laya-union", monkeypatch=monkeypatch)

        assert got == want, "a raising model changed the verdict for %r" % text


def test_a_model_that_never_loaded_leaves_the_gate_exactly_as_it_was(monkeypatch):
    """The warm-up window. Decisions arriving in the first ~45 s must be
    answered by the incumbent, identically, rather than waiting or failing."""
    for text in CASE_TEXTS:
        want = _gated(text, backend="keyword", monkeypatch=monkeypatch)
        got = _gated(text, backend="laya-union", monkeypatch=monkeypatch)
        assert got == want, "an unloaded model changed the verdict for %r" % text
    assert not laya_backend.is_ready()


@pytest.mark.parametrize("adversary", sorted(ADVERSARIES))
def test_the_severity_arm_holds_the_property_too(adversary, monkeypatch):
    """The same property on `action_severity`, which nothing calls yet.

    Covered because the question is registered and cheap to get wrong later,
    not because it guards anything today — see the module docstring. Asserted
    on the raw answer rather than on gated-ness, since there is no policy
    table between this question and a caller.
    """
    monkeypatch.setenv("FRIDAY_DECISION_BACKEND", "laya-union")
    for text in CASE_TEXTS:
        monkeypatch.setattr(laya_backend, "_agent", None)
        if decisions.decide("action_severity", text,
                            backend="keyword").answer != "hard":
            continue
        monkeypatch.setattr(laya_backend, "_agent",
                            _FakeAgent(ADVERSARIES[adversary]))
        assert decisions.decide("action_severity", text).answer == "hard", (
            "union de-escalated %r under adversary %r" % (text, adversary))


# ═══════════════════════════════════════════════════════════════════════════
#  THE TEST ABOVE MUST BE ABLE TO SEE A CHANGE AT ALL
# ═══════════════════════════════════════════════════════════════════════════
#
# A property of the form "X never gets smaller" passes trivially against a
# function that ignores its inputs. These two pin that the seam is live in
# both directions, so the safety tests are watching something that moves.

def test_the_union_does_add_cards_when_laya_escalates(monkeypatch):
    """Direction check: a model that always says `hard` must gate everything.

    If this fails, the safety tests above are passing because nothing reaches
    the model, not because the union is sound.
    """
    monkeypatch.setattr(laya_backend, "_agent",
                        _FakeAgent(ADVERSARIES["always_hard"]))
    ungated = [t for t in CASE_TEXTS
               if not _gated(t, backend="laya-union", monkeypatch=monkeypatch)]
    assert not ungated, "laya said hard and these still produced no card: %s" % ungated


def test_the_incumbent_leaves_real_cases_ungated(monkeypatch):
    """And the baseline is not already "everything is gated", which would make
    the implication vacuously true for every adversary."""
    ungated = [t for t in CASE_TEXTS
               if not _gated(t, backend="keyword", monkeypatch=monkeypatch)]
    assert len(ungated) >= 5, (
        "expected the keyword scan to leave several cases ungated; got %d"
        % len(ungated))


# ═══════════════════════════════════════════════════════════════════════════
#  SHADOW MODE CHANGES NOTHING
# ═══════════════════════════════════════════════════════════════════════════

def test_shadow_mode_does_not_move_a_single_verdict(monkeypatch):
    """The point of shadow mode: Laya scores every decision and governs none.

    Run with a model that disagrees with the incumbent on everything, which is
    the strongest version of the test — if a shadow could leak into a verdict,
    this adversary would move all of them.
    """
    monkeypatch.setattr(laya_backend, "_agent",
                        _FakeAgent(ADVERSARIES["inverts_the_incumbent"]))
    for text in CASE_TEXTS:
        monkeypatch.delenv("FRIDAY_DECISION_SHADOW", raising=False)
        want = _gated(text, backend="keyword", monkeypatch=monkeypatch)

        monkeypatch.setenv("FRIDAY_DECISION_SHADOW", "laya")
        got = _gated(text, backend="keyword", monkeypatch=monkeypatch)

        assert got == want, "shadow mode changed the verdict for %r" % text


def test_shadow_rows_are_marked_and_never_mistaken_for_decisions(tmp_path,
                                                                 monkeypatch):
    """A shadow row that read like a decision would poison every later
    calibration pass, which is the one job the log exists to support."""
    monkeypatch.setattr(laya_backend, "_agent",
                        _FakeAgent(ADVERSARIES["always_hard"]))
    monkeypatch.setenv("FRIDAY_DECISION_SHADOW", "laya")
    monkeypatch.setenv("FRIDAY_DECISION_BACKEND", "keyword")

    approvals.classify("Summarise my notes from today")
    _drain()

    rows = _rows(tmp_path)
    shadows = [r for r in rows if r.get("shadow")]
    real = [r for r in rows if not r.get("shadow")]

    assert len(real) == 1, "expected exactly one governing decision, got %d" % len(real)
    assert real[0]["method"] == "keyword"
    assert shadows, "shadow mode recorded nothing"
    for r in shadows:
        assert r["method"] == "laya"
        assert r["decided_by"] == "keyword"
        assert "agreed" in r
        assert r["context"]["shadow_of"] == "keyword"


def test_a_shadow_that_explodes_is_invisible_to_the_caller(tmp_path, monkeypatch):
    """Fire-and-forget means exactly that: no raise, no delay, no row."""
    monkeypatch.setattr(laya_backend, "_agent", _ExplodingAgent())
    monkeypatch.setenv("FRIDAY_DECISION_SHADOW", "laya")
    monkeypatch.setenv("FRIDAY_DECISION_BACKEND", "keyword")

    out = approvals.classify("Send an email to the whole team about the outage")
    _drain()

    assert out["gated"] is True
    assert not [r for r in _rows(tmp_path) if r.get("shadow")]


# ═══════════════════════════════════════════════════════════════════════════

def _drain(timeout=5.0):
    """Wait for the shadow threads, which are daemons and deliberately
    unawaited by the code under test."""
    import threading
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        live = [t for t in threading.enumerate()
                if t.name == "decision-shadow" and t.is_alive()]
        if not live:
            return
        time.sleep(0.02)


def _rows(tmp_path):
    p = tmp_path / "decisions.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in
            p.read_text(encoding="utf-8").splitlines() if l.strip()]
