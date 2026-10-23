"""`friday resume`: the owner lets outward actions go again after a restore,
from a terminal on this PC, as the Doctor page's Resume does. It asks first,
records who resumed in the action ledger, names any hold that still stands,
and cannot be run by Friday itself while the restore hold stands."""
from __future__ import annotations

import argparse
import json
import sys

import pytest

from agent_friday import cli
from agent_friday.governance import action_gate
from agent_friday.governance import proof_of_integrity as poi


@pytest.fixture
def held(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("FRIDAY_HOME", str(home))
    monkeypatch.setattr(action_gate, "friday_home", lambda: str(home))
    monkeypatch.setattr(poi, "_GOV_KEY_FILE", home / "vault" / ".governance-key")
    monkeypatch.setitem(sys.modules, "keyring", None)
    from agent_friday.services import action_ledger, friday_bundle
    action_ledger.reset_caches()
    p = friday_bundle.hold_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"bundle_id": "fb-test"}), encoding="utf-8")
    return p


def _args(**kw):
    return argparse.Namespace(status=kw.get("status", False), yes=kw.get("yes", False))


def _resumed_by():
    from agent_friday.services import action_ledger
    return [(e.get("rec") or {}).get("by") for e in action_ledger.entries(["restore_resumed"])]


def test_the_command_is_on_the_command_line(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["friday", "resume", "--help"])
    with pytest.raises(SystemExit):
        cli.main()
    assert "--yes" in capsys.readouterr().out


def test_status_says_what_waits_and_changes_nothing(held, capsys):
    assert cli.cmd_resume(_args(status=True)) == 0
    assert "fb-test" in capsys.readouterr().out
    assert held.exists() and _resumed_by() == []


def test_yes_resumes_and_records_the_owner_on_the_command_line(held, capsys):
    assert cli.cmd_resume(_args(yes=True)) == 0
    assert "Resumed" in capsys.readouterr().out
    assert not held.exists()
    assert _resumed_by() == ["owner:cli"]
    assert action_gate.outward_hold() is None or "restored" not in action_gate.outward_hold()


def test_without_a_terminal_or_yes_nothing_changes(held, monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    assert cli.cmd_resume(_args()) == 1
    assert held.exists() and _resumed_by() == []


@pytest.mark.parametrize("typed", ["", "no", "y", "yes please"])
def test_anything_but_yes_at_the_prompt_changes_nothing(held, monkeypatch, typed):
    monkeypatch.setattr(sys, "stdin", type("T", (), {"isatty": lambda self: True})())
    monkeypatch.setattr("builtins.input", lambda prompt="": typed)
    assert cli.cmd_resume(_args()) == 1
    assert held.exists()


def test_yes_at_the_prompt_resumes(held, monkeypatch):
    monkeypatch.setattr(sys, "stdin", type("T", (), {"isatty": lambda self: True})())
    monkeypatch.setattr("builtins.input", lambda prompt="": " Yes ")
    assert cli.cmd_resume(_args()) == 0
    assert not held.exists()


def test_a_hold_that_still_stands_is_named(held, monkeypatch, capsys):
    from agent_friday.services import safe_mode
    monkeypatch.setattr(safe_mode, "active", lambda *a, **k: True)
    assert cli.cmd_resume(_args(yes=True)) == 0
    assert "Still held" in capsys.readouterr().out


def test_nothing_to_resume_is_said_plainly(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    assert cli.cmd_resume(_args(yes=True)) == 0
    assert "not waiting" in capsys.readouterr().out


def test_friday_cannot_run_the_command_itself_while_held(held):
    v = action_gate.authorize("run_command", {"command": "friday resume --yes"},
                              {"is_background_task": True})
    assert v.action == "deny" and "restored" in v.reason, v
    assert held.exists()
