r"""A path into THIS machine through any network share, or through a link that
resolves to one, is judged like the local path it names. Secrets synthetic."""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

import pytest

from agent_friday.services import credential_paths as cred

pytestmark = pytest.mark.skipif(sys.platform != "win32",
                                reason="UNC spellings are Windows-only")

SECRET = "AWSSECRETSENTINEL0123456789"  # pragma: allowlist secret


@pytest.fixture
def shares(test_home, monkeypatch):
    """Shares as `net share` would list them: the drive root and the home's parent."""
    root = Path(test_home).anchor
    table = {"c": root, "users": str(Path(test_home).parent), "c$": root}
    monkeypatch.setattr(cred, "_local_shares", lambda: table)
    return table


def _self_hosts():
    hosts = ["localhost", "LOCALHOST", "127.0.0.1", "127.1", "127.0.0.2",
             "::1", "2130706433", "0x7f.0.0.1",
             "localhost.", socket.gethostname(),
             (os.environ.get("COMPUTERNAME") or socket.gethostname()).lower()]
    try:
        hosts += socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        pass
    return list(dict.fromkeys(hosts))


def _rel_from_drive(p: Path) -> str:
    return str(p)[len(Path(p).anchor):]


@pytest.mark.parametrize("host", _self_hosts())
@pytest.mark.parametrize("prefix", ["\\\\", "\\\\?\\UNC\\", "//"])
def test_non_admin_share_of_this_pc_is_judged_as_the_local_path(
        test_home, shares, host, prefix):
    rest = _rel_from_drive(Path(test_home) / ".aws" / "credentials")
    spelt = prefix + host + "\\C\\" + rest
    if prefix == "//":
        spelt = spelt.replace("\\", "/")
    assert cred.check(spelt, sniff=False), spelt


def test_share_rooted_below_the_drive_is_folded(test_home, shares):
    rel = str(Path(test_home).relative_to(Path(test_home).parent))
    spelt = "\\\\localhost\\Users\\" + rel + "\\.ssh\\id_work"
    assert cred.check(spelt, sniff=False), spelt


def test_ipv6_literal_host_name_is_this_pc(test_home, shares):
    rest = _rel_from_drive(Path(test_home) / ".ssh" / "id_work")
    assert cred.check("\\\\0--1.ipv6-literal.net\\C\\" + rest, sniff=False)


def test_unknown_share_on_this_pc_is_refused_not_guessed(test_home, shares):
    why = cred.check("\\\\localhost\\NoSuchShare\\Documents\\notes.txt", sniff=False)
    assert why


def test_ordinary_file_through_a_share_of_this_pc_stays_readable(test_home, shares):
    note = Path(test_home) / "Documents" / "notes.txt"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("groceries", encoding="utf-8")
    spelt = "\\\\localhost\\C\\" + _rel_from_drive(note)
    assert cred.check(spelt) is None, spelt


def test_share_on_another_machine_is_not_ours_to_judge():
    assert cred.check("\\\\fileserver\\public\\notes.txt", sniff=False) is None


def test_read_file_withholds_the_non_admin_share_spelling(test_home, shares):
    import agent_friday.services.agent as agent
    f = Path(test_home) / ".aws" / "credentials"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("[default]\nnote = " + SECRET + "\n", encoding="utf-8")
    out = agent._tool_read_file(
        {"path": "\\\\localhost\\C\\" + _rel_from_drive(f)})
    assert SECRET not in out


def test_open_path_refuses_the_non_admin_share_spelling(test_home, shares, monkeypatch):
    import agent_friday.services.agent as agent
    f = Path(test_home) / ".aws" / "credentials"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("x", encoding="utf-8")
    opened = []
    monkeypatch.setattr(os, "startfile", lambda p: opened.append(p), raising=False)
    out = agent._tool_open_path({"path": "\\\\127.0.0.1\\C\\" + _rel_from_drive(f)})
    assert not opened
    assert "won't open" in out.lower()


def test_run_command_refuses_the_share_spelling(test_home, shares):
    rest = _rel_from_drive(Path(test_home) / ".aws" / "credentials")
    assert cred.scan_command('Get-Content "\\\\localhost\\C\\' + rest + '"')


# -- links whose target resolves to a share spelling ------------------------

def _resolves_to(monkeypatch, link: Path, target: str):
    real = Path.resolve
    base = os.path.normcase(str(link))

    def fake(self, *a, **k):
        s = os.path.normcase(str(self))
        if s == base:
            return Path(target)
        if s.startswith(base + os.sep):
            return Path(target + s[len(base):])
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "resolve", fake)


@pytest.mark.parametrize("share_form", [
    "\\\\localhost\\C$\\{rel}",
    "\\\\?\\UNC\\localhost\\C$\\{rel}",
    "\\\\127.0.0.1\\C\\{rel}",
    "\\\\?\\C:\\{rel}",
])
def test_a_link_resolving_to_a_share_spelling_is_judged_by_its_target(
        test_home, shares, monkeypatch, share_form):
    link = Path(test_home) / "Documents" / "sshlink"
    target = share_form.format(rel=_rel_from_drive(Path(test_home) / ".ssh"))
    _resolves_to(monkeypatch, link, target)
    assert cred.check(link / "id_work", sniff=False)
    assert cred.check(link, sniff=False)


def test_a_link_to_a_share_spelling_is_refused_by_open_path_and_run_command(
        test_home, shares, monkeypatch):
    import agent_friday.services.agent as agent
    link = Path(test_home) / "Documents" / "sshlink"
    _resolves_to(monkeypatch, link, "\\\\localhost\\C$\\"
                 + _rel_from_drive(Path(test_home) / ".ssh"))
    opened = []
    monkeypatch.setattr(os, "startfile", lambda p: opened.append(p), raising=False)
    out = agent._tool_open_path({"path": str(link / "id_work")})
    assert not opened and "won't open" in out.lower()
    assert cred.scan_command('Get-Content "' + str(link / "id_work") + '"')


def test_a_link_to_an_ordinary_folder_stays_readable(test_home, shares, monkeypatch):
    link = Path(test_home) / "Documents" / "notes-link"
    _resolves_to(monkeypatch, link, "\\\\localhost\\C$\\"
                 + _rel_from_drive(Path(test_home) / "Documents" / "real"))
    assert cred.check(link / "a.txt", sniff=False) is None
