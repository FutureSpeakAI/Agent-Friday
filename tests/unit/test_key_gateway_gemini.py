"""Gemini through the key gateway: header ``x-goog-api-key``, one client factory for the media modules.

Rules guarded here: the real key goes out as ``x-goog-api-key`` replacing the caller's; only Gemini's own API paths
(``/v1beta/``, ``/v1/``, ``/upload/``) pass; the image, video and music modules and the media tools build their client
through ``services/gemini_client`` and hold only the stand-in; the three modules are off the raw-key allow-list.

Not verified here, because it needs a real key and spends money: Veo's long-running operations and the resumable file
upload against Google's own servers. Those paths are allowed through, and the SDK is pointed at the loopback listener
with the same ``base_url`` it would use for the public host.
"""
import json
import pathlib

import pytest
import requests

from agent_friday.services import key_gateway as gw
from tests.unit.test_key_gateway import Upstream

REAL = "AIzaSyREALKEYFORTESTS000000000000000"  # pragma: allowlist secret


@pytest.fixture
def upstream(monkeypatch):
    fake = Upstream()
    row = gw.PROVIDERS["gemini"]
    monkeypatch.setitem(gw.PROVIDERS, "gemini", gw.Provider(**{**row.__dict__, "upstream": fake.origin}))
    monkeypatch.setenv("GEMINI_API_KEY", REAL)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("NO_PROXY", "example.com")   # registered so the test's change to it is undone
    monkeypatch.setenv("no_proxy", "example.com")
    monkeypatch.setattr(gw, "_from_core", lambda row: "")
    monkeypatch.setattr(gw, "_from_settings", lambda row: "")
    monkeypatch.setattr("agent_friday.services.cloud_intent.require", lambda *a, **k: None)
    gw.stop_all()
    yield fake
    gw.stop_all()
    fake.server.shutdown()


def test_the_real_key_goes_out_as_x_goog_api_key_replacing_the_callers(upstream):
    answer = requests.post(gw.url("gemini") + "/v1beta/models/gemini-2.5-flash:generateContent",
                           headers={"X-Goog-Api-Key": gw.stand_in("gemini")}, json={}, timeout=10)
    assert answer.status_code == 200 and upstream.names[-1].count("x-goog-api-key") == 1
    assert upstream.header("x-goog-api-key") == REAL


@pytest.mark.parametrize("path", ["/v1beta/models/m:generateContent", "/v1beta/models/m:predictLongRunning",
                                  "/v1beta/operations/abc", "/v1beta/files/f:download?alt=media",
                                  "/upload/v1beta/files", "/v1/models"])
def test_geminis_own_paths_pass(upstream, path):
    assert requests.post(gw.url("gemini") + path, json={}, timeout=10).status_code == 200


@pytest.mark.parametrize("path", ["/admin", "/v2/models", "/oauth2/token", "/"])
def test_a_path_outside_geminis_own_is_refused_and_never_sent(upstream, path):
    assert requests.get(gw.url("gemini") + path, timeout=10).status_code == 403 and upstream.seen == []


def test_the_file_delete_verb_is_relayed_not_refused(upstream):
    # the fake upstream answers 501 to a verb it does not implement; the gateway's own refusal would be 403 or 405
    assert requests.delete(gw.url("gemini") + "/v1beta/files/f", timeout=10).status_code not in (403, 405)
    assert requests.put(gw.url("gemini") + "/v1beta/files/f", timeout=10).status_code in (403, 405)


def test_the_sdk_client_holds_only_the_stand_in_and_talks_to_the_loopback(upstream):
    from agent_friday.services import gemini_client
    client = gemini_client.client()
    assert client._api_client.api_key == gw.DUMMY_KEY and REAL not in repr(client._api_client._http_options)
    assert client._api_client._http_options.base_url.startswith("http://127.0.0.1:")


def test_a_real_sdk_call_reaches_the_upstream_with_the_real_key(upstream):
    from agent_friday.services import gemini_client
    client = gemini_client.client()              # kept: the SDK closes its HTTP client when the Client is collected
    try:
        client.models.generate_content(model="gemini-2.5-flash", contents="hi")
    except Exception:
        pass                                     # the fake upstream's body is not a generateContent answer; the request is the point
    path, _ = upstream.seen[-1]
    assert path.split("?")[0] in ("/v1beta/models/gemini-2.5-flash:generateContent",      # the SDK version decides which
                                  "/v1/models/gemini-2.5-flash:generateContent")
    assert upstream.header("x-goog-api-key") == REAL


def test_with_no_key_there_is_no_client_and_no_availability(monkeypatch):
    from agent_friday.services import gemini_client
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr(gw, "_from_core", lambda row: "")
    monkeypatch.setattr(gw, "_from_settings", lambda row: "")
    monkeypatch.setattr("agent_friday.services.credential_store.get_provider_key", lambda *a, **k: None)
    assert gemini_client.available() is False
    with pytest.raises(gemini_client.NoKey):
        gemini_client.client()


def test_the_media_modules_build_their_client_through_the_factory(upstream):
    from agent_friday.services import creative_engine, media_tools, music_engine
    for built in (creative_engine._client(), media_tools._gemini_client(), music_engine._gemini_client()):
        assert built._api_client.api_key == gw.DUMMY_KEY
        assert built._api_client._http_options.base_url.startswith("http://127.0.0.1:")


def test_the_modules_availability_checks_ask_the_gateway_not_the_raw_constant(upstream, monkeypatch):
    from agent_friday import core
    from agent_friday.services import creative_engine, media_tools, music_engine
    monkeypatch.setattr(core, "GEMINI_API_KEY", "", raising=False)       # the old way would say "no key"
    assert creative_engine.is_available() is True
    assert music_engine.is_available() is True
    monkeypatch.delenv("GEMINI_API_KEY")
    monkeypatch.setattr("agent_friday.services.credential_store.get_provider_key", lambda *a, **k: None)
    assert creative_engine.is_available() is False and music_engine.is_available() is False


def test_the_rows_wait_covers_a_long_generation():
    assert gw.PROVIDERS["gemini"].timeout_s >= 300


def test_the_three_modules_are_off_the_raw_key_allow_list():
    path = pathlib.Path(gw.__file__).resolve().parents[3] / "scripts" / "raw_key_readers.allow.json"
    listed = json.loads(path.read_text(encoding="utf-8"))
    names = {p.rsplit("/", 1)[-1] for p in (listed if isinstance(listed, dict) else {x[0]: 1 for x in listed})}
    assert not names & {"creative_engine.py", "music_engine.py", "media_tools.py"}
