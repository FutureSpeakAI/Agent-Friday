"""Tests for services/decisions.py — the seam in front of Friday's judgments.

The property that matters most here is a NEGATIVE one: adding this seam must
not have changed a single gate verdict. A module that quietly altered approval
behaviour while describing itself as instrumentation would be the worst
possible version of it, so the first test below re-derives every verdict the
old way and asserts the new path agrees.

The second thing being tested is that the record exists and is honest — it is
the entire reason the module was written, since the corpus behind Friday's
most consequential classifier turned out to be one record.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import approvals, decisions, dissent_gate


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(decisions, "log_path", lambda: tmp_path / "decisions.jsonl")
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(dissent_gate, "EVENTS_PATH", tmp_path / "dissent.jsonl")
    monkeypatch.delenv("FRIDAY_DECISION_BACKEND", raising=False)
    yield


def _rows(tmp_path):
    p = tmp_path / "decisions.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


# ═══════════════════════════════════════════════════════════════════════════
#  THE VERDICT DID NOT MOVE
# ═══════════════════════════════════════════════════════════════════════════

ACTIONS = [
    # hard, one per marker family
    "Send an email to the whole team",
    "Post this update publicly",
    "Permanently delete the backups folder",
    "Buy the annual subscription",
    "Transfer funds to the contractor",
    "Wipe the old drafts directory",
    # soft
    "Summarise this week's notes",
    "Read the calendar for tomorrow",
    # the two the old docstring calls out as the reason the drafting-prefix
    # exception had to exist at all: hard MARKERS inside a purely analytical ask
    "Analyze our spend trends for Q2",
    "Research typical order fulfillment times for our industry",
    # and the inverse it warns about: a drafting word mid-sentence must NOT
    # downgrade an irreversible act
    "Wipe the old drafts directory now",
]


@pytest.mark.parametrize("action", ACTIONS)
def test_seam_returns_exactly_what_the_old_path_returned(action):
    """Re-derive the verdict the pre-seam way and demand agreement.

    This is the whole safety claim of the change, so it is checked against a
    recomputation rather than against a frozen expected list - a hardcoded
    list would keep passing if BOTH paths drifted together.
    """
    severity = dissent_gate.classify_severity(action)
    expected = ("internal" if severity == "soft"
                else approvals._label_hard_class(action))
    assert decisions.decide("policy_class", action).answer == expected
    assert approvals.classify(action)["policy_class"] == expected


@pytest.mark.parametrize("action", ACTIONS)
def test_action_severity_matches_the_underlying_heuristic(action):
    """The other question the keyword backend answers.

    Added because a falsification pass caught that nothing asserted this
    branch's ANSWER - a test called it, so it had coverage, but replacing its
    return value with a constant lie kept the whole file green. Coverage is
    not evidence; only an assertion that could have failed is.
    """
    assert (decisions.decide("action_severity", action).answer
            == dissent_gate.classify_severity(action))


def test_an_unknown_question_is_refused_rather_than_guessed():
    """The keyword backend answers two questions and must not invent a third.

    decide() catches the KeyError and falls back - to the same backend, which
    raises again - so the caller gets the error rather than a made-up class.
    """
    with pytest.raises(KeyError):
        decisions.decide("is_this_a_good_idea", "Send an email")


def test_explicit_action_class_still_bypasses_classification():
    """A caller that names the class never reaches the scorer, as before."""
    out = approvals.classify("anything at all", action_class="internal")
    assert out["policy_class"] == "internal"


def test_gated_flag_still_follows_the_policy_table():
    assert approvals.classify("Send an email to the team")["gated"] is True
    assert approvals.classify("Summarise my notes")["gated"] is False


# ═══════════════════════════════════════════════════════════════════════════
#  THE RECORD
# ═══════════════════════════════════════════════════════════════════════════

def test_every_gate_decision_is_written_down(tmp_path):
    approvals.classify("Send an email to the whole team")
    rows = _rows(tmp_path)
    assert len(rows) == 1
    r = rows[0]
    assert r["question"] == "policy_class"
    assert r["state"] == "Send an email to the whole team"
    assert r["answer"] == "external_message"
    assert r["method"] == "keyword"
    assert r["context"]["caller"] == "approvals.classify"
    assert isinstance(r["elapsed_ms"], (int, float))


def test_keyword_backend_reports_no_confidence(tmp_path):
    """None, not 1.0.

    A substring scan has no probability. Writing 1.0 would read as certainty
    to every consumer and would poison any later calibration analysis with a
    column of fake perfect scores.
    """
    v = decisions.decide("policy_class", "Send an email")
    assert v.confidence is None
    assert _rows(tmp_path)[0]["confidence"] is None


def test_long_states_are_clipped_and_say_so(tmp_path):
    decisions.decide("policy_class", "Send an email. " + "x" * 5000)
    r = _rows(tmp_path)[0]
    assert r["state_truncated"] is True
    assert len(r["state"]) == decisions.MAX_LOGGED_STATE


def test_short_states_are_not_marked_truncated(tmp_path):
    decisions.decide("policy_class", "Send an email")
    assert _rows(tmp_path)[0]["state_truncated"] is False


def test_a_broken_log_never_breaks_a_verdict(tmp_path, monkeypatch):
    """Bookkeeping that can break the thing it observes is worse than none.

    The first version of this test patched `_record` itself to raise, which
    proved nothing: `_record` owns the try/except that makes this property
    true, so replacing the whole function removes the guard being tested and
    then reports its absence as a failure. Fail the WRITE instead, which is
    the thing that actually goes wrong on a full disk or a locked file.
    """
    real_open = open

    def _locked(path, *a, **k):
        if str(path).endswith("decisions.jsonl"):
            raise OSError("disk full")
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", _locked)
    v = decisions.decide("policy_class", "Send an email")
    monkeypatch.undo()
    assert v.answer == "external_message"
    assert _rows(tmp_path) == []          # nothing written, and nothing raised


# ═══════════════════════════════════════════════════════════════════════════
#  BACKENDS, AND FAILING TO THE INCUMBENT
# ═══════════════════════════════════════════════════════════════════════════

def test_default_backend_is_keyword_and_stays_that_way():
    assert decisions.DEFAULT_BACKEND == "keyword"
    assert decisions.active_backend() == "keyword"


def test_unknown_backend_name_falls_back_loudly_not_fatally(monkeypatch):
    """A typo in a setting must not take the approval gate offline."""
    monkeypatch.setenv("FRIDAY_DECISION_BACKEND", "laya-typo")
    assert decisions.active_backend() == "keyword"
    assert approvals.classify("Send an email to the team")["gated"] is True


def test_a_registered_backend_is_actually_used(tmp_path, monkeypatch):
    """Proves the swap point works - otherwise every test here is vacuous,
    because a seam that can only ever run one backend is not a seam."""
    monkeypatch.setitem(decisions._BACKENDS, "stub",
                        lambda q, s, **kw: ("spend", 0.77, {"why": "stub"}))
    monkeypatch.setenv("FRIDAY_DECISION_BACKEND", "stub")
    v = decisions.decide("policy_class", "Summarise my notes")
    assert (v.answer, v.method, v.confidence) == ("spend", "stub", 0.77)
    assert _rows(tmp_path)[0]["detail"] == {"why": "stub"}


def test_a_failing_backend_falls_back_to_keyword_and_records_it(tmp_path, monkeypatch):
    """An unanswerable question is not a reason to let an action through."""
    def _explode(q, s, **kw):
        raise RuntimeError("model not loaded")
    monkeypatch.setitem(decisions._BACKENDS, "broken", _explode)
    monkeypatch.setenv("FRIDAY_DECISION_BACKEND", "broken")
    v = decisions.decide("policy_class", "Send an email to the team")
    assert v.answer == "external_message"
    assert v.method == "keyword"
    r = _rows(tmp_path)[0]
    assert "model not loaded" in r["fell_back_from"]
    assert r["method"] == "keyword"


def test_a_failing_backend_does_not_open_the_gate(monkeypatch):
    def _explode(q, s, **kw):
        raise RuntimeError("down")
    monkeypatch.setitem(decisions._BACKENDS, "broken", _explode)
    monkeypatch.setenv("FRIDAY_DECISION_BACKEND", "broken")
    assert approvals.classify("Send an email to the team")["gated"] is True


def test_seam_unavailable_fails_closed(monkeypatch):
    """If the seam cannot be reached at all, classify still GATES.

    Note what had to change to make this test real. Blocking the import alone
    does nothing, because `decisions` is already in sys.modules by the time
    any test runs - the import inside classify() finds the cached module and
    succeeds. So the module has to be evicted as well as blocked, which is
    also the only way the failure happens for real: a module that never
    imported in the first place.

    The action chosen is one that classifies SOFT on the normal path, so a
    pass cannot be an accident of the input already being gated.
    """
    import sys as _sys
    import builtins
    monkeypatch.delitem(_sys.modules, "agent_friday.services.decisions",
                        raising=False)
    real = builtins.__import__

    def _no_decisions(name, globals=None, locals=None, fromlist=(), level=0):
        # `from agent_friday.services import decisions` calls __import__ with
        # name="agent_friday.services" and fromlist=("decisions",) - matching
        # on `name` alone never fires, which is why the first version of this
        # test passed the module straight through and reported a false pass.
        if "decisions" in (fromlist or ()) or name.endswith(".decisions"):
            raise ImportError("gone")
        return real(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _no_decisions)
    out = approvals.classify("Summarise my notes")   # SOFT on the normal path
    monkeypatch.undo()
    assert out["policy_class"] == "outward"
    assert out["gated"] is True


def test_that_action_really_is_soft_normally():
    """Guards the test above: if 'Summarise my notes' ever became hard, the
    fail-closed test would pass for the wrong reason and nobody would know."""
    assert approvals.classify("Summarise my notes")["policy_class"] == "internal"


# ═══════════════════════════════════════════════════════════════════════════
#  READING IT BACK
# ═══════════════════════════════════════════════════════════════════════════

def test_summary_counts_what_actually_happened(tmp_path):
    approvals.classify("Send an email to the team")
    approvals.classify("Summarise my notes")
    approvals.classify("Post this publicly")
    s = decisions.summary()
    assert s["total"] == 3
    q = s["by_question"]["policy_class"]
    assert q["n"] == 3
    assert q["answers"]["internal"] == 1
    assert q["methods"]["keyword"] == 3
    assert q["fallbacks"] == 0


def test_history_is_newest_first_and_filterable(tmp_path):
    decisions.decide("policy_class", "first one, send an email")
    decisions.decide("action_severity", "second one, summarise notes")
    assert decisions.history(limit=1)[0]["question"] == "action_severity"
    assert len(decisions.history(question="policy_class")) == 1


def test_history_survives_a_corrupt_line(tmp_path):
    decisions.decide("policy_class", "Send an email")
    with open(tmp_path / "decisions.jsonl", "a", encoding="utf-8") as fh:
        fh.write("{ this is not json\n")
    decisions.decide("policy_class", "Post this publicly")
    assert len(decisions.history()) == 2
