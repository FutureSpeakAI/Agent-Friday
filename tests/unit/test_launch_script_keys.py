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


def test_a_line_equal_to_the_saved_key_goes(tmp_path, anthropic_env, clean):
    cs.set_provider_key("anthropic", VALUE)
    app, _ = _script(tmp_path, anthropic_env, name="launch_now.bat", eol="\n")
    (r,) = lk.migrate(app)
    assert r["kept_saved"] == ["anthropic"] and r["imported"] == []
    assert VALUE not in (app / "launch_now.bat").read_text()


def test_a_line_that_differs_from_the_saved_key_stays(tmp_path, anthropic_env, clean):
    """Which one is newer is not knowable: nothing is lost either way."""
    cs.set_provider_key("anthropic", "saved-in-settings")
    app, before = _script(tmp_path, anthropic_env)
    (r,) = lk.migrate(app)
    assert r["changed"] is False
    assert any("differs from the saved key" in x for x in r["left"])
    assert (app / "start.bat").read_bytes() == before.encode()
    assert cs.get_provider_key("anthropic") == "saved-in-settings"


def test_two_different_values_for_one_key_are_left_alone(tmp_path, anthropic_env, clean):
    app = tmp_path / "app"
    app.mkdir()
    (app / "start.bat").write_bytes(f"set {anthropic_env}={VALUE}\r\npython server.py\r\n".encode())
    (app / "launch_now.bat").write_bytes(f"set {anthropic_env}=a-newer-value-0002\r\n".encode())
    res = lk.migrate(app)
    assert all(not r["changed"] for r in res)
    assert cs.provider_key_status("anthropic") == "missing"


@pytest.mark.parametrize("line", [
    "set {k}=%s && python server.py",
    "if 1==1 (set {k}=%s)",
    "set {k}=%s^",
    "set {k}=%%OTHER%%",
])
def test_a_line_that_is_more_than_an_assignment_is_left(tmp_path, anthropic_env, clean, line):
    app = tmp_path / "app"
    app.mkdir()
    text = (line.format(k=anthropic_env) % VALUE if "%s" in line else line.format(k=anthropic_env) % ())
    data = ("@echo off\r\n" + text + "\r\npause\r\n").encode()
    (app / "start.bat").write_bytes(data)
    (r,) = lk.migrate(app)
    assert r["changed"] is False
    assert (app / "start.bat").read_bytes() == data


def test_an_ansi_script_is_left_as_it_is(tmp_path, anthropic_env, clean):
    app = tmp_path / "app"
    app.mkdir()
    data = f"rem caf\xe9\r\nset {anthropic_env}={VALUE}\r\n".encode("latin-1")
    (app / "start.bat").write_bytes(data)
    (r,) = lk.migrate(app)
    assert r["changed"] is False and "UTF-8" in r["error"]
    assert (app / "start.bat").read_bytes() == data
    assert cs.provider_key_status("anthropic") == "missing"


def test_every_later_import_gets_the_moved_key_back_from_the_store(tmp_path, anthropic_env, clean, monkeypatch):
    app, _ = _script(tmp_path, anthropic_env)
    lk.migrate(app)
    monkeypatch.setenv(anthropic_env, "a-stale-windows-variable")
    got = lk.restore_moved_keys(already_loaded=set(), force_names={anthropic_env})
    assert anthropic_env in got
    import os
    assert os.environ[anthropic_env] == VALUE          # the store beats a stale variable, as the line did
    # A script line that still sets it wins, as before.
    monkeypatch.setenv(anthropic_env, "from-a-script")
    assert lk.restore_moved_keys(already_loaded={anthropic_env}, force_names={anthropic_env}) == []


def test_core_constants_take_the_moved_key(tmp_path, anthropic_env, clean, monkeypatch):
    import agent_friday.core as core
    app, _ = _script(tmp_path, anthropic_env)
    lk.migrate(app)
    monkeypatch.setattr(core, anthropic_env, "", raising=False)
    monkeypatch.setenv(anthropic_env, VALUE)
    lk.refresh_core_constants()
    assert getattr(core, anthropic_env) == VALUE


def test_only_built_in_api_key_variables_are_ever_moved(monkeypatch):
    class Reg:
        def list_providers(self):
            return [{"name": "anthropic", "auth": {"type": "env_var", "key": "ANTHROPIC_API_KEY"}},
                    {"name": "mine", "auth": {"type": "env_var", "key": "FRIDAY_PASSWORD"}},
                    {"name": "mine2", "auth": {"type": "env_var", "key": "OTHER_API_KEY"}}]

        def provider_origin(self, name):
            return "builtin" if name == "anthropic" else "custom"
    import agent_friday.services.provider_registry as pr
    monkeypatch.setattr(pr, "get_provider_registry", lambda: Reg())
    assert lk._provider_env_names() == {"ANTHROPIC_API_KEY": "anthropic"}


def test_the_moved_names_file_is_a_record_not_an_instruction(tmp_path, anthropic_env, clean, monkeypatch):
    import json
    import os
    cs.set_provider_key("anthropic", VALUE)
    lk._moved_path().parent.mkdir(parents=True, exist_ok=True)
    lk._moved_path().write_text(json.dumps({"names": {"OPENAI_API_KEY": "anthropic",
                                                      "FRIDAY_PASSWORD": "anthropic"}}), encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("FRIDAY_PASSWORD", raising=False)
    try:
        assert lk.restore_moved_keys(force_names={"OPENAI_API_KEY"}) == []
        assert not os.environ.get("OPENAI_API_KEY") and not os.environ.get("FRIDAY_PASSWORD")
    finally:
        lk._moved_path().unlink()


def test_an_unsafe_line_for_the_same_name_keeps_the_plain_one_too(tmp_path, anthropic_env, clean):
    app = tmp_path / "app"
    app.mkdir()
    data = f"set {anthropic_env}={VALUE}\r\nset {anthropic_env}=other && echo x\r\n".encode()
    (app / "start.bat").write_bytes(data)
    (r,) = lk.migrate(app)
    assert r["changed"] is False and (app / "start.bat").read_bytes() == data


def test_an_unreadable_script_stops_every_move(tmp_path, anthropic_env, clean):
    app = tmp_path / "app"
    app.mkdir()
    good = f"set {anthropic_env}={VALUE}\r\n".encode()
    (app / "start.bat").write_bytes(good)
    (app / "launch_now.bat").write_bytes(b"rem caf\xe9\r\n")
    res = lk.migrate(app)
    assert all(not r["changed"] for r in res)
    assert (app / "start.bat").read_bytes() == good


def test_a_leftover_temp_file_is_removed(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    (app / "start.bat.friday-tmp").write_text("set FRIDAY_PASSWORD=x", encoding="utf-8")
    (app / "start.bat").write_text("@echo off\r\n", encoding="utf-8")
    lk.migrate(app)
    assert not (app / "start.bat.friday-tmp").exists()


def test_core_restores_moved_keys_at_import():
    src = (Path(__file__).resolve().parents[2] / "src" / "agent_friday" / "core" / "__init__.py").read_text(encoding="utf-8")
    body = src[src.index("def _bootstrap_env_from_launch_scripts"):]
    body = body[:body.index("_bootstrap_env_from_launch_scripts()")]
    assert "restore_moved_keys(already_loaded=loaded, force_names=_FORCE_OVERRIDE)" in body


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
