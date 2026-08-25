"""Unit tests for the WO-2 sweep (2026-08-25 followup): eight more prompt-
build sites that skipped provider/vault_control gating, matching the same bug
class already fixed elsewhere (agent.py's _task_worker, calendar.py,
workflows.py, workspace_studio.py, creations.py, content_composer.py,
misc_engine.py, news_engine.py, news.py) — a call built its system prompt via
_get_friday_system_prompt with no `provider` or `vault_control`, so TIER_2
vault content rode into the prompt in the clear whenever the call landed on a
cloud provider, with only the egress gate's keyword classifier (a known-
incomplete second line of defense) standing between it and the wire.

Closed here: routes/code.py (code_plan), routes/messages.py
(api_messages_draft), services/calendar_engine.py (_day_annotation),
services/channels/manager.py (_system_prompt), services/creative_pipeline.py
(_exec_text_stage), services/persona_eval.py (run_live_eval),
services/scheduler.py (_afternoon_briefing_job — UNATTENDED, daily 16:00, the
worst of this set because a leak there repeats with nobody watching),
services/model_router.py's own self-referential call in
_generate_session_summary, services/worker_adapters/claude_code_adapter.py
(ClaudeCodeAdapter._run — the ninth site, found during the original sweep's
own audit and named rather than silently patched or silently dropped, then
fixed under a follow-up authorization), and routes/chat.py's source-dossier
builder (the TENTH site — found by accident, as a side effect of making
provider/vault_control required arguments, not by a fourth manual audit
pass; see tests/unit/test_gated_prompt_required_args.py and
scripts/check_gated_prompt_callers.py, which is what should catch an
eleventh).

INJECTION POINT: `_build_context_prompt`'s "== TODAY'S CONTEXT ==" section
(model_router.py, sourced from voice_engine._load_live_context()) is
UNCONDITIONAL — added at TIER_2 regardless of `keywords`/`workspace`/`needs`
— so it is a uniform TIER_2 probe for every site here without per-site
keyword tuning. Confirmed against the real VaultAccessControl singleton, not
a stub: gate_content on this section actually redacts for a non-local
provider and passes for 'local'.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest
from flask import Flask

import agent_friday.services.model_router as mr
import agent_friday.services.voice_engine as ve

TIER2_SECRET = "renew the passport before the custody hearing"
_app = Flask(__name__)


@pytest.fixture(autouse=True)
def _fake_live_context(monkeypatch):
    monkeypatch.setattr(ve, "_load_live_context", lambda: TIER2_SECRET)
    monkeypatch.setattr(mr, "_vault_local_only", lambda: True)


# ═══════════════════════════════════════════════════════════════════════════
# 1. routes/code.py :: code_plan
# ═══════════════════════════════════════════════════════════════════════════
import agent_friday.routes.code as code_mod


def _run_code_plan(monkeypatch, *, predicted_provider, force_ungated=False):
    captured = {}

    def _fake_generate_text(messages, system=None, **kw):
        captured['system'] = system
        return '{"summary": "x", "steps": [], "files": []}'

    monkeypatch.setattr(code_mod, "_generate_text", _fake_generate_text)
    monkeypatch.setattr(code_mod, "_repo_path", lambda name: "C:/fake/repo")
    monkeypatch.setattr(code_mod, "_repo_tree", lambda rp: [])
    monkeypatch.setattr(code_mod, "_predict_route_provider",
                        lambda **kw: predicted_provider)
    if force_ungated:
        monkeypatch.setattr(code_mod, "_gated_vault_control", lambda: None)
    with _app.test_request_context(
            '/api/code/plan', method='POST',
            json={"repo": "x", "instruction": "review my pending tasks"}):
        code_mod.code_plan()
    return captured.get('system') or ""


class TestCodePlan:
    def test_cloud_bound_omits_tier2(self, monkeypatch):
        system = _run_code_plan(monkeypatch, predicted_provider='cloud')
        assert TIER2_SECRET not in system

    def test_local_bound_keeps_tier2(self, monkeypatch):
        system = _run_code_plan(monkeypatch, predicted_provider='local')
        assert TIER2_SECRET in system

    def test_falsifiable(self, monkeypatch):
        system = _run_code_plan(monkeypatch, predicted_provider='cloud',
                                force_ungated=True)
        assert TIER2_SECRET in system, "ungated reproduction did not leak"


# ═══════════════════════════════════════════════════════════════════════════
# 2. routes/messages.py :: api_messages_draft
# ═══════════════════════════════════════════════════════════════════════════
import agent_friday.routes.messages as messages_mod


def _run_messages_draft(monkeypatch, *, predicted_provider, force_ungated=False):
    captured = {}

    def _fake_generate_text(messages, system=None, **kw):
        captured['system'] = system
        return "draft body"

    monkeypatch.setattr(messages_mod, "_generate_text", _fake_generate_text)
    monkeypatch.setattr(messages_mod, "_predict_route_provider",
                        lambda **kw: predicted_provider)
    if force_ungated:
        monkeypatch.setattr(messages_mod, "_gated_vault_control", lambda: None)
    with _app.test_request_context(
            '/api/messages/draft', method='POST',
            json={"sender": "a@b.com", "subject": "hi", "snippet": "hello"}):
        messages_mod.api_messages_draft()
    return captured.get('system') or ""


class TestMessagesDraft:
    def test_cloud_bound_omits_tier2(self, monkeypatch):
        system = _run_messages_draft(monkeypatch, predicted_provider='cloud')
        assert TIER2_SECRET not in system

    def test_local_bound_keeps_tier2(self, monkeypatch):
        system = _run_messages_draft(monkeypatch, predicted_provider='local')
        assert TIER2_SECRET in system

    def test_falsifiable(self, monkeypatch):
        system = _run_messages_draft(monkeypatch, predicted_provider='cloud',
                                     force_ungated=True)
        assert TIER2_SECRET in system, "ungated reproduction did not leak"


# ═══════════════════════════════════════════════════════════════════════════
# 3. services/calendar_engine.py :: _day_annotation
# ═══════════════════════════════════════════════════════════════════════════
import agent_friday.services.calendar_engine as cal_mod


def _run_day_annotation(monkeypatch, *, predicted_provider, force_ungated=False):
    captured = {}

    def _fake_generate_text(messages, system=None, **kw):
        captured['system'] = system
        return "a fine day ahead"

    monkeypatch.setattr(mr, "_generate_text", _fake_generate_text)
    monkeypatch.setattr(mr, "_predict_route_provider",
                        lambda **kw: predicted_provider)
    if force_ungated:
        monkeypatch.setattr(mr, "_gated_vault_control", lambda: None)
    monkeypatch.setattr(cal_mod, "_load_json_dict", lambda path: {})
    monkeypatch.setattr(cal_mod, "_save_json_dict", lambda path, data: data)
    events = [{"title": "review sprint plan", "type": "normal"}]
    cal_mod._day_annotation(__import__("datetime").date(2026, 8, 25), events)
    return captured.get('system') or ""


class TestDayAnnotation:
    def test_cloud_bound_omits_tier2(self, monkeypatch):
        system = _run_day_annotation(monkeypatch, predicted_provider='cloud')
        assert TIER2_SECRET not in system

    def test_local_bound_keeps_tier2(self, monkeypatch):
        system = _run_day_annotation(monkeypatch, predicted_provider='local')
        assert TIER2_SECRET in system

    def test_falsifiable(self, monkeypatch):
        system = _run_day_annotation(monkeypatch, predicted_provider='cloud',
                                     force_ungated=True)
        assert TIER2_SECRET in system, "ungated reproduction did not leak"


# ═══════════════════════════════════════════════════════════════════════════
# 4. services/channels/manager.py :: _system_prompt / _run_agent
# ═══════════════════════════════════════════════════════════════════════════
import agent_friday.services.channels.manager as chan_mod


class TestChannelsSystemPrompt:
    def test_cloud_bound_omits_tier2(self, monkeypatch):
        monkeypatch.setattr(mr, "_predict_route_provider", lambda **kw: 'cloud')
        out = chan_mod._system_prompt(keywords="incoming text")
        assert TIER2_SECRET not in out

    def test_local_bound_keeps_tier2(self, monkeypatch):
        monkeypatch.setattr(mr, "_predict_route_provider", lambda **kw: 'local')
        out = chan_mod._system_prompt(keywords="incoming text")
        assert TIER2_SECRET in out

    def test_falsifiable(self, monkeypatch):
        monkeypatch.setattr(mr, "_predict_route_provider", lambda **kw: 'cloud')
        monkeypatch.setattr(mr, "_gated_vault_control", lambda: None)
        out = chan_mod._system_prompt(keywords="incoming text")
        assert TIER2_SECRET in out, "ungated reproduction did not leak"


# ═══════════════════════════════════════════════════════════════════════════
# 5. services/creative_pipeline.py :: _exec_text_stage
# ═══════════════════════════════════════════════════════════════════════════
import agent_friday.services.creative_pipeline as cp_mod


def _run_exec_text_stage(monkeypatch, *, predicted_provider, force_ungated=False):
    captured = {}

    def _fake_generate_text(messages, system=None, **kw):
        captured['system'] = system
        return "stage output"

    monkeypatch.setattr(mr, "_generate_text", _fake_generate_text)
    monkeypatch.setattr(mr, "_predict_route_provider",
                        lambda **kw: predicted_provider)
    if force_ungated:
        monkeypatch.setattr(mr, "_gated_vault_control", lambda: None)
    cp_mod._exec_text_stage({"workspace": "content"}, "write something", {})
    return captured.get('system') or ""


class TestExecTextStage:
    def test_cloud_bound_omits_tier2(self, monkeypatch):
        system = _run_exec_text_stage(monkeypatch, predicted_provider='cloud')
        assert TIER2_SECRET not in system

    def test_local_bound_keeps_tier2(self, monkeypatch):
        system = _run_exec_text_stage(monkeypatch, predicted_provider='local')
        assert TIER2_SECRET in system

    def test_falsifiable(self, monkeypatch):
        system = _run_exec_text_stage(monkeypatch, predicted_provider='cloud',
                                      force_ungated=True)
        assert TIER2_SECRET in system, "ungated reproduction did not leak"


# ═══════════════════════════════════════════════════════════════════════════
# 6. services/persona_eval.py :: run_live_eval
#    The destination provider is KNOWN here (each candidate is dispatched to
#    explicitly), not predicted — so this test drives it via the adapter type
#    rather than _predict_route_provider.
# ═══════════════════════════════════════════════════════════════════════════
import agent_friday.services.persona_eval as pe_mod


def _run_live_eval(monkeypatch, *, adapter, force_ungated=False):
    captured = {}

    def _fake_dispatch(prov, user_prompt, system_prompt):
        captured['system'] = system_prompt
        return "a response"

    monkeypatch.setattr(pe_mod, "live_mode_enabled", lambda settings=None: True)
    monkeypatch.setattr(pe_mod, "load_golden_corpus",
                        lambda: [{"id": "g1", "prompt": "hello"}])
    monkeypatch.setattr(pe_mod, "_text_capable_providers",
                        lambda: [{"name": "p1", "adapter": adapter}])
    monkeypatch.setattr(pe_mod, "_dispatch_provider", _fake_dispatch)
    if force_ungated:
        monkeypatch.setattr(mr, "_gated_vault_control", lambda: None)
    pe_mod.run_live_eval()
    return captured.get('system') or ""


class TestPersonaEvalLive:
    def test_cloud_adapter_omits_tier2(self, monkeypatch):
        system = _run_live_eval(monkeypatch, adapter="anthropic")
        assert TIER2_SECRET not in system

    def test_ollama_adapter_keeps_tier2(self, monkeypatch):
        system = _run_live_eval(monkeypatch, adapter="ollama")
        assert TIER2_SECRET in system

    def test_falsifiable(self, monkeypatch):
        system = _run_live_eval(monkeypatch, adapter="anthropic", force_ungated=True)
        assert TIER2_SECRET in system, "ungated reproduction did not leak"


# ═══════════════════════════════════════════════════════════════════════════
# 7. services/scheduler.py :: _afternoon_briefing_job
#    UNATTENDED — runs daily at 16:00 with nobody watching. The worst site in
#    this set: a gating gap here leaks silently, every day, not once.
# ═══════════════════════════════════════════════════════════════════════════
import agent_friday.services.scheduler as sched_mod


def _run_afternoon_briefing(monkeypatch, *, predicted_provider, force_ungated=False):
    captured = {}

    def _fake_generate_text(messages, system=None, **kw):
        captured['system'] = system
        return "briefing body"

    monkeypatch.setattr(mr, "_generate_text", _fake_generate_text)
    monkeypatch.setattr(mr, "_predict_route_provider",
                        lambda **kw: predicted_provider)
    if force_ungated:
        monkeypatch.setattr(mr, "_gated_vault_control", lambda: None)

    import agent_friday.services.news_engine as news_mod
    monkeypatch.setattr(news_mod, "_gather_live_briefing_context", lambda: "today's data")
    monkeypatch.setattr(news_mod, "_notify_briefing", lambda date_str: None)
    sched_mod._afternoon_briefing_job()
    return captured.get('system') or ""


class TestAfternoonBriefing:
    def test_cloud_bound_omits_tier2(self, monkeypatch):
        system = _run_afternoon_briefing(monkeypatch, predicted_provider='cloud')
        assert TIER2_SECRET not in system

    def test_local_bound_keeps_tier2(self, monkeypatch):
        system = _run_afternoon_briefing(monkeypatch, predicted_provider='local')
        assert TIER2_SECRET in system

    def test_falsifiable(self, monkeypatch):
        system = _run_afternoon_briefing(monkeypatch, predicted_provider='cloud',
                                         force_ungated=True)
        assert TIER2_SECRET in system, "ungated reproduction did not leak"


# ═══════════════════════════════════════════════════════════════════════════
# 8. services/model_router.py :: _generate_session_summary (self-referential)
# ═══════════════════════════════════════════════════════════════════════════
class _FakeMem:
    def available(self):
        return True

    def get_session(self, date_str):
        return [{"role": "user", "text": "how's the migration going"},
                {"role": "friday", "text": "on track"}]


def _run_session_summary(monkeypatch, *, predicted_provider, force_ungated=False):
    captured = {}

    def _fake_generate_text(messages, system=None, **kw):
        captured['system'] = system
        return "a short continuity note"

    monkeypatch.setattr(mr, "_generate_text", _fake_generate_text)
    monkeypatch.setattr(mr, "_predict_route_provider",
                        lambda **kw: predicted_provider)
    if force_ungated:
        monkeypatch.setattr(mr, "_gated_vault_control", lambda: None)
    monkeypatch.setattr(mr, "_get_conversation_memory", lambda: _FakeMem())
    monkeypatch.setattr(mr, "_load_session_summary", lambda date_str: "")
    monkeypatch.setattr(mr, "_save_session_summary",
                        lambda date_str, text, meta=None: None)
    mr._generate_session_summary("2026-08-25", force=True)
    return captured.get('system') or ""


class TestGenerateSessionSummary:
    def test_cloud_bound_omits_tier2(self, monkeypatch):
        system = _run_session_summary(monkeypatch, predicted_provider='cloud')
        assert TIER2_SECRET not in system

    def test_local_bound_keeps_tier2(self, monkeypatch):
        system = _run_session_summary(monkeypatch, predicted_provider='local')
        assert TIER2_SECRET in system

    def test_falsifiable(self, monkeypatch):
        system = _run_session_summary(monkeypatch, predicted_provider='cloud',
                                      force_ungated=True)
        assert TIER2_SECRET in system, "ungated reproduction did not leak"


# ═══════════════════════════════════════════════════════════════════════════
# 9. services/worker_adapters/claude_code_adapter.py :: ClaudeCodeAdapter._run
#    The ninth site — named in the original sweep's report rather than
#    silently fixed or silently dropped, then fixed under a follow-up
#    authorization. `provider="auto"` was already being passed here; the
#    missing piece was `vault_control` (without it, `provider` does nothing
#    — see _build_context_prompt's "only tier-gates when vault_control is
#    given"), so this is a genuine ungated-leak site, not just a style gap.
# ═══════════════════════════════════════════════════════════════════════════
import types as _types
from agent_friday.services.worker_adapters.claude_code_adapter import (
    ClaudeCodeAdapter, _JOBS, _JOBS_LOCK)


def _run_claude_code_adapter(monkeypatch, *, predicted_provider, force_ungated=False):
    captured = {}

    def _fake_generate_agent(messages, system=None, **kw):
        captured['system'] = system
        return "done", []

    import agent_friday.services.agent as agent_mod
    monkeypatch.setattr(agent_mod, "_generate_agent", _fake_generate_agent)
    monkeypatch.setattr(mr, "_predict_route_provider",
                        lambda **kw: predicted_provider)
    if force_ungated:
        monkeypatch.setattr(mr, "_gated_vault_control", lambda: None)

    adapter = ClaudeCodeAdapter()
    aid = "test-cca-job"
    task = _types.SimpleNamespace(task_id="t1", prompt="write a small script")
    with _JOBS_LOCK:
        _JOBS[aid] = {}
    try:
        adapter._run(aid, task)
    finally:
        with _JOBS_LOCK:
            _JOBS.pop(aid, None)
    return captured.get('system') or ""


class TestClaudeCodeAdapter:
    def test_cloud_bound_omits_tier2(self, monkeypatch):
        system = _run_claude_code_adapter(monkeypatch, predicted_provider='cloud')
        assert TIER2_SECRET not in system

    def test_local_bound_keeps_tier2(self, monkeypatch):
        system = _run_claude_code_adapter(monkeypatch, predicted_provider='local')
        assert TIER2_SECRET in system

    def test_falsifiable(self, monkeypatch):
        system = _run_claude_code_adapter(monkeypatch, predicted_provider='cloud',
                                          force_ungated=True)
        assert TIER2_SECRET in system, "ungated reproduction did not leak"


# ═══════════════════════════════════════════════════════════════════════════
# 10. routes/chat.py :: the source-dossier builder
#     Found by accident (a side effect of the provider/vault_control-required
#     migration), not by a fourth manual audit pass — see
#     test_gated_prompt_required_args.py and check_gated_prompt_callers.py,
#     which is the mechanism that should catch the next one instead.
# ═══════════════════════════════════════════════════════════════════════════
import agent_friday.routes.chat as chat_mod


class _FakeDossierMem:
    def available(self):
        return True

    def get_session(self, session_id):
        return [{"role": "friday", "text": "The sky is blue.",
                 "timestamp": "2026-08-25T10:00:00"}]


def _run_source_dossier(monkeypatch, *, predicted_provider, force_ungated=False):
    captured = {}

    def _fake_generate_text(messages, system=None, **kw):
        captured['system'] = system
        return "dossier markdown"

    monkeypatch.setattr(chat_mod, "_generate_text", _fake_generate_text)
    monkeypatch.setattr(chat_mod, "_predict_route_provider",
                        lambda **kw: predicted_provider)
    monkeypatch.setattr(chat_mod, "_factcheck_news_citations", lambda md: md)
    monkeypatch.setattr(chat_mod, "_get_conversation_memory",
                        lambda: _FakeDossierMem())
    if force_ungated:
        monkeypatch.setattr(chat_mod, "_get_vault_control", lambda: None)

    with _app.test_request_context('/api/sources/dossier/2026-08-25'):
        chat_mod.sources_dossier("2026-08-25")
    return captured.get('system') or ""


class TestSourceDossier:
    def test_cloud_bound_omits_tier2(self, monkeypatch):
        system = _run_source_dossier(monkeypatch, predicted_provider='cloud')
        assert TIER2_SECRET not in system

    def test_local_bound_keeps_tier2(self, monkeypatch):
        system = _run_source_dossier(monkeypatch, predicted_provider='local')
        assert TIER2_SECRET in system

    def test_falsifiable(self, monkeypatch):
        system = _run_source_dossier(monkeypatch, predicted_provider='cloud',
                                     force_ungated=True)
        assert TIER2_SECRET in system, "ungated reproduction did not leak"
