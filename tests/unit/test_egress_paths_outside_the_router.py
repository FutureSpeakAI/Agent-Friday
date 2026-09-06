"""Outbound paths that do NOT go through model_router._seal_or_block.

docs/security/threat-model.md:42 says the egress gate "runs immediately before
every outbound cloud HTTP call"; SECURITY.md:60 says every cloud call passes
through the router chokepoint. The 2026-09-06 hardening audit traced every
call site and found these carrying user- or model-authored text past the gate.
Each test here was red on main @ 9d329fe and pins the fix.

Every test stubs the network. No key, no provider, no bytes leave.
"""
from __future__ import annotations

import json
import sys
import types

import pytest

SSN = "my SSN is 123-45-6789 and my bank account number is 987654321"  # pragma: allowlist secret


@pytest.fixture
def gate_spy(monkeypatch):
    """Replace the gate's public text entry points with a recorder that
    redacts, so a test can assert both that the gate was consulted and that
    what left was the gated text, without depending on the live classifier."""
    from agent_friday.services import egress_gate as eg
    calls = []

    def fake_gate_text(text, provider, field="prompt", log_path=None):
        calls.append((provider, field, text))
        return "[REDACTED]" if "123-45-6789" in str(text) else text  # pragma: allowlist secret

    def fake__gate_text(text, provider, field, log_path=None):
        return fake_gate_text(text, provider, field, log_path)

    monkeypatch.setattr(eg, "gate_text", fake_gate_text)
    monkeypatch.setattr(eg, "_gate_text", fake__gate_text)
    return calls


# ── 1. worker adapter: HTTP_API posts the delegate prompt anywhere ──────────

def test_http_api_adapter_gates_the_prompt_before_posting_it(monkeypatch):
    from agent_friday.services.worker_adapters import http_api_adapter as mod
    from agent_friday.services.orchestrator import WorkerTask, AdapterType
    from agent_friday.services import egress_gate as eg
    seen = {}

    def fake_gate_worker_payload(payload, *, base_url, provider="ollama"):
        seen["base_url"] = base_url
        seen["provider"] = provider
        out = dict(payload)
        out["prompt"] = "[REDACTED]"
        return out
    monkeypatch.setattr(eg, "gate_worker_payload", fake_gate_worker_payload)

    captured = {}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"ok": true}'

    def fake_urlopen(req, timeout=None):
        captured["body"] = req.data
        captured["url"] = req.full_url
        return _Resp()
    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)

    task = WorkerTask(task_id="t1", prompt=SSN, adapter_type=AdapterType.HTTP_API,
                      context={"endpoint": "https://example.invalid/ingest"})
    ad = mod.HttpApiAdapter()
    mod._JOBS["aid-1"] = {"aid": "aid-1", "task_id": "t1", "status": mod.WorkerStatus.RUNNING}
    ad._run("aid-1", task)
    assert seen.get("base_url") == "https://example.invalid/ingest", "gate never consulted"
    assert b"123-45-6789" not in captured.get("body", b"")  # pragma: allowlist secret
    assert mod._JOBS["aid-1"]["status"].name == "COMPLETED"


def test_http_api_adapter_fails_closed_when_the_gate_raises(monkeypatch):
    from agent_friday.services.worker_adapters import http_api_adapter as mod
    from agent_friday.services.orchestrator import WorkerTask, AdapterType
    from agent_friday.services import egress_gate as eg

    def boom(payload, *, base_url, provider="ollama"):
        raise RuntimeError("never-send hit")
    monkeypatch.setattr(eg, "gate_worker_payload", boom)
    posted = []
    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **k: posted.append(1))
    task = WorkerTask(task_id="t2", prompt=SSN, adapter_type=AdapterType.HTTP_API,
                      context={"endpoint": "https://example.invalid/ingest"})
    mod._JOBS["aid-2"] = {"aid": "aid-2", "task_id": "t2", "status": mod.WorkerStatus.RUNNING}
    mod.HttpApiAdapter()._run("aid-2", task)
    assert posted == [] and mod._JOBS["aid-2"]["status"].name == "FAILED"
    assert "egress gate" in mod._JOBS["aid-2"]["error"]


# ── 2. kie.ai: a gate exception fell open to the raw prompt ─────────────────

def test_kie_generate_fails_closed_when_the_gate_raises(monkeypatch):
    from agent_friday.services import kie_generate as kie
    from agent_friday.services import egress_gate as eg
    monkeypatch.setattr(kie, "is_configured", lambda: True)

    def boom(text, provider, field="prompt", log_path=None):
        raise RuntimeError("classifier crashed")
    monkeypatch.setattr(eg, "gate_text", boom)
    created = []
    monkeypatch.setattr(kie, "_create_task", lambda model, params: created.append(params) or "tid")
    kind = next(iter(kie._TOOLS))
    res = kie.generate(kind, SSN, model="some-kie-model")
    assert created == [], "the raw prompt was submitted after the gate failed"
    assert res["status"] == "blocked"


