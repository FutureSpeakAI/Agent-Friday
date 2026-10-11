"""Recording never starts by itself.

Starting a recording turns on a microphone and, for a call, the speakers'
loopback, near other people. It happens only when the owner presses start on
screen and confirms, and a voice "start recording" only opens that card. These
tests read the source, so a new code path that starts capture fails here
before it ships.
"""
from __future__ import annotations

import ast
import time
from pathlib import Path

import pytest

from agent_friday.services import meeting_capture as mc

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "agent_friday"
ROUTE = SRC / "routes" / "meetings.py"


def _py_files():
    for p in sorted(SRC.rglob("*.py")):
        if "__pycache__" not in p.parts:
            yield p


def _parse(p: Path) -> ast.AST:
    return ast.parse(p.read_text(encoding="utf-8-sig"), filename=str(p))


def _rel(p: Path) -> str:
    return p.relative_to(SRC).as_posix()


def _calls(tree):
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)]


# ── who may start a recording ───────────────────────────────────────────────

def test_only_the_start_route_passes_consent_to_the_recorder():
    """`consent_ack` is the recorder's start argument. A call that supplies it
    is a start; there must be exactly one place."""
    users = {_rel(p) for p in _py_files()
             if any(k.arg == "consent_ack" for c in _calls(_parse(p)) for k in c.keywords)}
    assert users == {"routes/meetings.py"}, users


def test_only_the_start_route_calls_start_on_the_meeting_manager():
    offenders = []
    for p in _py_files():
        text = p.read_text(encoding="utf-8-sig")
        if "meeting_capture" not in text and "MeetingManager" not in text:
            continue
        for c in _calls(ast.parse(text)):
            f = c.func
            if isinstance(f, ast.Attribute) and f.attr == "start":
                recv = ast.unparse(f.value)
                if "get_manager" in recv or "MeetingManager" in recv or recv in ("mc", "m", "manager"):
                    offenders.append((_rel(p), c.lineno, recv))
    assert [(f, r) for f, _l, r in offenders] == [("routes/meetings.py", "mc.get_manager()")], offenders


def test_the_start_route_forwards_consent_and_confirm_exactly_as_sent():
    tree = _parse(ROUTE)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "meetings_start")
    assert ("methods=['POST']" in ast.unparse(fn.decorator_list[0])) or "POST" in ast.unparse(fn.decorator_list[0])
    start = next(c for c in _calls(fn) if isinstance(c.func, ast.Attribute) and c.func.attr == "start")
    kw = {k.arg: ast.unparse(k.value) for k in start.keywords}
    assert kw["consent_ack"] == "d.get('consent_ack') is True"
    assert kw["confirm"] == "d.get('confirm') is True"


def test_the_recorder_never_starts_itself():
    """Nothing inside the manager (a timer, the watchdog, the suggestion code)
    calls its own start."""
    tree = _parse(SRC / "services" / "meeting_capture.py")
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "MeetingManager")
    for c in _calls(cls):
        f = c.func
        if isinstance(f, ast.Attribute) and f.attr == "start":
            assert ast.unparse(f.value) != "self", "the manager must not call its own start"
    # and the module has no entry point that starts on import or on a schedule
    for node in tree.body:
        assert not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)), \
            "meeting_capture runs something at import time"


def test_the_policy_and_suggestion_code_cannot_reach_start():
    tree = _parse(SRC / "services" / "meeting_policy.py")
    for c in _calls(tree):
        f = c.func
        assert not (isinstance(f, ast.Attribute) and f.attr == "start"), ast.unparse(c)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "get_manager" not in names


def test_the_suggestion_routes_never_call_the_recorders_start():
    tree = _parse(ROUTE)
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
        if fn.name.startswith("meetings_suggestion"):
            for c in _calls(fn):
                assert not (isinstance(c.func, ast.Attribute) and c.func.attr == "start"), fn.name


# ── what the recorder itself refuses ────────────────────────────────────────

