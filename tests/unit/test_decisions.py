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
#  THE RECORD DOES NOT BECOME A SECOND COPY OF THE USER'S MAIL
# ═══════════════════════════════════════════════════════════════════════════
#
# gmail_send.request_send builds its action_description from From/To/Cc/Subject
# and the FULL body, and approvals.classify hands exactly that string to
# decide(). Written verbatim, every message the user sends would leave its
# recipients and its first ~1,900 characters in an audit log. The governance
# log covering the same decision scrubs; so must this one.

#: Placeholder addresses and a placeholder secret, not the real ones. The
#: pre-commit secret scanner blocks personal PII and key-shaped strings even
#: inside tests, which is the correct call: a fixture is still a file in the
#: repo, and "it's only a test" is how real values get committed.
_SENDER = "owner@example.com"
_FAKE_SECRET = "ZmFrZXRva2VuZm9ydGVzdGluZzEyMzQ1"

_CARD = (
    "Send mail as you.\n\nFrom: %s\n"
    "To: hiring@example.com, second@example.org\nCc: —\n"
    "Subject: Following up on the Director role\n\n"
    "Hi - following up on our conversation. My number is on the CV. "
    "Token: %s\n" % (_SENDER, _FAKE_SECRET)
)


def test_addresses_do_not_reach_the_log(tmp_path):
    approvals.classify(_CARD)
    blob = (tmp_path / "decisions.jsonl").read_text(encoding="utf-8")
    for addr in (_SENDER, "hiring@example.com", "second@example.org"):
        assert addr not in blob, "raw recipient %r written to the log" % addr
    assert "<email>" in blob, "scrubbed, but the redaction is not visible"


def test_secrets_do_not_reach_the_log(tmp_path):
    approvals.classify(_CARD)
    blob = (tmp_path / "decisions.jsonl").read_text(encoding="utf-8")
    assert _FAKE_SECRET not in blob
    assert "<token>" in blob


def test_the_backend_still_judges_the_UNSCRUBBED_text(tmp_path):
    """The failure mode this fix could easily have introduced.

    Scrubbing before the backend runs would change verdicts - a gate quietly
    classifying redacted text is exactly the "instrumentation that altered
    behaviour" the module docstring forbids. Assert the backend saw the real
    string, and that the answer is unchanged.
    """
    seen = {}

    def spy(question, state, **kw):
        seen["state"] = state
        return "external_message", None, {}

    decisions.register_backend("spy", spy)
    try:
        decisions.decide("policy_class", _CARD, backend="spy")
    finally:
        decisions._BACKENDS.pop("spy", None)

    assert _SENDER in seen["state"], (
        "the backend was handed scrubbed text; redaction must be a LOGGING "
        "step, never a judging one")
    # Assert the ENFORCEMENT decision, not the label. A real gmail_send card
    # classifies as the generic "outward" bucket rather than
    # "external_message" - _label_hard_class matches on verbs like "email"
    # and "send a message", and this card opens "Send mail as you". That is
    # cosmetic by design (approvals.py: "Purely cosmetic... still gated; only
    # the label is approximate") and gmail_send passes force_gate=True on top,
    # so the card is gated either way. What must never move is `gated`.
    assert approvals.classify(_CARD)["gated"] is True


def test_a_record_can_still_be_tied_back_to_its_source(tmp_path):
    """Redaction must not make the corpus unjoinable.

    The full text's durable home is the approval itself. The digest is how a
    later re-scoring pass matches a log row to that record without the log
    keeping its own copy of the body.
    """
    import hashlib
    approvals.classify(_CARD)
    r = _rows(tmp_path)[0]
    assert r["state_sha256"] == hashlib.sha256(
        _CARD.encode("utf-8")).hexdigest()[:16]


