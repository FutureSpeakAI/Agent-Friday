"""The end-of-day summary is written on this machine or not at all.

The 23:30 job sent the whole day's conversation -- local-only chats
included -- through the ordinary router with a cloud model as its hint.
Like every other summary path with no seat of its own, it now runs under the
local-only guard, and a router refusal is not stored as the day's summary.
"""
from __future__ import annotations

from agent_friday.services import local_only_guard as g
from agent_friday.services import model_router as mr


class _Mem:
    def available(self):
        return True

    def get_session(self, date_str):
        return [{"role": "user", "text": "my local-only notes about the custody hearing"},
                {"role": "friday", "text": "noted"}]


def _run(monkeypatch, reply="a short continuity note"):
    seen, saved = {}, []

    def fake_generate_text(messages, system=None, model=None, **kw):
        seen.update(local_only=g.is_active(), model=model)
        return reply
    monkeypatch.setattr(mr, "_generate_text", fake_generate_text)
    monkeypatch.setattr(mr, "_get_conversation_memory", lambda: _Mem())
    monkeypatch.setattr(mr, "_load_session_summary", lambda d: "")
    monkeypatch.setattr(mr, "_save_session_summary",
                        lambda d, text, meta=None: saved.append(text))
    out = mr._generate_session_summary("2026-09-25", force=True)
    return seen, saved, out


def test_the_day_summary_runs_local_only(monkeypatch):
    seen, saved, out = _run(monkeypatch)
    assert seen["local_only"] is True
    assert g.is_active() is False                 # and only for that call
    assert saved == ["a short continuity note"]


def test_no_cloud_model_is_handed_to_the_local_leg(monkeypatch):
    monkeypatch.setattr(mr, "_load_settings", lambda: {"subagent_model": "claude-sonnet-5"})
    seen, _, _ = _run(monkeypatch)
    assert seen["model"] is None


def test_a_refusal_is_not_stored_as_the_day(monkeypatch):
    _, saved, out = _run(monkeypatch, reply=mr.RoutedRefusal("This request needs vault access"))
    assert saved == [] and out is None
