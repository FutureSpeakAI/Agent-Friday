"""The bundle passphrase strength meter (services/passphrase_strength.py).

A Friday Bundle carries the vault passphrase and the governance key inside its
encryption, so its passphrase is checked wherever one is set: the Doctor page,
the command line and the uninstall prompt (which runs the command line).

Red on the old code: the module did not exist, and a bundle accepted any
passphrase of 12 characters, "xxxxxxxxxxxx" and "password1234" included.
"""
from __future__ import annotations

import pytest


def _ps():
    from agent_friday.services import passphrase_strength
    return passphrase_strength

WEAK = ["xxxxxxxxxxxxxxxxxxxx", "password1234", "qwertyuiop123456", "abcdefghijklmnop",
        "correct horse battery staple", "correct horse battery staple 42", "summer2024summer",
        "my friday backup 2024", "iloveyou12345", "aaaaaaaaaaaaaaaa", "short"]
STRONG = ["orbit lantern pepper tundra violet 42", "violet anchor drizzle mammoth",
          "kL9#vQ2!mZ7@pX4$", "ci clean machine walnut harbour quartz"]


@pytest.mark.parametrize("pw", WEAK)
def test_weak_passphrases_are_refused_with_a_reason(pw):
    ps = _ps()
    a = ps.assess(pw)
    assert not a["ok"] and a["score"] <= 2, a
    assert ps.refusal(pw) and a["feedback"]


@pytest.mark.parametrize("pw", STRONG)
def test_strong_passphrases_pass(pw):
    ps = _ps()
    a = ps.assess(pw)
    assert a["ok"] and a["score"] >= 3, a
    assert ps.refusal(pw) is None


def test_the_meter_never_returns_the_passphrase():
    a = _ps().assess("orbit lantern pepper tundra violet 42")
    assert "orbit" not in repr(a)


def test_a_bundle_refuses_a_weak_passphrase(tmp_path, monkeypatch):
    from agent_friday.services import friday_bundle as fb
    home = tmp_path / "home"
    (home / "core_memory").mkdir(parents=True)
    (home / "core_memory" / "ME.md").write_text("x", encoding="utf-8")
    with pytest.raises(fb.BundleError) as e:
        fb.create(tmp_path / "out.fridaybundle", "password1234", home=home)
    assert "guess" in str(e.value) or "weak" in str(e.value)
    assert not (tmp_path / "out.fridaybundle").exists()


def test_the_command_line_refuses_a_weak_passphrase_from_the_environment(tmp_path, monkeypatch, capsys):
    import sys
    from agent_friday import cli
    monkeypatch.setenv("FRIDAY_BUNDLE_PASSPHRASE", "xxxxxxxxxxxxxxxx")
    monkeypatch.setattr(sys, "argv", ["friday", "bundle", "create", "--out", str(tmp_path / "x.fridaybundle")])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 1
    assert not (tmp_path / "x.fridaybundle").exists()
