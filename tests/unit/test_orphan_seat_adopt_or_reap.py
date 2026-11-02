"""A seat that outlived its parent is adopted or reaped, never duplicated.

Release lead, 2026-11-01 19:40: a llama-server started during a refused deploy
(bonsai2:27b on :8090) outlived the process that spawned it. It stayed published
in residency/endpoints.json but sat in no Arbiter's `procs`, so `serving()` never
adopted it (adoption ran only when nothing was published), and on the next restart
the Arbiter started a SECOND llama-server on :8090, which could not bind while the
first kept its VRAM. Two deploys were refused and the display reserve was breached.

Now, before a seat is started on a port:
  * the model we want, matching what we would start, already answering there is
    adopted;
  * a seat of ours there that does not match (over its context cap, another model
    file, another model) is reaped through the Arbiter first;
  * a port held by something that is not a Friday seat is refused;
and serving() adopts a published, answering seat no Arbiter owns.
All machine access is simulated: no process is started, listed or stopped.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_friday.services import residency_arbiter as ra

MODEL = "bonsai2:27b"
GGUF = "C:/Users/x/.friday/runtime/models/bonsai2-27b-q4.gguf"
ORPHAN = 13476
PORT = 8090


class Machine:
    """What the Arbiter would see on the real machine, and what it did to it."""

    def __init__(self):
        self.listening = {}          # port -> pid
        self.serves = {}             # port -> model id answering /v1/models
        self.ctx = {}                # pid -> -c it was started with
        self.model_path = {}         # pid -> -m it was started with
        self.ours_by_path = set()    # pids loaded from Friday's model directory
        self.published = {}          # model -> base url in endpoints.json
        self.spawned = []            # (model, port) started
        self.stopped = []            # pids stopped
        self.release_port_on_stop = True
        self.killed_by_taskkill = []


@pytest.fixture
def machine(monkeypatch):
    m = Machine()
    monkeypatch.setattr(ra, "_listening_ports", lambda: dict(m.listening))
    monkeypatch.setattr(ra, "_model_on_port", lambda port: m.serves.get(int(port)))
    monkeypatch.setattr(ra, "_llama_server_pids", lambda: set(m.ours_by_path))
    monkeypatch.setattr(ra, "_seat_num_ctx", lambda pid: m.ctx.get(pid))
    monkeypatch.setattr(ra, "_seat_model_path", lambda pid: m.model_path.get(pid))
    monkeypatch.setattr(ra, "_read_published", lambda: dict(m.published))
    monkeypatch.setattr(ra, "_publish_endpoints", lambda procs, drop=(): None)
    monkeypatch.setattr(ra, "_reap_sleep", lambda s: None)
    monkeypatch.setattr(ra, "REAP_WAIT_S", 0.0)

    def _stop(self, *a, **k):
        m.stopped.append(self.pid)
        if m.release_port_on_stop:
            for port, pid in list(m.listening.items()):
                if pid == self.pid:
                    m.listening.pop(port)
                    m.serves.pop(port, None)
        return 0
    monkeypatch.setattr(ra.AdoptedProc, "terminate", _stop)
    monkeypatch.setattr(ra.AdoptedProc, "kill", _stop)
    monkeypatch.setattr(ra.AdoptedProc, "wait", lambda self, timeout=None: 0)
    monkeypatch.setattr(ra.AdoptedProc, "poll", lambda self: None)

    def _run(cmd, *a, **k):
        # adopt_or_reap stops what it reaps with taskkill; nothing else may run.
        if cmd and cmd[0] == "taskkill":
            pid = int(cmd[-1])
            m.killed_by_taskkill.append(pid)
            for port, p in list(m.listening.items()):
                if p == pid:
                    m.listening.pop(port)
                    m.serves.pop(port, None)

            class R:
                returncode, stdout, stderr = 0, "", ""
            return R()
        raise AssertionError("no real process may be touched: %r" % (cmd,))
    monkeypatch.setattr(ra.subprocess, "run", _run)
    return m


@pytest.fixture
def backend(machine, monkeypatch):
    b = ra.LlamaServerBackend()
    monkeypatch.setattr(b, "engines_for", lambda model_id: [Path("llama-server.exe")])

    def _spawn(binary, model_id, num_ctx, *, port, **kw):
        assert machine.listening.get(port) is None, \
            "a second llama-server was started on occupied :%d" % port
        machine.spawned.append((model_id, port))
        machine.listening[port] = 99999
        machine.serves[port] = model_id
        return 1.0
    monkeypatch.setattr(b, "_spawn", _spawn)
    return b


def _orphan(machine, *, ctx=None, model=MODEL, published=True, ours_by_path=False):
    machine.listening[PORT] = ORPHAN
    machine.serves[PORT] = model
    if ctx:
        machine.ctx[ORPHAN] = ctx
    if published:
        machine.published[model] = "http://127.0.0.1:%d/v1" % PORT
    if ours_by_path:
        machine.ours_by_path.add(ORPHAN)


def test_a_matching_orphan_the_survey_missed_is_adopted_not_duplicated(backend, machine, monkeypatch):
    """The seat answers on its published port but the survey did not return it
    (it was loading, or the scan failed): the load adopts it at the port."""
    _orphan(machine)
    monkeypatch.setattr(ra, "survey_live_seats", lambda *a, **k: {})
    took = backend.load(MODEL, 32768, gguf_path=GGUF, port=PORT)
    assert took == 0.0 and machine.spawned == []
    assert backend.procs[MODEL][1] == PORT and backend.procs[MODEL][0].pid == ORPHAN
    assert machine.stopped == [] and machine.killed_by_taskkill == []


def test_a_non_matching_published_orphan_is_reaped_before_the_seat_starts(backend, machine):
    """Over its context cap, so it cannot be adopted; not under Friday's model
    directory either, but published by Friday as this model on this port: reaped
    through the Arbiter, then one seat is started."""
    _orphan(machine, ctx=10 ** 9)
    backend.load(MODEL, 32768, gguf_path=GGUF, port=PORT)
    assert machine.stopped and machine.stopped[0] == ORPHAN
    assert machine.spawned == [(MODEL, PORT)]


def test_an_orphan_from_another_model_file_is_reaped_not_adopted(backend, machine):
    _orphan(machine, ours_by_path=True)
    machine.model_path[ORPHAN] = "C:/Users/x/.friday/runtime/models/some-other-27b.gguf"
    backend.load(MODEL, 32768, gguf_path=GGUF, port=PORT)
    assert machine.stopped == [ORPHAN] and machine.spawned == [(MODEL, PORT)]


def test_an_orphan_that_will_not_let_go_is_never_joined_by_a_second_seat(backend, machine):
    _orphan(machine, ctx=10 ** 9)
    machine.release_port_on_stop = False
    with pytest.raises(ra.TransitionError):
        backend.load(MODEL, 32768, gguf_path=GGUF, port=PORT)
    assert machine.spawned == []


def test_a_port_held_by_something_that_is_not_a_friday_seat_is_left_alone(backend, machine):
    machine.listening[PORT] = 777
    machine.serves[PORT] = "someone-elses-model"
    with pytest.raises(ra.TransitionError) as ei:
        backend.load(MODEL, 32768, gguf_path=GGUF, port=PORT)
    assert "not a Friday seat" in str(ei.value)
    assert machine.spawned == [] and machine.stopped == []


def test_boot_reaps_a_published_orphan_it_cannot_adopt(backend, machine):
    """adopt_or_reap at boot: the wanted model's published seat, over its cap and
    loaded from outside Friday's model directory, is still recognised as ours."""
    _orphan(machine, ctx=10 ** 9)
    report = backend.adopt_or_reap({MODEL})
    assert report["adopted"] == [] and report["reaped"] == [ORPHAN]
    assert ORPHAN in machine.killed_by_taskkill


