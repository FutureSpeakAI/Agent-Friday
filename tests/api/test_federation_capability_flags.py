"""Per-capability switches and a kill switch for the Federation / Economy routes
(spec section 36 capabilities; roadmap section 40, slice F2).

Before this, one switch (settings.held_features.federation) opened every held route at once:
test-credit economy, marketplace listings, a peer's compute job. Now each capability has its
own switch `fed_<name>` (off when absent), `fed_kill_switch` stops all of it at once, and
`public_discovery` / `live_cash` cannot be turned on in this release. Settings are read on
every request, so a flip takes effect without a restart. Offline; the real route gate, with
the settings reader replaced by the dict each test names.
"""
import pytest

import agent_friday.core as core
from agent_friday.services import held_features as hf

ECONOMY = ("get", "/api/economy/leaderboard")
LISTINGS = ("get", "/api/marketplace/listings")
COMPUTE = ("get", "/api/federation/capabilities")          # not under /compute/: federation only
COMPUTE_JOB = ("get", "/api/federation/compute/status/none")
INBOX_ONLY_FEDERATION = ("get", "/api/federation/peers")
OPEN_PATHS = (("get", "/api/federation/identity"), ("get", "/.well-known/friday-agent.json"))


@pytest.fixture
def client():
    import agent_friday.server as s
    s.app.config["TESTING"] = True
    with s.app.test_client() as c:
        yield c


def _settings(monkeypatch, held):
    monkeypatch.setattr(core, "_load_settings", lambda: {"held_features": dict(held)})


def _answers(client, call):
    method, path = call
    r = getattr(client, method)(path)
    return r.status_code, (r.get_json(silent=True) or {})


def _held(client, call, feature):
    status, body = _answers(client, call)
    return status == 404 and body.get("error") == "not_enabled" and body.get("feature") == feature


def _need(name):
    fn = getattr(hf, name, None)
    assert fn is not None, f"services/held_features.{name} does not exist: no per-capability switches"
    return fn


def test_the_six_capabilities_are_named_and_two_are_never_on():
    assert set(getattr(hf, "CAPABILITIES", ())) == {
        "offline_rehearsal", "invited_jobs", "unattended_worker", "private_publish",
        "public_discovery", "live_cash"}
    assert set(getattr(hf, "NEVER_IN_THIS_RELEASE", ())) == {"public_discovery", "live_cash"}


def test_federation_alone_opens_no_capability(monkeypatch):
    cap = _need("capability_enabled")
    s = {"held_features": {"federation": True}}
    assert not any(cap(c, s) for c in hf.CAPABILITIES)


def test_a_capability_needs_its_own_switch_and_the_federation_switch(monkeypatch):
    cap = _need("capability_enabled")
    assert cap("offline_rehearsal", {"held_features": {"federation": True,
                                                       "fed_offline_rehearsal": True}}) is True
    assert cap("offline_rehearsal", {"held_features": {"fed_offline_rehearsal": True}}) is False
    assert cap("offline_rehearsal", {"held_features": {"federation": True,
                                                       "fed_offline_rehearsal": "yes"}}) is False
    assert cap("offline_rehearsal", {"held_features": {"federation": True,
                                                       "fed_offline_rehearsal": 1}}) is False


def test_the_kill_switch_beats_every_capability():
    cap = _need("capability_enabled")
    on = {"federation": True, "fed_kill_switch": True}
    on.update({"fed_" + c: True for c in ("offline_rehearsal", "invited_jobs",
                                         "unattended_worker", "private_publish")})
    assert not any(cap(c, {"held_features": on}) for c in hf.CAPABILITIES)
    assert hf.kill_switch_engaged({"held_features": on}) is True
    assert hf.kill_switch_engaged({"held_features": {"fed_kill_switch": "yes"}}) is False


@pytest.mark.parametrize("never", ["public_discovery", "live_cash"])
def test_public_discovery_and_live_cash_cannot_be_turned_on(never):
    cap = _need("capability_enabled")
    s = {"held_features": {"federation": True, "fed_" + never: True}}
    assert cap(never, s) is False


def test_an_unknown_capability_or_unreadable_settings_is_off(monkeypatch):
    cap = _need("capability_enabled")
    assert cap("something_else", {"held_features": {"federation": True,
                                                    "fed_something_else": True}}) is False

    def boom():
        raise RuntimeError("settings unreadable")
    monkeypatch.setattr(core, "_load_settings", boom)
    assert cap("offline_rehearsal") is False


def test_federation_on_but_no_capability_holds_the_capability_routes(client, monkeypatch):
    _need("capability_enabled")
    _settings(monkeypatch, {"federation": True})
    assert _held(client, ECONOMY, "fed_offline_rehearsal")
    assert _held(client, LISTINGS, "fed_private_publish")
    assert _held(client, COMPUTE_JOB, "fed_unattended_worker")


def test_each_switch_opens_only_its_own_routes(client, monkeypatch):
    _need("capability_enabled")
    _settings(monkeypatch, {"federation": True, "fed_offline_rehearsal": True})
    assert _answers(client, ECONOMY)[0] == 200
    assert _held(client, LISTINGS, "fed_private_publish")
    assert _held(client, COMPUTE_JOB, "fed_unattended_worker")
    _settings(monkeypatch, {"federation": True, "fed_private_publish": True})
    assert _held(client, ECONOMY, "fed_offline_rehearsal")
    assert _answers(client, LISTINGS)[0] == 200


def test_the_kill_switch_stops_every_held_route_at_once_and_leaves_the_open_ones(client, monkeypatch):
    _need("capability_enabled")
    everything = {"federation": True, "fed_offline_rehearsal": True, "fed_private_publish": True,
                  "fed_unattended_worker": True, "fed_invited_jobs": True}
    _settings(monkeypatch, everything)
    assert _answers(client, ECONOMY)[0] == 200
    _settings(monkeypatch, dict(everything, fed_kill_switch=True))
    for call in (ECONOMY, LISTINGS, COMPUTE_JOB, INBOX_ONLY_FEDERATION):
        assert _held(client, call, "fed_kill_switch"), call
    for call in OPEN_PATHS:
        assert _answers(client, call)[0] != 404, f"{call} is meant to stay open"


def test_the_switch_is_read_on_every_request(client, monkeypatch):
    _need("capability_enabled")
    state = {"held_features": {"federation": True, "fed_offline_rehearsal": True}}
    monkeypatch.setattr(core, "_load_settings", lambda: state)
    assert _answers(client, ECONOMY)[0] == 200
    state["held_features"]["fed_kill_switch"] = True
    assert _held(client, ECONOMY, "fed_kill_switch")
    del state["held_features"]["fed_kill_switch"]
    assert _answers(client, ECONOMY)[0] == 200


def test_the_federation_switch_off_still_answers_as_before(client, monkeypatch):
    _settings(monkeypatch, {"federation": False, "fed_offline_rehearsal": True})
    assert _held(client, ECONOMY, "federation")


def test_purchase_stays_refused_with_every_switch_on(client, monkeypatch):
    _settings(monkeypatch, {"federation": True, "fed_private_publish": True,
                            "fed_offline_rehearsal": True, "fed_live_cash": True})
    r = client.post("/api/marketplace/purchase", json={"listing_id": "x"})
    assert r.status_code != 200 and not (r.get_json(silent=True) or {}).get("ok")
