"""The public phone ingress: only Twilio's paths, only signed, fail closed."""
import json
import urllib.error
import urllib.request

import pytest

from agent_friday.phone import config, guard, ingress, signature, spool
from tests.unit.phone_fakes import (FAKE_ACCOUNT, FAKE_TOKEN, OWNER, PUBLIC, STRANGER,
                                    fake_twilio, phone_home, quiet)  # noqa: F401


@pytest.fixture
def client(phone_home, monkeypatch):
    monkeypatch.setattr(ingress, "_GUARD", guard.IngressGuard())
    return ingress.create_app().test_client()


def sms_params(**over):
    p = {"AccountSid": FAKE_ACCOUNT, "MessageSid": "SM" + "9" * 32, "From": STRANGER,
         "To": "+15125550100", "Body": "hello", "NumMedia": "0", "NumSegments": "1",
         "FromCity": "SOMEWHERE", "FromZip": "00000"}
    p.update(over)
    return p


def post(client, path, params, *, token=FAKE_TOKEN, url=None, headers=None):
    url = url or (PUBLIC + path)
    h = {"X-Twilio-Signature": signature.compute(token, url, params)}
    h.update(headers or {})
    return client.post(path, data=params, headers=h)


def test_a_signed_text_is_spooled_with_only_the_fields_friday_uses(client):
    r = post(client, "/twilio/sms", sms_params())
    assert r.status_code == 200 and b"<Response>" in r.data
    [ev] = spool.pending_events()
    assert ev["kind"] == "sms_in" and ev["data"]["Body"] == "hello"
    assert "FromCity" not in ev["data"] and "FromZip" not in ev["data"]


def test_an_unsigned_or_wrongly_signed_request_is_refused_and_does_nothing(client):
    assert client.post("/twilio/sms", data=sms_params()).status_code == 403
    assert post(client, "/twilio/sms", sms_params(), token="not-the-token").status_code == 403  # pragma: allowlist secret
    assert spool.pending_events() == []


def test_the_url_is_the_configured_public_one_never_the_request_host(client):
    # Signed for the URL the request actually hit (the tunnel's origin side):
    # a request cannot choose the URL it is checked against.
    r = post(client, "/twilio/sms", sms_params(), url="http://localhost/twilio/sms")
    assert r.status_code == 403


def test_a_signed_request_for_another_account_is_refused(client):
    r = post(client, "/twilio/sms", sms_params(AccountSid="AC" + "f" * 32))
    assert r.status_code == 403 and spool.pending_events() == []


def test_a_replay_is_answered_but_has_no_effect(client):
    p = sms_params()
    assert post(client, "/twilio/sms", p).status_code == 200
    spool.mark_event(spool.pending_events()[0]["id"], "done")
    assert post(client, "/twilio/sms", p).status_code == 200
    assert spool.pending_events() == []


def test_switched_off_or_missing_token_or_address_means_503(client, monkeypatch):
    config.update({"enabled": False})
    assert post(client, "/twilio/sms", sms_params()).status_code == 503
    config.update({"enabled": True, "public_base_url": ""})
    assert post(client, "/twilio/sms", sms_params()).status_code == 503
    config.update({"public_base_url": PUBLIC})
    monkeypatch.setattr(config, "get_secret", lambda name: None)
    assert post(client, "/twilio/sms", sms_params()).status_code == 503
    assert spool.pending_events() == []


def test_a_broken_replay_store_fails_closed(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk gone")
    monkeypatch.setattr(spool, "seen_before", boom)
    assert post(client, "/twilio/sms", sms_params()).status_code == 503


def test_nothing_but_the_twilio_paths_exists(client):
    for path in ("/", "/api/health", "/api/settings", "/api/phone/status", "/login",
                 "/api/approvals", "/twilio", "/twilio/sms/../../api/health"):
        assert client.get(path).status_code in (404, 405), path
        assert client.post(path, data={}).status_code in (404, 405), path
    assert client.get("/twilio/sms").status_code == 405


def test_oversized_bodies_are_refused_before_parsing(client):
    r = client.post("/twilio/sms", data={"Body": "x" * (ingress.MAX_BODY + 10)})
    assert r.status_code == 413


def test_bad_signatures_run_out_of_budget_fast(client):
    codes = [client.post("/twilio/sms", data=sms_params()).status_code for _ in range(12)]
    assert codes[0] == 403 and 429 in codes


def test_the_global_rate_limit_answers_429(client, monkeypatch):
    monkeypatch.setattr(ingress._GUARD, "admit", lambda key, now=None: False)
    assert post(client, "/twilio/sms", sms_params()).status_code == 429


def test_a_call_gets_the_greeting_and_voicemail_on_the_public_urls(client):
    config.update({"greeting": "Hi <there> & welcome"})
    p = {"AccountSid": FAKE_ACCOUNT, "CallSid": "CA" + "3" * 32, "From": STRANGER,
         "To": "+15125550100", "CallStatus": "ringing"}
    r = post(client, "/twilio/voice", p)
    body = r.data.decode()
    assert r.status_code == 200
    assert "Hi &lt;there&gt; &amp; welcome" in body
    assert 'action="%s/twilio/voice-done"' % PUBLIC in body
    assert 'recordingStatusCallback="%s/twilio/recording"' % PUBLIC in body
    assert "<Connect>" not in body                      # no live call unless enabled + owner
    assert [e["kind"] for e in spool.pending_events()] == ["call_in"]


def test_voicemail_off_declines_politely(client):
    config.update({"voicemail": False})
    p = {"AccountSid": FAKE_ACCOUNT, "CallSid": "CA" + "4" * 32, "From": STRANGER}
    body = post(client, "/twilio/voice", p).data.decode()
    assert "<Record" not in body and "AI assistant" in body


def test_only_a_completed_recording_is_spooled(client):
    base = {"AccountSid": FAKE_ACCOUNT, "CallSid": "CA" + "5" * 32,
            "RecordingSid": "RE" + "6" * 32, "RecordingDuration": "7"}
    assert post(client, "/twilio/recording", dict(base, RecordingStatus="in-progress")).status_code == 204
    assert spool.pending_events() == []
    assert post(client, "/twilio/recording", dict(base, RecordingStatus="completed")).status_code == 204
    assert [e["kind"] for e in spool.pending_events()] == ["recording"]


def test_the_real_server_binds_loopback_and_serves_no_friday_route(phone_home):
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    info = ingress.start(port)
    try:
        assert info["host"] == "127.0.0.1"
        for path in ("/api/health", "/"):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d%s" % (info["port"], path), timeout=5)
                raise AssertionError("expected an error status for " + path)
            except urllib.error.HTTPError as e:
                assert e.code == 404
    finally:
        ingress.stop()
    assert ingress.running() == {"running": False}
