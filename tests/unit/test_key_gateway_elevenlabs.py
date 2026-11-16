"""ElevenLabs through the key gateway (third provider): header ``xi-api-key``, a key-shape check made inside the gateway.

Rules guarded here: the real key goes out as ``xi-api-key`` replacing the caller's; only ``/v1/`` passes; the tools and
cloud voice hold only the stand-in; a key with the wrong shape (a key *id*) is refused without the key leaving the
gateway; the row waits as long as the modules do.
"""
import pytest
import requests

from agent_friday.services import cloud_voice, elevenlabs_tools
from agent_friday.services import key_gateway as gw
from tests.unit.test_key_gateway import Upstream

REAL = "sk_real_key_for_tests"  # pragma: allowlist secret


@pytest.fixture
def upstream(monkeypatch):
    fake = Upstream()
    row = gw.PROVIDERS["elevenlabs"]
    monkeypatch.setitem(gw.PROVIDERS, "elevenlabs", gw.Provider(**{**row.__dict__, "upstream": fake.origin}))
    monkeypatch.setenv("ELEVENLABS_API_KEY", REAL)
    monkeypatch.setenv("NO_PROXY", "example.com")   # registered so the test's change to it is undone
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setattr(gw, "_from_core", lambda row: "")
    monkeypatch.setattr(gw, "_from_settings", lambda row: "")
    monkeypatch.setattr("agent_friday.services.cloud_intent.require_owner_engine", lambda *a, **k: None)
    gw.stop_all()
    yield fake
    gw.stop_all()
    fake.server.shutdown()


def test_the_real_key_goes_out_as_xi_api_key_replacing_the_callers(upstream):
    answer = requests.get(gw.url("elevenlabs") + "/v1/voices", headers={"XI-API-KEY": gw.stand_in("elevenlabs")},
                          timeout=10)
    assert answer.status_code == 200 and upstream.names[-1].count("xi-api-key") == 1
    assert upstream.header("xi-api-key") == REAL


def test_a_path_outside_v1_is_refused_and_never_sent(upstream):
    assert requests.get(gw.url("elevenlabs") + "/admin", timeout=10).status_code == 403 and upstream.seen == []


def test_the_tools_hold_only_the_stand_in_and_the_request_carries_the_real_key(upstream):
    assert elevenlabs_tools._key_stand_in() == gw.DUMMY_KEY
    response, error = elevenlabs_tools._request("GET", "/voices", key=elevenlabs_tools._key_stand_in())
    assert error is None or error == ""
    assert upstream.seen[-1][0] == "/v1/voices" and upstream.header("xi-api-key") == REAL


def test_cloud_voice_holds_only_the_stand_in(upstream):
    assert cloud_voice._api_key("elevenlabs") == gw.DUMMY_KEY
    assert cloud_voice.key_problem("elevenlabs", cloud_voice._api_key("elevenlabs")) is None


def test_a_key_id_with_the_wrong_shape_is_refused_by_the_gateway_not_forwarded(upstream, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "a1b2c3d4e5f60718")  # pragma: allowlist secret
    problem = cloud_voice.key_problem("elevenlabs", cloud_voice._api_key("elevenlabs"))
    assert problem and "key *id*" in problem and "a1b2c3d4e5f60718" not in problem


def test_the_rows_wait_covers_the_modules_longest_timeout():
    assert gw.PROVIDERS["elevenlabs"].timeout_s >= max(elevenlabs_tools._TIMEOUT, cloud_voice._TIMEOUT)


def test_a_key_found_only_in_plaintext_settings_is_moved_into_the_store(upstream, monkeypatch):
    from agent_friday.services import credential_store as store
    monkeypatch.delenv("ELEVENLABS_API_KEY")
    saved, written = {}, []
    monkeypatch.setattr(store, "get_provider_key", lambda name, **k: saved.get(name))
    monkeypatch.setattr(store, "set_provider_key", lambda name, key: saved.__setitem__(name, key))
    monkeypatch.setattr(gw, "_from_settings", lambda row: "sk_legacy_plain")
    monkeypatch.setattr("agent_friday.core._save_settings", lambda data: written.append(data))
    assert gw._find_key(gw.PROVIDERS["elevenlabs"]) == "sk_legacy_plain"
    assert saved == {"elevenlabs": "sk_legacy_plain"} and written == [{"elevenlabs_api_key": ""}]