# ── 3. Gemini Veo / Omni video: prompt never gated ──────────────────────────

def _veo_stub(captured):
    class _Op:
        done = True
        response = types.SimpleNamespace(generated_videos=[])
        error = None
        name = "op"

    class _Models:
        def generate_videos(self, **kw):
            captured.update(kw)
            return _Op()

    class _Ops:
        def get(self, op): return op

    return types.SimpleNamespace(models=_Models(), operations=_Ops(),
                                 interactions=types.SimpleNamespace(create=lambda **kw: captured.update(omni=kw) or types.SimpleNamespace()))


def test_veo_video_prompt_is_gated_before_it_leaves(monkeypatch, gate_spy):
    from agent_friday.services import creative_engine as ce
    from agent_friday import core
    monkeypatch.setattr(core, "GEMINI_API_KEY", "k")
    captured = {}
    monkeypatch.setattr(ce, "_client", lambda: _veo_stub(captured))
    monkeypatch.setattr(ce, "_spend_cap_halt", lambda what: None)
    monkeypatch.setattr(ce, "_configured_video_model", lambda: "veo-3.0-generate-001")
    for name in ("_orb_start", "_orb_update", "_orb_fail"):
        monkeypatch.setattr(ce, name, lambda *a, **k: None)
    try:
        ce.generate_video(SSN, model="veo-3.0-generate-001")
    except Exception:
        pass  # the stub returns no video; we only care what was sent
    assert any(f.startswith("video") for _, f, _ in gate_spy), "gate not consulted for the video prompt"
    sent = captured.get("prompt") or json.dumps(captured.get("omni", {}), default=str)
    assert "123-45-6789" not in str(sent)  # pragma: allowlist secret


def test_omni_video_prompt_is_gated_before_it_leaves(monkeypatch, gate_spy):
    from agent_friday.services import creative_engine as ce
    from agent_friday import core
    monkeypatch.setattr(core, "GEMINI_API_KEY", "k")
    captured = {}
    monkeypatch.setattr(ce, "_client", lambda: _veo_stub(captured))
    monkeypatch.setattr(ce, "_spend_cap_halt", lambda what: None)
    monkeypatch.setattr(ce, "_configured_video_model", lambda: "gemini-omni-flash")
    monkeypatch.setattr(ce, "_is_omni_model", lambda m: True)
    for name in ("_orb_start", "_orb_update", "_orb_fail"):
        monkeypatch.setattr(ce, name, lambda *a, **k: None)
    try:
        ce.generate_video(SSN, model="gemini-omni-flash")
    except Exception:
        pass
    assert any(f.startswith("video") for _, f, _ in gate_spy)
    assert "123-45-6789" not in json.dumps(captured.get("omni", {}), default=str)  # pragma: allowlist secret


# ── 4. Lyria music: prompt, lyrics and negative prompt never gated ─────────

def test_music_prompt_lyrics_and_negative_prompt_are_gated(monkeypatch, gate_spy):
    from agent_friday.services import music_engine as me
    from agent_friday import core
    monkeypatch.setattr(core, "GEMINI_API_KEY", "k")
    monkeypatch.setattr(me, "cloud_music_available", lambda: (True, None))
    for name in ("_orb_start", "_orb_update", "_orb_fail"):
        monkeypatch.setattr(me, name, lambda *a, **k: None)
    fake_genai = types.SimpleNamespace(Client=lambda **kw: object())
    fake_google = types.ModuleType("google")
    fake_google.genai = fake_genai
    fake_genai_mod = types.ModuleType("google.genai")
    fake_genai_mod.Client = fake_genai.Client
    fake_genai_mod.types = types.SimpleNamespace()
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai_mod)
    monkeypatch.setitem(sys.modules, "google.genai.types", fake_genai_mod.types)
    captured = {}

    def fake_cloud(client, gtypes, api_model, full_prompt, *, mode, lyrics, duration_seconds,
                   language, timestamps, negative_prompt, seeds, orb):
        captured.update(prompt=full_prompt, lyrics=lyrics, negative=negative_prompt)
        return []
    monkeypatch.setattr(me, "_generate_music_cloud", fake_cloud)
    me.generate_music("a song about " + SSN, lyrics="verse: " + SSN, negative_prompt="no " + SSN)
    fields = {f for _, f, _ in gate_spy}
    assert {"music.prompt", "music.lyrics", "music.negative_prompt"} <= fields, fields
    for k in ("prompt", "lyrics", "negative"):
        assert "123-45-6789" not in str(captured.get(k)), k  # pragma: allowlist secret


