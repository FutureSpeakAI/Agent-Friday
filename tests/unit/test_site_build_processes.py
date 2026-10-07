"""Owned build process discovery retains lifetimes without adopting replacements."""
import io
import subprocess
from contextlib import contextmanager
from types import SimpleNamespace

import psutil
import pytest

from agent_friday.services import site_builds
from agent_friday.services.site_builds import _capture_process_tree


class Process:
    def __init__(self, pid, born, descendants=()):
        self.pid, self.born, self.descendants = pid, born, descendants
    def create_time(self):
        return self.born
    def children(self, recursive=False):
        return self.descendants
    def is_running(self):
        return True


def test_reused_descendant_pid_keeps_both_observed_lifetimes(monkeypatch):
    child = Process(20, 3)
    parent = Process(10, 1, [child])
    monkeypatch.setattr(psutil, "Process", lambda pid: {10: parent, 20: child}[pid])
    identities, uncertain = {(10, 1), (20, 2)}, []
    _capture_process_tree(identities, uncertain)
    assert (20, 2) in identities and (20, 3) in identities and not uncertain


def test_replacement_parent_cannot_contribute_new_children(monkeypatch):
    late = Process(30, 5)
    parent, replacement = Process(10, 1, [late]), Process(10, 4)
    calls = []
    def lookup(pid):
        if pid == 10:
            calls.append(pid)
            return parent if len(calls) == 1 else replacement
        return late
    monkeypatch.setattr(psutil, "Process", lookup)
    identities, uncertain = {(10, 1)}, []
    _capture_process_tree(identities, uncertain)
    assert (30, 5) not in identities
    assert uncertain


def test_surviving_child_discovers_grandchild_after_root_exits(monkeypatch):
    grandchild = Process(30, 3)
    child = Process(20, 2, [grandchild])
    def lookup(pid):
        if pid == 10:
            raise psutil.NoSuchProcess(pid)
        return {20: child, 30: grandchild}[pid]
    monkeypatch.setattr(psutil, "Process", lookup)
    identities, uncertain = {(10, 1), (20, 2)}, []
    _capture_process_tree(identities, uncertain)
    assert (30, 3) in identities and not uncertain


def test_replacement_departing_before_parent_postcheck_cannot_donate_children(monkeypatch):
    foreign = Process(30, 5)
    parent = Process(10, 1, [foreign])
    calls = []
    def lookup(pid):
        if pid == 10:
            calls.append(pid)
            if len(calls) > 1:
                raise psutil.NoSuchProcess(pid)
            return parent
        return foreign
    monkeypatch.setattr(psutil, "Process", lookup)
    identities, uncertain = {(10, 1)}, []
    _capture_process_tree(identities, uncertain)
    assert identities == {(10, 1)}
    assert uncertain == ["unconfirmed descendant ancestry"]


def test_departing_parent_preserves_previously_captured_live_child(monkeypatch):
    child = Process(20, 2)
    parent = Process(10, 1, [child])
    calls = []
    def lookup(pid):
        if pid == 10:
            calls.append(pid)
            if len(calls) > 1:
                raise psutil.NoSuchProcess(pid)
            return parent
        return child
    monkeypatch.setattr(psutil, "Process", lookup)
    identities, uncertain = {(10, 1), (20, 2)}, []
    _capture_process_tree(identities, uncertain)
    assert identities == {(10, 1), (20, 2)} and not uncertain


