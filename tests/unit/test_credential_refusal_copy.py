"""What Friday says when a path is refused reads correctly for every class of
path: a file under a denied folder is a file, a device path is not "key
material", an unreadable certificate is not called a key. Synthetic paths."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from agent_friday.services import credential_paths as cred

_BOILER = "so nothing slipped into a page, email or document"


def _mk(p: Path, body="x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


@pytest.mark.parametrize("rel", [
    ".ssh/id_ed25519", ".gnupg/pubring.kbx", ".kube/config", ".azure/tokens.json",
    ".friday/security/notes.txt", ".aws/credentials", ".friday/backups/x/notes.txt",
])
def test_a_file_under_a_denied_folder_is_named_as_a_file_in_a_folder(test_home, rel):
    f = _mk(Path(test_home) / rel)
    line = cred.refusal(f)
    assert line.startswith(f"I won't open {f.name}")
    assert re.search(r"\bsits in\b", line), line
    assert "directory" not in line.lower(), line


def test_the_folder_itself_is_called_a_folder(test_home):
    d = Path(test_home) / ".ssh"
    d.mkdir(parents=True, exist_ok=True)
    line = cred.refusal(d)
    assert "folder" in line and "sits in" not in line, line


@pytest.mark.parametrize("target", [
    "\\\\?\\GLOBALROOT\\Device\\HarddiskVolumeShadowCopy1\\x",
    "\\\\.\\PhysicalDrive0",
])
def test_a_device_path_reads_naturally(target):
    line = cred.refusal(target)
    assert "device" in line
    assert "key material" not in line and "You can open it yourself" not in line, line
    assert "GLOBALROOT" not in line and "PhysicalDrive" not in line, line


def test_a_certificate_that_cannot_be_read_is_not_called_a_key(test_home):
    line = cred.refusal(Path(test_home) / "Documents" / "absent.pem")
    assert "certificate" in line
    assert "it's a private key" not in line, line


def test_a_pem_holding_a_key_is_called_a_key(test_home):
    f = _mk(Path(test_home) / "Documents" / "k.pem",
            "-----BEGIN PRIVATE KEY-----\nAAAAFAKEFAKEFAKEFAKE\n-----END PRIVATE KEY-----\n")  # pragma: allowlist secret
    assert "it's a private key" in cred.refusal(f)


@pytest.mark.parametrize("make", [
    lambda h: _mk(h / ".ssh" / "id_rsa"),
    lambda h: _mk(h / "Documents" / "id_ed25519"),
    lambda h: _mk(h / "Documents" / "login.kdbx"),
    lambda h: "\\\\?\\GLOBALROOT\\x",
    lambda h: h / "Documents" / "absent.pem",
])
def test_refusals_are_short_and_never_carry_the_old_boilerplate(test_home, make):
    line = cred.refusal(make(Path(test_home)))
    assert _BOILER not in line
    assert len(line.split()) <= 36, (len(line.split()), line)
    assert "the user" not in line.lower()


def test_the_command_refusal_is_short_and_reads_with_every_reason():
    for why in ("an SSH key directory", "a device path", "Friday's keystore"):
        line = cred.refusal_command(why)
        assert _BOILER not in line and len(line.split()) <= 32, line
        assert why in line


def test_the_share_refusal_says_what_it_is(test_home):
    line = cred.refusal("\\\\localhost\\NoSuchShare\\x.txt")
    assert "share" in line and "key material" not in line, line
