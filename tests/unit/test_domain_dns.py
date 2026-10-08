import pytest

from agent_friday.services import domain_dns


@pytest.fixture(autouse=True)
def privacy(monkeypatch):
    epoch = {"value": 3}
    def require(generation):
        if generation != epoch["value"]:
            raise ValueError("Original privacy context ended")
    monkeypatch.setattr(domain_dns.sites_privacy, "require_generation", require)
    return epoch


def test_public_cname_proof_compares_cname_not_target_ip(monkeypatch):
    calls = []
    def query(host, kind, **kwargs):
        calls.append((host, kind))
        return ["target.example.net."]
    monkeypatch.setattr(domain_dns, "_query", query)
    result = domain_dns.verify("example.com", [{"host": "www", "type": "CNAME", "answer": "target.example.net", "ttl": 300}], generation=3)
    assert result["matches"]
    assert calls == [("www.example.com", "CNAME")]
    assert result["https"] == "not_checked"
    assert result["expectation_source"] == "supplied_records" and not result["site_verification_updated"]


def test_extra_address_is_not_mistaken_for_exact_target(monkeypatch):
    monkeypatch.setattr(domain_dns, "_query", lambda *a, **kw: ["192.0.2.10", "192.0.2.11"])
    result = domain_dns.verify("example.com", [{"host": "", "type": "A", "answer": "192.0.2.10", "ttl": 300}], generation=3)
    assert not result["matches"]


def test_aname_does_not_claim_a_nonexistent_public_rr(monkeypatch):
    def unexpected(*a, **kw):
        raise AssertionError("ANAME must not be queried as a real RR type")
    monkeypatch.setattr(domain_dns, "_query", unexpected)
    result = domain_dns.verify("example.com", [{"host": "", "type": "ANAME", "answer": "target.example.net", "ttl": 300}], generation=3)
    assert not result["matches"]
    assert result["checks"][0]["status"] == "not_directly_observable"


def test_txt_chunks_and_mx_priority_are_compared(monkeypatch):
    monkeypatch.setattr(domain_dns, "_query", lambda host, kind, **kw: ['"first ""second"'] if kind == "TXT" else ["10 mail.example.net."])
    result = domain_dns.verify("example.com", [
        {"host": "", "type": "TXT", "answer": "first second", "ttl": 300},
        {"host": "", "type": "MX", "answer": "mail.example.net", "priority": 10, "ttl": 300},
    ], generation=3)
    assert result["matches"]


def test_first_doh_response_transition_closes_response_and_stops_next_query(monkeypatch, privacy):
    calls, closed = [], []
    class Response:
        status_code, content = 200, b"{}"
        def json(self):
            privacy["value"] = 5
            return {"Status": 0, "Answer": [{"name": "one.example.com.", "type": 1, "data": "192.0.2.10"}]}
        def close(self):
            closed.append(True)
    def request(*args, **kwargs):
        calls.append(kwargs["params"]["name"])
        return Response()
    monkeypatch.setattr("requests.get", request)
    with pytest.raises(ValueError, match="Original privacy context"):
        domain_dns.verify("example.com", [
            {"host": "one", "type": "A", "answer": "192.0.2.10", "ttl": 300},
            {"host": "two", "type": "A", "answer": "192.0.2.11", "ttl": 300},
        ], generation=3)
    assert calls == ["one.example.com"] and closed == [True]


def test_verification_checks_between_replaced_queries(monkeypatch, privacy):
    calls = []
    def query(host, kind, **kwargs):
        calls.append((host, kind, kwargs["generation"]))
        privacy["value"] = 5
        return ["192.0.2.10"]
    monkeypatch.setattr(domain_dns, "_query", query)
    with pytest.raises(ValueError, match="Original privacy context"):
        domain_dns.verify("example.com", [
            {"host": "one", "type": "A", "answer": "192.0.2.10", "ttl": 300},
            {"host": "two", "type": "A", "answer": "192.0.2.11", "ttl": 300},
        ], generation=3)
    assert calls == [("one.example.com", "A", 3)]


def test_aname_only_result_rechecks_origin_before_return(monkeypatch, privacy):
    original = domain_dns.namecom.record_payload
    def payload(value):
        result = original(value)
        privacy["value"] = 5
        return result
    monkeypatch.setattr(domain_dns.namecom, "record_payload", payload)
    monkeypatch.setattr(domain_dns, "_query", lambda *a, **kw: pytest.fail("ANAME cannot issue a DNS query"))
    with pytest.raises(ValueError, match="Original privacy context"):
        domain_dns.verify("example.com", [{"host": "", "type": "ANAME", "answer": "target.example.net", "ttl": 300}], generation=3)
