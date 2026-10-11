"""Gemini Live goes through the one checkpoint, `cloud_intent.require`.

The owner's engine choice (Settings > Voice) is a recorded `user_explicit`
intent. Outcomes by mode, with no change for the owners who already chose
Gemini:

  cloud_only / cloud_preferred / local_preferred: allowed, one receipt, same
      fields as the old `record_owner_engine` row (intent user_explicit, source
      voice_engine);
  local_only: refused, nothing sent (it was already refused earlier in /ws/live;
      now the checkpoint refuses it too).
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from agent_friday.services import cloud_intent as ci

SRC = pathlib.Path(ci.__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean():
    ci.clear()
    ci.forget_recent()
    yield
    ci.clear()


@pytest.fixture
def mode(monkeypatch):
    def _set(name):
        monkeypatch.setattr(ci, "routing_mode", lambda: name)
        return name
    return _set


@pytest.mark.parametrize("m", ["cloud_only", "cloud_preferred", "local_preferred"])
def test_the_owners_engine_choice_is_allowed_and_receipted(mode, m):
    mode(m)
    tok = ci.require_owner_engine("google-gemini", "Gemini Live voice call")
    assert tok.intent == "user_explicit" and tok.source == "voice_engine"
    assert tok.provider == "google-gemini" and tok.mode == m
    row = ci.recent(1)[0]
    assert row["intent"] == "user_explicit" and row["purpose"] == "Gemini Live voice call"


@pytest.mark.parametrize("m", ["cloud_only", "local_preferred"])
def test_the_outcome_matches_the_old_receipt_row(mode, m):
    mode(m)
    old = ci.record_owner_engine("google-gemini", "Gemini Live voice call")
    new = ci.require_owner_engine("google-gemini", "Gemini Live voice call")
    for f in ("intent", "provider", "purpose", "model", "mode", "source"):
        assert getattr(new, f) == getattr(old, f), f


def test_local_only_still_refuses(mode):
    mode("local_only")
    with pytest.raises(ci.NoCloudIntent) as ei:
        ci.require_owner_engine("google-gemini", "Gemini Live voice call")
    assert ei.value.mode == "local_only" and "Nothing was sent" in str(ei.value)
    assert ci.recent(1) == []            # a refusal leaves no "sent" receipt


def test_the_intent_does_not_outlive_the_call(mode):
    mode("local_preferred")
    ci.require_owner_engine("google-gemini", "Gemini Live voice call")
    assert ci.current() is None
    with pytest.raises(ci.NoCloudIntent):
        ci.require("google-gemini", "something else")


def test_a_pinned_background_job_is_not_widened(mode):
    # A scheduled job pinned to a cloud model keeps its own intent.
    from agent_friday.services import local_only_guard as guard
    mode("local_only")
    with guard.cloud_pinned("claude-sonnet-5", "job"):
        assert ci.require_owner_engine("google-gemini", "x").intent == "scheduled_cloud"


def _calls(tree, name):
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and getattr(n.func, "attr", getattr(n.func, "id", "")) == name]


def test_require_owner_engine_calls_require():
    tree = ast.parse((SRC / "services" / "cloud_intent.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "require_owner_engine")
    assert _calls(fn, "require"), "the owner-engine checkpoint must call require()"
    assert _calls(fn, "intent_scope")


def test_the_gemini_live_gate_site_uses_the_checkpoint():
    """Route test (source): the /ws/live client factory goes through
    require_owner_engine and no longer writes a bare receipt."""
    tree = ast.parse((SRC / "routes" / "voice.py").read_text(encoding="utf-8-sig"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_client_for")
    assert _calls(fn, "require_owner_engine"), "_client_for must call require_owner_engine"
    assert not _calls(fn, "record_owner_engine"), "a bare receipt is not the gate"
