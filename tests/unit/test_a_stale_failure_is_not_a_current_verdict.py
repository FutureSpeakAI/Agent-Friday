"""A recorded failure expires; it does not become permanent truth.

Without an expiry, Google Drive keeps reporting "not activated" after the user
has activated it, and corrects itself only once something happens to call Drive.

`google_accounts.note_service_result` records a provider-side refusal in
`~/.friday/google_accounts/service_state.json` and clears it only on a
SUCCESSFUL call. The record is deliberate -- enabling an API is a console act,
so the condition is sticky rather than transient -- but nothing re-checked it,
so the verdict outlived the fix: the record clears the moment a Drive call
finally succeeds, not when the API is switched on.

`service_health` then reports DEGRADED / "switched off at Google" as a CURRENT
reading, and every surface downstream repeats it, including the note handed to
the model. A verdict that can only be revised by the very call it discourages is
a verdict that cannot self-correct.

So a provider-off record is a reading with an age. Past its TTL it stops being
presented as current: the service reads as usable-but-unverified and carries what
was last seen, and the next real call re-establishes the truth through
`note_service_result` exactly as before.

The trade is deliberate and asymmetric. If the service really is still off, the
cost is one failed call whose error re-records the condition. If it is on --
which is the case the moment the user fixes it -- the cost of the old behaviour
was a capability disabled indefinitely for no reason.
"""

import time

import pytest

from agent_friday.services import connector_health as ch
from agent_friday.services import google_accounts as ga


def _iso(ago_s):
    """An ISO timestamp `ago_s` seconds in the past, in the module's own format."""
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(seconds=ago_s)).isoformat()


@pytest.fixture
def one_healthy_drive_account(monkeypatch):
    """A connected account with Drive switched on, and nothing else wrong."""
    monkeypatch.setattr(ga, "_load_index", lambda *a, **k: {
        "accounts": [{"id": "acct-1", "label": "Primary",
                      "services": {"drive": True}}]})
    monkeypatch.setattr(
        ch, "from_google_account",
        lambda rec: ch.Health(state=ch.WORKING, source="test",
                              summary="connected"))


@pytest.fixture
def verdict(monkeypatch):
    """Put one provider-off record in the store, with an age."""
    def _set(age_s, blocked=True, at=None):
        rec = {"blocked": blocked,
               "detail": "Google Drive API has not been used in project 1 before "
                         "or it is disabled"}
        if at is not None:
            rec["at"] = at
        elif age_s is not None:
            rec["at"] = _iso(age_s)
        monkeypatch.setattr(ga, "_load_service_state",
                            lambda *a, **k: {"drive": rec})
    return _set


def test_a_fresh_provider_off_verdict_is_still_reported(
        one_healthy_drive_account, verdict):
    """The behaviour worth keeping: a refusal seen moments ago is current, and
    the user is told plainly that Drive is off at Google."""
    verdict(age_s=5)
    h = ga.service_health("drive")

    assert h.state == ch.DEGRADED
    assert h.action == "enable_api"
    assert "switched off" in h.summary


def test_a_verdict_past_its_ttl_is_not_presented_as_current(
        one_healthy_drive_account, verdict):
    """The bug. Long after the refusal, this must stop claiming Drive is off --
    the user may have fixed it minutes ago, and nothing here would know."""
    verdict(age_s=ga._PROVIDER_OFF_TTL_S + 60)
    h = ga.service_health("drive")

    assert h.state != ch.DEGRADED, (
        "a %ss-old refusal is still being reported as the current state"
        % (ga._PROVIDER_OFF_TTL_S + 60))
    assert h.healthy, "the service should be usable so the next call can re-check"
    assert h.verified is False, (
        "usable, but nothing has proven it -- that must not be hidden")


def test_an_expired_verdict_still_says_what_was_seen(
        one_healthy_drive_account, verdict):
    """Expiring the verdict must not erase it: the user is owed the history,
    or a service that silently fails every few minutes looks like nothing."""
    verdict(age_s=ga._PROVIDER_OFF_TTL_S + 60)
    h = ga.service_health("drive")

    low = (h.summary + " " + h.detail).lower()
    assert "drive" in low
    assert "re-check" in low or "not been checked" in low or "unconfirmed" in low, (
        "an expired verdict should say it has not been re-checked: %r" % h.summary)


def test_an_undated_verdict_is_treated_as_unconfirmed(
        one_healthy_drive_account, verdict):
    """Records written before this change have no `at`. An unknown age cannot
    be shown as a current reading -- that is the whole claim being fixed."""
    verdict(age_s=None, at=None)
    h = ga.service_health("drive")

    assert h.state != ch.DEGRADED
    assert h.verified is False


def test_a_cleared_verdict_leaves_health_alone(
        one_healthy_drive_account, verdict):
    """`blocked: false` is the normal state of the live file and must read as
    an ordinary healthy service, not as an expired failure."""
    verdict(age_s=5, blocked=False)
    h = ga.service_health("drive")

    assert h.state == ch.WORKING
    assert h.verified is True


def test_a_success_still_clears_the_record(monkeypatch):
    """The existing self-correction must keep working: the next successful call
    is still what turns the verdict off for good."""
    saved = {}
    monkeypatch.setattr(ga, "_load_service_state",
                        lambda *a, **k: {"drive": {"blocked": True,
                                                   "detail": "is disabled",
                                                   "at": _iso(5)}})
    monkeypatch.setattr(ga, "_save_service_state",
                        lambda state: saved.update(state))

    ga.note_service_result("drive", True)

    assert saved.get("drive", {}).get("blocked") is False


def test_an_unrecognised_error_is_never_recorded_as_provider_off(monkeypatch):
    """"I could not check" is not "it is off" -- pinned because the TTL makes
    over-recording cheaper to miss."""
    saved = {}
    monkeypatch.setattr(ga, "_load_service_state", lambda *a, **k: {})
    monkeypatch.setattr(ga, "_save_service_state",
                        lambda state: saved.update(state))

    ga.note_service_result("drive", False, "connection reset by peer")

    assert saved == {}
