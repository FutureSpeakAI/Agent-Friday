"""An Ollama cloud-tagged model is never a local seat.

A `:cloud` / `-cloud` tag is answered by the local daemon but run on
ollama.com. `describe_dispatch` used to report such a tag as route "daemon",
so `scheduler._resolve_local_seat` returned it as the local seat and
`local_call.call` sent owner text to it without consulting the local-only
guard. Synthetic model names and fakes only; nothing here touches a network.
"""
from __future__ import annotations

import pytest

from agent_friday.services import local_call as lc

CLOUD = ["fixture-model:cloud", "fixture-model:8b-cloud", "fixture-model-cloud"]


@pytest.fixture(autouse=True)
def _no_endpoint_cache():
    lc._EP_CACHE.clear()
    yield
    lc._EP_CACHE.clear()


@pytest.mark.parametrize("model", CLOUD)
def test_describe_dispatch_reports_a_cloud_tag_as_its_own_route(monkeypatch, model):
    monkeypatch.setattr(lc, "seat_endpoint", lambda m: "http://127.0.0.1:8090/v1")
    monkeypatch.setattr(lc, "_daemon_tags", lambda: {model})
    row = lc.describe_dispatch(model)
    assert row["route"] == "cloud_relay"
    assert row["route"] not in ("seat", "daemon")


def test_describe_dispatch_still_reports_plain_local_models(monkeypatch):
    monkeypatch.setattr(lc, "seat_endpoint", lambda m: None)
    monkeypatch.setattr(lc, "_daemon_tags", lambda: {"fixture-local:7b"})
    assert lc.describe_dispatch("fixture-local:7b")["route"] == "daemon"


def _resolve_env(monkeypatch, settings, installed, routes):
    import agent_friday.core as core
    from agent_friday.services import local_seats
    monkeypatch.setattr(core, "_load_settings", lambda: settings)
    monkeypatch.setattr(local_seats, "installed",
                        lambda force=False: [(n, 1.0) for n in installed])
    monkeypatch.setattr(lc, "describe_dispatch",
                        lambda m, daemon_tags=None: {"route": routes.get(m, "unreachable"),
                                                     "model": m})


@pytest.mark.parametrize("model", CLOUD)
def test_resolve_local_seat_never_returns_a_cloud_tag(monkeypatch, model):
    from agent_friday.services.scheduler import _resolve_local_seat
    # Even a dispatch table that (wrongly) calls it a daemon model must not win.
    _resolve_env(monkeypatch,
                 {"capability_routing": {"reasoning": {"model": model}},
                  "model_routing": {"local_model": model}},
                 installed=[model], routes={model: "daemon"})
    assert _resolve_local_seat() is None


def test_resolve_local_seat_skips_the_cloud_tag_for_a_real_seat(monkeypatch):
    from agent_friday.services.scheduler import _resolve_local_seat
    cloud = CLOUD[0]
    _resolve_env(monkeypatch,
                 {"capability_routing": {"reasoning": {"model": cloud}}},
                 installed=[cloud, "fixture-local:7b"],
                 routes={cloud: "daemon", "fixture-local:7b": "daemon"})
    assert _resolve_local_seat() == "fixture-local:7b"


def test_local_seats_installed_excludes_cloud_tags(monkeypatch):
    from agent_friday.services import local_seats
    monkeypatch.setattr(local_seats, "_friday_store", lambda: [])
    monkeypatch.setattr(local_seats, "_daemon_port_open", lambda base: True)
    monkeypatch.setattr(local_seats, "_CACHE", {"at": 0, "rows": []})
    import io
    import json
    import urllib.request
    body = json.dumps({"models": [{"name": CLOUD[0], "size": 0},
                                  {"name": "fixture-local:7b", "size": 4e9}]}).encode()
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: io.BytesIO(body))
    names = [n for n, _ in local_seats.installed(force=True)]
    assert CLOUD[0] not in names


class _Posted:
    def __init__(self):
        self.calls = []

    def __call__(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        raise AssertionError("no network in tests")


def _call_env(monkeypatch):
    from agent_friday.services import background_gate, visible_jobs
    monkeypatch.setattr(background_gate, "before_local_model_call", lambda m: None)
    monkeypatch.setattr(visible_jobs, "note_inference", lambda *a, **k: None)
    monkeypatch.setattr(lc, "seat_endpoint", lambda m: "http://127.0.0.1:8090/v1")


@pytest.mark.parametrize("model", CLOUD)
def test_call_refuses_a_cloud_tag_inside_local_only(monkeypatch, caplog, model):
    import requests
    from agent_friday.services import egress_gate
    from agent_friday.services.local_only_guard import local_only
    _call_env(monkeypatch)
    posted = _Posted()
    monkeypatch.setattr(requests, "post", posted)
    monkeypatch.setattr(egress_gate, "gate_ollama_body",
                        lambda *a, **k: pytest.fail("refusal comes before the egress path"))
    with caplog.at_level("WARNING", logger="friday.local_call"):
        with local_only("fixture job"):
            out = lc.call("sys", "owner text", model)
    assert out == ""
    assert posted.calls == []
    assert any("refused" in r.getMessage() for r in caplog.records)


def test_call_sends_a_cloud_tag_through_the_egress_gate_outside_local_only(monkeypatch):
    import requests
    from agent_friday.services import egress_gate
    _call_env(monkeypatch)
    gated, posted = [], []

    def fake_gate(body, model="", provider="ollama"):
        gated.append(model)
        return body

    class _R:
        status_code = 200

        def json(self):
            return {"message": {"content": "ok"}}

    monkeypatch.setattr(egress_gate, "gate_ollama_body", fake_gate)
    monkeypatch.setattr(requests, "post",
                        lambda url, json=None, timeout=None: posted.append(url) or _R())
    assert lc.call("sys", "text", CLOUD[0]) == "ok"
    assert gated == [CLOUD[0]]
    assert len(posted) == 1
    # Never to a llama.cpp seat endpoint, even if one is recorded for the name.
    assert "/api/chat" in posted[0]


def test_call_to_a_plain_local_model_is_unchanged_inside_local_only(monkeypatch):
    import requests
    from agent_friday.services.local_only_guard import local_only
    _call_env(monkeypatch)
    seen = []

    class _R:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "hi"}}]}

    monkeypatch.setattr(requests, "post",
                        lambda url, json=None, timeout=None: seen.append(url) or _R())
    with local_only("fixture job"):
        assert lc.call("s", "u", "fixture-local:7b") == "hi"
    assert seen
