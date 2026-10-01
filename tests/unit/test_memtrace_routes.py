"""The memory census answers only Friday's own page on this PC, and never
returns what the memory holds: sizes, counts and code locations only.

The routes are mounted on a bare Flask app so the test does not import the
whole server. tracemalloc is stopped after every test.
"""
from __future__ import annotations

import inspect
import json
import tracemalloc

import pytest
from flask import Flask

import agent_friday.core as core

MARKER = "memtrace-census-marker-"
_KEPT: list = []


def _grow():
    """Allocate and keep strings whose content the census must never echo."""
    for i in range(4000):
        _KEPT.append(MARKER + ("%06d" % i) * 8)  # GROWTH LINE


_GROWTH_LINE = next(
    n for n, line in enumerate(inspect.getsourcelines(_grow)[0],
                               start=inspect.getsourcelines(_grow)[1])
    if "GROWTH LINE" in line and "_KEPT.append" in line)


@pytest.fixture
def client():
    from agent_friday.routes import memtrace
    app = Flask(__name__)
    app.secret_key = "test-only"  # pragma: allowlist secret
    app.config.update(TESTING=True)
    app.register_blueprint(memtrace.memtrace_bp)
    yield app.test_client()
    if tracemalloc.is_tracing():
        tracemalloc.stop()
    memtrace._reset_for_tests()
    _KEPT.clear()


def _token():
    return {"X-Friday-Token": core._current_api_token()}


REMOTE = {"REMOTE_ADDR": "203.0.113.9"}


@pytest.mark.parametrize("method,path", [
    ("post", "/api/debug/memtrace/start"),
    ("get", "/api/debug/memtrace/top"),
    ("post", "/api/debug/memtrace/stop"),
])
def test_a_request_from_another_machine_is_refused(client, monkeypatch, method, path):
    # A remote key is configured and the page's token is valid, so the app's
    # login gate lets the request through: only the census's own loopback
    # check stands between a remote caller and the census.
    monkeypatch.setattr(core, "_HTTP_AUTH_KEY", "remote-key-for-tests")  # pragma: allowlist secret
    r = getattr(client, method)(path, headers=_token(), environ_base=REMOTE)
    assert r.status_code == 403
    assert not tracemalloc.is_tracing()


@pytest.mark.parametrize("method,path", [
    ("post", "/api/debug/memtrace/start"),
    ("get", "/api/debug/memtrace/top"),
    ("post", "/api/debug/memtrace/stop"),
])
def test_a_local_request_without_the_pages_token_is_refused(client, method, path):
    r = getattr(client, method)(path)
    assert r.status_code == 403
    assert not tracemalloc.is_tracing()


def test_top_before_start_says_so(client):
    r = client.get("/api/debug/memtrace/top", headers=_token())
    assert r.status_code == 409


def test_growth_is_attributed_to_the_line_that_keeps_it(client):
    r = client.post("/api/debug/memtrace/start", headers=_token())
    assert r.status_code == 200, r.get_data(as_text=True)
    assert tracemalloc.is_tracing()

    _grow()

    r = client.get("/api/debug/memtrace/top?limit=30", headers=_token())
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    text = r.get_data(as_text=True)
    assert MARKER not in text, "the census returned memory CONTENT"

    me = __file__.replace("\\", "/").rsplit("/", 1)[-1]
    hits = [g for g in body["top"]
            if g["top_frame"]["file"].endswith(me)
            and g["top_frame"]["line"] == _GROWTH_LINE]
    assert hits, "growth not attributed to %s:%d; got %s" % (
        me, _GROWTH_LINE, json.dumps([g["top_frame"] for g in body["top"][:10]]))
    hit = hits[0]
    assert hit["size_diff"] > 4000 * 50
    assert hit["count_diff"] >= 4000
    assert isinstance(hit["frames"], list) and hit["frames"]
    assert "agent_friday_frame" in hit

    assert len(body["gc_types"]) <= 30
    assert all(set(t) == {"type", "count"} for t in body["gc_types"])
    assert "private_bytes" in body["process"]

    r = client.post("/api/debug/memtrace/stop", headers=_token())
    assert r.status_code == 200
    assert not tracemalloc.is_tracing()


def test_no_frame_names_a_home_directory(client):
    client.post("/api/debug/memtrace/start", headers=_token())
    _grow()
    body = client.get("/api/debug/memtrace/top", headers=_token()).get_json()
    import os
    home = os.path.expanduser("~").replace("\\", "/").lower()
    for g in body["top"]:
        for f in g["frames"]:
            assert home not in f["file"].replace("\\", "/").lower()
