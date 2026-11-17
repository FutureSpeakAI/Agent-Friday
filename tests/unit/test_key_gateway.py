"""The key gateway (services/key_gateway.py): modules send a stand-in, the gateway puts the real key on the request.

Rules guarded here, each with a case that fails if the check is removed:
* the real key reaches the provider only, in the provider's own header, replacing whatever the caller sent;
* a path or verb outside the provider's row is refused and never reaches upstream; no key means 401 and nothing sent;
* the key is looked up on every request;
* an unknown provider has no URL;
* ``stand_in`` and ``sources`` never return the key, and Brave's callers never hold it.
"""
import http.server
import threading

import pytest
import requests

from agent_friday.services import key_gateway as gw
from agent_friday.services import news_engine, web_search

REAL = "real-brave-key-for-tests"  # pragma: allowlist secret


class Upstream:
    """A local stand-in for the provider: records every request it receives."""

    def __init__(self):
        self.seen = []
        self.names = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.seen.append((self.path, dict(self.headers)))
                outer.names.append([name.lower() for name in self.headers.keys()])
                body = b'{"web": {"results": []}, "results": []}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_POST = do_GET

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.origin = "http://127.0.0.1:%d" % self.server.server_address[1]

    def header(self, name):
        return {k.lower(): v for k, v in self.seen[-1][1].items()}.get(name.lower())


@pytest.fixture
def upstream(monkeypatch, tmp_path):
    fake = Upstream()
    row = gw.PROVIDERS["brave"]
    monkeypatch.setitem(gw.PROVIDERS, "brave", gw.Provider(**{**row.__dict__, "upstream": fake.origin}))
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", REAL)
    monkeypatch.setenv("NO_PROXY", "example.com")   # registered so the test's change to it is undone
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setattr(gw, "_from_settings", lambda row: "")
    gw.stop_all()
    yield fake
    gw.stop_all()
    fake.server.shutdown()


def ask(path, method="GET", **headers):
    return requests.request(method, gw.url("brave") + path, headers=headers, timeout=10)


def test_the_real_key_replaces_whatever_the_caller_sent(upstream):
    answer = ask("/res/v1/web/search?q=x", **{"X-Subscription-Token": gw.stand_in("brave")})
    assert answer.status_code == 200
    assert upstream.header("X-Subscription-Token") == REAL


def test_a_callers_key_header_in_another_spelling_is_dropped_not_forwarded(upstream):
    ask("/res/v1/web/search?q=x", **{"x-subscription-token": "caller-value"})
    assert upstream.names[-1].count("x-subscription-token") == 1
    assert upstream.header("X-Subscription-Token") == REAL


def test_a_path_outside_the_providers_row_is_refused_and_never_sent(upstream):
    assert ask("/admin/keys").status_code == 403 and upstream.seen == []


def test_a_verb_outside_the_providers_row_is_refused_and_never_sent(upstream):
    assert ask("/res/v1/web/search", method="POST").status_code == 403 and upstream.seen == []


def test_with_no_key_nothing_is_sent(upstream, monkeypatch):
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY")
    monkeypatch.setattr(gw, "_find_key", lambda row: "")
    assert ask("/res/v1/web/search?q=x").status_code == 401 and upstream.seen == []


def test_the_key_is_looked_up_on_every_request(upstream, monkeypatch):
    ask("/res/v1/web/search?q=a")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "a-changed-key")  # pragma: allowlist secret
    ask("/res/v1/web/search?q=b")
    assert upstream.header("X-Subscription-Token") == "a-changed-key"


def test_an_unknown_provider_has_no_url():
    with pytest.raises(gw.UnknownProvider):
        gw.url("not-a-provider")


def test_the_gateway_is_loopback_only(upstream):
    assert gw.url("brave").startswith("http://127.0.0.1:")


