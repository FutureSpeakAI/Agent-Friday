"""Zero cloud egress of meeting content without a grant (the canary test).

A distinctive string goes into a meeting's transcript. The whole life of the
meeting runs (record, transcribe, write notes, make tasks, draft a follow-up,
sweep, delete) with every network primitive replaced by a recorder. Nothing may
try to connect, and the canary may not appear anywhere it should not: not on
disk in the clear, not in the activity ledger, not in a notification.

The notes run inside a local-only context, so a cloud provider is refused at
the transport; the same test proves that refusal is live while the notes call
is running.
"""
from __future__ import annotations

import array
import ast
import http.client
import math
import socket
import types
import urllib.request
from pathlib import Path

import pytest

from agent_friday.services import local_only_guard
from agent_friday.services import meeting_capture as mc
from agent_friday.services import meeting_policy as mp

SRC = Path(__file__).resolve().parents[2] / "src" / "agent_friday"
CANARY = "CANARY-7f3a91c2-quarterly-numbers"

#: Modules that open a connection. A meeting module that imports one of these
#: has a way out that this test did not see.
NETWORK_MODULES = {"socket", "ssl", "http", "urllib", "urllib3", "requests", "httpx", "aiohttp",
                   "websocket", "websockets", "smtplib", "ftplib", "imaplib", "poplib",
                   "telnetlib", "xmlrpc", "grpc", "boto3", "botocore", "paramiko",
                   "google", "openai", "anthropic"}


def _imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            out.append(n.module)
    return out


@pytest.mark.parametrize("rel", ["services/meeting_capture.py", "services/meeting_audio.py",
                                 "services/meeting_policy.py", "routes/meetings.py"])
def test_no_meeting_module_imports_a_network_library(rel):
    bad = [m for m in _imports(SRC / rel) if m.split(".")[0] in NETWORK_MODULES]
    assert bad == [], (rel, bad)


def test_meeting_code_never_names_a_cloud_provider_or_a_url():
    for rel in ("services/meeting_capture.py", "services/meeting_audio.py",
                "services/meeting_policy.py", "routes/meetings.py"):
        text = (SRC / rel).read_text(encoding="utf-8-sig")
        assert "http://" not in text and "https://" not in text, rel
        for name in ("openai", "anthropic", "gemini", "elevenlabs"):
            assert name not in text.lower(), (rel, name)


# ── a whole meeting, with the network watched ───────────────────────────────

@pytest.fixture
def watched(monkeypatch):
    attempts = []

    def trip(*a, **k):
        attempts.append(("network", a[:2]))
        raise AssertionError("a meeting tried to reach the network")
    monkeypatch.setattr(socket.socket, "connect", trip)
    monkeypatch.setattr(socket.socket, "connect_ex", trip)
    monkeypatch.setattr(socket, "create_connection", trip)
    monkeypatch.setattr(urllib.request, "urlopen", trip)
    monkeypatch.setattr(http.client.HTTPConnection, "request", trip)
    monkeypatch.setattr(http.client.HTTPSConnection, "request", trip)
    for mod, cls, meth in (("requests", "Session", "send"), ("httpx", "Client", "send"),
                           ("httpx", "AsyncClient", "send")):
        try:
            m = __import__(mod)
        except Exception:
            continue
        monkeypatch.setattr(getattr(m, cls), meth, trip)
    return attempts


class Src:
    kind = "microphone"
    error = ""

    def __init__(self, max_s):
        self.on = None

    def start(self, on_pcm):
        self.on = on_pcm

    def stop(self):
        pass


def tone(seconds, amp=8000):
    n = int(mc.RATE * seconds)
    return array.array("h", (int(amp * math.sin(2 * math.pi * 440 * i / mc.RATE))
                             for i in range(n))).tobytes()


