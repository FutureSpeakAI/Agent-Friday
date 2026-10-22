"""Plaintext key files an older release left beside the app do not survive an upgrade.

Before this change `.env` and `secrets.yaml` in the app folder were carried across every
upgrade (install.ps1 AppUserFiles) and nothing ever read or removed them: a plaintext copy
of the owner's keys outlived the credential store (astra progress, drift item 8).

Now services/legacy_key_files.migrate (run at boot, installed copies only) puts each
provider key it recognises into the credential store unless one is saved there already,
keeps the whole file encrypted, and deletes the plaintext; when the store cannot write,
the file is left untouched for the next boot. The values below are synthetic.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_friday.services import credential_store as cs
from agent_friday.services import legacy_key_files as lk

VALUE = "synthetic-legacy-value-0001"
OTHER = "synthetic-other-line-0002"


@pytest.fixture
def anthropic_env():
    names = lk._provider_env_names()
    env = next((k for k, v in names.items() if v == "anthropic"), None)
    assert env, "the provider registry names no env var for anthropic: %r" % names
    return env


@pytest.fixture
def clean():
    cs.delete_provider_key("anthropic")
    yield
    cs.delete_provider_key("anthropic")


def _app(tmp_path) -> Path:
    root = tmp_path / "AgentFriday"
    app = root / "app"
    app.mkdir(parents=True)
    (root / "install-manifest.json").write_text("{}", encoding="utf-8")
    return app


def test_a_dotenv_key_moves_into_the_store_and_the_plaintext_goes(tmp_path, anthropic_env, clean):
    app = _app(tmp_path)
    f = app / ".env"
    f.write_text(f"# old file\n{anthropic_env}=\"{VALUE}\"\nSOMETHING_ELSE={OTHER}\n", encoding="utf-8")
    (r,) = lk.migrate(app)
    assert r["moved"] is True and r["imported"] == ["anthropic"]
    assert not f.exists()
    assert cs.get_provider_key("anthropic") == VALUE
    # Nothing in the file is lost: the whole file is kept encrypted.
    kept = lk._legacy_dir() / r["archived_as"]
    raw = kept.read_bytes()
    assert VALUE.encode() not in raw and OTHER.encode() not in raw
    assert OTHER in cs.read_secret(kept).decode()
    # No value in the result or the audit.
    assert VALUE not in repr(r) and OTHER not in repr(r)
    assert all(VALUE not in repr(e) for e in cs.read_audit("legacy_key_file"))


def test_a_key_saved_in_settings_wins(tmp_path, anthropic_env, clean):
    cs.set_provider_key("anthropic", "saved-in-settings")
    app = _app(tmp_path)
    (app / "secrets.yaml").write_text(f"{anthropic_env}: {VALUE}\n", encoding="utf-8")
    (r,) = lk.migrate(app)
    assert r["kept_saved"] == ["anthropic"] and r["imported"] == []
    assert cs.get_provider_key("anthropic") == "saved-in-settings"
    assert not (app / "secrets.yaml").exists()


def test_when_the_store_cannot_write_the_file_is_left_alone(tmp_path, anthropic_env, clean, monkeypatch):
    app = _app(tmp_path)
    f = app / ".env"
    f.write_text(f"{anthropic_env}={VALUE}\n", encoding="utf-8")

    def refuse(*a, **k):
        raise RuntimeError("keystore locked")
    monkeypatch.setattr(cs, "write_secret", refuse)
    (r,) = lk.migrate(app)
    assert r["moved"] is False and "left as it is" in r["error"]
    assert f.read_text(encoding="utf-8") == f"{anthropic_env}={VALUE}\n"
    assert VALUE not in repr(r)


def test_a_source_checkout_is_never_touched(tmp_path, monkeypatch):
    app = tmp_path / "checkout"
    app.mkdir()
    f = app / ".env"
    f.write_text("ANTHROPIC_API_KEY=dev\n", encoding="utf-8")
    monkeypatch.setattr(lk, "_app_dir", lambda: app)
    assert lk.migrate() == []
    assert f.exists()


def test_parse_reads_dotenv_and_flat_yaml_shapes():
    got = lk.parse('export A_KEY="x1"\nset B_KEY=x2\nC_KEY: \'x3\'\n# D_KEY=no\nE_KEY=x5 # note\n'
                   "nested:\n  F_KEY: x6\n")
    assert got["A_KEY"] == "x1" and got["B_KEY"] == "x2" and got["C_KEY"] == "x3"
    assert "D_KEY" not in got and got["E_KEY"] == "x5"


def test_boot_runs_it_before_the_stored_keys_load():
    src = (Path(__file__).resolve().parents[2] / "src" / "agent_friday" / "server.py").read_text(encoding="utf-8")
    assert src.index("legacy_key_files import migrate") < src.index("bootstrap_provider_env_detail()")
