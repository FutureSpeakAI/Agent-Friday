"""A provider descriptor through the key gateway (the chat leg's shape): one listener pinned to the descriptor's own
host and path, the key placed on the descriptor's own auth header.

Rules guarded here: the real key replaces whatever the caller sent, on the header the descriptor names (default
``Authorization: Bearer``; a custom header and an empty scheme work); only the descriptor's own path passes; a local,
private, plain-http or key-less descriptor gets no listener (it is called directly); two descriptors never share a
listener; the key is read at call time.
"""
import pytest
import requests

from agent_friday.services import key_gateway as gw
from tests.unit.test_key_gateway import Upstream

REAL = "sk-chat-real-key-for-tests"  # pragma: allowlist secret


@pytest.fixture
def upstream(monkeypatch):
    fake = Upstream()
    monkeypatch.setenv("TEST_CHAT_KEY", REAL)
    monkeypatch.setenv("NO_PROXY", "example.com")   # registered so the test's change to it is undone
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setattr(gw, "_is_cloud_endpoint", lambda base_url: True)
    gw.stop_all()
    yield fake
    gw.stop_all()
    fake.server.shutdown()


def descriptor(fake, **changes):
    return {"name": "chatco", "base_url": fake.origin + "/v1", "auth": {"type": "env_var", "key": "TEST_CHAT_KEY"},
            **changes}


def call(url, path="/chat/completions", **headers):
    return requests.post(url + path, json={"model": "m"}, headers=headers, timeout=10)


def test_the_real_key_goes_out_as_a_bearer_token_replacing_the_callers(upstream):
    base = gw.descriptor_url(descriptor(upstream))
    assert call(base, Authorization="Bearer caller-own-key").status_code == 200
    assert upstream.seen[-1][0] == "/v1/chat/completions"
    assert upstream.header("authorization") == "Bearer " + REAL
    assert "caller-own-key" not in str(upstream.seen)


def test_a_custom_header_with_no_scheme_is_used(upstream):
    prov = descriptor(upstream, auth={"type": "env_var", "key": "TEST_CHAT_KEY", "header": "x-api-key", "scheme": ""})
    call(gw.descriptor_url(prov), **{"x-api-key": "caller-own-key"})
    assert upstream.header("x-api-key") == REAL and upstream.header("authorization") is None


def test_only_the_descriptors_own_path_passes(upstream):
    base = gw.descriptor_url(descriptor(upstream))
    root = base.rsplit("/v1", 1)[0]
    assert requests.post(root + "/admin/keys", json={}, timeout=10).status_code == 403
    assert upstream.seen == []


def test_a_static_extra_header_is_not_mistaken_for_the_key_header(upstream):
    prov = descriptor(upstream, extra_headers={"X-Title": "Agent Friday"})
    call(gw.descriptor_url(prov))
    assert upstream.header("authorization") == "Bearer " + REAL


@pytest.mark.parametrize("changes", [{"auth": {"type": "none"}}])
def test_a_descriptor_that_sends_no_key_gets_no_listener(upstream, changes):
    assert gw.descriptor_url(descriptor(upstream, **changes)) is None


@pytest.mark.parametrize("address", ["https://127.0.0.1:8080/v1", "https://localhost/v1", "https://192.168.1.20/v1",
                                      "http://api.example.com/v1", "ftp://api.example.com/v1", ""])
def test_a_local_private_or_plain_http_host_is_never_proxied(monkeypatch, address):
    monkeypatch.setenv("TEST_CHAT_KEY", REAL)
    prov = {"name": "x", "base_url": address, "auth": {"type": "env_var", "key": "TEST_CHAT_KEY"}}
    assert gw.descriptor_url(prov) is None


def test_two_descriptors_never_share_a_listener_and_one_descriptor_reuses_its_own(upstream):
    first = gw.descriptor_url(descriptor(upstream))
    assert gw.descriptor_url(descriptor(upstream)) == first
    assert gw.descriptor_url(descriptor(upstream, name="other")) != first


def test_the_key_is_read_on_every_request(upstream, monkeypatch):
    base = gw.descriptor_url(descriptor(upstream))
    monkeypatch.setenv("TEST_CHAT_KEY", "sk-swapped-key")  # pragma: allowlist secret
    call(base)
    assert upstream.header("authorization") == "Bearer sk-swapped-key"


def test_without_a_key_the_gateway_answers_for_itself(upstream, monkeypatch):
    monkeypatch.delenv("TEST_CHAT_KEY")
    answer = call(gw.descriptor_url(descriptor(upstream)))
    assert gw.no_key(answer) and upstream.seen == []
