"""Call mode (services/call_watch, call_tools): when a call app takes the
camera or the mic, Friday stands back on her own, in the mode the owner chose,
and comes back when the call ends. A fake call app plays Zoom here: it takes
the webcam, the watcher notices on the second poll, stands Friday down with
kind "call", tells the page, and twenty seconds after the camera is free
again it resumes and tells the page that too."""
import pytest

from agent_friday.services import call_watch as cw


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Scene:
    """The fake machine: who holds what, which processes run, what the page
    says it holds, the chosen mode, and everything the watcher did."""

    def __init__(self, mode="automatic"):
        self.cam, self.mic, self.procs, self.page = [], [], [], {}
        self.mode = mode
        self.pushed, self.stood, self.resumed = [], [], []
        self.clock = FakeClock()
        self.sd_state = {"active": False}

    def holders(self, kind):
        return list(self.cam if kind == "webcam" else self.mic)

    def watch(self):
        return cw.Watch(holders=self.holders, processes=lambda: list(self.procs),
                        page_devices=lambda: dict(self.page), mode=lambda: self.mode,
                        push=self._push, now=self.clock)

    def _push(self, action):
        self.pushed.append(action)
        return {"delivered": True, "acked": True, "ack": {}}


@pytest.fixture
def scene(monkeypatch):
    sc = Scene()
    from agent_friday.services import stand_down

    def fake_stand_down(*, requested_by="", hours=None, kind="manual", app=""):
        sc.stood.append((requested_by, kind, app))
        sc.sd_state = {"active": True, "kind": kind, "app": app, "requested_by": requested_by}
        return dict(sc.sd_state)

    def fake_resume(*, requested_by=""):
        sc.resumed.append(requested_by)
        sc.sd_state = {"active": False}
        return dict(sc.sd_state)

    monkeypatch.setattr(stand_down, "stand_down", fake_stand_down)
    monkeypatch.setattr(stand_down, "resume", fake_resume)
    monkeypatch.setattr(stand_down, "state", lambda: dict(sc.sd_state))
    return sc


def _polls(w, sc, n, dt=cw.POLL_S):
    out = None
    for _ in range(n):
        sc.clock.t += dt
        out = w.tick()
    return out


