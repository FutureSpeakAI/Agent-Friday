"""Unit tests for services/onboarding.py — voice-first onboarding state machine."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent_friday.services import onboarding as ob  # noqa: E402
from agent_friday.services import soul  # noqa: E402


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(ob, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(ob, "STATE_PATH", tmp_path / "onboarding.json")
    monkeypatch.setattr(ob, "SETUP_MARKER", tmp_path / ".setup_complete")
    # isolate soul so onboarding never writes the real ~/.friday/SOUL.md
    monkeypatch.setattr(soul, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(soul, "SOUL_FILE", tmp_path / "SOUL.md")
    monkeypatch.setattr(soul, "HISTORY_DIR", tmp_path / "soul_history")
    monkeypatch.setattr(soul, "_cache", {"text": None, "mtime": 0.0})
    # keep identity generation fast + offline
    monkeypatch.setattr(ob, "_ensure_identity", lambda: "deadbeefpubkey")
    yield


def test_greeting_line_is_local_first():
    line = ob.line_for("greet")
    assert "Friday" in line
    assert "no cloud required" in line.lower()


def test_line_for_uses_name():
    line = ob.line_for("done", {"name": "Sam"})
    assert "Sam" in line


def test_steps_order():
    assert ob.STEPS[0] == "greet"
    assert ob.STEPS[-1] == "done"
    assert ob.next_step("greet") == "name"
    assert ob.next_step("done") == "done"


def test_get_state_initial():
    st = ob.get_state()
    assert st["step"] == "greet"
    assert "Friday" in st["line"]
    assert st["complete"] is False


def test_advance_captures_name():
    ob.advance("Riley")
    st = ob.load_state()
    assert st["name"] == "Riley"
    assert st["step"] == "voice_test"


def test_full_flow_completes():
    ob.advance("Riley")            # greet/name -> voice_test
    ob.advance("hi Friday")        # voice_test -> keys
    ob.advance("")                 # keys (skip) -> identity
    ob.advance("")                 # identity -> soul
    ob.advance("")                 # soul (skip) -> done
    res = ob.advance("")           # done -> complete()
    assert res["complete"] is True
    assert ob.is_complete() is True
    assert (ob.SETUP_MARKER).exists()


def test_keys_step_stores_key(monkeypatch):
    from agent_friday.services import credential_store
    stored = {}
    monkeypatch.setattr(credential_store, "set_provider_key",
                        lambda p, k: stored.__setitem__(p, k))
    ob.advance("Riley")            # -> voice_test
    ob.advance("hey")              # -> keys
    ob.advance("", key_provider="anthropic", key_value="sk-abc")  # keys -> identity
    assert stored.get("anthropic") == "sk-abc"
    assert "anthropic" in ob.load_state()["keys_added"]


def test_soul_step_seeds_preference():
    ob.advance("Sam")              # -> voice_test
    ob.advance("hi")               # -> keys
    ob.advance("")                 # -> identity
    ob.advance("")                 # -> soul
    ob.advance("Be extra concise and witty.")  # soul -> done, seeds SOUL.md
    assert "extra concise and witty" in soul.load_soul().lower()


def test_complete_is_idempotent():
    ob.complete()
    r = ob.complete()
    assert r["complete"] is True
    assert ob.is_complete() is True


def test_advance_null_answer_does_not_crash():
    # Regression: a client POSTing {"answer": null} passes None; must not raise.
    r = ob.advance(None)               # greet/name
    assert r["ok"] is True
    r = ob.advance(None)               # voice_test
    assert r["ok"] is True
    ob.advance("")                     # keys
    ob.advance("")                     # identity
    r = ob.advance(None)               # soul (None must not crash)
    assert r["ok"] is True


def test_key_save_failure_surfaces_warning(monkeypatch):
    # Regression: a failed key store must not silently look like success.
    from agent_friday.services import credential_store

    def boom(p, k):
        raise RuntimeError("keyring backend down")
    monkeypatch.setattr(credential_store, "set_provider_key", boom)
    ob.advance("Sam")                  # -> voice_test
    ob.advance("hi")                   # -> keys
    r = ob.advance("", key_provider="anthropic", key_value="sk-x")
    assert r.get("key_saved") is False
    assert "warning" in r
