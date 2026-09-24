"""Lazy imports inside try/except must name something that exists.

A function-local import wrapped in ``except Exception: pass`` fails silently
when its target does not exist: the feature is simply never there, and
nothing in the logs says so. Each test here drives one such call site and
asserts the real collaborator was reached.
"""
from __future__ import annotations

import sys
import types

import pytest


def test_budget_warning_reaches_the_notification_queue(monkeypatch):
    from agent_friday.services import budget_enforcer as be

    pushed = []
    fake = types.ModuleType("agent_friday.notifications_engine")
    fake.push = lambda **kw: pushed.append(kw) or kw
    monkeypatch.setitem(sys.modules, "agent_friday.notifications_engine", fake)
    import agent_friday
    monkeypatch.setattr(agent_friday, "notifications_engine", fake, raising=False)

    be._maybe_warn("research", 90_000, 100_000)

    assert len(pushed) == 1, "the budget warning never reached the notifier"
    assert pushed[0]["kind"] == "budget_warning"
    assert "90%" in pushed[0]["title"]


def test_setup_routing_screen_consults_the_hardware_assessment(monkeypatch):
    from agent_friday import setup_brain
    from agent_friday import setup_wizard as wiz

    calls = []

    def _assess(profile=None):
        calls.append(1)
        return {"capable": True, "reason": "", "brain_label": ""}

    monkeypatch.setattr(setup_brain, "assess", _assess)
    monkeypatch.setattr(wiz, "_clear", lambda: None)
    monkeypatch.setattr(wiz.console, "print", lambda *a, **k: None)
    defaults = []

    def _ask(prompt, default=None, **kw):
        defaults.append(default)
        return default

    monkeypatch.setattr(wiz.Prompt, "ask", staticmethod(_ask))

    wiz.step_routing(5, 2, "")

    assert calls, "step_routing never reached setup_brain.assess()"
    # A capable machine is offered the local-first choice as the default.
    assert defaults[-1] == "3"


@pytest.fixture
def _fresh_probe():
    from agent_friday.routes import intelligence as I
    I.reset_ollama_probe_state_for_tests()
    yield I
    I.reset_ollama_probe_state_for_tests()


def test_ollama_size_probe_uses_the_configured_daemon_url(monkeypatch, _fresh_probe):
    I = _fresh_probe
    from agent_friday.services import local_call

    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.setattr(local_call, "ollama_url", lambda: "http://box.lan:9999")
    seen = []

    class _Resp:
        def json(self):
            return {"models": [{"name": "m:1b", "size": 7}]}

    def _get(url, timeout=None):
        seen.append(url)
        return _Resp()

    import requests
    monkeypatch.setattr(requests, "get", _get)

    assert I._ollama_sizes() == {"m:1b": 7}
    assert seen == ["http://box.lan:9999/api/tags"]


def test_job_scanner_reaches_the_alert_templates():
    """scan() builds a priority alert only when _notify resolved."""
    from agent_friday import notifications
    from agent_friday.seed.skills.job_scanner import scanner

    assert scanner._notify is notifications
    assert callable(scanner._notify.priority_job_alert)
