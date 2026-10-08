"""Independent browser authority and input routing, without a browser process.

The page doubles record inputs instead of importing an OS automation library.
Real page/form regressions live in test_browser_session.py.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from types import SimpleNamespace
import threading
import traceback

import pytest

from agent_friday.services import browser_session as bs


def allow(_owner, *, purpose="act"):
    return True


def owner(actor="worker-a", **changes):
    return replace(bs.BrowserOwner(actor_id=actor, conversation_id="chat-a",
                                  task_id="task-a", project_id="project-a",
                                  conversation_project_id="project-a",
                                  profile_revision=1, room_revision=1,
                                  privacy_generation=3, permission_generation=2), **changes)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    monkeypatch.setattr(bs, "_OWNED", {})
    monkeypatch.setattr(bs, "_OWNER_SURFACES", {})
    monkeypatch.setattr(bs, "_BLOCKED_OWNERS", set())
    monkeypatch.setattr(bs, "_STOP_PERMISSION_FLOOR", -1)
    monkeypatch.setattr(bs, "_STOP_EPOCH", 0)
    monkeypatch.setattr(bs, "_SESSION", None)
    yield
    for session in list(bs._OWNED.values()):
        if not session._worker._stopped:
            session.shutdown()


def create(identity=None, validator=allow):
    identity = identity or owner()
    status = bs.create_owned_session(identity, validator, label="Researcher")
    return bs._OWNED[status["surface_id"]]


class Pointer:
    def __init__(self):
        self.calls = []

    def move(self, x, y, **kwargs):
        self.calls.append(("move", x, y))

    def click(self, x, y):
        self.calls.append(("click", x, y))

    def wheel(self, x, y):
        self.calls.append(("wheel", x, y))


class Keyboard:
    def __init__(self):
        self.calls = []

    def press(self, key):
        self.calls.append(("key", key))

    def insert_text(self, text):
        self.calls.append(("text", text))


class Page:
    url = "https://example.test/work"

    def __init__(self):
        self.mouse = Pointer()
        self.keyboard = Keyboard()
        self.captures = 0
        self.on_capture = None

    def title(self):
        return "Example work"

    def screenshot(self, **kwargs):
        self.captures += 1
        if self.on_capture:
            self.on_capture()
        return b"synthetic-jpeg-fixture"


def page_for(s):
    s.running = True
    s.page_generation = 1
    s._page = Page()
    s._check_landing = lambda: None
    return s._page


def frame_for(s):
    s._frame_at = 0
    return bs.owned_frame(s.surface_id, s.generation, allow)


def event_for(frame, **changes):
    return {"type": "click", "x": 120, "y": 80,
            "page_generation": frame["page_generation"],
            "frame_sequence": frame["frame_sequence"], **changes}


def test_owner_binding_never_resolves_another_agent_or_legacy_browser():
    a, b = create(), create(owner("worker-b"))
    legacy = object()
    bs._SESSION = legacy
    with bs.bind_owner(a.owner, allow):
        assert bs.session() is a and bs.current() is a
    with bs.bind_owner(b.owner, allow):
        assert bs.session() is b and bs.current() is b
    with bs.bind_owner(owner("worker-c"), allow):
        assert bs.session(create=False) is None
    assert bs.current() is legacy
    assert a.profile != b.profile and a._worker is not b._worker
    assert a.headless and b.headless


@pytest.mark.parametrize("field,value", [
    ("conversation_id", "chat-other"), ("task_id", "task-other"),
    ("project_id", "project-other"), ("conversation_project_id", "project-other"),
    ("profile_revision", 2), ("room_revision", 2),
    ("privacy_generation", 4), ("permission_generation", 3),
])
def test_every_authority_component_selects_a_distinct_scope(field, value):
    s = create()
    with bs.bind_owner(replace(s.owner, **{field: value}), allow):
        assert bs.session(create=False) is None


def test_failed_validator_does_not_fall_back_to_legacy():
    bs._SESSION = object()
    with pytest.raises(bs.BrowserRefused):
        with bs.bind_owner(owner(), lambda *_a, **_k: False):
            bs.session()
    assert not bs._OWNED


def test_agent_workers_do_not_serialize_unrelated_work():
    a, b = create(), create(owner("worker-b"))
    entered, release = threading.Event(), threading.Event()
    def hold():
        entered.set()
        assert release.wait(2)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(a.call, hold)
        try:
            assert entered.wait(1)
            assert b.call(lambda: "other-agent-work", timeout=0.5) == "other-agent-work"
        finally:
            release.set()
        pending.result(timeout=1)


def test_pause_invalidates_actions_already_queued(monkeypatch):
    s = create()
    entered, release, queued = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def hold():
        entered.set()
        assert release.wait(2)
    original_put = s._worker._q.put_nowait
    def signal_put(value):
        original_put(value)
        queued.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        active = pool.submit(s.call, hold)
        assert entered.wait(1)
        monkeypatch.setattr(s._worker._q, "put_nowait", signal_put)
        pending = pool.submit(s.call, lambda: calls.append("clicked"))
        try:
            assert queued.wait(1)
            status = bs.control_owned_session(s.surface_id, "pause", s.generation, allow)
            assert status["mode"] == "paused"
        finally:
            release.set()
        with pytest.raises(bs.BrowserRefused):
            active.result(timeout=1)
        with pytest.raises(bs.BrowserRefused):
            pending.result(timeout=1)
    assert calls == []


def test_expired_queued_action_is_never_dispatched():
    s = create()
    entered, release = threading.Event(), threading.Event()
    calls = []
    def hold():
        entered.set()
        assert release.wait(2)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(s.call, hold)
        try:
            assert entered.wait(1)
            with pytest.raises(bs.BrowserError, match="did not answer"):
                s.call(lambda: calls.append("late click"), timeout=0.02)
        finally:
            release.set()
        pending.result(timeout=1)
    s.call(lambda: None)
    assert calls == []


def test_completed_task_can_be_viewed_but_cannot_act_or_replay_approval():
    permitted = {"act": True, "view": True, "control": True}
    s = create(validator=lambda _owner, *, purpose: permitted[purpose])
    page_for(s)
    scope = s.call(s._approval_binding)["browser_scope"]
    permitted["act"] = False
    assert frame_for(s)["image"].startswith("data:image/jpeg;base64,")
    with pytest.raises(bs.BrowserRefused):
        s.call(lambda: "should not run")
    with pytest.raises(bs.BrowserRefused):
        bs.run_approved({"payload": {"browser_scope": scope}})


def test_approval_replay_selects_original_surface_despite_other_bound_agent(monkeypatch):
    a, b = create(), create(owner("worker-b"))
    page_for(a)
    page_for(b)
    record = {"payload": a.call(a._approval_binding)}
    monkeypatch.setattr(a, "op_run_approved", lambda rec: {"surface": a.surface_id})
    with bs.bind_owner(b.owner, allow):
        assert bs.run_approved(record) == {"surface": a.surface_id}
    bs.control_owned_session(a.surface_id, "pause", a.generation, allow)
    bs.control_owned_session(a.surface_id, "resume", a.generation, allow)
    with pytest.raises(bs.BrowserRefused):
        bs.run_approved(record)


def test_approval_binds_owner_surface_page_and_control_generation():
    s = create()
    page_for(s)
    scope = s.call(s._approval_binding)["browser_scope"]
    assert scope == {"surface_id": s.surface_id, "generation": s.generation,
                     "page_generation": s.page_generation, "owner": asdict(s.owner)}
    s.page_generation += 1
    with pytest.raises(bs.BrowserRefused, match="changed after this approval"):
        bs.run_approved({"payload": {"browser_scope": scope}})


def test_human_input_uses_its_page_mouse_and_consumes_displayed_frame():
    a, b = create(), create(owner("worker-b"))
    ap, bp = page_for(a), page_for(b)
    bs.control_owned_session(a.surface_id, "takeover", a.generation, allow)
    frame = frame_for(a)
    result = bs.owned_human_input(a.surface_id, a.generation, event_for(frame), allow)
    assert ap.mouse.calls == [("move", 120, 80), ("click", 120, 80)]
    assert bp.mouse.calls == []
    assert result["cursor"]["x"] == 120 and result["cursor"]["action"] == "click"
    with pytest.raises(bs.BrowserRefused, match="stale"):
        bs.owned_human_input(a.surface_id, a.generation, event_for(frame), allow)


def test_page_navigation_after_frame_refuses_human_coordinates():
    s = create()
    p = page_for(s)
    bs.control_owned_session(s.surface_id, "takeover", s.generation, allow)
    frame = frame_for(s)
    s.page_generation += 1
    with pytest.raises(bs.BrowserRefused, match="stale"):
        bs.owned_human_input(s.surface_id, s.generation, event_for(frame), allow)
    assert p.mouse.calls == []


def test_frame_rate_limit_reuses_frame_and_does_not_capture_again():
    s = create()
    p = page_for(s)
    first = frame_for(s)
    second = bs.owned_frame(s.surface_id, s.generation, allow)
    assert first["frame_sequence"] == second["frame_sequence"]
    assert p.captures == 1


@pytest.mark.parametrize("change", ["generation", "page"])
def test_frame_capture_never_returns_pixels_after_control_or_page_changes(change):
    s = create()
    p = page_for(s)
    def changed():
        if change == "generation":
            bs._transition(s, "pause")
        else:
            s.page_generation += 1
    p.on_capture = changed
    with pytest.raises(bs.BrowserRefused):
        frame_for(s)
    assert s._frame is None and not s._frame_usable


def test_local_owner_can_revoke_after_original_authority_expired(monkeypatch):
    permitted = {"value": True}
    s = create(validator=lambda *_a, **_k: permitted["value"])
    permitted["value"] = False
    monkeypatch.setattr(bs, "_close_owned", lambda _s: None)
    out = bs.control_owned_session(s.surface_id, "revoke", s.generation, allow)
    assert out["mode"] == "revoked"
    assert bs.list_owned_sessions(allow) == []


def test_live_resource_limit_counts_unconfirmed_shutdowns(monkeypatch):
    monkeypatch.setattr(bs, "MAX_OWNED_SESSIONS", 2)
    a, _b = create(), create(owner("worker-b"))
    bs._transition(a, "close")
    with pytest.raises(bs.BrowserRefused, match="in use"):
        create(owner("worker-c"))
    bs._close_owned(a)
    assert create(owner("worker-c")).owner.actor_id == "worker-c"


def test_cleaned_closed_owner_cannot_recreate_itself():
    s = create()
    bs._transition(s, "close")
    bs._close_owned(s)
    create(owner("worker-b"))  # Prunes the old resource, not its stopped authority.
    assert s.surface_id not in bs._OWNED
    with pytest.raises(bs.BrowserRefused, match="was stopped"):
        create(s.owner)
    assert create(replace(s.owner, permission_generation=3)).owner.permission_generation == 3


def test_stop_all_blocks_new_actors_until_explicit_permission_renewal(monkeypatch):
    from agent_friday.services import agent_workspace_permissions as permissions
    monkeypatch.setattr(permissions, "snapshot", lambda: {"generation": 2})
    monkeypatch.setattr(bs, "_schedule_close", lambda _s: None)
    s = create()
    bs.stop_all_owned_sessions()
    assert s.mode == "revoked"
    with pytest.raises(bs.BrowserRefused, match="was stopped"):
        create(owner("new-worker"))
    assert create(owner("new-worker", permission_generation=3)).owner.actor_id == "new-worker"


def test_stop_all_wins_creation_waiting_on_authority_validation(monkeypatch):
    from agent_friday.services import agent_workspace_permissions as permissions
    monkeypatch.setattr(permissions, "snapshot", lambda: {"generation": 2})
    entered, release = threading.Event(), threading.Event()
    def delayed(_owner, *, purpose):
        entered.set()
        assert release.wait(2)
        return True
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(bs.create_owned_session, owner(), delayed)
        try:
            assert entered.wait(1)
            bs.stop_all_owned_sessions()
        finally:
            release.set()
        with pytest.raises(bs.BrowserRefused, match="was stopped"):
            pending.result(timeout=1)
    assert bs._OWNED == {}


def test_pending_frame_and_human_input_cannot_replace_each_others_epoch():
    s = create()
    p = page_for(s)
    bs.control_owned_session(s.surface_id, "takeover", s.generation, allow)
    frame = frame_for(s)
    s._frame_pending = True
    with pytest.raises(bs.BrowserRefused, match="stale"):
        bs.owned_human_input(s.surface_id, s.generation, event_for(frame), allow)
    s._frame_pending = False
    s._input_pending = True
    with pytest.raises(bs.BrowserRefused, match="input is running"):
        frame_for(s)
    assert p.mouse.calls == []


def test_manual_text_failure_does_not_expose_browser_argument_diagnostics(monkeypatch):
    s = create()
    p = page_for(s)
    bs.control_owned_session(s.surface_id, "takeover", s.generation, allow)
    frame = frame_for(s)
    marker = "synthetic-private-entry"
    def rejected(text):
        raise RuntimeError("browser diagnostic echoed " + text)
    monkeypatch.setattr(p.keyboard, "insert_text", rejected)
    with pytest.raises(bs.BrowserRefused) as caught:
        bs.owned_human_input(s.surface_id, s.generation,
                             event_for(frame, type="text", text=marker), allow)
    rendered = "".join(traceback.format_exception(caught.value))
    assert marker not in str(caught.value) and marker not in rendered


def test_owned_sign_in_notice_names_takeover_controls_and_legacy_keeps_its_window(monkeypatch):
    notices = []
    monkeypatch.setattr(bs, "_notify", lambda title, body, *args: notices.append((title, body)))
    snapshot = {"has_password": True, "url": "https://example.test/login",
                "title": "Synthetic sign-in", "elements": [], "text": "Sign in"}
    s = create()
    result = bs._read_out(s, snapshot)
    for text in (result, notices[0][1]):
        assert "Agent workspaces" in text and "Researcher" in text
        assert "Take control" in text and "Let agent continue" in text
        assert "browser window" not in text and "purple Friday banner" not in text
    assert notices[0][0] == "Researcher needs you to sign in"
    legacy = SimpleNamespace(owner=None, _signin_noted=set())
    legacy_result = bs._read_out(legacy, snapshot)
    assert "purple Friday banner" in legacy_result
    assert "Friday browser window" in notices[1][1]
    assert "Agent workspaces" not in legacy_result


def test_profile_preparation_excludes_construction_but_not_browser_start(monkeypatch, tmp_path):
    """A shared parent cannot appear midway through another profile's resolution."""
    import sys
    from types import ModuleType

    preparing = threading.Event()
    release_preparation = threading.Event()
    constructor_attempted = threading.Event()
    constructor_validated = threading.Event()
    constructor_done = threading.Event()
    launch_probe_acquired = threading.Event()
    constructor_thread = []
    constructor_blocked = []

    class ObservedLock:
        def __init__(self):
            self.inner = threading.RLock()

        def __enter__(self):
            if (constructor_thread == [threading.get_ident()]
                    and not constructor_attempted.is_set()):
                acquired = self.inner.acquire(blocking=False)
                constructor_blocked.append(not acquired)
                constructor_attempted.set()
                if acquired:
                    return self
            assert self.inner.acquire(timeout=5), "Profile registry lock did not become available"
            return self

        def __exit__(self, *_args):
            self.inner.release()

    class BrowserStartReached(Exception):
        pass

    first = create(owner("worker-a"))
    lock = ObservedLock()
    monkeypatch.setattr(bs, "_LOCK", lock)
    original_prepare = bs._prepare_profile
    original_validate = bs.assert_dedicated

    def prepare(path):
        assert path == first.profile
        preparing.set()
        assert release_preparation.wait(5), "Preparation was never released"
        original_prepare(path)

    def validate(path):
        if constructor_thread == [threading.get_ident()]:
            constructor_validated.set()
        return original_validate(path)

    def construct_second():
        constructor_thread.append(threading.get_ident())
        try:
            return create(owner("worker-b"))
        finally:
            constructor_done.set()

    def start_without_browser():
        # An overbroad fix holding the registry through browser startup would
        # prevent the already-waiting constructor from finishing here.
        assert constructor_done.wait(5), "Browser startup still holds the profile registry"

        def probe():
            with lock:
                launch_probe_acquired.set()

        probe_thread = threading.Thread(target=probe, daemon=True)
        probe_thread.start()
        try:
            assert launch_probe_acquired.wait(5), "Registry lock spans browser startup"
        finally:
            probe_thread.join(timeout=5)
        assert not probe_thread.is_alive()
        raise BrowserStartReached

    fake_package = ModuleType("playwright")
    fake_package.__path__ = []
    fake_api = ModuleType("playwright.sync_api")
    fake_api.sync_playwright = lambda: SimpleNamespace(start=start_without_browser)
    fake_package.sync_api = fake_api
    monkeypatch.setitem(sys.modules, "playwright", fake_package)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_api)
    monkeypatch.setattr(bs, "_prepare_profile", prepare)
    monkeypatch.setattr(bs, "assert_dedicated", validate)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            starting = pool.submit(first.call, first._start, timeout=20)
            constructing = None
            try:
                assert preparing.wait(5), "Owned worker never reached profile preparation"
                constructing = pool.submit(construct_second)
                assert constructor_attempted.wait(5), "Second constructor never reached the registry"
                assert constructor_blocked == [True], "Profile construction overlapped parent creation"
                assert not constructor_validated.is_set()
            finally:
                release_preparation.set()
                # Consume both futures even when the ordering assertion fails.
                try:
                    with pytest.raises(BrowserStartReached):
                        starting.result(timeout=20)
                finally:
                    if constructing is not None:
                        second = constructing.result(timeout=10)
            assert constructor_validated.is_set() and launch_probe_acquired.is_set()
            assert first.profile != second.profile
            for session in (first, second):
                assert session.profile.is_relative_to(tmp_path.resolve())
                assert original_validate(session.profile) == session.profile
    finally:
        release_preparation.set()
        for session in list(bs._OWNED.values()):
            if session.mode not in ("closed", "revoked"):
                bs._transition(session, "close")
            bs._close_owned(session)
            session._worker.thread.join(timeout=5)
            assert session._cleanup_done and not session._worker.thread.is_alive()
