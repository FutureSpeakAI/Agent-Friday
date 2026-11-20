"""The chat leg's descriptor path goes through the key gateway (streamed and not).

Rules guarded here: a cloud provider's call carries only the stand-in key and the real key reaches the provider
through the gateway, on the descriptor's own header; a streamed reply still arrives as events; a provider with no key
is refused before any request; a local provider, and a provider the gateway will not serve (plain http), is called
directly as before; the gateway waits as long as the descriptor's own timeout.
"""
import http.server
import json
import threading

import pytest
import requests

from agent_friday.services import key_gateway as gw
from agent_friday.services import model_router as smr
from agent_friday.services.salon_proxy import DUMMY_KEY

REAL_CLOUD_CHECK = gw._is_cloud_endpoint
REAL = "sk-chat-leg-real-key"  # pragma: allowlist secret


class Provider:
    """A chat provider: answers a streamed request with two events and a plain one with a JSON body."""

    def __init__(self):
        self.seen = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                outer.seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
                if body.get("stream"):
                    events = [{"model": "m", "choices": [{"delta": {"content": "hel"}}]},
                              {"model": "m", "choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}],
                               "usage": {"prompt_tokens": 1, "completion_tokens": 2}}]
                    data = "".join("data: %s\n\n" % json.dumps(event) for event in events) + "data: [DONE]\n\n"
                    kind = "text/event-stream"
                else:
                    data = json.dumps({"model": "m", "choices": [{"message": {"role": "assistant", "content": "hello"},
                                                                  "finish_reason": "stop"}],
                                       "usage": {"prompt_tokens": 1, "completion_tokens": 2}})
                    kind = "application/json"
                payload = data.encode()
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.origin = "http://127.0.0.1:%d" % self.server.server_address[1]


@pytest.fixture
def provider(monkeypatch):
    fake = Provider()
    monkeypatch.setenv("TEST_CHAT_LEG_KEY", REAL)
    monkeypatch.setenv("NO_PROXY", "example.com")   # registered so the test's change to it is undone
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setattr(gw, "_is_cloud_endpoint", lambda address: True)
    monkeypatch.setattr(smr, "_seal_or_block", lambda payload, provider_name, model="": payload)
    monkeypatch.setattr("agent_friday.services.cloud_intent.require_owner_engine", lambda *a, **k: None, raising=False)
    gw.stop_all()
    yield fake
    gw.stop_all()
    fake.server.shutdown()


def descriptor(fake, **changes):
    return {"name": "chatleg", "base_url": fake.origin + "/v1", "auth": {"type": "env_var", "key": "TEST_CHAT_LEG_KEY"},
            "models": ["m"], "features": {"streaming": True}, **changes}


@pytest.fixture
def sent(monkeypatch):
    """The headers this module itself put on its requests."""
    headers_sent = []
    real_post = requests.post

    def spy(url, headers=None, **kwargs):
        headers_sent.append(dict(headers or {}))
        return real_post(url, headers=headers, **kwargs)
    monkeypatch.setattr(requests, "post", spy)
    return headers_sent


@pytest.mark.parametrize("stream", [True, False])
def test_a_cloud_call_holds_the_stand_in_and_the_provider_gets_the_real_key(provider, sent, stream):
    text, _ = smr._call_openai([{"role": "user", "content": "hi"}], model="m", tools=None,
                               provider=descriptor(provider), stream=stream)
    assert text == "hello"
    assert sent[0]["Authorization"] == "Bearer " + DUMMY_KEY and REAL not in str(sent)
    path, headers, body = provider.seen[0]
    assert path == "/v1/chat/completions" and headers["authorization"] == "Bearer " + REAL
    assert bool(body.get("stream")) is stream


def test_without_a_key_the_call_says_so_and_nothing_reaches_the_provider(provider, monkeypatch):
    monkeypatch.delenv("TEST_CHAT_LEG_KEY")
    with pytest.raises(RuntimeError, match="No API key"):
        smr._call_openai([{"role": "user", "content": "hi"}], model="m", tools=None, provider=descriptor(provider))
    assert provider.seen == []


def test_a_provider_on_this_machine_is_called_directly_with_its_own_key(provider, sent, monkeypatch):
    local = descriptor(provider, classification="local", adapter="openai-compatible")
    smr._call_openai([{"role": "user", "content": "hi"}], model="m", tools=None, provider=local, stream=False)
    assert sent[0]["Authorization"] == "Bearer " + REAL and provider.seen[0][1]["authorization"] == "Bearer " + REAL


def test_a_provider_the_gateway_will_not_serve_is_called_directly_as_before(provider, sent, monkeypatch):
    monkeypatch.setattr(gw, "_is_cloud_endpoint", REAL_CLOUD_CHECK)   # the real rule: plain http / 127.0.0.1 is not served
    smr._call_openai([{"role": "user", "content": "hi"}], model="m", tools=None, provider=descriptor(provider),
                     stream=False)
    assert sent[0]["Authorization"] == "Bearer " + REAL and provider.seen[0][1]["authorization"] == "Bearer " + REAL


def test_the_gateway_waits_as_long_as_the_descriptors_own_timeout(provider):
    short = gw.descriptor_url(descriptor(provider))
    long_wait = gw.descriptor_url(descriptor(provider, network={"timeout_s": 600}))
    waits = {proxy.url: proxy.upstream_timeout for key, proxy in gw._PROXIES.items() if isinstance(key, tuple)}
    assert waits == {long_wait.rsplit("/v1", 1)[0]: 600}, (short, waits)
