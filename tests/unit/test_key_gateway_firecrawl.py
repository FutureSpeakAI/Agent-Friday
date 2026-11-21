"""Firecrawl through the key gateway (second provider): same rules as Brave, a different header.

Rules guarded here: the real key goes out as ``Authorization: Bearer <key>`` replacing the caller's; only the
provider's own paths pass; firecrawl.py holds only the stand-in.
"""
import pytest
import requests

from agent_friday.services import firecrawl
from agent_friday.services import key_gateway as gw
from tests.unit.test_key_gateway import Upstream

REAL = "fc-real-key-for-tests"  # pragma: allowlist secret


@pytest.fixture
def upstream(monkeypatch):
    fake = Upstream()
    row = gw.PROVIDERS["firecrawl"]
    monkeypatch.setitem(gw.PROVIDERS, "firecrawl", gw.Provider(**{**row.__dict__, "upstream": fake.origin}))
    monkeypatch.setenv("FIRECRAWL_API_KEY", REAL)
    monkeypatch.setenv("NO_PROXY", "example.com")   # registered so the test's change to it is undone
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setattr(gw, "_from_settings", lambda row: "")
    monkeypatch.setattr("agent_friday.services.cloud_intent.outbound_held", lambda *a, **k: None)
    gw.stop_all()
    yield fake
    gw.stop_all()
    fake.server.shutdown()


def test_the_real_key_goes_out_as_a_bearer_replacing_the_callers(upstream):
    answer = requests.post(gw.url("firecrawl") + "/v2/search", json={"query": "x"},
                           headers={"Authorization": "Bearer " + gw.stand_in("firecrawl")}, timeout=10)
    assert answer.status_code == 200 and upstream.header("Authorization") == "Bearer " + REAL


def test_a_path_outside_the_providers_row_is_refused_and_never_sent(upstream):
    assert requests.get(gw.url("firecrawl") + "/admin", timeout=10).status_code == 403 and upstream.seen == []


def test_firecrawl_callers_never_hold_the_real_key(upstream):
    assert firecrawl.key_stand_in() == gw.DUMMY_KEY and firecrawl.configured() is True
    payload, error = firecrawl._post("/v2/search", {"query": "x"}, 10)
    assert upstream.header("Authorization") == "Bearer " + REAL
    assert REAL not in repr((payload, error))


def test_the_credit_check_goes_through_the_gateway(upstream):
    firecrawl.credits()
    assert upstream.seen[-1][0] == "/v2/team/credit-usage" and upstream.header("Authorization") == "Bearer " + REAL


def test_without_a_key_nothing_is_sent_and_it_says_so(upstream, monkeypatch):
    monkeypatch.delenv("FIRECRAWL_API_KEY")
    monkeypatch.setattr(gw, "_find_key", lambda row: "")
    payload, error = firecrawl._post("/v2/search", {"query": "x"}, 10)
    assert payload is None and "no Firecrawl API key" in error and upstream.seen == []


def test_the_rows_upstream_wait_covers_the_longest_wait_the_module_allows():
    longest = max(firecrawl.DEFAULT_TIMEOUT_S, firecrawl.SEARCH_TIMEOUT_S)
    assert gw.PROVIDERS["firecrawl"].timeout_s >= longest


def test_a_callers_authorization_in_another_spelling_is_not_forwarded(upstream):
    requests.get(gw.url("firecrawl") + "/v2/team/credit-usage", headers={"AUTHORIZATION": "Bearer caller"}, timeout=10)
    assert upstream.names[-1].count("authorization") == 1 and upstream.header("Authorization") == "Bearer " + REAL


def test_the_gateways_own_no_key_refusal_is_not_reported_as_a_rejected_key(upstream, monkeypatch):
    monkeypatch.setattr(firecrawl, "configured", lambda: True)
    monkeypatch.setattr(gw, "_find_key", lambda row: "")
    payload, error = firecrawl._post("/v2/search", {"query": "x"}, 10)
    assert payload is None and "rejected" not in error and "no Firecrawl API key" in error
