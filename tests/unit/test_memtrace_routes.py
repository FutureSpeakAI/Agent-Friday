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

    r = client.get("/api/debug/memtrace/top?limit=30&types=1", headers=_token())
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


def test_top_never_runs_a_per_trace_python_filter(client, monkeypatch):
    # Snapshot.filter_traces fnmatches every live trace in Python: on a server
    # holding millions of traces it ran for minutes with the census lock held.
    def refuse(self, *a, **k):
        raise AssertionError("filter_traces walks every trace")
    client.post("/api/debug/memtrace/start", headers=_token())
    monkeypatch.setattr(tracemalloc.Snapshot, "filter_traces", refuse)
    _grow()
    r = client.get("/api/debug/memtrace/top?limit=200", headers=_token())
    assert r.status_code == 200, r.get_data(as_text=True)
    for g in r.get_json()["top"]:
        assert not g["top_frame"]["file"].endswith("tracemalloc.py")
        assert not g["top_frame"]["file"].startswith("<frozen importlib")


def test_the_comparison_runs_outside_the_census_lock(client, monkeypatch):
    # The auto-stop timer takes the same lock: a long comparison under it
    # kept tracing on past its deadline.
    from agent_friday.routes import memtrace
    real = tracemalloc.Snapshot.compare_to
    held = []

    def spy(self, *a, **k):
        held.append(memtrace._lock.locked())
        return real(self, *a, **k)
    client.post("/api/debug/memtrace/start", headers=_token())
    monkeypatch.setattr(tracemalloc.Snapshot, "compare_to", spy)
    _grow()
    assert client.get("/api/debug/memtrace/top", headers=_token()).status_code == 200
    assert held == [False]


def test_object_types_are_counted_only_when_asked(client, monkeypatch):
    # gc.get_objects() walks the whole heap; the default census never does.
    from agent_friday.routes import memtrace

    def refuse():
        raise AssertionError("walked the heap without being asked")
    client.post("/api/debug/memtrace/start", headers=_token())
    monkeypatch.setattr(memtrace.gc, "get_objects", refuse)
    r = client.get("/api/debug/memtrace/top", headers=_token())
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["gc_types"] == []


class _FakeTimer:
    """Records the auto-stop timer instead of running it; ``fire`` runs it."""

    made: list = []

    def __init__(self, interval, fn, args=(), kwargs=None):
        self.interval, self.fn, self.cancelled, self.daemon = interval, fn, False, False
        self.args, self.kwargs = tuple(args), dict(kwargs or {})
        _FakeTimer.made.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True

    def fire(self):
        if not self.cancelled:
            self.fn(*self.args, **self.kwargs)


@pytest.fixture
def fake_timer(monkeypatch):
    from agent_friday.routes import memtrace
    _FakeTimer.made = []
    monkeypatch.setattr(memtrace, "_Timer", _FakeTimer, raising=False)
    return _FakeTimer


def test_the_census_traces_one_frame_unless_asked_for_more(client, fake_timer):
    r = client.post("/api/debug/memtrace/start", headers=_token())
    assert r.status_code == 200
    assert r.get_json()["frames"] == 1
    assert tracemalloc.get_traceback_limit() == 1
    client.post("/api/debug/memtrace/stop", headers=_token())
    r = client.post("/api/debug/memtrace/start?frames=25", headers=_token())
    assert r.get_json()["frames"] == 25
    client.post("/api/debug/memtrace/stop", headers=_token())
    r = client.post("/api/debug/memtrace/start?frames=500", headers=_token())
    assert r.get_json()["frames"] == 25


def test_tracing_stops_by_itself_after_fifteen_minutes(client, fake_timer):
    r = client.post("/api/debug/memtrace/start", headers=_token())
    assert r.status_code == 200
    assert tracemalloc.is_tracing()
    assert fake_timer.made, "no auto-stop timer was armed"
    timer = fake_timer.made[-1]
    assert timer.interval == 15 * 60
    assert r.get_json()["stops_in_seconds"] == 15 * 60
    timer.fire()
    assert not tracemalloc.is_tracing()
    r = client.get("/api/debug/memtrace/top", headers=_token())
    assert r.status_code == 409


def test_a_stop_by_hand_disarms_the_timer(client, fake_timer):
    client.post("/api/debug/memtrace/start?minutes=2", headers=_token())
    timer = fake_timer.made[-1]
    assert timer.interval == 120
    r = client.post("/api/debug/memtrace/stop", headers=_token())
    assert r.status_code == 200 and r.get_json()["was_running"] is True
    assert timer.cancelled
    # A timer from an earlier run never stops a later one.
    client.post("/api/debug/memtrace/start", headers=_token())
    timer.cancelled = False
    timer.fire()
    assert tracemalloc.is_tracing()