def test_boot_adopts_a_matching_published_orphan(backend, machine):
    _orphan(machine)
    report = backend.adopt_or_reap({MODEL})
    assert report["adopted"] and backend.procs[MODEL][0].pid == ORPHAN
    assert machine.killed_by_taskkill == []


def test_serving_adopts_a_published_seat_no_arbiter_owns(backend, machine, monkeypatch):
    """Adoption used to run only when nothing at all was published, so a
    published orphan was reported as serving and owned by no one."""
    from agent_friday.services import local_seats
    _orphan(machine)

    class _Arb:
        llama = backend
    monkeypatch.setattr(ra, "ARBITER", _Arb())
    monkeypatch.setattr(ra, "_endpoint_alive", lambda base: True)
    monkeypatch.setattr(local_seats, "_ADOPT_PUBLISHED", {"at": -1e9})

    def _no_daemon():
        raise RuntimeError("no Ollama daemon")
    import agent_friday.routing.ollama_manager as om
    monkeypatch.setattr(om, "get_manager", _no_daemon)
    out = local_seats.serving()
    assert out.get(MODEL) == "http://127.0.0.1:%d" % PORT
    assert MODEL in backend.procs and backend.procs[MODEL][0].pid == ORPHAN, \
        "the published seat is still owned by no Arbiter"
