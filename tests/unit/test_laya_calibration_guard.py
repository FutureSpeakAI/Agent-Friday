"""The calibration Friday's gate actually relies on is checked, and says so.

Upstream laya 0.3.5 clamps fitted temperatures to [0.5, 5.0], because the shipped
`choice:11+` bucket is 0.1006 -- it sharpens logits about tenfold and publishes a
coin flip as near-certainty. Verified on this machine: all four cached checkpoint
snapshots carry that value and 0.3.5 replaces it with 0.5, warning as it does.

Friday is not exposed to that bucket today. Its only Laya question is `severity`,
a two-option choice, so it lands in `choice:2` at 1.906 -- inside the range, and
softening rather than sharpening. And nothing gates on the confidence number:
temperature is monotonic, so it cannot move the argmax, and the union is
escalate-only regardless.

So this is a GUARD, not a fix. Two ways the comfortable position could end
quietly: a future checkpoint could ship a distorting temperature for the bucket
Friday does use, or Friday could grow a question with more options. Either way
the clamp would keep working and nobody would be told, because laya warns through
`warnings` and Friday never looked. Now the load captures that warning, the
bucket Friday depends on is checked against the range, and both are visible in
health and in the log rather than only in a RuntimeWarning nobody reads.
"""

import pytest

from agent_friday.services import laya_backend as lb


# ── which bucket Friday actually depends on ────────────────────────────────

def test_the_question_bucket_is_derived_not_guessed():
    """Read off SEVERITY_QUESTION, so growing the question changes the answer
    instead of silently invalidating a hardcoded string."""
    assert lb.question_bucket() == "choice:2"


def test_adding_options_moves_the_bucket(monkeypatch):
    """The failure mode this exists for: a question that grows past ten lands
    in the bucket whose shipped temperature is 0.1006."""
    big = {"severity": {"type": "choice",
                        "criteria": {"c%d" % i: "x" for i in range(12)}}}
    monkeypatch.setattr(lb, "SEVERITY_QUESTION", big)
    assert lb.question_bucket() == "choice:11+"


# ── the check itself ───────────────────────────────────────────────────────

def _cal(raw, applied=None):
    """A fake agent carrying the temperature attributes laya 0.3.5 exposes."""
    class _A:
        temperature_by_options_raw = dict(raw)
        temperature_by_options = dict(applied if applied is not None else raw)
    return _A()


def test_an_in_range_bucket_is_reported_ok():
    cal = lb.calibration_report(_cal({"choice:2": 1.906, "choice:11+": 0.1006},
                                     {"choice:2": 1.906, "choice:11+": 0.5}))
    assert cal["ok"] is True
    assert cal["bucket"] == "choice:2"
    assert cal["temperature"] == pytest.approx(1.906)
    assert cal["in_range"] is True


def test_other_buckets_being_clamped_is_reported_but_not_a_fault():
    """`choice:11+` is clamped on every shipped checkpoint. Friday does not use
    it, so it is information, not an alarm."""
    cal = lb.calibration_report(_cal({"choice:2": 1.906, "choice:11+": 0.1006},
                                     {"choice:2": 1.906, "choice:11+": 0.5}))
    assert cal["ok"] is True
    assert "choice:11+" in cal["clamped"], cal
    assert cal["clamped"]["choice:11+"]["shipped"] == pytest.approx(0.1006)
    assert cal["clamped"]["choice:11+"]["applied"] == pytest.approx(0.5)


def test_OUR_bucket_out_of_range_is_not_ok():
    """The case worth waking up for: the bucket the gate depends on is the one
    the checkpoint distorts."""
    cal = lb.calibration_report(_cal({"choice:2": 0.1006}, {"choice:2": 0.5}))
    assert cal["ok"] is False, cal
    assert cal["in_range"] is False
    assert cal["shipped"] == pytest.approx(0.1006)
    assert cal["temperature"] == pytest.approx(0.5)     # what is applied
    assert "uncalibrated" in cal["detail"].lower(), cal["detail"]


def test_a_bucket_the_checkpoint_never_mentions_is_ok():
    """No entry means the default temperature applies, which is not a fault."""
    cal = lb.calibration_report(_cal({"choice:11+": 0.1006}, {"choice:11+": 0.5}))
    assert cal["ok"] is True
    assert cal["temperature"] is None
    assert cal["in_range"] is True


