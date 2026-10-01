r"""A deny-listed path stays denied however Windows lets it be spelt.

`\\?\C:\x`, `\\.\C:\x`, `\\?\UNC\localhost\C$\x` and `\\localhost\C$\x` all
reach the same file as `C:\x`. The directory rules must not be walkable by
spelling. All secret values are synthetic.
"""
from __future__ import annotations

import os
import sys

import pytest

from agent_friday.services import credential_paths as cred

pytestmark = pytest.mark.skipif(sys.platform != "win32",
                                reason="drive-letter spellings are Windows-only")

SECRET = "AWSSECRETSENTINEL0123456789"  # pragma: allowlist secret


def _spellings(plain: str):
    drive, rest = plain[0], plain[2:]
    return [
        "\\\\?\\" + plain,
        "\\\\.\\" + plain,
        "//?/" + plain.replace("\\", "/"),
        "\\\\?\\UNC\\localhost\\" + drive + "$" + rest,
        "\\\\localhost\\" + drive + "$" + rest,
        "\\\\127.0.0.1\\" + drive + "$" + rest,
        "\\\\::1\\" + drive + "$" + rest,
        "\\\\" + (os.environ.get("COMPUTERNAME") or "host") + "\\" + drive.lower()
        + "$" + rest,
    ]


DIR_TARGETS = [
    (".aws", "credentials"),
    (".ssh", "config"),
    (".config/gh", "hosts.yml"),
    (".friday/backups/vault-reencrypt-1", "context.log"),
    (".friday/security", "notes.txt"),
    (".friday/providers/keys", "openrouter.key"),
]


@pytest.mark.parametrize("rel", DIR_TARGETS, ids=[d + "/" + f for d, f in DIR_TARGETS])
@pytest.mark.parametrize("idx", range(8))
def test_check_denies_every_spelling(test_home, rel, idx):
    d, f = rel
    plain = str(test_home / d.replace("/", os.sep) / f)
    assert cred.check(plain, sniff=False), plain
    spelt = _spellings(plain)[idx]
    assert cred.check(spelt, sniff=False), spelt


@pytest.mark.parametrize("idx", range(8))
def test_read_file_withholds_every_spelling(test_home, idx):
    import agent_friday.services.agent as agent
    f = test_home / ".aws" / "credentials"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("[default]\nnote = " + SECRET + "\n", encoding="utf-8")
    out = agent._tool_read_file({"path": _spellings(str(f))[idx]})
    assert SECRET not in out


@pytest.mark.parametrize("idx", range(8))
def test_open_path_refuses_every_spelling(test_home, monkeypatch, idx):
    import agent_friday.services.agent as agent
    f = test_home / ".aws" / "credentials"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("x", encoding="utf-8")
    opened = []
    monkeypatch.setattr(os, "startfile", lambda p: opened.append(p), raising=False)
    out = agent._tool_open_path({"path": _spellings(str(f))[idx]})
    assert not opened
    assert "won't open" in out.lower()


@pytest.mark.parametrize("target", [
    "\\\\?\\GLOBALROOT\\Device\\HarddiskVolumeShadowCopy1\\Users\\x\\.ssh\\config",
    "\\\\?\\Volume{01234567-89ab-cdef-0123-456789abcdef}\\Users\\x\\.aws\\credentials",
    "\\\\.\\PhysicalDrive0",
])
def test_device_paths_are_refused(test_home, target):
    assert cred.check(target, sniff=False)


def test_ordinary_spellings_stay_readable(test_home):
    note = test_home / "Documents" / "notes.txt"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("groceries", encoding="utf-8")
    for s in _spellings(str(note)):
        assert cred.check(s) is None, s