def test_a_system_proxy_is_told_to_leave_loopback_alone(upstream, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.com:8080")
    monkeypatch.setenv("NO_PROXY", "example.com")
    monkeypatch.delenv("no_proxy", raising=False)
    gw.stop_all()
    gw.url("brave")
    assert "127.0.0.1" in __import__("os").environ["NO_PROXY"]


def test_stand_in_sources_and_availability_never_return_the_key(upstream):
    assert gw.available("brave") is True
    assert gw.stand_in("brave") == gw.DUMMY_KEY != REAL
    assert REAL not in repr(gw.sources("brave")) and gw.sources("brave")["environment"] is True


def test_without_a_key_the_stand_in_is_empty(monkeypatch):
    monkeypatch.setattr(gw, "_find_key", lambda row: "")
    assert gw.stand_in("brave") == "" and gw.available("brave") is False


def test_brave_callers_never_hold_the_real_key(upstream):
    assert web_search.brave_key() == gw.DUMMY_KEY
    answer = web_search._brave("hello", 3)
    assert upstream.seen and upstream.header("X-Subscription-Token") == REAL
    assert REAL not in repr(answer) and "friday-proxy-dummy" not in repr(answer)


def test_the_news_search_goes_through_the_gateway(upstream, monkeypatch):
    monkeypatch.setattr("agent_friday.services.web_search._gate_search_query", lambda q, why: (q, None))
    monkeypatch.setattr("agent_friday.services.cloud_intent.outbound_held", lambda *a, **k: None)
    news_engine._brave_results("election", 5)
    assert upstream.seen and upstream.seen[-1][0].startswith("/res/v1/news/search")
    assert upstream.header("X-Subscription-Token") == REAL


def test_the_gateways_own_no_key_refusal_is_told_apart_from_the_providers_401(upstream, monkeypatch):
    monkeypatch.setattr(gw, "_find_key", lambda row: "")
    answer = ask("/res/v1/web/search?q=x")
    assert answer.status_code == 401 and gw.no_key(answer) is True
    monkeypatch.setattr(web_search.key_gateway, "stand_in", lambda name: gw.DUMMY_KEY)
    result = web_search._brave("hello", 3)
    assert "no Brave key is configured" in result["detail"] and "rejected" not in result["detail"]


def test_the_record_keeps_only_the_newest_entries(monkeypatch):
    from agent_friday.services import salon_proxy
    monkeypatch.setattr(salon_proxy, "CAPTURE_CAP", 5)
    proxy = salon_proxy.KeyProxy()
    for number in range(12):
        proxy._record(n=number)
    assert [entry["n"] for entry in proxy.capture()] == [7, 8, 9, 10, 11]


def test_the_upstream_wait_is_the_rows_not_ten_minutes(upstream):
    assert gw.PROVIDERS["brave"].timeout_s <= 60
    gw.url("brave")
    assert gw._PROXIES["brave"].upstream_timeout == gw.PROVIDERS["brave"].timeout_s


@pytest.mark.parametrize("before,after", [("", "127.0.0.1"), ("example.com", "example.com,127.0.0.1"),
                                           ("*", "*"), ("127.0.0.10", "127.0.0.10,127.0.0.1")])
def test_no_proxy_gets_loopback_as_a_whole_entry_and_a_wildcard_is_left_alone(monkeypatch, before, after):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.com:8080")
    monkeypatch.setenv("NO_PROXY", before)
    monkeypatch.setenv("no_proxy", before)
    gw._keep_loopback_direct()
    assert __import__("os").environ["NO_PROXY"] == after


def test_with_no_system_proxy_the_environment_is_left_alone(monkeypatch):
    for var in gw._PROXY_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "example.com")
    gw._keep_loopback_direct()
    assert __import__("os").environ["NO_PROXY"] == "example.com"


def test_a_gateway_that_cannot_start_does_not_crash_the_key_check(monkeypatch):
    monkeypatch.setattr(gw, "_find_key", lambda row: "stored")
    monkeypatch.setattr(web_search.key_gateway, "url", lambda name: (_ for _ in ()).throw(OSError("bind failed")))
    result = web_search.verify_key()
    assert result["valid"] is None and "could not start" in result["summary"]