def test_a_meeting_from_start_to_delete_sends_nothing_anywhere(tmp_path, monkeypatch, watched):
    from agent_friday.services import local_call
    monkeypatch.setattr(mc, "_root", lambda: tmp_path / "meetings")
    notices, ledger, cloud_refusals, prompts = [], [], [], []

    def local_model(system, user, model, **kw):
        # the default notes path: inside a local-only run, where a cloud call is refused
        assert local_only_guard.is_active()
        with pytest.raises(local_only_guard.CloudRefused):
            local_only_guard.refuse_if_active("anthropic", "a-cloud-model")
        cloud_refusals.append(1)
        prompts.append(user)
        return {"notes": ["Discussed " + CANARY], "decisions": ["Ship " + CANARY],
                "action_items": [{"task": "Send " + CANARY, "owner": None, "due": None}]}
    monkeypatch.setattr(local_call, "call_json", local_model)

    emails, todos = [], []
    m = mc.MeetingManager(
        source_factory=lambda max_s, **k: Src(max_s), transcriber=lambda pcm: "we discussed " + CANARY,
        seat_resolver=lambda: "local-model", asr_check=lambda: "", lock_detector=lambda: None,
        event_lookup=lambda: None, notifier=lambda m_, reason, msg: notices.append(msg),
        scheduler=lambda mid: types.SimpleNamespace(release=lambda: None),
        partials_factory=lambda: None, speech_gate=lambda pcm: True,
        ledger=lambda kind, **f: ledger.append((kind, f)), retention=lambda: "",
        todo_writer=lambda t: todos.append(t) or t,
        email_requester=lambda **k: emails.append(k) or {"approval_id": "ap1"},
        tick_s=0.01, chunk_s=1.0)

    st = m.start(consent_ack=True, confirm=True, title="Planning", keep_audio=True)
    mid = st["id"]
    m._active.source.on(tone(0.9))
    m.stop()
    assert m.wait_idle(10)

    got = m.get(mid)
    assert got["summary"]["status"] == "done" and cloud_refusals, "the notes ran, locally"
    assert CANARY in prompts[0], "the model saw the transcript (the canary is the test's tracer)"

    m.create_tasks(mid, [0])
    d = m.follow_up_draft(mid)
    m.request_follow_up(mid, to="someone@example.invalid", subject=d["subject"], body=d["body"])
    assert emails and todos, "tasks and the follow-up card are made by the owner's own action"

    m.delete_audio(mid)
    mp.sweep(m)
    m.delete(mid)

    assert watched == [], "nothing tried to reach the network"
    # the ledger and the notification carry counts and sentences, never meeting content
    for kind, fields in ledger:
        assert CANARY not in str(fields) and "Planning" not in str(fields)
    assert all(CANARY not in n for n in notices)


def test_the_canary_is_never_on_disk_in_the_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(mc, "_root", lambda: tmp_path / "meetings")
    m = mc.MeetingManager(
        source_factory=lambda max_s, **k: Src(max_s), transcriber=lambda pcm: "we discussed " + CANARY,
        seat_resolver=lambda: None, asr_check=lambda: "", lock_detector=lambda: None,
        event_lookup=lambda: None, notifier=lambda *a: None,
        scheduler=lambda mid: types.SimpleNamespace(release=lambda: None),
        partials_factory=lambda: None, speech_gate=lambda pcm: True,
        ledger=lambda *a, **k: None, retention=lambda: "", tick_s=0.01, chunk_s=1.0)
    m.start(consent_ack=True, confirm=True, title=CANARY, keep_audio=True)
    m._active.source.on(tone(0.9))
    m.stop()
    assert m.wait_idle(10)
    files = [p for p in (tmp_path / "meetings").rglob("*") if p.is_file()]
    assert files
    for p in files:
        assert CANARY.encode() not in p.read_bytes(), p.name
        assert CANARY not in p.name


def test_a_cloud_provider_is_refused_while_the_notes_run(tmp_path, monkeypatch):
    """The notes call is wrapped in local_only: this is the guard the canary
    test relies on, checked on its own against the real default call path."""
    from agent_friday.services import local_call
    monkeypatch.setattr(mc, "_root", lambda: tmp_path / "meetings")
    seen = {}

    def call_json(system, user, model, **kw):
        seen["active"] = local_only_guard.is_active()
        try:
            local_only_guard.refuse_if_active("gemini", "gemini-pro")
            seen["refused"] = False
        except local_only_guard.CloudRefused:
            seen["refused"] = True
        return {"notes": ["x"], "decisions": [], "action_items": []}
    monkeypatch.setattr(local_call, "call_json", call_json)
    m = mc.MeetingManager(
        source_factory=lambda max_s, **k: Src(max_s), transcriber=lambda pcm: "talk",
        seat_resolver=lambda: "local-model", asr_check=lambda: "", lock_detector=lambda: None,
        event_lookup=lambda: None, notifier=lambda *a: None,
        scheduler=lambda mid: types.SimpleNamespace(release=lambda: None),
        partials_factory=lambda: None, speech_gate=lambda pcm: True,
        ledger=lambda *a, **k: None, retention=lambda: "", tick_s=0.01, chunk_s=1.0)
    m.start(consent_ack=True, confirm=True)
    m._active.source.on(tone(0.9))
    m.stop()
    assert m.wait_idle(10)
    assert seen == {"active": True, "refused": True}
