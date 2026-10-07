"""Provider wire contracts use a fake HTTP transport, never registrar calls."""
import pytest

from agent_friday.services import namecom


@pytest.fixture
def wire(monkeypatch):
    calls, queued = [], []
    class Response:
        def __init__(self, status, payload, headers):
            self.status_code, self.payload, self.headers = status, payload, headers
            self.content = b"{}"
        def json(self):
            return self.payload
        def close(self):
            pass
    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        status, payload, headers = queued.pop(0)
        return Response(status, payload, headers)
    monkeypatch.setattr("requests.request", request)
    monkeypatch.setattr(namecom, "_CALLS", {})
    return namecom.Client("account-one", "synthetic-user", "test-token", "sandbox"), calls, queued


def test_dns_crud_uses_core_v1_and_full_update_body(wire):
    client, calls, queued = wire
    queued.extend([(200, {"id": 17}, {})] * 3)
    value = {"host": "www", "type": "CNAME", "answer": "pages.example.net", "ttl": 300}
    client.put_record("example.com", value)
    client.put_record("example.com", value, 17)
    client.delete_record("example.com", 17)
    assert [c[0] for c in calls] == ["POST", "PUT", "DELETE"]
    assert calls[1][1] == "https://api.dev.name.com/core/v1/domains/example.com/records/17"
    assert calls[1][2]["json"] == dict(value, answer="pages.example.net.", id=17)
    assert calls[1][2]["auth"] == ("synthetic-user", "test-token")
    assert calls[1][2]["allow_redirects"] is False
    assert all("test-token" not in c[1] for c in calls)


def test_domain_pagination_uses_numeric_pages_not_foreign_links(wire):
    client, calls, queued = wire
    queued.extend([(200, {"domains": [{"domainName": "one.example"}], "nextPage": 2}, {"Link": "https://untrusted.example"}),
                   (200, {"domains": [{"domainName": "two.example"}]}, {})])
    assert len(client.domains()) == 2
    assert [c[2]["params"]["page"] for c in calls] == [1, 2]
    assert all(c[1].startswith(namecom.BASES["sandbox"]) for c in calls)


def test_broken_pagination_never_returns_partial_inventory(wire):
    client, calls, queued = wire
    queued.append((200, {"records": [{"id": 1}], "nextPage": 1}, {}))
    with pytest.raises(namecom.ProviderError, match="pagination"):
        client.records("example.com")


@pytest.mark.parametrize("status,ambiguous", [(401, False), (403, False), (429, False), (503, True)])
def test_mutation_errors_are_redacted_and_never_retried(wire, status, ambiguous):
    client, calls, queued = wire
    queued.append((status, {"message": "test-token synthetic-user sensitive provider echo"}, {"Retry-After": "30"}))
    with pytest.raises(namecom.ProviderError) as caught:
        client.autorenew("example.com", True)
    assert "test-token" not in str(caught.value)
    assert "synthetic-user" not in str(caught.value)
    assert caught.value.ambiguous is ambiguous
    assert caught.value.retry_after == 30
    assert len(calls) == 1


def test_autorenew_patch_does_not_change_privacy_or_lock(wire):
    client, calls, queued = wire
    queued.append((200, {}, {}))
    client.autorenew("example.com", False)
    assert calls[0][0] == "PATCH"
    assert calls[0][2]["json"] == {"autorenewEnabled": False}


def test_renewal_quote_uses_requested_term_and_no_charge_method(wire):
    client, calls, queued = wire
    queued.append((200, {"renewalPrice": 25.5, "premium": False}, {}))
    assert client.pricing("example.com", 2)["renewalPrice"] == 25.5
    assert calls[0][0] == "GET"
    assert calls[0][2]["params"] == {"years": 2}
    assert not hasattr(client, "renew")


@pytest.mark.parametrize("value", ["https://example.com", "example.com/path", "example.com:443", "../example.com", "127.0.0.1"])
def test_domain_path_cannot_escape_provider_endpoint(value):
    with pytest.raises(ValueError):
        namecom.domain_name(value)


def test_unicode_domain_normalizes_to_one_identity():
    assert namecom.domain_name("BÜCHER.example.") == "xn--bcher-kva.example"


@pytest.mark.parametrize("record", [
    {"host": "www", "type": "NS", "answer": "ns.example.com"},
    {"host": "www", "type": "A", "answer": "not-an-ip"},
    {"host": "www", "type": "AAAA", "answer": "192.0.2.1"},
    {"host": "../escape", "type": "TXT", "answer": "text"},
    {"host": "", "type": "MX", "answer": "mail.example.com"},
    {"host": "", "type": "TXT", "answer": "text", "ttl": 1},
    {"host": False, "type": "TXT", "answer": "text"},
])
def test_invalid_records_and_delegation_are_refused(record):
    with pytest.raises(ValueError):
        namecom.record_payload(record)


@pytest.mark.parametrize("transition", ["response", "decode", "close"])
def test_admitted_guard_stops_next_page_and_closes_the_response(monkeypatch, transition):
    current, calls, closed = {"value": 7}, [], []
    def guard():
        if current["value"] != 7:
            raise ValueError("Original privacy context ended")
    client = namecom.Client("guarded-account", "synthetic", "synthetic-token", "sandbox", _request_guard=guard)
    class Response:
        status_code, headers, content = 200, {}, b"{}"
        def json(self):
            if transition == "decode":
                current["value"] = 9
            return {"domains": [{"domainName": "one.example"}], "nextPage": 2}
        def close(self):
            closed.append(True)
            if transition == "close":
                current["value"] = 9
    def request(*args, **kwargs):
        calls.append(kwargs["params"]["page"])
        if transition == "response":
            current["value"] = 9
        return Response()
    monkeypatch.setattr("requests.request", request)
    monkeypatch.setattr(namecom, "_CALLS", {})
    with pytest.raises(ValueError, match="Original privacy context") as caught:
        client.domains()
    assert not isinstance(caught.value, namecom.ProviderError)
    assert calls == [1] and closed == [True]


def test_pagination_rechecks_guard_even_when_transport_is_replaced(monkeypatch):
    valid, calls = {"value": True}, []
    def guard():
        if not valid["value"]:
            raise ValueError("Original privacy context ended")
    client = namecom.Client("guarded-account", "synthetic", "synthetic-token", _request_guard=guard)
    def request(*args, **kwargs):
        calls.append(kwargs["params"]["page"])
        valid["value"] = False
        return {"records": [{"id": 1}], "nextPage": 2}
    monkeypatch.setattr(client, "_request", request)
    with pytest.raises(ValueError, match="Original privacy context"):
        client.records("one.example")
    assert calls == [1]


def test_expired_guard_prevents_request_before_rate_limit_admission(monkeypatch):
    def guard():
        raise ValueError("Original privacy context ended")
    client = namecom.Client("guarded-account", "synthetic", "synthetic-token", _request_guard=guard)
    monkeypatch.setattr(namecom, "_admit", lambda *_: pytest.fail("Expired requests cannot enter the transport"))
    monkeypatch.setattr("requests.request", lambda *a, **kw: pytest.fail("Expired requests cannot send credentials"))
    with pytest.raises(ValueError, match="Original privacy context"):
        client.hello()
