"""Provider keys in start.bat move into the credential store at boot (S2).

Before this change a provider key in start.bat (or launch_now.bat, friday_startup.bat)
stayed there in plain text for as long as the install lived: core reads the scripts at
every import, and only a re-run of the setup wizard ever moved them.

Now services/launch_script_keys.migrate (server boot) stores each provider key the
provider registry names, unless one is already saved; keeps the original script
encrypted; and blanks the key lines IN PLACE with same-length `rem` lines, so a cmd.exe
running the script continues at the right offset. Other lines (the vault passphrase,
FRIDAY_SECRET_KEY) stay and are named. Values below are synthetic.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_friday.services import credential_store as cs
from agent_friday.services import launch_script_keys as lk

VALUE = "synthetic-launch-value-0001"


@pytest.fixture
def anthropic_env():
    names = lk._provider_env_names()
    env = next((k for k, v in names.items() if v == "anthropic"), None)
    assert env, names
    return env


@pytest.fixture
def clean():
    cs.delete_provider_key("anthropic")
    yield
    cs.delete_provider_key("anthropic")


def _script(tmp_path, anthropic_env, name="start.bat", eol="\r\n"):
    app = tmp_path / "app"
    app.mkdir(exist_ok=True)
    body = eol.join([
        "@echo off",
        f"set {anthropic_env}={VALUE}",
        "set FRIDAY_PASSWORD=vault-pass-stays-here",
        'set "OTHER_SETTING=keep me"',
        'cd /d "C:\\Friday"',
        "python server.py",
        "pause",
    ]) + eol
    (app / name).write_bytes(body.encode("utf-8"))
    return app, body


def test_a_key_moves_and_the_line_is_blanked_in_place(tmp_path, anthropic_env, clean):
    app, before = _script(tmp_path, anthropic_env)
    (r,) = lk.migrate(app)
    assert r["changed"] is True and r["imported"] == ["anthropic"]
    assert cs.get_provider_key("anthropic") == VALUE
    after = (app / "start.bat").read_bytes()
    assert VALUE.encode() not in after
    # Same length, same offsets: a running cmd.exe is not thrown off.
    assert len(after) == len(before.encode())
    old_lines, new_lines = before.split("\r\n"), after.decode().split("\r\n")
    assert [len(x) for x in old_lines] == [len(x) for x in new_lines]
    assert new_lines[1].startswith("rem ")
    # Everything else is untouched, and named when it is a set line it did not move.
    assert "set FRIDAY_PASSWORD=vault-pass-stays-here" in new_lines
    assert "python server.py" in new_lines
    assert "FRIDAY_PASSWORD" in r["left"]
    # The original is kept, encrypted.
    kept = lk._legacy_dir() / r["archived_as"]
    assert VALUE.encode() not in kept.read_bytes()
    assert VALUE in cs.read_secret(kept).decode()
    assert VALUE not in repr(r)


def test_a_saved_key_wins_and_the_stale_line_still_goes(tmp_path, anthropic_env, clean):
    cs.set_provider_key("anthropic", "saved-in-settings")
    app, _ = _script(tmp_path, anthropic_env, name="launch_now.bat", eol="\n")
    (r,) = lk.migrate(app)
    assert r["kept_saved"] == ["anthropic"] and r["imported"] == []
    assert cs.get_provider_key("anthropic") == "saved-in-settings"
    assert VALUE not in (app / "launch_now.bat").read_text()


def test_nothing_changes_when_the_store_cannot_write(tmp_path, anthropic_env, clean, monkeypatch):
    app, before = _script(tmp_path, anthropic_env)

    def refuse(*a, **k):
        raise RuntimeError("keystore locked")
    monkeypatch.setattr(cs, "set_provider_key", refuse)
    (r,) = lk.migrate(app)
    assert r["changed"] is False and "left as it is" in r["error"]
    assert (app / "start.bat").read_bytes() == before.encode()


def test_a_saved_key_that_does_not_decrypt_leaves_the_script(tmp_path, anthropic_env, clean, monkeypatch):
    app, before = _script(tmp_path, anthropic_env)
    monkeypatch.setattr(cs, "provider_key_status", lambda p: "present_but_unreadable")
    (r,) = lk.migrate(app)
    assert r["changed"] is False
    assert (app / "start.bat").read_bytes() == before.encode()


def test_core_no_longer_reads_a_key_from_the_blanked_script(tmp_path, anthropic_env, clean):
    import re
    app, _ = _script(tmp_path, anthropic_env)
    lk.migrate(app)
    set_re = re.compile(r'^\s*set\s+"?([A-Za-z_][A-Za-z0-9_]*)=([^"\r\n]*)"?\s*$', re.IGNORECASE)
    names = [m.group(1) for ln in (app / "start.bat").read_text().splitlines()
             for m in [set_re.match(ln)] if m]
    assert anthropic_env not in names and "FRIDAY_PASSWORD" in names


def test_the_test_suite_never_touches_a_real_checkout(monkeypatch):
    monkeypatch.setenv("FRIDAY_TESTING", "1")
    assert lk.migrate() == []


def test_boot_runs_it_before_the_stored_keys_load():
    src = (Path(__file__).resolve().parents[2] / "src" / "agent_friday" / "server.py").read_text(encoding="utf-8")
    assert src.index("launch_script_keys import migrate") < src.index("bootstrap_provider_env_detail()")
