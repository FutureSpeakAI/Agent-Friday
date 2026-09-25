"""`friday export` holds the owner's data, not the keys to their credentials.

The keystore root key plus the credential blobs decrypt every stored API key
and account token, so a plain export leaves them out. A full backup includes
them only inside a passphrase-encrypted file. Neither is written into the
application folder or into ~/.friday itself.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from agent_friday import cli
from agent_friday.privacy import vault_crypto as vc

SECRETS = [
    "security/keystore.json",
    "security/vault-passphrase.dpapi",
    "providers/keys/anthropic.key",
    "google_accounts/tokens/a.token.enc",
    "mcp_oauth/srv.oauth.enc",
    "phone/secrets/auth_token.bin",
    "platforms/x.cred",
    "secret_key",
    "vault/.governance-key",
    "vault/.attestation-key-ed25519",
    "grants/ledger_signing.key",
    "local-address/ca-key.pem",
    "tls/key.pem",
]
DATA = ["wiki/notes/page.md", "settings.json", "vault/finance/ledger.vault",
        "local-address/ca.pem"]
DOWNLOADS = ["runtime/models/gguf/x.gguf", "models/nemo/y.bin"]


@pytest.fixture
def home(tmp_path, monkeypatch):
    fh = tmp_path / ".friday"
    for rel in SECRETS + DATA + DOWNLOADS:
        p = fh / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("content of " + rel, encoding="utf-8")
    app = tmp_path / "app"
    app.mkdir()
    docs = tmp_path / "Documents"
    docs.mkdir()
    monkeypatch.setattr(cli, "FRIDAY_DIR", fh)
    monkeypatch.setattr(cli, "PROJ_ROOT", app)
    monkeypatch.setattr(cli, "_documents_dir", lambda: docs, raising=False)
    monkeypatch.setattr(cli, "_backup_profile", lambda: vc.FAST_PROFILE, raising=False)
    monkeypatch.chdir(app)          # the installed launcher's working directory
    return tmp_path


def _names(zpath: Path) -> set:
    with zipfile.ZipFile(zpath) as zf:
        return {n.replace("\\", "/") for n in zf.namelist()}


def test_default_export_has_data_and_no_secrets(home):
    cli.cmd_export()
    assert not list((home / "app").glob("*.zip")), "nothing is written into the app folder"
    out = list((home / "Documents").glob("friday-data-export-*.zip"))
    assert len(out) == 1
    names = _names(out[0])
    for rel in DATA:
        assert ".friday/" + rel in names
    for rel in SECRETS + DOWNLOADS:
        assert ".friday/" + rel not in names, rel


def test_export_honours_out_and_refuses_the_app_and_data_folders(home):
    target = home / "backups"
    target.mkdir()
    cli.cmd_export(out=str(target))
    assert len(list(target.glob("friday-data-export-*.zip"))) == 1
    assert cli.cmd_export(out=str(home / "app")) == 1
    assert cli.cmd_export(out=str(home / ".friday")) == 1
    assert not list((home / "app").glob("*.zip"))


def test_full_backup_is_encrypted_and_round_trips(home):
    cli.cmd_export(full=True, passphrase="correct horse battery")
    out = list((home / "Documents").glob("friday-backup-*.fbak"))
    assert len(out) == 1
    raw = out[0].read_bytes()
    assert b"content of security/keystore.json" not in raw
    assert b"PK\x03\x04" not in raw[:64], "the zip is not stored in the clear"

    assert cli.cmd_decrypt_backup(str(out[0]), passphrase="wrong passphrase!!") == 1
    assert cli.cmd_decrypt_backup(str(out[0]), passphrase="correct horse battery") == 0
    names = _names(out[0].with_suffix(".zip"))
    assert ".friday/security/keystore.json" in names
    assert ".friday/wiki/notes/page.md" in names


def test_full_backup_refuses_a_short_or_missing_passphrase(home):
    assert cli.cmd_export(full=True, passphrase="short") == 1
    assert not list((home / "Documents").glob("*.fbak"))