class Src:
    kind = "microphone"
    error = ""

    def __init__(self, max_s):
        pass

    def start(self, on_pcm):
        pass

    def stop(self):
        pass


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(mc, "_root", lambda: tmp_path / "meetings")
    made = []

    def factory(max_s, **k):
        made.append(1)
        return Src(max_s)
    m = mc.MeetingManager(source_factory=factory, transcriber=lambda pcm: "",
                          asr_check=lambda: "", lock_detector=lambda: None,
                          event_lookup=lambda: {"title": "Design review", "event_id": "e1"},
                          notifier=lambda *a: None, scheduler=lambda mid: None,
                          partials_factory=lambda: None, ledger=lambda *a, **k: None,
                          retention=lambda: "", tick_s=0.01)
    m.made = made
    yield m
    try:
        m.stop()
    except mc.MeetingError:
        pass
    m.wait_idle(5)


@pytest.mark.parametrize("confirm", [False, None, "yes", 1, "true", 0])
def test_consent_without_the_owners_confirm_records_nothing(manager, tmp_path, confirm):
    with pytest.raises(mc.MeetingError) as e:
        manager.start(consent_ack=True, confirm=confirm)
    assert e.value.code == "confirm_required"
    assert manager.made == [], "not even a source was created"
    assert manager.status()["recording"] is False
    assert not (tmp_path / "meetings").exists() or not any((tmp_path / "meetings").iterdir())


@pytest.mark.parametrize("ack", [False, None, "yes", 1])
def test_a_confirm_without_the_consent_statement_records_nothing(manager, ack):
    with pytest.raises(mc.MeetingError) as e:
        manager.start(consent_ack=ack, confirm=True)
    assert e.value.code == "consent_required" and manager.made == []


def test_asking_for_system_audio_needs_the_same_confirm(manager):
    with pytest.raises(mc.MeetingError):
        manager.start(consent_ack=True, system_audio=True)
    assert manager.made == []


def test_an_idle_recorder_stays_idle_through_every_read_and_suggestion(manager):
    """Status, suggest, live, list and the calendar lookup never record."""
    manager.status()
    manager.live()
    manager.list()
    manager.event_lookup()
    time.sleep(0.05)
    assert manager.made == [] and manager.status()["recording"] is False


# ── voice and the page ──────────────────────────────────────────────────────

def test_a_voice_start_recording_only_opens_the_start_card(monkeypatch):
    from agent_friday.services import desktop_targets as dt

    def no_manager():
        raise AssertionError("opening the card must not touch the recorder")
    monkeypatch.setattr(mc, "get_manager", no_manager)
    for phrase in ("start recording", "record this meeting", "take notes"):
        r = dt.resolve_calendar(query=phrase)
        assert r["ok"] and r["target"] == {"workspace": "calendar", "view": "meetings",
                                           "start": "confirm"}, phrase
        assert "nothing is recorded until you confirm" in r["label"]


def test_the_page_sends_confirm_only_from_the_start_button():
    """In both UI files `confirm:true` appears once, in the start card's own
    request, and the nav target `start:'confirm'` only opens the card."""
    index = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    app = (ROOT / "ui_parts" / "app.html").read_text(encoding="utf-8-sig")
    app = app[app.index("// \u2550\u2550\u2550 MEETINGS ("):app.index("// \u2550\u2550\u2550 DRAFT PREVIEW HELPERS")]
    index = index[index.index("// \u2550\u2550\u2550 MEETINGS ("):index.index("// \u2550\u2550\u2550 DRAFT PREVIEW HELPERS")]
    assert app.count("confirm:true") == 1 and index.count("confirm: true") == 1
    start = app[app.index("const start=async()=>"):]
    start = start[:start.index("const stop=")]
    assert "confirm:true" in start
    nav = app[app.index("const apply=t=>"):]
    nav = nav[:nav.index("apply(window.__fridayNavTarget)")]
    assert "ask()" in nav and "start()" not in nav.replace("ask()", "")