class Clock:
    def __init__(self):
        self.now = 0
    def monotonic(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds


class BuildProcess:
    def __init__(self, world, pid, born, parent=None):
        self.world, self.pid, self.born, self.parent = world, pid, born, parent
        self.live, self.signals = True, []
        world.processes[pid] = self
    def create_time(self):
        if self is self.world.root and self.world.create_failures:
            self.world.create_failures -= 1
            raise psutil.AccessDenied(self.pid)
        return self.born
    def is_running(self):
        return self.live and self.world.processes.get(self.pid) is self
    def children(self, recursive=False):
        if not self.is_running():
            raise psutil.NoSuchProcess(self.pid)
        direct = [process for process in self.world.processes.values()
                  if process.parent == (self.pid, self.born) and process.is_running()]
        result = list(direct)
        if recursive:
            for process in direct:
                result.extend(process.children(recursive=True))
        return result
    def kill(self):
        if not self.is_running():
            raise psutil.NoSuchProcess(self.pid)
        self.signals.append("psutil kill")
        self.live = False


class OutputPipe(io.BytesIO):
    def __init__(self, world, content):
        super().__init__(content)
        self.world = world
    def read(self, size=-1):
        result = super().read(size)
        if not result and self.world.finish_on_eof:
            self.world.root.live = False
        return result


class OwnedPopen:
    def __init__(self, world):
        self.world, self.pid, self.returncode = world, world.root.pid, None
        self.stdout = OutputPipe(world, world.output)
        self.kills, self.waits = 0, []
    def poll(self):
        if not self.world.root.live:
            self.returncode = -1 if self.kills or self.world.root.signals else 0
        return self.returncode
    def kill(self):
        self.kills += 1
        # This is the independently owned launch handle, not a PID lookup.
        self.world.root.live = False
    def wait(self, timeout=None):
        self.waits.append(timeout)
        result = self.poll()
        if result is None:
            raise subprocess.TimeoutExpired("synthetic build", timeout)
        return result


class Reader:
    def __init__(self, world, target):
        self.world, self.target, self.ident = world, target, None
        self.live = False
    def start(self):
        if self.world.reader_failure == "start":
            raise RuntimeError("synthetic reader start failure")
        self.ident, self.live = 1, True
        if self.world.reader_failure == "after_start":
            raise RuntimeError("synthetic reader post-start failure")
        self.target()
        self.live = False
    def join(self, timeout=None):
        if self.ident is None:
            raise RuntimeError("Reader was never started")
        if self.live and not self.world.root.live:
            self.target()
            self.live = False
    def is_alive(self):
        return self.live


class BuildWorld:
    def __init__(self, monkeypatch, *, lookup_failures=0, create_failures=0,
                 reader_failure=None, output=b"synthetic output\n"):
        self.processes, self.lookup_failures, self.create_failures = {}, lookup_failures, create_failures
        self.reader_failure, self.output, self.finish_on_eof = reader_failure, output, True
        self.root = BuildProcess(self, 10, 100)
        self.child = BuildProcess(self, 20, 101, (10, 100))
        self.popen, self.launches, self.reader, self.before_lookup = None, 0, None, None
        self.clock = Clock()
        monkeypatch.setattr(psutil, "Process", self.lookup)
        monkeypatch.setattr(site_builds, "time", self.clock)
        monkeypatch.setattr(site_builds, "threading", SimpleNamespace(Thread=self.thread))
        monkeypatch.setattr(site_builds, "subprocess", SimpleNamespace(
            Popen=self.launch, PIPE=subprocess.PIPE, STDOUT=subprocess.STDOUT,
            DEVNULL=subprocess.DEVNULL, CREATE_NO_WINDOW=0,
            TimeoutExpired=subprocess.TimeoutExpired, CompletedProcess=subprocess.CompletedProcess))
    def lookup(self, pid):
        if self.before_lookup is not None:
            hook, self.before_lookup = self.before_lookup, None
            hook()
        if pid == self.root.pid and self.lookup_failures:
            self.lookup_failures -= 1
            raise psutil.AccessDenied(pid)
        process = self.processes.get(pid)
        if process is None or not process.is_running():
            raise psutil.NoSuchProcess(pid)
        return process
    def launch(self, command, **kwargs):
        self.launches += 1
        self.popen = OwnedPopen(self)
        return self.popen
    def thread(self, *, target, daemon):
        assert daemon is True
        if self.reader_failure == "construct":
            raise RuntimeError("synthetic reader construction failure")
        self.reader = Reader(self, target)
        return self.reader
    def run(self, tmp_path, **kwargs):
        return site_builds.run_process("synthetic build", cwd=tmp_path, env={}, timeout_s=5, **kwargs)
    def assert_stopped_and_closed(self):
        assert not self.root.live and not self.child.live
        assert self.popen.waits, "The owned launch must be reaped even on initialization failure"
        assert self.popen.stdout.closed
        assert self.reader is None or not self.reader.is_alive()


@pytest.mark.parametrize("failure", ["lookup", "creation_time"])
def test_initial_identity_failure_retries_owned_cleanup_and_refuses_output(monkeypatch, tmp_path, failure):
    world = BuildWorld(monkeypatch, lookup_failures=1 if failure == "lookup" else 0,
                       create_failures=1 if failure == "creation_time" else 0)
    with pytest.raises(psutil.AccessDenied):
        world.run(tmp_path)
    world.assert_stopped_and_closed()


@pytest.mark.parametrize("failure", ["construct", "start", "after_start"])
def test_reader_initialization_failure_cleans_confirmed_descendants_and_pipe(monkeypatch, tmp_path, failure):
    world = BuildWorld(monkeypatch, reader_failure=failure)
    with pytest.raises(RuntimeError, match="synthetic reader"):
        world.run(tmp_path)
    world.assert_stopped_and_closed()


def test_guard_exit_failure_after_launch_cannot_bypass_cleanup(monkeypatch, tmp_path):
    world = BuildWorld(monkeypatch)
    @contextmanager
    def guard():
        yield
        raise RuntimeError("synthetic guard exit failure")
    with pytest.raises(RuntimeError, match="synthetic guard exit failure"):
        world.run(tmp_path, guard=guard())
    world.assert_stopped_and_closed()


def test_uncaptured_root_uses_owned_handle_without_claiming_unknown_descendants(monkeypatch, tmp_path):
    world = BuildWorld(monkeypatch, lookup_failures=100)
    with pytest.raises(RuntimeError, match="not eligible for publishing.*root identity"):
        world.run(tmp_path)
    assert world.popen.kills == 1 and not world.root.live
    assert world.popen.waits and world.popen.stdout.closed
    assert world.child.live and not world.child.signals
    assert world.clock.now <= 5


def test_replacement_pid_during_initial_capture_is_never_signalled(monkeypatch, tmp_path):
    world = BuildWorld(monkeypatch)
    replacement = []
    def recycle():
        world.root.live = False
        replacement.append(BuildProcess(world, 10, 200))
        replacement.append(BuildProcess(world, 30, 201, (10, 200)))
    world.before_lookup = recycle
    with pytest.raises(RuntimeError, match="not eligible for publishing"):
        world.run(tmp_path)
    assert all(process.live and not process.signals for process in replacement)
    assert world.popen.kills == 0 and world.popen.waits and world.popen.stdout.closed


def test_failure_before_launch_creates_no_process_or_reader(monkeypatch, tmp_path):
    world = BuildWorld(monkeypatch)
    @contextmanager
    def guard():
        raise ValueError("synthetic denied launch")
        yield
    with pytest.raises(ValueError, match="synthetic denied launch"):
        world.run(tmp_path, guard=guard())
    assert world.launches == 0 and world.popen is None and world.reader is None


def test_success_reaps_root_closes_output_and_keeps_capture_bounded(monkeypatch, tmp_path):
    world = BuildWorld(monkeypatch, output=b"x" * 25000)
    result = world.run(tmp_path)
    assert result.returncode == 0 and result.stdout == "x" * 20000
    world.assert_stopped_and_closed()