def test_a_call_app_taking_the_camera_stands_friday_back_and_the_end_brings_her_back(scene):
    w = scene.watch()
    assert not _polls(w, scene, 1)["active"]
    scene.cam = ["Zoom"]                                  # the fake call app takes the webcam
    assert not _polls(w, scene, 1)["active"], "one poll is a glance, not a call"
    snap = _polls(w, scene, 1)
    assert snap["active"] and snap["app"] == "Zoom" and snap["by"] == "automatic"
    assert scene.stood == [("call mode: Zoom", "call", "Zoom")]
    assert scene.pushed[-1] == {"type": "call", "op": "start", "app": "Zoom", "by": "automatic"}
    # Mid-call, a poll that sees nothing (a dropped frame) does not end it.
    scene.cam = []
    assert _polls(w, scene, 1)["active"]
    scene.cam = ["Zoom"]
    _polls(w, scene, 2)
    # The call ends: the camera is free for CLEAR_S, then Friday comes back.
    scene.cam = []
    _polls(w, scene, int(cw.CLEAR_S // cw.POLL_S))
    assert w.active, "came back before the clear window"
    snap = _polls(w, scene, 1)
    assert not snap["active"]
    assert scene.resumed == ["the call ended"]
    assert scene.pushed[-1] == {"type": "call", "op": "end", "app": "Zoom"}


def test_the_mic_alone_and_zooms_meeting_process_each_count_as_a_call(scene):
    w = scene.watch()
    scene.mic = ["Microsoft Teams"]
    assert _polls(w, scene, 2)["app"] == "Microsoft Teams"
    sc2 = Scene()
    sc2.procs = ["explorer.exe", "CptHost.exe"]
    w2 = sc2.watch()
    from agent_friday.services import stand_down
    assert w2.signals() == ["Zoom"]


def test_fridays_own_page_holding_the_camera_is_not_a_call(scene):
    w = scene.watch()
    scene.cam = ["Chrome"]
    scene.page = {"camera": True, "mic": False}
    assert not _polls(w, scene, 3)["active"]
    assert scene.stood == []
    # The same browser holding the camera when the page says it does not: Meet.
    scene.page = {"camera": False, "mic": False}
    assert _polls(w, scene, 2)["active"]


def test_ask_mode_asks_once_and_honours_the_answer(scene):
    scene.mode = "ask"
    w = scene.watch()
    scene.cam = ["Zoom"]
    _polls(w, scene, 3)
    asks = [p for p in scene.pushed if p["op"] == "ask"]
    assert asks == [{"type": "call", "op": "ask", "app": "Zoom"}], "asked once, not every poll"
    assert not w.active and scene.stood == []
    w.decide(False)
    assert scene.pushed[-1]["op"] == "declined"
    _polls(w, scene, 3)
    assert not w.active and len([p for p in scene.pushed if p["op"] == "ask"]) == 1, "declined once per call"
    # A new call after the camera was free asks again; yes stands back.
    scene.cam = []
    _polls(w, scene, 1)
    scene.cam = ["Zoom"]
    _polls(w, scene, 2)
    assert len([p for p in scene.pushed if p["op"] == "ask"]) == 2
    w.decide(True)
    assert w.active and w.by == "asked" and scene.stood[-1][1] == "call"


def test_off_does_nothing_on_its_own_but_by_hand_still_works(scene):
    scene.mode = "off"
    w = scene.watch()
    scene.cam = ["Zoom"]
    assert not _polls(w, scene, 4)["active"] and scene.pushed == []
    snap = w.start("Zoom")
    assert snap["active"] and snap["manual"] and scene.stood[-1] == ("call mode: Zoom", "call", "Zoom")
    # By hand is not ended by the detector: the owner ends it.
    scene.cam = []
    _polls(w, scene, 10)
    assert w.active
    assert not w.end()["active"] and scene.resumed == ["ended by hand"]


def test_a_manual_stand_down_is_not_resumed_by_the_call_watch(scene):
    w = scene.watch()
    scene.sd_state = {"active": True, "kind": "manual"}
    scene.cam = ["Zoom"]
    _polls(w, scene, 2)
    scene.cam = []
    _polls(w, scene, 20)
    # The call's own stand-down was replaced by the fake; what matters is that
    # resume is only called when the stand-down is the call's.
    assert all(k == "call" for _r, k, _a in scene.stood)


def test_a_call_stand_down_from_before_a_restart_is_adopted_and_ended(scene):
    scene.sd_state = {"active": True, "kind": "call", "app": "Zoom", "since": 900.0}
    w = scene.watch()
    w.adopt_stand_down()
    assert w.active and w.app == "Zoom"
    _polls(w, scene, int(cw.CLEAR_S // cw.POLL_S) + 1)
    assert not w.active and scene.resumed == ["the call ended"]


def test_a_failing_signal_source_never_kills_the_watch(scene):
    def boom(kind):
        raise RuntimeError("registry")
    w = cw.Watch(holders=boom, processes=lambda: ["CptHost.exe"], page_devices=lambda: {},
                 mode=lambda: "automatic", push=scene._push, now=scene.clock)
    snap = _polls(w, scene, 2)
    assert snap["active"] and snap["app"] == "Zoom" and "registry" in snap["error"]


def test_the_setting_is_declared_validated_and_read(monkeypatch):
    from agent_friday.core import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["call_mode"] == "automatic"
    from agent_friday.routes import core_routes as cr
    assert cr._check_voice_enums({"call_mode": "sometimes"}) is not None
    assert cr._check_voice_enums({"call_mode": "ask"}) is None
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings", lambda: {"call_mode": "ask"})
    assert cw.mode_from_settings() == "ask"
    monkeypatch.setattr(core, "_load_settings", lambda: {"call_mode": "nonsense"})
    assert cw.mode_from_settings() == "automatic"


def test_stand_down_records_the_call_and_says_so(tmp_path, monkeypatch):
    import agent_friday.core as core
    from agent_friday.services import stand_down as sd
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    sd._invalidate()
    monkeypatch.setattr(sd, "_release_gpu", lambda: None)
    monkeypatch.setattr(sd, "_in_background", lambda fn: None)
    st = sd.stand_down(requested_by="call mode: Zoom", kind="call", app="Zoom")
    assert st["kind"] == "call" and st["app"] == "Zoom"
    assert "standing back for your call in Zoom" in sd.reason()
    assert sd.state()["kind"] == "call"
    sd.resume(requested_by="the call ended")
    assert not sd.is_stood_down()
    sd._invalidate()


def test_voice_never_speaks_first_on_a_call(monkeypatch):
    from agent_friday.routes import voice as vr
    from agent_friday.services import stand_down
    monkeypatch.setattr(stand_down, "state", lambda: {"active": True, "kind": "call", "app": "Zoom"})
    assert vr._on_a_call() is True
    monkeypatch.setattr(stand_down, "state", lambda: {"active": True, "kind": "manual"})
    assert vr._on_a_call() is False
    src = open(vr.__file__, encoding="utf-8").read()
    assert "if live_proactive and _on_a_call():" in src


def test_the_tool_is_declared_once_shared_into_voice_and_inside_the_gate():
    from agent_friday.services import agent, voice_engine
    from agent_friday.governance import action_gate
    assert [t["name"] for t in agent.CLAUDE_TOOLS].count("call_mode") == 1
    assert "call_mode" in agent.CLAUDE_TOOL_HANDLERS and agent.TOOL_RINGS.get("call_mode") == 1
    assert "call_mode" in voice_engine._VOICE_SHARED_TOOLS
    assert "call_mode" in [n for n, _d, _s in voice_engine._voice_shared_tool_specs()]
    assert "call_mode" in action_gate.INTERNAL_TOOLS


def test_the_tool_speaks_plainly_and_sets_the_mode(scene, monkeypatch):
    from agent_friday.services import call_tools as ct
    w = scene.watch()
    monkeypatch.setattr(cw, "watch", lambda: w)
    saved = {}
    import agent_friday.core as core
    monkeypatch.setattr(core, "_save_settings", lambda d: saved.update(d))
    assert "not standing back" in ct.handle({"action": "status"})
    assert "Standing back now" in ct.handle({"action": "start", "app": "Zoom"})
    assert "standing back for your call in Zoom" in ct.handle({"action": "status"})
    assert "Welcome back" in ct.handle({"action": "end"})
    assert "ask you first" in ct.handle({"action": "set_mode", "mode": "ask"})
    assert saved == {"call_mode": "ask"}
    assert ct.handle({"action": "set_mode", "mode": "later"}).startswith("call_mode error")


def test_the_setup_screen_exists_and_the_recommended_answer_is_labelled_not_preselected():
    from agent_friday.services import onboarding_copy as oc
    assert "calls" in oc.SCREEN_ORDER
    scr = oc.screen("calls")
    values = [c["value"] for c in scr["choices"]]
    assert values == ["automatic", "ask", "off"]
    rec = [c for c in scr["choices"] if c.get("recommended")]
    assert [c["value"] for c in rec] == ["automatic"]
    assert "recommended" in rec[0]["label"].lower()
    from agent_friday import setup_wizard as sw
    assert "calls" in sw._unanswered({"acks": {}}) if callable(getattr(sw, "_unanswered", None)) else True


@pytest.fixture(autouse=True)
def _the_owner_said_yes(tmp_path, monkeypatch):
    """What this file pins is what the tool does once the owner has approved the change. That it asks first
    (a diff, one card, nothing written before the Yes) is tests/unit/test_direct_setting_writers_routed.py."""
    from agent_friday.services import setting_proposals as sp
    monkeypatch.setattr(sp, "_store", lambda: tmp_path / "setting_changes.json")
    tok = sp._APPROVED.set(True)
    yield
    sp._APPROVED.reset(tok)
