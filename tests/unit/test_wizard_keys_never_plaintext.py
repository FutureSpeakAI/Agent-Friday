"""The terminal setup wizard keeps provider keys only in the credential store.

Earlier versions copied every key entered in `friday setup` into
config.yaml, settings.json and start.bat in plain text. The wizard now writes
none of them, and on its next run moves keys it finds in those files into the
credential store and removes the plaintext copy. A key that cannot be stored,
or that conflicts with a different stored key, is left where it is.

Everything runs against a fake home: the wizard's paths are pointed at
tmp_path and the credential store is a dict.
"""
from __future__ import annotations

import json

import pytest
import yaml

from agent_friday import setup_brain as sb
from agent_friday import setup_wizard as w

ANTH = "sk-ant-test-" + "a" * 20      # pragma: allowlist secret
GEM = "AIza-test-" + "b" * 20          # pragma: allowlist secret


@pytest.fixture
def home(tmp_path, monkeypatch):
    fh = tmp_path / ".friday"
    fh.mkdir()
    app = tmp_path / "app"
    app.mkdir()
    monkeypatch.setattr(w, "FRIDAY_DIR", fh)
    monkeypatch.setattr(w, "SETTINGS_FILE", fh / "settings.json")
    monkeypatch.setattr(w, "CONFIG_YAML", fh / "config.yaml")
    monkeypatch.setattr(w, "SETUP_MARKER", fh / ".setup_complete")
    monkeypatch.setattr(w, "PROJ_ROOT", app)
    return tmp_path


@pytest.fixture
def store(monkeypatch):
    """A fake credential store. `fail` lists providers whose store fails."""
    data: dict = {}
    fail: set = set()

    def store_key(provider, key):
        if provider in fail:
            return False, "no"
        data[provider] = key
        return True, "ok"

    monkeypatch.setattr(sb, "store_key", store_key)
    monkeypatch.setattr(w, "_stored_provider_key", lambda p: data.get(p, ""),
                        raising=False)
    data["_fail"] = fail
    return data


def _all_plaintext(home):
    out = ""
    for p in (home / ".friday" / "settings.json", home / ".friday" / "config.yaml",
              home / "app" / "start.bat"):
        if p.exists():
            out += p.read_text(encoding="utf-8")
    return out


def test_persist_writes_no_key_anywhere(home, store):
    config = {"agent_name": "FRIDAY", "anthropic_api_key": ANTH,
              "gemini_api_key": GEM, "vault_password": "pw-secret",
              "preferred_scene_index": -1}
    w._persist(config)
    text = _all_plaintext(home)
    assert ANTH not in text and GEM not in text and "pw-secret" not in text
    assert "API_KEY" not in (home / "app" / "start.bat").read_text(encoding="utf-8")
    assert json.loads((home / ".friday" / "settings.json").read_text())["agent_name"] == "FRIDAY"


def test_existing_plaintext_keys_move_to_the_store_and_are_scrubbed(home, store):
    (home / ".friday" / "settings.json").write_text(json.dumps(
        {"agent_name": "FRIDAY", "anthropic_api_key": ANTH}), encoding="utf-8")
    (home / ".friday" / "config.yaml").write_text(yaml.dump(
        {"agent_name": "FRIDAY", "anthropic_api_key": ANTH, "gemini_api_key": GEM}),
        encoding="utf-8")
    (home / "app" / "start.bat").write_bytes(
        ("@echo off\r\nSET ANTHROPIC_API_KEY=%s\r\nSET GEMINI_API_KEY=%s\r\n"  # pragma: allowlist secret
         "python server.py\r\n" % (ANTH, GEM)).encode("utf-8"))

    report = w.migrate_plaintext_keys()

    assert store["anthropic"] == ANTH and store["google-gemini"] == GEM
    assert not report["kept"]
    text = _all_plaintext(home)
    assert ANTH not in text and GEM not in text
    bat = (home / "app" / "start.bat").read_bytes()
    assert b"python server.py\r\n" in bat, "start.bat keeps its other lines and CRLF"
    assert json.loads((home / ".friday" / "settings.json").read_text())["agent_name"] == "FRIDAY"
    assert yaml.safe_load((home / ".friday" / "config.yaml").read_text())["agent_name"] == "FRIDAY"


def test_a_key_that_fails_to_store_is_never_deleted(home, store):
    store["_fail"].add("anthropic")
    (home / "app" / "start.bat").write_text(
        "@echo off\nSET ANTHROPIC_API_KEY=%s\n" % ANTH, encoding="utf-8")
    report = w.migrate_plaintext_keys()
    assert ANTH in (home / "app" / "start.bat").read_text(encoding="utf-8")
    assert ("start.bat", "anthropic", "it could not be stored encrypted") in report["kept"]
    # A later _persist must not overwrite the start.bat still holding it.
    w._persist({"agent_name": "F", "preferred_scene_index": -1})
    assert ANTH in (home / "app" / "start.bat").read_text(encoding="utf-8")


def test_a_different_stored_key_is_not_overwritten_and_the_copy_is_kept(home, store):
    store["anthropic"] = "sk-ant-other-" + "c" * 20   # pragma: allowlist secret
    (home / ".friday" / "settings.json").write_text(json.dumps(
        {"anthropic_api_key": ANTH}), encoding="utf-8")
    report = w.migrate_plaintext_keys()
    assert store["anthropic"].startswith("sk-ant-other-")
    assert ANTH in (home / ".friday" / "settings.json").read_text(encoding="utf-8")
    assert report["kept"] and not report["moved"]


def test_the_abandon_path_stores_the_new_passphrase_and_does_not_claim_start_bat(
        home, monkeypatch):
    import agent_friday.services.vault_passphrase as vp
    stored = []
    monkeypatch.setattr(vp, "store", lambda pw: stored.append(pw) or ["keychain"])
    printed = []
    monkeypatch.setattr(w.console, "print", lambda *a, **k: printed.append(" ".join(map(str, a))))
    monkeypatch.setattr(w, "_clear", lambda: None)
    answers = iter(["3", "abandon", ""])
    monkeypatch.setattr(w.Prompt, "ask", staticmethod(lambda *a, **k: next(answers)))

    got = w._vault_lost_passphrase(12, already_explained=True)

    assert got and stored == [got]
    assert not any("start.bat" in line for line in printed)