def test_an_agent_without_the_attributes_never_raises():
    """An older or patched laya that does not expose them must degrade to
    'cannot tell', not break the gate."""
    class _Bare:
        pass
    cal = lb.calibration_report(_Bare())
    assert cal["ok"] is True
    assert cal["known"] is False


def test_no_agent_at_all_is_not_a_fault():
    cal = lb.calibration_report(None)
    assert cal["known"] is False
    assert cal["ok"] is True


# ── it is visible ──────────────────────────────────────────────────────────

def test_status_carries_the_calibration(monkeypatch):
    monkeypatch.setattr(lb, "_agent",
                        _cal({"choice:2": 1.906, "choice:11+": 0.1006},
                             {"choice:2": 1.906, "choice:11+": 0.5}))
    st = lb.status()
    assert "calibration" in st, sorted(st)
    assert st["calibration"]["bucket"] == "choice:2"
    assert st["calibration"]["ok"] is True


def test_status_flags_a_distorted_bucket(monkeypatch):
    monkeypatch.setattr(lb, "_agent", _cal({"choice:2": 0.1006}, {"choice:2": 0.5}))
    st = lb.status()
    assert st["calibration"]["ok"] is False
    assert st["calibration"]["in_range"] is False


def test_the_load_warning_is_captured_not_lost(monkeypatch):
    """laya warns through `warnings`, which goes nowhere a person will look.
    Capture it at the load so it can be shown."""
    import warnings

    def fake_load(*a, **kw):
        warnings.warn("laya: this checkpoint ships temperatures outside "
                      "[0.5, 5] which would distort confidence; clamping "
                      "choice:11+=0.1006.", RuntimeWarning)
        return _cal({"choice:2": 1.906}, {"choice:2": 1.906})

    lb.reset_calibration_warnings()
    captured = lb.capture_load_warnings(fake_load)
    assert captured is not None
    warns = lb.calibration_warnings()
    assert warns and "clamping" in warns[0], warns


def test_a_clean_load_captures_nothing(monkeypatch):
    lb.reset_calibration_warnings()
    lb.capture_load_warnings(lambda: _cal({"choice:2": 1.906}, {"choice:2": 1.906}))
    assert lb.calibration_warnings() == []


def test_a_load_that_raises_still_propagates():
    """The guard must not swallow a real load failure."""
    lb.reset_calibration_warnings()
    with pytest.raises(RuntimeError):
        lb.capture_load_warnings(lambda: (_ for _ in ()).throw(RuntimeError("boom")))


# ── visible in health ──────────────────────────────────────────────────────

def test_health_carries_the_calibration(monkeypatch):
    """The whole point of the guard is that a person and a boot check can see
    it, not that a RuntimeWarning went somewhere."""
    from agent_friday.routes import core_routes as cr
    monkeypatch.setattr(lb, "_agent",
                        _cal({"choice:2": 1.906, "choice:11+": 0.1006},
                             {"choice:2": 1.906, "choice:11+": 0.5}))
    c = cr._laya_calibration()
    assert c["bucket"] == "choice:2"
    assert c["ok"] is True and c["known"] is True
    assert c["clamped_buckets"] == ["choice:11+"]
    assert "within" in c["detail"]


def test_health_flags_a_distorted_bucket(monkeypatch):
    from agent_friday.routes import core_routes as cr
    monkeypatch.setattr(lb, "_agent", _cal({"choice:2": 0.1006}, {"choice:2": 0.5}))
    c = cr._laya_calibration()
    assert c["ok"] is False
    assert "UNCALIBRATED" in c["detail"]


def test_health_never_raises_when_laya_is_absent(monkeypatch):
    from agent_friday.routes import core_routes as cr
    monkeypatch.setattr(lb, "_agent", None)
    c = cr._laya_calibration()
    assert c["ok"] is True and c["known"] is False


def test_a_failure_in_the_health_helper_does_not_leak_exception_text(monkeypatch):
    """5.14.3 closed the class of leak where an exception's own words reach a
    browser. The health payload is served to one, so the fallback marks its text
    for the HTTP boundary to swap for an id."""
    from agent_friday.routes import core_routes as cr
    from agent_friday.user_errors import ExceptionText

    def boom(agent=None):
        raise RuntimeError("secret path C:/Users/someone/.friday/x")

    monkeypatch.setattr(lb, "calibration_report", boom)
    c = cr._laya_calibration()
    assert c["ok"] is True and c["known"] is False
    assert isinstance(c["detail"], ExceptionText), type(c["detail"])
