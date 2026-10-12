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
    assert "fixture-local:7b" in names


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
    from agent_friday import core
    from agent_friday.services import egress_gate
    _call_env(monkeypatch)
    monkeypatch.setattr(core, "_load_settings",
                        lambda: {"model_routing": {"mode": "local_preferred"}})
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


# ── refusal happens before the queue ────────────────────────────────────────
def test_refusal_comes_before_the_queue(monkeypatch):
    from agent_friday.services import background_gate, visible_jobs
    from agent_friday.services.local_only_guard import local_only
    monkeypatch.setattr(background_gate, "before_local_model_call",
                        lambda m: pytest.fail("queued before the refusal"))
    monkeypatch.setattr(visible_jobs, "note_inference",
                        lambda *a, **k: pytest.fail("job card before the refusal"))
    with local_only("fixture job"):
        assert lc.call("s", "u", CLOUD[0]) == ""


# ── the owner's global Local only mode (M1) ──────────────────────────────────
def test_call_refuses_a_cloud_tag_under_global_local_only_mode(monkeypatch):
    import requests
    from agent_friday import core
    from agent_friday.services import background_gate, visible_jobs
    monkeypatch.setattr(core, "_load_settings",
                        lambda: {"model_routing": {"mode": "local_only"}})
    monkeypatch.setattr(background_gate, "before_local_model_call",
                        lambda m: pytest.fail("queued before the refusal"))
    monkeypatch.setattr(visible_jobs, "note_inference", lambda *a, **k: None)
    monkeypatch.setattr(requests, "post", _Posted())
    assert lc.call("s", "u", CLOUD[0]) == ""


# ── aliases the daemon marks as remote (M2) ─────────────────────────────────
@pytest.fixture
def _aliases():
    from agent_friday.services import local_only_guard as g
    before = g._REMOTE_ALIASES
    yield g
    g._REMOTE_ALIASES = before


def test_an_alias_marked_remote_by_the_daemon_is_cloud(_aliases):
    g = _aliases
    assert not g.is_cloud_model_tag("myglm:latest")
    g.note_remote_aliases([{"name": "MyGLM:latest", "remote_host": "https://ollama.com"},
                           {"name": "plain:7b"}])
    assert g.is_cloud_model_tag("myglm:latest")
    assert not g.is_cloud_model_tag("plain:7b")
    g.note_remote_aliases([{"name": "plain:7b"}])   # replaced by the next read
    assert not g.is_cloud_model_tag("myglm:latest")


def test_daemon_tags_read_feeds_the_alias_set_and_dispatch_uses_it(monkeypatch, _aliases):
    import io
    import json
    import urllib.request
    g = _aliases
    body = json.dumps({"models": [{"name": "myglm:latest", "remote_model": "x:cloud"},
                                  {"name": "plain:7b"}]}).encode()
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: io.BytesIO(body))
    monkeypatch.setattr(lc, "seat_endpoint", lambda m: None)
    assert lc.describe_dispatch("myglm:latest")["route"] == "cloud_relay"
    assert lc.describe_dispatch("plain:7b")["route"] == "daemon"


# ── other "is it installed" checks (H1, H2) ──────────────────────────────────
@pytest.mark.parametrize("model", CLOUD)
def test_a_cloud_tag_is_not_a_local_name(model):
    from agent_friday.services import local_seats
    assert local_seats._names_a_cloud_model(model)
    assert not local_seats._is_local_name(model)
    assert local_seats._is_local_name("fixture-local:7b")


@pytest.mark.parametrize("model", CLOUD)
def test_saving_a_cloud_tag_is_not_refused_as_not_installed(monkeypatch, model):
    from agent_friday.routes.core_routes import _check_seat_installed
    from agent_friday.services import local_seats
    monkeypatch.setattr(local_seats, "installed",
                        lambda force=False: [("fixture-local:7b", 4.0)])
    settings = {"capability_routing": {"reasoning": {"model": model}},
                "model_routing": {"local_model": model}}
    assert _check_seat_installed(settings) is None
    # A missing plain local model is still refused.
    settings = {"capability_routing": {"reasoning": {"model": "fixture-gone:7b"}}}
    assert _check_seat_installed(settings)["error"] == "seat_not_installed"


# ── the cloud_relay log branch and the once-only notice (L1) ─────────────────
def test_log_dispatch_table_names_a_cloud_relay(monkeypatch, caplog):
    monkeypatch.setattr(lc, "seat_endpoint", lambda m: None)
    monkeypatch.setattr(lc, "_daemon_tags", lambda: set())
    with caplog.at_level("INFO", logger="friday.local_call"):
        rows = lc.log_dispatch_table([CLOUD[0]], expect_up={CLOUD[0]})
    assert rows[0]["route"] == "cloud_relay"
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "cloud-relayed" in text and "UNREACHABLE" not in text


def test_a_skipped_cloud_role_is_logged_once(monkeypatch, caplog):
    from agent_friday.services import scheduler
    from agent_friday.services.scheduler import _resolve_local_seat
    scheduler._CLOUD_SKIP_LOGGED.discard(CLOUD[0])
    _resolve_env(monkeypatch, {"model_routing": {"local_model": CLOUD[0]}},
                 installed=[], routes={})
    with caplog.at_level("INFO"):
        _resolve_local_seat()
        _resolve_local_seat()
    hits = [r for r in caplog.records if CLOUD[0] in r.getMessage()
            and "cloud-relayed" in r.getMessage()]
    assert len(hits) == 1