def test_scrubbing_happens_before_clipping(tmp_path, monkeypatch):
    """Order matters on the day someone raises the cap.

    Clip-then-scrub leaves anything past the boundary intact in the record.
    """
    monkeypatch.setattr(decisions, "MAX_LOGGED_STATE", 100000)
    decisions.decide("policy_class", "x" * 2500 + " reply to boss@corp.com")
    blob = (tmp_path / "decisions.jsonl").read_text(encoding="utf-8")
    assert "boss@corp.com" not in blob


# ═══════════════════════════════════════════════════════════════════════════
#  THE RECORD
# ═══════════════════════════════════════════════════════════════════════════

def test_every_gate_decision_is_written_down(tmp_path, monkeypatch):
    # Pinned to `keyword` explicitly. What this proves is that the LOG records
    # a decision faithfully, which has nothing to do with which backend ships
    # as the default (now `laya-union`); a test that read the ambient setting
    # would assert the wrong method whenever the default moves.
    monkeypatch.setenv("FRIDAY_DECISION_BACKEND", "keyword")
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
    """Filler is PROSE, deliberately.

    Not "x" * 5000: an unbroken 5,000-character run of alphanumerics is
    exactly what _TOKEN_RE (24+ chars) exists to catch, so the state would
    collapse to "Send an email. <token>" (22 chars) and correctly not be
    truncated - measuring the scrubber, not the clip. The collapse behaviour
    is pinned separately below.
    """
    decisions.decide("policy_class", "Send an email. " + "lorem ipsum " * 500)
    r = _rows(tmp_path)[0]
    assert r["state_truncated"] is True
    assert len(r["state"]) == decisions.MAX_LOGGED_STATE


def test_a_long_unbroken_blob_is_collapsed_rather_than_stored(tmp_path):
    """The behaviour the test above tripped over, asserted on purpose.

    A base64 payload, a hash, or a minified blob carries no severity signal
    and is precisely the shape of a leaked secret, so collapsing it is the
    wanted outcome - but it should be a stated property, not a surprise.
    """
    decisions.decide("policy_class", "Send an email. " + "A1b2C3d4" * 400)
    r = _rows(tmp_path)[0]
    assert "<token>" in r["state"]
    assert len(r["state"]) < 100


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

def test_the_fallback_backend_is_keyword_and_stays_that_way():
    """The FALLBACK, which is a different thing from the shipped default.

    `active_backend()` does not return keyword by default: the shipped default
    is `laya-union` (core.DEFAULT_SETTINGS), a product decision made with the
    eval in hand, not a regression.

    What must NOT move is this: `DEFAULT_BACKEND` is the value every failure
    path lands on. `decide()` falls back to it when a backend raises,
    `active_backend()` returns it when a setting names something unregistered,
    and `union_backend` answers from it when Laya is absent or still loading.
    A substring scan that always works is the floor under all three. If this
    ever becomes a model, an unloadable checkpoint takes the approval gate
    with it.
    """
    assert decisions.DEFAULT_BACKEND == "keyword"
    assert decisions._BACKENDS[decisions.DEFAULT_BACKEND] is decisions._keyword_backend


def test_the_shipped_default_is_the_union_and_is_selectable(monkeypatch):
    """The other half of the old assertion, restated as what is true now."""
    from agent_friday import core
    assert core.DEFAULT_SETTINGS["decision_backend"] == "laya-union"

    from agent_friday.services import laya_backend
    laya_backend.register()
    monkeypatch.setattr("agent_friday.core._load_settings",
                        lambda: dict(core.DEFAULT_SETTINGS), raising=False)
    assert decisions.active_backend() == "laya-union"


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
        # on `name` alone never fires, passes the module straight through and
        # reports a false pass.
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

def test_summary_counts_what_actually_happened(tmp_path, monkeypatch):
    # Pinned for the same reason as test_every_gate_decision_is_written_down:
    # this is about the summary's arithmetic, not about which scanner ships.
    monkeypatch.setenv("FRIDAY_DECISION_BACKEND", "keyword")
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