# ── 5. Calendar annotate: siblings gated, this one was not ──────────────────

def test_annotate_events_gates_location_and_note(monkeypatch, gate_spy):
    from agent_friday.services import calendar_write as cw
    monkeypatch.setattr(cw, "write_ready", lambda: (True, None))
    monkeypatch.setattr(cw, "find_events", lambda q: {"ok": True, "series": [], "events": [
        {"id": "e1", "title": "Dentist", "location": "", "description": "", "recurring_event_id": None}]})
    bodies = []

    class _Patch:
        def __init__(self, body): self.body = body

        def execute(self):
            bodies.append(self.body)
            return {}

    class _Events:
        def patch(self, calendarId, eventId, body): return _Patch(body)

    svc = types.SimpleNamespace(events=lambda: _Events())
    monkeypatch.setattr(cw, "_service", lambda: (svc, None))
    res = cw.annotate_events("dentist", location="Suite 4, " + SSN, note="bring " + SSN)
    fields = {f for _, f, _ in gate_spy}
    assert {"calendar.location", "calendar.description"} <= fields, fields
    assert bodies and "123-45-6789" not in json.dumps(bodies), (bodies, res)  # pragma: allowlist secret


# ── 6. Google Tasks: title and notes left ungated ───────────────────────────

def _fake_tasks_build(bodies):
    class _Call:
        def __init__(self, body): self.body = body

        def execute(self):
            bodies.append(self.body)
            return {"id": "x", "title": self.body.get("title", ""), "status": "needsAction"}

    class _Tasks:
        def insert(self, tasklist, body): return _Call(body)
        def patch(self, tasklist, task, body): return _Call(body)

    svc = types.SimpleNamespace(tasks=lambda: _Tasks())
    return lambda *a, **k: svc


def test_google_tasks_create_and_update_gate_title_and_notes(monkeypatch, gate_spy):
    from agent_friday.services import google_accounts as ga
    bodies = []
    disc = types.ModuleType("googleapiclient.discovery")
    disc.build = _fake_tasks_build(bodies)
    pkg = types.ModuleType("googleapiclient")
    pkg.discovery = disc
    monkeypatch.setitem(sys.modules, "googleapiclient", pkg)
    monkeypatch.setitem(sys.modules, "googleapiclient.discovery", disc)
    monkeypatch.setattr(ga, "credentials_for", lambda acct: object())
    monkeypatch.setattr(ga.cs, "audit_event", lambda *a, **k: None)
    ga.create_task("acct1", "Call the bank re " + SSN, notes="MRN " + SSN)
    ga.update_task("acct1", "@default", "t1", notes="follow up " + SSN)
    fields = {f for _, f, _ in gate_spy}
    assert {"tasks.title", "tasks.notes"} <= fields, fields
    assert bodies and "123-45-6789" not in json.dumps(bodies)  # pragma: allowlist secret


# ── 7. Publisher: alt text and link cards egress outside _final_texts ───────

def test_publisher_final_texts_include_alt_text_and_link_cards():
    from agent_friday.services import publisher
    post = {"title": "T", "body": "B",
            "assets": [{"path": "x.png", "alt_text": "photo of " + SSN}]}
    target = {"adapted_title": "T", "adapted_body": "B",
              "options": {"link_card": {"uri": "https://x", "title": "card " + SSN,
                                        "description": "desc " + SSN},
                          "link_description": "ld " + SSN}}
    texts = publisher._final_texts(target, post)
    joined = "\n".join(texts)
    for marker in ("photo of", "card ", "desc ", "ld "):
        assert marker in joined, f"{marker!r} not among the gated outbound strings"


# ── 8. Higgsfield: a missing gate attribute silently skipped gating ─────────

def test_higgsfield_call_refuses_when_the_gate_is_unavailable(monkeypatch):
    from agent_friday.services import higgsfield_generate as hg
    from agent_friday.services import agent as ag
    sent = []
    monkeypatch.setattr(ag, "_MCP_MANAGER", types.SimpleNamespace(call=lambda *a, **k: sent.append(a) or {"ok": 1}), raising=False)
    monkeypatch.delattr(ag, "_mcp_gate_args", raising=False)
    with pytest.raises(Exception):
        hg._call("generate_image", {"params": {"prompt": SSN}})
    assert sent == [], "forwarded to the remote MCP with no gate available"
